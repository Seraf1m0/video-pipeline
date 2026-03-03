"""
PixelAgent — Photo generation via API
--------------------------------------
Asyncio + aiohttp, параллельная генерация фото.
Платформа 3 в media_generator.py.
"""

import asyncio
import base64
import io
import json
import sys
from pathlib import Path

import aiohttp
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
