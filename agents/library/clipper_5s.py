"""
clipper_5s.py — Нарезка всех клипов библиотеки до 5 секунд.

Читает library/library.json, режет каждый normalized клип до 5.0s,
перезаписывает оригинал, обновляет duration в library.json.

Запуск:
  py agents/library/clipper_5s.py
  py agents/library/clipper_5s.py --workers 4
  py agents/library/clipper_5s.py --dry-run
"""

import argparse
import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── Пути ──────────────────────────────────────────────────────────────────────

_BASE_DIR    = Path(__file__).resolve().parent.parent.parent
_LIB_DIR     = _BASE_DIR / "library"
_CLIPS_DIR   = _LIB_DIR / "clips"
_LIB_JSON    = _LIB_DIR / "library.json"

# ── Константы ─────────────────────────────────────────────────────────────────

TARGET_DUR   = 5.0   # целевая длительность
SKIP_THRESH  = 5.1   # пропустить если уже <= этого
SAVE_EVERY   = 50    # сохранять library.json каждые N клипов

# ── Глобальные ────────────────────────────────────────────────────────────────

_json_lock   = threading.Lock()
_nvenc_lock  = threading.Semaphore(3)   # RTX 3060: не больше 3 NVENC одновременно
_print_lock  = threading.Lock()


def _log(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


# ── GPU детектор ──────────────────────────────────────────────────────────────

def check_nvenc() -> bool:
    try:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, timeout=10,
        )
        return b"h264_nvenc" in r.stdout
    except Exception:
        return False


HAS_NVENC = check_nvenc()


# ── Нарезка одного клипа ──────────────────────────────────────────────────────

def clip_to_5s(clip_path: Path, dry_run: bool) -> tuple[str, str]:
    """
    Порезать clip_path до TARGET_DUR секунд.
    Возвращает ("ok" | "skip" | "error", детали).
    """
    if not clip_path.exists():
        return "error", f"файл не найден: {clip_path.name}"

    # Текущая длительность через ffprobe
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", str(clip_path)],
            capture_output=True, timeout=15,
        )
        dur = float(json.loads(r.stdout)["format"]["duration"])
    except Exception:
        return "error", f"ffprobe fail: {clip_path.name}"

    if dur <= SKIP_THRESH:
        return "skip", f"{dur:.2f}s уже <= {SKIP_THRESH}s"

    if dry_run:
        return "ok", f"[dry-run] {dur:.2f}s → {TARGET_DUR}s"

    tmp_path = clip_path.parent / f"tmp_{clip_path.name}"

    # Попытка NVENC
    if HAS_NVENC:
        with _nvenc_lock:
            result = subprocess.run([
                "ffmpeg", "-y", "-i", str(clip_path),
                "-t", str(TARGET_DUR),
                "-c:v", "h264_nvenc", "-preset", "p4", "-cq", "19",
                "-b:v", "8M", "-maxrate", "10M", "-bufsize", "20M",
                "-pix_fmt", "yuv420p", "-an",
                "-loglevel", "warning",
                str(tmp_path),
            ], capture_output=True, timeout=60)
        nvenc_ok = result.returncode == 0 and tmp_path.exists() and tmp_path.stat().st_size > 1000
    else:
        nvenc_ok = False

    # CPU fallback
    if not nvenc_ok:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        result = subprocess.run([
            "ffmpeg", "-y", "-i", str(clip_path),
            "-t", str(TARGET_DUR),
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-pix_fmt", "yuv420p", "-an",
            "-loglevel", "warning",
            str(tmp_path),
        ], capture_output=True, timeout=120)

    if result.returncode != 0 or not tmp_path.exists() or tmp_path.stat().st_size < 1000:
        tmp_path.unlink(missing_ok=True)
        return "error", f"encode fail: {clip_path.name}"

    # Атомарная замена: tmp → оригинал
    for attempt in range(5):
        try:
            tmp_path.replace(clip_path)
            break
        except PermissionError:
            time.sleep(0.5)
    else:
        tmp_path.unlink(missing_ok=True)
        return "error", f"move locked: {clip_path.name}"

    return "ok", f"{dur:.2f}s → {TARGET_DUR}s"


# ── Воркер ────────────────────────────────────────────────────────────────────

def _worker(args: tuple) -> tuple[str, str, str]:
    """Возвращает (clip_id, status, detail)."""
    clip_id, entry, dry_run = args
    clip_path = _CLIPS_DIR / entry["file"]
    status, detail = clip_to_5s(clip_path, dry_run)
    return clip_id, status, detail


# ── Главная функция ───────────────────────────────────────────────────────────

def run(workers: int = 4, dry_run: bool = False) -> None:
    if not _LIB_JSON.exists():
        print(f"❌ library.json не найден: {_LIB_JSON}")
        sys.exit(1)

    with open(_LIB_JSON, encoding="utf-8") as f:
        library = json.load(f)

    clips = library.get("clips", {})
    total = len(clips)

    # Отобрать кандидатов
    to_process = [
        (clip_id, entry)
        for clip_id, entry in clips.items()
        if entry.get("normalized", False)
        and not entry.get("rejected", False)
        and entry.get("file")
    ]

    print(f"""
{'='*54}
✂  CLIPPER 5S — НАРЕЗКА БИБЛИОТЕКИ
  Всего клипов в БД:  {total}
  К обработке:        {len(to_process)}
  Целевая длина:      {TARGET_DUR}s
  Потоков:            {workers}
  NVENC:              {'да' if HAS_NVENC else 'нет (CPU fallback)'}
  Режим:              {'DRY-RUN (без изменений)' if dry_run else 'ЗАПИСЬ'}
{'='*54}
""")

    if not to_process:
        print("Нечего обрабатывать.")
        return

    stats   = {"ok": 0, "skip": 0, "error": 0}
    done    = 0
    tasks   = [(clip_id, entry, dry_run) for clip_id, entry in to_process]

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_worker, t): t[0] for t in tasks}

        for future in as_completed(futures):
            clip_id, status, detail = future.result()
            stats[status] = stats.get(status, 0) + 1
            done += 1

            # Обновить duration в памяти
            if status == "ok" and not dry_run:
                with _json_lock:
                    library["clips"][clip_id]["duration"] = TARGET_DUR

            # Лог только ok/error (skip молча)
            if status == "ok":
                _log(f"  ✅ [{done}/{len(to_process)}] {clip_id}: {detail}")
            elif status == "error":
                _log(f"  ❌ [{done}/{len(to_process)}] {clip_id}: {detail}")

            # Сохранение каждые SAVE_EVERY клипов
            if done % SAVE_EVERY == 0 and not dry_run:
                with _json_lock:
                    _save_library(library)
                _log(f"\n💾 Сохранено ({done}/{len(to_process)})\n")

    # Финальное сохранение
    if not dry_run:
        _save_library(library)

    print(f"""
{'='*54}
📊 ГОТОВО
  ✅ Нарезано:               {stats.get('ok', 0)}
  ⏭  Пропущено (уже <= {SKIP_THRESH}s): {stats.get('skip', 0)}
  ❌ Ошибок:                 {stats.get('error', 0)}
  💾 library.json {'обновлён' if not dry_run else 'не изменён (dry-run)'}
{'='*54}
""")


def _save_library(library: dict) -> None:
    tmp = _LIB_JSON.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(library, f, ensure_ascii=False, indent=2)
    tmp.replace(_LIB_JSON)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Нарезка клипов библиотеки до 5 секунд"
    )
    parser.add_argument(
        "--workers", type=int, default=4,
        help="Количество параллельных потоков (default: 4)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Показать что будет нарезано, не трогать файлы"
    )
    args = parser.parse_args()
    run(workers=args.workers, dry_run=args.dry_run)
