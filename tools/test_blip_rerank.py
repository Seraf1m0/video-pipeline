"""
test_blip_rerank.py — быстрый тест BLIP VQA reranker.

Берёт 10 случайных клипов из библиотеки и спрашивает BLIP
соответствуют ли они запросу. Показывает thumbnail + ответ.

Запуск:
    py tools/test_blip_rerank.py --channel channel_001_cosmos_de
"""
import sys
import json
import argparse
import random
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_utils_dir = Path(__file__).resolve().parent.parent / "agents" / "utils"
_lib_dir   = Path(__file__).resolve().parent.parent / "agents" / "library"
if str(_utils_dir) not in sys.path:
    sys.path.insert(0, str(_utils_dir))
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from paths import get_library_json, get_library_dir

TEST_CASES = [
    ("Hubble Space Telescope galaxy image",         True),   # должны найти да
    ("rocket launch fire exhaust smoke",             True),   # должны найти да
    ("astronaut floating in space",                  True),   # должны найти да
    ("person cooking in kitchen",                    False),  # точно нет
    ("car driving on highway",                       False),  # точно нет
    ("James Webb telescope infrared deep field",     True),   # должны найти да
]

N_CANDIDATES = 20   # сколько случайных клипов на каждый тест


def run_test(channel_id: str):
    import torch
    from transformers import BlipProcessor, BlipForQuestionAnswering
    from PIL import Image

    lib_json  = get_library_json(channel_id)
    lib_dir   = get_library_dir(channel_id)
    thumb_dir = lib_dir / "thumbnails"

    with open(lib_json, encoding="utf-8") as f:
        library = json.load(f)

    valid_clips = [
        cid for cid, e in library["clips"].items()
        if e.get("indexed") and not e.get("rejected")
        and (thumb_dir / f"{cid}.jpg").exists()
    ]
    print(f"✅ Клипов с thumbnails: {len(valid_clips)}\n")

    print("⏳ Загружаю BLIP...", flush=True)
    processor = BlipProcessor.from_pretrained("Salesforce/blip-vqa-base")
    model     = BlipForQuestionAnswering.from_pretrained("Salesforce/blip-vqa-base")
    model.eval()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model  = model.to(device)
    print(f"✅ BLIP загружен на {device}\n")

    for query, expect_yes in TEST_CASES:
        question = f"Is there {query} in this image?"
        candidates = random.sample(valid_clips, min(N_CANDIDATES, len(valid_clips)))

        yes = no = errors = 0
        yes_examples = []
        no_examples  = []

        for cid in candidates:
            jpg = thumb_dir / f"{cid}.jpg"
            try:
                img    = Image.open(jpg).convert("RGB")
                inputs = processor(img, question, return_tensors="pt").to(device)
                with torch.no_grad():
                    out = model.generate(**inputs)
                answer = processor.decode(out[0], skip_special_tokens=True).strip().lower()
                if answer.startswith("yes"):
                    yes += 1
                    if len(yes_examples) < 2:
                        desc = library["clips"].get(cid, {}).get("description", "?")[:50]
                        yes_examples.append(f"{cid}: {desc}")
                else:
                    no += 1
                    if len(no_examples) < 2:
                        desc = library["clips"].get(cid, {}).get("description", "?")[:50]
                        no_examples.append(f"{cid}: {desc}")
            except Exception as e:
                errors += 1

        icon = "✅" if (yes > 0) == expect_yes else "❌"
        print(f"{icon} Query: \"{query}\"")
        print(f"   yes={yes}  no={no}  errors={errors}  (из {N_CANDIDATES} случайных клипов)")
        if yes_examples:
            print(f"   YES примеры: {yes_examples[0]}")
        if no_examples:
            print(f"   NO  примеры: {no_examples[0]}")
        print()

    # Замер скорости
    import time
    sample = random.sample(valid_clips, min(10, len(valid_clips)))
    q = "Is there a telescope in this image?"
    t0 = time.time()
    for cid in sample:
        jpg = thumb_dir / f"{cid}.jpg"
        img = Image.open(jpg).convert("RGB")
        inputs = processor(img, q, return_tensors="pt").to(device)
        with torch.no_grad():
            model.generate(**inputs)
    elapsed = time.time() - t0
    print(f"⚡ Скорость: {elapsed:.2f}с на {len(sample)} клипов "
          f"= {elapsed/len(sample)*1000:.0f}мс/клип")
    print(f"   Топ-20 кандидатов займёт: ~{elapsed/len(sample)*20:.1f}с")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", default="channel_001_cosmos_de")
    args = parser.parse_args()
    run_test(args.channel)
