"""
ab_test_matching.py — A/B тест: E5-large vs Gemini на одинаковых сегментах.

Запускает одни и те же 10 сегментов (запуск ракеты) через два пайплайна:
  - E5-large: multilingual-e5-large encode_query → cosine → top-1
  - Gemini:   embedding-001 embed_batch → cosine → top-1

Выводит side-by-side в ab_test_out.txt
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agents" / "utils"))
sys.path.insert(0, str(ROOT / "agents" / "library"))

import numpy as np
from paths import get_library_json

CHANNEL = "channel_001_cosmos_de"

SEGMENTS = [
    {"id": 1,  "start": 0.0,
     "text": "На стартовой площадке тишина. Техники покинули зону.",
     "visual_query": "rocket on launch pad static waiting countdown launchpad empty silent"},

    {"id": 2,  "start": 5.0,
     "text": "Искра, мгновенное воспламенение, клубы густого белого дыма заполняют экран.",
     "visual_query": "rocket ignition liftoff moment white smoke exhaust clouds bottom frame launch"},

    {"id": 3,  "start": 10.0,
     "text": "Вид изнутри кабины. Вибрация, приборы дрожат, всё залито солнечным светом из иллюминатора.",
     "visual_query": "cockpit interior POV astronaut cabin instruments vibration sunlight porthole window inside"},

    {"id": 4,  "start": 15.0,
     "text": "Ракета пронзает облака, уходя вертикально вверх, оставляя за собой инверсионный след.",
     "visual_query": "rocket ascending vertical through clouds contrail trail upward motion dynamic ascent"},

    {"id": 5,  "start": 20.0,
     "text": "Небо темнеет, превращаясь из голубого в глубокий чёрный. Тонкая прослойка атмосферы.",
     "visual_query": "atmosphere edge Earth limb thin blue line horizon darkness space transition high altitude"},

    {"id": 6,  "start": 25.0,
     "text": "Капля воды медленно парит в воздухе, отражая интерьер станции.",
     "visual_query": "water droplet floating microgravity weightlessness ISS interior macro close-up"},

    {"id": 7,  "start": 30.0,
     "text": "Роботизированная рука-манипулятор медленно разворачивает солнечную панель.",
     "visual_query": "robotic arm manipulator solar panel deployment slow motion mechanical ISS Canadarm"},

    {"id": 8,  "start": 35.0,
     "text": "Бесконечные звёздные поля. Млечный путь во всей своей красе, без лишних деталей.",
     "visual_query": "Milky Way deep space star field clean no details pure cosmic background"},

    {"id": 9,  "start": 40.0,
     "text": "Раскалённая капсула входит в плотные слои, окутанная плазменным пламенем.",
     "visual_query": "capsule reentry plasma fire heat shield atmospheric reentry glowing fireball"},

    {"id": 10, "start": 45.0,
     "text": "Парашюты раскрываются в чистом синем небе. Мягкое касание земли.",
     "visual_query": "parachute deployment blue sky capsule landing splashdown touchdown recovery"},
]

# ── Загрузка библиотеки ───────────────────────────────────────────────────────
print("Loading library metadata...", flush=True)
lib_data = json.loads(get_library_json(CHANNEL).read_text(encoding="utf-8"))
clips_meta = lib_data["clips"]

def clip_info(cid):
    e = clips_meta.get(str(cid), {})
    kw = e.get("keywords", "")[:90]
    cat = e.get("category", "")
    return f"[{cid}] {kw} | {cat}"

# ── E5-large ──────────────────────────────────────────────────────────────────
print("Loading E5 embeddings...", flush=True)
from clip_selector import load_library_embeddings, encode_query
e5_ids, e5_embs = load_library_embeddings(CHANNEL)
print(f"  E5: {len(e5_ids)} clips, {e5_embs.shape[1]}-dim", flush=True)

# ── Gemini embeddings ─────────────────────────────────────────────────────────
print("Loading Gemini embeddings...", flush=True)
from gemini_embedder import load_library_embeddings as load_gemini, embed_batch
g_ids, g_embs = load_gemini(CHANNEL)
print(f"  Gemini: {len(g_ids)} clips, {g_embs.shape[1]}-dim", flush=True)

# ── Batch embed visual_queries through Gemini ─────────────────────────────────
queries = [seg["visual_query"] for seg in SEGMENTS]
print(f"Batch embedding {len(queries)} queries via Gemini...", flush=True)
g_seg_embs = embed_batch(queries)
print(f"  Done: {g_seg_embs.shape}", flush=True)

# ── Run both systems ──────────────────────────────────────────────────────────
OUT = open(ROOT / "ab_test_out.txt", "w", encoding="utf-8")

def w(s=""):
    OUT.write(s + "\n")
    print(s, flush=True)

w("=" * 80)
w("A/B ТЕСТ: E5-large vs Gemini embedding-001")
w("СЦЕНАРИЙ: ЗАПУСК РАКЕТЫ — 10 сегментов")
w("=" * 80)

for i, seg in enumerate(SEGMENTS):
    query = seg["visual_query"]

    # ── E5 match ──────────────────────────────────────────────────────────────
    e5_vec = encode_query(query)
    e5_vec = e5_vec / (np.linalg.norm(e5_vec) + 1e-9)
    e5_scores = e5_embs @ e5_vec
    e5_top_idx = np.argsort(e5_scores)[::-1][:5]
    e5_top = [(e5_ids[idx], float(e5_scores[idx])) for idx in e5_top_idx]

    # ── Gemini match ──────────────────────────────────────────────────────────
    g_scores = g_embs @ g_seg_embs[i]
    g_top_idx = np.argsort(g_scores)[::-1][:5]
    g_top = [(g_ids[idx], float(g_scores[idx])) for idx in g_top_idx]

    w()
    w(f"[{seg['id']:02d}] {seg['start']:.0f}s  \"{seg['text'][:65]}\"")
    w(f"     QUERY: {query[:80]}")
    w()

    # Top-5 side by side
    w(f"  {'E5-large':^45}  {'Gemini emb-001':^45}")
    w(f"  {'-'*45}  {'-'*45}")
    for rank in range(5):
        e_cid, e_sc = e5_top[rank]
        g_cid, g_sc = g_top[rank]
        e_info = clip_info(e_cid)[:43]
        g_info = clip_info(g_cid)[:43]
        marker_e = " <" if rank == 0 else "  "
        marker_g = " <" if rank == 0 else "  "
        w(f"  #{rank+1} {e_sc:.3f} {e_info:<43}{marker_e}  #{rank+1} {g_sc:.3f} {g_info:<43}{marker_g}")

    # Margin comparison
    e5_margin = e5_top[0][1] - e5_top[1][1]
    g_margin  = g_top[0][1]  - g_top[1][1]
    w()
    w(f"     margin: E5={e5_margin:.4f}  Gemini={g_margin:.4f}  "
      f"({'Gemini уверенней' if g_margin > e5_margin else 'E5 уверенней' if e5_margin > g_margin else 'равно'})")

    # Same winner?
    same = (str(e5_top[0][0]) == str(g_top[0][0]))
    w(f"     winner: {'СОВПАДАЕТ' if same else 'РАЗНЫЙ'}")

w()
w("=" * 80)

# ── Summary ───────────────────────────────────────────────────────────────────
e5_wins = 0
g_wins  = 0
same_count = 0
for i, seg in enumerate(SEGMENTS):
    query = seg["visual_query"]
    e5_vec = encode_query(query)
    e5_vec = e5_vec / (np.linalg.norm(e5_vec) + 1e-9)
    e5_scores = e5_embs @ e5_vec
    e5_best = e5_ids[int(np.argmax(e5_scores))]

    g_scores = g_embs @ g_seg_embs[i]
    g_best = g_ids[int(np.argmax(g_scores))]

    if str(e5_best) == str(g_best):
        same_count += 1

w(f"Совпадение топ-1: {same_count}/{len(SEGMENTS)}")
w(f"Разных результатов: {len(SEGMENTS) - same_count}/{len(SEGMENTS)}")
w()

OUT.close()
print(f"\nWritten to ab_test_out.txt")
