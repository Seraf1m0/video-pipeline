"""
pipeline_runner.py — параллельная генерация фото + видео (streaming-режим).

Фото  : 7 потоков (v1×4 + v2×3) через pixel_agent.generate_photo
Видео : Grok запускается после завершения всех фото

Использование из media_generator:
    from pipeline_runner import run_pipeline
    run_pipeline(session, photo_prompts_json, video_prompts, photos_dir, videos_dir, platform, api_key)
"""

import json
import os
import queue
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

_MODULE_DIR = Path(__file__).parent
if str(_MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(_MODULE_DIR))

from pixel_agent import generate_photo  # noqa: E402

PHOTO_WORKERS = 7   # v1(4) + v2(3)


def run_pipeline(
    session:            str,
    photo_prompts_json: Path,
    video_prompts:      list[str],
    photos_dir:         Path,
    videos_dir:         Path,
    platform:           dict,
    api_key:            str,
) -> tuple[int, int]:
    """
    Параллельная генерация фото (7 потоков) → Grok видео.
    Возвращает (photos_saved, videos_saved).
    """
    # ── Загружаем фото-промпты из JSON ────────────────────────────────────
    if not photo_prompts_json.exists():
        print(f"❌ Нет photo_prompts.json: {photo_prompts_json}")
        return 0, 0

    raw = json.loads(photo_prompts_json.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        photo_prompts = {str(item["id"]): item for item in raw}
    else:
        photo_prompts = raw

    all_ids    = sorted(int(k) for k in photo_prompts.keys())
    total      = len(all_ids)
    start_time = time.time()

    # ── Счётчики ──────────────────────────────────────────────────────────
    _lock       = threading.Lock()
    photos_done = 0
    _peak       = {"v1": 0, "v2": 0}
    _active     = {"v1": 0, "v2": 0}

    def _inc(ver: str) -> None:
        with _lock:
            _active[ver] += 1
            if _active[ver] > _peak[ver]:
                _peak[ver] = _active[ver]

    def _dec(ver: str) -> None:
        with _lock:
            _active[ver] = max(0, _active[ver] - 1)

    ready_for_video: "queue.Queue[int | None]" = queue.Queue()

    # ── Генерация одного фото ─────────────────────────────────────────────
    def generate_single_photo(seg_id: int) -> None:
        nonlocal photos_done

        output = photos_dir / f"photo_{int(seg_id):03d}.png"
        if output.exists() and output.stat().st_size > 500:
            print(f"  ⏭️ Фото #{seg_id}: уже есть", flush=True)
            with _lock:
                photos_done += 1
            ready_for_video.put(seg_id)
            return

        prompt = photo_prompts.get(str(seg_id), {}).get("photo_prompt") \
              or photo_prompts.get(str(seg_id), {}).get("prompt", "")

        ver = "v1" if int(seg_id) % 2 == 0 else "v2"
        _inc(ver)

        # version=None → роутер в pixel_agent сам выберет v1 или v2
        success = generate_photo(
            prompt=prompt,
            output_path=str(output),
            seg_id=seg_id,
            version=None,
        )

        _dec(ver)
        with _lock:
            photos_done += 1

        elapsed      = time.time() - start_time
        version_used = "v1" if int(seg_id) % 2 == 0 else "v2"
        status       = "✓" if success else "✗"
        print(
            f"  🖼️ Фото #{seg_id} [{version_used}] {status} готово "
            f"({photos_done}/{total}) ⏱️ {elapsed:.0f}s",
            flush=True,
        )
        ready_for_video.put(seg_id)

    # ── 7 потоков фото (без батчей — семафоры внутри pixel_agent) ─────────
    photos_dir.mkdir(parents=True, exist_ok=True)
    print(f"🚀 Запуск {PHOTO_WORKERS} потоков фото (v1×4 + v2×3)...", flush=True)

    with ThreadPoolExecutor(max_workers=PHOTO_WORKERS) as ex:
        list(ex.map(generate_single_photo, all_ids))

    # Сигнал что все фото готовы
    ready_for_video.put(None)

    photos_saved = sum(
        1 for seg_id in all_ids
        if (photos_dir / f"photo_{int(seg_id):03d}.png").exists()
        and (photos_dir / f"photo_{int(seg_id):03d}.png").stat().st_size > 500
    )

    print(
        f"\n✅ Фото: {photos_saved}/{total} готово "
        f"(пик потоков: v1={_peak['v1']}, v2={_peak['v2']})",
        flush=True,
    )

    # ── Grok видео ────────────────────────────────────────────────────────
    from grok_agent import generate_grok_video  # noqa: E402
    from utils import read_photos               # noqa: E402

    photos_list = read_photos(session)
    videos_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n🎬 Запуск Grok видео ({len(photos_list)} фото)...", flush=True)

    n = min(len(photos_list), len(video_prompts))
    if len(photos_list) != len(video_prompts):
        print(f"  [!] Фото: {len(photos_list)}, видео-промптов: {len(video_prompts)} — беру первые {n}")

    videos_saved = generate_grok_video(
        platform=platform,
        photos=photos_list[:n],
        prompts=video_prompts[:n],
        out_dir=videos_dir,
        session=session,
    )

    elapsed_total = time.time() - start_time
    print(f"\n✅ Видео: {videos_saved}/{n} готово. Общее время: {elapsed_total:.0f}s", flush=True)
    return photos_saved, videos_saved
