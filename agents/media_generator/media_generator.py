"""
Media Generator Agent — Router
--------------------------------
Поддерживаемые платформы:
  1. Google Flow  (браузер + куки)
  2. Grok         (браузер + куки) — image-to-video (SuperGrok)
  3. PixelAgent   (API, asyncio + aiohttp, параллельная генерация)

Запуск:
  py agents/media_generator/media_generator.py                             # интерактивный
  py agents/media_generator/media_generator.py --platform 3 --type photo  # из бота
  py agents/media_generator/media_generator.py --platform 2 --type video --session Video_xxx
"""

import argparse
import io
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# Добавляем папку модуля в sys.path для импорта utils, pixel_agent, grok_agent, flow_agent
_MODULE_DIR = Path(__file__).parent
if str(_MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(_MODULE_DIR))

from utils import (  # noqa: E402
    PLATFORMS,
    PIXEL_API_URL,
    PIXEL_API_KEY,
    MEDIA_DIR,
    PROMPTS_DIR,
    find_latest_session,
    read_prompts,
    read_photos,
    make_output_dir,
    ask_platform,
    ask_media_type,
)
from pixel_agent import generate_pixel        # noqa: E402
from grok_agent import generate_grok_video    # noqa: E402  (multi-tab)
from flow_agent import generate_flow          # noqa: E402


def run() -> None:
    print("\n=== Media Generator ===")

    parser = argparse.ArgumentParser(description="Генератор медиа (фото и видео)")
    parser.add_argument("--platform", choices=["1", "2", "3"],
                        help="Платформа: 1=Flow, 2=Grok, 3=PixelAgent")
    parser.add_argument("--type", dest="gen_type", choices=["photo", "video", "both"],
                        help="Тип: photo | video | both")
    parser.add_argument("--session", help="Имя сессии (по умолчанию — последняя)")
    parser.add_argument("--tabs", type=int, default=None,
                        help="Кол-во вкладок для Grok (по умолчанию GROK_NUM_TABS из .env)")
    parser.add_argument("--streaming", action="store_true",
                        help="Параллельный режим: 7 потоков фото (v1×4 + v2×3) → Grok видео")
    parser.add_argument("--regenerate", type=int, default=None,
                        help="Перегенерировать только видео с указанным индексом (1-based)")
    args = parser.parse_args()

    # ── Сессия ────────────────────────────────────────────────────────────
    session = args.session or find_latest_session()
    if not session:
        print("Нет сессий в data/prompts/")
        return
    print(f"Сессия: {session}")

    # ── Regenerate режим (перегенерировать одно видео) ────────────────────
    if args.regenerate is not None:
        from grok_agent import grok_load_progress, grok_save_progress  # noqa: E402
        regen_idx = args.regenerate
        platform = PLATFORMS["2"].copy()
        platform["key"] = "2"
        photos   = read_photos(session)
        prompts  = read_prompts(session, "video")
        out_dir  = make_output_dir(session, "videos")

        if regen_idx < 1 or regen_idx > min(len(photos), len(prompts)):
            print(f"[!] Индекс {regen_idx} вне диапазона (1-{min(len(photos), len(prompts))})")
            return

        # Удаляем старое видео и убираем из прогресса
        old_file = out_dir / f"video_{regen_idx:03d}.mp4"
        if old_file.exists():
            old_file.unlink()
            print(f"  Удалено старое: {old_file.name}")

        completed = grok_load_progress(session)
        completed.discard(regen_idx)
        grok_save_progress(session, completed, min(len(photos), len(prompts)))

        # Подготовить одноэлементные списки для нужного индекса
        # Маппинг: передаём один элемент, но generate_grok_video использует idx 1..n
        # Нам нужно чтобы видео сохранилось как video_REGEN_IDX, поэтому временно
        # подменяем out_dir на тот же, но список photos/prompts с 1 элементом
        # Но тогда имя будет video_001.mp4, а не video_NNN.mp4 — неверно.
        # Лучше: удалить файл, убрать из прогресса, и запустить полную генерацию
        # с существующим прогрессом — она пропустит уже готовые и сделает только regen_idx.
        print(f"  🔄 Перегенерирую видео #{regen_idx:03d}...")
        saved = generate_grok_video(
            platform, photos, prompts, out_dir, session=session,
        )
        print(f"  ✅ Перегенерировано: {saved} видео")
        return

    # ── Streaming режим (7 потоков фото + Grok видео) ─────────────────────
    if args.streaming:
        from pipeline_runner import run_pipeline  # noqa: E402
        # Поддержка обоих layouts: photo/photo_prompts.json и photo_prompts.json
        photo_prompts_json = PROMPTS_DIR / session / "photo" / "photo_prompts.json"
        if not photo_prompts_json.exists():
            photo_prompts_json = PROMPTS_DIR / session / "photo_prompts.json"
        video_prompts = read_prompts(session, "video")
        photos_dir = make_output_dir(session, "photos")
        videos_dir = make_output_dir(session, "videos")
        grok_platform = PLATFORMS["2"].copy()
        grok_platform["key"] = "2"
        photos_saved, videos_saved = run_pipeline(
            session=session,
            photo_prompts_json=photo_prompts_json,
            video_prompts=video_prompts,
            photos_dir=photos_dir,
            videos_dir=videos_dir,
            platform=grok_platform,
            api_key=PIXEL_API_KEY,
        )
        print(f"\n✅ Streaming: фото={photos_saved}, видео={videos_saved}")
        return

    # ── Платформа ─────────────────────────────────────────────────────────
    if args.platform:
        platform = PLATFORMS[args.platform].copy()
        platform["key"] = args.platform
        print(f"Платформа: {platform['name']}")
    else:
        platform = ask_platform()

    # ── API ключ для PixelAgent ────────────────────────────────────────────
    api_key = None
    if platform["type"] == "api":
        if not PIXEL_API_URL or not PIXEL_API_KEY:
            print("❌ Заполни PIXEL_API_URL и PIXEL_API_KEY в config/.env")
            return
        api_key = PIXEL_API_KEY

    # ── Тип генерации ─────────────────────────────────────────────────────
    if args.gen_type:
        raw = args.gen_type
        if platform["type"] == "api" and raw == "video":
            print(f"ОШИБКА: {platform['name']} не поддерживает видео")
            return
        media_types = platform["supports"] if raw == "both" else [raw]
    else:
        media_types = ask_media_type(platform)

    # ── Генерация ──────────────────────────────────────────────────────────
    start_time    = time.time()
    total_saved   = 0
    total_prompts = 0

    for media_type in media_types:
        print(f"\n--- {media_type.upper()} ---")

        prompts = read_prompts(session, media_type)
        if not prompts:
            print(f"  Нет промптов для {media_type}, пропускаю.")
            continue

        total_prompts = len(prompts)
        folder  = "photos" if media_type == "photo" else "videos"
        out_dir = make_output_dir(session, folder)
        print(f"  Папка: {out_dir}")

        if platform["type"] == "api":
            # PixelAgent
            saved = generate_pixel(media_type, prompts, out_dir, api_key)

        elif platform.get("key") == "2" and media_type == "video":
            # Grok image-to-video
            photos = read_photos(session)
            if not photos:
                print(f"  [!] Нет фото в data/media/{session}/photos/")
                print("      Сначала сгенерируй фото (PixelAgent → Фото)")
                saved = 0
            else:
                n = min(len(photos), len(prompts))
                if len(photos) != len(prompts):
                    print(f"  [!] Фото: {len(photos)}, промптов: {len(prompts)} — беру первые {n}")
                saved = generate_grok_video(
                    platform, photos[:n], prompts[:n], out_dir,
                    session=session, num_tabs=args.tabs,
                )

        else:
            # Google Flow (браузер)
            saved = generate_flow(platform, media_type, prompts, out_dir, session=session)

        total_saved += saved

    # ── Статистика ────────────────────────────────────────────────────────
    elapsed = time.time() - start_time
    hours, rem = divmod(int(elapsed), 3600)
    minutes, seconds = divmod(rem, 60)
    folder_label = "videos" if "video" in media_types else "photos"

    if hours:
        time_str = f"{hours} ч {minutes} мин {seconds} сек"
    elif minutes:
        time_str = f"{minutes} мин {seconds} сек"
    else:
        time_str = f"{seconds} сек"

    print(f"\n{'='*50}")
    print(f"✅ Готово! {total_saved} {'видео' if 'video' in media_types else 'файлов'} сгенерировано")
    print(f"📁 {MEDIA_DIR / session / folder_label}/")
    print(f"⏱  Время: {time_str}")
    print(f"Сгенерировано: {total_saved}")
    print(f"Всего: {total_prompts}")
    print(f"{'='*50}")


if __name__ == "__main__":
    run()
