"""
Grok Agent — Image-to-Video generation (multi-tab parallel)
------------------------------------------------------------
Генерация видео через grok.com/imagine.
Требуется SuperGrok подписка.
Платформа 2 в media_generator.py.

Параллельный режим: каждая «вкладка» — отдельный Chrome-процесс
на своём CDP-порту и своём профиле. Управляется через GROK_NUM_TABS.
"""

import base64
import json
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

# Добавляем папку модуля в sys.path для импорта utils
_MODULE_DIR = Path(__file__).parent
if str(_MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(_MODULE_DIR))

from utils import (  # noqa: E402
    CHROME_PATH,
    CHROME_CDP_PORT,
    GROK_VIDEO_TIMEOUT,
    GROK_DEBUG_DIR,
    GROK_PROGRESS_FILE,
    GROK_MAX_RETRIES,
    GROK_NUM_TABS,
    cookies_exist,
    load_cookies,
    save_cookies,
    invalidate_cookies,
    is_cdp_open,
    launch_chrome,
    run_setup_mode,
    read_photos,
    send_tg_notification,
)

_IMAGINE_URL = "https://grok.com/imagine"


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


def grok_save_progress(
    session: str,
    completed: set[int],
    total: int,
    tabs_status: dict | None = None,
) -> None:
    try:
        GROK_PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
        data: dict = {
            "session":   session,
            "total":     total,
            "completed": sorted(completed),
        }
        if tabs_status:
            data["tabs_status"] = tabs_status
        GROK_PROGRESS_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        print(f"  [progress] ошибка сохранения: {e}")


# ---------------------------------------------------------------------------
# Grok internal helpers
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


def _js_click_text(page, texts: list[str]) -> str | None:
    """
    Находит первый DOM-элемент с совпадающим текстом/aria-label и кликает через
    dispatchEvent (обходит CSS pointer-events: none и HTML-оверлеи).
    Возвращает найденный текст или None.
    """
    return page.evaluate(
        """(texts) => {
            const elements = Array.from(document.querySelectorAll(
                'button, [role="radio"], [role="option"], [role="tab"], [role="button"], label'
            ));
            for (const text of texts) {
                const el = elements.find(e =>
                    (e.textContent || '').trim() === text ||
                    (e.getAttribute('aria-label') || '') === text
                );
                if (el) {
                    el.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                    return text;
                }
            }
            return null;
        }""",
        texts,
    )


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


def _grok_open_video_panel(page, idx: int = 0) -> bool:
    """
    Новый UI (2026-03): кнопки «Изображение» / «Видео» / «720p» / «10s» — прямо в строке ввода.
    Просто кликаем «Видео» inline-кнопку. Никакого dropdown/панели.
    """
    def _dump_all_interactive(label: str) -> None:
        """Диагностический дамп всех интерактивных элементов."""
        try:
            items = page.evaluate("""() => {
                const sels = 'button,[role="button"],[role="radio"],[role="option"],' +
                             '[role="menuitem"],[role="menuitemradio"],label,select';
                return Array.from(document.querySelectorAll(sels))
                    .map(e => {
                        const r = e.getBoundingClientRect();
                        return {
                            tag: e.tagName,
                            t: (e.textContent||'').trim().replace(/\\s+/g,' ').slice(0,30),
                            l: e.getAttribute('aria-label')||'',
                            role: e.getAttribute('role')||'',
                            p: `(${Math.round(r.x)},${Math.round(r.y)},${Math.round(r.width)}x${Math.round(r.height)})`,
                            vis: r.width > 0 && r.height > 0,
                        };
                    }).filter(e => e.vis);
            }""")
            print(f"    [diag:{label}] {[(e['t'] or e['l'], e['role'], e['p']) for e in items[:20]]}")
        except Exception as ex:
            print(f"    [diag:{label}] ошибка: {ex}")

    # Новый UI (2026-03): кнопка «Видео» прямо в строке ввода — кликаем напрямую
    video_btn = _grok_try_locators(page, [
        "button:has-text('Видео')",
        "button:has-text('Video')",
        "[aria-label='Видео']",
        "[aria-label='Video']",
    ], timeout_ms=4000)

    if video_btn:
        try:
            video_btn.click(force=True)
            print("    ✓ Режим «Видео» активирован")
            page.wait_for_timeout(500)
            return True
        except Exception as e:
            print(f"    [!] click Видео: {e}")

    _dump_all_interactive("no_video_btn")
    print("    [!] Кнопка «Видео» не найдена — продолжаю с настройками по умолчанию")
    return True


def _grok_select_video_in_menu(page) -> bool:
    """
    Если после клика на dropdown открылось меню с режимами (Изображение/Видео),
    выбираем «Видео». Используем force=True.
    """
    video_option = _grok_try_locators(page, [
        "[role='menuitemradio']:has-text('Видео')",
        "[role='menuitem']:has-text('Видео')",
        "[role='option']:has-text('Видео')",
    ], timeout_ms=800)
    if video_option:
        try:
            video_option.click(force=True)
            print("    ✓ Выбран режим «Видео» в меню")
            page.wait_for_timeout(500)
            return True
        except Exception:
            video_option.dispatch_event("click")
            page.wait_for_timeout(500)
            return True
    return False


def _grok_configure_and_make_video(page) -> bool:
    """
    Выбирает настройки 10s / 720p / 16:9 и нажимает «Сделать видео».
    Использует JS dispatch_event для обхода CSS pointer-events и HTML-оверлеев.
    Работает как с открытой панелью, так и без неё (если кнопки есть в DOM).
    """
    # ── Длительность: 10 секунд ──────────────────────────────────────────────
    found = _js_click_text(page, ["10s", "10 с", "10с", "10 sec", "10 сек", "10"])
    if found:
        page.wait_for_timeout(400)
        print(f"    ✓ Длительность: {found!r}")
    else:
        print("    [!] 10s не найдено — по умолчанию")

    # ── Разрешение: 720p ─────────────────────────────────────────────────────
    found = _js_click_text(page, ["720p", "720", "HD"])
    if found:
        page.wait_for_timeout(400)
        print(f"    ✓ Разрешение: {found!r}")
    else:
        print("    [!] 720p не найдено — по умолчанию")

    # ── Соотношение сторон: 16:9 ─────────────────────────────────────────────
    found = _js_click_text(page, ["16:9"])
    if found:
        page.wait_for_timeout(400)
        print(f"    ✓ Соотношение: {found!r}")
    else:
        print("    [!] 16:9 не найдено — по умолчанию")

    # ── Кнопка отправки (↑) — новый UI 2026-03 ───────────────────────────────
    send_btn = _grok_try_locators(page, [
        "button[aria-label='Отправить']",
        "button[aria-label='Send']",
        "button[aria-label='Submit']",
        "button[type='submit']",
        "[data-testid*='send']",
        "[data-testid*='submit']",
    ], timeout_ms=3000)

    if send_btn:
        try:
            send_btn.click(force=True)
            print("    ✓ Отправлено (кнопка отправки)")
            return True
        except Exception as e:
            print(f"    [!] click send: {e}")

    # Fallback: Enter в текстовом поле
    try:
        page.keyboard.press("Enter")
        print("    ✓ Отправлено (Enter)")
        return True
    except Exception as e:
        print(f"    [!] Enter: {e}")

    print("    [!] Кнопка отправки не найдена — прерываю")
    return False


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
    """Скачивает видео через Python urllib с куками из браузера."""
    # Сначала пробуем через Python (обходит CORS для imagine-public.x.ai)
    try:
        cookies = page.context.cookies()
        cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
        req = urllib.request.Request(
            video_url,
            headers={
                "Cookie": cookie_str,
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": "https://grok.com/",
            },
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = resp.read()
        if len(data) > 1024:
            print(f"    [dl] Python urllib OK ({len(data)//1024}KB)")
            return data
        print(f"    [dl] пустой ответ (urllib) для {video_url[:60]}")
    except Exception as e:
        print(f"    [dl] urllib ошибка: {e}")

    # Fallback: browser fetch
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


def _wait_for_grok_image(page, timeout: int = 120) -> str | None:
    """Ждёт появления AI-сгенерированного изображения от Grok (до нажатия «Создать видео»)."""
    deadline = time.time() + timeout
    poll = 3
    ticks = 0

    while time.time() < deadline:
        time.sleep(poll)
        ticks += 1

        url = page.evaluate("""() => {
            const imgs = Array.from(document.querySelectorAll('img[src]'));
            // CDN Grok assets
            const cdn = imgs.find(i => i.src.includes('assets.grok.com') && i.src.includes('image'));
            if (cdn) return cdn.src;
            // Большое изображение в контенте (не аватар)
            const big = imgs.filter(i => {
                const r = i.getBoundingClientRect();
                return r.width > 200 && r.height > 200
                    && !i.src.startsWith('data:')
                    && !i.src.includes('avatar')
                    && !i.src.includes('logo');
            });
            return big.length ? big[big.length - 1].src : null;
        }""")

        if url:
            return url

        if ticks % 5 == 0:
            remaining = int(deadline - time.time())
            print(f"    [ожидание изображения... ~{remaining}с]", flush=True)

    return None


def _grok_click_create_video(page) -> bool:
    """
    Кликает кнопку «Создать видео» на сгенерированном изображении.
    Сначала наводит мышь на изображение, чтобы кнопка стала видимой.
    """
    # Наводим мышь на сгенерированное изображение
    img = _grok_try_locators(page, [
        "img[src*='assets.grok.com']",
        "img[src*='generated']",
        "main img",
        "article img",
        "[class*='image'] img",
    ], timeout_ms=5000)

    if img:
        try:
            img.hover()
            page.wait_for_timeout(600)
        except Exception:
            pass

    # Ищем кнопку
    btn = _grok_try_locators(page, [
        "[aria-label='Создать видео']",
        "[aria-label='Create video']",
        "button:has-text('Создать видео')",
        "button:has-text('Create video')",
        "[data-testid*='create-video']",
    ], timeout_ms=5000)

    if btn is None:
        # JS hover на последнем крупном img
        page.evaluate("""() => {
            const imgs = Array.from(document.querySelectorAll('img'));
            const big = imgs.filter(i => {
                const r = i.getBoundingClientRect();
                return r.width > 200 && r.height > 200;
            });
            if (big.length) big[big.length-1].dispatchEvent(
                new MouseEvent('mouseover', {bubbles: true})
            );
        }""")
        page.wait_for_timeout(800)

        btn = _grok_try_locators(page, [
            "[aria-label='Создать видео']",
            "[aria-label='Create video']",
            "button:has-text('Создать видео')",
            "button:has-text('Create video')",
        ], timeout_ms=3000)

    if btn:
        btn.click()
        print("    [v] Кнопка «Создать видео» нажата")
        return True

    print("    [!] Кнопка «Создать видео» не найдена")
    return False


def _grok_generate_one(page, idx: int, total: int, photo_path: Path,
                        prompt: str, out_path: Path) -> bool:
    """
    Генерирует одно видео через Grok /imagine — прямой флоу:
      1. Navigate → Upload photo → Prompt → Switch to VIDEO → 16:9 → 10s → Generate → Wait → Download
    """
    # Шаг 1: Открываем /imagine
    page.goto(_IMAGINE_URL, wait_until="domcontentloaded", timeout=30_000)
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
    page.wait_for_timeout(2500)   # ждём пока input bar восстановится после загрузки фото
    _grok_screenshot(page, idx, "2_photo_loaded")

    # Шаг 3: Вставляем промпт
    print("    [3] Вставляю промпт...", flush=True)
    text_area = _grok_try_locators(page, [
        "div[contenteditable='true']",
        "[role='textbox']",
        "textarea",
        "[placeholder*='Опиши' i]",
        "[placeholder*='prompt' i]",
        "[placeholder*='текст' i]",
    ], timeout_ms=8000)

    if text_area is None:
        print("    ОШИБКА: поле ввода не найдено")
        _grok_screenshot(page, idx, "err_textarea")
        return False

    inserted_len = 0
    for _attempt in range(4):
        text_area.click()
        page.wait_for_timeout(300)

        # Очищаем поле перед повторной вставкой
        page.evaluate("""() => {
            const el = document.activeElement;
            if (el && el.isContentEditable) el.textContent = '';
            else if (el && (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT')) el.value = '';
        }""")
        page.wait_for_timeout(100)

        page.evaluate(f"""() => {{
            const el = document.activeElement;
            const txt = {json.dumps(prompt)};
            if (el && el.isContentEditable) {{
                document.execCommand('insertText', false, txt);
            }} else if (el && (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT')) {{
                el.value = txt;
                el.dispatchEvent(new Event('input', {{bubbles: true}}));
            }}
        }}""")
        page.wait_for_timeout(500)

        # Проверяем фактическую длину вставленного текста
        try:
            inserted_len = page.evaluate("""() => {
                const el = document.querySelector(
                    "div[contenteditable='true'], [role='textbox'], textarea");
                return el ? (el.textContent || el.value || '').trim().length : 0;
            }""")
        except Exception:
            inserted_len = 0

        if inserted_len >= max(10, len(prompt) // 2):
            break

        # Turnstile активен — ждём его завершения и повторяем
        print(f"    [!] Промпт вставлен частично ({inserted_len}/{len(prompt)} симв)"
              f" — ожидаю Turnstile... (попытка {_attempt + 1}/4)")
        page.wait_for_timeout(5000)

    print(f"    Промпт вставлен ({inserted_len} символов)")
    _grok_screenshot(page, idx, "3_prompt_filled")

    # Шаг 4: Открываем панель видео-опций (иконка камеры + ∨)
    print("    [4] Открываю панель видео-опций...", flush=True)
    panel_ok = _grok_open_video_panel(page, idx)
    _grok_screenshot(page, idx, "4_panel")

    if not panel_ok:
        print("    [!] Панель не открылась — прерываю")
        return False

    # Шаг 5: Настраиваем 10s, 720p, 16:9 и нажимаем «Сделать видео»
    print("    [5] Настраиваю параметры и нажимаю «Сделать видео»...", flush=True)
    video_started = _grok_configure_and_make_video(page)
    _grok_screenshot(page, idx, "5_make_video")

    if not video_started:
        print("    [!] «Сделать видео» не сработало — прерываю")
        return False

    page.wait_for_timeout(1500)
    _grok_screenshot(page, idx, "6_sent")

    # Шаг 7: Ждём готовое видео
    print(f"    [7] Жду видео (до {GROK_VIDEO_TIMEOUT}с)...", flush=True)
    video_url = _wait_for_grok_video(page)

    if not video_url:
        print("    ОШИБКА: таймаут — видео не появилось")
        _grok_screenshot(page, idx, "err_timeout")
        return False

    print(f"    Видео: ...{video_url[-60:]}")
    _grok_screenshot(page, idx, "7_video_found")

    # Шаг 8: Скачиваем и сохраняем
    print("    [8] Скачиваю...", flush=True)
    video_bytes = _grok_download_video(page, video_url)

    if video_bytes and len(video_bytes) > 1024:
        out_path.write_bytes(video_bytes)
        size_kb = len(video_bytes) // 1024
        print(f"    [v] {out_path.name} сохранено ({size_kb} KB)")
        return True
    else:
        print("    ОШИБКА: пустой ответ при скачивании")
        return False


# ---------------------------------------------------------------------------
# Multi-tab helpers
# ---------------------------------------------------------------------------

def _is_cdp_open_on(port: int) -> bool:
    """Проверяет доступность CDP-порта."""
    with socket.socket() as s:
        s.settimeout(1)
        try:
            s.connect(("localhost", port))
            return True
        except Exception:
            return False


def _launch_chrome_on_port(port: int, profile_dir: str) -> subprocess.Popen:
    """Запускает Chrome на заданном CDP-порту и профиле."""
    print(f"  [port:{port}] Запускаю Chrome (профиль: {Path(profile_dir).name})...")
    proc = subprocess.Popen([
        CHROME_PATH,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        _IMAGINE_URL,
    ])
    for _ in range(30):
        time.sleep(1)
        if _is_cdp_open_on(port):
            print(f"  [port:{port}] Chrome готов (CDP).")
            return proc
    raise RuntimeError(f"Chrome не поднял CDP на порту {port} за 30 секунд")


def _worker_thread(
    worker_id: int,
    indices: list[int],
    photos: list[Path],
    prompts: list[str],
    out_dir: Path,
    session: str,
    platform: dict,
    cdp_port: int,
    profile_dir: str,
    completed_set: set,
    lock: threading.Lock,
    saved_counter: list,   # [int]
    tabs_status: dict,
    total: int,
) -> None:
    """Один воркер: открывает Chrome, обрабатывает свои видео по очереди."""
    tag = f"[Tab-{worker_id}]"
    print(f"\n{tag} Старт: {len(indices)} видео  #{indices[0]}..#{indices[-1]}", flush=True)

    chrome_proc = None
    try:
        chrome_proc = _launch_chrome_on_port(cdp_port, profile_dir)

        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(f"http://localhost:{cdp_port}")
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()

            # Инжектируем сохранённые куки (автологин)
            cookies = load_cookies(platform) if cookies_exist(platform) else []
            if cookies:
                ctx.add_cookies(cookies)
                print(f"  {tag} Куки инжектированы ({len(cookies)} записей)")

            page = ctx.new_page()
            page.goto(_IMAGINE_URL, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(3000)

            # Проверяем залогинен: ищем input[type=file] или contenteditable — они есть только у авторизованных
            def _is_logged_in(p) -> bool:
                try:
                    return p.evaluate("""() => {
                        // Если есть поле ввода — залогинен
                        if (document.querySelector("div[contenteditable='true']")) return true;
                        if (document.querySelector("input[type='file']")) return true;
                        // Если есть кнопка "Войти" прямо в хедере — не залогинен
                        const header = document.querySelector('header, nav, [class*="header"], [class*="nav"]');
                        if (header) {
                            const btns = Array.from(header.querySelectorAll('button, a'));
                            const hasLogin = btns.some(b => {
                                const txt = (b.textContent || '').trim().toLowerCase();
                                return txt === 'войти' || txt === 'sign in' || txt === 'login';
                            });
                            if (hasLogin) return false;
                        }
                        return true;
                    }""")
                except Exception:
                    return False

            if not _is_logged_in(page):
                # Попробуем перезагрузить — иногда куки применяются с задержкой
                print(f"  {tag} Куки применяются, перезагружаю...", flush=True)
                page.reload(wait_until="domcontentloaded", timeout=30_000)
                page.wait_for_timeout(3000)

            if not _is_logged_in(page):
                print(f"{tag} ❌ Не залогинен после инъекции куков — пропускаю вкладку")
                print(f"{tag} 💡 Запусти setup: py agents/media_generator/media_generator.py --platform 2 --setup")
                return

            print(f"  {tag} ✅ Сессия активна ({page.url[:60]})")
            # Обновляем сохранённые куки из живой сессии
            try:
                save_cookies(platform, ctx.cookies())
            except Exception:
                pass

            for idx in indices:
                out_path = out_dir / f"video_{idx:03d}.mp4"

                # Пропускаем уже готовые
                if out_path.exists() and out_path.stat().st_size > 1024:
                    with lock:
                        completed_set.add(idx)
                        saved_counter[0] += 1
                        tabs_status[worker_id]["done"] += 1
                        grok_save_progress(session, completed_set, total, tabs_status)
                    continue

                photo_path = photos[idx - 1]
                prompt     = prompts[idx - 1]

                print(f"\n{tag} [{idx}/{total}] {photo_path.stem}", flush=True)

                success = False
                for attempt in range(1, GROK_MAX_RETRIES + 1):
                    if attempt > 1:
                        print(f"  {tag} Попытка {attempt}/{GROK_MAX_RETRIES}...", flush=True)
                        try:
                            page.goto(_IMAGINE_URL, wait_until="domcontentloaded", timeout=30_000)
                            page.wait_for_timeout(3000)
                        except Exception:
                            break
                    try:
                        success = _grok_generate_one(page, idx, total, photo_path, prompt, out_path)
                    except Exception as e:
                        print(f"  {tag} ОШИБКА (попытка {attempt}): {e}")
                    if success:
                        break

                with lock:
                    if success:
                        completed_set.add(idx)
                        saved_counter[0] += 1
                        tabs_status[worker_id]["done"] += 1
                    grok_save_progress(session, completed_set, total, tabs_status)

                if success and saved_counter[0] % 10 == 0:
                    send_tg_notification(
                        f"🎬 Grok: {saved_counter[0]}/{total} видео"
                        + (f" [{session}]" if session else "")
                    )

                if idx != indices[-1]:
                    time.sleep(3)

            try:
                save_cookies(platform, ctx.cookies())
            except Exception:
                pass

    except Exception as e:
        print(f"{tag} КРИТИЧЕСКАЯ ОШИБКА: {e}", flush=True)
    finally:
        if chrome_proc:
            chrome_proc.terminate()
            print(f"  {tag} Chrome закрыт.")


# ---------------------------------------------------------------------------
# Grok image-to-video — multi-tab public API
# ---------------------------------------------------------------------------

def generate_grok_video(
    platform: dict,
    photos: list[Path],
    prompts: list[str],
    out_dir: Path,
    session: str = "",
    num_tabs: int | None = None,
) -> int:
    """
    Генерация видео через Grok (grok.com/imagine).
    num_tabs — количество параллельных Chrome вкладок (по умолчанию GROK_NUM_TABS).
    Каждая вкладка — отдельный Chrome-процесс на своём CDP-порту.
    """
    if num_tabs is None:
        num_tabs = GROK_NUM_TABS

    # Проверяем что хотя бы один таб-профиль существует (пользователь уже залогинился)
    base_profile = platform["profile_dir"]
    tab_profiles_exist = any(
        Path(f"{base_profile}-tab{i}").exists() for i in range(1, num_tabs + 1)
    )
    if not tab_profiles_exist and not cookies_exist(platform):
        run_setup_mode(platform)
        print("Перезапусти агент для начала генерации.")
        return 0

    total = min(len(photos), len(prompts))

    # Собираем что нужно сделать
    completed_set = grok_load_progress(session)
    todo = []
    for idx in range(1, total + 1):
        out_path = out_dir / f"video_{idx:03d}.mp4"
        if idx in completed_set or (out_path.exists() and out_path.stat().st_size > 1024):
            completed_set.add(idx)
        else:
            todo.append(idx)

    already_done = total - len(todo)
    print(f"\n  Всего видео: {total}  Готово: {already_done}  Осталось: {len(todo)}  Вкладок: {num_tabs}\n")

    if not todo:
        print("  Все видео уже готовы!")
        return total

    # Распределяем по вкладкам (round-robin)
    actual_tabs = min(num_tabs, len(todo))
    chunks: list[list[int]] = [[] for _ in range(actual_tabs)]
    for i, idx in enumerate(todo):
        chunks[i % actual_tabs].append(idx)

    lock          = threading.Lock()
    saved_counter = [0]
    tabs_status   = {
        i + 1: {"assigned": len(chunks[i]), "done": 0}
        for i in range(actual_tabs)
    }

    grok_save_progress(session, completed_set, total, tabs_status)

    base_profile = platform["profile_dir"]

    threads = []
    for i, chunk in enumerate(chunks):
        worker_id  = i + 1
        cdp_port   = CHROME_CDP_PORT + i
        # Отдельный профиль для каждой вкладки (Chrome не поддерживает общий профиль)
        profile_dir = f"{base_profile}-tab{worker_id}"

        t = threading.Thread(
            target=_worker_thread,
            args=(
                worker_id, chunk, photos, prompts, out_dir, session, platform,
                cdp_port, profile_dir, completed_set, lock, saved_counter,
                tabs_status, total,
            ),
            daemon=True,
            name=f"grok-tab-{worker_id}",
        )
        threads.append(t)

    # Запускаем все потоки
    for t in threads:
        t.start()

    # Ждём завершения
    for t in threads:
        t.join()

    final_saved = len(completed_set)

    if final_saved >= total:
        send_tg_notification(
            f"✅ Grok: все {total} видео готовы!"
            + (f"\n📁 {session}" if session else "")
        )

    grok_save_progress(session, completed_set, total, tabs_status)
    return final_saved
