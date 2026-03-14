"""
PixelAgent — Photo generation via API
--------------------------------------
Asyncio + aiohttp, параллельная генерация фото.
Платформа 3 в media_generator.py.

Поддерживает v1 (синхронный) и v2 (task-based) режимы API.
Версия выбирается через PIXEL_API_VERSION в config/.env.
"""

import asyncio
import base64
import io
import json
import os
import sys
from pathlib import Path

import aiohttp
import numpy as np
from PIL import Image

# Добавляем папку модуля в sys.path для импорта utils
_MODULE_DIR = Path(__file__).parent
if str(_MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(_MODULE_DIR))

from utils import (  # noqa: E402
    PIXEL_API_URL,
    PIXEL_MAX_CONCURRENT,
    PIXEL_PROGRESS_FILE,
    send_tg_notification,
)


# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

# Паузы после каждой провалившейся попытки (индекс = attempt - 1)
RETRY_DELAYS = [5, 10, 20, 30, 60, 120]  # попытки 1-6, после 7й → FAILED
MAX_ATTEMPTS = 7
AUTO_RETRY_ROUNDS = 3       # максимум кругов автоповтора
AUTO_RETRY_PAUSE  = 60      # пауза между кругами (сек)
REQUEST_TIMEOUT   = 180     # таймаут одного запроса v1 (сек)

# Версия API: "v1" (синхронный) или "v2" (task-based polling)
PIXEL_API_VERSION = os.environ.get("PIXEL_API_VERSION", "v2").strip()


# ---------------------------------------------------------------------------
# Спецсключение для ошибки авторизации
# ---------------------------------------------------------------------------

class _AuthError(Exception):
    """401 — неверный API ключ. Стоп всей генерации без retry."""
    pass


# ---------------------------------------------------------------------------
# Прогресс PixelAgent (temp/pixel_progress.json)
# ---------------------------------------------------------------------------

def _write_pixel_progress(current: int, total: int, status: str,
                           failed: list[int], **extra) -> None:
    PIXEL_PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = {
            "current": current,
            "total":   total,
            "status":  status,
            "failed":  sorted(failed),
            "threads": PIXEL_MAX_CONCURRENT,
        }
        data.update(extra)
        PIXEL_PROGRESS_FILE.write_text(
            json.dumps(data, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


def _progress_extra(progress: dict) -> dict:
    """Возвращает autoretry-поля из progress для записи в JSON."""
    return {
        k: progress[k]
        for k in ("autoretry_round", "autoretry_done", "autoretry_total")
        if k in progress
    }


# ---------------------------------------------------------------------------
# Валидация и автоисправление изображения
# ---------------------------------------------------------------------------

def _validate_and_fix_image(img_data: bytes, idx: int) -> tuple[bytes, "Image.Image"]:
    """
    Проверяет и при необходимости исправляет изображение:

    1. Портрет (w <= h)        → ValueError, повтор
    2. Пейзаж, не 16:9         → кадрируем по центру до 16:9
    3. Пиксель max < 15 / 255  → полностью чёрное (API failure), повтор
       (mean < 10 было слишком строго — тёмные сцены (космос, ночь)
        имеют низкое mean, но при этом содержат видимый контент)

    Возвращает (bytes, PIL.Image) — возможно исправленные данные.
    """
    img = Image.open(io.BytesIO(img_data))
    w, h = img.size

    # ── Портрет (строго) → retry ──────────────────────────────────────────
    # Квадраты (w == h) → уходят в crop-ветку ниже
    if w < h:
        raise ValueError(
            f"Вертикальное фото {w}x{h} (ratio={w/h:.3f}) — retry"
        )

    # ── Пейзаж не 16:9 → кадрировать по центру ───────────────────────────
    ratio = w / h
    if ratio < 1.7 or ratio > 1.8:
        target_h = int(round(w * 9 / 16))
        if target_h <= h:
            top = (h - target_h) // 2
            img = img.crop((0, top, w, top + target_h))
            print(
                f"  [{idx}] [crop] {w}x{h} -> {w}x{target_h} (16:9)",
                flush=True,
            )
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            img_data = buf.getvalue()
            w, h = img.size
        else:
            raise ValueError(
                f"Нельзя кадрировать {w}x{h} до 16:9 "
                f"(нужна высота {target_h}, есть {h})"
            )

    # ── Полностью чёрное (API failure) → retry ────────────────────────────
    # Используем max-пиксель, а НЕ mean: тёмные сцены (космос, ночь)
    # имеют низкое mean, но содержат видимые яркие пиксели (звёзды, огни).
    arr = np.array(img)
    max_brightness = int(arr.max())
    if max_brightness < 15:
        raise ValueError(
            f"Фото полностью чёрное (max_brightness={max_brightness}) — API failure"
        )

    return img_data, img


# ---------------------------------------------------------------------------
# v1: Генерация одного фото (синхронный ответ с image_b64)
# ---------------------------------------------------------------------------

async def _pixel_generate_one(
    http:        aiohttp.ClientSession,
    sem:         asyncio.Semaphore,
    idx:         int,
    prompt:      str,
    out_path:    Path,
    total_global: int,
    progress:    dict,
    abort_event: asyncio.Event,
) -> bool:
    """
    v1 API: POST /api/v1/image/create → {"image_b64": "..."}.
    7 попыток с умными паузами.
    401 → _AuthError (abort всей генерации).
    """
    async with sem:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            if abort_event.is_set():
                return False

            try:
                # ── Запрос к API ───────────────────────────────────────────
                try:
                    async with http.post(
                        f"{PIXEL_API_URL}/api/v1/image/create",
                        json={"prompt": prompt, "aspect_ratio": "16:9"},
                        timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
                    ) as resp:
                        if resp.status == 401:
                            print(f"  [{idx}] 401 — неверный API ключ, останавливаю всё", flush=True)
                            abort_event.set()
                            raise _AuthError("401: неверный API ключ")

                        if resp.status != 200:
                            text = await resp.text()
                            raise ValueError(f"API ошибка {resp.status}: {text[:200]}")

                        data = await resp.json()
                        img_b64 = data.get("image_b64")
                        if not img_b64:
                            raise ValueError("Нет image_b64 в ответе")

                        img_data = base64.b64decode(img_b64)

                except asyncio.TimeoutError:
                    raise ValueError(f"Таймаут {REQUEST_TIMEOUT} сек")
                except (_AuthError, ValueError):
                    raise
                except Exception as e:
                    raise ValueError(f"Ошибка запроса: {e}")

                # ── Проверки и исправление изображения ────────────────────
                img_data, img = _validate_and_fix_image(img_data, idx)

                # ── Сохранение ─────────────────────────────────────────────
                out_path.write_bytes(img_data)
                # Удаляем маркер FAILED если он был (от предыдущего круга retry)
                fail_marker = out_path.parent / f"photo_{idx:03d}_FAILED.txt"
                fail_marker.unlink(missing_ok=True)

                size_kb = len(img_data) // 1024
                print(f"  [{idx}/{total_global}] ✓  {out_path.name} ({size_kb}KB)", flush=True)

                progress["done"] += 1
                if "autoretry_done" in progress:
                    progress["autoretry_done"] += 1
                _write_pixel_progress(
                    progress["done"], total_global,
                    progress.get("status", "running"),
                    progress["failed"],
                    **_progress_extra(progress),
                )
                return True

            except asyncio.CancelledError:
                raise
            except _AuthError:
                raise
            except Exception as e:
                print(f"  [{idx}] попытка {attempt}/{MAX_ATTEMPTS} ошибка: {e}", flush=True)
                if attempt < MAX_ATTEMPTS:
                    delay = RETRY_DELAYS[attempt - 1]
                    print(f"  [{idx}] пауза {delay}с...", flush=True)
                    await asyncio.sleep(delay)

    # Все 7 попыток провалились
    print(f"  [{idx}/{total_global}] ✗  FAILED после {MAX_ATTEMPTS} попыток", flush=True)
    fail_path = out_path.parent / f"photo_{idx:03d}_FAILED.txt"
    try:
        fail_path.write_text(
            f"Failed after {MAX_ATTEMPTS} attempts\nPrompt: {prompt[:200]}",
            encoding="utf-8",
        )
    except Exception:
        pass

    progress["failed"].append(idx)
    progress["done"] += 1
    if "autoretry_done" in progress:
        progress["autoretry_done"] += 1
    _write_pixel_progress(
        progress["done"], total_global,
        progress.get("status", "running"),
        progress["failed"],
        **_progress_extra(progress),
    )
    return False


# ---------------------------------------------------------------------------
# v2: Генерация одного фото (task-based polling)
# ---------------------------------------------------------------------------

async def _pixel_generate_one_v2(
    http:        aiohttp.ClientSession,
    sem:         asyncio.Semaphore,
    idx:         int,
    prompt:      str,
    out_path:    Path,
    total_global: int,
    progress:    dict,
    abort_event: asyncio.Event,
) -> bool:
    """
    v2 API: POST /api/v2/image/generate → task_id → poll status → download.
    7 попыток с умными паузами.
    401 → _AuthError (abort всей генерации).
    """
    async with sem:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            if abort_event.is_set():
                return False

            try:
                # ── Шаг 1: создать задачу ─────────────────────────────────
                try:
                    async with http.post(
                        f"{PIXEL_API_URL}/api/v2/image/generate",
                        json={"prompt": prompt, "aspect_ratio": "16:9"},
                        timeout=aiohttp.ClientTimeout(total=30),
                    ) as resp:
                        if resp.status == 401:
                            print(f"  [{idx}] 401 — неверный API ключ, останавливаю всё", flush=True)
                            abort_event.set()
                            raise _AuthError("401: неверный API ключ")

                        if resp.status not in (200, 202):
                            text = await resp.text()
                            raise ValueError(f"Create ошибка {resp.status}: {text[:200]}")

                        data = await resp.json()
                        task_id = data.get("task_id")
                        if not task_id:
                            raise ValueError(f"Нет task_id в ответе: {data}")

                        print(f"  [{idx}] v2 task={task_id} — ожидаю...", flush=True)

                except asyncio.TimeoutError:
                    raise ValueError("Таймаут create (30 сек)")
                except (_AuthError, ValueError):
                    raise
                except Exception as e:
                    raise ValueError(f"Ошибка create: {e}")

                # ── Шаг 2: polling статуса (до 3 минут) ───────────────────
                status_ok = False
                for _poll in range(60):  # 60 × 3с = 180с
                    await asyncio.sleep(3)
                    if abort_event.is_set():
                        return False
                    try:
                        async with http.get(
                            f"{PIXEL_API_URL}/api/v2/image/tasks/{task_id}/status",
                            timeout=aiohttp.ClientTimeout(total=15),
                        ) as st_resp:
                            if st_resp.status != 200:
                                continue
                            st_data = await st_resp.json()
                            st = st_data.get("status", "")
                            if st == "completed":
                                status_ok = True
                                break
                            if st == "failed":
                                err = st_data.get("error", "нет деталей")
                                raise ValueError(f"Задача провалилась: {err}")
                    except (_AuthError, ValueError):
                        raise
                    except Exception:
                        pass  # временная сетевая ошибка при poll — продолжаем

                if not status_ok:
                    raise ValueError(f"Таймаут polling 180 сек (task={task_id})")

                # ── Шаг 3: скачать изображение ────────────────────────────
                try:
                    async with http.get(
                        f"{PIXEL_API_URL}/api/v2/image/tasks/{task_id}/download",
                        timeout=aiohttp.ClientTimeout(total=60),
                    ) as dl_resp:
                        if dl_resp.status == 401:
                            abort_event.set()
                            raise _AuthError("401: неверный API ключ при download")
                        if dl_resp.status != 200:
                            text = await dl_resp.text()
                            raise ValueError(f"Download ошибка {dl_resp.status}: {text[:200]}")
                        img_data = await dl_resp.read()

                except asyncio.TimeoutError:
                    raise ValueError("Таймаут download (60 сек)")
                except (_AuthError, ValueError):
                    raise
                except Exception as e:
                    raise ValueError(f"Ошибка download: {e}")

                # ── Проверки и исправление изображения ────────────────────
                img_data, img = _validate_and_fix_image(img_data, idx)

                # ── Сохранение ─────────────────────────────────────────────
                out_path.write_bytes(img_data)
                fail_marker = out_path.parent / f"photo_{idx:03d}_FAILED.txt"
                fail_marker.unlink(missing_ok=True)

                size_kb = len(img_data) // 1024
                print(f"  [{idx}/{total_global}] ✓  {out_path.name} ({size_kb}KB)", flush=True)

                progress["done"] += 1
                if "autoretry_done" in progress:
                    progress["autoretry_done"] += 1
                _write_pixel_progress(
                    progress["done"], total_global,
                    progress.get("status", "running"),
                    progress["failed"],
                    **_progress_extra(progress),
                )
                return True

            except asyncio.CancelledError:
                raise
            except _AuthError:
                raise
            except Exception as e:
                print(f"  [{idx}] попытка {attempt}/{MAX_ATTEMPTS} ошибка: {e}", flush=True)
                if attempt < MAX_ATTEMPTS:
                    delay = RETRY_DELAYS[attempt - 1]
                    print(f"  [{idx}] пауза {delay}с...", flush=True)
                    await asyncio.sleep(delay)

    # Все 7 попыток провалились
    print(f"  [{idx}/{total_global}] ✗  FAILED после {MAX_ATTEMPTS} попыток", flush=True)
    fail_path = out_path.parent / f"photo_{idx:03d}_FAILED.txt"
    try:
        fail_path.write_text(
            f"Failed after {MAX_ATTEMPTS} attempts\nPrompt: {prompt[:200]}",
            encoding="utf-8",
        )
    except Exception:
        pass

    progress["failed"].append(idx)
    progress["done"] += 1
    if "autoretry_done" in progress:
        progress["autoretry_done"] += 1
    _write_pixel_progress(
        progress["done"], total_global,
        progress.get("status", "running"),
        progress["failed"],
        **_progress_extra(progress),
    )
    return False


# ---------------------------------------------------------------------------
# Один батч генерации
# ---------------------------------------------------------------------------

async def _run_batch(
    to_generate:  list[tuple[int, str]],
    out_dir:      Path,
    api_key:      str,
    total_global: int,
    progress:     dict,
) -> list[int]:
    """
    Запускает один батч (параллельно, с семафором).
    Возвращает список индексов, провалившихся в ЭТОМ батче.
    Поднимает _AuthError если получен 401.
    Использует v1 или v2 в зависимости от PIXEL_API_VERSION.
    """
    failed_before = set(progress["failed"])

    _write_pixel_progress(
        progress["done"], total_global,
        progress.get("status", "running"),
        progress["failed"],
        **_progress_extra(progress),
    )

    sem         = asyncio.Semaphore(PIXEL_MAX_CONCURRENT)
    abort_event = asyncio.Event()
    headers     = {"X-API-Key": api_key, "Content-Type": "application/json"}

    # Выбираем реализацию в зависимости от версии API
    _generate_fn = (
        _pixel_generate_one_v2
        if PIXEL_API_VERSION == "v2"
        else _pixel_generate_one
    )

    async with aiohttp.ClientSession(headers=headers) as http:
        tasks = [
            asyncio.create_task(
                _generate_fn(
                    http, sem, idx, prompt,
                    out_dir / f"photo_{idx:03d}.png",
                    total_global, progress, abort_event,
                )
            )
            for idx, prompt in to_generate
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    # Пробрасываем _AuthError если была
    for r in results:
        if isinstance(r, _AuthError):
            raise r

    # Возвращаем только те, что провалились В ЭТОМ батче
    return sorted(idx for idx in progress["failed"] if idx not in failed_before)


# ---------------------------------------------------------------------------
# Главная async-функция с авто-retry
# ---------------------------------------------------------------------------

async def generate_pixel_photos_async(
    prompts: list[str],
    out_dir: Path,
    api_key: str,
) -> tuple[int, list[int]]:
    """
    Параллельная генерация + авто-retry до AUTO_RETRY_ROUNDS кругов.
    Возвращает (saved_count, final_failed_indices).
    """
    total    = len(prompts)
    progress = {"done": 0, "failed": [], "status": "running"}

    # Пропускаем уже готовые
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
        f"{PIXEL_MAX_CONCURRENT} потоков | {MAX_ATTEMPTS} попыток | API {PIXEL_API_VERSION}\n",
        flush=True,
    )

    # ── Первичная генерация ─────────────────────────────────────────────────
    try:
        current_failed = await _run_batch(to_generate, out_dir, api_key, total, progress)
    except _AuthError as e:
        print(f"\n  ❌ Критическая ошибка авторизации: {e}", flush=True)
        _write_pixel_progress(total, total, "auth_error", progress["failed"])
        saved = _count_saved(out_dir, total)
        return saved, progress["failed"]

    # ── Авто-retry (до AUTO_RETRY_ROUNDS кругов) ────────────────────────────
    for retry_round in range(1, AUTO_RETRY_ROUNDS + 1):
        if not current_failed:
            break

        print(
            f"\n  🔄 Автоповтор #{retry_round}/{AUTO_RETRY_ROUNDS}: "
            f"{len(current_failed)} провалившихся фото...",
            flush=True,
        )
        print(f"  ⏳ Пауза {AUTO_RETRY_PAUSE}с перед автоповтором...", flush=True)
        await asyncio.sleep(AUTO_RETRY_PAUSE)

        # Настраиваем поля для TG-отображения
        retry_total = len(current_failed)
        progress["status"]          = "autoretry"
        progress["autoretry_round"] = retry_round
        progress["autoretry_total"] = retry_total
        progress["autoretry_done"]  = 0

        # Убираем текущие failed из списка (они будут заново добавлены если снова провалятся)
        for idx in current_failed:
            if idx in progress["failed"]:
                progress["failed"].remove(idx)

        retry_batch = [(idx, prompts[idx - 1]) for idx in current_failed]

        try:
            current_failed = await _run_batch(retry_batch, out_dir, api_key, total, progress)
        except _AuthError as e:
            print(f"\n  ❌ Критическая ошибка авторизации: {e}", flush=True)
            break

        # Очищаем autoretry-поля
        for k in ("autoretry_round", "autoretry_done", "autoretry_total"):
            progress.pop(k, None)
        progress["status"] = "running"

        if current_failed:
            print(
                f"  ⚠️  После круга #{retry_round}: осталось {len(current_failed)} ошибок",
                flush=True,
            )
        else:
            print(f"  ✅ Автоповтор #{retry_round}: все фото восстановлены!", flush=True)

    # ── Финал ───────────────────────────────────────────────────────────────
    saved = _count_saved(out_dir, total)
    _write_pixel_progress(total, total, "completed", sorted(current_failed))
    return saved, sorted(current_failed)


def _count_saved(out_dir: Path, total: int) -> int:
    return sum(
        1 for idx in range(1, total + 1)
        if (out_dir / f"photo_{idx:03d}.png").exists()
        and (out_dir / f"photo_{idx:03d}.png").stat().st_size > 500
    )


# ---------------------------------------------------------------------------
# Синхронная обёртка
# ---------------------------------------------------------------------------

def generate_pixel(media_type: str, prompts: list[str],
                   out_dir: Path, api_key: str) -> int:
    """Синхронная обёртка для asyncio: запускает асинхронную генерацию."""
    if media_type != "photo":
        print(f"  [!] PixelAgent поддерживает только фото (запрошено: {media_type})")
        return 0

    total = len(prompts)
    print(f"\n  PixelAgent | Всего: {total} фото | MAX_CONCURRENT={PIXEL_MAX_CONCURRENT} | API {PIXEL_API_VERSION}\n")

    saved, failed = asyncio.run(generate_pixel_photos_async(prompts, out_dir, api_key))

    if failed:
        print(f"\n  ⚠️  После всех кругов retry: {len(failed)} ошибок — созданы заглушки *_FAILED.txt")
        print(f"  Провалившиеся: {failed}")
        send_tg_notification(
            f"⚠️ PixelAgent: {len(failed)}/{total} фото не сгенерированы "
            f"(после {AUTO_RETRY_ROUNDS} кругов авто-retry)\n"
            f"Провалившиеся: {failed}"
        )
    else:
        print(f"\n  ✅ Все {saved}/{total} фото успешно сгенерированы!")

    print(f"\nСгенерировано: {saved}")
    print(f"Всего: {total}")
    return saved
