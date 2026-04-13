"""
build_thumbnails.py — извлечь по одному среднему кадру из каждого клипа библиотеки.

Сохраняет: {library_dir}/thumbnails/{clip_id}.jpg  (224x224)
Параллельно: 8 ffmpeg воркеров.

Запуск:
    py tools/build_thumbnails.py --channel channel_001_cosmos_de
    py tools/build_thumbnails.py --channel channel_001_cosmos_de --rebuild
"""
import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_utils_dir = Path(__file__).resolve().parent.parent / "agents" / "utils"
if str(_utils_dir) not in sys.path:
    sys.path.insert(0, str(_utils_dir))
from paths import get_library_json, get_clips_dir, get_library_dir

FFMPEG_WORKERS   = 8
THUMB_SIZE       = "224:224"
THUMB_FILENAME   = "thumbnails"


def _get_duration(clip_path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(clip_path)],
        capture_output=True, text=True,
    )
    try:
        return float(r.stdout.strip())
    except (ValueError, AttributeError):
        return 5.0


def _extract_thumbnail(clip_path: Path, out_path: Path) -> bool:
    """Извлечь средний кадр (50% длительности) → JPEG."""
    dur = _get_duration(clip_path)
    t   = dur * 0.5

    r = subprocess.run(
        ["ffmpeg", "-y", "-ss", f"{t:.3f}", "-i", str(clip_path),
         "-vframes", "1", "-vf", f"scale={THUMB_SIZE}",
         "-q:v", "4", "-loglevel", "quiet", str(out_path)],
        capture_output=True,
    )
    return r.returncode == 0 and out_path.exists()


def build_thumbnails(channel_id: str, rebuild: bool = False):
    lib_json  = get_library_json(channel_id)
    clips_dir = get_clips_dir(channel_id)
    lib_dir   = get_library_dir(channel_id)
    thumb_dir = lib_dir / THUMB_FILENAME
    thumb_dir.mkdir(exist_ok=True)

    with open(lib_json, encoding="utf-8") as f:
        library = json.load(f)

    valid_clips = [
        clip_id
        for clip_id, entry in library["clips"].items()
        if entry.get("indexed") and not entry.get("rejected")
    ]
    print(f"🎞  Клипов: {len(valid_clips)}", flush=True)

    # Resume — пропустить уже готовые
    if rebuild:
        todo = valid_clips
    else:
        todo = [
            cid for cid in valid_clips
            if not (thumb_dir / f"{cid}.jpg").exists()
        ]

    already = len(valid_clips) - len(todo)
    print(f"  ↩️  Уже готово: {already}", flush=True)
    print(f"  Осталось:     {len(todo)}", flush=True)

    if not todo:
        print("✅ Все thumbnails уже есть!", flush=True)
        return

    ok = fail = 0

    def _process(cid):
        p   = clips_dir / f"{cid}.mp4"
        out = thumb_dir / f"{cid}.jpg"
        if not p.exists():
            return cid, False
        return cid, _extract_thumbnail(p, out)

    with ThreadPoolExecutor(max_workers=FFMPEG_WORKERS) as ex:
        futures = {ex.submit(_process, cid): cid for cid in todo}
        for i, fut in enumerate(as_completed(futures), 1):
            cid, success = fut.result()
            if success:
                ok += 1
            else:
                fail += 1
            if i % 100 == 0 or i == len(todo):
                print(f"  [{i}/{len(todo)}] ✓{ok}  ✗{fail}", flush=True)

    print(f"\n✅ Thumbnails готовы → {thumb_dir}", flush=True)
    print(f"   Успешно: {ok}  |  Ошибок: {fail}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", default="channel_001_cosmos_de")
    parser.add_argument("--rebuild", action="store_true",
                        help="Перегенерировать все, даже если уже есть")
    args = parser.parse_args()
    build_thumbnails(args.channel, rebuild=args.rebuild)
