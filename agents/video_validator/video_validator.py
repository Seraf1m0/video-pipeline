"""
Video Validator Agent
---------------------
Валидирует сгенерированные видео через Vision (Qwen2.5-VL-7B) + Claude.

Флоу для каждого видео:
  1. FFprobe: формат, битость, ориентация, соотношение сторон
  2. Vision (порт 8765): описания 3 кадров + black_screen / frozen
  3. Claude: valid/invalid + search_queries для замены

Каскад замены:
  Pixabay → Vecteezy → Grok перегенерация

Запуск:
  py agents/video_validator/video_validator.py
  py agents/video_validator/video_validator.py --session Video_20260307_222353
  py agents/video_validator/video_validator.py --session V... --channel channel_001_cosmos_de
"""

import argparse
import json
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# ── Пути ─────────────────────────────────────────────────────────────────────
BASE_DIR      = Path(__file__).resolve().parent.parent.parent
TEMP_DIR      = BASE_DIR / "temp"
PROGRESS_FILE = TEMP_DIR / "video_validator_progress.json"

if str(Path(__file__).parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).parent))

_VISION_DIR = BASE_DIR / "agents" / "vision"
if str(_VISION_DIR) not in sys.path:
    sys.path.insert(0, str(_VISION_DIR))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MAX_PARALLEL_VALIDATION = 3


# ── Утилиты путей ─────────────────────────────────────────────────────────────

def _get_channel_base(channel_id: str) -> Path:
    if not channel_id:
        return BASE_DIR / "data"
    config_path = BASE_DIR / "bot" / "channels" / channel_id / "config.json"
    try:
        cfg    = json.loads(config_path.read_text(encoding="utf-8"))
        subdir = cfg.get("data_subdir", "").strip()
        if subdir:
            return BASE_DIR / "data" / subdir
    except Exception:
        pass
    return BASE_DIR / "data"


def _get_channel_lang(channel_id: str) -> str:
    if "fr" in channel_id:
        return "fr"
    return "de"


def find_latest_session(channel_id: str = "") -> str | None:
    media_dir = _get_channel_base(channel_id) / "media"
    if not media_dir.exists():
        return None
    dirs = sorted(media_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    for d in dirs:
        if d.is_dir() and (d / "videos").exists():
            return d.name
    return None


def load_segments(session: str, transcripts_base: Path) -> list[dict]:
    p    = transcripts_base / session / "result.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    return data["segments"] if isinstance(data, dict) else data


def load_video_prompts(session: str, prompts_base: Path) -> dict[str, dict]:
    p = prompts_base / session / "video" / "video_prompts.json"
    if not p.exists():
        p = prompts_base / session / "video_prompts.json"
    if p.exists():
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return {str(item["id"]): item for item in data if "id" in item}
        return data
    return {}


def _write_progress(data: dict) -> None:
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    PROGRESS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ── FFprobe проверки ──────────────────────────────────────────────────────────

def _ffprobe_check(video_path: Path) -> tuple[bool, str, int, int, Path]:
    """
    Технические проверки ДО Vision-анализа.

    Порядок:
      1. MOV → MP4 конвертация (если нужно)
      2. Битый файл (ffprobe -v error)
      3. Портретная ориентация (h > w)
      4. Соотношение сторон: 1.67 — 1.87

    Возвращает: (ok, reason, width, height, final_path)
    final_path может отличаться от video_path если был .mov файл.
    """
    final_path = video_path

    # 1. MOV → MP4
    if video_path.suffix.lower() == ".mov":
        mp4_path = video_path.with_suffix(".mp4")
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", str(video_path), "-c", "copy",
             str(mp4_path), "-loglevel", "quiet"],
            capture_output=True, timeout=60,
        )
        if r.returncode == 0 and mp4_path.exists():
            video_path.unlink(missing_ok=True)
            final_path = mp4_path
            print(f"  🔄 MOV→MP4: {final_path.name}", flush=True)
        else:
            return False, "MOV конвертация провалилась", 0, 0, video_path

    # 2. Битый файл
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_type", "-of", "json", str(final_path)],
        capture_output=True, text=True, timeout=15,
    )
    if r.returncode != 0:
        return False, "битый файл (ffprobe error)", 0, 0, final_path
    try:
        out = json.loads(r.stdout or "{}")
        streams = out.get("streams", [])
        if not streams:
            return False, "нет видеопотока", 0, 0, final_path
    except Exception:
        return False, "ошибка парсинга ffprobe", 0, 0, final_path

    # 3. Размеры
    r2 = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_streams", str(final_path)],
        capture_output=True, text=True, timeout=15,
    )
    try:
        streams2 = json.loads(r2.stdout).get("streams", [])
        vs = next((s for s in streams2 if s.get("codec_type") == "video"), None)
        if not vs:
            return False, "нет видеопотока", 0, 0, final_path
        w = int(vs.get("width", 0))
        h = int(vs.get("height", 0))
    except Exception:
        return False, "ошибка получения размеров", 0, 0, final_path

    # 4. Портретная ориентация
    if h > w:
        return False, f"портретная ориентация {w}x{h}", w, h, final_path

    # 5. Соотношение сторон
    if h == 0:
        return False, "нулевая высота", w, h, final_path
    ratio = w / h
    if not (1.67 <= ratio <= 1.87):
        return (
            False,
            f"неверное соотношение сторон {ratio:.2f} (допустимо 1.67–1.87)",
            w, h, final_path,
        )

    return True, f"OK ({w}x{h}, {ratio:.2f})", w, h, final_path


# ── Апскейл ───────────────────────────────────────────────────────────────────

def _upscale_video(input_path: Path, output_path: Path) -> bool:
    """
    Апскейл до 1920×1080 через FFmpeg Lanczos + h264_nvenc.
    Если nvenc недоступен — fallback на libx264.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    def _run(codec: str) -> bool:
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", str(input_path),
             "-vf", "scale=1920:1080:flags=lanczos",
             "-c:v", codec,
             "-preset", "p4" if "nvenc" in codec else "fast",
             "-cq" if "nvenc" in codec else "-crf", "19",
             "-c:a", "copy",
             str(output_path), "-loglevel", "quiet"],
            capture_output=True, timeout=120,
        )
        return r.returncode == 0

    if _run("h264_nvenc"):
        return True
    print("  ⚠️  nvenc недоступен — используем libx264", flush=True)
    return _run("libx264")


# ── Утилиты замены ────────────────────────────────────────────────────────────

def _apply_stock(
    stock: dict,
    local_path: Path,
    video_path: Path,
    idx: int,
    upscaled_dir: Path | None,
) -> None:
    """Переместить сток на место оригинала, апскейл."""
    backup = video_path.parent / f"video_{idx:03d}_original.mp4"
    if video_path.exists():
        video_path.rename(backup)
    shutil.move(str(local_path), video_path)
    if backup.exists():
        backup.unlink(missing_ok=True)
    print(f"  ✅ #{idx}: {stock['source']} | '{stock.get('query', '')}'", flush=True)
    if upscaled_dir:
        up_out = upscaled_dir / video_path.name
        _upscale_video(video_path, up_out)


def _add_to_cache_safe(
    stock: dict, channel_id: str, session: str, local_file: Path
) -> None:
    try:
        from stock_cache import add_to_cache
        add_to_cache(stock, channel_id, session, local_file=local_file)
    except Exception:
        pass


# ── Умный каскад замены ───────────────────────────────────────────────────────

def replace_with_smart_cascade(
    idx: int,
    seg: dict,
    analysis: dict,
    session: str,
    channel_id: str,
    niche: str,
    videos_dir: Path,
    upscaled_dir: Path | None = None,
    grok_only: bool = False,
) -> tuple[bool, str]:
    """
    Умный каскад: до 5 попыток с Vision+Claude проверкой каждого кандидата.
    match_score >= 7 → принимаем.
    Все попытки провалились → Grok перегенерация.
    grok_only=True → пропустить стоки, сразу Grok перегенерация.
    """
    if grok_only:
        print(f"  🔄 #{idx}: --grok-only → сразу Grok перегенерация", flush=True)
        if _regenerate_with_grok(idx, seg, session, channel_id):
            return True, "grok_regen"
        print(f"  ℹ️  #{idx}: Grok не смог — оставляю оригинал", flush=True)
        return False, "original_kept"
    from vision_client import analyze_video as _vision_analyze
    from claude_analyzer import analyze_with_claude
    from stock_finder import find_stock_video, generate_stock_query
    from stock_history import add_stock_to_history as _add_to_history

    MAX_ATTEMPTS    = 5
    SCORE_THRESHOLD = 7

    segment_text = seg.get("text", "")
    channel_lang = _get_channel_lang(channel_id)
    video_path   = videos_dir / f"video_{idx:03d}.mp4"

    if not video_path.exists():
        print(f"  ⚠️  #{idx}: файл не найден, пропускаю", flush=True)
        return False, "missing"

    # Запросы от предыдущего анализа (или fallback)
    search_queries = analysis.get("search_queries") or []
    if not search_queries:
        fb_q = generate_stock_query(segment_text, niche=niche, segment_idx=idx)
        search_queries = [fb_q, "space cosmos universe footage", "galaxy nebula stars deep"]

    print(f"\n  🔄 #{idx}: умный каскад ({MAX_ATTEMPTS} попыток)...", flush=True)

    for attempt in range(1, MAX_ATTEMPTS + 1):
        print(f"  ─ Попытка {attempt}/{MAX_ATTEMPTS}", flush=True)

        stock = find_stock_video(
            search_queries=search_queries,
            min_duration=10,
            niche=niche,
            channel_id=channel_id,
            session=session,
        )
        if not stock:
            print(f"  📦 Стоки исчерпаны на попытке {attempt}", flush=True)
            break

        local_path = Path(stock.get("local_path", ""))
        if not local_path.exists():
            continue

        # BLIP + Claude проверка кандидата
        try:
            blip_result = _vision_analyze(str(local_path), segment_text=segment_text)
        except RuntimeError as e:
            print(f"  ⚠️  Vision недоступен: {e} — принимаем без проверки", flush=True)
            _apply_stock(stock, local_path, video_path, idx, upscaled_dir)
            _add_to_cache_safe(stock, channel_id, session, video_path)
            return True, stock["source"]

        cl_result = analyze_with_claude(
            blip_result, segment_text, niche=niche, channel_lang=channel_lang
        )
        score = cl_result.get("match_score", 0)

        if score >= SCORE_THRESHOLD:
            print(f"  ✅ #{idx}: match={score}/10 ≥ {SCORE_THRESHOLD} → принимаем", flush=True)
            _apply_stock(stock, local_path, video_path, idx, upscaled_dir)
            try:
                _add_to_history(channel_id, session, {**stock, "seg_id": idx})
            except Exception:
                pass
            _add_to_cache_safe(stock, channel_id, session, video_path)
            return True, stock["source"]
        else:
            print(
                f"  ❌ #{idx}: match={score}/10 < {SCORE_THRESHOLD} → следующий кандидат",
                flush=True,
            )
            new_queries = cl_result.get("search_queries", [])
            if new_queries:
                search_queries = new_queries
            try:
                local_path.unlink(missing_ok=True)
            except Exception:
                pass

    # Все попытки исчерпаны → Grok перегенерация
    print(f"  🔄 #{idx}: все попытки исчерпаны → Grok перегенерация", flush=True)
    if _regenerate_with_grok(idx, seg, session, channel_id):
        return True, "grok_regen"

    print(f"  ℹ️  #{idx}: оставляю оригинал", flush=True)
    return False, "original_kept"


# ── Замена стоками ────────────────────────────────────────────────────────────

def handle_partial_replacement(
    session: str,
    results: dict,
    videos_dir: Path,
    prompts: dict,
    niche: str = "cosmos",
    channel_id: str = "",
    upscaled_dir: Path | None = None,
    grok_only: bool = False,
) -> dict:
    """
    Заменить невалидные видео стоками (умный каскад, 3 потока).
    grok_only=True → пропустить стоки, сразу Grok перегенерация.

    results: {idx: (seg, video_path, analysis)}
    """
    from stock_finder import save_used_stocks

    invalid_items = [
        (idx, seg, analysis)
        for idx, (seg, video_path, analysis) in results.items()
        if not analysis["valid"]
    ]

    label = "Grok перегенерацию" if grok_only else "стоки"
    print(f"\n📦 Запускаю {label} для {len(invalid_items)} видео...", flush=True)
    stats: dict = {"replaced": 0, "failed": 0, "sources": {}}

    def replace_one(item):
        idx, seg, analysis = item
        print(f"\n  🔍 #{idx}: '{seg.get('text', '')[:60]}'", flush=True)
        print(f"  ❌ Причина: {analysis['reason']}", flush=True)
        success, source = replace_with_smart_cascade(
            idx, seg, analysis, session, channel_id, niche, videos_dir, upscaled_dir,
            grok_only=grok_only,
        )
        return idx, success, source

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(replace_one, item) for item in invalid_items]
        for future in as_completed(futures):
            idx, success, source = future.result()
            if success:
                stats["replaced"] += 1
                stats["sources"][source] = stats["sources"].get(source, 0) + 1
            else:
                stats["failed"] += 1

    save_used_stocks(session)
    return stats


def _regenerate_with_grok(
    idx: int,
    seg: dict,
    session: str,
    channel_id: str = "",
) -> bool:
    """Перегенерировать одно видео через Grok."""
    try:
        result = subprocess.run([
            "py",
            str(BASE_DIR / "agents" / "media_generator" / "media_generator.py"),
            "--session", session,
            "--platform", "2",
            "--type", "video",
            "--regenerate", str(idx),
        ], timeout=300)
        return result.returncode == 0
    except Exception as e:
        print(f"  ⚠️  Grok regen #{idx}: {e}", flush=True)
        return False


# ── Replace-only режим ────────────────────────────────────────────────────────

def _run_replace_only(session: str, niche: str, channel_id: str, grok_only: bool = False) -> None:
    """
    Запустить замену стоков используя существующий отчёт анализа.
    Vision-сервер должен быть запущен — каждый кандидат проходит Vision+Claude.
    grok_only=True → батч-регенерация всех плохих видео через Grok (3 таба параллельно).
    """
    ch_base        = _get_channel_base(channel_id)
    ch_media       = ch_base / "media"
    ch_transcripts = ch_base / "transcripts"

    report_path = ch_transcripts / session / "video_validation_report.json"
    if not report_path.exists():
        print(f"[!] Отчёт не найден: {report_path}")
        sys.exit(1)

    report = json.loads(report_path.read_text(encoding="utf-8"))
    invalid_videos = [v for v in report["videos"] if not v["valid"]]
    print(f"\n📦 Найдено {len(invalid_videos)} невалидных видео для замены", flush=True)

    if not invalid_videos:
        print("  ✅ Нечего заменять", flush=True)
        return

    videos_dir   = ch_media / session / "videos"
    upscaled_dir = ch_media / session / "videos_upscaled"

    # ── Батч-регенерация через Grok (3 таба параллельно) ──────────────────
    if grok_only:
        _MODULE_DIR_VV = Path(__file__).parent
        _MEDIA_GEN     = BASE_DIR / "agents" / "media_generator"
        if str(_MEDIA_GEN) not in sys.path:
            sys.path.insert(0, str(_MEDIA_GEN))

        from grok_agent import generate_grok_video  # noqa: E402
        from grok_agent import grok_load_progress, grok_save_progress  # noqa: E402
        from utils import PLATFORMS, read_prompts, read_photos  # noqa: E402

        invalid_ids = {v["id"] for v in invalid_videos}
        platform = PLATFORMS["2"].copy()
        platform["key"] = "2"
        photos  = read_photos(session)
        prompts = read_prompts(session, "video")
        total   = min(len(photos), len(prompts))

        # Удалить файлы плохих видео и убрать из прогресса
        completed = grok_load_progress(session)
        for idx in invalid_ids:
            old_file = videos_dir / f"video_{idx:03d}.mp4"
            if old_file.exists():
                old_file.unlink()
            completed.discard(idx)
        grok_save_progress(session, completed, total)

        remaining = invalid_ids - completed
        print(f"  🔄 Батч-регенерация {len(remaining)} видео через Grok (3 таба)...", flush=True)
        saved = generate_grok_video(platform, photos, prompts, videos_dir, session=session)
        print(f"\n{'='*60}")
        print(f"  БАТЧ-РЕГЕНЕРАЦИЯ ЗАВЕРШЕНА")
        print(f"  🔄 Сгенерировано: {saved} видео")
        print(f"{'='*60}\n")
        return

    # ── Стоковая замена (по одному, 3 потока) ─────────────────────────────
    # Проверка Vision
    from vision_client import check_health
    try:
        check_health()
        print("  ✅ Vision сервер доступен (порт 8765)", flush=True)
    except RuntimeError as e:
        print(f"\n❌ {e}\n", flush=True)
        sys.exit(1)

    # Загрузить полные сегменты
    segments = load_segments(session, ch_transcripts)
    seg_map  = {s["id"]: s for s in segments}

    upscaled_dir = ch_media / session / "videos_upscaled"

    stats: dict = {"replaced": 0, "failed": 0, "sources": {}}

    for entry in invalid_videos:
        idx = entry["id"]
        seg = seg_map.get(idx, {"text": entry.get("segment_text", "")})
        analysis = {
            "valid":             False,
            "reason":            entry.get("reason", ""),
            "score":             entry.get("score", 0),
            "match_score":       entry.get("match_score", 0),
            "search_queries":    entry.get("search_queries", []),
            "what_qwen_sees":    entry.get("what_qwen_sees", ""),
            "what_segment_needs": entry.get("what_segment_needs", ""),
        }
        print(f"\n  🔍 #{idx}: '{seg.get('text', '')[:60]}'", flush=True)
        print(f"  ❌ Причина: {analysis['reason']}", flush=True)

        success, source = replace_with_smart_cascade(
            idx, seg, analysis, session, channel_id, niche, videos_dir, upscaled_dir,
        )
        if success:
            stats["replaced"] += 1
            stats["sources"][source] = stats["sources"].get(source, 0) + 1
        else:
            stats["failed"] += 1

    from stock_finder import save_used_stocks
    save_used_stocks(session)

    print(f"\n{'='*60}")
    print(f"  ЗАМЕНА ЗАВЕРШЕНА")
    sources_str = ", ".join(f"{k}: {v}" for k, v in stats["sources"].items())
    print(f"  🔄 Заменено: {stats['replaced']}" + (f" ({sources_str})" if sources_str else ""))
    print(f"  ❌ Остались: {stats['failed']}")
    print(f"{'='*60}\n")


# ── Главная функция ───────────────────────────────────────────────────────────

def run(session: str, niche: str = "cosmos", channel_id: str = "", use_upscaled: bool = False, analysis_only: bool = False, replace_only: bool = False, grok_only: bool = False) -> dict:  # noqa: E501
    if replace_only:
        niche = niche or "cosmos"
        _run_replace_only(session, niche, channel_id, grok_only=grok_only)
        return {}

    print(f"\n{'='*60}")
    print(f"  VIDEO VALIDATOR  (Vision Qwen2.5-VL port 8765 + Claude)")
    print(f"  Сессия: {session}")
    if channel_id:
        print(f"  Канал:  {channel_id}")
    print(f"{'='*60}\n")

    # Пути канала
    ch_base        = _get_channel_base(channel_id)
    ch_media       = ch_base / "media"
    ch_transcripts = ch_base / "transcripts"
    ch_prompts     = ch_base / "prompts"
    channel_lang   = _get_channel_lang(channel_id)

    # niche из конфига канала если не задан
    if not niche and channel_id:
        try:
            cfg   = json.loads((BASE_DIR / "bot" / "channels" / channel_id / "config.json")
                                .read_text(encoding="utf-8"))
            niche = cfg.get("validator", {}).get("niche", "cosmos")
        except Exception:
            niche = "cosmos"
    niche = niche or "cosmos"

    upscaled_dir = ch_media / session / "videos_upscaled"
    upscaled_dir.mkdir(parents=True, exist_ok=True)

    if use_upscaled:
        videos_dir = upscaled_dir
        print(f"  📂 Источник: videos_upscaled/", flush=True)
    else:
        videos_dir = ch_media / session / "videos"

    if not videos_dir.exists():
        print(f"[!] Папка видео не найдена: {videos_dir}")
        sys.exit(1)

    segments = load_segments(session, ch_transcripts)
    prompts  = load_video_prompts(session, ch_prompts)
    seg_map  = {s["id"]: s for s in segments}

    video_items: list[tuple[int, Path]] = []
    for vp in sorted(videos_dir.glob("video_*.mp4")):
        if "_original" in vp.stem:
            continue
        try:
            idx = int(vp.stem.split("_")[1])
        except Exception:
            continue
        video_items.append((idx, vp))

    total = len(video_items)
    print(f"  Видео для анализа: {total}")
    print(f"  Сегментов:         {len(segments)}")

    # Проверка Vision сервера
    from vision_client import check_health, analyze_video as _vision_analyze
    try:
        check_health()
        print("  ✅ Vision сервер доступен (порт 8765)", flush=True)
    except RuntimeError as e:
        print(f"\n❌ {e}\n", flush=True)
        sys.exit(1)

    # Загрузка истории стоков
    from stock_finder import load_used_stocks, save_used_stocks, _used_stock_ids, _used_stock_urls
    load_used_stocks(session)

    if channel_id:
        try:
            from stock_history import get_used_stock_ids, cleanup_old_history
            hist_ids, hist_urls = get_used_stock_ids(channel_id)
            _used_stock_ids.update(hist_ids)
            _used_stock_urls.update(hist_urls)
            print(f"  📋 Кэш: {len(hist_ids)} ID заблокировано", flush=True)
        except Exception as e:
            print(f"  ⚠️  История стоков: {e}", flush=True)

    # Загрузка общего кэша
    try:
        from stock_cache import get_used_ids as _cache_get_ids
        cache_ids = _cache_get_ids(channel_id)
        _used_stock_ids.update(cache_ids)
    except Exception:
        pass

    from claude_analyzer import analyze_with_claude

    _write_progress({
        "session": session, "current": 0, "total": total,
        "status": "running", "valid": 0, "replaced": 0, "failed": 0,
    })

    t0 = time.time()
    print(f"\n🔍 Анализ {total} видео ({MAX_PARALLEL_VALIDATION} потока)...\n")

    raw_results: dict[int, tuple[dict, Path, dict]] = {}
    completed = 0

    def analyze_one(idx: int, video_path: Path) -> tuple[int, dict, Path]:
        seg  = seg_map.get(idx, {})
        text = seg.get("text", "")

        print(f"\n{'─'*50}", flush=True)
        print(f"  🎬 #{idx:03d}: {video_path.name}", flush=True)
        print(f"  📝 Сегмент: '{text[:80]}'", flush=True)

        # ── Шаг 1: FFprobe ────────────────────────────────────────────────
        ok, reason, w, h, final_path = _ffprobe_check(video_path)
        if not ok:
            print(f"  ❌ FFprobe: {reason} → reject", flush=True)
            from stock_finder import generate_stock_query
            fb = generate_stock_query(text, niche=niche, segment_idx=idx)
            return idx, {
                "valid":          False,
                "reason":         f"FFprobe: {reason}",
                "search_queries": [fb, "space cosmos universe", "galaxy nebula stars"],
                "score":          0,
                "match_score":    0,
            }, final_path

        print(f"  ✅ FFprobe: {reason}", flush=True)

        # ── Шаг 2: Vision (динамические вопросы на основе сегмента) ──────
        try:
            blip_result = _vision_analyze(str(final_path), segment_text=text)
            if blip_result.get("black_screen"):
                print(f"  ❌ Vision: чёрный экран → reject", flush=True)
            elif blip_result.get("frozen"):
                print(f"  ❌ Vision: стоп-кадр → reject", flush=True)
            else:
                print(f"  ✅ Vision: описания получены", flush=True)
        except RuntimeError as e:
            # Временный таймаут Vision под нагрузкой → не убиваем процесс
            print(f"  ⚠️  Vision недоступен для #{idx} — помечаем invalid", flush=True)
            from stock_finder import generate_stock_query
            fb = generate_stock_query(text, niche=niche, segment_idx=idx)
            return idx, {
                "valid":          False,
                "reason":         f"Vision недоступен: {e}",
                "search_queries": [fb, "space cosmos universe footage", "galaxy nebula stars"],
                "score":          0,
                "match_score":    0,
            }, final_path

        # ── Шаг 3: Claude ─────────────────────────────────────────────────
        analysis = analyze_with_claude(
            blip_result, text, niche=niche, channel_lang=channel_lang
        )

        if not analysis["valid"]:
            queries = analysis.get("search_queries", [])
            print(f"  🔍 Запросы для поиска:", flush=True)
            for i, q in enumerate(queries, 1):
                print(f"     {i}. '{q}'", flush=True)

        return idx, analysis, final_path

    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_VALIDATION) as executor:
        future_map = {
            executor.submit(analyze_one, idx, vp): (idx, vp)
            for idx, vp in video_items
        }
        for future in as_completed(future_map):
            orig_idx, orig_vp = future_map[future]
            try:
                idx, analysis, final_path = future.result()
            except Exception as e:
                print(f"  ⚠️  #{orig_idx}: ошибка анализа: {e}", flush=True)
                from stock_finder import generate_stock_query
                fb = generate_stock_query("space cosmos", niche=niche, segment_idx=orig_idx)
                analysis   = {
                    "valid": False, "reason": f"ошибка: {e}", "score": 0,
                    "search_queries": [fb, "space cosmos universe", "galaxy nebula stars"],
                }
                idx        = orig_idx
                final_path = orig_vp

            seg = seg_map.get(idx, {})
            raw_results[idx] = (seg, final_path, analysis)
            completed += 1

            icon = "✅" if analysis["valid"] else "❌"
            print(
                f"  {icon} #{idx:03d}: {analysis['reason']}  (score: {analysis.get('score', 0)})",
                flush=True,
            )

            _write_progress({
                "session": session, "current": completed, "total": total,
                "status": "running",
                "valid":    sum(1 for _, _, a in raw_results.values() if a["valid"]),
                "replaced": 0,
                "failed":   sum(1 for _, _, a in raw_results.values() if not a["valid"]),
            })

            if completed % 10 == 0:
                valid_so_far = sum(1 for _, _, a in raw_results.values() if a["valid"])
                print(
                    f"\n📊 {completed}/{total} | ✅ {valid_so_far} | ❌ {completed - valid_so_far}\n",
                    flush=True,
                )

    # ── Итог ─────────────────────────────────────────────────────────────────
    n_invalid   = sum(1 for _, _, a in raw_results.values() if not a["valid"])
    invalid_pct = n_invalid / total * 100 if total else 0

    print(f"\n{'─'*60}")
    print(f"📊 Результат: {total} | ✅ {total - n_invalid} | ❌ {n_invalid} ({invalid_pct:.1f}%)")
    print(f"{'─'*60}")

    if analysis_only and n_invalid > 0:
        print(f"\n⏸️  --analysis-only: замена стоками пропущена.", flush=True)
        print(f"   Запусти без --analysis-only чтобы заменить {n_invalid} невалидных.", flush=True)

    replaced_stats: dict = {"replaced": 0, "failed": 0, "sources": {}}
    if n_invalid > 0 and not analysis_only:
        replaced_stats = handle_partial_replacement(
            session, raw_results, videos_dir, prompts,
            niche=niche, channel_id=channel_id,
            upscaled_dir=upscaled_dir,
            grok_only=grok_only,
        )

    # ── Апскейл валидных видео ─────────────────────────────────────────────
    print("\n⏳ Апскейл валидных видео → videos_upscaled/", flush=True)
    for idx, (seg, vp, analysis) in raw_results.items():
        if analysis["valid"] and vp.exists():
            up_out = upscaled_dir / vp.name
            if not up_out.exists():
                ok = _upscale_video(vp, up_out)
                status = "✅" if ok else "⚠️"
                print(f"  {status} #{idx:03d}: апскейл", flush=True)

    # ── Отчёт ─────────────────────────────────────────────────────────────
    report_videos = []
    for idx, (seg, vp, analysis) in sorted(raw_results.items()):
        report_videos.append({
            "id":               idx,
            "file":             vp.name,
            "valid":            analysis["valid"],
            "score":            analysis.get("score", 0),
            "match_score":      analysis.get("match_score", 0),
            "reason":           analysis["reason"],
            "what_qwen_sees":   analysis.get("what_qwen_sees", ""),
            "what_segment_needs": analysis.get("what_segment_needs", ""),
            "search_queries":   analysis.get("search_queries", []),
            "segment_text":     seg.get("text", "")[:120],
        })

    report = {
        "session":             session,
        "total":               total,
        "valid":               total - n_invalid,
        "invalid":             n_invalid,
        "invalid_pct":         round(invalid_pct, 1),
        "replaced_with_stock": replaced_stats["replaced"],
        "failed":              replaced_stats["failed"],
        "stock_sources":       replaced_stats["sources"],
        "videos":              report_videos,
    }

    report_path = ch_transcripts / session / "video_validation_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    elapsed    = int(time.time() - t0)
    sources_str = ", ".join(f"{k}: {v}" for k, v in replaced_stats["sources"].items())

    print(f"\n{'='*60}")
    print(f"  ВАЛИДАЦИЯ ЗАВЕРШЕНА  ({elapsed}с)")
    print(f"  📊 Всего:    {total}")
    print(f"  ✅ Хорошие:  {total - n_invalid}")
    print(f"  🔄 Стоки:    {replaced_stats['replaced']}"
          + (f" ({sources_str})" if sources_str else ""))
    print(f"  ❌ Остались: {replaced_stats['failed']}")
    print(f"  📁 Отчёт:    {report_path}")
    print(f"  📂 Апскейл:  {upscaled_dir}")
    print(f"{'='*60}\n")

    save_used_stocks(session)

    if channel_id:
        try:
            from stock_history import cleanup_old_history
            cleanup_old_history(channel_id)
        except Exception:
            pass
        try:
            from stock_cache import cleanup_old_files
            cleanup_old_files()
        except Exception:
            pass

    _write_progress({
        "session": session, "current": total, "total": total,
        "status": "done",
        "valid":    total - n_invalid,
        "replaced": replaced_stats["replaced"],
        "failed":   replaced_stats["failed"],
    })

    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Video Validator Agent")
    parser.add_argument("--session", default=None)
    parser.add_argument("--channel", default="",
                        help="ID канала (напр. channel_001_cosmos_de)")
    parser.add_argument("--use-upscaled", action="store_true",
                        help="Запускать на videos_upscaled/ вместо videos/")
    parser.add_argument("--analysis-only", action="store_true",
                        help="Только анализ — без замены стоками")
    parser.add_argument("--replace-only", action="store_true",
                        help="Только замена стоками по готовому отчёту (без Vision анализа)")
    parser.add_argument("--grok-only", action="store_true",
                        help="Перегенерация плохих видео через Grok (пропустить поиск стоков)")
    args = parser.parse_args()

    session = args.session or find_latest_session(args.channel)
    if not session:
        print("[!] Нет сессий с видео")
        sys.exit(1)

    run(session, channel_id=args.channel, use_upscaled=args.use_upscaled,
        analysis_only=args.analysis_only, replace_only=args.replace_only,
        grok_only=args.grok_only)
