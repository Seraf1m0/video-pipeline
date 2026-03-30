"""
Тест Qwen vision на 4 клипа из incoming/cuts.
Ничего не сохраняет — только выводит описания.
"""
import sys
import time
import json
import urllib.request
from pathlib import Path

CUTS_DIR  = Path(__file__).resolve().parent / "library" / "incoming" / "cuts"
SERVER    = "http://127.0.0.1:8765"

# ── Найти 4 клипа ──────────────────────────────────────────────────────────────
clips = []
for pool in sorted(CUTS_DIR.iterdir()):
    if pool.is_dir():
        for f in sorted(pool.glob("cut_*.mp4")):
            clips.append(f)
        if len(clips) >= 4:
            break

clips = clips[:4]
if not clips:
    print("❌ Клипы не найдены в", CUTS_DIR)
    sys.exit(1)

print(f"📂 Найдено клипов: {len(clips)}")
for c in clips:
    print(f"   {c.relative_to(CUTS_DIR.parent.parent)}")

# ── Проверить сервер ───────────────────────────────────────────────────────────
def _check_server():
    try:
        body = json.dumps({"videos": [str(clips[0])]}).encode()
        req = urllib.request.Request(
            f"{SERVER}/analyze_batch",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=5)
        return True
    except Exception as e:
        # Попробуем /health или просто ping
        try:
            urllib.request.urlopen(f"{SERVER}/health", timeout=3)
            return True
        except:
            return False

if not _check_server():
    print(f"\n❌ Vision сервер недоступен на {SERVER}")
    print("   Запусти: py -X utf8 agents/vision/vision_server.py --port 8765")
    sys.exit(1)

print(f"\n✅ Vision сервер доступен: {SERVER}")

# ── Отправить батч ─────────────────────────────────────────────────────────────
print(f"\n🔍 Анализирую {len(clips)} клипов батчем...\n")

t0 = time.time()

body = json.dumps({"video_paths": [str(c) for c in clips]}).encode()
req = urllib.request.Request(
    f"{SERVER}/analyze_batch4",
    data=body,
    headers={"Content-Type": "application/json"},
)

try:
    with urllib.request.urlopen(req, timeout=120) as resp:
        result = json.loads(resp.read())
except Exception as e:
    print(f"❌ Ошибка запроса: {e}")
    sys.exit(1)

elapsed = time.time() - t0

# ── Вывод результата ───────────────────────────────────────────────────────────
print("=" * 60)
print(f"  Время: {elapsed:.1f}s  ({elapsed/len(clips):.1f}s/клип)")
print("=" * 60)

# /analyze_batch4 возвращает {"clip1": {...}, "clip2": {...}, ...}
results = [result.get(f"clip{i+1}", {}) for i in range(len(clips))]

for i, (clip, res) in enumerate(zip(clips, results), 1):
    kw       = res.get("keywords", "—")
    valid    = res.get("valid", False)
    black    = res.get("black_screen", False)
    frozen   = res.get("frozen", False)
    status   = "✅" if valid else ("⬛ чёрный" if black else ("🧊 frozen" if frozen else "❌"))

    print(f"\n[{i}] {clip.name}  {status}")
    print(f"     {kw}")

print("\n" + "=" * 60)
print("  Тест завершён. library.json не изменён.")
print("=" * 60)
