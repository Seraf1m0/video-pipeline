"""
Grok Agent — Image-to-Video generation
----------------------------------------
Генерация видео через grok.com/imagine.
Требуется SuperGrok подписка.
Платформа 2 в media_generator.py.
"""

import base64
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

# Добавляем папку модуля в sys.path для импорта utils
_MODULE_DIR = Path(__file__).parent
if str(_MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(_MODULE_DIR))

from utils import (  # noqa: E402
    CHROME_CDP_PORT,
    GROK_VIDEO_TIMEOUT,
    GROK_DEBUG_DIR,
    GROK_PROGRESS_FILE,
    GROK_MAX_RETRIES,
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


# ---------------------------------------------------------------------------
# Grok image-to-video — public API
# ---------------------------------------------------------------------------

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
