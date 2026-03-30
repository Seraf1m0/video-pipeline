"""
Добавляет языковые keywords в library.json через Claude CLI (haiku).
Батчами по 20 клипов. Сохраняет прогресс после каждого батча.

Запуск:
    # cosmos: добавить DE+FR
    python agents/library/add_multilang_keywords.py --channel channel_001_cosmos_de

    # religion: добавить ES
    python agents/library/add_multilang_keywords.py --channel channel_003_religion_es
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_utils_dir = Path(__file__).resolve().parent.parent / "utils"
if str(_utils_dir) not in sys.path:
    sys.path.insert(0, str(_utils_dir))
from paths import get_library_json, get_niche

BATCH_SIZE = 20
MODEL      = "claude-haiku-4-5-20251001"

# Целевые языки по нише
NICHE_LANGS = {
    "cosmos":   ["de", "fr"],
    "religion": ["es"],
}

LANG_NAMES = {
    "de": "German",
    "fr": "French",
    "es": "Spanish",
}


def translate_batch(batch: list[tuple[str, str]], langs: list[str]) -> dict[str, dict[str, str]]:
    """
    batch: [(clip_id, keywords_en), ...]
    langs: ["de", "fr"] or ["es"]
    Returns: {clip_id: {"de": ..., "fr": ...}} or {clip_id: {"es": ...}}
    """
    lang_names = [LANG_NAMES[l] for l in langs]
    lang_cols  = " | ".join(lang_names)
    lines      = "\n".join(f"{cid}|||{kw}" for cid, kw in batch)

    example_out = "|||".join(["0001"] + [f"{l}_translated_text" for l in langs])

    prompt = (
        f"You are a translator for a video clip library.\n\n"
        f"Each line has format: ID|||english_text\n"
        f"Translate the english_text into: {lang_cols}.\n"
        f"Preserve meaning and style. Keep it concise.\n\n"
        f"Output ONLY translated lines, one per input line:\n"
        f"ID|||{'|||'.join(lang_names)}\n\n"
        f"Example output line:\n{example_out}\n\n"
        f"Rules:\n"
        f"- Translate accurately\n"
        f"- No explanations, no extra text, no markdown\n"
        f"- One output line per input line\n\n"
        f"Input:\n{lines}"
    )

    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

    result = subprocess.run(
        ["claude.cmd", "--model", MODEL, "-p", "-"],
        input=prompt,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        env=env,
    )

    if result.returncode != 0:
        raise RuntimeError(f"claude CLI error: {result.stderr.strip()[:200]}")

    results: dict[str, dict[str, str]] = {}
    for line in result.stdout.strip().splitlines():
        line = line.strip()
        if not line or "|||" not in line:
            continue
        parts = line.split("|||")
        if len(parts) == len(langs) + 1:
            cid = parts[0].strip()
            results[cid] = {langs[i]: parts[i + 1].strip() for i in range(len(langs))}
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", default="channel_001_cosmos_de",
                        help="Channel ID (e.g. channel_003_religion_es)")
    args = parser.parse_args()

    niche = get_niche(args.channel)
    langs = NICHE_LANGS.get(niche, ["de", "fr"])
    lib_json = get_library_json(args.channel)

    print(f"Канал: {args.channel}  Ниша: {niche}  Языки: {langs}", flush=True)
    print(f"Библиотека: {lib_json}", flush=True)

    with open(lib_json, encoding="utf-8") as f:
        lib = json.load(f)

    clips = lib["clips"]

    # Клипы без нужных переводов
    def needs_translation(entry: dict) -> bool:
        return (
            entry.get("indexed")
            and not entry.get("rejected")
            and entry.get("keywords")
            and any(not entry.get(f"keywords_{l}") for l in langs)
        )

    todo = [
        (cid, c["keywords"])
        for cid, c in clips.items()
        if needs_translation(c)
    ]

    total     = len(todo)
    n_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE
    print(f"Клипов без {'/'.join(l.upper() for l in langs)}: {total} → {n_batches} батчей", flush=True)

    if total == 0:
        print("Все клипы уже переведены.", flush=True)
        return

    done   = 0
    errors = 0

    for b_idx in range(n_batches):
        batch = todo[b_idx * BATCH_SIZE : (b_idx + 1) * BATCH_SIZE]
        print(f"[{b_idx+1}/{n_batches}] Батч {len(batch)} клипов...", end=" ", flush=True)

        try:
            results = translate_batch(batch, langs)
        except Exception as e:
            print(f"ОШИБКА: {e}", flush=True)
            errors += 1
            time.sleep(5)
            continue

        applied = 0
        for cid, translations in results.items():
            if cid in clips:
                for lang, text in translations.items():
                    clips[cid][f"keywords_{lang}"] = text
                applied += 1

        missed = len(batch) - applied
        done  += applied
        print(f"OK {applied}" + (f" ({missed} пропущено)" if missed else ""), flush=True)

        with open(lib_json, "w", encoding="utf-8") as f:
            json.dump(lib, f, ensure_ascii=False, indent=2)

        if b_idx < n_batches - 1:
            time.sleep(0.3)

    print(f"\n{'='*50}", flush=True)
    print(f"Готово: {done}/{total} клипов обновлено", flush=True)
    if errors:
        print(f"Ошибок батчей: {errors}", flush=True)
    print(f"Сохранено: {lib_json}", flush=True)
    print(f"\nТеперь пересобери embeddings:", flush=True)
    print(f"  python -c \"from clip_selector import build_library_embeddings; "
          f"build_library_embeddings('{args.channel}')\"", flush=True)


if __name__ == "__main__":
    main()
