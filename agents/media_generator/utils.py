"""
Media Generator — Shared utilities
------------------------------------
Конфиг, пути, Chrome-хелперы, cookie-хелперы, хелперы сессий/промптов.
Импортируется pixel_agent, grok_agent, flow_agent и media_generator (роутер).
"""

import json
import os
import re
import socket
import subprocess
import time
from pathlib import Path

import requests
from dotenv import load_dotenv, set_key
from playwright.sync_api import sync_playwright

# ---------------------------------------------------------------------------
# Пути и конфиг
# ---------------------------------------------------------------------------

BASE_DIR    = Path(__file__).parent.parent.parent
ENV_FILE    = BASE_DIR / "config" / ".env"
CONFIG_DIR  = BASE_DIR / "config"
PROMPTS_DIR = BASE_DIR / "data" / "prompts"
MEDIA_DIR   = BASE_DIR / "data" / "media"

load_dotenv(ENV_FILE)

CHROME_PATH     = os.environ.get("CHROME_PATH", r"C:\Program Files\Google\Chrome\Application\chrome.exe")
CHROME_CDP_PORT = int(os.environ.get("CHROME_CDP_PORT", "9222"))
USERPROFILE     = os.environ.get("USERPROFILE", str(Path.home()))

# ---------------------------------------------------------------------------
# Платформы
# ---------------------------------------------------------------------------

PLATFORMS = {
    "1": {
        "name":         "Google Flow",
        "type":         "browser",
        "url":          "https://labs.google/fx/tools/flow",
        "login_url":    "https://accounts.google.com",
        "cookies_file": CONFIG_DIR / "flow_cookies.json",
        "profile_dir":  str(Path(USERPROFILE) / ".chrome-flow-profile"),
        "supports":     ["photo", "video"],
    },
    "2": {
        "name":         "Grok",
        "type":         "browser",
        "url":          "https://grok.com/imagine",
        "login_url":    "https://x.com/login",
        "cookies_file": CONFIG_DIR / "grok_cookies.json",
        "profile_dir":  str(Path(USERPROFILE) / ".chrome-grok-profile"),
        "supports":     ["video"],
    },
    "3": {
        "name":     "PixelAgent",
        "type":     "api",
        "supports": ["photo"],
    },
}

PIXEL_API_URL        = os.environ.get("PIXEL_API_URL", "").strip()
PIXEL_API_KEY        = os.environ.get("PIXEL_API_KEY", "").strip()
PIXEL_MAX_CONCURRENT = int(os.environ.get("PIXEL_MAX_CONCURRENT", "3"))

MAX_RETRIES = 3
RETRY_PAUSE = 5

# Таймаут ожидания видео от Grok (секунды)
GROK_VIDEO_TIMEOUT = 180

# Папка для отладочных скриншотов и прогресса
GROK_DEBUG_DIR      = BASE_DIR / "temp"
GROK_PROGRESS_FILE  = BASE_DIR / "temp" / "grok_progress.json"
PIXEL_PROGRESS_FILE = BASE_DIR / "temp" / "pixel_progress.json"

# Максимум попыток на один клип (с перезапуском браузера)
GROK_MAX_RETRIES = 3

# Количество параллельных вкладок (Chrome-процессов) для Grok
GROK_NUM_TABS = int(os.environ.get("GROK_NUM_TABS", "3"))


# ---------------------------------------------------------------------------
# TG уведомления
# ---------------------------------------------------------------------------

def send_tg_notification(text: str) -> None:
    """Отправляет уведомление в Telegram."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    uid   = os.environ.get("TELEGRAM_ALLOWED_USER_ID", "").strip()
    if not token or not uid:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": uid, "text": text},
            timeout=10,
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Интерфейс выбора
# ---------------------------------------------------------------------------

def ask_platform() -> dict:
    print("\nВыбери платформу:")
    for k, p in PLATFORMS.items():
        tag = "API" if p["type"] == "api" else "браузер"
        print(f"  {k}. {p['name']}  [{tag}]")
    print()
    while True:
        choice = input("Номер (1-3): ").strip()
        if choice in PLATFORMS:
            platform = PLATFORMS[choice].copy()
            platform["key"] = choice
            print(f"\nПлатформа: {platform['name']}")
            return platform
        print("  Неверный ввод, попробуй ещё раз.")


def ask_media_type(platform: dict) -> list[str]:
    supported = platform["supports"]
    if len(supported) == 1:
        return supported

    print("\nЧто генерировать?")
    print("  1. Фото")
    print("  2. Видео")
    if platform["type"] != "api":
        print("  3. Фото + Видео")

    while True:
        choice = input("Номер: ").strip()
        if choice == "1":  return ["photo"]
        if choice == "2":  return ["video"]
        if choice == "3" and platform["type"] != "api":  return ["photo", "video"]
        print("  Неверный ввод.")


def ask_pixel_api_key() -> str:
    key = os.environ.get("PIXEL_API_KEY", "").strip()
    if key:
        return key
    print("\nPIXEL_API_KEY не найден в config/.env")
    key = input("Введи API ключ: ").strip()
    if key:
        set_key(str(ENV_FILE), "PIXEL_API_KEY", key)
        os.environ["PIXEL_API_KEY"] = key
        print("  Ключ сохранён в config/.env")
    return key


# ---------------------------------------------------------------------------
# Утилиты: промпты, фото, сессии
# ---------------------------------------------------------------------------

_SESSION_RE = re.compile(r"^Video_\d{8}_\d{6}$")


def find_latest_session() -> str | None:
    if not PROMPTS_DIR.exists():
        return None
    folders = [
        d for d in PROMPTS_DIR.iterdir()
        if d.is_dir() and _SESSION_RE.match(d.name)
    ]
    if not folders:
        return None
    return max(folders, key=lambda f: f.name).name


def read_prompts(session: str, kind: str) -> list[str]:
    """kind = 'photo' или 'video'"""
    filename = f"{kind}_prompts.txt"
    # Новая структура: data/prompts/{session}/{kind}/
    txt = PROMPTS_DIR / session / kind / filename
    if not txt.exists():  # fallback: старая плоская структура
        txt = PROMPTS_DIR / session / filename
    if not txt.exists():
        print(f"  [!] Файл не найден: {txt}")
        return []
    content = txt.read_text(encoding="utf-8")
    prompts = [p.strip() for p in content.split("\n\n") if p.strip()]
    print(f"  {kind.capitalize()} промптов: {len(prompts)}")
    return prompts


def read_photos(session: str) -> list[Path]:
    """Читает фото из data/media/session/photos/ в порядке photo_001, photo_002..."""
    photos_dir = MEDIA_DIR / session / "photos"
    if not photos_dir.exists():
        print(f"  [!] Папка не найдена: {photos_dir}")
        return []
    photos = sorted(
        [p for p in photos_dir.iterdir()
         if p.stem.startswith("photo_") and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")],
        key=lambda p: p.stem,
    )
    print(f"  Фото найдено: {len(photos)}")
    return photos


def make_output_dir(session: str, kind: str) -> Path:
    """kind = 'photos' или 'videos'"""
    out = MEDIA_DIR / session / kind
    out.mkdir(parents=True, exist_ok=True)
    return out


# ---------------------------------------------------------------------------
# Cookie management (браузерные платформы)
# ---------------------------------------------------------------------------

def cookies_exist(platform: dict) -> bool:
    f = platform["cookies_file"]
    return Path(f).exists() and Path(f).stat().st_size > 20


def load_cookies(platform: dict) -> list[dict]:
    with open(platform["cookies_file"], encoding="utf-8") as f:
        return json.load(f)


def save_cookies(platform: dict, cookies: list[dict]) -> None:
    path = Path(platform["cookies_file"])
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cookies, f, indent=2)
    print(f"  Куки сохранены: {path.name} ({len(cookies)} записей)")


def invalidate_cookies(platform: dict) -> None:
    path = Path(platform["cookies_file"])
    if path.exists():
        path.unlink()
    print(f"  Куки удалены для {platform['name']}")


# ---------------------------------------------------------------------------
# Chrome
# ---------------------------------------------------------------------------

def is_cdp_open() -> bool:
    with socket.socket() as s:
        s.settimeout(1)
        try:
            s.connect(("localhost", CHROME_CDP_PORT))
            return True
        except Exception:
            return False


def launch_chrome(profile_dir: str, start_url: str) -> subprocess.Popen:
    print(f"  Запускаю Chrome...")
    proc = subprocess.Popen([
        CHROME_PATH,
        f"--remote-debugging-port={CHROME_CDP_PORT}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        start_url,
    ])
    for _ in range(20):
        time.sleep(1)
        if is_cdp_open():
            print("  Chrome готов (CDP).")
            return proc
    raise RuntimeError("Chrome не поднял CDP порт за 20 секунд.")


# ---------------------------------------------------------------------------
# Setup mode — первый вход в браузерную платформу
# ---------------------------------------------------------------------------

def run_setup_mode(platform: dict) -> None:
    print(f"\n{'='*55}")
    print(f"[Setup] {platform['name']} — требуется авторизация")
    print(f"{'='*55}")

    chrome_proc = None
    if not is_cdp_open():
        chrome_proc = launch_chrome(platform["profile_dir"], platform["login_url"])
    else:
        print("  Chrome уже запущен.")

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(
                f"http://localhost:{CHROME_CDP_PORT}"
            )
            context = browser.contexts[0] if browser.contexts else browser.new_context()

            login_page = None
            for p in context.pages:
                if any(domain in p.url for domain in ["accounts.google", "x.com", "runwayml", "klingai", "grok"]):
                    login_page = p
                    break
            if login_page is None:
                login_page = context.new_page()
                login_page.goto(platform["login_url"], wait_until="networkidle", timeout=30000)

            print()
            print(f">>> Залогинься в {platform['name']} в открытом Chrome")
            print(">>> После успешного входа нажми Enter здесь...")
            print()
            input()

            cookies = context.cookies()
            save_cookies(platform, cookies)
            print(f"\n[Setup] Готово! Запусти агента снова.\n")

    finally:
        if chrome_proc:
            chrome_proc.terminate()
            print("  Chrome закрыт.")
