"""
Тест Gemini matching — последовательность запуска ракеты.
Пишет результат в test_matching_demo_out.txt
"""
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agents" / "utils"))
sys.path.insert(0, str(ROOT / "agents" / "library"))

import numpy as np
from gemini_embedder import load_library_embeddings, embed_batch
from gemini_reranker import rerank_one
from paths import get_library_json

CHANNEL = "channel_001_cosmos_de"
TOPIC_SHIFT_THRESHOLD = 0.72

SEGMENTS = [
    {"id": 1,  "start": 0.0,   "end": 5.0,
     "text": "На стартовой площадке тишина. Техники покинули зону.",
     "visual_query": "rocket on launch pad static waiting countdown launchpad empty silent"},

    {"id": 2,  "start": 5.0,   "end": 10.0,
     "text": "Искра, мгновенное воспламенение, клубы густого белого дыма заполняют экран.",
     "visual_query": "rocket ignition liftoff moment white smoke exhaust clouds bottom frame launch"},

    {"id": 3,  "start": 10.0,  "end": 15.0,
     "text": "Вид изнутри кабины. Вибрация, приборы дрожат, всё залито солнечным светом из иллюминатора.",
     "visual_query": "cockpit interior POV astronaut cabin instruments vibration sunlight porthole window inside"},

    {"id": 4,  "start": 15.0,  "end": 20.0,
     "text": "Ракета пронзает облака, уходя вертикально вверх, оставляя за собой инверсионный след.",
     "visual_query": "rocket ascending vertical through clouds contrail trail upward motion dynamic ascent"},

    {"id": 5,  "start": 20.0,  "end": 25.0,
     "text": "Небо темнеет, превращаясь из голубого в глубокий чёрный. Тонкая прослойка атмосферы.",
     "visual_query": "atmosphere edge Earth limb thin blue line horizon darkness space transition high altitude"},

    {"id": 6,  "start": 25.0,  "end": 30.0,
     "text": "Капля воды медленно парит в воздухе, отражая интерьер станции.",
     "visual_query": "water droplet floating microgravity weightlessness ISS interior macro close-up"},

    {"id": 7,  "start": 30.0,  "end": 35.0,
     "text": "Роботизированная рука-манипулятор медленно разворачивает солнечную панель.",
     "visual_query": "robotic arm manipulator solar panel deployment slow motion mechanical ISS Canadarm"},

    {"id": 8,  "start": 35.0,  "end": 40.0,
     "text": "Бесконечные звёздные поля. Млечный путь во всей своей красе, без лишних деталей.",
     "visual_query": "Milky Way deep space star field clean no details pure cosmic background"},

    {"id": 9,  "start": 40.0,  "end": 45.0,
     "text": "Раскалённая капсула входит в плотные слои, окутанная плазменным пламенем.",
     "visual_query": "capsule reentry plasma fire heat shield atmospheric reentry glowing fireball"},

    {"id": 10, "start": 45.0,  "end": 50.0,
     "text": "Парашюты раскрываются в чистом синем небе. Мягкое касание земли.",
     "visual_query": "parachute deployment blue sky capsule landing splashdown touchdown recovery"},
]

# ── Загрузка ─────────────────────────────────────────────────────────────────
print("Loading...", flush=True)
lib_ids, lib_embs = load_library_embeddings(CHANNEL)
lib_data = json.loads(get_library_json(CHANNEL).read_text(encoding="utf-8"))
clips_meta = lib_data["clips"]

def clip_info(cid):
    e = clips_meta.get(str(cid), {})
    kw = e.get("keywords", "")[:100]
    cat = e.get("category", "")
    return f"[{cid}] {kw} | {cat}"

# ── Batch embed visual_queries ────────────────────────────────────────────────
queries = [seg["visual_query"] for seg in SEGMENTS]
print(f"Batch embedding {len(queries)} queries...", flush=True)
seg_embs = embed_batch(queries)
print(f"Done: {seg_embs.shape}", flush=True)

# ── Matching ──────────────────────────────────────────────────────────────────
OUT = open(ROOT / "test_matching_demo_out.txt", "w", encoding="utf-8")

def w(s=""):
    OUT.write(s + "\n")
    print(s, flush=True)

w("=" * 70)
w("ТЕСТ: ЗАПУСК РАКЕТЫ — 10 сегментов")
w("=" * 70)

prev_clip_desc = ""

for i, seg in enumerate(SEGMENTS):
    scores  = lib_embs @ seg_embs[i]
    top_idx = np.argsort(scores)[::-1][:20]
    top_cands = [(lib_ids[idx], float(scores[idx])) for idx in top_idx]
    margin = top_cands[0][1] - top_cands[1][1] if len(top_cands) >= 2 else 1.0

    topic_changed = False
    topic_sim = None
    if i > 0:
        topic_sim = float(seg_embs[i] @ seg_embs[i - 1])
        topic_changed = topic_sim < TOPIC_SHIFT_THRESHOLD

    w()
    w(f"[{seg['id']:02d}] {seg['start']:.0f}s  \"{seg['text'][:60]}\"")
    w(f"     QUERY: {seg['visual_query'][:80]}")
    if topic_sim is not None:
        mark = " [SHIFT]" if topic_changed else ""
        w(f"     sim_prev={topic_sim:.3f}{mark}")
    w(f"     Top-5:")
    for rank, (cid, score) in enumerate(top_cands[:5]):
        w(f"       #{rank+1} {score:.3f}  {clip_info(cid)}")
    w(f"     margin={margin:.4f}")

    flash_cands = [(cid, clips_meta.get(cid, {}).get("keywords", "")) for cid, _ in top_cands[:20]]
    effective_prev = "" if topic_changed else prev_clip_desc
    flash_winner = rerank_one(seg["text"], flash_cands, effective_prev, topic_changed)
    w(f"     => WINNER: {clip_info(flash_winner)}")

    prev_clip_desc = clips_meta.get(flash_winner, {}).get("keywords", "")[:120]

w()
w("=" * 70)
OUT.close()
print(f"\nWritten to test_matching_demo_out.txt")
