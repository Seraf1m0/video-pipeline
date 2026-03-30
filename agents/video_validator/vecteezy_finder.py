"""
vecteezy_finder.py — поиск и скачивание стоковых видео с Vecteezy через Playwright.

Требует предварительного входа в аккаунт:
  py agents/video_validator/vecteezy_login.py

Данные браузера хранятся в: browser_data/vecteezy/
"""

import re
import time
from pathlib import Path

import requests as _requests

_BASE_DIR    = Path(__file__).resolve().parent.parent.parent
_BROWSER_DATA = _BASE_DIR / "browser_data" / "vecteezy"
_BROWSER_DATA.mkdir(parents=True, exist_ok=True)

VECTEEZY_BASE = "https://www.vecteezy.com"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Referer": VECTEEZY_BASE,
}


def _launch_context():
    """
    Запустить Playwright с persistent context.
    Возвращает (playwright, context).
    """
    from playwright.sync_api import sync_playwright
    pw      = sync_playwright().start()
    context = pw.chromium.launch_persistent_context(
        str(_BROWSER_DATA),
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
        ],
    )
    return pw, context


def _close(pw, context) -> None:
    """Закрыть браузер."""
    try:
        context.close()
    except Exception:
        pass
    try:
        pw.stop()
    except Exception:
        pass


def search_vecteezy(
    query: str,
    used_ids: set | None = None,
    min_duration: int = 10,
    max_results: int = 20,
) -> list[dict]:
    """
    Поиск видео на Vecteezy.

    Возвращает список кандидатов:
      [{"id": "vecteezy_12345", "url": "page_url", "source": "vecteezy", ...}]

    used_ids — ID которые уже использованы (пропускаем).
    """
    if used_ids is None:
        used_ids = set()

    pw = context = None
    results: list[dict] = []

    try:
        pw, context = _launch_context()
        page = context.new_page()

        # Формируем URL поиска
        slug        = query.strip().replace(" ", "-").lower()
        search_url  = f"{VECTEEZY_BASE}/videos/{slug}"

        page.goto(search_url, wait_until="domcontentloaded", timeout=30_000)
        time.sleep(2)

        # Проверяем авторизацию
        if "sign-in" in page.url or "login" in page.url:
            print(
                "  ⚠️  Vecteezy: не авторизован. Запусти:\n"
                "       py agents/video_validator/vecteezy_login.py",
                flush=True,
            )
            return []

        # Собираем ссылки на страницы видео
        links = page.query_selector_all("a[href*='/video/']")
        for link in links:
            href = link.get_attribute("href") or ""
            if not href or "/video/" not in href:
                continue
            if not href.startswith("http"):
                href = VECTEEZY_BASE + href

            m = re.search(r"/video/(\d+)", href)
            if not m:
                continue
            vid_id = f"vecteezy_{m.group(1)}"
            if vid_id in used_ids:
                continue

            results.append({
                "id":       vid_id,
                "url":      href,           # страница для скачивания
                "source":   "vecteezy",
                "duration": 15,             # неизвестна до загрузки
                "width":    1920,
                "query":    query,
                "page_url": href,
            })

            if len(results) >= max_results:
                break

    except Exception as e:
        print(f"  ⚠️  Vecteezy поиск '{query}': {e}", flush=True)
    finally:
        _close(pw, context)

    return results


def download_vecteezy(candidate: dict, output_path: Path) -> bool:
    """
    Скачать видео с Vecteezy.

    Открывает страницу через Playwright для перехвата MP4 URL,
    затем скачивает через requests.

    Возвращает True если файл успешно скачан.
    """
    pw = context = None
    try:
        pw, context = _launch_context()
        page = context.new_page()

        # Перехватываем сетевые запросы — ищем MP4
        mp4_urls: list[str] = []

        def _on_response(response) -> None:
            url = response.url
            if ".mp4" in url and ("vecteezy" in url or "cdn" in url):
                if url not in mp4_urls:
                    mp4_urls.append(url)

        page.on("response", _on_response)

        page.goto(candidate["page_url"], wait_until="networkidle", timeout=30_000)
        time.sleep(2)

        # Ищем video element на странице
        for sel in [
            "video source[src]",
            "video[src]",
            "source[type='video/mp4']",
        ]:
            el = page.query_selector(sel)
            if el:
                src = el.get_attribute("src") or el.get_attribute("data-src") or ""
                if src and ".mp4" in src:
                    mp4_urls.insert(0, src)
                    break

        if not mp4_urls:
            print(f"  ⚠️  Vecteezy: MP4 не найден на {candidate['page_url']}", flush=True)
            return False

        # Скачиваем первый найденный MP4
        mp4_url = mp4_urls[0]
        r = _requests.get(mp4_url, stream=True, headers=_HEADERS, timeout=90)
        r.raise_for_status()

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=512 * 1024):
                f.write(chunk)

        ok = output_path.exists() and output_path.stat().st_size > 10_240
        if not ok:
            output_path.unlink(missing_ok=True)
        return ok

    except Exception as e:
        print(f"  ⚠️  Vecteezy скачивание: {e}", flush=True)
        output_path.unlink(missing_ok=True)
        return False
    finally:
        _close(pw, context)
