"""
Video Validator Agent
---------------------
Анализирует сгенерированные видео через Molmo 2 (4 критерия):
  - Артефакты нейросети
  - Научность контента
  - Соответствие промпту
  - Техническое качество

Логика:
  > 50% плохих → handle_critical_failure  (50% перегенерация + 50% стоки)
  ≤ 50% плохих → handle_partial_replacement (только стоки)

После валидации: нормализация + апскейл всех видео.

Запуск:
  py agents/video_validator/video_validator.py
  py agents/video_validator/video_validator.py --session Video_20260307_222353
"""

import argparse
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# ── Пути ─────────────────────────────────────────────────────────────────────
BASE_DIR      = Path(__file__).parent.parent.parent
TRANSCRIPTS   = BASE_DIR / "data" / "transcripts"
PROMPTS_DIR   = BASE_DIR / "data" / "prompts"
MEDIA_DIR     = BASE_DIR / "data" / "media"
TEMP_DIR      = BASE_DIR / "temp"
PROGRESS_FILE = TEMP_DIR / "video_validator_progress.json"

# sys.path — для импорта соседних модулей
if str(Path(__file__).parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).parent))

# video_cutter (для process_all_videos)
_CUTTER_DIR = BASE_DIR / "agents" / "video_cutter"
if str(_CUTTER_DIR) not in sys.path:
    sys.path.insert(0, str(_CUTTER_DIR))

# Windows: UTF-8 консоль
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MAX_PARALLEL_VALIDATION = 3  # параллельных анализов Molmo


# ── Вспомогательные функции ───────────────────────────────────────────────────

def find_latest_session() -> str | None:
    dirs = sorted(MEDIA_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    for d in dirs:
        if d.is_dir() and (d / "videos").exists():
            return d.name
    return None


def load_segments(session: str) -> list[dict]:
    p = TRANSCRIPTS / session / "result.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    return data["segments"] if isinstance(data, dict) else data


def load_video_prompts(session: str) -> dict[str, dict]:
    """Возвращает {str(id): {prompt, ...}} из video_prompts.json."""
    p = PROMPTS_DIR / session / "video" / "video_prompts.json"
    if not p.exists():
        p = PROMPTS_DIR / session / "video_prompts.json"
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


# ── Вспомогательные функции замены ───────────────────────────────────────────

def regenerate_with_grok(idx: int, seg: dict, video_path: Path, session: str) -> bool:
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
        return result.returncode == 0 and video_path.exists() and video_path.stat().st_size > 1024
    except Exception as e:
        print(f"  ⚠️ Grok regen #{idx}: {e}")
        return False


# ── Замена стоками ────────────────────────────────────────────────────────────

def handle_partial_replacement(
    session: str,
    results: dict,
    videos_dir: Path,
    prompts: dict,
    niche: str = "cosmos",
) -> dict:
    """
    ≤ 50% плохих — заменяем стоками параллельно (5 потоков).
    results: {idx: (seg, video_path, analysis)}
    Возвращает статистику {replaced, failed, sources}.
    """
    from stock_finder import (
        find_stock_video,
        download_and_verify,
        load_used_stocks,
        save_used_stocks,
        _used_stock_ids,
    )

    invalid_items = [
        (idx, seg, video_path, analysis)
        for idx, (seg, video_path, analysis) in results.items()
        if not analysis["valid"]
    ]

    print(f"\n📦 Ищу стоки для {len(invalid_items)} видео параллельно...")

    stats = {"replaced": 0, "failed": 0, "sources": {}}

    def replace_with_stock(item):
        idx, seg, video_path, analysis = item
        segment_text = seg.get("text", "")
        print(f"\n  🔍 #{idx}: '{segment_text[:60]}'")
        print(f"  ❌ Причина: {analysis['reason']}")

        if not video_path.exists():
            print(f"  ⚠️ #{idx}: файл не найден, пропускаю")
            return idx, False, "missing"

        stock = find_stock_video(
            segment_text=segment_text,
            prompt_text="",
            niche=niche,
            min_duration=10,
            segment_idx=idx,
        )
        if stock:
            backup = video_path.parent / f"video_{idx:03d}_original.mp4"
            video_path.rename(backup)

            local_path = stock.get("local_path")
            if local_path and Path(local_path).exists():
                # BLIP уже проверил — просто перемещаем файл
                import shutil
                shutil.move(local_path, video_path)
                success = True
            else:
                success = download_and_verify(stock, video_path)

            if success:
                # Удаляем оригинал — бэкап не нужен
                try:
                    backup.unlink(missing_ok=True)
                except Exception:
                    pass
                print(
                    f"  ✅ #{idx}: {stock['source']} "
                    f"| '{stock.get('query', '')}'"
                )
                print(f"  📊 Уникальных стоков: {len(_used_stock_ids)}")
                return idx, True, stock["source"]
            else:
                backup.rename(video_path)

        # Стоки не нашли → Grok перегенерация
        print(f"  🔄 #{idx}: Grok перегенерация...")
        success = regenerate_with_grok(idx, seg, video_path, session)
        if success:
            return idx, True, "grok_regen"

        print(f"  ℹ️ #{idx}: оставляю оригинал")
        return idx, False, "original_kept"

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(replace_with_stock, item) for item in invalid_items]
        for future in as_completed(futures):
            idx, success, source = future.result()
            if success:
                stats["replaced"] += 1
                stats["sources"][source] = stats["sources"].get(source, 0) + 1
            else:
                stats["failed"] += 1

    save_used_stocks(session)
    return stats


def handle_critical_failure(
    session: str,
    results: dict,
    segments: list[dict],
    prompts: dict,
    videos_dir: Path,
) -> None:
    """
    > 50% плохих — полная перегенерация:
      - чётные по порядку → перегенерация промпта + фото + видео
      - нечётные → замена стоками
    """
    invalid_ids = sorted(idx for idx, (_, _, a) in results.items() if not a["valid"])

    regen_ids = invalid_ids[::2]   # чётные позиции → перегенерация
    stock_ids  = invalid_ids[1::2] # нечётные → стоки

    print(f"\n🚨 КРИТИЧНО: > 50% плохих видео!")
    print(f"  🔄 Перегенерация: {len(regen_ids)} видео → {regen_ids}")
    print(f"  📦 Стоки:         {len(stock_ids)} видео → {stock_ids}")

    # ── 1. Перегенерация промптов + фото + видео ───────────────────────────
    if regen_ids:
        regen_str = ",".join(map(str, regen_ids))
        agents_dir = BASE_DIR / "agents"

        print(f"\n🎨 Перегенерирую промпты для: {regen_str}")
        subprocess.run([
            "py",
            str(agents_dir / "prompt_generator" / "prompt_generator.py"),
            "--type", "both",
            "--project", session,
            "--regenerate", regen_str,
        ])

        print(f"🖼️ Перегенерирую фото для: {regen_str}")
        subprocess.run([
            "py",
            str(agents_dir / "media_generator" / "media_generator.py"),
            "--platform", "pixel",
            "--project", session,
            "--regenerate", regen_str,
        ])

        print(f"🎬 Перегенерирую видео для: {regen_str}")
        subprocess.run([
            "py",
            str(agents_dir / "media_generator" / "media_generator.py"),
            "--platform", "grok",
            "--project", session,
            "--regenerate", regen_str,
        ])

    # ── 2. Замена стоками для оставшихся ──────────────────────────────────
    if stock_ids:
        stock_results = {idx: results[idx] for idx in stock_ids if idx in results}
        handle_partial_replacement(session, stock_results, videos_dir, prompts)


# ── Главная функция ───────────────────────────────────────────────────────────

def run(session: str, niche: str = "cosmos") -> dict:
    print(f"\n{'='*60}")
    print(f"  VIDEO VALIDATOR  (BLIP, 6 критериев)")
    print(f"  Сессия: {session}")
    print(f"{'='*60}\n")

    videos_dir = MEDIA_DIR / session / "videos"
    if not videos_dir.exists():
        print(f"[!] Папка видео не найдена: {videos_dir}")
        sys.exit(1)

    segments = load_segments(session)
    prompts  = load_video_prompts(session)
    seg_map  = {s["id"]: s for s in segments}

    # Список видеофайлов с известными ID
    video_items: list[tuple[int, Path]] = []
    for vp in sorted(videos_dir.glob("video_*.mp4")):
        if "_original" in vp.stem:
            continue  # пропускаем бэкапы
        try:
            idx = int(vp.stem.split("_")[1])
        except Exception:
            continue
        video_items.append((idx, vp))

    total = len(video_items)
    print(f"  Видео для анализа: {total}")
    print(f"  Сегментов:         {len(segments)}")
    print(f"  Потоков Molmo:     {MAX_PARALLEL_VALIDATION}\n")

    # Загружаем историю использованных стоков
    from stock_finder import load_used_stocks, save_used_stocks
    load_used_stocks(session)

    # Импорт тяжёлых зависимостей
    from blip_analyzer import analyze_video

    _write_progress({
        "session": session, "current": 0, "total": total,
        "status": "running", "valid": 0, "replaced": 0, "failed": 0,
    })

    t0 = time.time()

    # ── Параллельный анализ ───────────────────────────────────────────────────
    print(f"🔍 Параллельный анализ {total} видео ({MAX_PARALLEL_VALIDATION} потока)...\n")

    raw_results: dict[int, tuple[dict, Path, dict]] = {}
    completed = 0

    def analyze_one(idx: int, video_path: Path) -> tuple[int, dict]:
        seg    = seg_map.get(idx, {})
        text   = seg.get("text", "")
        p_data = prompts.get(str(idx), {})
        prompt = (p_data.get("video_prompt") or p_data.get("prompt")
                  or p_data.get("text") or "")
        analysis = analyze_video(video_path, prompt, text)
        return idx, analysis

    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_VALIDATION) as executor:
        future_map = {
            executor.submit(analyze_one, idx, vp): (idx, vp)
            for idx, vp in video_items
        }

        for future in as_completed(future_map):
            idx, vp = future_map[future]
            try:
                idx, analysis = future.result()
            except Exception as e:
                print(f"  ⚠️ #{idx}: ошибка анализа: {e}")
                analysis = {
                    "valid": False, "has_artifacts": False,
                    "is_scientific": False, "matches_prompt": False,
                    "good_quality": False, "exact_object": False,
                    "meaningful": False,
                    "reason": f"ошибка: {e}", "score": 0,
                }

            seg = seg_map.get(idx, {})
            raw_results[idx] = (seg, vp, analysis)
            completed += 1

            icon = "✅" if analysis["valid"] else "❌"
            print(
                f"  {icon} #{idx:03d}: {analysis['reason']}"
                f"  (score: {analysis['score']}/6)",
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
                    f"\n📊 {completed}/{total} | "
                    f"✅ {valid_so_far} | ❌ {completed - valid_so_far}\n",
                    flush=True,
                )

    # ── Итог анализа ─────────────────────────────────────────────────────────
    n_invalid    = sum(1 for _, _, a in raw_results.values() if not a["valid"])
    invalid_pct  = n_invalid / total * 100 if total else 0

    print(f"\n{'─'*60}")
    print(f"📊 Результат валидации:")
    print(f"  Всего:  {total}")
    print(f"  Хороших: {total - n_invalid}")
    print(f"  Плохих:  {n_invalid}  ({invalid_pct:.1f}%)")
    print(f"{'─'*60}")

    # ── Выбор стратегии ───────────────────────────────────────────────────────
    replaced_stats = {"replaced": 0, "failed": 0, "sources": {}}

    if invalid_pct > 50:
        print(f"\n🚨 {invalid_pct:.1f}% > 50% — запускаю полную перегенерацию...")
        handle_critical_failure(session, raw_results, segments, prompts, videos_dir)
    else:
        print(f"\n✅ {invalid_pct:.1f}% ≤ 50% — заменяем плохие стоками")
        if n_invalid > 0:
            replaced_stats = handle_partial_replacement(
                session, raw_results, videos_dir, prompts, niche=niche
            )

    # ── Строим финальный отчёт ────────────────────────────────────────────────
    report_videos = []
    for idx, (seg, vp, analysis) in sorted(raw_results.items()):
        report_videos.append({
            "id":            idx,
            "file":          vp.name,
            "valid":         analysis["valid"],
            "score":         analysis["score"],
            "has_artifacts": analysis.get("has_artifacts", False),
            "is_scientific": analysis.get("is_scientific", True),
            "matches_prompt": analysis.get("matches_prompt", True),
            "good_quality":  analysis.get("good_quality", True),
            "exact_object":  analysis.get("exact_object", True),
            "meaningful":    analysis.get("meaningful", True),
            "reason":        analysis["reason"],
        })

    report = {
        "session":          session,
        "total":            total,
        "valid":            total - n_invalid,
        "invalid":          n_invalid,
        "invalid_pct":      round(invalid_pct, 1),
        "replaced_with_stock": replaced_stats["replaced"],
        "failed":           replaced_stats["failed"],
        "stock_sources":    replaced_stats["sources"],
        "videos":           report_videos,
    }

    report_path = TRANSCRIPTS / session / "video_validation_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    elapsed = int(time.time() - t0)
    sources_str = ", ".join(f"{k}: {v}" for k, v in replaced_stats["sources"].items())

    print(f"\n{'='*60}")
    print(f"  ВАЛИДАЦИЯ ЗАВЕРШЕНА  ({elapsed}с)")
    print(f"  📊 Всего:    {total}")
    print(f"  ✅ Хорошие:  {total - n_invalid}")
    print(f"  🔄 Стоки:    {replaced_stats['replaced']}"
          + (f" ({sources_str})" if sources_str else ""))
    print(f"  ❌ Плохие:   {replaced_stats['failed']}")
    print(f"  📁 Отчёт:    {report_path}")
    print(f"{'='*60}\n")

    save_used_stocks(session)

    # ── Нормализация + апскейл ────────────────────────────────────────────────
    print("⏳ Нормализация и апскейл всех видео...")
    try:
        from video_cutter import process_all_videos
        process_all_videos(session)
    except ImportError as e:
        print(f"⚠️ video_cutter не найден, пропускаю апскейл: {e}")

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
    parser.add_argument("--session", default=None, help="Имя сессии")
    args = parser.parse_args()

    session = args.session or find_latest_session()
    if not session:
        print("[!] Нет сессий с видео в data/media/")
        sys.exit(1)

    run(session)
