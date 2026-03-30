"""
vecteezy_login.py — интерактивный вход в Vecteezy.

Запускается ОДИН РАЗ для авторизации браузера.
После входа сессия сохраняется в browser_data/vecteezy/.

Запуск: py agents/video_validator/vecteezy_login.py
"""

import sys
import time
from pathlib import Path

_BASE_DIR     = Path(__file__).resolve().parent.parent.parent
_BROWSER_DATA = _BASE_DIR / "browser_data" / "vecteezy"
_BROWSER_DATA.mkdir(parents=True, exist_ok=True)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    from playwright.sync_api import sync_playwright

    print("🌐 Открываю Vecteezy для входа...")
    print("   Войдите в аккаунт в открывшемся окне браузера.")
    print("   После входа нажмите Enter здесь.\n")

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            str(_BROWSER_DATA),
            headless=False,
            args=["--no-sandbox"],
        )
        page = context.new_page()
        page.goto("https://www.vecteezy.com/sign-in", wait_until="domcontentloaded")

        input("👉 Войдите в браузере, затем нажмите Enter...")

        # Проверяем что вошли
        page.goto("https://www.vecteezy.com", wait_until="domcontentloaded")
        time.sleep(2)

        if "sign-in" not in page.url:
            print("✅ Успешно авторизован! Сессия сохранена.")
        else:
            print("❌ Похоже вход не выполнен. Попробуйте снова.")

        context.close()


if __name__ == "__main__":
    main()
