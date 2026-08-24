from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DOWNLOADS_DIR = BASE_DIR / "downloads"
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)

# Limite de downloads em paralelo
PARALLEL_LIMIT = 2

# Qualidades de vídeo suportadas (altura máxima; None = sem limite)
QUALITY_HEIGHT = {
    "highest": None,
    "1080": 1080,
    "720": 720,
    "480": 480,
    "360": 360,
    "lowest": 0,
}

VIDEO_CONTAINERS = ("mp4", "webm", "mkv")
AUDIO_FORMATS = ("mp3", "m4a", "ogg")
SUBTITLE_FORMATS = ("srt", "vtt")
CONTENT_MODES = ("video", "audio", "subtitles")
