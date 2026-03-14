"""
BLIP Validator Agent
---------------------
Анализирует фото и видео через BLIP-2 (Salesforce/blip2-opt-2.7b).
Сравнивает подпись BLIP с оригинальным промптом по ключевым словам.
Сохраняет blip_report.json в папке сессии.

Установка:
  pip install transformers torch torchvision pillow accelerate

Запуск:
  py agents/blip_validator/blip_validator.py
  py agents/blip_validator/blip_validator.py --type photo --project Video_xxx
  py agents/blip_validator/blip_validator.py --type video --threshold 0.2
  py agents/blip_validator/blip_validator.py --type both --project Video_xxx
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── Пути ──────────────────────────────────────────────────────────────────────

BASE_DIR    = Path(__file__).parent.parent.parent
MEDIA_DIR   = BASE_DIR / "data" / "media"
PROMPTS_DIR = BASE_DIR / "data" / "prompts"
TEMP_DIR    = BASE_DIR / "temp"
PROGRESS_FILE = TEMP_DIR / "blip_progress.json"

# ffmpeg в PATH (winget)
_FFMPEG_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / (
    "Microsoft/WinGet/Packages/"
    "Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe/"
    "ffmpeg-8.0.1-full_build/bin"
)
if _FFMPEG_DIR.exists():
    os.environ["PATH"] = str(_FFMPEG_DIR) + os.pathsep + os.environ.get("PATH", "")

_SESSION_RE = re.compile(r"^Video_\d{8}_\d{6}$")


# ── Прогресс ──────────────────────────────────────────────────────────────────

def _write_progress(data: dict) -> None:
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    try:
        PROGRESS_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


# ── Сессия / промпты ──────────────────────────────────────────────────────────

def find_latest_session() -> str | None:
    if not MEDIA_DIR.exists():
        return None
    folders = [d for d in MEDIA_DIR.iterdir()
               if d.is_dir() and _SESSION_RE.match(d.name)]
    if not folders:
        return None
    return max(folders, key=lambda f: f.name).name


def _load_prompt_map(session: str, kind: str) -> dict[int, str]:
    """kind = 'photo' или 'video'. Возвращает {id: prompt_text}."""
    # Новая структура: data/prompts/{session}/{kind}/
    p = PROMPTS_DIR / session / kind / f"{kind}_prompts.json"
    # Fallback: старая плоская структура
    if not p.exists():
        p = PROMPTS_DIR / session / f"{kind}_prompts.json"
    if not p.exists():
        return {}
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    key = "photo_prompt" if kind == "photo" else "video_prompt"
    return {item["id"]: item.get(key, "") for item in data}


# ── Ключевые слова ────────────────────────────────────────────────────────────

_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "in", "on", "at", "of", "to", "for", "with", "by", "from", "that",
    "this", "it", "its", "and", "or", "not", "but", "as", "so", "up",
    "into", "over", "out", "about", "through", "very", "some", "which",
    "two", "three", "four", "five", "six", "many", "large", "small",
    "white", "black", "dark", "light",
}

# ── Чёрный список: явно не научный / не космический контент ──────────────────
# Если хоть одно слово из подписи BLIP совпадает — кадр отбраковывается,
# независимо от whitelist и score.
_CONTENT_BLACKLIST = frozenset({
    # Люди в нерабочем контексте
    "santa", "christmas", "holiday", "xmas",
    "wedding", "bride", "groom",
    "baby", "infant", "toddler",
    "dancing", "dance", "dancer",
    "yoga", "fitness", "gym", "workout",
    "cooking", "kitchen", "food", "restaurant", "cafe",
    "shopping", "mall", "store",
    # Животные
    "dog", "cat", "horse", "cow", "bird", "fish", "butterfly",
    "elephant", "lion", "tiger", "bear", "wolf", "deer",
    "snail", "insect", "bug", "spider", "bee", "ant",
    # Природа (не космическая)
    "flower", "flowers", "rose", "tulip", "petal", "bouquet",
    "beach", "wave", "waves", "ocean", "sea", "lake", "river",
    "forest", "tree", "trees", "leaf", "leaves", "grass",
    "mountain", "mountains", "waterfall", "valley",
    "sunset", "sunrise", "rainbow",
    # Транспорт (нерелевантный)
    "car", "truck", "bus", "train", "boat", "ship", "sailboat",
    "bicycle", "motorcycle",
    # Быт и развлечения
    "castle", "church", "building", "house", "home",
    "concert", "music", "guitar", "piano",
    "sports", "football", "soccer", "basketball",
    "pool", "swimming",
})

# ── Белый список: автоматически считает кадр «научным» ───────────────────────
# Если хоть одно слово из подписи BLIP есть в этом списке — кадр принимается,
# даже если overlap с промптом нулевой (разные слова, тот же смысл).
_SCIENCE_WHITELIST = frozenset({
    # Общие — наука и техника
    "science", "scientific", "scientist", "scientists",
    "lab", "laboratory", "laboratories", "experiment",
    "engineer", "engineers", "engineering", "technician",
    "researcher", "researchers",
    # Космос
    "space", "planet", "planets", "star", "stars", "galaxy", "galaxies",
    "universe", "cosmos", "cosmic", "orbit", "orbital",
    "satellite", "satellites", "spacecraft", "rocket", "rockets",
    "probe", "probes", "capsule", "capsules", "rover", "rovers", "lander",
    "telescope", "observatory",
    # Планеты и спутники
    "mars", "martian", "venus", "venusian",
    "saturn", "saturnian", "jupiter", "jovian",
    "mercury", "uranus", "neptune", "ganymede",
    "moon", "lunar", "solar", "sun",
    # Астрофизика
    "nebula", "nebulae", "comet", "comets", "asteroid", "asteroids",
    "atmosphere", "atmospheric", "magnetic", "magnetism",
    "gravitational", "gravity", "plasma", "radiation",
    # Млечный Путь, чёрные дыры
    "milky", "galactic", "interstellar", "intergalactic",
    "supernova", "quasar", "pulsar", "wormhole", "blackhole",
    "aurora", "borealis", "meteor", "meteorite",
    # Астронавты / миссии
    "astronaut", "astronauts", "cosmonaut", "cosmonauts",
    "nasa", "esa", "mission", "missions", "launch",
    "habitat", "station", "module",
    # Планетарная поверхность
    "crater", "craters", "regolith", "basalt", "lava",
    # Инженерия / оборудование
    "circuit", "circuits", "blueprint", "blueprints", "schematic",
    "diagram", "diagrams", "equation",
    "drill", "drilling", "drills",
    "globe", "cleanroom",
    "instrument", "instruments", "apparatus",
    "control",
    # Оптика и электроника
    "lens", "lenses", "optics", "optical",
    "electronic", "electronics",
    # Промышленная сборка
    "assembly", "assembled", "factory",
    # Компьютеры
    "computing", "workstation",
})


def _stem(word: str) -> str:
    """Минимальный стеммер: убирает типичные суффиксы для лучшего матчинга."""
    for sfx in ("ational", "ional", "tions", "tion", "ness", "ical", "ing", "ity",
                "ive", "ous", "al", "ly", "ed", "er", "es"):
        if word.endswith(sfx) and len(word) - len(sfx) >= 4:
            return word[: -len(sfx)]
    if word.endswith("s") and len(word) > 4:
        return word[:-1]
    return word


def _keywords(text: str) -> set[str]:
    """
    Ключевые слова: regex-токенизация (убирает апострофы и пунктуацию),
    без стоп-слов, длина > 2, со стеммингом.
    """
    tokens = re.findall(r"[a-z]+", text.lower())
    return {_stem(w) for w in tokens if w not in _STOPWORDS and len(w) > 2}


# ── BLIP модель ───────────────────────────────────────────────────────────────

def load_blip():
    """Загружает модель BLIP-2 один раз. Возвращает (processor, model, device)."""
    from transformers import Blip2Processor, Blip2ForConditionalGeneration
    import torch

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"[BLIP] Устройство: {device.upper()}"
          + (f" ({torch.cuda.get_device_name(0)})" if device == "cuda" else ""))
    print("[BLIP] Загружаю Salesforce/blip2-opt-2.7b ...")
    t0 = time.time()
    processor = Blip2Processor.from_pretrained("Salesforce/blip2-opt-2.7b")
    dtype = torch.float16 if device == "cuda" else torch.float32
    model = Blip2ForConditionalGeneration.from_pretrained(
        "Salesforce/blip2-opt-2.7b",
        torch_dtype=dtype,
    ).to(device)
    print(f"[BLIP] Модель загружена за {time.time() - t0:.1f}s")
    return processor, model, device


# ── Анализ изображения ────────────────────────────────────────────────────────

def analyze_image(
    image_path: Path | str,
    original_prompt: str,
    processor,
    model,
    device: str,
    threshold: float = 0.05,
) -> dict:
    """
    Получает BLIP-подпись, сравнивает с промптом.
    Возвращает dict: caption, score, match, brightness, issues.
    """
    from PIL import Image
    import torch

    img = Image.open(image_path).convert("RGB")

    # Яркость (средняя по оттенкам серого)
    gray_pixels = list(img.convert("L").getdata())
    avg_brightness = sum(gray_pixels) / len(gray_pixels) if gray_pixels else 128.0

    # BLIP-2 caption
    inputs = processor(img, return_tensors="pt").to(device, torch.float16 if device == "cuda" else torch.float32)
    with torch.no_grad():
        out = model.generate(**inputs, max_new_tokens=100)
    caption = processor.decode(out[0], skip_special_tokens=True)

    # Сравнение по ключевым словам (caption coverage: что % слов caption есть в промпте)
    prompt_kw  = _keywords(original_prompt)
    caption_kw = _keywords(caption)
    overlap = len(prompt_kw & caption_kw)
    score   = overlap / max(len(caption_kw), 1)

    # Чёрный / белый список по токенам подписи
    raw_tokens   = set(re.findall(r"[a-z]+", caption.lower()))
    blacklisted  = bool(raw_tokens & _CONTENT_BLACKLIST)
    science_hit  = bool(raw_tokens & _SCIENCE_WHITELIST)

    issues: list[str] = []
    if blacklisted:
        issues.append(f"чёрный список: {raw_tokens & _CONTENT_BLACKLIST}")
    if avg_brightness < 30:
        issues.append("слишком тёмное")
    if not science_hit and score <= threshold:
        issues.append("не совпадает с промптом")

    # Чёрный список — автоматический отказ, даже если whitelist совпал
    if blacklisted:
        match = False
    else:
        # Космические сцены тёмные по природе — не требуем яркость если science_hit
        match = (science_hit or score > threshold) and (science_hit or avg_brightness >= 30)

    return {
        "caption":    caption,
        "score":      round(score, 3),
        "match":      match,
        "brightness": round(avg_brightness, 1),
        "issues":     issues,
    }


# ── Кадры из видео ────────────────────────────────────────────────────────────

def extract_frames(video_path: Path | str) -> list[Path]:
    """Извлекает 3 кадра из видео на 25%, 50%, 75% через FFmpeg."""
    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    # Длина видео
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", str(video_path)],
        capture_output=True, text=True,
    )
    try:
        duration = float(json.loads(r.stdout)["format"]["duration"])
    except Exception:
        duration = 10.0

    frames: list[Path] = []
    for i, pct in enumerate([0.25, 0.50, 0.75]):
        ts         = duration * pct
        frame_path = TEMP_DIR / f"blip_frame_{i}.jpg"
        subprocess.run(
            ["ffmpeg", "-y",
             "-ss", str(ts),
             "-i", str(video_path),
             "-vframes", "1",
             "-q:v", "2",
             str(frame_path)],
            capture_output=True,
        )
        if frame_path.exists():
            frames.append(frame_path)

    return frames


def analyze_video(
    video_path: Path | str,
    original_prompt: str,
    processor,
    model,
    device: str,
    threshold: float = 0.05,
) -> dict:
    """Анализирует 3 кадра из видео, возвращает средний score."""
    frames = extract_frames(video_path)
    if not frames:
        return {
            "frames":         [],
            "avg_score":      0.0,
            "avg_brightness": 0.0,
            "match":          False,
            "issues":         ["не удалось извлечь кадры"],
        }

    results = [analyze_image(f, original_prompt, processor, model, device, threshold)
               for f in frames]

    avg_score      = sum(r["score"]      for r in results) / len(results)
    avg_brightness = sum(r["brightness"] for r in results) / len(results)

    # Если хотя бы ОДИН кадр из трёх прошёл — видео считается OK.
    # BLIP может давать разные описания для разных кадров одного и того же видео,
    # поэтому достаточно одного попадания.
    frame_match_count = sum(1 for r in results if r["match"])
    majority_match    = frame_match_count >= 1

    # Уникальные проблемы по всем кадрам
    seen: set[str] = set()
    unique_issues: list[str] = []
    for r in results:
        for iss in r["issues"]:
            if iss not in seen:
                seen.add(iss)
                unique_issues.append(iss)

    return {
        "frames":         [{"caption": r["caption"], "score": r["score"]} for r in results],
        "avg_score":      round(avg_score, 3),
        "avg_brightness": round(avg_brightness, 1),
        "match":          majority_match,
        "issues":         unique_issues,
    }


# ── Фото-анализ ───────────────────────────────────────────────────────────────

def run_photo_analysis(
    session: str,
    processor,
    model,
    device: str,
    threshold: float,
    progress_data: dict,
) -> list[dict]:
    photos_dir = MEDIA_DIR / session / "photos"
    if not photos_dir.exists():
        print(f"[BLIP] Папка фото не найдена: {photos_dir}")
        return []

    photos = sorted(
        [p for p in photos_dir.iterdir()
         if p.stem.startswith("photo_") and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")],
        key=lambda p: p.stem,
    )
    if not photos:
        print("[BLIP] Фото не найдено в папке сессии")
        return []

    prompt_map = _load_prompt_map(session, "photo")
    total      = len(photos)
    print(f"[BLIP] Анализирую {total} фото | порог={threshold}")

    progress_data.update({"type": "photo", "current": 0, "total": total,
                           "status": "running", "speed": 0.0})
    _write_progress(progress_data)

    results: list[dict] = []
    t_start = time.time()

    for i, photo_path in enumerate(photos, 1):
        m         = re.search(r"photo_(\d+)", photo_path.stem)
        photo_id  = int(m.group(1)) if m else i
        prompt    = prompt_map.get(photo_id, "")

        r = analyze_image(photo_path, prompt, processor, model, device, threshold)

        elapsed = time.time() - t_start
        speed   = i / elapsed if elapsed > 0 else 0.0

        mark = "✓" if r["match"] else "✗"
        print(f"  [{i}/{total}] {mark}  {photo_path.name}"
              f"  score={r['score']:.3f}  bright={r['brightness']:.0f}"
              f"  \"{r['caption'][:50]}\"")

        results.append({
            "id":         photo_id,
            "file":       photo_path.name,
            "caption":    r["caption"],
            "prompt":     (prompt[:100] + "...") if len(prompt) > 100 else prompt,
            "score":      r["score"],
            "match":      r["match"],
            "brightness": r["brightness"],
            "issues":     r["issues"],
        })

        progress_data["current"] = i
        progress_data["speed"]   = round(speed, 2)
        _write_progress(progress_data)

        # Каждые 10 — промежуточный итог
        if i % 10 == 0:
            bad_so_far = sum(1 for rr in results if not rr["match"])
            eta = (total - i) / speed if speed > 0 else 0
            print(f"  ── Прогресс: {i}/{total}  "
                  f"✗ плохих: {bad_so_far}  "
                  f"⚡ {speed:.1f} фото/с  "
                  f"ETA ~{int(eta)}с")

    return results


# ── Видео-анализ ──────────────────────────────────────────────────────────────

def run_video_analysis(
    session: str,
    processor,
    model,
    device: str,
    threshold: float,
    progress_data: dict,
) -> list[dict]:
    videos_dir = MEDIA_DIR / session / "videos"
    if not videos_dir.exists():
        print(f"[BLIP] Папка видео не найдена: {videos_dir}")
        return []

    videos = sorted(
        [v for v in videos_dir.iterdir()
         if v.stem.startswith("video_") and v.suffix.lower() in (".mp4", ".webm", ".mov")
         and "_original" not in v.stem],  # пропускаем бэкапы
        key=lambda v: v.stem,
    )
    if not videos:
        print("[BLIP] Видео не найдено в папке сессии")
        return []

    prompt_map = _load_prompt_map(session, "video")
    total      = len(videos)
    print(f"[BLIP] Анализирую {total} видео | порог={threshold}")

    progress_data.update({"type": "video", "current": 0, "total": total,
                           "status": "running", "speed": 0.0})
    _write_progress(progress_data)

    results: list[dict] = []
    t_start = time.time()

    for i, video_path in enumerate(videos, 1):
        m        = re.search(r"video_(\d+)", video_path.stem)
        video_id = int(m.group(1)) if m else i
        prompt   = prompt_map.get(video_id, "")

        r = analyze_video(video_path, prompt, processor, model, device, threshold)

        elapsed = time.time() - t_start
        speed   = i / elapsed if elapsed > 0 else 0.0

        mark = "✓" if r["match"] else "✗"
        print(f"  [{i}/{total}] {mark}  {video_path.name}"
              f"  score={r['avg_score']:.3f}"
              + (f"  issues={r['issues']}" if r["issues"] else ""))

        results.append({
            "id":         video_id,
            "file":       video_path.name,
            "avg_score":  r["avg_score"],
            "match":      r["match"],
            "issues":     r["issues"],
            "frames":     r["frames"],
        })

        progress_data["current"] = i
        progress_data["speed"]   = round(speed, 2)
        _write_progress(progress_data)

    return results


# ── Точка входа ───────────────────────────────────────────────────────────────

def run() -> None:
    parser = argparse.ArgumentParser(description="BLIP Validator Agent")
    parser.add_argument("--type",      choices=["photo", "video", "both"], default="photo",
                        help="Тип анализа (default: photo)")
    parser.add_argument("--threshold", type=float, default=0.05,
                        help="Порог совпадения (default: 0.05)")
    parser.add_argument("--project",   default=None,
                        help="Имя сессии (по умолчанию — последняя)")
    args = parser.parse_args()

    session = args.project or find_latest_session()
    if not session:
        print("[BLIP] Нет сессий в data/media/")
        sys.exit(1)

    print(f"[BLIP] Сессия  : {session}")
    print(f"[BLIP] Тип     : {args.type}")
    print(f"[BLIP] Порог   : {args.threshold}")
    print()

    progress_data: dict = {
        "session":   session,
        "type":      args.type,
        "current":   0,
        "total":     0,
        "status":    "loading",
        "speed":     0.0,
    }
    _write_progress(progress_data)

    # Загружаем модель один раз для всех типов
    processor, model, device = load_blip()

    photo_results: list[dict] = []
    video_results: list[dict] = []

    if args.type in ("photo", "both"):
        photo_results = run_photo_analysis(
            session, processor, model, device, args.threshold, progress_data
        )

    if args.type in ("video", "both"):
        video_results = run_video_analysis(
            session, processor, model, device, args.threshold, progress_data
        )

    # ── Сводка ────────────────────────────────────────────────────────────────
    bad_photos = [r["id"] for r in photo_results if not r["match"]]
    bad_videos = [r["id"] for r in video_results if not r["match"]]

    summary = {
        "photos_ok":    len(photo_results) - len(bad_photos),
        "photos_bad":   len(bad_photos),
        "photos_total": len(photo_results),
        "videos_ok":    len(video_results) - len(bad_videos),
        "videos_bad":   len(bad_videos),
        "videos_total": len(video_results),
        "bad_photos":   bad_photos,
        "bad_videos":   bad_videos,
        "threshold":    args.threshold,
    }

    report = {
        "session":  session,
        "type":     args.type,
        "photos":   photo_results,
        "videos":   video_results,
        "summary":  summary,
    }

    # Сохраняем отчёт
    report_path = MEDIA_DIR / session / "blip_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── Вывод ─────────────────────────────────────────────────────────────────
    print()
    print("=" * 52)
    print("  BLIP АНАЛИЗ ЗАВЕРШЁН")
    print("=" * 52)

    if photo_results:
        ok  = summary["photos_ok"]
        bad = summary["photos_bad"]
        tot = summary["photos_total"]
        print(f"  📸 Фото : ✅ {ok}/{tot}  ⚠️ плохих: {bad}")
        if bad_photos:
            ids = ", ".join(f"#{n}" for n in bad_photos[:20])
            if len(bad_photos) > 20:
                ids += f" ... ещё {len(bad_photos) - 20}"
            print(f"     Плохие: {ids}")

    if video_results:
        ok  = summary["videos_ok"]
        bad = summary["videos_bad"]
        tot = summary["videos_total"]
        print(f"  🎬 Видео: ✅ {ok}/{tot}  ⚠️ плохих: {bad}")
        if bad_videos:
            ids = ", ".join(f"#{n}" for n in bad_videos[:20])
            if len(bad_videos) > 20:
                ids += f" ... ещё {len(bad_videos) - 20}"
            print(f"     Плохие: {ids}")

    print(f"  📄 Отчёт: {report_path}")
    print("=" * 52)

    # Машинно-читаемая строка для бота
    print(f"BLIP_SUMMARY: {json.dumps(summary)}")

    progress_data["status"] = "completed"
    _write_progress(progress_data)


if __name__ == "__main__":
    run()
