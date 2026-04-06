"""
Тест индексации cosmos библиотеки — 20 клипов.
Проверяет:
  - скорость (клипов/сек)
  - качество keywords от Qwen (новый промпт)
  - процент валидных / rejected
Результат никуда не сохраняется (in-memory only).
"""
import sys, time, random
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from pathlib import Path

sys.path.insert(0, "C:/Projects/Video-pipeline/agents/library")
sys.path.insert(0, "C:/Projects/Video-pipeline/agents/utils")
sys.path.insert(0, "C:/Projects/Video-pipeline/agents")

from vision.vision_client import analyze_batch, get_available_servers

CLIPS_DIR = Path("D:/Video-pipeline-library/library_cosmos/clips")
N_TEST    = 20   # сколько клипов тестировать
BATCH     = 4    # Qwen батч

# ── Взять N случайных клипов ──────────────────────────────────────────────────
all_clips = sorted(CLIPS_DIR.glob("*.mp4"))
print(f"Всего клипов в библиотеке: {len(all_clips)}")

random.seed(42)
test_clips = random.sample(all_clips, min(N_TEST, len(all_clips)))
test_clips.sort()
print(f"Тестируем: {len(test_clips)} клипов (случайная выборка)\n")

# ── Получить серверы ──────────────────────────────────────────────────────────
servers = get_available_servers()
if not servers:
    print("❌ Vision серверы не запущены! Запусти start_servers.bat")
    sys.exit(1)
print(f"Vision серверов: {len(servers)} -> {servers}\n")
print("=" * 70)

# ── Тест батчами по 4 ─────────────────────────────────────────────────────────
results = []
t_start = time.time()

for i in range(0, len(test_clips), BATCH):
    batch = test_clips[i:i + BATCH]
    paths = [str(p) for p in batch]

    # дополнить до 4
    p1 = paths[0]
    p2 = paths[1] if len(paths) > 1 else ""
    p3 = paths[2] if len(paths) > 2 else ""
    p4 = paths[3] if len(paths) > 3 else ""

    t_batch = time.time()
    server = servers[(i // BATCH) % len(servers)]
    resp = analyze_batch(p1, p2, p3, p4, server_url=server, niche="cosmos")
    elapsed = time.time() - t_batch

    print(f"Батч {i//BATCH + 1} ({elapsed:.1f}s | {len(batch)/elapsed:.1f} клип/с):")
    for j, clip_path in enumerate(batch):
        key = f"clip{j+1}"
        r = resp.get(key, {}) if resp else {}
        kw    = r.get("keywords", "")
        valid = r.get("valid", False)
        icon  = "✅" if valid and kw else "❌"
        print(f"  {icon} {clip_path.name}: {kw[:100]}")
        results.append({"file": clip_path.name, "keywords": kw, "valid": valid})
    print()

total_time = time.time() - t_start

# ── Итоги ─────────────────────────────────────────────────────────────────────
print("=" * 70)
valid   = sum(1 for r in results if r["valid"] and r["keywords"])
invalid = len(results) - valid
kw_lens = [len(r["keywords"].split()) for r in results if r["keywords"]]
avg_words = sum(kw_lens) / len(kw_lens) if kw_lens else 0

print(f"Итого: {len(results)} клипов за {total_time:.1f}s")
print(f"  ✅ Валидных:  {valid}/{len(results)}")
print(f"  ❌ Rejected:  {invalid}/{len(results)}")
print(f"  Скорость:    {len(results)/total_time:.2f} клип/с  ({total_time/len(results):.1f}s/клип)")
print(f"  Слов в keywords: avg={avg_words:.1f}  min={min(kw_lens) if kw_lens else 0}  max={max(kw_lens) if kw_lens else 0}")
print(f"\n  Ожидаемое время на 5598 клипов: {5598 * (total_time/len(results)) / 3600:.1f}ч (1 сервер)")
print(f"  С 4 серверами: {5598 * (total_time/len(results)) / 3600 / 4:.1f}ч")
