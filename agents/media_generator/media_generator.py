"""
Media Generator Agent
----------------------
Поддерживаемые платформы:
  1. Google Flow  (браузер + куки)
  2. Grok         (браузер + куки) — image-to-video (SuperGrok)
  3. PixelAgent   (API, asyncio + aiohttp, параллельная генерация)

Запуск:
  py agents/media_generator.py                             # интерактивный
  py agents/media_generator.py --platform 3 --type photo  # из бота
  py agents/media_generator.py --platform 2 --type video --session Video_xxx
"""

import argparse
import asyncio
import base64
import io
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import aiohttp
import requests
from PIL import Image
from dotenv import load_dotenv, set_key
from playwright.sync_api import sync_playwright

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# Пути и конфиг
# ---------------------------------------------------------------------------

BASE_DIR    = Path(__file__).parent.parent
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
PIXEL_MAX_CONCURRENT = int(os.environ.get("PIXEL_MAX_CONCURRENT", "5"))

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


# ---------------------------------------------------------------------------
# TG уведомления (из media_generator)
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
# Прогресс Grok (temp/grok_progress.json)
# ---------------------------------------------------------------------------

def grok_load_progress(session: str) -> set[int]:
    try:
        if GROK_PROGRESS_FILE.exists():
            data = json.loads(GROK_PROGRESS_FILE.read_text(encoding="utf-8"))
            if data.get("session") == session:
                return set(data.get("completed", []))
    except Exception:
        pass
    return set()


def grok_save_progress(session: str, completed: set[int], total: int) -> None:
    try:
        GROK_PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
        GROK_PROGRESS_FILE.write_text(
            json.dumps({"session": session, "total": total,
                        "completed": sorted(completed)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        print(f"  [progress] ошибка сохранения: {e}")


# ---------------------------------------------------------------------------
# Прогресс PixelAgent (temp/pixel_progress.json)
# ---------------------------------------------------------------------------

def _write_pixel_progress(current: int, total: int, status: str, failed: list[int]) -> None:
    PIXEL_PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        PIXEL_PROGRESS_FILE.write_text(
            json.dumps({
                "current": current,
                "total":   total,
                "status":  status,
                "failed":  sorted(failed),
                "threads": PIXEL_MAX_CONCURRENT,
            }, ensure_ascii=False),
            encoding="utf-8",
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


# ---------------------------------------------------------------------------
# PixelAgent API — параллельная генерация фото (asyncio + aiohttp)
# ---------------------------------------------------------------------------

async def _pixel_generate_one(
    http: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    idx: int,
    prompt: str,
    out_path: Path,
    total: int,
    progress: dict,
) -> bool:
    """Генерирует одно фото: 3 попытки, проверка ориентации PIL."""
    async with sem:
        for attempt in range(1, 4):
            try:
                async with http.post(
                    f"{PIXEL_API_URL}/api/v1/image/create",
                    json={"prompt": prompt, "aspect_ratio": "16:9"},
                    timeout=aiohttp.ClientTimeout(total=120),
                ) as resp:
                    if resp.status == 401:
                        print(f"  [{idx}] 401 — неверный API ключ", flush=True)
                        return False
                    if resp.status != 200:
                        body = await resp.text()
                        print(f"  [{idx}] HTTP {resp.status}: {body[:80]}, попытка {attempt}", flush=True)
                        await asyncio.sleep(10 * attempt)
                        continue

                    data = await resp.json()
                    img_b64 = data.get("image_b64")
                    if not img_b64:
                        print(f"  [{idx}] нет image_b64 в ответе, попытка {attempt}", flush=True)
                        await asyncio.sleep(10 * attempt)
                        continue

                    img_data = base64.b64decode(img_b64)

                    # Проверка ориентации: ОБЯЗАТЕЛЬНО горизонтальное (w >= h)
                    img = Image.open(io.BytesIO(img_data))
                    w, h = img.size
                    if w < h:
                        raise ValueError(f"Вертикальное фото {w}x{h} — повтор")

                    out_path.write_bytes(img_data)
                    size_kb = len(img_data) // 1024
                    print(f"  [{idx}/{total}] ✓  {out_path.name} ({size_kb}KB)", flush=True)
                    progress["done"] += 1
                    _write_pixel_progress(progress["done"], total, "running", progress["failed"])
                    return True

            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"  [{idx}] попытка {attempt} ошибка: {e}", flush=True)
                if attempt < 3:
                    await asyncio.sleep(10 * attempt)

    # Все 3 попытки провалились
    print(f"  [{idx}/{total}] ✗  FAILED", flush=True)
    fail_path = out_path.parent / f"photo_{idx:03d}_FAILED.txt"
    try:
        fail_path.write_text(
            f"Failed after 3 attempts\nPrompt: {prompt[:200]}",
            encoding="utf-8",
        )
    except Exception:
        pass
    progress["failed"].append(idx)
    progress["done"] += 1
    _write_pixel_progress(progress["done"], total, "running", progress["failed"])
    return False


async def generate_pixel_photos_async(
    prompts: list[str],
    out_dir: Path,
    api_key: str,
) -> tuple[int, list[int]]:
    """
    Асинхронная параллельная генерация фото через PixelAgent API.
    Возвращает (saved_count, failed_indices).
    """
    total    = len(prompts)
    progress = {"done": 0, "failed": []}

    # Определяем что уже готово
    to_generate: list[tuple[int, str]] = []
    for idx, prompt in enumerate(prompts, start=1):
        out_path = out_dir / f"photo_{idx:03d}.png"
        if out_path.exists() and out_path.stat().st_size > 500:
            progress["done"] += 1
        else:
            to_generate.append((idx, prompt))

    already = total - len(to_generate)
    if already:
        print(f"  Пропускаю уже готовые: {already}/{total}", flush=True)
    if not to_generate:
        print("  Все фото уже готовы!", flush=True)
        _write_pixel_progress(total, total, "completed", [])
        return total, []

    print(
        f"\n  PixelAgent | Осталось: {len(to_generate)}/{total} фото | "
        f"{PIXEL_MAX_CONCURRENT} потоков\n",
        flush=True,
    )
    _write_pixel_progress(progress["done"], total, "running", [])

    sem = asyncio.Semaphore(PIXEL_MAX_CONCURRENT)
    # X-API-Key — корректный заголовок (Authorization: Bearer возвращает 401)
    headers = {
        "X-API-Key":    api_key,
        "Content-Type": "application/json",
    }

    async with aiohttp.ClientSession(headers=headers) as http:
        tasks = [
            asyncio.create_task(
                _pixel_generate_one(
                    http, sem, idx, prompt,
                    out_dir / f"photo_{idx:03d}.png",
                    total, progress,
                )
            )
            for idx, prompt in to_generate
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

    # Считаем реально сохранённые файлы
    saved = sum(
        1 for idx in range(1, total + 1)
        if (out_dir / f"photo_{idx:03d}.png").exists()
        and (out_dir / f"photo_{idx:03d}.png").stat().st_size > 500
    )
    _write_pixel_progress(total, total, "completed", sorted(progress["failed"]))
    return saved, sorted(progress["failed"])


def generate_pixel(media_type: str, prompts: list[str],
                   out_dir: Path, api_key: str) -> int:
    """Синхронная обёртка для asyncio: запускает асинхронную генерацию."""
    if media_type != "photo":
        print(f"  [!] PixelAgent поддерживает только фото (запрошено: {media_type})")
        return 0

    total = len(prompts)
    print(f"\n  PixelAgent | Всего: {total} фото\n")

    saved, failed = asyncio.run(generate_pixel_photos_async(prompts, out_dir, api_key))

    if failed:
        print(f"\n  ⚠️  Ошибок: {len(failed)} — созданы заглушки *_FAILED.txt")
        print(f"  Провалившиеся: {failed}")
        send_tg_notification(
            f"⚠️ PixelAgent: {len(failed)}/{total} фото не сгенерированы\n"
            f"Провалившиеся: {failed}"
        )

    print(f"\nСгенерировано: {saved}")
    print(f"Всего: {total}")
    return saved


# ---------------------------------------------------------------------------
# Grok image-to-video  (точный порядок действий)
# ---------------------------------------------------------------------------

def _grok_screenshot(page, idx: int, step: str) -> None:
    """Сохраняет отладочный скриншот в temp/grok_check_N_step.png."""
    try:
        GROK_DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        path = GROK_DEBUG_DIR / f"grok_check_{idx:03d}_{step}.png"
        page.screenshot(path=str(path), timeout=8000, animations="disabled")
        print(f"    [debug] {path.name}")
    except Exception as e:
        print(f"    [debug] скриншот не удался: {e}")


def _grok_try_locators(page, selectors: list[str], timeout_ms: int = 3000):
    """Возвращает первый найденный locator из списка или None."""
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="visible", timeout=timeout_ms)
            return loc
        except Exception:
            continue
    return None


def _grok_click_image_btn(page) -> bool:
    """Шаг 2: Находит и кликает кнопку загрузки изображения."""
    file_input = page.locator("input[type='file']").first
    if file_input.count() > 0:
        return True

    btn = _grok_try_locators(page, [
        "button[aria-label*='image' i]",
        "button[aria-label*='Image' i]",
        "button[aria-label*='photo' i]",
        "button[aria-label*='attach' i]",
        "button[aria-label*='upload' i]",
        "button[aria-label*='Add image' i]",
        "[data-testid*='image-upload']",
        "[data-testid*='attach']",
        "label[for*='file']",
    ], timeout_ms=2000)

    if btn:
        btn.click()
        page.wait_for_timeout(800)
        return True

    print("    [!] Кнопка загрузки изображения не найдена")
    return False


def _grok_upload_photo(page, photo_path: Path) -> bool:
    """Шаги 2-4: Кликает кнопку, загружает фото, ждёт появления превью."""
    _grok_click_image_btn(page)
    page.wait_for_timeout(500)

    file_input = page.locator("input[type='file']").first
    if file_input.count() == 0:
        print("    [!] file input не найден после клика")
        return False

    file_input.set_input_files(str(photo_path))
    print(f"    Файл передан: {photo_path.name}")

    preview_selectors = [
        "img[src*='preview-image']",
        "img[src*='blob:']",
        "[data-testid*='uploaded-image']",
        "[data-testid*='attachment']",
        "figure img",
        "[class*='attachment'] img",
        "[class*='upload'] img",
    ]
    for sel in preview_selectors:
        try:
            page.wait_for_selector(sel, timeout=5000)
            print(f"    Фото загружено (превью: {sel})")
            return True
        except Exception:
            continue

    page.wait_for_timeout(2000)
    print("    Фото загружено (превью не обнаружено, продолжаю)")
    return True


def _grok_set_video_mode(page) -> bool:
    """Шаг 6: Переключает тип генерации на Video через dropdown на /imagine."""
    dropdown_btn = None
    try:
        btns = page.locator('button[aria-haspopup="menu"]')
        count = btns.count()
        for i in range(count):
            btn = btns.nth(i)
            try:
                bbox = btn.bounding_box()
                if bbox and bbox['width'] > 80:
                    dropdown_btn = btn
                    break
            except Exception:
                continue
    except Exception as e:
        print(f"    [!] Ошибка поиска dropdown: {e}")

    if dropdown_btn is None:
        print("    [!] Dropdown кнопка (aria-haspopup=menu) не найдена")
        return False

    btn_text = ""
    try:
        btn_text = dropdown_btn.inner_text().strip()
    except Exception:
        pass
    print(f"    Dropdown: '{btn_text}' — открываю...")

    if "идео" in btn_text:
        print("    Video уже выбран в dropdown")
        return True

    dropdown_btn.click()
    page.wait_for_timeout(800)

    video_item = _grok_try_locators(page, [
        "[role='menuitemradio']:has-text('Видео')",
        "[role='menuitem']:has-text('Видео')",
        "[role='option']:has-text('Видео')",
        "li:has-text('Видео')",
        ":text-is('Видео')",
    ], timeout_ms=3000)

    if video_item is None:
        page.keyboard.press("Escape")
        print("    [!] Пункт 'Видео' в меню не найден (возможно, требуется SuperGrok)")
        return False

    video_item.click()
    page.wait_for_timeout(800)
    print("    Video режим выбран через dropdown")
    return True


def _grok_set_duration(page, seconds: int = 10) -> bool:
    """Шаг 7: Устанавливает длительность генерации (10 сек)."""
    label = f"{seconds}"

    dur_btn = _grok_try_locators(page, [
        f"button[aria-label='{label}s']",
        f"button[aria-label='{label} seconds']",
        f"button:has-text('{label}s')",
        f"button:has-text('{label} sec')",
        f"[data-value='{label}']",
        f"[data-testid*='duration']:has-text('{label}')",
        f"option[value='{label}']",
    ], timeout_ms=3000)

    if not dur_btn:
        try:
            sel_el = page.locator("select[name*='duration'], select[aria-label*='duration' i]").first
            if sel_el.count() > 0:
                sel_el.select_option(label)
                print(f"    Длительность {label}с выбрана (select)")
                return True
        except Exception:
            pass
        print(f"    [!] Контрол длительности {label}с не найден — продолжаю без него")
        return False

    dur_btn.click()
    page.wait_for_timeout(500)
    print(f"    Длительность {label}с выбрана")
    return True


def _grok_verify_state(page, idx: int) -> dict:
    """Шаг 8: Проверяет состояние перед отправкой."""
    state = {
        "photo_loaded":  False,
        "prompt_filled": False,
        "video_mode":    False,
        "duration_10s":  False,
    }

    try:
        photo_indicators = [
            "img[src*='preview-image']",
            "figure img",
            "[class*='attachment'] img",
            "[class*='upload'] img",
            "img[src*='blob:']",
        ]
        for sel in photo_indicators:
            if page.locator(sel).count() > 0:
                state["photo_loaded"] = True
                break

        ta = page.locator("div[contenteditable='true'], textarea, [role='textbox']").first
        if ta.count() > 0:
            try:
                val = ta.inner_text()
            except Exception:
                try:
                    val = ta.input_value()
                except Exception:
                    val = ""
            state["prompt_filled"] = len(val.strip()) > 10

        video_indicators = page.evaluate("""() => {
            const els = Array.from(document.querySelectorAll('button, [role="tab"], [role="radio"], label'));
            return els.some(el => {
                const txt = (el.textContent || '').trim().toLowerCase();
                const sel = el.getAttribute('aria-selected') === 'true';
                const chk = el.getAttribute('aria-checked') === 'true';
                const cls = el.className || '';
                return txt === 'video' && (sel || chk || cls.includes('active') || cls.includes('selected'));
            });
        }""")
        state["video_mode"] = bool(video_indicators)

        dur_indicators = page.evaluate("""() => {
            const els = Array.from(document.querySelectorAll('button, [role="option"], option'));
            return els.some(el => {
                const txt = (el.textContent || '').trim();
                return (txt === '10s' || txt === '10 sec' || txt === '10') &&
                    (el.getAttribute('aria-selected') === 'true' ||
                     el.getAttribute('aria-checked') === 'true' ||
                     (el.className || '').includes('active') ||
                     (el.className || '').includes('selected') ||
                     el.selected === true);
            });
        }""")
        state["duration_10s"] = bool(dur_indicators)

    except Exception as e:
        print(f"    [verify] ошибка: {e}")

    _grok_screenshot(page, idx, "before_send")

    marks = {True: "[v]", False: "[ ]"}
    print(f"    Чеклист перед отправкой:")
    print(f"      {marks[state['photo_loaded']]}  Фото загружено")
    print(f"      {marks[state['prompt_filled']]}  Промпт вставлен")
    print(f"      {marks[state['video_mode']]}  Режим VIDEO")
    print(f"      {marks[state['duration_10s']]}  Длительность 10с")

    return state


def _grok_find_video_url(page) -> str | None:
    """Ищет URL готового видео: <video src> или CDN generated_video.mp4."""
    try:
        url = page.evaluate("""() => {
            const v = document.querySelector('video[src]');
            if (v && v.src && !v.src.startsWith('data:')) return v.src;
            const v2 = document.querySelector('video');
            if (v2 && v2.currentSrc && !v2.currentSrc.startsWith('data:')) return v2.currentSrc;
            const s = document.querySelector('video source[src]');
            if (s && s.src) return s.src;
            const a = Array.from(document.querySelectorAll('a[href]'))
                .find(l => l.href.includes('.mp4'));
            if (a) return a.href;
            return null;
        }""")
        return url if url else None
    except Exception:
        return None


def _grok_download_video(page, video_url: str) -> bytes | None:
    """Скачивает видео через браузер с credentials."""
    try:
        b64 = page.evaluate(
            """async (url) => {
                const r = await fetch(url, {credentials: 'include'});
                if (!r.ok) return null;
                const buf = await r.arrayBuffer();
                const bytes = new Uint8Array(buf);
                let bin = '';
                for (let i = 0; i < bytes.length; i += 8192)
                    bin += String.fromCharCode(...bytes.subarray(i, i + 8192));
                return btoa(bin);
            }""",
            video_url,
        )
        if b64:
            data = base64.b64decode(b64)
            if len(data) > 1024:
                return data
        print(f"    [dl] пустой ответ для {video_url[:60]}")
    except Exception as e:
        print(f"    [dl] ошибка: {e}")
    return None


def _wait_for_grok_video(page, timeout: int = GROK_VIDEO_TIMEOUT) -> str | None:
    """Ждёт появления <video> элемента с generated_video.mp4."""
    deadline = time.time() + timeout
    poll = 5
    ticks = 0

    while time.time() < deadline:
        time.sleep(poll)
        ticks += 1
        if ticks % 6 == 0:
            remaining = int(deadline - time.time())
            print(f"    [ожидание... осталось ~{remaining}с]", flush=True)

        url = _grok_find_video_url(page)
        if url:
            return url

        try:
            progress = page.evaluate("""() => {
                const txt = document.body.innerText;
                const m = txt.match(/(\\d+)%/);
                return m ? m[1] : null;
            }""")
            if progress and ticks % 3 == 0:
                print(f"    [прогресс: {progress}%]", flush=True)
        except Exception:
            pass

    return None


def _grok_generate_one(page, idx: int, total: int, photo_path: Path,
                        prompt: str, out_path: Path) -> bool:
    """
    Генерирует одно видео через Grok /imagine.
    Порядок: navigate → upload photo → insert prompt → VIDEO → 10s → verify → generate → wait → download.
    """
    IMAGINE_URL = "https://grok.com/imagine"

    # Шаг 1: Открываем /imagine
    page.goto(IMAGINE_URL, wait_until="domcontentloaded", timeout=30_000)
    page.wait_for_timeout(2000)
    _grok_screenshot(page, idx, "1_imagine")

    # Шаг 2: Загрузка фото
    print("    [2] Загрузка фото...", flush=True)
    ok = _grok_upload_photo(page, photo_path)
    if not ok:
        time.sleep(5)
        ok = _grok_upload_photo(page, photo_path)
    if not ok:
        print("    ОШИБКА: не удалось загрузить фото")
        _grok_screenshot(page, idx, "err_photo")
        return False
    page.wait_for_timeout(800)
    _grok_screenshot(page, idx, "2_photo_loaded")

    # Шаг 3: Вставляем промпт
    print("    [3] Вставляю промпт...", flush=True)
    text_area = _grok_try_locators(page, [
        "div[contenteditable='true']",
        "[role='textbox']",
        "textarea",
        "[placeholder*='Опиши' i]",
        "[placeholder*='prompt' i]",
    ], timeout_ms=5000)

    if text_area is None:
        print("    ОШИБКА: поле ввода не найдено")
        _grok_screenshot(page, idx, "err_textarea")
        return False

    text_area.click()
    page.wait_for_timeout(200)
    page.evaluate(f"""() => {{
        const el = document.activeElement;
        const txt = {json.dumps(prompt)};
        if (el && el.isContentEditable) {{
            el.textContent = '';
            document.execCommand('insertText', false, txt);
        }}
    }}""")
    page.wait_for_timeout(400)
    print(f"    Промпт вставлен ({len(prompt)} символов)")
    _grok_screenshot(page, idx, "3_prompt_filled")

    # Шаг 4: Переключаем в VIDEO режим
    print("    [4] Переключаю в VIDEO режим...", flush=True)
    _grok_set_video_mode(page)
    page.wait_for_timeout(600)
    _grok_screenshot(page, idx, "4_video_mode")

    # Шаг 5: Устанавливаем 10 секунд
    print("    [5] Устанавливаю длительность 10с...", flush=True)
    _grok_set_duration(page, seconds=10)
    page.wait_for_timeout(400)

    # Шаг 6: Проверяем состояние
    print("    [6] Проверка состояния...", flush=True)
    _grok_verify_state(page, idx)

    # Шаг 7: Нажимаем Generate
    print("    [7] Нажимаю Generate...", flush=True)
    send_btn = _grok_try_locators(page, [
        "button[type='submit']",
        "button[aria-label*='send' i]",
        "button[aria-label*='generate' i]",
        "[data-testid*='send']",
        "button[aria-label='Send message']",
    ], timeout_ms=3000)

    if send_btn:
        send_btn.click()
    else:
        text_area.press("Enter")
    page.wait_for_timeout(1500)
    _grok_screenshot(page, idx, "7_sent")

    # Шаг 8: Ждём <video> с generated_video.mp4
    print(f"    [8] Жду видео (до {GROK_VIDEO_TIMEOUT}с)...", flush=True)
    video_url = _wait_for_grok_video(page)

    if not video_url:
        print("    ОШИБКА: таймаут — видео не появилось")
        _grok_screenshot(page, idx, "err_timeout")
        return False

    print(f"    Видео: ...{video_url[-60:]}")
    _grok_screenshot(page, idx, "8_video_found")

    # Шаг 9: Скачиваем и сохраняем
    print("    [9] Скачиваю...", flush=True)
    video_bytes = _grok_download_video(page, video_url)

    if video_bytes and len(video_bytes) > 1024:
        out_path.write_bytes(video_bytes)
        size_kb = len(video_bytes) // 1024
        print(f"    [v] {out_path.name} сохранено ({size_kb} KB)")
        return True
    else:
        print("    ОШИБКА: пустой ответ при скачивании")
        return False


def generate_grok_video(
    platform: dict,
    photos: list[Path],
    prompts: list[str],
    out_dir: Path,
    session: str = "",
) -> int:
    """
    Генерация видео через Grok (grok.com/imagine).
    Требуется SuperGrok подписка.
    Поддерживает: прогресс, 3 попытки с перезапуском, TG уведомление каждые 10 клипов.
    """
    if not cookies_exist(platform):
        run_setup_mode(platform)
        print("Перезапусти агент для начала генерации.")
        return 0

    total = min(len(photos), len(prompts))

    completed_set = grok_load_progress(session)
    if completed_set:
        print(f"  Прогресс загружен: {len(completed_set)}/{total} уже готово")

    saved = 0
    for idx in range(1, total + 1):
        out_path = out_dir / f"video_{idx:03d}.mp4"
        if out_path.exists() and out_path.stat().st_size > 1024:
            completed_set.add(idx)
            saved += 1

    print(f"  Всего видео: {total}  (пропускаю уже готовые: {saved})\n")

    def _run_with_page(start_idx: int) -> tuple[int, bool]:
        nonlocal saved

        chrome_proc = None
        if not is_cdp_open():
            chrome_proc = launch_chrome(platform["profile_dir"], platform["url"])

        local_saved = 0
        need_restart = False

        try:
            with sync_playwright() as pw:
                browser = pw.chromium.connect_over_cdp(
                    f"http://localhost:{CHROME_CDP_PORT}"
                )
                ctx = browser.contexts[0] if browser.contexts else browser.new_context()

                cookies = load_cookies(platform)
                ctx.add_cookies(cookies)
                print(f"  Куки применены ({len(cookies)} записей)")

                IMAGINE_URL = "https://grok.com/imagine"
                page = None
                for p in ctx.pages:
                    if "grok" in p.url:
                        page = p
                        break
                if page is None:
                    page = ctx.new_page()

                page.goto(IMAGINE_URL, wait_until="domcontentloaded", timeout=60_000)
                page.wait_for_timeout(2000)

                if any(kw in page.url for kw in ["login", "signin", "auth"]):
                    print("  Куки протухли — запускаю setup mode...")
                    invalidate_cookies(platform)
                    run_setup_mode(platform)
                    print("Перезапусти агент для начала генерации.")
                    return local_saved, False

                for idx in range(start_idx, total + 1):
                    if idx in completed_set:
                        continue

                    photo_path = photos[idx - 1]
                    prompt     = prompts[idx - 1]
                    out_path   = out_dir / f"video_{idx:03d}.mp4"

                    print(f"\n  [{idx}/{total}] {photo_path.stem}", flush=True)

                    success = False
                    for attempt in range(1, GROK_MAX_RETRIES + 1):
                        if attempt > 1:
                            print(f"    Попытка {attempt}/{GROK_MAX_RETRIES}...", flush=True)
                            try:
                                page.goto(IMAGINE_URL, wait_until="domcontentloaded", timeout=30_000)
                                page.wait_for_timeout(3000)
                            except Exception:
                                need_restart = True
                                break
                        try:
                            success = _grok_generate_one(
                                page, idx, total, photo_path, prompt, out_path
                            )
                        except Exception as e:
                            print(f"    ОШИБКА (попытка {attempt}): {e}")
                            _grok_screenshot(page, idx, f"err_attempt{attempt}")
                            if attempt == GROK_MAX_RETRIES:
                                need_restart = True
                        if success:
                            break

                    if success:
                        completed_set.add(idx)
                        saved += 1
                        local_saved += 1
                        grok_save_progress(session, completed_set, total)

                        # TG уведомление каждые 10 клипов
                        if saved % 10 == 0:
                            send_tg_notification(
                                f"🎬 Grok: сгенерировано {saved}/{total} видео"
                                + (f" [{session}]" if session else "")
                            )
                    elif need_restart:
                        break

                    if idx < total:
                        time.sleep(3)

                try:
                    save_cookies(platform, ctx.cookies())
                except Exception:
                    pass

        finally:
            if chrome_proc:
                chrome_proc.terminate()
                print("  Chrome закрыт.")

        return local_saved, need_restart

    # Основной цикл с перезапуском Chrome при ошибках
    restart_attempts = 0
    start_from = 1
    while True:
        _, need_restart = _run_with_page(start_from)
        if not need_restart:
            break
        restart_attempts += 1
        if restart_attempts >= GROK_MAX_RETRIES:
            print(f"  [!] Достигнут лимит перезапусков ({GROK_MAX_RETRIES}), останавливаюсь.")
            break
        remaining = [i for i in range(1, total + 1) if i not in completed_set]
        if not remaining:
            break
        start_from = remaining[0]
        print(f"\n  [перезапуск Chrome] продолжаю с видео #{start_from}...\n")
        time.sleep(10)

    if saved == total:
        send_tg_notification(
            f"✅ Grok: все {total} видео готовы!"
            + (f"\n📁 {session}" if session else "")
        )

    return saved


# ---------------------------------------------------------------------------
# Генерация через браузер (общая + dispatch)
# ---------------------------------------------------------------------------

def generate_browser(platform: dict, media_type: str, prompts: list[str],
                     out_dir: Path, session: str = "") -> int:
    """
    Генерация через браузер с куками.
    Для Grok + video — использует специальную image-to-video логику.
    """
    if platform.get("key") == "2" and media_type == "video":
        if not session:
            print("  [!] session не передан — не могу найти фото")
            return 0
        photos = read_photos(session)
        if not photos:
            print(f"  [!] Нет фото в data/media/{session}/photos/")
            print("      Сначала сгенерируй фото (PixelAgent → Фото)")
            return 0
        n = min(len(photos), len(prompts))
        if len(photos) != len(prompts):
            print(f"  [!] Фото: {len(photos)}, промптов: {len(prompts)} — беру первые {n}")
        return generate_grok_video(platform, photos[:n], prompts[:n], out_dir, session=session)

    if not cookies_exist(platform):
        run_setup_mode(platform)
        print("Перезапусти агента для начала генерации.")
        return 0

    ext    = "jpg" if media_type == "photo" else "mp4"
    prefix = "photo" if media_type == "photo" else "video"
    saved  = 0
    session_expired = False

    chrome_proc = None
    if not is_cdp_open():
        chrome_proc = launch_chrome(platform["profile_dir"], platform["url"])

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(
                f"http://localhost:{CHROME_CDP_PORT}"
            )
            context = browser.contexts[0] if browser.contexts else browser.new_context()

            cookies = load_cookies(platform)
            context.add_cookies(cookies)
            print(f"  Куки применены ({len(cookies)} записей)")

            page = None
            for p in context.pages:
                if platform["url"].replace("https://", "") in p.url:
                    page = p
                    break
            if page is None:
                page = context.new_page()
                page.goto(platform["url"], wait_until="networkidle", timeout=60000)

            total = len(prompts)
            for idx, prompt in enumerate(prompts, start=1):
                out_path = out_dir / f"{prefix}_{idx:03d}.{ext}"
                if out_path.exists():
                    print(f"  [{idx}/{total}] Пропускаю (уже есть): {out_path.name}")
                    continue
                print(f"  [{idx}/{total}] {prompt[:70]}...")
                print(f"  [!] Генерация через {platform['name']} — требует настройки")
                break

            if not session_expired:
                fresh = context.cookies()
                save_cookies(platform, fresh)

    finally:
        if chrome_proc:
            chrome_proc.terminate()
            print("  Chrome закрыт.")

    return saved


# ---------------------------------------------------------------------------
# Главная функция
# ---------------------------------------------------------------------------

def run() -> None:
    print("\n=== Media Generator ===")

    parser = argparse.ArgumentParser(description="Генератор медиа (фото и видео)")
    parser.add_argument("--platform", choices=["1", "2", "3"],
                        help="Платформа: 1=Flow, 2=Grok, 3=PixelAgent")
    parser.add_argument("--type", dest="gen_type", choices=["photo", "video", "both"],
                        help="Тип: photo | video | both")
    parser.add_argument("--session", help="Имя сессии (по умолчанию — последняя)")
    args = parser.parse_args()

    # ── Сессия ────────────────────────────────────────────────────────────
    session = args.session or find_latest_session()
    if not session:
        print("Нет сессий в data/prompts/")
        return
    print(f"Сессия: {session}")

    # ── Платформа ─────────────────────────────────────────────────────────
    if args.platform:
        platform = PLATFORMS[args.platform].copy()
        platform["key"] = args.platform
        print(f"Платформа: {platform['name']}")
    else:
        platform = ask_platform()

    # ── API ключ для PixelAgent ────────────────────────────────────────────
    api_key = None
    if platform["type"] == "api":
        if not PIXEL_API_URL or not PIXEL_API_KEY:
            print("❌ Заполни PIXEL_API_URL и PIXEL_API_KEY в config/.env")
            return
        api_key = PIXEL_API_KEY

    # ── Тип генерации ─────────────────────────────────────────────────────
    if args.gen_type:
        raw = args.gen_type
        if platform["type"] == "api" and raw == "video":
            print(f"ОШИБКА: {platform['name']} не поддерживает видео")
            return
        media_types = platform["supports"] if raw == "both" else [raw]
    else:
        media_types = ask_media_type(platform)

    # ── Генерация ──────────────────────────────────────────────────────────
    start_time   = time.time()
    total_saved  = 0
    total_prompts = 0

    for media_type in media_types:
        print(f"\n--- {media_type.upper()} ---")

        prompts = read_prompts(session, media_type)
        if not prompts:
            print(f"  Нет промптов для {media_type}, пропускаю.")
            continue

        total_prompts = len(prompts)
        folder  = "photos" if media_type == "photo" else "videos"
        out_dir = make_output_dir(session, folder)
        print(f"  Папка: {out_dir}")

        if platform["type"] == "api":
            saved = generate_pixel(media_type, prompts, out_dir, api_key)
        else:
            saved = generate_browser(platform, media_type, prompts, out_dir, session=session)

        total_saved += saved

    # ── Статистика ────────────────────────────────────────────────────────
    elapsed = time.time() - start_time
    hours, rem = divmod(int(elapsed), 3600)
    minutes, seconds = divmod(rem, 60)
    folder_label = "videos" if "video" in media_types else "photos"

    if hours:
        time_str = f"{hours} ч {minutes} мин {seconds} сек"
    elif minutes:
        time_str = f"{minutes} мин {seconds} сек"
    else:
        time_str = f"{seconds} сек"

    print(f"\n{'='*50}")
    print(f"✅ Готово! {total_saved} {'видео' if 'video' in media_types else 'файлов'} сгенерировано")
    print(f"📁 {MEDIA_DIR / session / folder_label}/")
    print(f"⏱  Время: {time_str}")
    print(f"Сгенерировано: {total_saved}")
    print(f"Всего: {total_prompts}")
    print(f"{'='*50}")


if __name__ == "__main__":
    run()
