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
    args = parser.parse_args()

    # ── Сессия ────────────────────────────────────────────────────────────
    session = args.session or find_latest_session()
    if not session:
        print("Нет сессий в data/prompts/")
        return
    print(f"Сессия: {session}")

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
