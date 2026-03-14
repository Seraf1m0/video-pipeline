"""
Upscale video files from 720p to 1080p using ffmpeg (lanczos, H.264).
Skips files already at 1920x1080.
Processes in parallel (default 4 workers).
"""

import subprocess
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed


FFMPEG = r"C:\Users\Serafim\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.0.1-full_build\bin\ffmpeg.exe"
TARGET_W, TARGET_H = 1920, 1080
WORKERS = 4


def get_resolution(path: Path):
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True
    )
    out = result.stdout.strip()
    if not out:
        return None, None
    parts = out.split(",")
    return int(parts[0]), int(parts[1])


def upscale(path: Path) -> str:
    w, h = get_resolution(path)
    if w is None:
        return f"SKIP (probe failed): {path.name}"
    if w == TARGET_W and h == TARGET_H:
        return f"SKIP (already 1080p): {path.name}"

    tmp = path.with_suffix(".tmp.mp4")
    cmd = [
        FFMPEG, "-y", "-i", str(path),
        "-vf", f"scale={TARGET_W}:{TARGET_H}:flags=lanczos",
        "-c:v", "libx264", "-crf", "18", "-preset", "medium",
        "-c:a", "copy",
        str(tmp)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        if tmp.exists():
            tmp.unlink()
        return f"ERROR: {path.name}\n{result.stderr[-300:]}"

    path.unlink()
    tmp.rename(path)
    return f"OK: {path.name} ({w}x{h} → {TARGET_W}x{TARGET_H})"


def main():
    if len(sys.argv) < 2:
        print("Usage: python upscale_videos.py <videos_folder>")
        sys.exit(1)

    folder = Path(sys.argv[1])
    videos = sorted(folder.glob("video_*.mp4"))
    print(f"Found {len(videos)} video files to process with {WORKERS} workers\n")

    done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futures = {ex.submit(upscale, v): v for v in videos}
        for fut in as_completed(futures):
            done += 1
            print(f"[{done}/{len(videos)}] {fut.result()}")

    print("\nDone.")


if __name__ == "__main__":
    main()
