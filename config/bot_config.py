"""
Bot Configuration
-----------------
Централизованные настройки для Telegram бота.
Импортируется из bot/telegram_bot.py.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / "config" / ".env"

load_dotenv(ENV_FILE)

# ─── Telegram ─────────────────────────────────────────────────────────────────

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()

_uid = os.environ.get("TELEGRAM_ALLOWED_USER_ID", "0").strip()
TELEGRAM_ALLOWED_USER_ID = int(_uid) if _uid.isdigit() else 0

# ─── Пути проекта ─────────────────────────────────────────────────────────────

INPUT_DIR       = BASE_DIR / "data" / "input"
TRANSCRIPTS_DIR = BASE_DIR / "data" / "transcripts"
PROMPTS_DIR     = BASE_DIR / "data" / "prompts"
MEDIA_DIR       = BASE_DIR / "data" / "media"
CONFIG_DIR      = BASE_DIR / "config"
AGENTS_DIR      = BASE_DIR / "agents"

# ─── Платформы ────────────────────────────────────────────────────────────────

# Файлы куков браузерных платформ (media_generator.py)
BROWSER_COOKIES: dict[str, Path] = {
    "1": CONFIG_DIR / "flow_cookies.json",
    "2": CONFIG_DIR / "grok_cookies.json",
    "3": CONFIG_DIR / "runway_cookies.json",
    "4": CONFIG_DIR / "kling_cookies.json",
}

PLATFORM_NAMES: dict[str, str] = {
    "1": "Google Flow",
    "2": "Grok",
    "3": "Runway",
    "4": "Kling",
    "5": "Gemini API",
}

GEMINI_MODEL_NAMES: dict[str, str] = {
    "1": "Nano Banana",          # gemini-2.5-flash-image
    "2": "Nano Banana Pro",      # gemini-3-pro-image-preview
    "3": "Nano Banana 2",        # gemini-3.1-flash-image-preview
}

# ─── Параметры агентов ────────────────────────────────────────────────────────

# Интервал обновления progress-сообщений (секунды)
PROGRESS_INTERVAL_DEFAULT   = 2.5   # transcription, media
PROGRESS_INTERVAL_PROMPTS   = 3.0   # prompts (claude CLI работает медленнее)

# Максимальная длина текста отчёта в Telegram (символов)
VALIDATION_REPORT_MAX_CHARS = 2000
