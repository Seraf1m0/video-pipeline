"""
Targeted replacement for video_011 and video_038.
Round 2 — exclude all known-bad Pixabay IDs.
"""
import os
import sys
import json
import subprocess
import requests
import shutil
from pathlib import Path
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding='utf-8')

load_dotenv(Path("config/.env"))
PIXABAY_KEY = os.getenv("PIXABAY_API_KEY", "")
PEXELS_KEY  = os.getenv("PEXELS_API_KEY", "")

UPSCALED = Path("data/media/Video_20260313_204235/upscaled")
TEMP     = Path("temp")

# All known-bad or already-used IDs
GLOBAL_EXCLUDE = {
    "pixabay_10",       # used by #085
    "pixabay_10715",    # ocean/clouds (got for #011)
    "pixabay_7350",     # vinyl record player (was #038)
    "pixabay_214946",   # building interior (got for #038)
}

print(f"Global exclude: {GLOBAL_EXCLUDE}")


def search_pixabay(query: str, exclude: set, min_duration=10) -> dict | None:
    for page in range(1, 10):
        params = {
            "key":        PIXABAY_KEY,
            "q":          query,
            "video_type": "film",
            "per_page":   20,
            "page":       page,
            "min_width":  1280,
        }
        try:
            r = requests.get("https://pixabay.com/api/videos/", params=params, timeout=15)
            hits = r.json().get("hits", [])
            if not hits:
                print(f"    (no more results on page {page})")
                break
            for v in hits:
                vid_id = f"pixabay_{v['id']}"
                dur = v.get("duration", 0)
                if vid_id in exclude:
                    continue
                if dur < min_duration:
                    continue
                for q in ["large", "medium", "small"]:
                    url = v.get("videos", {}).get(q, {}).get("url", "")
                    if url:
                        print(f"    FOUND {vid_id} dur={dur}s")
                        return {"id": vid_id, "url": url, "source": "pixabay"}
        except Exception as e:
            print(f"    Pixabay p{page} error: {e}")
            break
    return None


def search_pexels(query: str, exclude: set, min_duration=10) -> dict | None:
    if not PEXELS_KEY:
        return None
    headers = {"Authorization": PEXELS_KEY}
    for page in range(1, 8):
        params = {
            "query":        query,
            "per_page":     20,
            "page":         page,
            "min_duration": min_duration,
            "orientation":  "landscape",
        }
        try:
            r = requests.get(
                "https://api.pexels.com/videos/search",
                headers=headers, params=params, timeout=15
            )
            videos = r.json().get("videos", [])
            if not videos:
                break
            for v in videos:
                vid_id = f"pexels_{v['id']}"
                if vid_id in exclude:
                    continue
                files = [f for f in v.get("video_files", []) if f.get("width", 0) >= 1280]
                if not files:
                    continue
                best = max(files, key=lambda x: x.get("width", 0))
                print(f"    FOUND {vid_id}")
                return {"id": vid_id, "url": best["link"], "source": "pexels"}
        except Exception as e:
            print(f"    Pexels p{page} error: {e}")
            break
    return None


def download_video(url: str, dest: Path) -> bool:
    try:
        r = requests.get(url, stream=True, timeout=90)
        r.raise_for_status()
        tmp = dest.with_suffix(".tmp.mp4")
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 512):
                f.write(chunk)
        tmp.rename(dest)
        print(f"    Saved {dest.name} ({dest.stat().st_size // 1024}KB)")
        return True
    except Exception as e:
        print(f"    Download failed: {e}")
        return False


def extract_frame(video_path: Path, out_name: str, pct=0.5) -> Path | None:
    out = TEMP / out_name
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(video_path)],
            capture_output=True, text=True
        )
        dur = float(json.loads(result.stdout)["format"]["duration"])
        ts  = max(0.5, dur * pct)
        subprocess.run([
            "ffmpeg", "-y", "-ss", str(ts), "-i", str(video_path),
            "-vframes", "1", "-q:v", "2", "-loglevel", "quiet", str(out)
        ], capture_output=True)
        if out.exists():
            print(f"    Frame: {out.name}")
            return out
    except Exception as e:
        print(f"    Frame extract error: {e}")
    return None


def fix_video(seg_num: int, queries: list[str], extra_exclude: set = None):
    target = UPSCALED / f"video_{seg_num:03d}.mp4"
    if not target.exists():
        print(f"  ERROR: {target.name} not found!")
        return

    exclude = GLOBAL_EXCLUDE.copy()
    if extra_exclude:
        exclude |= extra_exclude

    print(f"\n{'='*60}")
    print(f"  Fixing #{seg_num:03d}")

    result = None
    for q in queries:
        print(f"  Query: '{q}'")
        # Try Pixabay first
        result = search_pixabay(q, exclude)
        if result:
            break
        # Then Pexels
        result = search_pexels(q, exclude)
        if result:
            break

    if not result:
        print(f"  FAILED: No stock found for #{seg_num:03d}")
        return

    tmp_path = TEMP / f"fix_{seg_num:03d}.mp4"
    if not download_video(result["url"], tmp_path):
        return

    backup = target.with_suffix(".bak.mp4")
    shutil.copy2(target, backup)
    shutil.move(tmp_path, target)
    backup.unlink(missing_ok=True)

    GLOBAL_EXCLUDE.add(result["id"])
    print(f"  DONE: #{seg_num:03d} => {result['id']} (source={result['source']})")
    extract_frame(target, f"chk_new_{seg_num:03d}.jpg")


# ── Fix #011 — need nebula/galaxy, NOT ocean/clouds ──────────────────────────
# Try Pexels first (Pixabay keeps giving wrong results for these queries)
fix_video(11, [
    "galaxy nebula cosmos animation",
    "colorful nebula space stars",
    "deep space galaxy animation",
    "interstellar nebula cosmos",
    "space cosmos galaxy stars timelapse",
    "nebula colorful space",
    "cosmic nebula animation",
])

# ── Fix #038 — need black hole / space, NOT building ─────────────────────────
fix_video(38, [
    "space universe cosmos animation",
    "galaxy rotating cosmos deep space",
    "cosmos stars universe dark",
    "outer space universe timelapse",
    "space cosmos stars animation",
    "milky way stars cosmos timelapse",
])

print("\n\nCheck frames in temp/:")
for f in sorted(TEMP.glob("chk_new_*.jpg")):
    print(f"  {f.name}")
