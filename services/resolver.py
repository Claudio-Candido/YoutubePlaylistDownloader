"""Resolve queries YouTube: vídeo, playlist, canal ou pesquisa."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import yt_dlp


LANGUAGE_NAMES: dict[str, str] = {
    "pt": "Português",
    "pt-PT": "Português (Portugal)",
    "pt-BR": "Português (Brasil)",
    "en": "Inglês",
    "en-US": "Inglês (EUA)",
    "en-GB": "Inglês (Reino Unido)",
    "es": "Espanhol",
    "es-ES": "Espanhol (Espanha)",
    "es-419": "Espanhol (América Latina)",
    "fr": "Francês",
    "de": "Alemão",
    "it": "Italiano",
    "ru": "Russo",
    "uk": "Ucraniano",
    "ja": "Japonês",
    "ko": "Coreano",
    "zh": "Chinês",
    "zh-Hans": "Chinês (Simplificado)",
    "zh-Hant": "Chinês (Tradicional)",
    "ar": "Árabe",
    "hi": "Hindi",
    "nl": "Holandês",
    "pl": "Polaco",
    "tr": "Turco",
    "sv": "Sueco",
    "no": "Norueguês",
    "da": "Dinamarquês",
    "fi": "Finlandês",
    "cs": "Checo",
    "ro": "Romeno",
    "hu": "Húngaro",
    "el": "Grego",
    "th": "Tailandês",
    "vi": "Vietnamita",
    "id": "Indonésio",
    "ms": "Malaio",
    "he": "Hebraico",
    "ca": "Catalão",
    "eu": "Basco",
    "gl": "Galego",
}


def _language_label(code: str) -> str:
    if code in LANGUAGE_NAMES:
        return LANGUAGE_NAMES[code]
    base = code.split("-")[0]
    if base in LANGUAGE_NAMES:
        return f"{LANGUAGE_NAMES[base]} ({code})"
    return code


def _collect_subtitles(info: dict[str, Any]) -> list[dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}

    for lang, tracks in (info.get("subtitles") or {}).items():
        if lang == "live_chat":
            continue
        result[lang] = {
            "code": lang,
            "name": _language_label(lang),
            "auto": False,
            "formats": sorted({t.get("ext") for t in tracks if t.get("ext")}),
        }

    for lang, tracks in (info.get("automatic_captions") or {}).items():
        if lang == "live_chat" or lang in result:
            continue
        result[lang] = {
            "code": lang,
            "name": f"{_language_label(lang)} (automática)",
            "auto": True,
            "formats": sorted({t.get("ext") for t in tracks if t.get("ext")}),
        }

    preferred = ("pt", "pt-PT", "pt-BR", "en", "en-US", "es", "fr", "de")
    items = list(result.values())
    items.sort(
        key=lambda s: (
            preferred.index(s["code"]) if s["code"] in preferred else 999,
            s["name"].lower(),
        )
    )
    return items


def _collect_audio_languages(info: dict[str, Any]) -> list[dict[str, str]]:
    """Lista pistas de áudio multi-idioma a partir dos formatos / audio_tracks."""
    seen: dict[str, str] = {}

    def add(lang: str | None, *, original: bool = False, default: bool = False, note: str = "") -> None:
        if not lang:
            return
        label = _language_label(lang)
        extras = []
        if original or "original" in note.lower():
            extras.append("original")
        if default or "default" in note.lower():
            extras.append("predefinido")
        if extras:
            label = f"{label} ({', '.join(extras)})"
        prev = seen.get(lang)
        # Preferir o rótulo mais informativo
        if prev is None or len(label) > len(prev):
            seen[lang] = label

    for track in info.get("audio_tracks") or []:
        if not isinstance(track, dict):
            continue
        add(
            track.get("language") or track.get("id"),
            original=bool(track.get("original")),
            default=bool(track.get("default") or track.get("audioIsDefault")),
            note=str(track.get("display_name") or track.get("displayName") or ""),
        )

    for fmt in info.get("formats") or []:
        # Ignorar streams só de vídeo
        acodec = fmt.get("acodec") or "none"
        if acodec == "none":
            continue
        lang = fmt.get("language") or fmt.get("audio_lang")
        pref = fmt.get("language_preference")
        note = str(fmt.get("format_note") or "")
        add(
            lang,
            original=pref == 10 or "original" in note.lower(),
            default=pref == 5 or "default" in note.lower(),
            note=note,
        )

    preferred = ("pt", "pt-PT", "pt-BR", "en", "en-US", "es", "fr", "de", "ja")
    items = [{"code": k, "name": v} for k, v in seen.items()]
    items.sort(
        key=lambda a: (
            preferred.index(a["code"]) if a["code"] in preferred else 999,
            a["name"].lower(),
        )
    )
    return items


def _thumbnail(info: dict[str, Any]) -> str | None:
    if info.get("thumbnail"):
        return info["thumbnail"]
    thumbs = info.get("thumbnails") or []
    if thumbs:
        return thumbs[-1].get("url")
    return None


def _video_summary(info: dict[str, Any], *, full: bool = False) -> dict[str, Any]:
    video_id = info.get("id") or ""
    url = info.get("webpage_url") or info.get("url")
    if not url and video_id:
        url = f"https://www.youtube.com/watch?v={video_id}"

    summary: dict[str, Any] = {
        "id": video_id,
        "title": info.get("title") or "Sem título",
        "uploader": info.get("uploader") or info.get("channel") or "Desconhecido",
        "duration": info.get("duration"),
        "thumbnail": _thumbnail(info),
        "webpage_url": url,
    }

    if full:
        summary["subtitles"] = _collect_subtitles(info)
        summary["audio_languages"] = _collect_audio_languages(info)
        summary["description"] = (info.get("description") or "")[:500]

    return summary


def _base_opts(**extra: Any) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": False,
        "ignoreerrors": False,
    }
    opts.update(extra)
    return opts


def _extract(url: str, **extra: Any) -> dict[str, Any]:
    with yt_dlp.YoutubeDL(_base_opts(**extra)) as ydl:
        info = ydl.extract_info(url, download=False)
    if not info:
        raise ValueError("Não foi possível obter informação para esta query.")
    return info


def _looks_like_url(text: str) -> bool:
    lower = text.lower()
    if lower.startswith("http://") or lower.startswith("https://"):
        return True
    if "youtube.com/" in lower or "youtu.be/" in lower:
        return True
    parsed = urlparse(text if "://" in text else f"https://{text}")
    host = (parsed.hostname or "").lower()
    return host in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "music.youtube.com"}


def _flatten_entries(info: dict[str, Any], limit: int = 100) -> list[dict[str, Any]]:
    entries = []
    for entry in info.get("entries") or []:
        if not entry:
            continue
        entries.append(_video_summary(entry, full=False))
        if len(entries) >= limit:
            break
    return entries


def _search(query: str, limit: int = 20) -> dict[str, Any]:
    info = _extract(
        f"ytsearch{limit}:{query}",
        extract_flat="in_playlist",
        noplaylist=True,
    )
    return {
        "kind": "search",
        "title": f"Pesquisa: {query}",
        "videos": _flatten_entries(info, limit=limit),
    }


def _friendly_error(exc: Exception) -> str:
    text = str(exc)
    lower = text.lower()
    if "private video" in lower:
        return "Este vídeo é privado."
    if "video unavailable" in lower or "unavailable" in lower:
        return "Vídeo indisponível."
    if "sign in" in lower or "confirm your age" in lower:
        return "Este conteúdo requer autenticação (não suportada nesta versão)."
    if "ffmpeg" in lower:
        return "FFmpeg não encontrado. Instale o FFmpeg e garanta que está no PATH."
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return lines[-1] if lines else "Erro ao contactar o YouTube."


def resolve_query(query: str) -> dict[str, Any]:
    """
    Resolve uma query do utilizador.

    - Prefixo '?' força pesquisa
    - URLs de vídeo / playlist / canal
    - Texto livre → pesquisa YouTube (até 20 resultados)
    """
    query = (query or "").strip()
    if not query:
        raise ValueError("A query não pode estar vazia.")

    force_search = query.startswith("?")
    if force_search:
        query = query[1:].strip()
        if not query:
            raise ValueError("A query de pesquisa não pode estar vazia.")
        return _search(query)

    if not _looks_like_url(query):
        return _search(query)

    # Normalizar URLs sem esquema
    url = query if "://" in query else f"https://{query}"

    try:
        info = _extract(url, extract_flat="in_playlist")
    except yt_dlp.utils.DownloadError as exc:
        raise ValueError(_friendly_error(exc)) from exc

    is_playlist = info.get("_type") in {"playlist", "multi_video"} or bool(
        info.get("entries")
    )

    if is_playlist:
        videos = _flatten_entries(info)
        extractor = (info.get("extractor_key") or info.get("extractor") or "").lower()
        channel_name = info.get("channel") or info.get("uploader")
        title_raw = info.get("title") or channel_name or "Lista"

        if "channel" in extractor:
            kind = "channel"
            title = f"Canal: {channel_name or title_raw}"
        else:
            kind = "playlist"
            title = f"Playlist: {title_raw}"

        return {"kind": kind, "title": title, "videos": videos}

    # Vídeo único — detalhes completos
    try:
        full = _extract(url, noplaylist=True)
    except yt_dlp.utils.DownloadError as exc:
        raise ValueError(_friendly_error(exc)) from exc

    video = _video_summary(full, full=True)
    return {
        "kind": "video",
        "title": video["title"],
        "videos": [video],
    }


def enrich_video(url: str) -> dict[str, Any]:
    """Obtém detalhes completos (legendas, áudios) de um único vídeo."""
    try:
        info = _extract(url, noplaylist=True)
    except yt_dlp.utils.DownloadError as exc:
        raise ValueError(_friendly_error(exc)) from exc
    return _video_summary(info, full=True)
