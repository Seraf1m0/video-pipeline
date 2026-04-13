"""
renumber_clips.py — перенумерует все клипы в clips/ в сквозной порядок 0001.mp4, 0002.mp4...
Сначала оригинальные клипы (по номеру), потом pool3 (по номеру).
"""
import os
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CLIPS_DIR = Path("D:/Video-pipeline-library/library_cosmos/clips")


def main():
    all_files = sorted(CLIPS_DIR.glob("*.mp4"))

    # Разделяем на обычные и pool3
    regular = []
    pool3   = []
    for f in all_files:
        if f.stem.startswith("pool3_"):
            m = re.match(r"pool3_(\d+)", f.stem)
            pool3.append((int(m.group(1)) if m else 999999, f))
        else:
            m = re.match(r"(\d+)", f.stem)
            regular.append((int(m.group(1)) if m else 999999, f))

    regular.sort(key=lambda x: x[0])
    pool3.sort(key=lambda x: x[0])

    ordered = [f for _, f in regular] + [f for _, f in pool3]
    total = len(ordered)
    print(f"Всего клипов: {total} ({len(regular)} обычных + {len(pool3)} pool3)", flush=True)

    # Сначала переименуем во временные имена чтобы избежать коллизий
    tmp_names = []
    for i, f in enumerate(ordered, 1):
        tmp = CLIPS_DIR / f"__tmp_{i:04d}.mp4"
        f.rename(tmp)
        tmp_names.append(tmp)

    # Потом в финальные имена
    renamed = 0
    for i, tmp in enumerate(tmp_names, 1):
        final = CLIPS_DIR / f"{i:04d}.mp4"
        tmp.rename(final)
        renamed += 1
        if renamed % 500 == 0 or renamed == total:
            print(f"  [{renamed}/{total}] переименовано...", flush=True)

    print(f"\n✅ Готово: {renamed} клипов пронумеровано 0001–{renamed:04d}", flush=True)


if __name__ == "__main__":
    main()
