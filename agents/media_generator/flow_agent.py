"""
Flow Agent — Image generation via Google Flow (labs.google)
------------------------------------------------------------
Генерация изображений через Google Flow в браузере с куки-авторизацией.
Скачивание в 2K качестве через нативный CDP download механизм.

Setup (первый запуск):
    py agents/media_generator/flow_agent.py --setup

Прямая генерация:
    py agents/media_generator/flow_agent.py --prompt "..." --out result.png
"""

import base64
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

_MODULE_DIR = Path(__file__).parent
if str(_MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(_MODULE_DIR))

from utils import (  # noqa: E402
    CHROME_PATH,
    CHROME_CDP_PORT,
    GROK_DEBUG_DIR,
    cookies_exist,
    load_cookies,
    save_cookies,
    run_setup_mode,
)

# ── Конфиг ────────────────────────────────────────────────────────────────────

_BASE_DIR = Path(__file__).resolve().parents[2]
_ENV_FILE = _BASE_DIR / "config" / ".env"


def _get_project_url() -> str:
    from dotenv import load_dotenv
    load_dotenv(_ENV_FILE)
    return os.environ.get(
        "FLOW_PROJECT_URL",
        "https://labs.google/fx/ru/tools/flow",
    )


FLOW_IMAGE_TIMEOUT = int(os.environ.get("FLOW_IMAGE_TIMEOUT", "180"))
FLOW_DEBUG_DIR     = GROK_DEBUG_DIR
FLOW_MAX_RETRIES   = 3
FLOW_QUALITY       = os.environ.get("FLOW_QUALITY", "2K")  # 1K | 2K | 4K


# ── Debug ─────────────────────────────────────────────────────────────────────

def _screenshot(page, name: str) -> None:
    try:
        FLOW_DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        path = FLOW_DEBUG_DIR / f"flow_{name}.png"
        page.screenshot(path=str(path), timeout=8000, animations="disabled")
        print(f"    [debug] {path.name}", flush=True)
    except Exception as e:
        print(f"    [debug] screenshot failed: {e}", flush=True)


# ── Chrome helpers ────────────────────────────────────────────────────────────

def _is_cdp_open(port: int = CHROME_CDP_PORT) -> bool:
    with socket.socket() as s:
        s.settimeout(1)
        try:
            s.connect(("localhost", port))
            return True
        except Exception:
            return False


def _launch_chrome(profile_dir: str, url: str) -> subprocess.Popen:
    print(f"  [Flow] Запускаю Chrome (профиль: {Path(profile_dir).name})...", flush=True)
    proc = subprocess.Popen([
        CHROME_PATH,
        f"--remote-debugging-port={CHROME_CDP_PORT}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        url,
    ])
    for _ in range(30):
        time.sleep(1)
        if _is_cdp_open():
            print("  [Flow] Chrome готов (CDP).", flush=True)
            return proc
    raise RuntimeError("Chrome не поднял CDP за 30 секунд")


# ── Auth check ────────────────────────────────────────────────────────────────

def _is_logged_in(page) -> bool:
    try:
        return page.evaluate("""() => {
            const btns = Array.from(document.querySelectorAll('a,button'));
            const signIn = btns.some(b => {
                const t = (b.textContent || b.getAttribute('aria-label') || '').trim().toLowerCase();
                return t === 'sign in' || t === 'войти' || t === 'вход';
            });
            if (signIn) return false;
            if (document.querySelector('[contenteditable="true"]')) return true;
            const hasGenBtn = btns.some(b => {
                const t = (b.textContent || '').trim().toLowerCase();
                return t.includes('generat') || t.includes('создат') || t.includes('imagine');
            });
            return hasGenBtn;
        }""")
    except Exception:
        return False


# ── Step 1: Insert prompt via keyboard ───────────────────────────────────────

def _insert_prompt(page, prompt: str) -> bool:
    """Вставляет промпт через буфер обмена (один Ctrl+V вместо 2000 keystrokes)."""
    try:
        page.wait_for_selector('[contenteditable="true"]', timeout=10_000)
    except Exception:
        print("    [!] Поле ввода не найдено", flush=True)
        return False

    for attempt in range(3):
        try:
            field = page.locator('[contenteditable="true"]').first
            field.click()
            time.sleep(0.3)

            # Очистка
            page.keyboard.press("Control+a")
            time.sleep(0.1)
            page.keyboard.press("Delete")
            time.sleep(0.2)

            # Кладём промпт в буфер обмена через JS Clipboard API
            page.evaluate(f"() => navigator.clipboard.writeText({json.dumps(prompt)})")
            time.sleep(0.1)

            # Вставляем одним Ctrl+V — один event, ноль лишних запросов
            page.keyboard.press("Control+v")
            time.sleep(0.5)

            length = page.evaluate("""() => {
                const el = document.querySelector('[contenteditable="true"]');
                return el ? (el.textContent || '').trim().length : 0;
            }""")
            if length >= min(20, len(prompt) // 2):
                print(f"    Промпт вставлен ({length} символов)", flush=True)
                return True

            print(f"    [!] Ввод неполный ({length}/{len(prompt)}) — попытка {attempt+1}/3", flush=True)
            time.sleep(2)
        except Exception as e:
            print(f"    [!] Ошибка ввода: {e}", flush=True)

    return False


# ── Step 2: Click generate (arrow_forward) ───────────────────────────────────

def _click_generate(page) -> bool:
    """Кликает кнопку arrow_forward (Создать) через JS."""
    clicked = page.evaluate("""() => {
        const btns = Array.from(document.querySelectorAll('button'));
        const btn = btns.find(b => (b.textContent || '').includes('arrow_forward'));
        if (btn) { btn.click(); return true; }
        return false;
    }""")
    if clicked:
        print("    [OK] Generate нажат (arrow_forward)", flush=True)
        return True

    # Fallback: Enter если фокус на поле
    try:
        if page.evaluate("() => document.activeElement?.isContentEditable"):
            page.keyboard.press("Enter")
            print("    [OK] Generate нажат (Enter)", flush=True)
            return True
    except Exception:
        pass

    print("    [!] Кнопка Generate не найдена", flush=True)
    return False


# ── Step 3: Wait for generation (grid thumbnails) ────────────────────────────

def _wait_for_generation(page, timeout: int = FLOW_IMAGE_TIMEOUT) -> str | None:
    """
    Ждёт появления новых изображений в гриде.
    Возвращает URL первого нового изображения (для прямого скачивания).
    """
    deadline = time.time() + timeout
    poll = 3

    # Запоминаем текущие URL изображений
    initial_urls = set(page.evaluate("""() => {
        return Array.from(document.querySelectorAll('img')).filter(i => {
            const r = i.getBoundingClientRect();
            return r.width > 200 && i.src && i.src.includes('labs.google');
        }).map(i => i.src);
    }"""))

    ticks = 0
    while time.time() < deadline:
        time.sleep(poll)
        ticks += 1

        current = page.evaluate("""() => {
            return Array.from(document.querySelectorAll('img')).filter(i => {
                const r = i.getBoundingClientRect();
                return r.width > 200 && i.src && i.src.includes('labs.google');
            }).map(i => i.src);
        }""")

        new_urls = [u for u in current if u not in initial_urls]
        if new_urls:
            print(f"    [OK] Появилось {len(new_urls)} новых изображений", flush=True)
            return new_urls[0]  # URL первого нового

        if ticks % 5 == 0:
            remaining = int(deadline - time.time())
            print(f"    [ожидаю... ~{remaining}с]", flush=True)

    print("    [!] Таймаут ожидания генерации", flush=True)
    return None


# ── Step 4: Open image detail view ───────────────────────────────────────────

def _open_image_detail(page, img_url: str) -> bool:
    """
    Кликает на конкретное изображение в гриде (по URL) через mouse.click,
    что единственно надёжно открывает detail view в Flow.
    """
    try:
        # Получаем координаты центра карточки
        coords = page.evaluate(f"""() => {{
            const target_src = {json.dumps(img_url)};
            const imgs = Array.from(document.querySelectorAll('img'));
            const img = imgs.find(i => i.src === target_src);
            if (!img) return null;
            const card = img.closest('[role="button"]') || img.parentElement;
            const r = (card || img).getBoundingClientRect();
            return {{ x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) }};
        }}""")

        if not coords:
            print(f"    [!] Изображение не найдено по URL (пробую первое в гриде)", flush=True)
            # Fallback: кликаем первое большое изображение в гриде
            coords = page.evaluate("""() => {
                const img = Array.from(document.querySelectorAll('img')).find(i => {
                    const r = i.getBoundingClientRect();
                    return r.width > 200 && i.src.includes('labs.google');
                });
                if (!img) return null;
                const card = img.closest('[role="button"]') || img.parentElement;
                const r = (card || img).getBoundingClientRect();
                return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) };
            }""")

        if not coords:
            print("    [!] Нет изображений в гриде для клика", flush=True)
            return False

        # Реальный клик мышью (JS .click() не открывает detail view в Flow)
        print(f"    [4] Mouse click ({coords['x']},{coords['y']})", flush=True)
        page.mouse.click(coords["x"], coords["y"])
        time.sleep(2.5)

        # Проверяем что detail view открылся (кнопка Скачать с aria-haspopup=menu)
        has_dl = page.evaluate("""() => {
            return Array.from(document.querySelectorAll('button')).some(b =>
                b.getAttribute('aria-haspopup') === 'menu' &&
                (b.textContent || '').includes('download')
            );
        }""")

        if has_dl:
            print("    [OK] Detail view открыт (кнопка Скачать найдена)", flush=True)
            return True

        # Вторая попытка
        print("    [!] Detail view не открылся — повторный клик", flush=True)
        page.mouse.click(coords["x"], coords["y"])
        time.sleep(2.5)
        return True

    except Exception as e:
        print(f"    [!] Ошибка открытия изображения: {e}", flush=True)
    return False


# ── Step 5: Download in 2K via CDP ───────────────────────────────────────────

def _download_2k(page, out_path: Path, quality: str = FLOW_QUALITY) -> bool:
    """
    Открывает меню Скачать, выбирает 2K, перехватывает файл через CDP.
    """
    out_dir = out_path.parent

    # CDP session для управления загрузкой
    cdp = page.context.new_cdp_session(page)
    cdp.send("Browser.setDownloadBehavior", {
        "behavior": "allow",
        "downloadPath": str(out_dir),
        "eventsEnabled": True,
    })

    download_done = {"file": None, "error": None}

    def on_begin(event):
        fname = event.get("suggestedFilename", "flow_download")
        print(f"    [dl] начало: {fname}", flush=True)
        download_done["file"] = out_dir / fname

    def on_progress(event):
        state = event.get("state", "")
        if state == "completed":
            print(f"    [dl] завершено", flush=True)
            download_done["done"] = True
        elif state == "canceled":
            download_done["error"] = "canceled"

    cdp.on("Browser.downloadWillBegin", on_begin)
    cdp.on("Browser.downloadProgress", on_progress)
    download_done["done"] = False

    # 1. Открываем меню Скачать (aria-haspopup=menu)
    print(f"    [5a] Открываю меню Скачать...", flush=True)
    menu_opened = page.evaluate("""() => {
        const btn = Array.from(document.querySelectorAll('button')).find(b =>
            b.getAttribute('aria-haspopup') === 'menu' &&
            (b.textContent || '').includes('download')
        );
        if (btn) { btn.click(); return true; }
        return false;
    }""")

    if not menu_opened:
        print("    [!] Кнопка Скачать (menu) не найдена", flush=True)
        return False

    time.sleep(0.6)

    # 2. Кликаем на нужное качество в Radix портале
    print(f"    [5b] Выбираю {quality}...", flush=True)
    quality_clicked = page.evaluate(f"""() => {{
        const q = '{quality}';
        const container = document.querySelector('[data-radix-popper-content-wrapper]');
        if (!container) return 'no menu';
        const all = Array.from(container.querySelectorAll('button,div,span'));
        // Ищем кнопку у которой текст начинается с нашего качества (2K, 4K, 1K)
        const btn = all.find(el => {{
            const t = (el.textContent || '').trim();
            return t === q || t.startsWith(q);
        }});
        if (!btn) return 'not found';
        btn.click();
        return 'ok';
    }}""")

    if quality_clicked != "ok":
        print(f"    [!] Опция {quality} не найдена в меню: {quality_clicked}", flush=True)
        # Fallback: пробуем 1K если 2K/4K недоступны
        if quality != "1K":
            print("    Пробую 1K...", flush=True)
            page.evaluate("""() => {
                const c = document.querySelector('[data-radix-popper-content-wrapper]');
                if (!c) return;
                const btn = Array.from(c.querySelectorAll('button')).find(b =>
                    (b.textContent || '').trim().startsWith('1K'));
                if (btn) btn.click();
            }""")
        else:
            return False

    # 3. Ждём завершения загрузки
    print("    [5c] Жду загрузку файла...", flush=True)
    deadline = time.time() + 60
    while time.time() < deadline:
        time.sleep(1)
        if download_done.get("done"):
            break
        if download_done.get("error"):
            print(f"    [!] Ошибка загрузки: {download_done['error']}", flush=True)
            return False

    if not download_done.get("done"):
        print("    [!] Таймаут загрузки", flush=True)
        return False

    # 4. Переименовываем скачанный файл в out_path
    src = download_done.get("file")
    if src and src.exists():
        # Если формат другой (jpeg) — сохраняем как есть, меняем расширение
        actual_ext = src.suffix
        final_path = out_path.with_suffix(actual_ext)
        src.rename(final_path)
        sz = final_path.stat().st_size
        print(f"    [DONE] {final_path.name} ({sz//1024}KB)", flush=True)

        # Обновляем out_path в результат (вернём через side-effect dict)
        download_done["final_path"] = final_path
        return True

    # Если имя файла неизвестно — ищем самый свежий файл в папке
    recent = sorted(out_dir.glob("*"), key=lambda f: f.stat().st_mtime, reverse=True)
    if recent:
        src = recent[0]
        actual_ext = src.suffix
        final_path = out_path.with_suffix(actual_ext)
        if src != final_path:
            src.rename(final_path)
        sz = final_path.stat().st_size
        print(f"    [DONE] {final_path.name} ({sz//1024}KB)", flush=True)
        download_done["final_path"] = final_path
        return True

    print("    [!] Файл загрузки не найден", flush=True)
    return False


# ── Generate one image ────────────────────────────────────────────────────────

def _flow_generate_one(
    page,
    prompt: str,
    out_path: Path,
    idx: int = 0,
) -> Path | None:
    """
    Генерирует одно изображение через Google Flow и скачивает в 2K.
    Возвращает реальный путь к файлу (расширение может быть .jpeg) или None.
    """
    project_url = _get_project_url()

    # Навигация на проект
    try:
        if project_url not in page.url:
            page.goto(project_url, wait_until="domcontentloaded", timeout=30_000)
            time.sleep(2.5)
    except Exception as e:
        print(f"    [!] Навигация ошибка: {e}", flush=True)
        return None

    _screenshot(page, f"{idx:03d}_1_loaded")

    # Шаг 1: Вставляем промпт
    print("    [1] Ввожу промпт...", flush=True)
    if not _insert_prompt(page, prompt):
        _screenshot(page, f"{idx:03d}_err_prompt")
        return None

    _screenshot(page, f"{idx:03d}_2_prompt")

    # Шаг 2: Нажимаем Generate
    print("    [2] Нажимаю Generate...", flush=True)
    if not _click_generate(page):
        _screenshot(page, f"{idx:03d}_err_generate")
        return None

    time.sleep(1)
    _screenshot(page, f"{idx:03d}_3_generating")

    # Шаг 3: Ждём появления изображений в гриде
    print(f"    [3] Жду генерацию (до {FLOW_IMAGE_TIMEOUT}с)...", flush=True)
    new_img_url = _wait_for_generation(page)
    if not new_img_url:
        _screenshot(page, f"{idx:03d}_err_timeout")
        return None

    _screenshot(page, f"{idx:03d}_4_generated")

    # Шаг 4: Открываем конкретное изображение по URL
    print("    [4] Открываю изображение...", flush=True)
    if not _open_image_detail(page, new_img_url):
        print("    [!] Не удалось открыть изображение", flush=True)
        _screenshot(page, f"{idx:03d}_err_open")
        return None

    _screenshot(page, f"{idx:03d}_5_opened")

    # Шаг 5: Скачиваем в 2K
    print(f"    [5] Скачиваю в {FLOW_QUALITY}...", flush=True)
    dl_state = {"final_path": None}
    # Передаём dict чтобы поймать final_path из _download_2k
    orig_download_done = {}

    # Патчим функцию чтобы получить final_path
    import types

    download_result = {}

    def _download_2k_captured(page, out_path, quality=FLOW_QUALITY):
        out_dir = out_path.parent
        cdp = page.context.new_cdp_session(page)
        cdp.send("Browser.setDownloadBehavior", {
            "behavior": "allow",
            "downloadPath": str(out_dir),
            "eventsEnabled": True,
        })
        state = {"done": False, "file": None, "error": None}

        def on_begin(ev):
            fname = ev.get("suggestedFilename", "flow_dl")
            state["file"] = out_dir / fname
            print(f"    [dl] {fname}", flush=True)

        def on_progress(ev):
            if ev.get("state") == "completed":
                state["done"] = True
            elif ev.get("state") == "canceled":
                state["error"] = "canceled"

        cdp.on("Browser.downloadWillBegin", on_begin)
        cdp.on("Browser.downloadProgress", on_progress)

        # Открываем меню кнопки Скачать через mouse.click (JS click не открывает Radix popover)
        dl_coords = page.evaluate("""() => {
            const btn = Array.from(document.querySelectorAll('button')).find(b =>
                b.getAttribute('aria-haspopup') === 'menu' &&
                (b.textContent || '').includes('download'));
            if (!btn) return null;
            const r = btn.getBoundingClientRect();
            return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) };
        }""")
        if not dl_coords:
            print("    [!] Кнопка Скачать не найдена", flush=True)
            return None

        print(f"    [5a] Mouse click Скачать ({dl_coords['x']},{dl_coords['y']})", flush=True)
        page.mouse.click(dl_coords["x"], dl_coords["y"])
        time.sleep(0.7)

        # Кликаем качество в Radix меню через mouse.click
        # Ищем кнопку: текст начинается с '2K' (без пробела — Flow не добавляет разделитель)
        quality_coords = page.evaluate(f"""() => {{
            const q = '{quality}';
            const c = document.querySelector('[data-radix-popper-content-wrapper]');
            if (!c) return null;
            // Ищем BUTTON (предпочтительно) с текстом начинающимся на q
            const buttons = Array.from(c.querySelectorAll('button'));
            let btn = buttons.find(el => (el.textContent || '').trim().startsWith(q));
            // Если не найдено — ищем любой элемент (span/div)
            if (!btn) {{
                btn = Array.from(c.querySelectorAll('*')).find(el => {{
                    const t = (el.textContent || '').trim();
                    return t === q || t.startsWith(q);
                }});
            }}
            if (!btn) return null;
            const r = btn.getBoundingClientRect();
            return {{ x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) }};
        }}""")

        if not quality_coords and quality != "1K":
            # Fallback: 1K
            quality_coords = page.evaluate("""() => {
                const c = document.querySelector('[data-radix-popper-content-wrapper]');
                if (!c) return null;
                const b = Array.from(c.querySelectorAll('button')).find(b =>
                    (b.textContent||'').trim().startsWith('1K'));
                if (!b) return null;
                const r = b.getBoundingClientRect();
                return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) };
            }""")

        if not quality_coords:
            print(f"    [!] Опция {quality} не найдена в меню", flush=True)
            return None

        print(f"    [5b] Mouse click {quality} ({quality_coords['x']},{quality_coords['y']})", flush=True)
        page.mouse.click(quality_coords["x"], quality_coords["y"])

        # Ждём download — CDP события + filesystem polling (двойная проверка)
        # .crdownload = Chrome временный файл, игнорируем
        before_files = {f for f in out_dir.glob("*.*") if f.suffix != ".crdownload"}
        deadline = time.time() + 60
        while time.time() < deadline:
            time.sleep(1)
            if state["done"] or state["error"]:
                break
            # Filesystem fallback: новый завершённый файл появился в out_dir
            current = {f for f in out_dir.glob("*.*") if f.suffix != ".crdownload"}
            new_files = current - before_files
            if new_files:
                print(f"    [dl] файл через filesystem: {[f.name for f in new_files]}", flush=True)
                state["done"] = True
                state["file"] = sorted(new_files, key=lambda f: f.stat().st_mtime)[-1]
                break

        if not state["done"]:
            print(f"    [!] Загрузка не завершилась (error={state['error']})", flush=True)
            return None

        # Находим и переименовываем файл
        src = state.get("file")
        if src:
            src = Path(src)
            if src.exists():
                final = out_path.with_suffix(src.suffix)
                if final.exists():
                    final.unlink()  # удаляем старый файл если уже есть (retry)
                if src != final:
                    src.rename(final)
                print(f"    [dl] {final.name} ({final.stat().st_size//1024}KB)", flush=True)
                return final

        # Последний шанс: самый свежий файл в out_dir (не .crdownload)
        candidates = sorted(
            [f for f in out_dir.glob("*.*") if f.suffix != ".crdownload"],
            key=lambda f: f.stat().st_mtime, reverse=True
        )
        if candidates:
            src = candidates[0]
            final = out_path.with_suffix(src.suffix)
            if src != final:
                if final.exists():
                    final.unlink()
                src.rename(final)
            print(f"    [dl] {final.name} ({final.stat().st_size//1024}KB) [fallback]", flush=True)
            return final

        return None

    result_path = _download_2k_captured(page, out_path)
    if result_path:
        print(f"    [DONE] {result_path.name}", flush=True)
        _screenshot(page, f"{idx:03d}_6_done")
        return result_path

    print("    ОШИБКА: не удалось скачать изображение", flush=True)
    _screenshot(page, f"{idx:03d}_err_download")
    return None


# ── Public API ────────────────────────────────────────────────────────────────

def generate_flow_images(
    platform: dict,
    prompts: list[dict],  # [{"id": 1, "prompt": "...", "concept": "..."}]
    out_dir: Path,
    round_num: int = 1,
) -> list[dict]:
    """
    Генерирует изображения через Google Flow (2K качество).
    Возвращает список {"id", "path", "prompt"} — тот же формат что PixelAgent.
    """
    project_url = _get_project_url()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not cookies_exist(platform):
        print("  [Flow] Куки не найдены — запускаю setup...", flush=True)
        run_setup_mode(platform)
        return []

    chrome_proc = None
    if not _is_cdp_open():
        chrome_proc = _launch_chrome(platform["profile_dir"], project_url)

    results = []

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(f"http://localhost:{CHROME_CDP_PORT}")
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()

            cookies = load_cookies(platform)
            if cookies:
                ctx.add_cookies(cookies)
                print(f"  [Flow] Куки применены ({len(cookies)} записей)", flush=True)

            # Используем существующую страницу если она уже на Flow, иначе создаём новую
            page = None
            for p in ctx.pages:
                if "labs.google" in p.url:
                    page = p
                    break
            if page is None:
                page = ctx.new_page()

            # Навигация на проект если нужно
            if project_url not in page.url:
                page.goto(project_url, wait_until="domcontentloaded", timeout=60_000)

            # Ждём появления content-editable (признак загруженного React-приложения)
            try:
                page.wait_for_selector('[contenteditable="true"]', timeout=15_000)
                print(f"  [Flow] Сессия активна", flush=True)
            except Exception:
                # Пробуем перезагрузить
                print("  [Flow] Не найдено поле ввода — перезагружаю...", flush=True)
                page.reload(wait_until="domcontentloaded", timeout=30_000)
                try:
                    page.wait_for_selector('[contenteditable="true"]', timeout=15_000)
                    print(f"  [Flow] Сессия активна (после reload)", flush=True)
                except Exception:
                    print("  [Flow] [ERR] Не залогинен — обнови куки:", flush=True)
                    print("  py agents/media_generator/flow_agent.py --setup", flush=True)
                    return []

            save_cookies(platform, ctx.cookies())

            for p_data in prompts:
                pid    = p_data["id"]
                prompt = p_data["prompt"]
                out_path = out_dir / f"r{round_num}_v{pid}.png"

                print(f"\n  [Flow] [{pid}/{len(prompts)}] {p_data.get('concept','')}", flush=True)

                result_path = None
                for attempt in range(1, FLOW_MAX_RETRIES + 1):
                    if attempt > 1:
                        wait = 5 * attempt
                        print(f"    Попытка {attempt}/{FLOW_MAX_RETRIES} (пауза {wait}s)...", flush=True)
                        time.sleep(wait)
                        try:
                            page.goto(project_url, wait_until="domcontentloaded", timeout=30_000)
                            time.sleep(2)
                        except Exception:
                            pass

                    try:
                        result_path = _flow_generate_one(page, prompt, out_path, idx=pid)
                    except Exception as e:
                        print(f"    ОШИБКА (попытка {attempt}): {e}", flush=True)

                    if result_path:
                        break

                results.append({
                    "id":     pid,
                    "path":   result_path,
                    "prompt": prompt,
                })

            save_cookies(platform, ctx.cookies())

    finally:
        if chrome_proc:
            chrome_proc.terminate()
            print("  [Flow] Chrome закрыт.", flush=True)

    ok = sum(1 for r in results if r["path"])
    print(f"\n  [Flow] Done: {ok}/{len(prompts)}", flush=True)
    return results


# ── Setup mode ────────────────────────────────────────────────────────────────

def flow_setup(platform: dict) -> None:
    project_url = _get_project_url()
    profile_dir = platform["profile_dir"]
    cookies_file = platform["cookies_file"]

    print("\n" + "="*55, flush=True)
    print("  GOOGLE FLOW — SETUP", flush=True)
    print("="*55, flush=True)
    print(f"  Профиль: {profile_dir}", flush=True)
    print(f"  URL:     {project_url}", flush=True)
    print("\n  Открываю Chrome...", flush=True)
    print("  1. Войди в свой Google аккаунт", flush=True)
    print("  2. Убедись что Flow проект открылся", flush=True)
    print("  3. Вернись сюда и нажми Enter", flush=True)
    print("="*55, flush=True)

    chrome_proc = None
    if not _is_cdp_open():
        chrome_proc = _launch_chrome(profile_dir, project_url)

    input("\n  Нажми Enter когда залогинишься... ")

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(f"http://localhost:{CHROME_CDP_PORT}")
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            cookies = ctx.cookies()
            if cookies:
                Path(cookies_file).parent.mkdir(parents=True, exist_ok=True)
                Path(cookies_file).write_text(
                    json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                print(f"\n  [OK] Куки сохранены ({len(cookies)} записей) -> {cookies_file}", flush=True)
            else:
                print("  [!] Куки не найдены", flush=True)
    finally:
        if chrome_proc:
            chrome_proc.terminate()


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    from utils import PLATFORMS

    parser = argparse.ArgumentParser(description="Google Flow image generator")
    parser.add_argument("--setup",  action="store_true", help="Setup: login & save cookies")
    parser.add_argument("--prompt", type=str, default=None, help="Generate single image")
    parser.add_argument("--out",    type=str, default="flow_output.png", help="Output path")
    args = parser.parse_args()

    platform = PLATFORMS["1"].copy()
    platform["key"] = "1"

    if args.setup:
        flow_setup(platform)
    elif args.prompt:
        out_path = Path(args.out)
        results = generate_flow_images(
            platform,
            [{"id": 1, "prompt": args.prompt, "concept": "test"}],
            out_path.parent,
            round_num=1,
        )
        if results and results[0].get("path"):
            print(f"[OK] Сохранено: {results[0]['path']}")
        else:
            print("[FAIL] Генерация не удалась")
    else:
        parser.print_help()
