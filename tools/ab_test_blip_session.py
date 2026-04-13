"""
ab_test_blip_session.py — сравнение итоговых клипов с BLIP и без.

Для каждого сегмента первых 5 минут:
  A = топ-1 по CLIP (без BLIP)
  B = топ-1 после BLIP-фильтра

Показывает где клипы отличаются и насколько вырос CLIP-score.

Запуск:
    py tools/ab_test_blip_session.py --channel channel_001_cosmos_de --session Video_20260407_182148
"""
import sys, json, argparse
from pathlib import Path
import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_utils_dir = Path(__file__).resolve().parent.parent / "agents" / "utils"
_lib_dir   = Path(__file__).resolve().parent.parent / "agents" / "library"
for p in [str(_utils_dir), str(_lib_dir)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from paths import get_library_json, get_library_dir, get_clips_dir
from visual_embedder import encode_text, load_visual_embeddings

BLIP_RERANK_S = 300.0
TOP_CLIP      = 8
TOP_BLIP      = 20


def get_scores(embs_3d, clip_ids, query_vec):
    scores = (embs_3d @ query_vec).max(axis=1)
    order  = np.argsort(scores)[::-1]
    return [(clip_ids[i], float(scores[i])) for i in order]


def blip_yn(candidates, question, thumb_dir, processor, model, device):
    from PIL import Image
    import torch
    yes, no = [], []
    for cid, sc in candidates:
        jpg = thumb_dir / f"{cid}.jpg"
        if not jpg.exists():
            yes.append((cid, sc)); continue
        try:
            img    = Image.open(jpg).convert("RGB")
            inputs = processor(img, question, return_tensors="pt").to(device)
            with torch.no_grad():
                out = model.generate(**inputs)
            ans = processor.decode(out[0], skip_special_tokens=True).strip().lower()
            (yes if ans.startswith("yes") else no).append((cid, sc))
        except Exception:
            yes.append((cid, sc))
    return yes, no


def run(channel_id, session):
    import torch
    from transformers import BlipProcessor, BlipForQuestionAnswering

    # Пути
    data_root = Path("D:/Video-pipeline-data/channels")
    lang      = "de" if "_de" in channel_id else "fr" if "_fr" in channel_id else "es"
    vis_json  = data_root / lang / session / "transcripts" / "result_visual.json"
    lib_json  = get_library_json(channel_id)
    lib_dir   = get_library_dir(channel_id)
    thumb_dir = lib_dir / "thumbnails"

    with open(vis_json, encoding="utf-8") as f:
        segments = json.load(f)["segments"]
    with open(lib_json, encoding="utf-8") as f:
        clips_data = json.load(f)["clips"]

    result = load_visual_embeddings(channel_id)
    clip_ids, embs = result

    print("⏳ Загружаю BLIP...", flush=True)
    processor = BlipProcessor.from_pretrained("Salesforce/blip-vqa-base")
    model     = BlipForQuestionAnswering.from_pretrained("Salesforce/blip-vqa-base")
    model.eval()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model  = model.to(device)
    print(f"✅ BLIP на {device}\n")

    # Фильтруем только первые 5 минут с visual_query
    segs_5min = [s for s in segments
                 if float(s.get("start", 0)) < BLIP_RERANK_S
                 and s.get("visual_query", "").strip()]

    print(f"Сегментов в первых 5 мин с visual_query: {len(segs_5min)}\n")
    print(f"{'='*72}")
    print(f"  A (CLIP top-1) vs B (BLIP-filtered top-1)")
    print(f"{'='*72}\n")

    changed = 0
    score_gains = []
    blip_harder = []  # случаи где BLIP сильно срезал (yes < 5)

    for seg in segs_5min:
        start = float(seg.get("start", 0))
        vq    = seg.get("visual_query", "").strip()
        text  = seg.get("text", "")[:50]

        q_vec    = encode_text(vq)
        all_sc   = get_scores(embs, clip_ids, q_vec)

        # A: топ-1 из top-8
        top_a_id, top_a_sc = all_sc[0]

        # B: BLIP filter на top-20
        top_20 = all_sc[:TOP_BLIP]
        question = "Is there " + " ".join(vq.split()[:8]) + " in this image?"
        yes_clips, no_clips = blip_yn(top_20, question, thumb_dir, processor, model, device)

        if yes_clips:
            top_b_id, top_b_sc = yes_clips[0]
        else:
            top_b_id, top_b_sc = top_a_id, top_a_sc  # fallback

        same = (top_a_id == top_b_id)
        if not same:
            changed += 1
            gain = top_b_sc - top_a_sc
            score_gains.append(gain)

            desc_a = clips_data.get(top_a_id, {}).get("description", "?")[:45]
            desc_b = clips_data.get(top_b_id, {}).get("description", "?")[:45]

            print(f"[{start:.0f}s] \"{text}\"")
            print(f"  VQ: {vq[:65]}")
            print(f"  A [{top_a_sc:.3f}]: {desc_a}")
            print(f"  B [{top_b_sc:.3f}]: {desc_b}  (Δ{gain:+.3f})")
            print(f"  BLIP: yes={len(yes_clips)} no={len(no_clips)}")
            if len(yes_clips) < 5:
                blip_harder.append((start, vq, len(yes_clips)))
            print()

    # Итоги
    total = len(segs_5min)
    same_count = total - changed
    print(f"{'='*72}")
    print(f"  ИТОГИ  ({total} сегментов, первые 5 мин)")
    print(f"{'='*72}")
    print(f"  Клип не изменился (A=B):     {same_count:>3} / {total}  ({same_count/total*100:.0f}%)")
    print(f"  Клип изменился (B≠A):        {changed:>3} / {total}  ({changed/total*100:.0f}%)")
    if score_gains:
        print(f"\n  Δ CLIP-score при замене клипа:")
        print(f"    Среднее: {np.mean(score_gains):+.4f}")
        print(f"    Макс:    {max(score_gains):+.4f}")
        print(f"    Мин:     {min(score_gains):+.4f}")
        neg = sum(1 for g in score_gains if g < 0)
        pos = sum(1 for g in score_gains if g >= 0)
        print(f"    BLIP выбрал клип с ВЫШЕ CLIP-score:  {pos}")
        print(f"    BLIP выбрал клип с НИЖЕ CLIP-score:  {neg}  (BLIP исправил ошибку CLIP)")
    if blip_harder:
        print(f"\n  Сегменты где BLIP срезал агрессивно (yes < 5):")
        for t, q, y in blip_harder:
            print(f"    [{t:.0f}s] yes={y}: {q[:55]}")
    print(f"{'='*72}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", default="channel_001_cosmos_de")
    parser.add_argument("--session", default="Video_20260407_182148")
    args = parser.parse_args()
    run(args.channel, args.session)
