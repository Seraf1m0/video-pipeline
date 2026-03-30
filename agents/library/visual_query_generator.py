"""
visual_query_generator.py — обогащение сегментов Whisper визуальными описаниями.

Для каждого сегмента Claude Haiku генерирует короткое английское описание
того, что должно быть видно на экране — оптимизировано для CLIP-матчинга.

Запуск:
    python agents/library/visual_query_generator.py \
        --result_json data/channels/fr/Video_20260330_174554/transcripts/result.json \
        --niche cosmos

Результат: рядом создаётся result_visual.json с полем visual_query в каждом сегменте.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── Claude CLI setup ──────────────────────────────────────────────────────────
_CLAUDE_DIR = Path(os.environ.get("APPDATA", "")) / "Claude/claude-code"
if _CLAUDE_DIR.exists():
    for _v in sorted(_CLAUDE_DIR.iterdir(), reverse=True):
        _exe = _v / "claude.exe"
        if _exe.exists():
            os.environ["PATH"] = str(_v) + os.pathsep + os.environ.get("PATH", "")
            break

BATCH_SIZE   = 20
MAX_WORKERS  = 4
RETRY_LIMIT  = 2

NICHE_CONTEXT = {
    "cosmos":   "space, astronomy, cosmos, universe, stars, galaxies, nebulae, planets",
    "religion": "religion, spirituality, church, faith, bible, prayer, sacred places",
}


def _call_claude(prompt: str) -> str:
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    r = subprocess.run(
        ["claude", "-p", prompt, "--model", "claude-3-haiku-20240307"],
        capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        timeout=90, env=env,
    )
    if r.returncode != 0:
        raise RuntimeError(f"claude CLI error {r.returncode}: {r.stderr[:300]}")
    return r.stdout.strip()


def _generate_batch(batch: list[tuple[int, str]], niche: str) -> dict[int, str]:
    """
    batch: [(seg_id, text), ...]
    Returns: {seg_id: visual_query_en}
    """
    context = NICHE_CONTEXT.get(niche, "documentary footage")
    lines = "\n".join(f"{i+1}. {text}" for i, (_, text) in enumerate(batch))
    seg_ids = [seg_id for seg_id, _ in batch]

    prompt = (
        f"You are a visual director for a {niche} YouTube channel.\n"
        f"Theme context: {context}\n\n"
        f"For each numbered segment below, write a SHORT English visual description "
        f"(5-12 words) of what stock footage should play on screen. "
        f"Describe VISUALS only — what the camera shows, not what is said. "
        f"Optimize for stock footage search.\n\n"
        f"Segments:\n{lines}\n\n"
        f"Respond ONLY with valid JSON object, no markdown:\n"
        f'{{"1": "visual description", "2": "visual description", ...}}'
    )

    for attempt in range(RETRY_LIMIT + 1):
        try:
            raw = _call_claude(prompt)
            raw = raw.replace("```json", "").replace("```", "").strip()
            start = raw.find("{")
            end   = raw.rfind("}") + 1
            if start < 0 or end <= start:
                raise ValueError("No JSON object found")
            data = json.loads(raw[start:end])
            result = {}
            for i, seg_id in enumerate(seg_ids):
                key = str(i + 1)
                if key in data and isinstance(data[key], str):
                    result[seg_id] = data[key].strip()
            return result
        except Exception as e:
            if attempt < RETRY_LIMIT:
                time.sleep(2)
            else:
                print(f"  ⚠ Batch failed after {RETRY_LIMIT+1} attempts: {e}", flush=True)
                return {}


def generate_visual_queries(
    result_json: Path,
    niche: str = "cosmos",
    workers: int = MAX_WORKERS,
    force: bool = False,
) -> Path:
    """
    Читает result.json, генерирует visual_query для каждого сегмента,
    сохраняет result_visual.json рядом с result.json.
    Возвращает путь к result_visual.json.
    """
    out_path = result_json.parent / "result_visual.json"

    if out_path.exists() and not force:
        print(f"✅ result_visual.json уже есть: {out_path}", flush=True)
        return out_path

    with open(result_json, encoding="utf-8") as f:
        data = json.load(f)

    segments = data.get("segments", [])
    if not segments:
        raise ValueError("Нет сегментов в result.json")

    print(f"🎬 Генерация visual queries: {len(segments)} сегментов, {niche}, {workers} потоков", flush=True)

    # Разбить на батчи
    batches = []
    for i in range(0, len(segments), BATCH_SIZE):
        batch = [(seg["id"], seg.get("text", "")) for seg in segments[i:i + BATCH_SIZE]]
        batches.append(batch)

    print(f"📦 Батчей: {len(batches)} × {BATCH_SIZE}", flush=True)

    # Параллельная генерация
    visual_map: dict[int, str] = {}
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=workers) as exe:
        futures = {exe.submit(_generate_batch, batch, niche): idx for idx, batch in enumerate(batches)}
        for future in as_completed(futures):
            idx = futures[future]
            try:
                result = future.result()
                visual_map.update(result)
                print(f"  ✓ Батч {idx+1}/{len(batches)}: {len(result)} queries", flush=True)
            except Exception as e:
                print(f"  ✗ Батч {idx+1} ошибка: {e}", flush=True)

    elapsed = time.time() - t0
    print(f"⏱ Генерация: {elapsed:.1f}s | Получено: {len(visual_map)}/{len(segments)}", flush=True)

    # Добавить visual_query в каждый сегмент
    for seg in segments:
        seg_id = seg.get("id")
        vq = visual_map.get(seg_id)
        if vq:
            seg["visual_query"] = vq
        else:
            # Fallback: используем оригинальный текст
            seg["visual_query"] = seg.get("text", "")

    data["segments"] = segments
    data["visual_queries_generated"] = True
    data["visual_queries_niche"] = niche

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    covered = sum(1 for s in segments if s.get("visual_query") and s["visual_query"] != s.get("text", ""))
    print(f"💾 result_visual.json сохранён: {out_path}", flush=True)
    print(f"   Обогащено: {covered}/{len(segments)} сегментов", flush=True)
    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--result_json", required=True)
    parser.add_argument("--niche", default="cosmos", choices=["cosmos", "religion"])
    parser.add_argument("--workers", type=int, default=MAX_WORKERS)
    parser.add_argument("--force", action="store_true", help="Перегенерировать даже если файл есть")
    args = parser.parse_args()

    out = generate_visual_queries(
        result_json=Path(args.result_json),
        niche=args.niche,
        workers=args.workers,
        force=args.force,
    )
    print(f"\nГотово: {out}")
