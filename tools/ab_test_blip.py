"""
ab_test_blip.py — A/B тест: CLIP-only vs CLIP + BLIP VQA reranker.

A = топ-8 по CLIP (текущее поведение)
B = топ-20 по CLIP → BLIP yes/no → финальный список

Запуск:
    py tools/ab_test_blip.py --channel channel_001_cosmos_de
"""
import sys
import json
import argparse
import random
from pathlib import Path

import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_utils_dir = Path(__file__).resolve().parent.parent / "agents" / "utils"
_lib_dir   = Path(__file__).resolve().parent.parent / "agents" / "library"
if str(_utils_dir) not in sys.path:
    sys.path.insert(0, str(_utils_dir))
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from paths import get_library_json, get_library_dir
from visual_embedder import encode_text, load_visual_embeddings

# Тестовые visual_query — типичные запросы от visual_query_generator
TEST_QUERIES = [
    ("named",    "James Webb Space Telescope JWST deep field infrared"),
    ("named",    "Hubble Space Telescope galaxy photograph"),
    ("named",    "International Space Station ISS orbit Earth"),
    ("named",    "Perseverance rover Mars red surface"),
    ("named",    "Falcon 9 SpaceX rocket launch"),
    ("object",   "telescope observatory night sky stars"),
    ("object",   "rocket launch fire smoke exhaust"),
    ("object",   "astronaut spacesuit floating"),
    ("object",   "solar system planets orbit animation"),
    ("object",   "satellite in orbit Earth view"),
    ("abstract", "dark matter cosmic web filaments"),
    ("abstract", "black hole accretion disk glowing"),
    ("abstract", "universe expansion big bang"),
    ("abstract", "nebula colorful gas dust clouds"),
    ("abstract", "stars milky way galaxy"),
]

TOP_CLIP  = 8    # A: сколько берёт CLIP-only
TOP_BLIP  = 20   # B: сколько подаём на BLIP


def get_top_by_clip(embs_3d, clip_ids, query_vec, top_n):
    """MAX-frame CLIP scoring → топ N clip_ids."""
    scores = (embs_3d @ query_vec).max(axis=1)
    idxs   = np.argsort(scores)[::-1][:top_n]
    return [(clip_ids[i], float(scores[i])) for i in idxs]


def blip_filter(candidates, question, thumb_dir, processor, model, device):
    """BLIP VQA: вернуть только yes-клипы."""
    from PIL import Image
    import torch

    yes_clips = []
    no_clips  = []
    for cid, score in candidates:
        jpg = thumb_dir / f"{cid}.jpg"
        if not jpg.exists():
            yes_clips.append((cid, score))
            continue
        try:
            img    = Image.open(jpg).convert("RGB")
            inputs = processor(img, question, return_tensors="pt").to(device)
            with torch.no_grad():
                out = model.generate(**inputs)
            answer = processor.decode(out[0], skip_special_tokens=True).strip().lower()
            if answer.startswith("yes"):
                yes_clips.append((cid, score))
            else:
                no_clips.append((cid, score))
        except Exception:
            yes_clips.append((cid, score))

    return yes_clips, no_clips


def run(channel_id: str):
    import torch
    from transformers import BlipProcessor, BlipForQuestionAnswering

    lib_json  = get_library_json(channel_id)
    lib_dir   = get_library_dir(channel_id)
    thumb_dir = lib_dir / "thumbnails"

    with open(lib_json, encoding="utf-8") as f:
        library = json.load(f)
    clips_data = library["clips"]

    result = load_visual_embeddings(channel_id)
    if result is None:
        print("❌ visual_embeddings.npz не найден"); return
    clip_ids, embs = result
    if embs.ndim != 3:
        print("❌ Нужен 3D формат [N,5,768]"); return

    print("⏳ Загружаю BLIP...", flush=True)
    processor = BlipProcessor.from_pretrained("Salesforce/blip-vqa-base")
    model     = BlipForQuestionAnswering.from_pretrained("Salesforce/blip-vqa-base")
    model.eval()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model  = model.to(device)
    print(f"✅ BLIP на {device}\n")

    # Метрики
    new_in_b      = 0   # клипов которые появились в B но не были в A
    filtered_out  = 0   # клипов которые A выбрал но BLIP отфильтровал
    total_yes     = 0
    total_no      = 0
    all_no_cases  = 0   # случаев когда BLIP сказал "no" на всё → fallback

    print(f"{'='*72}")
    print(f"  A/B TEST: CLIP-only (top-{TOP_CLIP}) vs CLIP+BLIP (top-{TOP_BLIP}→filter)")
    print(f"{'='*72}\n")

    for qtype, query in TEST_QUERIES:
        q_words  = query.strip().split()[:8]
        question = "Is there " + " ".join(q_words) + " in this image?"

        q_vec = encode_text(query)

        # A: топ-8 по CLIP
        top_a = get_top_by_clip(embs, clip_ids, q_vec, TOP_CLIP)

        # B: топ-20 по CLIP → BLIP filter
        top_20 = get_top_by_clip(embs, clip_ids, q_vec, TOP_BLIP)
        yes_clips, no_clips = blip_filter(top_20, question, thumb_dir, processor, model, device)

        total_yes += len(yes_clips)
        total_no  += len(no_clips)

        # Fallback если все no
        if not yes_clips:
            top_b = top_20[:TOP_CLIP]
            all_no_cases += 1
            fallback = " [FALLBACK]"
        else:
            top_b    = yes_clips[:TOP_CLIP]
            fallback = ""

        set_a = {c for c, _ in top_a}
        set_b = {c for c, _ in top_b}
        only_b = set_b - set_a
        only_a = set_a - set_b

        new_in_b     += len(only_b)
        filtered_out += len(only_a)

        print(f"[{qtype.upper()}] \"{query[:55]}\"")
        print(f"  BLIP: yes={len(yes_clips)} no={len(no_clips)}{fallback}")
        print(f"  Новые в B: {len(only_b)}  |  Отфильтровано: {len(only_a)}")

        # Показать топ-3 из A и B
        print(f"  {'A (CLIP-only)':^42}  {'B (CLIP+BLIP)':^42}")
        for rank in range(3):
            a_cid, a_sc = top_a[rank] if rank < len(top_a) else ("—", 0)
            b_cid, b_sc = top_b[rank] if rank < len(top_b) else ("—", 0)
            a_desc = clips_data.get(a_cid, {}).get("description", "?")[:36]
            b_desc = clips_data.get(b_cid, {}).get("description", "?")[:36]
            marker = "★" if a_cid != b_cid else " "
            print(f"  {marker}{rank+1}.[{a_sc:.3f}] {a_desc:<36}  "
                  f"{marker}{rank+1}.[{b_sc:.3f}] {b_desc:<36}")
        print()

    # ── Итоги ────────────────────────────────────────────────────────────────
    n = len(TEST_QUERIES)
    print(f"{'='*72}")
    print(f"  ИТОГИ  ({n} запросов)")
    print(f"{'='*72}")
    print(f"  Всего BLIP yes:              {total_yes:>4}  ({total_yes/(total_yes+total_no)*100:.1f}%)")
    print(f"  Всего BLIP no:               {total_no:>4}  ({total_no/(total_yes+total_no)*100:.1f}%)")
    print(f"  Новых клипов появилось в B:  {new_in_b:>4}  ({new_in_b/n:.1f} avg/запрос)")
    print(f"  Отфильтровано A→B:           {filtered_out:>4}  ({filtered_out/n:.1f} avg/запрос)")
    print(f"  Fallback (all no):           {all_no_cases:>4} / {n}")
    print(f"{'='*72}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", default="channel_001_cosmos_de")
    args = parser.parse_args()
    run(args.channel)
