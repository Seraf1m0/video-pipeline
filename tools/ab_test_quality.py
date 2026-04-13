"""
ab_test_quality.py — измерение улучшения качества видео.

Для каждого сегмента сравниваем:
  A (старая система) = avg-embedding [N,768], top-8 CLIP, без BLIP
  B (новая система)  = MAX-frame [N,5,768], top-20 CLIP + BLIP ITM rerank

Метрика качества: BLIP ITM score выбранного клипа
(насколько кадр клипа соответствует запросу — объективная vision оценка)

Запуск:
    py tools/ab_test_quality.py --channel channel_003_religion_es --session Video_20260408_222623
"""
import sys, json, argparse, time
from pathlib import Path
import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_utils = Path(__file__).resolve().parent.parent / "agents" / "utils"
_lib   = Path(__file__).resolve().parent.parent / "agents" / "library"
for p in [str(_utils), str(_lib)]:
    if p not in sys.path: sys.path.insert(0, p)

from paths import get_library_json, get_library_dir
from visual_embedder import encode_text, load_visual_embeddings

BLIP_RERANK_S = 300.0
TOP_A = 8
TOP_B = 20
BATCH = 8
CLIP_W = 0.6
ITM_W  = 0.4


def itm_score_single(img, query, proc, model, device):
    import torch
    inputs = proc(images=[img], text=[query[:77]], return_tensors="pt",
                  padding=True, truncation=True).to(device)
    with torch.no_grad():
        out = model(**inputs, use_itm_head=True)
    return float(out.itm_score.softmax(-1)[0, 1])


def run(channel_id, session):
    import torch
    from transformers import BlipProcessor, BlipForImageTextRetrieval
    from PIL import Image

    lang = "de" if "_de" in channel_id else "fr" if "_fr" in channel_id else "es"
    vis_json  = Path(f"D:/Video-pipeline-data/channels/{lang}/{session}/transcripts/result_visual.json")
    lib_dir   = get_library_dir(channel_id)
    thumb_dir = lib_dir / "thumbnails"

    with open(vis_json, encoding="utf-8") as f:
        segments = json.load(f)["segments"]
    with open(get_library_json(channel_id), encoding="utf-8") as f:
        clips_data = json.load(f)["clips"]

    result = load_visual_embeddings(channel_id)
    clip_ids, embs_3d = result   # [N, 5, 768]

    # Avg embeddings (симуляция старой системы)
    embs_avg = embs_3d.mean(axis=1)  # [N, 768]
    norms = np.linalg.norm(embs_avg, axis=1, keepdims=True)
    embs_avg = embs_avg / (norms + 1e-9)

    print("⏳ Загружаю BLIP ITM...", flush=True)
    proc  = BlipProcessor.from_pretrained("Salesforce/blip-itm-base-coco")
    model = BlipForImageTextRetrieval.from_pretrained("Salesforce/blip-itm-base-coco")
    model.eval()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model  = model.to(device)
    print(f"✅ BLIP на {device}\n")

    segs = [s for s in segments
            if float(s.get("start", 0)) < BLIP_RERANK_S
            and s.get("visual_query", "").strip()]
    print(f"Сегментов для теста (первые 5 мин): {len(segs)}\n")
    print(f"{'='*68}")
    print(f"  A (avg CLIP top-{TOP_A}) vs B (MAX CLIP top-{TOP_B} + BLIP ITM)")
    print(f"{'='*68}\n")

    scores_a, scores_b = [], []
    wins_b = wins_a = ties = 0
    changed = 0

    for seg in segs:
        vq    = seg["visual_query"].strip()
        start = float(seg.get("start", 0))
        text  = seg.get("text", "")[:40]
        q_vec = encode_text(vq)

        # A: avg embedding top-8
        sc_avg   = embs_avg @ q_vec
        top_a_i  = np.argsort(sc_avg)[::-1][:TOP_A]
        pick_a   = clip_ids[top_a_i[0]]

        # B: MAX-frame top-20 → BLIP rerank
        sc_max   = (embs_3d @ q_vec).max(axis=1)
        top_b_i  = np.argsort(sc_max)[::-1][:TOP_B]
        top_b_ids = [clip_ids[i] for i in top_b_i]

        imgs, vids = [], []
        for cid in top_b_ids:
            jpg = thumb_dir / f"{cid}.jpg"
            if jpg.exists():
                try:
                    imgs.append(Image.open(jpg).convert("RGB"))
                    vids.append(cid)
                except: pass

        itm_scores = []
        for i in range(0, len(imgs), BATCH):
            batch = imgs[i:i+BATCH]
            inputs = proc(images=batch, text=[vq[:77]]*len(batch),
                          return_tensors="pt", padding=True, truncation=True).to(device)
            with torch.no_grad():
                out = model(**inputs, use_itm_head=True)
            itm_scores.extend(out.itm_score.softmax(-1)[:,1].cpu().tolist())

        n = len(vids)
        combined = sorted(zip(vids, [CLIP_W*(n-i)/n + ITM_W*s
                                     for i,s in enumerate(itm_scores)]),
                          key=lambda x: x[1], reverse=True)
        pick_b = combined[0][0] if combined else top_b_ids[0]

        # Измеряем BLIP ITM score каждого выбранного клипа
        def get_itm(cid):
            jpg = thumb_dir / f"{cid}.jpg"
            if not jpg.exists(): return None
            try:
                img = Image.open(jpg).convert("RGB")
                return itm_score_single(img, vq, proc, model, device)
            except: return None

        sc_a = get_itm(pick_a)
        sc_b = get_itm(pick_b)

        if sc_a is None or sc_b is None:
            continue

        scores_a.append(sc_a)
        scores_b.append(sc_b)

        if pick_a != pick_b:
            changed += 1

        if sc_b > sc_a + 0.01:
            wins_b += 1
            marker = "▲"
        elif sc_a > sc_b + 0.01:
            wins_a += 1
            marker = "▼"
        else:
            ties += 1
            marker = "="

        print(f"{marker} [{start:.0f}s] A={sc_a:.3f} B={sc_b:.3f} Δ={sc_b-sc_a:+.3f}  '{text}'")

    # Итоги
    n = len(scores_a)
    avg_a = np.mean(scores_a)
    avg_b = np.mean(scores_b)
    improvement = (avg_b - avg_a) / avg_a * 100

    print(f"\n{'='*68}")
    print(f"  ИТОГИ  ({n} сегментов)")
    print(f"{'='*68}")
    print(f"  Avg BLIP ITM score:")
    print(f"    A (старая):  {avg_a:.4f}")
    print(f"    B (новая):   {avg_b:.4f}")
    print(f"    Улучшение:   {avg_b-avg_a:+.4f}  ({improvement:+.1f}%)")
    print(f"\n  Клипы изменились:     {changed}/{n}  ({changed/n*100:.0f}%)")
    print(f"  B лучше A:            {wins_b}/{n}  ({wins_b/n*100:.0f}%)")
    print(f"  A лучше B:            {wins_a}/{n}  ({wins_a/n*100:.0f}%)")
    print(f"  Ничья (±0.01):        {ties}/{n}  ({ties/n*100:.0f}%)")
    print(f"\n  Медиана A: {np.median(scores_a):.4f}")
    print(f"  Медиана B: {np.median(scores_b):.4f}")
    print(f"  P90 A:     {np.percentile(scores_a, 90):.4f}")
    print(f"  P90 B:     {np.percentile(scores_b, 90):.4f}")
    print(f"{'='*68}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", default="channel_003_religion_es")
    parser.add_argument("--session", default="Video_20260408_222623")
    args = parser.parse_args()
    run(args.channel, args.session)
