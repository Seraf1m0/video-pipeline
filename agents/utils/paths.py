"""
paths.py — централизованное управление путями Video Pipeline.

Структура сессии:
  data/channels/{lang}/{session}/input/
  data/channels/{lang}/{session}/transcripts/
  data/channels/{lang}/{session}/intro_clips/
  data/channels/{lang}/{session}/intro.mp4
  data/channels/{lang}/{session}/output/

Библиотеки:
  C:/Projects/Video-pipeline/library_cosmos   — cosmos-ниша (DE, FR)
  C:/Projects/Video-pipeline/library_religion — religion-ниша (ES)
"""

from pathlib import Path

BASE_DIR     = Path("C:/Projects/Video-pipeline")   # код — остаётся на C:
DATA_ROOT    = Path("D:/Video-pipeline-data")        # данные — на D: SSD
CHANNELS_DIR = DATA_ROOT / "channels"
TEMP_ROOT    = Path("D:/Video-pipeline-temp")        # temp рендер — на D: SSD

# ── Библиотеки по нишам ───────────────────────────────────────────────────────

LIBRARY_MAP = {
    "cosmos":   Path("D:/Video-pipeline-library/library_cosmos"),
    "religion": Path("D:/Video-pipeline-library/library_religion"),
}

# ── Привязка каналов к нишам ──────────────────────────────────────────────────

NICHE_MAP = {
    "channel_001_cosmos_de":   "cosmos",
    "channel_002_cosmos_fr":   "cosmos",
    "channel_003_religion_es": "religion",
    "channel_004_cosmos_fr":   "cosmos",
}

# ── Привязка каналов к языкам ─────────────────────────────────────────────────

LANG_MAP = {
    "channel_001_cosmos_de":   "de",
    "channel_002_cosmos_fr":   "fr",
    "channel_003_religion_es": "es",
    "channel_004_cosmos_fr":   "fr2",
}


def get_niche(channel_id: str) -> str:
    return NICHE_MAP.get(channel_id, "cosmos")


def get_lang(channel_id: str) -> str:
    # Поддержка коротких форм "de"/"fr"/"es"
    if channel_id in LANG_MAP.values():
        return channel_id
    return LANG_MAP.get(
        channel_id,
        "de" if "_de" in channel_id
        else "fr" if "_fr" in channel_id
        else "es" if "_es" in channel_id
        else "de",
    )


def get_library_dir(channel_id: str) -> Path:
    niche = get_niche(channel_id)
    return LIBRARY_MAP.get(niche, BASE_DIR / "library_cosmos")


def get_clips_dir(channel_id: str) -> Path:
    return get_library_dir(channel_id) / "clips"


def get_library_json(channel_id: str) -> Path:
    return get_library_dir(channel_id) / "library.json"


def get_usage_history(channel_id: str) -> Path:
    # Per-channel file: usage_history_de.json / usage_history_fr.json / usage_history_es.json
    # Channels in the same niche (e.g. DE + FR both = cosmos) share the same clip library
    # but track cooldowns independently so each channel gets its own clip variety.
    lang = get_lang(channel_id)
    return get_library_dir(channel_id) / f"usage_history_{lang}.json"


def get_channel_dir(channel_id: str) -> Path:
    return CHANNELS_DIR / get_lang(channel_id)


def get_session_dir(channel_id: str, session: str) -> Path:
    return get_channel_dir(channel_id) / session


def get_input_dir(channel_id: str, session: str) -> Path:
    return get_session_dir(channel_id, session) / "input"


def get_transcripts_dir(channel_id: str, session: str) -> Path:
    return get_session_dir(channel_id, session) / "transcripts"


def get_intro_clips_dir(channel_id: str, session: str) -> Path:
    return get_session_dir(channel_id, session) / "intro_clips"


def get_intro_path(channel_id: str, session: str) -> Path:
    return get_session_dir(channel_id, session) / "intro.mp4"


def get_output_dir(channel_id: str, session: str) -> Path:
    return get_session_dir(channel_id, session) / "output"


def get_audio_path(channel_id: str, session: str) -> "Path | None":
    input_dir = get_input_dir(channel_id, session)
    for ext in ["*.mp3", "*.wav", "*.m4a"]:
        files = list(input_dir.glob(ext))
        if files:
            return files[0]
    return None


def get_result_json(channel_id: str, session: str) -> Path:
    return get_transcripts_dir(channel_id, session) / "result.json"


def get_ass_path(channel_id: str, session: str) -> Path:
    return get_transcripts_dir(channel_id, session) / "subtitles_animated.ass"


def get_final_video(channel_id: str, session: str) -> Path:
    lang = get_lang(channel_id).upper()
    return get_output_dir(channel_id, session) / f"{session}_{lang}_final.mp4"


def get_last_session(channel_id: str) -> "str | None":
    channel_dir = get_channel_dir(channel_id)
    if not channel_dir.exists():
        return None
    sessions = sorted(
        [d for d in channel_dir.iterdir()
         if d.is_dir() and d.name.startswith("Video_")],
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    return sessions[0].name if sessions else None


def ensure_session_dirs(channel_id: str, session: str) -> bool:
    dirs = [
        get_input_dir(channel_id, session),
        get_transcripts_dir(channel_id, session),
        get_intro_clips_dir(channel_id, session),
        get_output_dir(channel_id, session),
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
    return True


def print_session_structure(channel_id: str, session: str) -> None:
    session_dir = get_session_dir(channel_id, session)
    lang  = get_lang(channel_id).upper()
    niche = get_niche(channel_id)
    lib   = get_library_dir(channel_id)
    print(f"""
{'='*50}
📁 {session_dir}
├── input\\
│   └── audio.mp3
├── transcripts\\
│   ├── result.json
│   └── subtitles_animated.ass
├── intro_clips\\
├── intro.mp4
└── output\\
    └── {session}_{lang}_final.mp4
📚 Библиотека [{niche}]: {lib}
{'='*50}
""", flush=True)
