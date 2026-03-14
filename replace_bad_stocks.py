"""
Replace bad videos with stock footage, then re-upscale replaced files.
"""
import json
import sys
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR    = Path(__file__).parent
MEDIA_DIR   = BASE_DIR / "data" / "media"
TRANSCRIPTS = BASE_DIR / "data" / "transcripts"
PROMPTS_DIR = BASE_DIR / "data" / "prompts"

SESSION = "Video_20260311_170905"

# IDs from BLIP report
BAD_IDS = [2, 4, 8, 10, 11, 12, 14, 20, 22, 24, 26, 28, 32, 34, 40, 43,
           47, 48, 49, 51, 53, 55, 57, 59, 65, 67, 69, 71, 73, 75, 79, 85, 87, 91, 94]

sys.path.insert(0, str(BASE_DIR / "agents" / "video_validator"))
sys.path.insert(0, str(BASE_DIR / "agents" / "video_cutter"))

from stock_finder import find_stock_video, download_stock_video

videos_dir   = MEDIA_DIR / SESSION / "videos"
upscaled_dir = MEDIA_DIR / SESSION / "upscaled"

# Load segments
raw = json.loads((TRANSCRIPTS / SESSION / "result.json").read_text(encoding="utf-8"))
segs = raw["segments"] if isinstance(raw, dict) else raw
seg_map = {s["id"]: s for s in segs}

# Load prompts
prompts_data = json.loads(
    (PROMPTS_DIR / SESSION / "video" / "video_prompts.json").read_text(encoding="utf-8")
)
if isinstance(prompts_data, list):
    prompts_map = {item["id"]: item for item in prompts_data}
else:
    prompts_map = {int(k): v for k, v in prompts_data.items()}

print(f"Сессия: {SESSION}")
print(f"Плохих видео: {len(BAD_IDS)}")
print()


def replace_one(vid_id: int) -> tuple[int, bool, str]:
    seg    = seg_map.get(vid_id, {})
    text   = seg.get("text", "")
    prompt = prompts_map.get(vid_id, {}).get("video_prompt", "")
    video_path = videos_dir / f"video_{vid_id:03d}.mp4"

    stock = find_stock_video(text, prompt)
    if not stock:
        return vid_id, False, "сток не найден"

    if download_stock_video(stock, video_path):
        return vid_id, True, stock["source"]
    else:
        return vid_id, False, "ошибка скачивания"


print("=" * 60)
print("  ЗАМЕНА ПЛОХИХ ВИДЕО НА СТОКИ (5 потоков)")
print("=" * 60)

replaced = []
failed   = []

with ThreadPoolExecutor(max_workers=5) as ex:
    futures = {ex.submit(replace_one, vid_id): vid_id for vid_id in BAD_IDS}
    for future in as_completed(futures):
        vid_id, ok, msg = future.result()
        if ok:
            replaced.append(vid_id)
            print(f"  ✅ #{vid_id:03d}: {msg}")
        else:
            failed.append(vid_id)
            print(f"  ❌ #{vid_id:03d}: {msg}")

print()
print(f"Заменено: {len(replaced)}, Не найдено: {len(failed)}")
if failed:
    print(f"Не заменены: {failed}")

# Re-upscale replaced videos
if replaced:
    print()
    print("=" * 60)
    print("  НОРМАЛИЗАЦИЯ И АПСКЕЙЛ ЗАМЕНЁННЫХ ВИДЕО")
    print("=" * 60)
    try:
        from video_cutter import process_all_videos
        # process_all_videos handles all, but we only need replaced ones
        # We'll copy to upscaled manually using process_video
        from video_cutter import process_video
        for vid_id in sorted(replaced):
            src = videos_dir / f"video_{vid_id:03d}.mp4"
            dst = upscaled_dir / f"video_{vid_id:03d}.mp4"
            print(f"  ⏳ #{vid_id:03d} → upscaled/")
            process_video(src, dst)
            print(f"  ✅ #{vid_id:03d} готово")
    except Exception as e:
        print(f"  ⚠️ Ошибка апскейла: {e}")
        print("  Попробуем через subprocess...")
        subprocess.run([
            "py", "agents/video_validator/video_validator.py",
            "--session", SESSION,
        ])

print()
print("ГОТОВО!")
