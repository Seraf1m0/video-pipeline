"""
BLIP fix — анализ upscaled/video_010..097, замена плохих стоками.
Модель загружается один раз, всё выполняется инлайн.
"""
import json
import sys
import subprocess
import shutil
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR    = Path(__file__).parent
MEDIA_DIR   = BASE_DIR / "data" / "media"
TRANSCRIPTS = BASE_DIR / "data" / "transcripts"
PROMPTS_DIR = BASE_DIR / "data" / "prompts"
SESSION     = "Video_20260311_170905"

sys.path.insert(0, str(BASE_DIR / "agents" / "video_validator"))
sys.path.insert(0, str(BASE_DIR / "agents" / "blip_validator"))

from blip_validator import load_blip, analyze_video
from stock_finder   import find_stock_video, download_and_verify

upscaled_dir = MEDIA_DIR / SESSION / "upscaled"

# ── Загружаем сегменты и промпты ───────────────────────────────────────────────

raw = json.loads((TRANSCRIPTS / SESSION / "result.json").read_text(encoding="utf-8"))
segs = raw["segments"] if isinstance(raw, dict) else raw
seg_map = {s["id"]: s for s in segs}

prompts_data = json.loads(
    (PROMPTS_DIR / SESSION / "video" / "video_prompts.json").read_text(encoding="utf-8")
)
if isinstance(prompts_data, list):
    prompts_map = {item["id"]: item for item in prompts_data}
else:
    prompts_map = {int(k): v for k, v in prompts_data.items()}

# ── Список видео для анализа (010–097, без интро) ──────────────────────────────

video_files = sorted(
    [
        p for p in upscaled_dir.glob("video_*.mp4")
        if "_original" not in p.stem
    ],
    key=lambda p: p.stem,
)
# Только 010–097
video_files = [
    p for p in video_files
    if (m := __import__("re").search(r"video_(\d+)", p.stem)) and 10 <= int(m.group(1)) <= 97
]

print(f"[BLIP] Сессия  : {SESSION}")
print(f"[BLIP] Видео   : {len(video_files)} (upscaled/video_010–097)")
print()

# ── Загружаем BLIP один раз ────────────────────────────────────────────────────

processor, model, device = load_blip()

# ── Анализ ─────────────────────────────────────────────────────────────────────

bad_ids: list[int] = []
results: list[dict] = []

import re as _re

t_start = time.time()
total   = len(video_files)

for i, video_path in enumerate(video_files, 1):
    m        = _re.search(r"video_(\d+)", video_path.stem)
    vid_id   = int(m.group(1)) if m else i
    seg      = seg_map.get(vid_id, {})
    p_data   = prompts_map.get(vid_id, {})
    prompt   = p_data.get("video_prompt") or p_data.get("prompt", "")

    r = analyze_video(video_path, prompt, processor, model, device, threshold=0.05)

    elapsed = time.time() - t_start
    speed   = i / elapsed if elapsed > 0 else 0.0
    eta     = int((total - i) / speed) if speed > 0 else 0

    mark = "✓" if r["match"] else "✗"
    print(
        f"  [{i:02d}/{total}] {mark} #{vid_id:03d}  score={r['avg_score']:.3f}"
        + (f"  issues={r['issues']}" if r["issues"] else "")
        + f"  ETA~{eta}s"
    )

    results.append({
        "id":    vid_id,
        "file":  video_path.name,
        "match": r["match"],
        "score": r["avg_score"],
        "issues": r["issues"],
    })

    if not r["match"]:
        bad_ids.append(vid_id)

# ── Итог анализа ───────────────────────────────────────────────────────────────

ok_count  = total - len(bad_ids)
print()
print("=" * 60)
print(f"  BLIP АНАЛИЗ ЗАВЕРШЁН")
print(f"  ✅ Хороших : {ok_count}/{total}")
print(f"  ❌ Плохих  : {len(bad_ids)}")
if bad_ids:
    print(f"  Плохие IDs : {bad_ids}")
print("=" * 60)

# Сохраняем отчёт
report_path = MEDIA_DIR / SESSION / "blip_upscaled_report.json"
report_path.write_text(
    json.dumps({"session": SESSION, "total": total, "bad": bad_ids, "results": results},
               ensure_ascii=False, indent=2),
    encoding="utf-8"
)
print(f"  📄 Отчёт: {report_path}")


def normalize_video(src: Path, dst: Path) -> bool:
    """Trim to 10s, scale 1920x1080, NVENC."""
    tmp = BASE_DIR / "temp" / f"fix_{src.stem}.mp4"
    tmp.parent.mkdir(exist_ok=True)
    r = subprocess.run([
        "ffmpeg", "-y", "-i", str(src),
        "-ss", "0", "-t", "10",
        "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,"
               "pad=1920:1080:(ow-iw)/2:(oh-ih)/2",
        "-c:v", "h264_nvenc", "-preset", "p4", "-cq", "19",
        "-pix_fmt", "yuv420p", "-an",
        str(tmp),
    ], capture_output=True)
    if r.returncode == 0 and tmp.exists() and tmp.stat().st_size > 100_000:
        tmp.replace(dst)
        return True
    tmp.unlink(missing_ok=True)
    return False


# ── Замена плохих стоками ──────────────────────────────────────────────────────

if bad_ids:
    print(f"\n📦 Заменяю {len(bad_ids)} плохих видео стоками...")

    replaced = []
    failed   = []

    for vid_id in bad_ids:
        seg      = seg_map.get(vid_id, {})
        text     = seg.get("text", "")
        p_data   = prompts_map.get(vid_id, {})
        prompt   = p_data.get("video_prompt") or p_data.get("prompt", "")
        up_path  = upscaled_dir / f"video_{vid_id:03d}.mp4"

        print(f"\n  🔍 #{vid_id:03d}: {text[:60]}")

        stock = find_stock_video(
            segment_text=text,
            prompt_text=prompt,
            niche="science",
            min_duration=10
        )

        if not stock:
            print(f"  ❌ #{vid_id:03d}: сток не найден")
            failed.append(vid_id)
            continue

        # Резервная копия
        backup = upscaled_dir / f"video_{vid_id:03d}_original.mp4"
        if up_path.exists():
            up_path.rename(backup)

        raw_dl = BASE_DIR / "temp" / f"stock_raw_{vid_id:03d}.mp4"
        raw_dl.parent.mkdir(exist_ok=True)

        ok = download_and_verify(stock, raw_dl)
        if ok:
            if normalize_video(raw_dl, up_path):
                raw_dl.unlink(missing_ok=True)
                print(f"  ✅ #{vid_id:03d}: заменён ({stock['source']}) → {up_path.name}")
                replaced.append(vid_id)
            else:
                # Fallback: просто скопировать без нормализации
                shutil.copy2(raw_dl, up_path)
                raw_dl.unlink(missing_ok=True)
                print(f"  ✅ #{vid_id:03d}: заменён без нормализации ({stock['source']})")
                replaced.append(vid_id)
        else:
            # Вернуть оригинал
            if backup.exists():
                backup.rename(up_path)
            print(f"  ❌ #{vid_id:03d}: ошибка скачивания")
            failed.append(vid_id)

    print()
    print("=" * 60)
    print(f"  ЗАМЕНА ЗАВЕРШЕНА")
    print(f"  ✅ Заменено: {len(replaced)}  {replaced}")
    print(f"  ❌ Не найдено: {len(failed)}  {failed}")
    print("=" * 60)
else:
    print("\n✅ Все видео прошли BLIP — замена не нужна.")

print("\nГОТОВО! Теперь запустить монтаж:")
print(f"  py agents/assembler/assembler.py --session {SESSION}")
