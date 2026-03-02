"""
Flow Generator Agent
--------------------
Генерирует изображения через Google Flow API.

Читает:  data/prompts/<session>/photo_prompts.txt
Сохраняет: data/media/<session>/photos/photo_001.jpg, ...

Запуск: py agents/flow_generator.py
--- Первый запуск: открывает Chrome, ждёт логина, сохраняет куки
--- Последующие: использует сохранённые куки автоматически
"""

import asyncio
import io
import json
import os
import random
import socket
import subprocess
import sys
import time
from pathlib import Path

import aiohttp
from dotenv import load_dotenv
from playwright.async_api import async_playwright

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).parent.parent
ENV_FILE = BASE_DIR / "config" / ".env"
COOKIES_FILE = BASE_DIR / "config" / "cookies.json"
PROMPTS_DIR = BASE_DIR / "data" / "prompts"
MEDIA_DIR = BASE_DIR / "data" / "media"

load_dotenv(ENV_FILE)

CHROME_PATH = os.environ.get(
    "CHROME_PATH", r"C:\Program Files\Google\Chrome\Application\chrome.exe"
)
CHROME_CDP_PORT = int(os.environ.get("CHROME_CDP_PORT", "9222"))
CHROME_FLOW_PROFILE_DIR = os.path.expandvars(
    os.environ.get("CHROME_FLOW_PROFILE_DIR", r"%USERPROFILE%\.chrome-flow-profile")
)
FLOW_PROJECT_ID = os.environ.get("FLOW_PROJECT_ID", "")
FLOW_REFERENCE_IDS = [
    r.strip() for r in os.environ.get("FLOW_REFERENCE_IDS", "").split(",") if r.strip()
]
RECAPTCHA_SITE_KEY = os.environ.get("RECAPTCHA_SITE_KEY", "")

API_URL = (
    f"https://aisandbox-pa.googleapis.com/v1/projects/{FLOW_PROJECT_ID}"
    f"/flowMedia:batchGenerateImages"
)
FLOW_URL = "https://labs.google/fx/tools/flow"
LOGIN_URL = "https://accounts.google.com"
MAX_RETRIES = 2


# ---------------------------------------------------------------------------
# Управление куками
# ---------------------------------------------------------------------------

def cookies_exist() -> bool:
    return COOKIES_FILE.exists() and COOKIES_FILE.stat().st_size > 20


def load_cookies() -> list[dict]:
    with open(COOKIES_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_cookies(cookies: list[dict]) -> None:
    COOKIES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(COOKIES_FILE, "w", encoding="utf-8") as f:
        json.dump(cookies, f, indent=2)
    print(f"[Setup] Куки сохранены: {len(cookies)} записей → {COOKIES_FILE.name}")


def invalidate_cookies() -> None:
    if COOKIES_FILE.exists():
        COOKIES_FILE.unlink()
    print("[Auth] Куки удалены — при следующем запуске потребуется переавторизация")


# ---------------------------------------------------------------------------
# Chrome
# ---------------------------------------------------------------------------

def is_cdp_port_open() -> bool:
    with socket.socket() as s:
        s.settimeout(1)
        try:
            s.connect(("localhost", CHROME_CDP_PORT))
            return True
        except Exception:
            return False


def launch_chrome(start_url: str = FLOW_URL) -> subprocess.Popen:
    print(f"[Chrome] Запускаю Chrome (порт {CHROME_CDP_PORT})...")
    proc = subprocess.Popen([
        CHROME_PATH,
        f"--remote-debugging-port={CHROME_CDP_PORT}",
        f"--user-data-dir={CHROME_FLOW_PROFILE_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
        start_url,
    ])
    for _ in range(20):
        time.sleep(1)
        if is_cdp_port_open():
            print("[Chrome] CDP порт готов.")
            return proc
    raise RuntimeError("Chrome не поднял CDP порт за 20 секунд.")


async def connect_browser(pw):
    """Подключается к CDP и возвращает (browser, context)."""
    browser = await pw.chromium.connect_over_cdp(
        f"http://localhost:{CHROME_CDP_PORT}"
    )
    context = (
        browser.contexts[0] if browser.contexts else await browser.new_context()
    )
    return browser, context


# ---------------------------------------------------------------------------
# Setup mode — первый запуск / переавторизация
# ---------------------------------------------------------------------------

async def setup_mode(pw) -> None:
    """
    Открывает Chrome на странице Google Login.
    Ждёт пока пользователь залогинится.
    Сохраняет куки в config/cookies.json.
    """
    print("\n" + "=" * 55)
    print("[Setup] Требуется авторизация Google")
    print("=" * 55)

    chrome_proc = None
    if not is_cdp_port_open():
        chrome_proc = launch_chrome(start_url=LOGIN_URL)
    else:
        print(f"[Chrome] Уже запущен на порту {CHROME_CDP_PORT}")

    try:
        browser, context = await connect_browser(pw)

        # Открываем страницу логина если её нет
        login_page = None
        for p in context.pages:
            if "accounts.google.com" in p.url:
                login_page = p
                break
        if login_page is None:
            login_page = await context.new_page()
            await login_page.goto(LOGIN_URL, wait_until="networkidle", timeout=30000)

        print()
        print(">>> Залогинься в Google в открытом окне Chrome")
        print(">>> После успешного входа нажми Enter здесь...")
        print()
        input()

        # Сохраняем куки всех доменов (google.com, labs.google и т.д.)
        cookies = await context.cookies([
            "https://accounts.google.com",
            "https://google.com",
            "https://labs.google",
        ])
        save_cookies(cookies)

        print("[Setup] Авторизация завершена.")
        print("[Setup] Запусти агента снова для генерации изображений.\n")

    finally:
        if chrome_proc:
            print("[Chrome] Закрываю Chrome...")
            chrome_proc.terminate()


# ---------------------------------------------------------------------------
# reCAPTCHA и Bearer token
# ---------------------------------------------------------------------------

async def get_recaptcha_token(page) -> str:
    await page.wait_for_function(
        "typeof grecaptcha !== 'undefined' && typeof grecaptcha.enterprise !== 'undefined'",
        timeout=30000,
    )
    token = await page.evaluate(
        f"grecaptcha.enterprise.execute('{RECAPTCHA_SITE_KEY}', {{action: 'IMAGE_GENERATION'}})"
    )
    if not token:
        raise RuntimeError("reCAPTCHA token пустой")
    return token


async def get_bearer_token(page) -> str:
    result = await page.evaluate("""
        async () => {
            const r = await fetch('/fx/api/auth/session');
            const d = await r.json();
            return d.token || d.accessToken || d.access_token || '';
        }
    """)
    if not result:
        raise PermissionError("Bearer token пустой — сессия протухла")
    print(f"[Flow] Bearer token получен ({len(result)} символов)")
    return result


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def build_request_body(prompt: str, recaptcha_token: str) -> dict:
    session_id = f";{int(time.time() * 1000)}"
    client_context = {
        "recaptchaContext": {
            "token": recaptcha_token,
            "applicationType": "RECAPTCHA_APPLICATION_TYPE_WEB",
        },
        "sessionId": session_id,
        "projectId": FLOW_PROJECT_ID,
        "tool": "PINHOLE",
    }
    return {
        "clientContext": client_context,
        "requests": [{
            "clientContext": client_context,
            "seed": random.randint(1, 2 ** 31),
            "imageModelName": "GEM_PIX_2",
            "imageAspectRatio": "IMAGE_ASPECT_RATIO_LANDSCAPE",
            "prompt": prompt,
            "imageInputs": [
                {"name": ref, "imageInputType": "IMAGE_INPUT_TYPE_REFERENCE"}
                for ref in FLOW_REFERENCE_IDS
            ],
        }],
    }


def extract_image_url(data: dict) -> str | None:
    for item in data.get("generatedImages", []):
        url = item.get("image", {}).get("fifeUrl")
        if url:
            return url
    for item in data.get("media", []):
        url = item.get("image", {}).get("generatedImage", {}).get("fifeUrl")
        if url:
            return url
    return None


async def generate_image(
    http: aiohttp.ClientSession,
    prompt: str,
    bearer_token: str,
    recaptcha_token: str,
) -> str:
    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Content-Type": "application/json",
        "Origin": "https://labs.google",
        "Referer": "https://labs.google/",
    }
    async with http.post(API_URL, json=build_request_body(prompt, recaptcha_token), headers=headers) as resp:
        if resp.status in (401, 403):
            raise PermissionError(f"HTTP {resp.status} — сессия невалидна")
        if resp.status == 400:
            text = await resp.text()
            if "recaptcha" in text.lower():
                raise ValueError("reCAPTCHA токен протух")
            raise ValueError(f"HTTP 400: {text[:300]}")
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}: {(await resp.text())[:300]}")
        data = await resp.json()

    url = extract_image_url(data)
    if not url:
        raise RuntimeError(f"No image URL in response: {str(data)[:300]}")
    return url


async def download_image(
    http: aiohttp.ClientSession,
    url: str,
    out_path: Path,
    bearer_token: str,
) -> None:
    async with http.get(url, headers={"Authorization": f"Bearer {bearer_token}"}) as resp:
        if resp.status == 200:
            out_path.write_bytes(await resp.read())
            return
    # Retry без авторизации (fife URLs бывают публичными)
    async with http.get(url) as resp:
        resp.raise_for_status()
        out_path.write_bytes(await resp.read())


# ---------------------------------------------------------------------------
# Основной цикл генерации
# ---------------------------------------------------------------------------

async def process_prompts(page, prompts: list[str], out_dir: Path) -> bool:
    """
    Возвращает False если сессия протухла и нужна переавторизация.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        bearer_token = await get_bearer_token(page)
    except PermissionError:
        return False  # Сессия мертва сразу

    session_expired = False

    async with aiohttp.ClientSession() as http:
        for idx, prompt in enumerate(prompts, start=1):
            out_path = out_dir / f"photo_{idx:03d}.jpg"
            if out_path.exists():
                print(f"[{idx}/{len(prompts)}] Пропускаю (уже есть): {out_path.name}")
                continue

            print(f"[{idx}/{len(prompts)}] {prompt[:70]}...")

            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    recaptcha_token = await get_recaptcha_token(page)
                    image_url = await generate_image(http, prompt, bearer_token, recaptcha_token)
                    await download_image(http, image_url, out_path, bearer_token)
                    print(f"  Сохранено: {out_path.name}")
                    break

                except PermissionError:
                    if attempt < MAX_RETRIES:
                        print("  Bearer token протух, обновляю...")
                        try:
                            bearer_token = await get_bearer_token(page)
                        except PermissionError:
                            print("  Сессия истекла — нужна переавторизация")
                            session_expired = True
                            break
                    else:
                        session_expired = True
                        break

                except ValueError as e:
                    print(f"  Промпт заблокирован: {e}")
                    break

                except Exception as e:
                    print(f"  Попытка {attempt}/{MAX_RETRIES}: {e}")
                    if attempt == MAX_RETRIES:
                        print(f"  Пропускаю #{idx}")

            if session_expired:
                break

            await asyncio.sleep(1.5)

    return not session_expired


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

async def run_async():
    if not FLOW_PROJECT_ID:
        print("ОШИБКА: FLOW_PROJECT_ID не задан в config/.env")
        return

    session = find_latest_session()
    if not session:
        print("Нет сессий в data/prompts/")
        return

    print(f"Сессия: {session}")
    prompts = read_prompts(session)
    print(f"Промптов: {len(prompts)}")
    out_dir = MEDIA_DIR / session / "photos"

    async with async_playwright() as pw:

        # --- Первый запуск или куки удалены → Setup mode ---
        if not cookies_exist():
            await setup_mode(pw)
            return

        # --- Обычный запуск ---
        chrome_proc = None
        if not is_cdp_port_open():
            chrome_proc = launch_chrome(start_url=FLOW_URL)
        else:
            print(f"[Chrome] Уже запущен на порту {CHROME_CDP_PORT}")

        try:
            browser, context = await connect_browser(pw)

            # Применяем сохранённые куки
            cookies = load_cookies()
            await context.add_cookies(cookies)
            print(f"[Auth] Куки применены ({len(cookies)} записей)")

            # Открываем Flow
            flow_page = None
            for p in context.pages:
                if "labs.google" in p.url:
                    flow_page = p
                    break
            if flow_page is None:
                flow_page = await context.new_page()
                print(f"[Flow] Открываю {FLOW_URL}...")
                await flow_page.goto(FLOW_URL, wait_until="networkidle", timeout=60000)

            print(f"[Flow] Страница: {flow_page.url}")

            success = await process_prompts(flow_page, prompts, out_dir)

            if not success:
                # Куки протухли — удаляем и просим переавторизоваться
                invalidate_cookies()
                print("\n[Auth] Сессия истекла.")
                print("[Auth] Запусти агента снова — откроется окно логина.\n")
            else:
                # Обновляем куки после успешной генерации
                fresh_cookies = await context.cookies([
                    "https://accounts.google.com",
                    "https://google.com",
                    "https://labs.google",
                ])
                save_cookies(fresh_cookies)

                saved = list(out_dir.glob("photo_*.jpg"))
                print(f"\n--- Готово ---")
                print(f"Изображений: {len(saved)}/{len(prompts)}")
                print(f"Папка: {out_dir}")

        finally:
            if chrome_proc:
                print("[Chrome] Закрываю Chrome...")
                chrome_proc.terminate()


def find_latest_session() -> str | None:
    sessions = sorted(
        (d.name for d in PROMPTS_DIR.iterdir()
         if d.is_dir() and d.name.startswith("Video_")),
        reverse=True,
    )
    return sessions[0] if sessions else None


def read_prompts(session: str) -> list[str]:
    txt = PROMPTS_DIR / session / "photo_prompts.txt"
    if not txt.exists():
        raise FileNotFoundError(f"Не найден: {txt}")
    content = txt.read_text(encoding="utf-8")
    return [p.strip() for p in content.split("\n\n") if p.strip()]


def run():
    asyncio.run(run_async())


if __name__ == "__main__":
    run()
