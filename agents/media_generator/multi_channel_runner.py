"""
multi_channel_runner.py — параллельная генерация двух каналов.

Два канала запускаются одновременно:
  - Фото: 7 потоков (v1×4 + v2×3), общие семафоры на оба канала
  - Видео: Grok после завершения всех фото (по одному каналу за раз — Chrome)
  - Монтаж: последовательно через GPU_SEMAPHORE

Запуск:
  py agents/media_generator/multi_channel_runner.py
"""

import subprocess
import sys
import threading
import time
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from queue import Queue, Empty

# ─── Пути ─────────────────────────────────────────────────────────────────────
_BASE_DIR = Path(__file__).resolve().parent.parent.parent
_UTILS_DIR = _BASE_DIR / "agents" / "utils"
if str(_UTILS_DIR) not in sys.path:
    sys.path.insert(0, str(_UTILS_DIR))
_MEDIA_GEN_DIR = Path(__file__).resolve().parent
if str(_MEDIA_GEN_DIR) not in sys.path:
    sys.path.insert(0, str(_MEDIA_GEN_DIR))
_BOT_DIR = _BASE_DIR / "bot"
if str(_BOT_DIR) not in sys.path:
    sys.path.insert(0, str(_BOT_DIR))

from paths import (  # noqa: E402
    get_prompts_dir, get_media_dir,
    get_output_dir, get_last_session,
    ensure_session_dirs,
)

# ─── ГЛОБАЛЬНЫЕ СЕМАФОРЫ (общие на ВСЕ каналы) ────────────────────────────────
PIXEL_V1_SEMAPHORE = threading.Semaphore(4)
PIXEL_V2_SEMAPHORE = threading.Semaphore(3)
GROK_SEMAPHORE     = threading.Semaphore(1)  # Chrome — только один канал за раз
GPU_SEMAPHORE      = threading.Semaphore(1)  # FFmpeg монтаж — один канал за раз


def run_channel_pipeline(
    channel_id: str,
    session: str,
    channel_config: dict,
    progress_callback=None,
) -> dict:
    """
    Полный пайплайн одного канала:
      - Фото (7 потоков v1+v2) параллельно
      - После всех фото → Grok видео (через GROK_SEMAPHORE)
      - Монтаж через GPU очередь
    """
    from pixel_agent import generate_photo_v1, generate_photo_v2  # noqa: E402

    channel_name = channel_config.get("name", channel_id)
    prompts_dir  = get_prompts_dir(channel_id, session)
    photos_dir   = get_media_dir(channel_id, session) / "photos"
    videos_dir   = get_media_dir(channel_id, session) / "videos"
    photos_dir.mkdir(parents=True, exist_ok=True)
    videos_dir.mkdir(parents=True, exist_ok=True)

    # ── Загрузка промптов ──────────────────────────────────────────────────
    photo_prompts_path = prompts_dir / "photo_prompts.json"
    video_prompts_path = prompts_dir / "video_prompts.json"

    if not photo_prompts_path.exists():
        print(f"[{channel_name}] ❌ Нет photo_prompts.json: {photo_prompts_path}")
        return {"status": "error", "error": "no photo_prompts.json"}
    if not video_prompts_path.exists():
        print(f"[{channel_name}] ❌ Нет video_prompts.json: {video_prompts_path}")
        return {"status": "error", "error": "no video_prompts.json"}

    with open(photo_prompts_path, encoding="utf-8") as f:
        photo_prompts = json.load(f)
    with open(video_prompts_path, encoding="utf-8") as f:
        video_prompts_raw = json.load(f)

    # Нормализация: list → dict
    if isinstance(photo_prompts, list):
        photo_prompts = {str(item["id"]): item for item in photo_prompts}
    if isinstance(video_prompts_raw, list):
        video_prompts = {str(item["id"]): item for item in video_prompts_raw}
    else:
        video_prompts = video_prompts_raw

    all_ids = sorted(photo_prompts.keys(), key=lambda x: int(x))
    total   = len(all_ids)

    stats = {
        "photos_done": 0,
        "videos_done": 0,
        "total":       total,
        "start_time":  time.time(),
        "status":      "running",
    }

    def log(msg: str) -> None:
        elapsed = time.time() - stats["start_time"]
        line = f"[{channel_name}] {msg} ⏱️ {elapsed:.0f}s"
        print(line, flush=True)
        if progress_callback:
            progress_callback(channel_id, stats, msg)

    video_queue: "Queue[str | None]" = Queue()

    # ── ФОТО ВОРКЕР ───────────────────────────────────────────────────────
    def photo_worker() -> None:
        def generate_one(seg_id: str) -> None:
            output = photos_dir / f"photo_{int(seg_id):03d}.png"
            if output.exists() and output.stat().st_size > 500:
                stats["photos_done"] += 1
                video_queue.put(seg_id)
                return

            item   = photo_prompts.get(str(seg_id), {})
            prompt = item.get("photo_prompt") or item.get("prompt", "")

            if int(seg_id) % 2 == 0:
                with PIXEL_V1_SEMAPHORE:
                    success = generate_photo_v1(prompt, str(output), int(seg_id))
                ver = "v1"
            else:
                with PIXEL_V2_SEMAPHORE:
                    success = generate_photo_v2(prompt, str(output), int(seg_id))
                ver = "v2"

            stats["photos_done"] += 1
            log(f"🖼️ Фото #{seg_id} [{ver}] {'✓' if success else '✗'} ({stats['photos_done']}/{total})")
            video_queue.put(seg_id)

        # 7 потоков — семафоры внутри ограничат по версиям
        with ThreadPoolExecutor(max_workers=7) as ex:
            list(ex.map(generate_one, all_ids))
        video_queue.put(None)  # сигнал конца
        log("✅ Все фото готовы!")

    # ── ВИДЕО ВОРКЕР ──────────────────────────────────────────────────────
    def video_worker() -> None:
        # Собираем все seg_ids из очереди, затем вызываем Grok одним батчем
        collected: list[str] = []
        while True:
            try:
                seg_id = video_queue.get(timeout=600)
            except Empty:
                log("⚠️ Timeout очереди видео")
                break
            if seg_id is None:
                log("⏳ Все фото готовы — запускаю Grok...")
                break
            collected.append(seg_id)

        if not collected:
            return

        # Формируем списки photos + prompts в порядке собранных ID
        from utils import PLATFORMS  # noqa: E402
        grok_platform = PLATFORMS["2"].copy()
        grok_platform["key"] = "2"

        photos_list = []
        prompts_list = []
        for seg_id in sorted(collected, key=lambda x: int(x)):
            photo_path = photos_dir / f"photo_{int(seg_id):03d}.png"
            if photo_path.exists():
                item = video_prompts.get(str(seg_id), {})
                prompt = item.get("video_prompt") or item.get("prompt", "")
                photos_list.append(photo_path)
                prompts_list.append(prompt)

        if not photos_list:
            log("⚠️ Нет фото для Grok")
            return

        # Один канал за раз в Grok (Chrome ограничение)
        with GROK_SEMAPHORE:
            from grok_agent import generate_grok_video  # noqa: E402
            log(f"🎬 Grok: {len(photos_list)} видео...")
            n_saved = generate_grok_video(
                platform=grok_platform,
                photos=photos_list,
                prompts=prompts_list,
                out_dir=videos_dir,
                session=session,
            )
            stats["videos_done"] = n_saved
            log(f"✅ Grok: {n_saved}/{len(photos_list)} видео готово!")

    # ── СТАРТ ─────────────────────────────────────────────────────────────
    log(f"🚀 Старт ({total} сег.) | Pixel 7 потоков | Grok 1 слот")

    photo_t = threading.Thread(
        target=photo_worker, name=f"Photo-{channel_id}", daemon=True
    )
    video_t = threading.Thread(
        target=video_worker, name=f"Video-{channel_id}", daemon=True
    )
    photo_t.start()
    video_t.start()
    photo_t.join()
    video_t.join()

    elapsed = time.time() - stats["start_time"]
    stats["status"] = "done"
    log(f"🎉 Генерация готова! Время: {elapsed / 60:.1f} мин")
    return stats


def run_two_channels_parallel(
    channel_id_de: str, session_de: str, config_de: dict,
    channel_id_fr: str, session_fr: str, config_fr: dict,
    progress_callback=None,
) -> dict:
    """
    Два канала параллельно.
    Генерация — параллельно с общими лимитами.
    Монтаж — последовательно через GPU_SEMAPHORE.
    """
    print("\n" + "=" * 60)
    print("🚀 ПАРАЛЛЕЛЬНЫЙ ЗАПУСК ДВУХ КАНАЛОВ")
    print(f"  DE: {session_de}")
    print(f"  FR: {session_fr}")
    print(f"  Pixel: 7 потоков общих")
    print(f"  Grok:  1 слот (Chrome)")
    print("=" * 60 + "\n")

    results: dict = {}
    errors:  dict = {}

    def run_ch(ch_id: str, session: str, config: dict) -> None:
        try:
            r = run_channel_pipeline(ch_id, session, config, progress_callback)
            results[ch_id] = r
        except Exception as e:
            errors[ch_id] = str(e)
            print(f"❌ [{config.get('name', ch_id)}] {e}")

    t_de = threading.Thread(
        target=run_ch, args=(channel_id_de, session_de, config_de),
        name="Channel-DE", daemon=True,
    )
    t_fr = threading.Thread(
        target=run_ch, args=(channel_id_fr, session_fr, config_fr),
        name="Channel-FR", daemon=True,
    )

    start = time.time()
    t_de.start()
    t_fr.start()
    t_de.join()
    t_fr.join()

    elapsed = time.time() - start
    print(f"\n✅ Генерация обоих каналов: {elapsed / 60:.1f} мин")

    # ── Монтаж через GPU очередь ───────────────────────────────────────────
    print("\n⏳ GPU очередь монтажа...")
    _run_montage_queue([
        (channel_id_de, session_de, config_de),
        (channel_id_fr, session_fr, config_fr),
    ])

    return results


def _run_montage_queue(channel_sessions: list) -> None:
    """Монтаж последовательно — один GPU."""
    for ch_id, session, config in channel_sessions:
        name = config.get("name", ch_id)
        print(f"\n🎬 Монтаж: {name} ({session})...")
        with GPU_SEMAPHORE:
            r = subprocess.run(
                ["py", "agents/assembler/assembler.py",
                 "--session", session,
                 "--channel", ch_id],
                capture_output=False,
                cwd=str(_BASE_DIR),
            )
            if r.returncode == 0:
                print(f"✅ {name}: монтаж готов!")
            else:
                print(f"❌ {name}: ошибка монтажа! (rc={r.returncode})")


if __name__ == "__main__":
    from channel_manager import get_all_channels  # noqa: E402

    channels = get_all_channels()
    if len(channels) < 2:
        print("❌ Нужно минимум 2 канала!")
        sys.exit(1)

    ch_de = channels[0]
    ch_fr = channels[1]
    s_de  = get_last_session(ch_de["id"])
    s_fr  = get_last_session(ch_fr["id"])

    if not s_de or not s_fr:
        print(f"❌ Сессии не найдены!")
        print(f"  DE: {s_de}")
        print(f"  FR: {s_fr}")
        sys.exit(1)

    print(f"DE сессия: {s_de}")
    print(f"FR сессия: {s_fr}")
    run_two_channels_parallel(
        ch_de["id"], s_de, ch_de,
        ch_fr["id"], s_fr, ch_fr,
    )
