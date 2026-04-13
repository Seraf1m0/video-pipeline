"""
renumber_recovery.py — восстановление после частичного переименования.
Собирает все .mp4 (в любом формате имени), нумерует 0001..N.
Использует rename с таймаутом для обхода заблокированных файлов.
"""
import sys
import re
import shutil
import threading
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CLIPS_DIR = Path("D:/Video-pipeline-library/library_cosmos/clips")
RENAME_TIMEOUT = 3.0  # секунд до считать файл заблокированным


def sort_key(p: Path) -> tuple:
    s = p.stem
    if s.startswith("__tmp_"):
        m = re.search(r"__tmp_(\d+)", s)
        return (1, 0, int(m.group(1)) if m else 0)
    elif s.startswith("pool3_"):
        m = re.search(r"pool3_(\d+)", s)
        return (2, 0, int(m.group(1)) if m else 0)
    elif s.startswith("recovery_tmp_"):
        m = re.search(r"recovery_tmp_(\d+)", s)
        return (0, -1, int(m.group(1)) if m else 0)
    else:
        m = re.match(r"(\d+)$", s)
        return (0, 0, int(m.group(1)) if m else 0)


def rename_timeout(src: Path, dst: Path, timeout: float = RENAME_TIMEOUT):
    """Rename src->dst with timeout. Returns (success, error_msg)."""
    result = {"ok": False, "err": None}

    def do_rename():
        try:
            src.rename(dst)
            result["ok"] = True
        except Exception as e:
            result["err"] = str(e)

    t = threading.Thread(target=do_rename, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return False, f"timeout after {timeout}s (file locked)"
    if result["err"]:
        return False, result["err"]
    return True, None


def copy_fallback(src: Path, dst: Path):
    """Copy src to dst as fallback when rename fails."""
    shutil.copy2(src, dst)


def main():
    all_mp4 = sorted(CLIPS_DIR.glob("*.mp4"), key=sort_key)
    total = len(all_mp4)
    print(f"Найдено .mp4: {total}", flush=True)
    print("Первые 5:", [f.name for f in all_mp4[:5]], flush=True)
    print("Последние 5:", [f.name for f in all_mp4[-5:]], flush=True)

    # Фаза 1: → recovery_tmp_XXXX.mp4
    print(f"\nФаза 1: переименование в recovery_tmp...", flush=True)
    tmp_files = []
    locked_files = []

    for i, f in enumerate(all_mp4, 1):
        tmp = CLIPS_DIR / f"recovery_tmp_{i:04d}.mp4"
        if f.name == tmp.name:
            tmp_files.append(tmp)
            if i % 1000 == 0:
                print(f"  [{i}/{total}]", flush=True)
            continue

        ok, err = rename_timeout(f, tmp)
        if ok:
            tmp_files.append(tmp)
        else:
            # Rename failed/timed out — try copy
            print(f"  rename failed ({err}): {f.name} — пробую copy...", flush=True)
            try:
                copy_fallback(f, tmp)
                tmp_files.append(tmp)
                locked_files.append(f.name)
                print(f"  COPY ok: {f.name} -> {tmp.name}", flush=True)
            except Exception as e2:
                print(f"  SKIP (copy failed): {f.name}: {e2}", flush=True)

        if i % 1000 == 0:
            print(f"  [{i}/{total}]", flush=True)

    print(f"  Фаза 1 готова: {len(tmp_files)} файлов", flush=True)
    if locked_files:
        print(f"  Скопировано (были заблокированы): {locked_files}", flush=True)

    # Фаза 2: recovery_tmp_XXXX → XXXX.mp4
    print(f"\nФаза 2: финальная нумерация...", flush=True)
    for i, tmp in enumerate(tmp_files, 1):
        final = CLIPS_DIR / f"{i:04d}.mp4"
        ok, err = rename_timeout(tmp, final)
        if not ok:
            print(f"  WARNING phase2 rename failed: {tmp.name}: {err}", flush=True)
        if i % 1000 == 0:
            print(f"  [{i}/{total}]", flush=True)
    print(f"  Фаза 2 готова", flush=True)

    # Проверка
    result = sorted(CLIPS_DIR.glob("*.mp4"))
    numbered = [f for f in result if re.match(r"^\d{4}\.mp4$", f.name)]
    leftover = [f.name for f in result if not re.match(r"^\d{4}\.mp4$", f.name)]
    print(f"\n✅ Итого: {len(result)} файлов, пронумеровано: {len(numbered)}", flush=True)
    if numbered:
        print(f"  Первый: {numbered[0].name}, Последний: {numbered[-1].name}", flush=True)
    if leftover:
        print(f"  Остатки (не переименованы): {leftover}", flush=True)


if __name__ == "__main__":
    main()
