"""
paths.py — централизованное управление путями Video Pipeline.

Новая структура сессии (без media/ и prompts/):
  data/channels/{lang}/{session}/input/
  data/channels/{lang}/{session}/transcripts/
  data/channels/{lang}/{session}/intro_clips/
  data/channels/{lang}/{session}/intro.mp4
  data/channels/{lang}/{session}/output/
"""

from pathlib import Path

BASE_DIR     = Path("C:/Projects/Video-pipeline")
LIBRARY_DIR  = BASE_DIR / "library"
CLIPS_DIR    = LIBRARY_DIR / "clips"
CHANNELS_DIR = BASE_DIR / "data" / "channels"

CHANNEL_LANG_MAP = {
    "channel_001_cosmos_de": "de",
    "channel_002_cosmos_fr": "fr",
}


def get_lang(channel_id: str) -> str:
    if channel_id in ("de", "fr"):
        return channel_id
    return CHANNEL_LANG_MAP.get(
        channel_id,
        "de" if "_de" in channel_id else "fr",
    )


def get_channel_dir(channel_id: str) -> Path:
    return CHANNELS_DIR / get_lang(channel_id)


def get_session_dir(channel_id: str, session: str) -> Path:
    return get_channel_dir(channel_id) / session


def get_input_dir(channel_id: str, session: str) -> Path:
    """MP3 озвучка"""
    return get_session_dir(channel_id, session) / "input"


def get_transcripts_dir(channel_id: str, session: str) -> Path:
    """result.json, SRT, ASS"""
    return get_session_dir(channel_id, session) / "transcripts"


def get_intro_clips_dir(channel_id: str, session: str) -> Path:
    """Клипы для монтажа интро"""
    return get_session_dir(channel_id, session) / "intro_clips"


def get_intro_path(channel_id: str, session: str) -> Path:
    """Готовое интро (ты кладёшь сюда)"""
    return get_session_dir(channel_id, session) / "intro.mp4"


def get_output_dir(channel_id: str, session: str) -> Path:
    """Финальное видео"""
    return get_session_dir(channel_id, session) / "output"


def get_audio_path(channel_id: str, session: str) -> Path | None:
    """Найти MP3 в папке input"""
    input_dir = get_input_dir(channel_id, session)
    mp3_files = list(input_dir.glob("*.mp3"))
    if mp3_files:
        return mp3_files[0]
    # Fallback WAV
    wav_files = list(input_dir.glob("*.wav"))
    if wav_files:
        return wav_files[0]
    return None


def get_result_json(channel_id: str, session: str) -> Path:
    """Путь к result.json"""
    return get_transcripts_dir(channel_id, session) / "result.json"


def get_ass_path(channel_id: str, session: str) -> Path:
    """Путь к ASS субтитрам"""
    return get_transcripts_dir(channel_id, session) / "subtitles_animated.ass"


def get_final_video(channel_id: str, session: str) -> Path:
    """Путь к финальному видео"""
    lang   = get_lang(channel_id)
    return get_output_dir(channel_id, session) / f"{session}_{lang.upper()}_final.mp4"


def get_last_session(channel_id: str) -> str | None:
    """Последняя сессия канала"""
    channel_dir = get_channel_dir(channel_id)
    if not channel_dir.exists():
        return None
    sessions = sorted(
        [
            d for d in channel_dir.iterdir()
            if d.is_dir() and d.name.startswith("Video_")
        ],
        reverse=True,
    )
    return sessions[0].name if sessions else None


def ensure_session_dirs(channel_id: str, session: str) -> bool:
    """Создать все папки сессии"""
    dirs = [
        get_input_dir(channel_id, session),
        get_transcripts_dir(channel_id, session),
        get_intro_clips_dir(channel_id, session),
        get_output_dir(channel_id, session),
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
    print(
        f"✅ Папки созданы: "
        f"{get_session_dir(channel_id, session)}",
        flush=True,
    )
    return True


def print_session_structure(channel_id: str, session: str) -> None:
    """Показать структуру сессии"""
    session_dir = get_session_dir(channel_id, session)
    print(f"""
{'='*50}
📁 {session_dir}
├── input\\
│   └── audio.mp3
├── transcripts\\
│   ├── result.json
│   ├── subtitles.srt
│   └── subtitles_animated.ass
├── intro_clips\\
│   ├── clip_001.mp4
│   └── ...
├── intro.mp4  ← положи сюда готовое интро
└── output\\
    └── {get_final_video(channel_id, session).name}
{'='*50}
""", flush=True)
