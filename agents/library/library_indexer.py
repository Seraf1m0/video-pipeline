"""
Library Indexer — индексация библиотеки футажей для матчинга по скрипту.

Этапы:
  1. Копирование mp4/mov из источника в library/clips/
  2. FFprobe-фильтрация (портрет, ratio, длительность, разрешение)
  3. Нарезка по сценам (файлы >30с), trim 2с с краёв
  4. Апскейл до 1920x1080 (NVENC или CPU fallback)
  5. Vision-описание 3 кадров (если сервер доступен)
  6. Сохранение library.json

Запуск:
  py agents/library/library_indexer.py --source "D:\\Монета ютуб\\Cosmos\\Футажи"
  py agents/library/library_indexer.py --source "..." --resume
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

try:
    import filelock as _filelock_mod
    _HAS_FILELOCK = True
except ImportError:
    _HAS_FILELOCK = False

# ── Пути через paths.py ────────────────────────────────────────────────────────

_BASE_DIR = Path(__file__).resolve().parent.parent.parent
_utils_dir = Path(__file__).resolve().parent.parent / "utils"
if str(_utils_dir) not in sys.path:
    sys.path.insert(0, str(_utils_dir))
from paths import get_library_dir, get_clips_dir, get_library_json, get_niche

# Дефолтные пути (channel_001_cosmos_de); переопределяются в main() через --channel
_LIB_DIR     = get_library_dir("channel_001_cosmos_de")
_CLIPS_DIR   = get_clips_dir("channel_001_cosmos_de")
_PHOTOS_DIR  = _LIB_DIR / "photos"
_TEMP_DIR    = _LIB_DIR / "temp"
_LIB_JSON    = get_library_json("channel_001_cosmos_de")

_VISION_URL  = "http://localhost:8765"

# ── Константы фильтрации ───────────────────────────────────────────────────────

MIN_RATIO      = 1.2    # принимаем от 4:3 и шире
MAX_RATIO      = 2.5    # принимаем ультраwide
MIN_DURATION   = 4.5    # сек
MIN_WIDTH      = 640
MIN_HEIGHT     = 360
SCENE_THRESH   = 0.3    # порог scene detection (ниже = больше сцен)
LONG_CLIP      = 30.0   # файлы длиннее этого нарезаем
TRIM_EDGE      = 2.0    # обрезаем 2с с каждого края фрагмента
MAX_SEGMENT    = 20.0   # фрагменты длиннее этого делим пополам

# ── Windows UTF-8 ──────────────────────────────────────────────────────────────

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


# ══════════════════════════════════════════════════════════════════════════════
# FFprobe / FFmpeg helpers
# ══════════════════════════════════════════════════════════════════════════════

def _run(cmd: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    """Запустить команду, захватить stdout+stderr."""
    return subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )


def ffprobe_info(path: Path) -> dict | None:
    """
    Вернуть метаданные видео через ffprobe.
    None если файл нечитаемый.
    """
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_streams", "-show_format", str(path),
    ]
    try:
        r = _run(cmd, timeout=30)
        if r.returncode != 0:
            return None
        data = json.loads(r.stdout.decode("utf-8", errors="replace"))
    except Exception:
        return None

    video_stream = next(
        (s for s in data.get("streams", []) if s.get("codec_type") == "video"),
        None,
    )
    if not video_stream:
        return None

    # Длительность — из stream или format
    duration = 0.0
    try:
        duration = float(
            video_stream.get("duration")
            or data.get("format", {}).get("duration", 0)
        )
    except (TypeError, ValueError):
        pass

    # FPS
    fps = 25.0
    fr = video_stream.get("r_frame_rate", "25/1")
    try:
        num, den = fr.split("/")
        fps = round(int(num) / int(den), 2)
    except Exception:
        pass

    # Ротация — из тегов или side_data
    rotation = 0
    try:
        rotation = int(
            video_stream.get("tags", {}).get("rotate", 0)
            or video_stream.get("side_data_list", [{}])[0].get("rotation", 0)
        )
    except Exception:
        pass

    # Битрейт
    bitrate = 0
    try:
        bitrate = int(data.get("format", {}).get("bit_rate", 0))
    except (TypeError, ValueError):
        pass

    return {
        "width":    int(video_stream.get("width", 0)),
        "height":   int(video_stream.get("height", 0)),
        "duration": round(duration, 3),
        "fps":      fps,
        "rotation": rotation,
        "bitrate":  bitrate,
    }


def check_nvenc() -> bool:
    """Проверить доступность NVENC энкодера."""
    cmd = ["ffmpeg", "-hide_banner", "-encoders"]
    try:
        r = _run(cmd, timeout=10)
        return b"h264_nvenc" in r.stdout
    except Exception:
        return False


def detect_scenes(path: Path) -> list[float]:
    """
    Вернуть список временных меток scene-change (в секундах).
    Использует ffmpeg select filter + showinfo.
    """
    cmd = [
        "ffmpeg", "-i", str(path),
        "-vf", f"select='gt(scene,{SCENE_THRESH})',showinfo",
        "-vsync", "vfr",
        "-f", "null", "-",
    ]
    try:
        r = _run(cmd, timeout=120)
        output = r.stderr.decode("utf-8", errors="replace")
    except Exception:
        return []

    timestamps = []
    # Паттерн: pts_time:12.345
    for match in re.finditer(r"pts_time:([\d.]+)", output):
        try:
            timestamps.append(float(match.group(1)))
        except ValueError:
            pass
    return sorted(set(timestamps))


def build_segments(duration: float, scene_times: list[float]) -> list[tuple[float, float]]:
    """
    Построить список сегментов (start, end) по найденным сценам.
    Добавляет 0.0 в начало и duration в конец как границы.
    """
    boundaries = sorted(set([0.0] + scene_times + [duration]))
    segments = []
    for i in range(len(boundaries) - 1):
        start = boundaries[i]
        end   = boundaries[i + 1]
        # Trim 2с с краёв
        t_start = start + TRIM_EDGE
        t_end   = end   - TRIM_EDGE
        seg_dur = t_end - t_start
        if seg_dur < MIN_DURATION:
            continue
        if seg_dur > MAX_SEGMENT:
            # Делим пополам
            mid = t_start + seg_dur / 2
            segments.append((t_start, mid))
            segments.append((mid, t_end))
        else:
            segments.append((t_start, t_end))
    return segments


def cut_segment(
    input_path: Path,
    start: float,
    duration: float,
    output_path: Path,
    use_nvenc: bool,
) -> bool:
    """Нарезать и апскейлить один сегмент."""
    vf = "scale=1920:1080:flags=lanczos"
    if use_nvenc:
        encode = [
            "-c:v", "h264_nvenc", "-preset", "p4", "-cq", "19",
            "-b:v", "8M", "-maxrate", "10M", "-bufsize", "20M",
        ]
    else:
        encode = [
            "-c:v", "libx264", "-preset", "fast", "-crf", "19",
            "-b:v", "8M", "-maxrate", "10M", "-bufsize", "20M",
        ]
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start),
        "-i", str(input_path),
        "-t", str(duration),
        "-vf", vf,
        *encode,
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(output_path),
    ]
    try:
        r = _run(cmd, timeout=180)
        return r.returncode == 0 and output_path.exists()
    except Exception:
        return False


def upscale_file(input_path: Path, output_path: Path, use_nvenc: bool) -> bool:
    """Апскейлить файл целиком до 1920x1080."""
    vf = "scale=1920:1080:flags=lanczos"
    if use_nvenc:
        encode = [
            "-c:v", "h264_nvenc", "-preset", "p4", "-cq", "19",
            "-b:v", "8M", "-maxrate", "10M", "-bufsize", "20M",
        ]
    else:
        encode = [
            "-c:v", "libx264", "-preset", "fast", "-crf", "19",
            "-b:v", "8M", "-maxrate", "10M", "-bufsize", "20M",
        ]
    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-vf", vf,
        *encode,
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(output_path),
    ]
    try:
        r = _run(cmd, timeout=300)
        return r.returncode == 0 and output_path.exists()
    except Exception:
        return False


# ══════════════════════════════════════════════════════════════════════════════
# Vision helpers
# ══════════════════════════════════════════════════════════════════════════════

_TEXT_KEYWORDS = {
    "text", "title", "letter", "caption", "watermark", "logo",
    "overlay", "subtitle", "word", "written", "label", "sign",
}


def vision_server_available() -> bool:
    """Проверить доступность хотя бы одного Vision сервера (8765-8768)."""
    for port in [8765, 8766, 8767, 8768]:
        try:
            r = requests.get(f"http://localhost:{port}/health", timeout=2)
            if r.status_code == 200 and r.json().get("model_loaded", False):
                return True
        except Exception:
            pass
    return False


def vision_analyze_clip(clip_path: Path) -> dict:
    """
    Получить Vision-описания для клипа.
    Возвращает:
      {"descriptions": [...], "vision_tags": str, "has_text": bool}
    Или {"descriptions": [], "vision_tags": "", "has_text": False} при ошибке.
    """
    empty = {"descriptions": [], "vision_tags": "", "has_text": False}
    try:
        r = requests.post(
            f"{_VISION_URL}/analyze",
            json={"video_path": str(clip_path), "segment_text": "space cosmos nebula"},
            timeout=120,
        )
        r.raise_for_status()
        data = r.json()
        frames = data.get("frames_description", [])
        descriptions = [f["description"] for f in frames]
        # Определить has_text
        all_text = " ".join(descriptions).lower()
        has_text = any(kw in all_text for kw in _TEXT_KEYWORDS)
        # Сформировать теги из первых слов каждого описания
        tags_words = []
        for desc in descriptions:
            words = desc.lower().split()[:4]
            tags_words.extend(words)
        # Уникальные теги
        seen = set()
        tags = []
        for w in tags_words:
            w_clean = re.sub(r"[^a-z]", "", w)
            if w_clean and w_clean not in seen:
                seen.add(w_clean)
                tags.append(w_clean)
        return {
            "descriptions": descriptions,
            "vision_tags":  " ".join(tags[:12]),
            "has_text":     has_text,
        }
    except Exception:
        return empty


# ══════════════════════════════════════════════════════════════════════════════
# Library JSON helpers
# ══════════════════════════════════════════════════════════════════════════════

# _LIB_LOCK переопределяется в main() после --channel аргумента
_LIB_LOCK = _LIB_JSON.with_suffix(".lock")


def _get_lock():
    """Вернуть файловый лок (filelock если доступен, иначе заглушка)."""
    if _HAS_FILELOCK:
        return _filelock_mod.FileLock(str(_LIB_LOCK), timeout=60)
    # заглушка-контекст без блокировки
    class _Noop:
        def __enter__(self): return self
        def __exit__(self, *a): pass
    return _Noop()


def load_library() -> dict:
    """Загрузить library.json с файловой блокировкой."""
    with _get_lock():
        if _LIB_JSON.exists():
            try:
                with open(_LIB_JSON, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
    return {"total": 0, "indexed_at": "", "clips": {}, "photos": {}}


def save_library(lib: dict) -> None:
    """Сохранить library.json атомарно с файловой блокировкой.
    Авто-бэкап каждые 500 проиндексированных клипов (хранить 3 последних).
    """
    with _get_lock():
        lib["indexed_at"] = datetime.now(timezone.utc).isoformat()
        lib.setdefault("photos", {})
        lib["total"] = len(lib["clips"]) + len(lib["photos"])
        tmp = _LIB_JSON.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(lib, f, ensure_ascii=False, indent=2)
        tmp.replace(_LIB_JSON)

        # Авто-бэкап каждые 500 проиндексированных клипов
        indexed_count = sum(1 for c in lib["clips"].values() if c.get("indexed"))
        if indexed_count > 0 and indexed_count % 500 == 0:
            date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            bak = _LIB_JSON.with_name(f"library.json.bak.{date_str}")
            shutil.copy2(_LIB_JSON, bak)
            for old in sorted(_LIB_JSON.parent.glob("library.json.bak.*"))[:-3]:
                try:
                    old.unlink()
                except Exception:
                    pass


def next_clip_id(lib: dict) -> str:
    """Следующий свободный ID клипа (4-значный)."""
    existing = set(lib["clips"].keys())
    n = 1
    while f"{n:04d}" in existing:
        n += 1
    return f"{n:04d}"


def get_processed_originals(lib: dict) -> set[str]:
    """Набор original_file уже обработанных клипов."""
    return {clip["original_file"] for clip in lib["clips"].values() if clip.get("original_file")}


# ══════════════════════════════════════════════════════════════════════════════
# Основная обработка одного файла
# ══════════════════════════════════════════════════════════════════════════════

def _build_fixed_segments(duration: float, seg_dur: float) -> list[tuple[float, float]]:
    """Нарезать видео на равные куски по seg_dur секунд (без trim с краёв)."""
    segs = []
    t = 0.0
    while t + seg_dur - 0.05 <= duration:   # 0.05s tolerance
        segs.append((t, t + seg_dur))
        t += seg_dur
    return segs


def process_file(
    src: Path,
    lib: dict,
    use_nvenc: bool,
    skip_vision: bool,
    vision_available: bool,
    idx: int,
    total: int,
    pool_name: str = "",
    fixed_duration: float = 0.0,  # >0 → нарезать строго по N секунд
) -> dict:
    """
    Обработать один исходный файл.
    Возвращает статистику: {"accepted": int, "cut": int, "skip_portrait": int,
                            "skip_short": int, "skip_ratio": int, "skip_res": int,
                            "vision_done": int}
    """
    stats = {
        "accepted": 0, "cut": 0,
        "skip_portrait": 0, "skip_short": 0,
        "skip_ratio": 0, "skip_res": 0,
        "vision_done": 0,
    }
    prefix = f"[{idx}/{total}]"

    # ── FFprobe ──────────────────────────────────────────────────────────────
    info = ffprobe_info(src)
    if not info:
        print(f"{prefix} ❌ ffprobe fail → {src.name}")
        return stats

    w, h      = info["width"], info["height"]
    duration  = info["duration"]
    rotation  = info["rotation"]

    # Portrait (повёрнутые на 90/270 или высота > ширины)
    effective_w, effective_h = w, h
    if abs(rotation) in (90, 270):
        effective_w, effective_h = h, w
    if effective_h > effective_w:
        print(f"{prefix} ⚠️  portrait → skip  ({src.name})")
        stats["skip_portrait"] += 1
        return stats

    # Разрешение
    if effective_w < MIN_WIDTH or effective_h < MIN_HEIGHT:
        print(f"{prefix} ⚠️  resolution {effective_w}x{effective_h} < min → skip  ({src.name})")
        stats["skip_res"] += 1
        return stats

    # Ratio
    ratio = round(effective_w / effective_h, 3)
    if not (MIN_RATIO <= ratio <= MAX_RATIO):
        print(f"{prefix} ⚠️  ratio {ratio:.2f} out of range → skip  ({src.name})")
        stats["skip_ratio"] += 1
        return stats

    # Минимальная длительность
    if duration < MIN_DURATION:
        print(f"{prefix} ⚠️  {duration:.1f}s < {MIN_DURATION}s → skip  ({src.name})")
        stats["skip_short"] += 1
        return stats

    # ── Нарезка по сценам / фиксированным кускам / целиком ───────────────────
    segments_to_encode: list[tuple[float | None, float | None]]

    if fixed_duration > 0:
        # Режим строгой нарезки: каждые N секунд
        segs = _build_fixed_segments(duration, fixed_duration)
        if not segs:
            print(f"{prefix} ⚠️  {duration:.1f}s < {fixed_duration}s → skip  ({src.name})")
            stats["skip_short"] += 1
            return stats
        segments_to_encode = segs
        cut_mode = True
        print(f"{prefix} ✂️  fixed {fixed_duration}s × {len(segs)} сегментов  ({src.name})")
    elif duration > LONG_CLIP:
        scene_times = detect_scenes(src)
        segs = build_segments(duration, scene_times)
        if not segs:
            # Нет сцен — берём целиком с trim
            t_start = TRIM_EDGE
            t_end   = duration - TRIM_EDGE
            seg_dur = t_end - t_start
            if seg_dur < MIN_DURATION:
                print(f"{prefix} ⚠️  после trim {seg_dur:.1f}s → skip  ({src.name})")
                stats["skip_short"] += 1
                return stats
            segs = [(t_start, t_end)]
        segments_to_encode = [(s, e) for s, e in segs]
        cut_mode = True
    else:
        # Короткий файл — обрабатываем целиком
        segments_to_encode = [(None, None)]
        cut_mode = False

    # ── Энкодинг + запись в lib ──────────────────────────────────────────────
    clip_count = 0
    for seg_start, seg_end in segments_to_encode:
        clip_id   = next_clip_id(lib)
        out_path  = _CLIPS_DIR / f"{clip_id}.mp4"

        if cut_mode:
            seg_dur = seg_end - seg_start
            ok = cut_segment(src, seg_start, seg_dur, out_path, use_nvenc)
        else:
            ok = upscale_file(src, out_path, use_nvenc)

        if not ok:
            print(f"{prefix} ❌ encode fail → {src.name} seg {seg_start}")
            continue

        # Реальная длительность готового клипа
        real_info = ffprobe_info(out_path)
        real_dur  = real_info["duration"] if real_info else (
            seg_end - seg_start if cut_mode else duration
        )

        # Vision
        vision_result = {"descriptions": [], "vision_tags": "", "has_text": False}
        do_vision = (not skip_vision) and vision_available
        if do_vision:
            vision_result = vision_analyze_clip(out_path)
            if vision_result["descriptions"]:
                stats["vision_done"] += 1

        clip_entry = {
            "file":               f"{clip_id}.mp4",
            "original_file":      f"{pool_name}/{src.name}" if pool_name else src.name,
            "duration":           round(real_dur, 3),
            "width":              1920,
            "height":             1080,
            "ratio":              round(1920 / 1080, 3),
            "fps":                info["fps"],
            "bitrate":            info["bitrate"],
            "has_text":           vision_result["has_text"],
            "indexed":            bool(vision_result["descriptions"]),
        }
        lib["clips"][clip_id] = clip_entry
        clip_count += 1
        stats["accepted"] += 1
        if cut_mode:
            stats["cut"] += 1

    if clip_count > 0:
        label = f"→ нарезан на {clip_count} части" if (cut_mode and clip_count > 1) else ""
        print(f"{prefix} ✅ {list(lib['clips'].keys())[-1]}.mp4  "
              f"({real_dur:.1f}s {w}x{h})  {label}")
    return stats


# ══════════════════════════════════════════════════════════════════════════════
# Фаза копирования
# ══════════════════════════════════════════════════════════════════════════════

def copy_phase(source: Path) -> list[Path]:
    """
    Скопировать mp4/mov файлы из source в _CLIPS_DIR (как raw, не переименовывая).
    Возвращает список скопированных файлов.
    Файлы кладём в _TEMP_DIR/raw/ для последующей обработки.
    """
    raw_dir = _TEMP_DIR / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    extensions = {".mp4", ".mov"}
    src_files  = [f for f in source.rglob("*") if f.is_file() and f.suffix.lower() in extensions]

    print(f"\n📂 Копирование {len(src_files)} файлов из {source}")
    print(f"   → {raw_dir}\n")

    copied = []
    for i, f in enumerate(src_files, 1):
        dest = raw_dir / f.name
        if dest.exists():
            copied.append(dest)
            continue
        try:
            shutil.copy2(f, dest)
            copied.append(dest)
            if i % 50 == 0 or i == len(src_files):
                print(f"  [{i}/{len(src_files)}] скопировано...")
        except Exception as e:
            print(f"  ❌ Не удалось скопировать {f.name}: {e}")

    print(f"\n✅ Скопировано: {len(copied)} файлов\n")
    return copied




def _apply_single_result(cid: str, clip: dict, result: dict) -> None:
    """Применить результат одиночного анализа к записи клипа."""
    if result["descriptions"]:
        clip["indexed"]  = True
        clip["rejected"] = False
        clip["keywords"] = result.get("vision_tags", result["descriptions"][0])
        print(f"  ✅ {cid}: {clip['keywords'][:60]}", flush=True)
    else:
        # Чёрный экран или frozen — всё равно помечаем indexed чтобы не ретраить
        clip["indexed"]       = True
        clip["rejected"]      = True
        clip["reject_reason"] = "black_or_frozen"
        print(f"  ❌ {cid}: black/frozen", flush=True)


def _batch_request(path1: Path, path2: Path, path3: Path | None = None) -> dict | None:
    """POST /analyze_batch к Vision серверу (2 или 3 клипа)."""
    try:
        import requests as _req
        body = {"video_path1": str(path1), "video_path2": str(path2)}
        if path3:
            body["video_path3"] = str(path3)
        r = _req.post(
            f"{_VISION_URL}/analyze_batch",
            json=body,
            timeout=150,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  ⚠️  Batch ошибка: {e}", flush=True)
        return None


# ══════════════════════════════════════════════════════════════════════════════
# Фото-фаза
# ══════════════════════════════════════════════════════════════════════════════

def vision_analyze_photo(photo_path: Path) -> dict:
    """Получить Vision-описание для одного фото (1 кадр)."""
    empty = {"description": "", "vision_tags": "", "has_text": False}
    try:
        r = requests.post(
            f"{_VISION_URL}/analyze",
            json={"video_path": str(photo_path), "segment_text": "space cosmos nebula"},
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()
        frames = data.get("frames_description", [])
        if not frames:
            return empty
        desc = frames[0]["description"]
        all_text = desc.lower()
        has_text = any(kw in all_text for kw in _TEXT_KEYWORDS)
        words = desc.lower().split()[:8]
        seen, tags = set(), []
        for w in words:
            w_clean = re.sub(r"[^a-z]", "", w)
            if w_clean and w_clean not in seen:
                seen.add(w_clean)
                tags.append(w_clean)
        return {"description": desc, "vision_tags": " ".join(tags), "has_text": has_text}
    except Exception:
        return empty


def get_photo_dims(path: Path) -> tuple[int, int]:
    """Получить размеры фото через ffprobe."""
    try:
        r = _run(["ffprobe", "-v", "quiet", "-print_format", "json",
                  "-show_streams", str(path)], timeout=10)
        data = json.loads(r.stdout.decode("utf-8", errors="replace"))
        s = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
        if s:
            return int(s.get("width", 0)), int(s.get("height", 0))
    except Exception:
        pass
    return 0, 0


def index_photos_phase(skip_vision: bool = False) -> None:
    """Проиндексировать все фото из library/photos/ и добавить в library.json."""
    if not _PHOTOS_DIR.exists():
        print("Папка library/photos/ не найдена.")
        return

    photo_files = sorted(
        f for f in _PHOTOS_DIR.iterdir()
        if f.is_file() and f.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
    )
    if not photo_files:
        print("Фото не найдены в library/photos/")
        return

    lib = load_library()
    lib.setdefault("photos", {})
    already = set(lib["photos"].keys())

    vision_available = False
    if not skip_vision:
        vision_available = vision_server_available()

    print(f"\n📷 Индексация фото: {len(photo_files)} файлов")
    print(f"   Vision: {'OK' if vision_available else 'недоступен'}\n")

    done = 0
    for i, f in enumerate(photo_files, 1):
        key = f.stem  # e.g. "photo_0001"
        if key in already and lib["photos"][key].get("indexed"):
            print(f"[{i}/{len(photo_files)}] пропуск (уже проиндексировано): {f.name}")
            continue

        w, h = get_photo_dims(f)
        ratio = round(w / h, 3) if h else 0

        vision_result = {"description": "", "vision_tags": "", "has_text": False}
        if vision_available and not skip_vision:
            vision_result = vision_analyze_photo(f)

        entry = {
            "file":          f.name,
            "type":          "photo",
            "width":         w,
            "height":        h,
            "ratio":         ratio,
            "description":   vision_result["description"],
            "keywords":      vision_result["vision_tags"],
            "has_text":      vision_result["has_text"],
            "indexed":       bool(vision_result["description"]),
        }
        lib["photos"][key] = entry
        done += 1
        print(f"[{i}/{len(photo_files)}] ✅ {f.name}  {w}x{h}  {vision_result['vision_tags'][:40]}")

        if i % 20 == 0:
            save_library(lib)

    save_library(lib)
    print(f"\nФото добавлено: {done}, всего в БД: {len(lib['photos'])}")
    print(f"Итого в библиотеке: {lib['total']} (клипы + фото)")


# ══════════════════════════════════════════════════════════════════════════════
# main
# ══════════════════════════════════════════════════════════════════════════════

def fix_missing_durations() -> None:
    """
    Добавить duration для клипов у которых его нет в library.json.
    Использует ffprobe для каждого клипа без duration.
    """
    lib = load_library()
    clips = lib.get("clips", {})

    to_fix = [
        (cid, entry)
        for cid, entry in clips.items()
        if entry.get("duration", 0) == 0
        and (_CLIPS_DIR / entry.get("file", "")).exists()
    ]

    if not to_fix:
        print("✅ Все клипы имеют duration — ничего не нужно фиксить")
        return

    print(f"⏱️  Фикс duration для {len(to_fix)} клипов...", flush=True)
    fixed = 0

    for cid, entry in to_fix:
        clip_path = _CLIPS_DIR / entry["file"]
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", str(clip_path)],
            capture_output=True, text=True,
        )
        try:
            dur = float(json.loads(r.stdout)["format"]["duration"])
            entry["duration"] = round(dur, 3)
            fixed += 1
        except Exception:
            pass

        if fixed % 100 == 0 and fixed > 0:
            print(f"  ⏱️  {fixed}/{len(to_fix)}...", flush=True)
            save_library(lib)

    save_library(lib)
    print(f"✅ Duration добавлен: {fixed}/{len(to_fix)} клипов", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Индексация библиотеки футажей"
    )
    parser.add_argument("--source", default=r"D:\Монета ютуб\Cosmos\Футажи",
                        help="Папка с исходными футажами")
    parser.add_argument("--skip-vision", action="store_true",
                        dest="skip_vision", help="Пропустить Vision анализ")
    parser.add_argument("--skip-copy",  action="store_true",
                        help="Не копировать файлы (уже в temp/raw/)")
    parser.add_argument("--resume",     action="store_true",
                        help="Продолжить с места остановки")
    parser.add_argument("--vision-only", action="store_true",
                        dest="vision_only", help="Только добавить Vision к уже готовым клипам")
    parser.add_argument("--photos-only", action="store_true",
                        dest="photos_only", help="Только проиндексировать фото из library/photos/")
    parser.add_argument("--retry-rejected", action="store_true",
                        dest="retry_rejected",
                        help="Сбросить rejected-клипы и незаиндексированные фото и переиндексировать")
    parser.add_argument("--fix-durations", action="store_true",
                        dest="fix_durations",
                        help="Добавить duration для клипов у которых его нет в library.json")
    parser.add_argument("--channel",
                        default="channel_001_cosmos_de",
                        help="ID канала для индексации (определяет библиотеку)")
    parser.add_argument("--fixed-duration", type=float, default=0.0,
                        dest="fixed_duration",
                        help="Нарезать строго по N секунд (0 = по сценам, дефолт)")
    args = parser.parse_args()

    # ── Переопределить пути под выбранный канал ─────────────────────────────────
    global _LIB_DIR, _CLIPS_DIR, _PHOTOS_DIR, _TEMP_DIR, _LIB_JSON, _LIB_LOCK
    _LIB_DIR    = get_library_dir(args.channel)
    _CLIPS_DIR  = get_clips_dir(args.channel)
    _PHOTOS_DIR = _LIB_DIR / "photos"
    _TEMP_DIR   = _LIB_DIR / "temp"
    _LIB_JSON   = get_library_json(args.channel)
    _LIB_LOCK   = _LIB_JSON.with_suffix(".lock")
    print(
        f"📚 Индексация библиотеки:\n"
        f"  Канал: {args.channel}\n"
        f"  Ниша:  {get_niche(args.channel)}\n"
        f"  Путь:  {_LIB_DIR}",
        flush=True,
    )

    # Убедиться что папки существуют
    _CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    _TEMP_DIR.mkdir(parents=True, exist_ok=True)

    # ── Fix-durations режим ─────────────────────────────────────────────────────
    if args.fix_durations:
        fix_missing_durations()
        return

    # ── Vision-only режим ───────────────────────────────────────────────────────
    if args.vision_only:
        print("⚠ --vision-only: vision_only_phase удалена (использовала удалённый vision.vision_client)")
        return

    # ── Photos-only режим ───────────────────────────────────────────────────────
    if args.photos_only:
        index_photos_phase(skip_vision=args.skip_vision)
        return

    # ── Retry-rejected режим ────────────────────────────────────────────────────
    if args.retry_rejected:
        lib = load_library()

        # Сбросить все rejected клипы → готовы к повторной индексации
        reset_clips = 0
        for cid, clip in lib["clips"].items():
            if clip.get("rejected") and clip.get("indexed"):
                clip["indexed"]  = False
                clip["rejected"] = False
                clip.pop("reject_reason", None)
                reset_clips += 1
        print(f"Клипов сброшено: {reset_clips}")

        # Сбросить indexed=True у фото без description (чтобы переиндексировать)
        reset_photos = 0
        for pid, photo in lib.get("photos", {}).items():
            if not photo.get("description") and photo.get("indexed"):
                photo["indexed"] = False
                reset_photos += 1
        print(f"Фото сброшено:   {reset_photos}")

        save_library(lib)
        print("\nЗапускаю index_photos_phase (фото)...\n")
        index_photos_phase(skip_vision=args.skip_vision)
        return

    source = Path(args.source)
    if not source.exists():
        print(f"❌ Папка не найдена: {source}")
        sys.exit(1)

    # ── Проверки окружения ─────────────────────────────────────────────────────
    use_nvenc = check_nvenc()
    encoder_label = "NVENC (GPU)" if use_nvenc else "libx264 (CPU)"
    print(f"\n🎬 Library Indexer")
    print(f"   Источник:  {source}")
    print(f"   Библиотека: {_LIB_DIR}")
    print(f"   Энкодер:   {encoder_label}")

    vision_available = False
    if not args.skip_vision:
        vision_available = vision_server_available()
        vision_label = "OK" if vision_available else "недоступен (пропускаем)"
        print(f"   Vision:    {vision_label}")
    else:
        print(f"   Vision:    пропущен (--skip-vision)")

    # ── Загрузка / инициализация библиотеки ───────────────────────────────────
    lib = load_library()  # всегда загружаем существующую библиотеку
    processed_originals = get_processed_originals(lib)

    if args.resume and processed_originals:
        print(f"\n▶️  Резюме: уже обработано {len(processed_originals)} оригиналов")

    # ── Копирование ────────────────────────────────────────────────────────────
    raw_dir = _TEMP_DIR / "raw"
    if not args.skip_copy:
        copy_phase(source)
    else:
        print(f"\n⏭️  Копирование пропущено (--skip-copy)")

    # Найти все файлы для обработки
    if raw_dir.exists():
        all_files = sorted(raw_dir.glob("*.mp4")) + sorted(raw_dir.glob("*.mov"))
    else:
        # Fallback: читать прямо из источника
        all_files = sorted(source.rglob("*.mp4")) + sorted(source.rglob("*.mov"))

    # Исключить уже обработанные (resume)
    if args.resume:
        all_files = [f for f in all_files if f.name not in processed_originals]

    total = len(all_files)
    print(f"\n🔄 Обработка {total} файлов...\n")

    # ── Сбор статистики ────────────────────────────────────────────────────────
    total_stats = {
        "accepted": 0, "cut": 0,
        "skip_portrait": 0, "skip_short": 0,
        "skip_ratio": 0, "skip_res": 0,
        "vision_done": 0,
    }
    t_start_all = time.time()

    for i, src_file in enumerate(all_files, 1):
        file_stats = process_file(
            src=src_file,
            lib=lib,
            use_nvenc=use_nvenc,
            skip_vision=args.skip_vision,
            vision_available=vision_available,
            idx=i,
            total=total,
            pool_name=source.name,
            fixed_duration=args.fixed_duration,
        )
        for k in total_stats:
            total_stats[k] += file_stats.get(k, 0)

        # Сохранять прогресс каждые 10 файлов
        if i % 10 == 0:
            save_library(lib)

    # Финальное сохранение
    save_library(lib)

    elapsed = time.time() - t_start_all
    mins    = int(elapsed // 60)
    secs    = int(elapsed % 60)

    skipped_total = (
        total_stats["skip_portrait"]
        + total_stats["skip_short"]
        + total_stats["skip_ratio"]
        + total_stats["skip_res"]
    )

    print(f"\n{'═' * 51}")
    print(f"📊 ИНДЕКСАЦИЯ ЗАВЕРШЕНА  ({mins}м {secs}с)")
    print(f"{'═' * 51}")
    print(f"  Обработано:          {total}")
    print(f"  ✅ Принято клипов:   {total_stats['accepted']}")
    print(f"  ✂️  Нарезано частей:  {total_stats['cut']}")
    print(f"  ⚠️  Portrait:         {total_stats['skip_portrait']}")
    print(f"  ⚠️  Слишком короткие: {total_stats['skip_short']}")
    print(f"  ⚠️  Плохой ratio:     {total_stats['skip_ratio']}")
    print(f"  ⚠️  Низкое разреш.:   {total_stats['skip_res']}")
    print(f"  📝 Vision описано:   {total_stats['vision_done']}")
    print(f"  💾 library.json:     {_LIB_JSON}")
    print(f"{'═' * 51}\n")


if __name__ == "__main__":
    main()
