"""
Нарезать вторую половину (5s-10s) Grok-видео и добавить в library.json.
Запуск: python agents/library/cut_grok_second_half.py
"""
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "utils"))
from paths import get_library_json, get_clips_dir

GROK_DIR   = Path("C:/Projects/Video-pipeline/library_religion/grok_generated/videos")
CHANNEL    = "channel_003_religion_es"
LIB_JSON   = get_library_json(CHANNEL)
CLIPS_DIR  = get_clips_dir(CHANNEL)
SEG_START  = 5.0
SEG_DUR    = 5.0

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def next_clip_id(lib: dict) -> str:
    existing = set(lib["clips"].keys())
    n = 1
    while f"{n:04d}" in existing:
        n += 1
    return f"{n:04d}"


def get_duration(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=15,
    )
    try:
        return float(r.stdout.strip())
    except (ValueError, TypeError):
        return 0.0


def cut_segment(src: Path, start: float, dur: float, out: Path) -> bool:
    cmd = [
        "ffmpeg", "-y", "-ss", str(start), "-i", str(src),
        "-t", str(dur),
        "-c:v", "h264_nvenc", "-preset", "p4", "-cq", "28",
        "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2",
        "-c:a", "aac", "-b:a", "128k",
        str(out),
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=60)
    if r.returncode != 0:
        # Fallback CPU
        cmd[cmd.index("h264_nvenc")] = "libx264"
        cmd[cmd.index("-preset")] = "-preset"
        cmd[cmd.index("p4")] = "medium"
        cmd.remove("-cq"); cmd.remove("28")
        r = subprocess.run(cmd, capture_output=True, timeout=120)
    return r.returncode == 0


def main():
    with open(LIB_JSON, encoding="utf-8") as f:
        lib = json.load(f)

    videos = sorted(GROK_DIR.glob("video_*.mp4"))
    print(f"Grok videos: {len(videos)}")
    print(f"Clips dir: {CLIPS_DIR}")

    added = 0
    skipped = 0
    errors = 0

    for i, vid in enumerate(videos, 1):
        duration = get_duration(vid)
        if duration < SEG_START + SEG_DUR - 0.05:
            print(f"[{i}/{len(videos)}] SKIP {vid.name}: {duration:.2f}s < {SEG_START+SEG_DUR}s")
            skipped += 1
            continue

        clip_id  = next_clip_id(lib)
        out_path = CLIPS_DIR / f"{clip_id}.mp4"

        ok = cut_segment(vid, SEG_START, SEG_DUR, out_path)
        if not ok:
            print(f"[{i}/{len(videos)}] ERROR {vid.name}")
            errors += 1
            continue

        lib["clips"][clip_id] = {
            "file":          f"{clip_id}.mp4",
            "original_file": f"videos_second/{vid.name}",
            "duration":      SEG_DUR,
            "width":         1920,
            "height":        1080,
            "ratio":         round(1920 / 1080, 3),
            "fps":           25.0,
            "bitrate":       0,
            "has_text":      False,
            "blip_descriptions": [],
            "blip_tags":     "",
            "indexed":       False,
            "source":        "grok_generated_second",
        }
        added += 1
        print(f"[{i}/{len(videos)}] ✅ {clip_id}.mp4 ← {vid.name} [{SEG_START:.0f}s-{SEG_START+SEG_DUR:.0f}s]")

        # Save every 10
        if added % 10 == 0:
            lib["total"] = len(lib["clips"])
            with open(LIB_JSON, "w", encoding="utf-8") as f:
                json.dump(lib, f, indent=2, ensure_ascii=False)

    lib["total"] = len(lib["clips"])
    lib["indexed_at"] = datetime.now(timezone.utc).isoformat()
    with open(LIB_JSON, "w", encoding="utf-8") as f:
        json.dump(lib, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*50}")
    print(f"Добавлено:  {added}")
    print(f"Пропущено:  {skipped}")
    print(f"Ошибок:     {errors}")
    print(f"Total:      {lib['total']}")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
