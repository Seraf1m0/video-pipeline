"""
FR stock replacement + Grok regen для плохих видео.
Читает blip_report.json, для каждого плохого видео:
  1. Пробует найти сток (Pixabay/Pexels)
  2. Если не нашёл → добавляет в список на Grok-регенерацию
"""
import sys, json, shutil, subprocess
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE     = Path(__file__).resolve().parent
SESSION  = "Video_20260316_162629"
VIDEOS   = BASE / "data/channels/fr/media" / SESSION / "videos"
PROMPTS  = BASE / "data/channels/fr/prompts" / SESSION / "video"
REPORT   = BASE / "data/channels/fr/media"   / SESSION / "blip_report.json"
PROGRESS = BASE / "temp/grok_progress.json"

sys.path.insert(0, str(BASE / "agents/video_validator"))

# ── Загружаем плохие видео ─────────────────────────────────────────────────
report   = json.loads(REPORT.read_text(encoding="utf-8"))
# Объединяем BLIP-bad + вручную выявленных по captions
_blip_bad = report["summary"]["bad_videos"]
_manual_bad = []
bad_ids = sorted(set(_blip_bad + _manual_bad))
print(f"Плохих видео: {len(bad_ids)} → {bad_ids}")

# ── Загружаем промпты ──────────────────────────────────────────────────────
pf = PROMPTS / "video_prompts.json"
if not pf.exists():
    pf = BASE / "data/channels/fr/prompts" / SESSION / "video_prompts.json"
prompts_raw = json.loads(pf.read_text(encoding="utf-8")) if pf.exists() else {}
# prompts_raw: либо dict {id: {prompt...}}, либо list [{id, prompt...}]
if isinstance(prompts_raw, list):
    prompts = {str(item["id"]): item for item in prompts_raw}
else:
    prompts = prompts_raw

# ── Загружаем транскрипцию для текста сегментов ────────────────────────────
tr_file = BASE / "data/channels/fr/transcripts" / SESSION / "result.json"
seg_map = {}
if tr_file.exists():
    data = json.loads(tr_file.read_text(encoding="utf-8"))
    segs = data.get("segments", []) if isinstance(data, dict) else data
    for s in segs:
        seg_map[s["id"]] = s

# ── Импортируем stock_finder ───────────────────────────────────────────────
from stock_finder import find_stock_video, load_used_stocks, save_used_stocks
load_used_stocks(SESSION)

to_regen  = []
replaced  = []
failed    = []

for vid_id in bad_ids:
    video_path = VIDEOS / f"video_{vid_id:03d}.mp4"
    seg = seg_map.get(vid_id, {})
    text = seg.get("text", "")
    p_data = prompts.get(str(vid_id), {})
    prompt = p_data.get("video_prompt") or p_data.get("prompt") or p_data.get("text") or ""

    print(f"\n🔍 #{vid_id}: '{text[:60]}'")

    stock = find_stock_video(
        segment_text=text,
        prompt_text=prompt,
        niche="cosmos",
        min_duration=10,
        segment_idx=vid_id,
    )

    if stock:
        local_path = stock.get("local_path")
        backup = VIDEOS / f"video_{vid_id:03d}_original.mp4"
        if video_path.exists():
            video_path.rename(backup)
        if local_path and Path(local_path).exists():
            shutil.move(local_path, video_path)
            try: backup.unlink(missing_ok=True)
            except: pass
            print(f"  ✅ Заменён стоком [{stock['source']}]")
            replaced.append(vid_id)
        else:
            # fallback download
            from stock_finder import download_and_verify
            ok = download_and_verify(stock, video_path)
            if ok:
                try: backup.unlink(missing_ok=True)
                except: pass
                print(f"  ✅ Скачан и заменён [{stock['source']}]")
                replaced.append(vid_id)
            else:
                if backup.exists(): backup.rename(video_path)
                print(f"  ❌ Скачать не удалось → в очередь Grok")
                to_regen.append(vid_id)
    else:
        print(f"  ❌ Сток не найден → в очередь Grok")
        to_regen.append(vid_id)

save_used_stocks(SESSION)

print(f"\n{'='*50}")
print(f"✅ Заменено стоками: {len(replaced)} → {replaced}")
print(f"🔄 На регенерацию Grok: {len(to_regen)} → {to_regen}")
print(f"{'='*50}")

# ── Grok регенерация ───────────────────────────────────────────────────────
if to_regen:
    print(f"\n🚀 Запускаю Grok рег enerацию для {len(to_regen)} видео...")

    # Убираем эти ID из grok_progress.json чтобы Grok их перегенерировал
    if PROGRESS.exists():
        data = json.loads(PROGRESS.read_text(encoding="utf-8"))
        before = len(data.get("completed", []))
        data["completed"] = [x for x in data.get("completed", []) if x not in to_regen]
        PROGRESS.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  grok_progress: {before} → {len(data['completed'])} (убрано {before - len(data['completed'])})")

    result = subprocess.run(
        [sys.executable, "agents/media_generator/media_generator.py",
         "--type", "video", "--session", SESSION],
        cwd=str(BASE), capture_output=False
    )
    print(f"\nGrok завершён с кодом {result.returncode}")
else:
    print("\n✅ Все плохие видео заменены стоками, Grok не нужен!")
