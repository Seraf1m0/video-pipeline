"""
Быстрый тест Gemini-based recall для 5 немецких сегментов.
Запуск: python agents/library/test_gemini_match.py
"""
import sys, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agents" / "utils"))
sys.path.insert(0, str(ROOT / "agents" / "library"))

import numpy as np
from gemini_embedder import load_library_embeddings, embed_batch

CHANNEL = "channel_001_cosmos_de"
SESSION = "Video_20260415_210049"

# Загрузить библиотечные embeddings
lib_ids, lib_embs = load_library_embeddings(CHANNEL)
lib_index = {cid: i for i, cid in enumerate(lib_ids)}
print(f"Library: {len(lib_ids)} clips, {lib_embs.shape[1]}-dim\n")

# Загрузить библиотеку для описаний
from paths import CHANNELS_DIR, get_lang, get_library_dir
lib_json_path = get_library_dir(CHANNEL).parent / f"library_{CHANNEL.split('_library_')[0].split('channel_001_cosmos')[0]}"
# Правильный путь
from paths import get_library_json
lib_data = json.loads(get_library_json(CHANNEL).read_text(encoding="utf-8"))
clips_meta = lib_data["clips"]

# Загрузить сегменты из result.json
lang = get_lang(CHANNEL)
result_path = CHANNELS_DIR / lang / SESSION / "transcripts" / "result.json"
result = json.loads(result_path.read_text(encoding="utf-8"))
segments = result.get("segments", [])
print(f"Total segments: {len(segments)}")
print()

# Взять первые 5 сегментов для теста
TEST_SEGS = segments[:5]
queries = []
for seg in TEST_SEGS:
    text = seg.get("text", "")
    queries.append(text)

print("Embedding 5 test queries...")
seg_embs = embed_batch(queries)

for i, (seg, seg_emb) in enumerate(zip(TEST_SEGS, seg_embs)):
    text = seg.get("text", "")
    scores = lib_embs @ seg_emb  # cosine similarity
    top_idx = np.argsort(scores)[::-1][:5]
    print(f"\n[Seg {i}] '{text[:80]}'")
    for rank, idx in enumerate(top_idx):
        cid = lib_ids[idx]
        score = float(scores[idx])
        kw = clips_meta.get(cid, {}).get("keywords", "")[:80]
        print(f"  #{rank+1} clip {cid} score={score:.3f} | {kw}")
