"""
replace_stocks.py — Заменить указанные видео стоками прямо в upscaled/
Проверяет уникальность каждого стока по ID перед сохранением.

Запуск:
  py agents/video_validator/replace_stocks.py --session Video_20260312_170838 --ids 24 48 59 63 73 79 82 90 92
"""

import argparse
import json
import sys
import hashlib
import shutil
import tempfile
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_DIR = Path(__file__).parent.parent.parent
TRANSCRIPTS = BASE_DIR / "data" / "transcripts"
MEDIA_DIR   = BASE_DIR / "data" / "media"

if str(Path(__file__).parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def load_segments(session):
    p = TRANSCRIPTS / session / "result.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    return data["segments"] if isinstance(data, dict) else data


def file_hash(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def normalize_video(src: Path, dst: Path) -> bool:
    """Нормализовать до 1920x1080, 10s через ffmpeg."""
    cmd = [
        "ffmpeg", "-y", "-i", str(src),
        "-t", "10",
        "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,"
               "pad=1920:1080:(ow-iw)/2:(oh-ih)/2",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-an",
        str(dst)
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=120)
    return r.returncode == 0 and dst.exists() and dst.stat().st_size > 10_000


def replace_one(idx, segment_text, upscaled_dir, existing_hashes, from_stock_finder):
    find_stock_video, download_and_verify = from_stock_finder
    dst = upscaled_dir / f"video_{idx:03d}.mp4"

    print(f"\n  🔍 #{idx:03d}: '{segment_text[:70]}'")

    # Пробуем до 5 раз чтобы найти уникальный сток
    for attempt in range(1, 6):
        stock = find_stock_video(
            segment_text=segment_text,
            prompt_text="",
            niche="cosmos",
            min_duration=10
        )
        if not stock:
            print(f"  ❌ #{idx:03d}: стоки исчерпаны")
            return idx, False

        # Скачиваем во временный файл
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp_path = Path(tmp.name)

        ok = download_and_verify(stock, tmp_path)
        if not ok:
            tmp_path.unlink(missing_ok=True)
            print(f"  ⚠️  #{idx:03d}: скачивание не удалось (попытка {attempt})")
            continue

        # Нормализуем во второй temp
        with tempfile.NamedTemporaryFile(suffix="_norm.mp4", delete=False) as tmp2:
            tmp_norm = Path(tmp2.name)

        norm_ok = normalize_video(tmp_path, tmp_norm)
        tmp_path.unlink(missing_ok=True)

        if not norm_ok:
            tmp_norm.unlink(missing_ok=True)
            print(f"  ⚠️  #{idx:03d}: нормализация не удалась (попытка {attempt})")
            continue

        # Проверяем хеш нормализованного файла
        h = file_hash(tmp_norm)
        if h in existing_hashes:
            tmp_norm.unlink(missing_ok=True)
            print(f"  🔄 #{idx:03d}: дубликат по хешу (ID={stock['id']}), пробую следующий...")
            continue

        # Уникальный — сохраняем
        existing_hashes.add(h)
        shutil.move(str(tmp_norm), str(dst))
        print(f"  ✅ #{idx:03d}: {stock['source']} ID={stock['id']} | '{stock['query']}'")
        return idx, True

    print(f"  ❌ #{idx:03d}: не удалось найти уникальный сток за 5 попыток")
    return idx, False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", required=True)
    ap.add_argument("--ids", nargs="+", type=int, required=True,
                    help="Номера видео для замены (1-based)")
    ap.add_argument("--workers", type=int, default=3)
    args = ap.parse_args()

    session = args.session
    bad_ids = sorted(set(args.ids))

    upscaled_dir = MEDIA_DIR / session / "upscaled"
    if not upscaled_dir.exists():
        print(f"❌ Папка не найдена: {upscaled_dir}")
        sys.exit(1)

    segments = load_segments(session)
    seg_map = {}
    for seg in segments:
        idx = seg.get("id") or seg.get("idx")
        if idx is not None:
            seg_map[int(idx)] = seg.get("text", "")

    # Строим карту хешей существующих upscaled видео
    print("📊 Считаю хеши существующих видео...")
    existing_hashes = set()
    for mp4 in upscaled_dir.glob("video_*.mp4"):
        try:
            existing_hashes.add(file_hash(mp4))
        except Exception:
            pass
    print(f"   {len(existing_hashes)} файлов проиндексировано")

    # Загружаем stock_finder
    from stock_finder import (
        find_stock_video,
        download_and_verify,
        load_used_stocks,
        save_used_stocks,
    )
    load_used_stocks(session)

    from_stock_finder = (find_stock_video, download_and_verify)

    print(f"\n🚀 Заменяю {len(bad_ids)} видео в {upscaled_dir.name}/")
    print(f"   IDs: {bad_ids}\n")

    replaced = 0
    failed = 0

    # Последовательно чтобы lock в find_stock_video работал корректно
    for idx in bad_ids:
        text = seg_map.get(idx, f"cosmos space universe galaxy segment {idx}")
        _, ok = replace_one(idx, text, upscaled_dir, existing_hashes, from_stock_finder)
        if ok:
            replaced += 1
        else:
            failed += 1

    save_used_stocks(session)

    print(f"\n{'='*50}")
    print(f"✅ Заменено: {replaced}")
    print(f"❌ Не удалось: {failed}")
    print(f"📁 {upscaled_dir}")


if __name__ == "__main__":
    main()
