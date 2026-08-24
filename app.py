"""YoutubeDownloader — aplicação web Flask."""

from __future__ import annotations

import shutil
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file

from config import (
    AUDIO_FORMATS,
    CONTENT_MODES,
    DOWNLOADS_DIR,
    SUBTITLE_FORMATS,
    VIDEO_CONTAINERS,
)
from services.downloader import check_ffmpeg
from services.job_manager import manager
from services.resolver import enrich_video, resolve_query

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False


@app.get("/")
def index():
    return render_template(
        "index.html",
        ffmpeg_ok=check_ffmpeg(),
        video_containers=VIDEO_CONTAINERS,
        audio_formats=AUDIO_FORMATS,
        subtitle_formats=SUBTITLE_FORMATS,
        content_modes=CONTENT_MODES,
    )


@app.get("/api/health")
def health():
    return jsonify(
        {
            "ok": True,
            "ffmpeg": check_ffmpeg(),
            "downloads_dir": str(DOWNLOADS_DIR),
        }
    )


@app.post("/api/resolve")
def api_resolve():
    data = request.get_json(silent=True) or {}
    query = (data.get("query") or "").strip()
    if not query:
        return jsonify({"error": "Indique um URL ou termo de pesquisa."}), 400
    try:
        result = resolve_query(query)
        return jsonify(result)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"Falha ao analisar: {exc}"}), 500


@app.post("/api/enrich")
def api_enrich():
    """Obtém legendas/áudios detalhados para um vídeo (útil em listas)."""
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "URL em falta."}), 400
    try:
        return jsonify(enrich_video(url))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"Falha ao obter detalhes: {exc}"}), 500


def _normalize_options(raw: dict) -> dict:
    mode = (raw.get("mode") or "video").strip()
    if mode not in CONTENT_MODES:
        raise ValueError("Modo inválido. Use: video, audio ou subtitles.")

    container = (raw.get("container") or "mp4").strip().lower()
    quality = (raw.get("quality") or "highest").strip()
    audio_format = (raw.get("audio_format") or "mp3").strip().lower()

    audio_langs = raw.get("audio_langs") or []
    if isinstance(audio_langs, str):
        audio_langs = [s.strip() for s in audio_langs.split(",") if s.strip()]
    # Compatibilidade com o campo singular antigo
    audio_language = (raw.get("audio_language") or "").strip() or None
    if not audio_langs and audio_language:
        audio_langs = [audio_language]
    audio_langs = [str(x).strip() for x in audio_langs if str(x).strip()]
    # Por defeito embutir áudios seleccionados no vídeo
    embed_audio = raw.get("embed_audio")
    if embed_audio is None:
        embed_audio = True
    else:
        embed_audio = bool(embed_audio)

    subtitle_langs = raw.get("subtitle_langs") or []
    if isinstance(subtitle_langs, str):
        subtitle_langs = [s.strip() for s in subtitle_langs.split(",") if s.strip()]
    subtitle_format = (raw.get("subtitle_format") or "srt").strip().lower()
    write_subs = bool(raw.get("write_subs"))
    embed_subs = bool(raw.get("embed_subs"))

    if mode == "subtitles" and not subtitle_langs:
        raise ValueError("Selecione pelo menos um idioma de legendas.")
    if subtitle_format not in SUBTITLE_FORMATS:
        subtitle_format = "srt"
    if container not in VIDEO_CONTAINERS:
        container = "mp4"
    if audio_format not in AUDIO_FORMATS:
        audio_format = "mp3"

    # Em modo só-legendas, forçar escrita de ficheiros
    if mode == "subtitles":
        write_subs = True
        embed_subs = False
        embed_audio = False
    elif mode == "audio":
        embed_audio = False

    return {
        "mode": mode,
        "container": container,
        "quality": quality,
        "audio_format": audio_format,
        "audio_langs": list(audio_langs),
        "audio_language": audio_langs[0] if len(audio_langs) == 1 else None,
        "embed_audio": embed_audio,
        "subtitle_langs": list(subtitle_langs),
        "subtitle_format": subtitle_format,
        "write_subs": write_subs,
        "embed_subs": embed_subs,
    }


@app.post("/api/download")
def api_download():
    if not check_ffmpeg():
        return jsonify(
            {
                "error": "FFmpeg não encontrado. Instale o FFmpeg e reinicie a aplicação."
            }
        ), 400

    data = request.get_json(silent=True) or {}
    items = data.get("items")
    options_raw = data.get("options") or {}

    if not items:
        # Forma singular
        url = (data.get("url") or "").strip()
        if not url:
            return jsonify({"error": "Nenhum vídeo seleccionado."}), 400
        items = [
            {
                "url": url,
                "title": data.get("title") or "Vídeo",
                "thumbnail": data.get("thumbnail"),
            }
        ]

    try:
        options = _normalize_options(options_raw)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    enqueue_items = []
    for item in items:
        url = (item.get("url") or "").strip()
        if not url:
            continue
        enqueue_items.append(
            {
                "url": url,
                "title": item.get("title") or "Vídeo",
                "thumbnail": item.get("thumbnail"),
                "options": options,
            }
        )

    if not enqueue_items:
        return jsonify({"error": "Nenhum URL válido."}), 400

    jobs = manager.enqueue_many(enqueue_items)
    return jsonify({"jobs": jobs})


@app.get("/api/jobs")
def api_jobs():
    return jsonify({"jobs": manager.list_jobs()})


@app.get("/api/jobs/<job_id>")
def api_job(job_id: str):
    job = manager.get_job(job_id)
    if not job:
        return jsonify({"error": "Job não encontrado."}), 404
    return jsonify(job.to_dict())


@app.post("/api/jobs/<job_id>/cancel")
def api_cancel(job_id: str):
    result = manager.cancel(job_id)
    if not result:
        return jsonify({"error": "Job não encontrado."}), 404
    return jsonify(result)


@app.delete("/api/jobs/<job_id>")
def api_remove(job_id: str):
    if not manager.remove(job_id):
        return jsonify({"error": "Job não encontrado."}), 404
    return jsonify({"ok": True})


@app.get("/api/files/<job_id>")
def api_file(job_id: str):
    result = manager.file_path_for_download(job_id)
    if not result:
        return jsonify({"error": "Ficheiro não disponível."}), 404
    path, download_name = result
    return send_file(
        path,
        as_attachment=True,
        download_name=download_name,
    )


@app.post("/api/jobs/clear-finished")
def api_clear_finished():
    removed = 0
    for job in manager.list_jobs():
        if job["status"] in {"completed", "failed", "cancelled"}:
            if manager.remove(job["id"]):
                removed += 1
    return jsonify({"removed": removed})


def create_app() -> Flask:
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    return app


if __name__ == "__main__":
    if not check_ffmpeg():
        print("AVISO: FFmpeg não está no PATH. Os downloads vão falhar até o instalar.")
    else:
        print(f"FFmpeg: {shutil.which('ffmpeg')}")
    print(f"Downloads: {DOWNLOADS_DIR}")
    app.run(host="127.0.0.1", port=5000, debug=True, threaded=True)
