"""
stock_replacer.py — найти стоковое видео для плохих клипов и заменить.

Берёт список плохих видео из blip_report.json (или --ids),
для каждого ищет подходящий сток (Pexels → Pixabay → NASA),
скачивает, нормализует до 10с и ресайзит до 1920×1080,
заменяет оригинал (старый файл сохраняет как video_NNN_original.mp4).

Запуск:
  py agents/video_validator/stock_replacer.py --session Video_20260309_190235
  py agents/video_validator/stock_replacer.py --session ... --ids 9,28,33,43
"""

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── Пути ─────────────────────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).parent.parent.parent
MEDIA_DIR   = BASE_DIR / "data" / "media"
PROMPTS_DIR = BASE_DIR / "data" / "prompts"
TEMP_DIR    = BASE_DIR / "temp"

# Добавим папку video_validator в sys.path (для stock_finder)
_VALIDATOR_DIR = Path(__file__).parent
if str(_VALIDATOR_DIR) not in sys.path:
    sys.path.insert(0, str(_VALIDATOR_DIR))

# ── Константы ─────────────────────────────────────────────────────────────────
TARGET_W         = 1920
TARGET_H         = 1080
TARGET_DURATION  = 10    # секунд
TARGET_FPS       = 24

# Общий стиль-префикс в промптах — ищем по этой фразе
_STYLE_END = "flashing monitor content."

# Задержка между запросами к NASA (rate limit: 30/hour)
_DELAY = 1.0   # секунд между запросами


# ── Вспомогательные функции ───────────────────────────────────────────────────

def _load_prompts(session: str) -> dict[int, dict]:
    """Загрузить video_prompts.json → {id: item}."""
    for p in [
        PROMPTS_DIR / session / "video" / "video_prompts.json",
        PROMPTS_DIR / session / "video_prompts.json",
    ]:
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            return {item["id"]: item for item in data}
    return {}


def _load_bad_ids(session: str) -> list[int]:
    """Загрузить список плохих видео из blip_report.json."""
    report = MEDIA_DIR / session / "blip_report.json"
    if not report.exists():
        return []
    data = json.loads(report.read_text(encoding="utf-8"))
    return data.get("summary", {}).get("bad_videos", [])


def _extract_visual(prompt: str) -> str:
    """Извлечь визуальную часть промпта (после общего стиль-префикса)."""
    idx = prompt.find(_STYLE_END)
    if idx >= 0:
        visual = prompt[idx + len(_STYLE_END):].strip()
        # Убрать суффикс "Animate only ... ---" если есть
        visual = re.sub(r"\s*Animate only.*", "", visual, flags=re.DOTALL).strip()
        return visual
    return prompt


def _smart_keywords(visual: str, segment_text: str = "") -> str:
    """
    Вытащить лучшие поисковые слова из немецкого сегмента + английского промпта.
    Немецкие существительные → английские эквиваленты.
    """
    # Немецкие → английские эквиваленты (для поиска)
    _DE_EN = {
        # Планеты — Pexels-friendly (не буквальные названия которые дают пляжи)
        "Venus":         "space exploration",
        "Mars":          "space exploration",
        "Erde":          "earth orbit",
        "Mond":          "moon space",
        "Sonne":         "sun solar",
        "Saturn":        "space planet",
        "Jupiter":       "space planet",
        # Миссия и техника
        "Atmosphäre":    "atmosphere",
        "Raumsonde":     "spacecraft",
        "Sonde":         "spacecraft",
        "Landemodul":    "spacecraft landing",
        "Weltraum":      "outer space",
        "Satellit":      "satellite",
        "Rakete":        "rocket launch",
        "Astronaut":     "astronaut",
        "Wissenschaftl": "scientist laboratory",
        "Ingenieur":     "engineer technology",
        # Физические термины — Pexels-friendly замены
        "Temperatur":    "volcanic lava",   # 475°C → лава, не термометр
        "Celsius":       "volcanic",
        "Oberfläche":    "geology rock",
        "Krater":        "crater geology",
        "Basalt":        "volcanic basalt rock",
        "Dunkelheit":    "dark space",
        "Kohlendioxid":  "atmosphere science",
        "Panorama":      "science presentation",
        "Forscher":      "researcher science",
        "Bilder":        "science imagery",
    }

    found: list[str] = []
    # Поиск немецких терминов в тексте сегмента
    for de, en in _DE_EN.items():
        if de in segment_text:
            found.append(en)

    # Дополняем из визуального промпта (английские слова — Pexels-friendly)
    tech_en = [
        "spacecraft", "astronaut", "laboratory", "engineer", "rocket",
        "satellite", "orbit", "telescope", "research", "science",
    ]
    vis_lower = visual.lower()
    for w in tech_en:
        if w in vis_lower:
            found.append(w)

    # Убрать дубли, оставить 3 лучших
    seen, result = set(), []
    for w in found:
        if w not in seen:
            seen.add(w)
            result.append(w)
        if len(result) >= 3:
            break

    return " ".join(result) if result else "space exploration science"


def _normalize_and_resize(src: Path, dst: Path) -> bool:
    """
    Нормализовать видео: обрезать / зациклить до TARGET_DURATION сек,
    ресайзнуть до TARGET_WxTARGET_H, выставить TARGET_FPS.
    Всё одной FFmpeg командой (без звука).
    """
    # Определяем длительность
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(src)],
        capture_output=True, text=True,
    )
    try:
        actual_dur = float(json.loads(r.stdout)["format"]["duration"])
    except Exception:
        actual_dur = 0.0

    if actual_dur <= 0:
        print(f"  ❌ Не удалось определить длительность: {src.name}")
        return False

    dst.parent.mkdir(parents=True, exist_ok=True)

    vf = (
        f"scale={TARGET_W}:{TARGET_H}:flags=lanczos:force_original_aspect_ratio=decrease,"
        f"pad={TARGET_W}:{TARGET_H}:(ow-iw)/2:(oh-ih)/2,"
        f"fps={TARGET_FPS}"
    )

    if actual_dur >= TARGET_DURATION:
        cmd = [
            "ffmpeg", "-y",
            "-i", str(src),
            "-t", str(TARGET_DURATION),
            "-vf", vf,
            "-c:v", "libx264", "-crf", "18", "-preset", "fast",
            "-pix_fmt", "yuv420p",
            "-an",
            str(dst),
        ]
    else:
        # Зациклить через stream_loop
        loops = int(TARGET_DURATION / actual_dur) + 2
        cmd = [
            "ffmpeg", "-y",
            "-stream_loop", str(loops),
            "-i", str(src),
            "-t", str(TARGET_DURATION),
            "-vf", vf,
            "-c:v", "libx264", "-crf", "18", "-preset", "fast",
            "-pix_fmt", "yuv420p",
            "-an",
            str(dst),
        ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0 and dst.exists() and dst.stat().st_size > 1024:
        dur_out = TARGET_DURATION
        size_mb = dst.stat().st_size / 1024 / 1024
        print(f"  ✅ Нормализовано: {actual_dur:.1f}s → {dur_out}s  "
              f"{TARGET_W}x{TARGET_H}  ({size_mb:.1f} МБ)")
        return True
    else:
        err = result.stderr[-300:] if result.stderr else "нет вывода"
        print(f"  ❌ Ошибка нормализации: {err}")
        return False


# ── Основной цикл ──────────────────────────────────────────────────────────────

def replace_bad_videos(session: str, ids: list[int] | None = None) -> None:
    from stock_finder import find_stock_video, download_stock_video, extract_keywords

    videos_dir = MEDIA_DIR / session / "videos"
    if not videos_dir.exists():
        print(f"❌ Папка видео не найдена: {videos_dir}")
        sys.exit(1)

    bad_ids = ids if ids else _load_bad_ids(session)
    if not bad_ids:
        print("✅ Нет плохих видео для замены.")
        return

    prompts = _load_prompts(session)

    print(f"\n{'='*60}")
    print(f"  ЗАМЕНА СТОКАМИ — {len(bad_ids)} видео")
    print(f"  Сессия: {session}")
    print(f"{'='*60}\n")

    stats = {"replaced": 0, "failed": 0, "sources": {}}
    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    for i, vid_id in enumerate(sorted(bad_ids), 1):
        video_path = videos_dir / f"video_{vid_id:03d}.mp4"
        print(f"[{i}/{len(bad_ids)}] #{vid_id:03d}  {video_path.name}")

        if not video_path.exists():
            print(f"  ⚠️ Файл не найден: {video_path.name} — пропускаю")
            stats["failed"] += 1
            continue

        # Извлечь ключевые слова для поиска
        item = prompts.get(vid_id, {})
        video_prompt = item.get("video_prompt", item.get("prompt", ""))
        segment_text = item.get("text", "")

        visual = _extract_visual(video_prompt) if video_prompt else ""
        query  = _smart_keywords(visual, segment_text)
        print(f"  🔍 Запрос: '{query}'")

        # Искать стоковое видео: передаём наш query как segment_text,
        # чтобы find_stock_video использовал именно его (через extract_keywords).
        stock = find_stock_video(query, visual or video_prompt)
        if not stock:
            # Fallback: пробуем более общие Pexels-friendly запросы
            from stock_finder import search_pexels_video, search_pixabay_video
            for fallback_q in ["space science technology", "science laboratory research", "space exploration"]:
                print(f"  ↩️ Фоллбэк: '{fallback_q}'")
                stock = search_pexels_video(fallback_q) or search_pixabay_video(fallback_q)
                if stock:
                    break

        if not stock:
            print(f"  ❌ Сток не найден для #{vid_id}")
            stats["failed"] += 1
            time.sleep(_DELAY)
            continue

        # Скачать во временный файл
        raw_tmp  = TEMP_DIR / f"stock_raw_{vid_id:03d}.mp4"
        norm_tmp = TEMP_DIR / f"stock_norm_{vid_id:03d}.mp4"

        raw_tmp.unlink(missing_ok=True)
        norm_tmp.unlink(missing_ok=True)

        if not download_stock_video(stock, raw_tmp):
            print(f"  ❌ Не удалось скачать сток для #{vid_id}")
            stats["failed"] += 1
            raw_tmp.unlink(missing_ok=True)
            time.sleep(_DELAY)
            continue

        # Нормализовать (размер, fps, длительность)
        if not _normalize_and_resize(raw_tmp, norm_tmp):
            print(f"  ❌ Ошибка нормализации для #{vid_id}")
            stats["failed"] += 1
            raw_tmp.unlink(missing_ok=True)
            norm_tmp.unlink(missing_ok=True)
            time.sleep(_DELAY)
            continue

        # Сохранить оригинал и заменить
        backup = video_path.with_name(f"video_{vid_id:03d}_original.mp4")
        if not backup.exists():
            video_path.rename(backup)
        else:
            video_path.unlink(missing_ok=True)

        import shutil
        shutil.copy2(str(norm_tmp), str(video_path))

        # Очистить temp
        raw_tmp.unlink(missing_ok=True)
        norm_tmp.unlink(missing_ok=True)

        src_name = stock.get("source", "unknown")
        src_id   = stock.get("id", "")
        print(f"  ✅ #{vid_id} заменено → {src_name} [{src_id}]")
        stats["replaced"] += 1
        stats["sources"][src_name] = stats["sources"].get(src_name, 0) + 1

        # Пауза между запросами (NASA rate limit)
        if i < len(bad_ids):
            time.sleep(_DELAY)

    # ── Итог ──────────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  ЗАМЕНА ЗАВЕРШЕНА")
    print(f"  ✅ Заменено:    {stats['replaced']}/{len(bad_ids)}")
    print(f"  ❌ Не найдено:  {stats['failed']}")
    if stats["sources"]:
        for src, cnt in stats["sources"].items():
            print(f"     {src}: {cnt}")
    print(f"{'='*60}\n")

    if stats["replaced"] > 0:
        print("💡 Запусти BLIP-валидатор снова чтобы проверить новые стоки:")
        print(f"   py agents/blip_validator/blip_validator.py --type video --project {session}")


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="stock_replacer — заменить плохие видео стоковыми"
    )
    parser.add_argument("--session", required=True,
                        help="Имя сессии (напр. Video_20260309_190235)")
    parser.add_argument("--ids",     default=None,
                        help="Конкретные ID через запятую (напр. 9,28,33). "
                             "Если не задано — берём из blip_report.json")
    args = parser.parse_args()

    ids_list = None
    if args.ids:
        ids_list = [int(x.strip()) for x in args.ids.split(",") if x.strip()]

    replace_bad_videos(args.session, ids_list)
