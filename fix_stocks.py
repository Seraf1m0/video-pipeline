"""
Прямая замена плохих видео стоками по существующему validation report.
Не требует Vision-сервера.

Использование:
  py fix_stocks.py <channel_id> <session> [id1,id2,id3...]
  py fix_stocks.py channel_001_cosmos_de Video_20260316_200046
  py fix_stocks.py channel_001_cosmos_de Video_20260316_200046 63,64,65,66,76
"""

import json
import sys
import shutil

# Windows: UTF-8 stdout
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

BASE_DIR = Path(__file__).parent

# ── Аргументы ────────────────────────────────────────────────────────────────
if len(sys.argv) < 3:
    print("Usage: py fix_stocks.py <channel_id> <session> [id1,id2,id3...]")
    sys.exit(1)

channel_id  = sys.argv[1]
session     = sys.argv[2]
# Опциональный список конкретных ID для замены (переопределяет отчёт)
forced_ids = None
if len(sys.argv) >= 4:
    forced_ids = [int(x) for x in sys.argv[3].split(",") if x.strip().isdigit()]

# ── Пути из конфига канала ────────────────────────────────────────────────────
cfg_path = BASE_DIR / "bot" / "channels" / channel_id / "config.json"
cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
subdir = cfg.get("data_subdir", "")
ch_base = BASE_DIR / "data" / subdir if subdir else BASE_DIR / "data"

videos_dir   = ch_base / "media"   / session / "videos"
transcripts  = ch_base / "transcripts" / session
prompts_dir  = ch_base / "prompts" / session
report_path  = transcripts / "video_validation_report.json"

# ── Загрузить sys.path для stock_finder ──────────────────────────────────────
import os
sys.path.insert(0, str(BASE_DIR / "agents" / "video_validator"))

# Load .env
from dotenv import load_dotenv
load_dotenv(BASE_DIR / "config" / ".env")

from stock_finder import (
    find_stock_video,
    download_and_verify,
    load_used_stocks,
    save_used_stocks,
    _used_stock_ids,
    _used_stock_urls,
)

# ── Загрузить отчёт ──────────────────────────────────────────────────────────
report = json.loads(report_path.read_text(encoding="utf-8"))
all_videos_map = {v["id"]: v for v in report["videos"]}

# ── Загрузить сегменты ───────────────────────────────────────────────────────
result_json = transcripts / "result.json"
segs_raw = json.loads(result_json.read_text(encoding="utf-8"))
segments  = segs_raw["segments"] if isinstance(segs_raw, dict) else segs_raw
seg_map   = {s["id"]: s for s in segments}

# ── Определить список видео для замены ───────────────────────────────────────
if forced_ids:
    # Явный список — включаем даже "valid" видео (ручная коррекция)
    invalid_videos = []
    for fid in forced_ids:
        if fid in all_videos_map:
            invalid_videos.append(all_videos_map[fid])
        else:
            # ID есть в папке, но нет в отчёте — создаём запись
            seg = seg_map.get(fid, {})
            invalid_videos.append({
                "id":     fid,
                "valid":  False,
                "reason": "Принудительная замена",
                "file":   f"video_{fid:03d}.mp4",
            })
    print(f"  Принудительная замена {len(invalid_videos)} видео: {forced_ids}")
else:
    invalid_videos = [v for v in report["videos"] if not v["valid"]]

# ── Загрузить историю стоков ─────────────────────────────────────────────────
load_used_stocks(session)

niche = cfg.get("validator", {}).get("niche", "cosmos")

# ── Загрузить видео-промпты (для точных запросов) ────────────────────────────
_video_prompts: dict[int, str] = {}
for _p in [
    prompts_dir / "video" / "video_prompts.json",
    prompts_dir / "video_prompts.json",
]:
    if _p.exists():
        _raw = json.loads(_p.read_text(encoding="utf-8"))
        _list = _raw if isinstance(_raw, list) else []
        for _item in _list:
            _vp = _item.get("video_prompt") or _item.get("prompt", "")
            if _vp:
                _video_prompts[_item["id"]] = _vp
        print(f"  📋 Загружено {len(_video_prompts)} видео-промптов")
        break

print(f"\n{'='*60}")
print(f"  STOCK REPLACEMENT")
print(f"  Канал:   {channel_id}")
print(f"  Сессия:  {session}")
print(f"  Видео:   {len(invalid_videos)}")
print(f"  Niche:   {niche}")
print(f"{'='*60}\n")

stats = {"replaced": 0, "failed": 0}

def replace_one(v: dict):
    idx  = v["id"]
    vp   = videos_dir / f"video_{idx:03d}.mp4"
    seg  = seg_map.get(idx, {})
    text        = seg.get("text", "")
    prompt_text = _video_prompts.get(idx, "")

    print(f"  🔍 #{idx:03d}: {text[:60]}")
    print(f"       Причина: {v.get('reason','?')[:80]}")

    if not vp.exists():
        print(f"       ⚠️ Файл не найден: {vp.name}")
        return idx, False

    stock = find_stock_video(
        segment_text=text,
        prompt_text=prompt_text,
        niche=niche,
        min_duration=10,
        segment_idx=idx,
    )
    if stock:
        backup = vp.parent / f"video_{idx:03d}_original.mp4"
        if backup.exists():
            backup.unlink()  # удаляем старый бэкап
        vp.rename(backup)

        local_path = stock.get("local_path")
        if local_path and Path(local_path).exists():
            shutil.move(local_path, vp)
            success = True
        else:
            success = download_and_verify(stock, vp)

        if success:
            backup.unlink(missing_ok=True)
            print(f"       ✅ Заменено: {stock['source']} | '{stock.get('query','')}'")
            return idx, True
        else:
            backup.rename(vp)

    print(f"       ❌ Сток не найден — оставляю оригинал")
    return idx, False

MAX_WORKERS = 5
done = 0
with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
    futures = {ex.submit(replace_one, v): v for v in invalid_videos}
    for fut in as_completed(futures):
        idx, ok = fut.result()
        done += 1
        if ok:
            stats["replaced"] += 1
        else:
            stats["failed"] += 1
        print(f"  [{done}/{len(invalid_videos)}] {'✅' if ok else '❌'} #{idx:03d}")

save_used_stocks(session)

print(f"\n{'='*60}")
print(f"  ГОТОВО: заменено {stats['replaced']}/{len(invalid_videos)}")
print(f"  Провалено: {stats['failed']}")
print(f"{'='*60}\n")
