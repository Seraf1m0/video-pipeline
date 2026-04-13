"""
ab_test_clip_matching.py — A/B тест: avg-embedding vs MAX-frame CLIP matching.

A = mean(axis=1) по 5 кадрам → [N, 768]  (симуляция старого поведения)
B = max по кадрам при запросе  → [N, 5, 768]  (новый MAX-frame)

Запуск:
    py tools/ab_test_clip_matching.py --channel channel_001_cosmos_de
"""
import sys
import json
import argparse
from pathlib import Path

import numpy as np

_utils_dir = Path(__file__).resolve().parent.parent / "agents" / "utils"
_lib_dir   = Path(__file__).resolve().parent.parent / "agents" / "library"
if str(_utils_dir) not in sys.path:
    sys.path.insert(0, str(_utils_dir))
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from paths import get_library_dir, get_library_json
from visual_embedder import encode_text, load_visual_embeddings

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── Тестовые запросы ────────────────────────────────────────────────────────
TEST_QUERIES = [
    # (описание типа, запрос)
    ("abstract",  "cosmic web dark matter filaments"),
    ("abstract",  "glowing nebula in deep space"),
    ("abstract",  "universe expansion animation"),
    ("object",    "telescope observing stars"),
    ("object",    "rocket launch from earth"),
    ("object",    "astronaut in space suit"),
    ("object",    "solar panels on satellite"),
    ("named",     "James Webb Space Telescope JWST"),
    ("named",     "Hubble Space Telescope galaxy image"),
    ("named",     "International Space Station ISS orbit"),
    ("named",     "Perseverance rover Mars surface"),
    ("named",     "Falcon 9 SpaceX rocket landing"),
    ("named",     "Aurora borealis northern lights"),
    ("named",     "Euclid telescope dark energy survey"),
    ("object",    "black hole accretion disk"),
]

TOP_K = 5  # сколько топ-клипов показывать


def score_avg(embs_3d: np.ndarray, query_vec: np.ndarray) -> np.ndarray:
    """A: усреднить кадры → скор через dot product."""
    avg = embs_3d.mean(axis=1)  # [N, 768]
    norms = np.linalg.norm(avg, axis=1, keepdims=True)
    avg_norm = avg / (norms + 1e-9)
    return avg_norm @ query_vec  # [N]


def score_max(embs_3d: np.ndarray, query_vec: np.ndarray) -> np.ndarray:
    """B: MAX по кадрам."""
    return (embs_3d @ query_vec).max(axis=1)  # [N]


def run_ab_test(channel_id: str):
    print(f"\n{'='*70}")
    print(f"  A/B TEST: avg-embedding (A) vs MAX-frame (B)")
    print(f"  Channel: {channel_id}")
    print(f"{'='*70}\n")

    # Загрузка эмбеддингов
    result = load_visual_embeddings(channel_id)
    if result is None:
        print("❌ visual_embeddings.npz не найден!")
        return
    clip_ids, embs = result

    if embs.ndim != 3:
        print(f"❌ Ожидается 3D [N, 5, 768], получено: {embs.shape}")
        print("   Сначала запусти rebuild: py agents/library/visual_embedder.py --channel ...")
        return

    print(f"✅ Эмбеддинги: {embs.shape}  ({len(clip_ids)} клипов)\n")

    # Загрузка библиотеки для описаний клипов
    lib_json = get_library_json(channel_id)
    with open(lib_json, encoding="utf-8") as f:
        library = json.load(f)
    clips_data = library.get("clips", {})

    # Результаты
    wins_a = wins_b = ties = 0
    score_diffs = []

    for qtype, query in TEST_QUERIES:
        print(f"{'─'*70}")
        print(f"  [{qtype.upper()}] \"{query}\"")
        print(f"{'─'*70}")

        # Кодируем запрос
        q_vec = encode_text(query)  # [768]

        # Скоры
        s_a = score_avg(embs, q_vec)
        s_b = score_max(embs, q_vec)

        top_a = np.argsort(s_a)[::-1][:TOP_K]
        top_b = np.argsort(s_b)[::-1][:TOP_K]

        # Проверяем совпадение топ-1
        best_a_id = clip_ids[top_a[0]]
        best_b_id = clip_ids[top_b[0]]
        same_top1 = (best_a_id == best_b_id)

        print(f"\n  {'A (avg)':^35}  {'B (MAX)':^35}")
        print(f"  {'─'*35}  {'─'*35}")

        for rank in range(TOP_K):
            ia = top_a[rank]
            ib = top_b[rank]
            cid_a = clip_ids[ia]
            cid_b = clip_ids[ib]
            desc_a = clips_data.get(cid_a, {}).get("description", "?")[:32]
            desc_b = clips_data.get(cid_b, {}).get("description", "?")[:32]
            sc_a   = s_a[ia]
            sc_b   = s_b[ib]
            marker = "★" if rank == 0 and not same_top1 else " "
            print(f"{marker} {rank+1}. [{sc_a:.3f}] {desc_a:<32}  {rank+1}. [{sc_b:.3f}] {desc_b:<32}")

        # Считаем разницу скора топ-1
        diff = s_b[top_b[0]] - s_a[top_a[0]]
        score_diffs.append(diff)

        # Какие клипы ТОЛЬКО в B (новые находки)
        set_a = set(clip_ids[i] for i in top_a)
        set_b = set(clip_ids[i] for i in top_b)
        only_b = set_b - set_a
        only_a = set_a - set_b

        if only_b:
            print(f"\n  🆕 Только в B (новые находки MAX):")
            for cid in only_b:
                desc = clips_data.get(cid, {}).get("description", "?")[:60]
                print(f"     {cid}: {desc}")
        if only_a:
            print(f"\n  📤 Только в A (потеряны в MAX):")
            for cid in only_a:
                desc = clips_data.get(cid, {}).get("description", "?")[:60]
                print(f"     {cid}: {desc}")

        if same_top1:
            ties += 1
        elif s_b[top_b[0]] > s_a[top_a[0]]:
            wins_b += 1
        else:
            wins_a += 1

        print()

    # ── Итоги ────────────────────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  ИТОГИ A/B ТЕСТА  ({len(TEST_QUERIES)} запросов)")
    print(f"{'='*70}")
    print(f"  Топ-1 совпадает (ничья):        {ties:>3} / {len(TEST_QUERIES)}")
    print(f"  Выиграл A (avg лучше по скору): {wins_a:>3} / {len(TEST_QUERIES)}")
    print(f"  Выиграл B (MAX лучше по скору): {wins_b:>3} / {len(TEST_QUERIES)}")
    print(f"\n  Среднее изменение скора топ-1:  {np.mean(score_diffs):+.4f}")
    print(f"  Макс рост (B лучше):             {max(score_diffs):+.4f}")
    print(f"  Макс падение (A лучше):          {min(score_diffs):+.4f}")

    # По типам запросов
    print(f"\n  По типам запросов:")
    for qtype in ["abstract", "object", "named"]:
        idxs = [i for i, (qt, _) in enumerate(TEST_QUERIES) if qt == qtype]
        diffs = [score_diffs[i] for i in idxs]
        print(f"    {qtype:10s}: avg Δscore = {np.mean(diffs):+.4f}  "
              f"(из {len(diffs)} запросов)")

    print(f"\n{'='*70}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", default="channel_001_cosmos_de")
    args = parser.parse_args()
    run_ab_test(args.channel)
