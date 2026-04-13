"""
Renumber clips in library_cosmos after manual cleanup.
Updates: MP4 files, thumbnails, library.json, usage_history_*.json
"""
import json
import shutil
from pathlib import Path
from datetime import datetime

LIB = Path("D:/Video-pipeline-library/library_cosmos")
CLIPS_DIR   = LIB / "clips"
THUMBS_DIR  = LIB / "thumbnails"
LIB_JSON    = LIB / "library.json"

def backup(path: Path):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = path.with_suffix(f".bak.{ts}")
    shutil.copy2(path, dst)
    print(f"  backup → {dst.name}")

def main():
    # ── 1. Получаем отсортированный список существующих MP4 ────────────────
    clips = sorted(CLIPS_DIR.glob("*.mp4"), key=lambda p: int(p.stem))
    total = len(clips)
    print(f"Found {total} clips (last number: {clips[-1].stem})")

    # Маппинг: старый номер (str, без ведущих нулей) → новый номер (str 4-digit)
    old_to_new: dict[str, str] = {}
    for new_idx, path in enumerate(clips, start=1):
        old = path.stem          # e.g. "0436"
        new = f"{new_idx:04d}"
        old_to_new[old] = new

    # Считаем сколько реально нужно переименовать
    to_rename = {o: n for o, n in old_to_new.items() if o != n}
    print(f"Need to rename: {len(to_rename)} files ({total - len(to_rename)} already correct)")

    if not to_rename:
        print("Nothing to do.")
        return

    # ── 2. Переименовываем MP4 (один проход, порядок исключает конфликты) ──
    print("\nRenaming clips...")
    for old, new in sorted(to_rename.items(), key=lambda x: int(x[0])):
        src = CLIPS_DIR / f"{old}.mp4"
        dst = CLIPS_DIR / f"{new}.mp4"
        src.rename(dst)
    print(f"  done ({len(to_rename)} renamed)")

    # ── 3. Переименовываем thumbnails ──────────────────────────────────────
    print("\nRenaming thumbnails...")
    thumb_renamed = 0
    # Удаляем thumbnails для которых нет соответствующего MP4 (orphans)
    valid_old_stems = set(old_to_new.keys())
    deleted = 0
    for thumb in list(THUMBS_DIR.glob("*.jpg")):
        if thumb.stem not in valid_old_stems:
            thumb.unlink()
            deleted += 1
    if deleted:
        print(f"  deleted {deleted} orphan thumbnails")

    for old, new in sorted(to_rename.items(), key=lambda x: int(x[0])):
        src = THUMBS_DIR / f"{old}.jpg"
        dst = THUMBS_DIR / f"{new}.jpg"
        if src.exists():
            # replace() перезаписывает если dst существует (в отличие от rename())
            src.replace(dst)
            thumb_renamed += 1
    print(f"  done ({thumb_renamed} renamed)")

    # ── 4. Обновляем library.json ──────────────────────────────────────────
    print("\nUpdating library.json...")
    backup(LIB_JSON)

    with open(LIB_JSON, encoding="utf-8") as f:
        lib = json.load(f)

    old_clips = lib["clips"]
    new_clips  = {}
    missing = 0

    for new_idx, path in enumerate(sorted(CLIPS_DIR.glob("*.mp4"),
                                          key=lambda p: int(p.stem)), start=1):
        new_key = f"{new_idx:04d}"
        # Находим соответствующий старый ключ
        old_key = next((o for o, n in old_to_new.items() if n == new_key), new_key)

        if old_key in old_clips:
            entry = old_clips[old_key].copy()
            entry["file"] = f"{new_key}.mp4"
            new_clips[new_key] = entry
        else:
            missing += 1

    lib["clips"] = new_clips
    lib["total"] = len(new_clips)

    with open(LIB_JSON, "w", encoding="utf-8") as f:
        json.dump(lib, f, ensure_ascii=False)

    print(f"  done ({len(new_clips)} entries, {missing} missing entries skipped)")

    # ── 5. Обновляем usage_history_*.json ─────────────────────────────────
    for uh_path in LIB.glob("usage_history_*.json"):
        if "BACKUP" in uh_path.name:
            continue
        print(f"\nUpdating {uh_path.name}...")
        backup(uh_path)

        with open(uh_path, encoding="utf-8") as f:
            uh = json.load(f)

        # Ремапим clip_usage ключи
        if "clip_usage" in uh and isinstance(uh["clip_usage"], dict):
            new_usage = {}
            for old_key, val in uh["clip_usage"].items():
                new_key = old_to_new.get(old_key)
                if new_key:
                    new_usage[new_key] = val
                # else: клип удалён, выбрасываем
            print(f"  clip_usage: {len(uh['clip_usage'])} → {len(new_usage)}")
            uh["clip_usage"] = new_usage

        with open(uh_path, "w", encoding="utf-8") as f:
            json.dump(uh, f, ensure_ascii=False)
        print(f"  done")

    # ── Итог ───────────────────────────────────────────────────────────────
    print(f"\n{'─'*50}")
    print(f"Renumbering complete: {total} clips → 0001 … {total:04d}")
    print(f"library.json: {len(new_clips)} entries")

if __name__ == "__main__":
    main()
