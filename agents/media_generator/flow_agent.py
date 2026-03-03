"""
Flow Agent — Browser-based generation (Google Flow)
-----------------------------------------------------
Генерация через Google Flow (браузер + куки).
Платформа 1 в media_generator.py.
"""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

# Добавляем папку модуля в sys.path для импорта utils
_MODULE_DIR = Path(__file__).parent
if str(_MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(_MODULE_DIR))

from utils import (  # noqa: E402
    CHROME_CDP_PORT,
    cookies_exist,
    load_cookies,
    save_cookies,
    is_cdp_open,
    launch_chrome,
    run_setup_mode,
)


def generate_flow(
    platform: dict,
    media_type: str,
    prompts: list[str],
    out_dir: Path,
    session: str = "",
) -> int:
    """
    Генерация через Google Flow (браузер + куки).
    """
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
