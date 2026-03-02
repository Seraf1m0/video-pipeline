"""
Validator Agent
---------------
4 проверки пайплайна по очереди:
  1. Транскрипция  — порядок сегментов, покрытие MP3
  2. Фото промпты  — наличие и корректность photo_prompts.json
  3. Видео промпты — наличие и корректность video_prompts.json (если есть)
  4. Медиафайлы   — наличие photo_*.png и video_*.mp4 (если есть)

Запуск: py agents/validator.py [--project Video_YYYYMMDD_HHMMSS] [--fix]
"""

import argparse
import re
import io
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ffmpeg/ffprobe в PATH (winget-установка)
_FFMPEG_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / (
    "Microsoft/WinGet/Packages/"
    "Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe/"
    "ffmpeg-8.0.1-full_build/bin"
)
if _FFMPEG_DIR.exists():
    os.environ["PATH"] = str(_FFMPEG_DIR) + os.pathsep + os.environ.get("PATH", "")

BASE_DIR        = Path(__file__).parent.parent
INPUT_DIR       = BASE_DIR / "data" / "input"
TRANSCRIPTS_DIR = BASE_DIR / "data" / "transcripts"
PROMPTS_DIR     = BASE_DIR / "data" / "prompts"
MEDIA_DIR       = BASE_DIR / "data" / "media"

GAP_TOLERANCE      = 0.05   # секунды — допуск разрыва между сегментами
DURATION_TOLERANCE = 1.0    # секунды — допуск разницы длины MP3 и последнего end


# ---------------------------------------------------------------------------
# Утилиты
# ---------------------------------------------------------------------------

_SESSION_RE = re.compile(r"^Video_\d{8}_\d{6}$")


def find_latest_session() -> str | None:
    """Имя последней сессии по data/input/Video_YYYYMMDD_HHMMSS/."""
    if not INPUT_DIR.exists():
        return None
    folders = [
        d for d in INPUT_DIR.iterdir()
        if d.is_dir() and _SESSION_RE.match(d.name)
    ]
    if not folders:
        return None
    return max(folders, key=lambda f: f.name).name


def find_mp3(session: str) -> Path | None:
    """MP3 или WAV в data/input/<session>/."""
    d = INPUT_DIR / session
    if not d.exists():
        return None
    for ext in ("*.mp3", "*.wav"):
        files = list(d.glob(ext))
        if files:
            return files[0]
    return None


def get_mp3_duration(mp3_path: Path) -> float | None:
    """Длина аудиофайла через ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                str(mp3_path),
            ],
            capture_output=True, text=True, check=True,
        )
        info = json.loads(result.stdout)
        return float(info["format"]["duration"])
    except Exception:
        return None


def load_segments(session: str) -> list[dict] | None:
    path = TRANSCRIPTS_DIR / session / "result.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_json_file(path: Path) -> list[dict] | None:
    """Загружает JSON; None если файла нет."""
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def is_english(text: str) -> bool:
    """Эвристика: >70% ASCII-букв → английский язык."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    ascii_count = sum(1 for c in letters if ord(c) < 128)
    return ascii_count / len(letters) > 0.70


def is_valid_image(path: Path) -> bool:
    """Проверяет валидность изображения через PIL (если установлен)."""
    try:
        from PIL import Image
        with Image.open(path) as img:
            img.verify()
        return True
    except ImportError:
        # PIL не установлен — доверяем размеру файла
        return path.stat().st_size > 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# ПРОВЕРКА 1 — Транскрипция
# ---------------------------------------------------------------------------

def check_transcription(session: str) -> dict:
    mp3_path     = find_mp3(session)
    mp3_duration = get_mp3_duration(mp3_path) if mp3_path else None
    segments     = load_segments(session)

    if segments is None:
        return {
            "status": "error",
            "segments": 0,
            "coverage": None,
            "errors": ["result.json не найден"],
            "message": "❌ Транскрипция: result.json не найден",
        }

    errors = []
    required_fields = {"id", "start", "end", "text"}

    # Обязательные поля
    for seg in segments:
        missing = required_fields - seg.keys()
        if missing:
            errors.append(f"Сегмент #{seg.get('id','?')}: отсутствуют поля {missing}")

    # Строгий порядок id (1, 2, 3, ...)
    for i, seg in enumerate(segments):
        if seg.get("id") != i + 1:
            errors.append(
                f"Нарушен порядок id: ожидался {i+1}, получен {seg.get('id','?')}"
            )

    # Первый сегмент с нуля
    if segments and segments[0].get("start", -1) != 0.0:
        errors.append(
            f"Первый сегмент начинается не с 0.0, а с {segments[0]['start']}"
        )

    # end[i] == start[i+1]
    for i in range(len(segments) - 1):
        cur = segments[i]
        nxt = segments[i + 1]
        gap = abs(nxt.get("start", 0) - cur.get("end", 0))
        if gap > GAP_TOLERANCE:
            errors.append(
                f"Пропуск между сегментами #{cur['id']} и #{nxt['id']}: "
                f"end={cur['end']}s → start={nxt['start']}s (разрыв {gap:.3f}s)"
            )

    # start < end
    for seg in segments:
        if "start" in seg and "end" in seg:
            if seg["start"] >= seg["end"]:
                errors.append(
                    f"Сегмент #{seg['id']}: start({seg['start']}) >= end({seg['end']})"
                )

    # Пустой текст
    for seg in segments:
        if not str(seg.get("text", "")).strip():
            errors.append(f"Сегмент #{seg['id']}: пустой текст")

    # Покрытие MP3
    coverage = None
    if mp3_duration and segments:
        last_end = segments[-1]["end"]
        diff     = abs(last_end - mp3_duration)
        coverage = round(last_end / mp3_duration * 100, 1)
        if diff > DURATION_TOLERANCE:
            errors.append(
                f"Покрытие: сегменты до {last_end:.2f}s, MP3 = {mp3_duration:.2f}s "
                f"(разница {diff:.2f}s)"
            )

    n       = len(segments)
    cov_str = f", покрытие {coverage}%" if coverage is not None else ""

    if errors:
        return {
            "status": "error",
            "segments": n,
            "coverage": coverage,
            "errors": errors,
            "message": f"❌ Транскрипция: {errors[0]}",
        }
    return {
        "status": "ok",
        "segments": n,
        "coverage": coverage,
        "errors": [],
        "message": f"✅ Транскрипция: {n} сегментов{cov_str}",
    }


# ---------------------------------------------------------------------------
# ПРОВЕРКА 2 — Фото промпты
# ---------------------------------------------------------------------------

def check_photo_prompts(session: str) -> dict:
    segments = load_segments(session)
    if segments is None:
        return {
            "status": "error",
            "count": 0,
            "errors": ["result.json не найден"],
            "message": "❌ Фото промпты: result.json не найден",
        }

    path = PROMPTS_DIR / session / "photo_prompts.json"
    data = load_json_file(path)

    if data is None:
        return {
            "status": "error",
            "count": 0,
            "errors": ["photo_prompts.json не найден"],
            "message": "❌ Фото промпты: файл не найден",
        }

    n_seg = len(segments)
    n_pr  = len(data)
    seg_ids      = {s["id"] for s in segments}
    data_ids     = {item.get("id") for item in data}
    errors       = []

    # Количество
    if n_pr != n_seg:
        errors.append(f"Количество промптов {n_pr} ≠ сегментов {n_seg}")

    # Для каждого сегмента есть промпт
    for sid in sorted(seg_ids - data_ids):
        errors.append(f"Отсутствует промпт для сегмента #{sid}")

    # Качество каждого промпта
    for item in data:
        sid  = item.get("id", "?")
        text = item.get("photo_prompt", "")
        if not text.strip():
            errors.append(f"Промпт сегмента #{sid} пустой")
        elif not is_english(text):
            errors.append(f"Промпт сегмента #{sid} не на английском")

    if errors:
        return {
            "status": "error",
            "count": n_pr,
            "errors": errors,
            "message": f"❌ Фото промпты: {errors[0]}",
        }
    return {
        "status": "ok",
        "count": n_pr,
        "errors": [],
        "message": f"✅ Фото промпты: {n_pr}/{n_seg} промптов, все корректны",
    }


# ---------------------------------------------------------------------------
# ПРОВЕРКА 3 — Видео промпты
# ---------------------------------------------------------------------------

def check_video_prompts(session: str) -> dict:
    segments = load_segments(session)
    if segments is None:
        return {
            "status": "error",
            "count": None,
            "errors": ["result.json не найден"],
            "message": "❌ Видео промпты: result.json не найден",
        }

    path = PROMPTS_DIR / session / "video_prompts.json"

    if not path.exists():
        return {
            "status": "skipped",
            "count": None,
            "errors": [],
            "message": "⏭ Видео промпты: файл не найден, пропускаю",
        }

    data  = load_json_file(path)
    n_seg = len(segments)
    n_pr  = len(data)
    seg_ids  = {s["id"] for s in segments}
    data_ids = {item.get("id") for item in data}
    errors   = []

    if n_pr != n_seg:
        errors.append(f"Количество промптов {n_pr} ≠ сегментов {n_seg}")

    for sid in sorted(seg_ids - data_ids):
        errors.append(f"Отсутствует видео промпт для сегмента #{sid}")

    for item in data:
        sid  = item.get("id", "?")
        text = item.get("video_prompt", item.get("photo_prompt", ""))
        if not text.strip():
            errors.append(f"Видео промпт сегмента #{sid} пустой")
        elif not is_english(text):
            errors.append(f"Видео промпт сегмента #{sid} не на английском")

    if errors:
        return {
            "status": "error",
            "count": n_pr,
            "errors": errors,
            "message": f"❌ Видео промпты: {errors[0]}",
        }
    return {
        "status": "ok",
        "count": n_pr,
        "errors": [],
        "message": f"✅ Видео промпты: {n_pr}/{n_seg} промптов, все корректны",
    }


# ---------------------------------------------------------------------------
# ПРОВЕРКА 4 — Медиафайлы
# ---------------------------------------------------------------------------

def _check_photos(session: str, n_seg: int, seg_ids: set) -> dict:
    photos_dir = MEDIA_DIR / session / "photos"

    if not photos_dir.exists():
        return {
            "status": "skipped",
            "found": None,
            "missing": [],
            "errors": [],
            "message": "⏭ Фото: папка не найдена, пропускаю",
        }

    photo_files = sorted(photos_dir.glob("photo_*.png"))
    n_photos    = len(photo_files)
    found_ids   = set()
    errors      = []

    for f in photo_files:
        try:
            found_ids.add(int(f.stem.replace("photo_", "")))
        except ValueError:
            pass

    missing = sorted(seg_ids - found_ids)
    for sid in missing:
        errors.append(f"Отсутствует photo_{sid:03d}.png")

    for f in photo_files:
        if f.stat().st_size == 0:
            errors.append(f"{f.name}: пустой файл (0 байт)")
        elif not is_valid_image(f):
            errors.append(f"{f.name}: не является валидным изображением")

    if errors:
        return {
            "status": "error",
            "found": n_photos,
            "missing": missing,
            "errors": errors,
            "message": f"❌ Фото: {errors[0]}",
        }
    return {
        "status": "ok",
        "found": n_photos,
        "missing": [],
        "errors": [],
        "message": f"✅ Фото: {n_photos}/{n_seg} файлов, все валидны",
    }


def _check_videos(session: str, n_seg: int, seg_ids: set) -> dict:
    videos_dir = MEDIA_DIR / session / "videos"

    if not videos_dir.exists():
        return {
            "status": "skipped",
            "found": None,
            "missing": [],
            "errors": [],
            "message": "⏭ Видео: папка не найдена, пропускаю",
        }

    video_files = sorted(videos_dir.glob("video_*.mp4"))
    n_videos    = len(video_files)
    found_ids   = set()
    errors      = []

    for f in video_files:
        try:
            found_ids.add(int(f.stem.replace("video_", "")))
        except ValueError:
            pass

    missing = sorted(seg_ids - found_ids)
    for sid in missing:
        errors.append(f"Отсутствует video_{sid:03d}.mp4")

    for f in video_files:
        if f.stat().st_size == 0:
            errors.append(f"{f.name}: пустой файл (0 байт)")

    if errors:
        return {
            "status": "error",
            "found": n_videos,
            "missing": missing,
            "errors": errors,
            "message": f"❌ Видео: {errors[0]}",
        }
    return {
        "status": "ok",
        "found": n_videos,
        "missing": [],
        "errors": [],
        "message": f"✅ Видео: {n_videos}/{n_seg} файлов, все корректны",
    }


def check_media(session: str) -> tuple[dict, dict]:
    segments = load_segments(session)
    if segments is None:
        err = {"status": "error", "found": None, "missing": [], "errors": ["result.json не найден"], "message": "❌ result.json не найден"}
        return err, err

    n_seg   = len(segments)
    seg_ids = {s["id"] for s in segments}
    return (
        _check_photos(session, n_seg, seg_ids),
        _check_videos(session, n_seg, seg_ids),
    )


# ---------------------------------------------------------------------------
# Сохранение отчёта
# ---------------------------------------------------------------------------

def save_report(session: str, checks: dict, overall: str) -> Path:
    report = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "project":   session,
        "checks":    checks,
        "overall":   overall,
    }
    out = TRANSCRIPTS_DIR / session / "validation_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return out


# ---------------------------------------------------------------------------
# Вспомогательный вывод
# ---------------------------------------------------------------------------

def print_check(title: str, result: dict) -> None:
    print(f"  {result['message']}")
    errors = result.get("errors", [])
    for e in errors[1:]:          # первая ошибка уже в message
        print(f"    • {e}")


# ---------------------------------------------------------------------------
# Главная функция
# ---------------------------------------------------------------------------

def run() -> None:
    parser = argparse.ArgumentParser(description="Валидатор Video-Pipeline")
    parser.add_argument(
        "--project",
        help="Имя папки сессии (по умолчанию — последняя)",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Автоматически пытается исправить мелкие ошибки",
    )
    args = parser.parse_args()

    session = args.project or find_latest_session()
    if not session:
        print("❌ Нет сессий в data/input/")
        sys.exit(1)

    print(f"\n{'═'*54}")
    print(f"  Валидация: {session}")
    print(f"{'═'*54}\n")

    all_errors: list[str] = []
    checks: dict = {}

    # ── ПРОВЕРКА 1: Транскрипция ──────────────────────────
    print("ПРОВЕРКА 1 — Транскрипция:")
    r1 = check_transcription(session)
    print_check("Транскрипция", r1)
    checks["transcription"] = {
        "status":   r1["status"],
        "segments": r1.get("segments", 0),
        "coverage": r1.get("coverage"),
    }
    if r1["status"] == "error":
        all_errors.extend(r1["errors"])
    print()

    # ── ПРОВЕРКА 2: Фото промпты ──────────────────────────
    print("ПРОВЕРКА 2 — Фото промпты:")
    r2 = check_photo_prompts(session)
    print_check("Фото промпты", r2)
    checks["photo_prompts"] = {
        "status": r2["status"],
        "count":  r2.get("count", 0),
    }
    if r2["status"] == "error":
        all_errors.extend(r2["errors"])
    print()

    # ── ПРОВЕРКА 3: Видео промпты ─────────────────────────
    print("ПРОВЕРКА 3 — Видео промпты:")
    r3 = check_video_prompts(session)
    print_check("Видео промпты", r3)
    checks["video_prompts"] = {
        "status": r3["status"],
        "count":  r3.get("count"),
    }
    if r3["status"] == "error":
        all_errors.extend(r3["errors"])
    print()

    # ── ПРОВЕРКА 4: Медиафайлы ────────────────────────────
    print("ПРОВЕРКА 4 — Медиафайлы:")
    ph, vi = check_media(session)
    print_check("Фото", ph)
    print_check("Видео", vi)
    checks["photos"] = {
        "status":  ph["status"],
        "found":   ph.get("found"),
        "missing": ph.get("missing", []),
    }
    checks["videos"] = {
        "status":  vi["status"],
        "found":   vi.get("found"),
        "missing": vi.get("missing", []),
    }
    if ph["status"] == "error":
        all_errors.extend(ph["errors"])
    if vi["status"] == "error":
        all_errors.extend(vi["errors"])
    print()

    # ── ИТОГ ──────────────────────────────────────────────
    overall = "PASSED" if not all_errors else "FAILED"

    print("═" * 54)
    if overall == "PASSED":
        print("✅ ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ — пайплайн готов к монтажу!")
    else:
        print(f"❌ НАЙДЕНЫ ОШИБКИ ({len(all_errors)}) — исправь перед монтажом!")
    print("═" * 54)

    report_path = save_report(session, checks, overall)
    print(f"\nОтчёт сохранён: {report_path}")

    sys.exit(0 if overall == "PASSED" else 1)


if __name__ == "__main__":
    run()
