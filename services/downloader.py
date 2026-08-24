"""Download de vídeo, áudio e legendas via yt-dlp."""

from __future__ import annotations

import shutil
import subprocess
import time
import zipfile
from pathlib import Path
from typing import Any, Callable

import yt_dlp

from config import AUDIO_FORMATS, QUALITY_HEIGHT, VIDEO_CONTAINERS


ProgressCallback = Callable[[float, str], None]
CancelChecker = Callable[[], bool]


class DownloadCancelled(Exception):
    """Levantada quando o utilizador cancela o job."""


def check_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def _normalize_audio_langs(
    audio_langs: list[str] | None,
    audio_language: str | None,
) -> list[str]:
    """Aceita lista nova ou o campo singular antigo."""
    langs = [str(x).strip() for x in (audio_langs or []) if str(x).strip()]
    if not langs and audio_language:
        langs = [audio_language.strip()]
    # Deduplicar preservando ordem
    seen: set[str] = set()
    out: list[str] = []
    for lang in langs:
        if lang not in seen:
            seen.add(lang)
            out.append(lang)
    return out


def _lang_filter(lang: str) -> str:
    """
    Filtro yt-dlp para idioma. Códigos com hífen (ex.: en-US) partem o parser
    se usados literalmente — usar prefixo na subtag principal.
    """
    lang = (lang or "").strip()
    if not lang:
        return ""
    if "-" in lang:
        return f"[language^={lang.split('-', 1)[0]}]"
    return f"[language^={lang}]"


def _single_audio_selectors(container: str, audio_lang: str | None) -> list[str]:
    """Lista de alternativas de áudio (sem '/'); o caller junta com '/'."""
    lang_f = _lang_filter(audio_lang) if audio_lang else ""
    alts: list[str] = []
    if container == "mp4":
        if lang_f:
            alts.extend([f"ba{lang_f}[ext=m4a]", f"ba{lang_f}", "ba[ext=m4a]", "ba"])
        else:
            alts.extend(["ba[ext=m4a]", "ba"])
    elif container == "webm":
        if lang_f:
            alts.extend([f"ba{lang_f}[ext=webm]", f"ba{lang_f}", "ba[ext=webm]", "ba"])
        else:
            alts.extend(["ba[ext=webm]", "ba"])
    elif container == "mkv":
        # MKV aceita m4a/opus — preferir m4a, depois qualquer bestaudio
        if lang_f:
            alts.extend(
                [f"ba{lang_f}[ext=m4a]", f"ba{lang_f}[ext=webm]", f"ba{lang_f}", "ba"]
            )
        else:
            alts.extend(["ba[ext=m4a]", "ba[ext=webm]", "ba"])
    else:
        alts.extend([f"ba{lang_f}", "ba"] if lang_f else ["ba"])
    seen: set[str] = set()
    out: list[str] = []
    for a in alts:
        if a not in seen:
            seen.add(a)
            out.append(a)
    return out


def _multi_audio_selector(container: str, audio_langs: list[str], *, prefer_ext: bool) -> str:
    """Várias pistas: ba[lang1]+ba[lang2]+… (sem '/' internos — partem o merge)."""
    parts: list[str] = []
    for lang in audio_langs:
        lang_f = _lang_filter(lang)
        if prefer_ext and container == "mp4":
            parts.append(f"ba{lang_f}[ext=m4a]")
        elif prefer_ext and container == "webm":
            parts.append(f"ba{lang_f}[ext=webm]")
        elif prefer_ext and container == "mkv":
            parts.append(f"ba{lang_f}[ext=m4a]")
        else:
            parts.append(f"ba{lang_f}")
    return "+".join(parts)


def _video_format_string(
    quality: str,
    container: str,
    audio_langs: list[str],
) -> str:
    """Preferir codecs compatíveis com o contentor (avc1+m4a para mp4)."""
    height = QUALITY_HEIGHT.get(quality)
    multi = len(audio_langs) > 1

    def video_candidates() -> list[str]:
        if container == "mp4":
            if height == 0:
                return ["wv*[ext=mp4]", "wv*"]
            if height is None:
                return ["bv*[ext=mp4][vcodec^=avc1]", "bv*[ext=mp4]", "bv*"]
            return [
                f"bv*[height<=?{height}][ext=mp4][vcodec^=avc1]",
                f"bv*[height<=?{height}][ext=mp4]",
                f"bv*[height<=?{height}]",
            ]
        if container == "webm":
            if height == 0:
                return ["wv*[ext=webm]", "wv*"]
            if height is None:
                return ["bv*[ext=webm]", "bv*"]
            return [f"bv*[height<=?{height}][ext=webm]", f"bv*[height<=?{height}]"]
        # mkv — remux flexível (h264/vp9/av1 + aac/opus)
        if height == 0:
            return ["wv*", "wv*[ext=mp4]", "wv*[ext=webm]"]
        if height is None:
            return [
                "bv*[vcodec^=avc1]",
                "bv*[ext=mp4]",
                "bv*[ext=webm]",
                "bv*",
            ]
        return [
            f"bv*[height<=?{height}][vcodec^=avc1]",
            f"bv*[height<=?{height}][ext=mp4]",
            f"bv*[height<=?{height}][ext=webm]",
            f"bv*[height<=?{height}]",
        ]

    chains: list[str] = []
    if multi:
        audio_ext = _multi_audio_selector(container, audio_langs, prefer_ext=True)
        audio_any = _multi_audio_selector(container, audio_langs, prefer_ext=False)
        for v in video_candidates():
            chains.append(f"{v}+{audio_ext}")
            chains.append(f"{v}+{audio_any}")
        # Último recurso: só o primeiro idioma
        for audio in _single_audio_selectors(container, audio_langs[0]):
            for v in video_candidates():
                chains.append(f"{v}+{audio}")
    elif audio_langs:
        for audio in _single_audio_selectors(container, audio_langs[0]):
            for v in video_candidates():
                chains.append(f"{v}+{audio}")
    else:
        for audio in _single_audio_selectors(container, None):
            for v in video_candidates():
                chains.append(f"{v}+{audio}")

    if height == 0:
        chains.append("w")
    elif height is None:
        chains.append("b")
    else:
        chains.append(f"b[height<=?{height}]")
        chains.append("b")

    # Deduplicar preservando ordem
    seen: set[str] = set()
    ordered: list[str] = []
    for c in chains:
        if c not in seen:
            seen.add(c)
            ordered.append(c)
    return "/".join(ordered)


def _audio_format_string(audio_lang: str | None) -> str:
    if audio_lang:
        return f"ba{_lang_filter(audio_lang)}/ba/b"
    return "bestaudio/best"


def _count_audio_streams(media_path: Path) -> int:
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a",
                "-show_entries",
                "stream=index",
                "-of",
                "csv=p=0",
                str(media_path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return 0
    lines = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
    return len(lines)


def _download_single_audio_track(
    url: str,
    out_dir: Path,
    lang: str,
    container: str,
    is_cancelled: CancelChecker | None,
) -> Path | None:
    """Descarrega uma pista de áudio isolada (para remux multi-áudio)."""
    ext_pref = "m4a" if container in {"mp4", "mkv"} else "webm"
    outtmpl = str(out_dir / f"__audio_{lang}.%(ext)s")
    fmt = (
        f"ba{_lang_filter(lang)}[ext={ext_pref}]/"
        f"ba{_lang_filter(lang)}/ba"
    )
    opts: dict[str, Any] = {
        "outtmpl": outtmpl,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "format": fmt,
        "overwrites": True,
        "retries": 5,
    }
    before = set(out_dir.glob(f"__audio_{lang}.*"))
    try:
        _run_ydl(url, opts, is_cancelled)
    except Exception:  # noqa: BLE001
        return None
    after = list(out_dir.glob(f"__audio_{lang}.*"))
    created = [p for p in after if p not in before or p.stat().st_size > 0]
    media = [
        p
        for p in created
        if p.suffix.lower() not in {".part", ".ytdl", ".temp", ".srt", ".vtt"}
    ]
    return max(media, key=lambda p: p.stat().st_size) if media else None


def _remux_multi_audio(
    video_path: Path,
    audio_paths: list[tuple[str, Path]],
) -> Path | None:
    """Junta vídeo + várias pistas de áudio num único ficheiro com metadados de idioma."""
    if not video_path.exists() or not audio_paths:
        return None
    out_path = video_path.with_name(video_path.stem + ".multi" + video_path.suffix)
    cmd = ["ffmpeg", "-y", "-i", str(video_path)]
    for _, ap in audio_paths:
        cmd.extend(["-i", str(ap)])
    # Vídeo da entrada 0; áudio de cada ficheiro de áudio (ignorar áudio original se houver extras)
    cmd.extend(["-map", "0:v:0"])
    for i in range(len(audio_paths)):
        cmd.extend(["-map", f"{i + 1}:a:0"])
    # Preservar metadados/capítulos globais do vídeo original
    cmd.extend(["-map_metadata", "0", "-map_chapters", "0", "-c", "copy"])
    for i, (lang, _) in enumerate(audio_paths):
        tag = _ffmpeg_lang_tag(lang)
        cmd.extend([f"-metadata:s:a:{i}", f"language={tag}"])
        # Primeira pista como predefinida
        if i == 0:
            cmd.extend([f"-disposition:a:{i}", "default"])
    cmd.append(str(out_path))
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=900)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        out_path.unlink(missing_ok=True)
        return None
    try:
        video_path.unlink(missing_ok=True)
        out_path.rename(video_path)
        return video_path
    except OSError:
        return out_path if out_path.exists() else None


def _tag_audio_languages(media_path: Path, audio_langs: list[str]) -> None:
    """Define metadados de idioma nas pistas de áudio (sem re-encode)."""
    n = _count_audio_streams(media_path)
    if n < 1 or not audio_langs:
        return
    out_path = media_path.with_name(media_path.stem + ".tagged" + media_path.suffix)
    cmd = ["ffmpeg", "-y", "-i", str(media_path), "-map", "0", "-c", "copy"]
    for i, lang in enumerate(audio_langs[:n]):
        tag = _ffmpeg_lang_tag(lang)
        cmd.extend([f"-metadata:s:a:{i}", f"language={tag}"])
        if i == 0:
            cmd.extend([f"-disposition:a:{i}", "default"])
    cmd.append(str(out_path))
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=600)
        media_path.unlink(missing_ok=True)
        out_path.rename(media_path)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        out_path.unlink(missing_ok=True)


def _ensure_embedded_audio_tracks(
    url: str,
    out_dir: Path,
    audio_langs: list[str],
    container: str,
    on_progress: ProgressCallback | None,
    is_cancelled: CancelChecker | None,
) -> None:
    """Garante que o media tem as pistas de áudio pedidas; remux com FFmpeg se faltar."""
    if len(audio_langs) < 2:
        return
    media = _media_files(out_dir)
    if not media:
        return
    primary = max(media, key=lambda p: p.stat().st_size)
    have = _count_audio_streams(primary)
    if have >= len(audio_langs):
        if on_progress:
            on_progress(96.0, "A etiquetar idiomas de áudio…")
        _tag_audio_languages(primary, audio_langs)
        return

    if on_progress:
        on_progress(88.0, f"A completar multi-áudios ({have}/{len(audio_langs)})…")

    tracks: list[tuple[str, Path]] = []
    for i, lang in enumerate(audio_langs):
        if is_cancelled and is_cancelled():
            raise DownloadCancelled("Download cancelado.")
        if on_progress:
            on_progress(
                88.0 + (i / len(audio_langs)) * 6.0,
                f"Áudio embutido {i + 1}/{len(audio_langs)} ({lang})…",
            )
        path = _download_single_audio_track(
            url, out_dir, lang, container, is_cancelled
        )
        if path:
            tracks.append((lang, path))

    if len(tracks) < 2:
        for _, p in tracks:
            p.unlink(missing_ok=True)
        return

    if on_progress:
        on_progress(95.0, "A fundir pistas de áudio no vídeo…")
    _remux_multi_audio(primary, tracks)
    for _, p in tracks:
        p.unlink(missing_ok=True)
    for p in out_dir.glob("__audio_*"):
        p.unlink(missing_ok=True)


def _metadata_postprocessors(*, mode: str, container: str) -> list[dict[str, Any]]:
    """Post-processadores yt-dlp: metadados, capítulos, capa."""
    pps: list[dict[str, Any]] = [
        # webp/png → jpg (necessário para embutir em mp4/m4a/mp3)
        {"key": "FFmpegThumbnailsConvertor", "format": "jpg"},
        {
            "key": "FFmpegMetadata",
            "add_metadata": True,
            "add_chapters": True,
            # Anexo info.json funciona bem em mkv
            "add_infojson": "if_exists" if container == "mkv" else False,
        },
        {"key": "EmbedThumbnail", "already_have_thumbnail": False},
    ]
    return pps


def build_ydl_opts(
    *,
    out_dir: Path,
    mode: str,
    container: str = "mp4",
    quality: str = "highest",
    audio_format: str = "mp3",
    audio_language: str | None = None,
    audio_langs: list[str] | None = None,
    embed_audio: bool = True,
    subtitle_langs: list[str] | None = None,
    subtitle_format: str = "srt",
    write_subs: bool = False,
    embed_subs: bool = False,
    progress_hook: Callable[[dict[str, Any]], None] | None = None,
    outtmpl_override: str | None = None,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    # Evitar [] no nome — o FFmpeg interpreta como filtergraph e o merge falha
    outtmpl = outtmpl_override or str(out_dir / "%(title).180B (%(id)s).%(ext)s")
    langs = _normalize_audio_langs(audio_langs, audio_language)
    # Em vídeo: multi-áudios só se embutir estiver activo
    if mode == "video" and not embed_audio:
        langs = langs[:1]

    opts: dict[str, Any] = {
        "outtmpl": outtmpl,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "retries": 5,
        "fragment_retries": 5,
        "ignoreerrors": False,
        "overwrites": True,
        # Mitigar 429 do YouTube
        "retry_sleep": {"http": "exp=1:12", "extractor": "linear=1::3"},
        "sleep_requests": 0.5,
    }

    if progress_hook:
        opts["progress_hooks"] = [progress_hook]

    subtitle_langs = subtitle_langs or []
    want_subs = bool(subtitle_langs) and (write_subs or embed_subs or mode == "subtitles")
    postprocessors: list[dict[str, Any]] = []

    if mode == "video":
        if container not in VIDEO_CONTAINERS:
            container = "mp4"
        opts["format"] = _video_format_string(quality, container, langs)
        opts["merge_output_format"] = container
        if len(langs) > 1:
            # Nome correcto na API Python (o CLI --audio-multistreams mapeia para isto)
            opts["allow_multiple_audio_streams"] = True
        # Metadados / capa / capítulos (info.json usado também após remux)
        opts["writethumbnail"] = True
        opts["writeinfojson"] = True
        postprocessors.extend(_metadata_postprocessors(mode=mode, container=container))

    elif mode == "audio":
        if audio_format not in AUDIO_FORMATS:
            audio_format = "mp3"
        # Uma língua por chamada; multi trata-se em download()
        lang = langs[0] if langs else None
        opts["format"] = _audio_format_string(lang)
        opts["writethumbnail"] = True
        opts["writeinfojson"] = True
        postprocessors.append(
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": audio_format,
                "preferredquality": "192",
            }
        )
        postprocessors.extend(
            _metadata_postprocessors(mode=mode, container=audio_format)
        )

    elif mode == "subtitles":
        if not subtitle_langs:
            raise ValueError("Selecione pelo menos um idioma de legendas.")
        opts["skip_download"] = True
        opts["writesubtitles"] = True
        opts["writeautomaticsub"] = True
        opts["subtitleslangs"] = subtitle_langs
        opts["subtitlesformat"] = f"{subtitle_format}/best"
        opts["sleep_subtitles"] = 2
        postprocessors.append(
            {"key": "FFmpegSubtitlesConvertor", "format": subtitle_format}
        )
    else:
        raise ValueError(f"Modo inválido: {mode}")

    # want_subs reservado para a fase 2 em download()
    _ = want_subs

    if postprocessors:
        opts["postprocessors"] = postprocessors

    return opts


_SIDE_CAR_SUFFIXES = {
    ".part",
    ".ytdl",
    ".temp",
    ".json",
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".image",
}


def _list_artifacts(out_dir: Path) -> list[Path]:
    if not out_dir.exists():
        return []
    files = [
        p
        for p in out_dir.iterdir()
        if p.is_file() and p.suffix.lower() not in _SIDE_CAR_SUFFIXES
    ]
    files.sort(key=lambda p: p.stat().st_mtime)
    return files


def _find_info_json(out_dir: Path) -> Path | None:
    matches = sorted(out_dir.glob("*.info.json"), key=lambda p: p.stat().st_mtime)
    return matches[-1] if matches else None


def _find_thumbnail(out_dir: Path) -> Path | None:
    for pattern in ("*.jpg", "*.jpeg", "*.png", "*.webp"):
        matches = sorted(out_dir.glob(pattern), key=lambda p: p.stat().st_mtime)
        if matches:
            return matches[-1]
    return None


def _ffmeta_escape(value: str) -> str:
    return (
        (value or "")
        .replace("\\", "\\\\")
        .replace("\n", "\\\n")
        .replace("=", "\\=")
        .replace(";", "\\;")
        .replace("#", "\\#")
    )


def _reapply_metadata_from_infojson(out_dir: Path, media_path: Path) -> None:
    """
    Reaplica metadados relevantes após remux (multi-áudio / legendas),
    a partir do .info.json gerado pelo yt-dlp.
    """
    import json

    info_path = _find_info_json(out_dir)
    if not info_path or not media_path.exists():
        return
    try:
        info = json.loads(info_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return

    title = str(info.get("title") or media_path.stem)
    artist = str(
        info.get("artist")
        or info.get("uploader")
        or info.get("channel")
        or info.get("creator")
        or ""
    )
    album = str(info.get("album") or artist)
    description = str(info.get("description") or "")[:3500]
    url = str(info.get("webpage_url") or info.get("original_url") or info.get("url") or "")
    video_id = str(info.get("id") or "")
    upload_date = str(info.get("upload_date") or "")  # YYYYMMDD
    date = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}" if len(upload_date) == 8 else upload_date
    genre = str(info.get("genre") or "YouTube")
    comment = (
        f"Downloaded with YoutubePlaylistDownloader (Flask + yt-dlp)\n"
        f"Video: {title}\n"
        f"URL: {url}\n"
        f"Channel: {artist}\n"
        f"ID: {video_id}"
    )

    meta_file = out_dir / "__ffmetadata.txt"
    meta_file.write_text(
        "\n".join(
            [
                ";FFMETADATA1",
                f"title={_ffmeta_escape(title)}",
                f"artist={_ffmeta_escape(artist)}",
                f"album={_ffmeta_escape(album)}",
                f"album_artist={_ffmeta_escape(artist)}",
                f"description={_ffmeta_escape(description)}",
                f"comment={_ffmeta_escape(comment)}",
                f"synopsis={_ffmeta_escape(description[:500])}",
                f"purl={_ffmeta_escape(url)}",
                f"date={_ffmeta_escape(date)}",
                f"genre={_ffmeta_escape(genre)}",
                f"encoding_tool={_ffmeta_escape('YoutubePlaylistDownloader (Flask + yt-dlp)')}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    out_path = media_path.with_name(media_path.stem + ".meta" + media_path.suffix)
    thumb = _find_thumbnail(out_dir)
    ext = media_path.suffix.lower()
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(media_path),
        "-i",
        str(meta_file),
    ]
    thumb_input = bool(thumb and thumb.exists() and ext in {".mp4", ".m4a", ".mp3", ".mkv"})
    if thumb_input:
        cmd.extend(["-i", str(thumb)])

    # Mapear media + metadados; capa como attached_pic quando possível
    cmd.extend(["-map", "0", "-map_metadata", "1", "-map_chapters", "0"])
    if thumb_input:
        cmd.extend(["-map", "2"])
    cmd.extend(["-c", "copy"])
    if thumb_input:
        # Não usar copy na capa — reencodar para mjpeg
        cmd.extend(["-c:v:1", "mjpeg", "-disposition:v:1", "attached_pic"])
        if ext == ".mp3":
            # mp3: a capa é o único vídeo; stream de áudio mantém-se em copy
            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                str(media_path),
                "-i",
                str(meta_file),
                "-i",
                str(thumb),
                "-map",
                "0:a",
                "-map",
                "2",
                "-map_metadata",
                "1",
                "-c:a",
                "copy",
                "-c:v",
                "mjpeg",
                "-disposition:v:0",
                "attached_pic",
                str(out_path),
            ]

    if not (thumb_input and ext == ".mp3"):
        cmd.append(str(out_path))
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=600)
        media_path.unlink(missing_ok=True)
        out_path.rename(media_path)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        # Fallback: só metadados de texto, sem capa
        out_path.unlink(missing_ok=True)
        if thumb_input:
            cmd_fb = [
                "ffmpeg",
                "-y",
                "-i",
                str(media_path),
                "-i",
                str(meta_file),
                "-map",
                "0",
                "-map_metadata",
                "1",
                "-map_chapters",
                "0",
                "-c",
                "copy",
                str(out_path),
            ]
            try:
                subprocess.run(
                    cmd_fb, check=True, capture_output=True, text=True, timeout=600
                )
                media_path.unlink(missing_ok=True)
                out_path.rename(media_path)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
                out_path.unlink(missing_ok=True)
    finally:
        meta_file.unlink(missing_ok=True)


def _cleanup_sidecars(out_dir: Path) -> None:
    """Remove thumbnails / info.json temporários após embutir metadados."""
    if not out_dir.exists():
        return
    for p in out_dir.iterdir():
        if not p.is_file():
            continue
        name = p.name.lower()
        if name.endswith(".info.json") or name.startswith("__"):
            p.unlink(missing_ok=True)
            continue
        if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".image"}:
            p.unlink(missing_ok=True)


def _run_ydl(
    url: str,
    ydl_opts: dict[str, Any],
    is_cancelled: CancelChecker | None = None,
) -> None:
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            if is_cancelled and is_cancelled():
                raise DownloadCancelled("Download cancelado.")
            ydl.download([url])
    except DownloadCancelled:
        raise
    except yt_dlp.utils.DownloadError as exc:
        text = str(exc)
        if is_cancelled and is_cancelled():
            raise DownloadCancelled("Download cancelado.") from exc
        raise RuntimeError(_friendly_download_error(text)) from exc


def _is_rate_limit_error(text: str) -> bool:
    lower = text.lower()
    return "429" in text or "too many requests" in lower or "rate-limit" in lower


def _subtitle_files(out_dir: Path) -> list[Path]:
    exts = {".srt", ".vtt", ".ass", ".lrc"}
    return sorted(
        p for p in out_dir.iterdir() if p.is_file() and p.suffix.lower() in exts
    )


def _media_files(out_dir: Path) -> list[Path]:
    sub_ext = {".srt", ".vtt", ".ass", ".lrc"}
    skip = _SIDE_CAR_SUFFIXES | {".zip"}
    return sorted(
        p
        for p in out_dir.iterdir()
        if p.is_file()
        and p.suffix.lower() not in sub_ext
        and p.suffix.lower() not in skip
        and not p.name.startswith("__")
        and not p.name.endswith(".info.json")
    )


def _ffmpeg_lang_tag(code: str) -> str:
    """ISO 639-2/B aproximado para metadados mov_text."""
    base = (code or "und").split("-")[0].lower()
    mapping = {
        "pt": "por",
        "en": "eng",
        "es": "spa",
        "fr": "fra",
        "de": "deu",
        "it": "ita",
        "ja": "jpn",
        "ko": "kor",
        "zh": "zho",
        "ru": "rus",
        "uk": "ukr",
        "ar": "ara",
        "hi": "hin",
        "nl": "nld",
        "pl": "pol",
        "tr": "tur",
        "sv": "swe",
        "no": "nor",
        "da": "dan",
        "fi": "fin",
        "cs": "ces",
        "ro": "ron",
        "hu": "hun",
        "el": "ell",
        "th": "tha",
        "vi": "vie",
        "id": "ind",
        "ms": "msa",
        "he": "heb",
        "ca": "cat",
    }
    return mapping.get(base, base[:3] if len(base) >= 3 else "und")


def _lang_from_sub_path(path: Path) -> str:
    # título (id).pt-PT.srt → pt-PT
    name = path.name
    for ext in (".srt", ".vtt", ".ass", ".lrc"):
        if name.lower().endswith(ext):
            name = name[: -len(ext)]
            break
    if "." in name:
        return name.rsplit(".", 1)[-1]
    return "und"


def _embed_subs_with_ffmpeg(video_path: Path, sub_paths: list[Path]) -> Path | None:
    """Embute legendas no vídeo. Devolve o novo caminho ou None se falhar."""
    if not sub_paths or not video_path.exists():
        return None
    # mp4 → mov_text; webm/mkv → webvtt
    sub_codec = "webvtt" if video_path.suffix.lower() in {".webm", ".mkv"} else "mov_text"
    out_path = video_path.with_name(video_path.stem + ".subs" + video_path.suffix)
    cmd = ["ffmpeg", "-y", "-i", str(video_path)]
    for sp in sub_paths:
        cmd.extend(["-i", str(sp)])
    cmd.extend(["-map", "0"])
    for i in range(len(sub_paths)):
        cmd.extend(["-map", str(i + 1)])
    cmd.extend(
        ["-map_metadata", "0", "-map_chapters", "0", "-c", "copy", "-c:s", sub_codec]
    )
    for i, sp in enumerate(sub_paths):
        tag = _ffmpeg_lang_tag(_lang_from_sub_path(sp))
        cmd.extend([f"-metadata:s:s:{i}", f"language={tag}"])
    cmd.append(str(out_path))
    try:
        subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        if out_path.exists():
            out_path.unlink(missing_ok=True)
        return None

    # Substituir o original pelo ficheiro com legendas
    try:
        video_path.unlink(missing_ok=True)
        out_path.rename(video_path)
        return video_path
    except OSError:
        return out_path if out_path.exists() else None


def _download_subtitles_resilient(
    url: str,
    out_dir: Path,
    subtitle_langs: list[str],
    subtitle_format: str,
    on_progress: ProgressCallback | None,
    is_cancelled: CancelChecker | None,
) -> list[str]:
    """
    Descarrega legendas idioma a idioma (com pausa) para evitar HTTP 429.
    Devolve lista de códigos que falharam (para aviso).
    """
    failed: list[str] = []
    total = len(subtitle_langs)
    outtmpl = str(out_dir / "%(title).180B (%(id)s).%(ext)s")

    for i, lang in enumerate(subtitle_langs):
        if is_cancelled and is_cancelled():
            raise DownloadCancelled("Download cancelado.")
        if on_progress:
            on_progress(
                90.0 + (i / max(total, 1)) * 8.0,
                f"Legendas {i + 1}/{total} ({lang})…",
            )
        # Pausa entre pedidos — YouTube rate-limita facilmente
        if i > 0:
            time.sleep(2.5)

        opts: dict[str, Any] = {
            "outtmpl": outtmpl,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "noplaylist": True,
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": [lang],
            "subtitlesformat": f"{subtitle_format}/best",
            "sleep_subtitles": 2,
            "retries": 6,
            "retry_sleep": {"http": "exp=2:20"},
            "overwrites": True,
            "ignoreerrors": False,
            "postprocessors": [
                {"key": "FFmpegSubtitlesConvertor", "format": subtitle_format}
            ],
        }
        try:
            _run_ydl(url, opts, is_cancelled)
        except Exception as exc:  # noqa: BLE001 — continuar com outros idiomas
            failed.append(lang)
            if _is_rate_limit_error(str(exc)):
                # Espera extra e tenta uma vez mais
                time.sleep(5)
                try:
                    _run_ydl(url, opts, is_cancelled)
                    failed.remove(lang)
                except Exception:  # noqa: BLE001
                    pass
    return failed


def download(
    url: str,
    out_dir: Path,
    options: dict[str, Any],
    on_progress: ProgressCallback | None = None,
    is_cancelled: CancelChecker | None = None,
) -> list[Path]:
    """
    Executa o download. Devolve a lista de ficheiros gerados.
    `options` deve conter as chaves usadas por build_ydl_opts (exceto out_dir/hooks).
    """

    def hook(d: dict[str, Any]) -> None:
        if is_cancelled and is_cancelled():
            raise DownloadCancelled("Download cancelado.")
        if not on_progress:
            return
        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes") or 0
            pct = (downloaded / total * 100.0) if total else 0.0
            # Reservar 10% finais para legendas quando aplicável
            scale = 90.0 if _wants_subs(options) else 99.0
            speed = d.get("_speed_str") or d.get("speed") or ""
            eta = d.get("_eta_str") or ""
            msg = f"{pct:.1f}%"
            if speed:
                msg += f" · {speed}"
            if eta:
                msg += f" · ETA {eta}"
            on_progress(min(pct / 100.0 * scale, scale), msg)
        elif status == "finished":
            on_progress(90.0 if _wants_subs(options) else 99.0, "A processar (FFmpeg)…")

    langs = _normalize_audio_langs(
        options.get("audio_langs"),
        options.get("audio_language"),
    )
    mode = options.get("mode") or "video"
    subtitle_langs = list(options.get("subtitle_langs") or [])
    subtitle_format = options.get("subtitle_format") or "srt"
    write_subs = bool(options.get("write_subs"))
    embed_subs = bool(options.get("embed_subs"))

    # Só-áudio com vários idiomas → um ficheiro por idioma
    if mode == "audio" and len(langs) > 1:
        total = len(langs)
        for i, lang in enumerate(langs):
            if is_cancelled and is_cancelled():
                raise DownloadCancelled("Download cancelado.")
            if on_progress:
                on_progress(
                    (i / total) * 90.0,
                    f"Áudio {i + 1}/{total} ({lang})…",
                )
            lang_opts = {
                **options,
                "audio_langs": [lang],
                "audio_language": lang,
                "subtitle_langs": [],
                "write_subs": False,
                "embed_subs": False,
            }
            outtmpl = str(out_dir / f"%(title).160B (%(id)s).{lang}.%(ext)s")

            def lang_hook(d: dict[str, Any], _i=i, _total=total) -> None:
                if is_cancelled and is_cancelled():
                    raise DownloadCancelled("Download cancelado.")
                if not on_progress or d.get("status") != "downloading":
                    return
                t = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                got = d.get("downloaded_bytes") or 0
                frac = (got / t) if t else 0.0
                pct = ((_i + frac) / _total) * 90.0
                on_progress(min(pct, 90.0), f"Áudio {_i + 1}/{_total}…")

            ydl_opts = build_ydl_opts(
                out_dir=out_dir,
                progress_hook=lang_hook,
                outtmpl_override=outtmpl,
                **_opts_for_build(lang_opts),
            )
            _run_ydl(url, ydl_opts, is_cancelled)
    elif mode == "subtitles":
        if not subtitle_langs:
            raise ValueError("Selecione pelo menos um idioma de legendas.")
        failed = _download_subtitles_resilient(
            url,
            out_dir,
            subtitle_langs,
            subtitle_format,
            on_progress,
            is_cancelled,
        )
        artifacts = _list_artifacts(out_dir)
        if not artifacts:
            if failed:
                raise RuntimeError(
                    "Não foi possível descarregar legendas "
                    "(YouTube limitou os pedidos — HTTP 429). Espere um minuto e tente de novo."
                )
            raise RuntimeError("O download terminou sem gerar ficheiros.")
        if on_progress:
            msg = "Concluído"
            if failed:
                msg = f"Concluído (falhou: {', '.join(failed)})"
            on_progress(100.0, msg)
        return artifacts
    else:
        # Vídeo ou áudio simples — sem legendas nesta fase
        media_opts = {
            **options,
            "subtitle_langs": [],
            "write_subs": False,
            "embed_subs": False,
        }
        ydl_opts = build_ydl_opts(
            out_dir=out_dir,
            progress_hook=hook,
            **_opts_for_build(media_opts),
        )
        _run_ydl(url, ydl_opts, is_cancelled)

        # Garantir multi-áudios embutidos (yt-dlp + remux FFmpeg se necessário)
        embed_audio = options.get("embed_audio", True)
        if (
            mode == "video"
            and embed_audio
            and len(langs) > 1
        ):
            _ensure_embedded_audio_tracks(
                url,
                out_dir,
                langs,
                options.get("container") or "mp4",
                on_progress,
                is_cancelled,
            )

    # Fase 2: legendas (não bloqueiam o sucesso do media)
    warning = ""
    if mode in {"video", "audio"} and _wants_subs(options):
        if on_progress:
            on_progress(91.0, "A obter legendas…")
        failed = _download_subtitles_resilient(
            url,
            out_dir,
            subtitle_langs,
            subtitle_format,
            on_progress,
            is_cancelled,
        )
        subs = _subtitle_files(out_dir)
        if embed_subs and mode == "video" and subs:
            if on_progress:
                on_progress(98.0, "A embutir legendas…")
            media = _media_files(out_dir)
            if media:
                primary = max(media, key=lambda p: p.stat().st_size)
                _embed_subs_with_ffmpeg(primary, subs)
                if not write_subs:
                    for sp in subs:
                        sp.unlink(missing_ok=True)
        if failed:
            warning = f"Algumas legendas falharam ({', '.join(failed)})"

    # Reaplicar metadados ricos após remux (multi-áudio / legendas) e limpar sidecars
    if mode in {"video", "audio"}:
        if on_progress:
            on_progress(99.0, "A gravar metadados…")
        for media in _media_files(out_dir):
            _reapply_metadata_from_infojson(out_dir, media)
        _cleanup_sidecars(out_dir)

    artifacts = _list_artifacts(out_dir)
    if not artifacts:
        raise RuntimeError("O download terminou sem gerar ficheiros.")
    if on_progress:
        on_progress(100.0, warning or "Concluído")
    return artifacts


def _wants_subs(options: dict[str, Any]) -> bool:
    langs = options.get("subtitle_langs") or []
    if not langs:
        return False
    mode = options.get("mode") or "video"
    if mode == "subtitles":
        return True
    return bool(options.get("write_subs") or options.get("embed_subs"))


def _opts_for_build(options: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "mode",
        "container",
        "quality",
        "audio_format",
        "audio_language",
        "audio_langs",
        "embed_audio",
        "subtitle_langs",
        "subtitle_format",
        "write_subs",
        "embed_subs",
    }
    return {k: v for k, v in options.items() if k in allowed}


def pack_artifacts(artifacts: list[Path], zip_path: Path) -> Path:
    """Cria um ZIP com todos os artefactos (vídeo + legendas, etc.)."""
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in artifacts:
            zf.write(path, arcname=path.name)
    return zip_path


def primary_artifact(artifacts: list[Path]) -> Path:
    """Escolhe o ficheiro media principal (não legenda)."""
    sub_ext = {".srt", ".vtt", ".ass", ".lrc"}
    media = [p for p in artifacts if p.suffix.lower() not in sub_ext]
    if media:
        # Preferir o maior (geralmente o vídeo/áudio)
        return max(media, key=lambda p: p.stat().st_size)
    return artifacts[0]


def _friendly_download_error(text: str) -> str:
    lower = text.lower()
    if "429" in text or "too many requests" in lower:
        return (
            "YouTube limitou os pedidos (HTTP 429). "
            "Espere cerca de 1 minuto e tente de novo; "
            "seleccione menos idiomas de legendas de cada vez."
        )
    if "ffmpeg" in lower:
        return "FFmpeg não encontrado ou falhou. Instale o FFmpeg e tente novamente."
    if "requested format is not available" in lower:
        return "O formato pedido não está disponível para este vídeo."
    if "private" in lower:
        return "Este vídeo é privado."
    if "unavailable" in lower:
        return "Vídeo indisponível."
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return lines[-1] if lines else "Falha no download."
