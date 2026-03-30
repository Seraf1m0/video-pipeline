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

SFX_RULES = (
    "Also decide if a sound effect cue should trigger at this segment.\n"
    "Available sfx values: \"riser\", \"boom\", \"riser+boom\", \"impact\", \"downlifter\", null\n"
    "- riser      → tension building, anticipation before a reveal\n"
    "- boom       → big reveal, climax, jaw-dropping moment\n"
    "- riser+boom → full combo: tension → hit (for the most dramatic moments)\n"
    "- impact     → sudden shock, unexpected fact, surprise\n"
    "- downlifter → sad news, disappointment, let-down\n"
    "- null       → normal segment, no SFX\n"
    "Use VERY sparingly — only 3-5 SFX total per entire batch of 20 segments.\n"
    "At least 75% of segments MUST have sfx: null. Reserve for peak dramatic moments only.\n"
)

NICHE_PROMPTS = {
    "cosmos": (
        "You are a visual director and sound designer for a space documentary YouTube channel.\n\n"
        "For each segment return TWO things:\n"
        "  vq  — SHORT English visual description (8-10 words) of what footage should play\n"
        "  sfx — sound effect cue (or null)\n\n"
        "Visual rules:\n"
        "- Name exact objects ONLY if explicitly mentioned in segment text\n"
        "- If unsure about specific object → use generic visual analogy\n"
        "- Always positive descriptions, never use \"not\", \"unlike\", \"without\"\n"
        "- Segments may be in any language — always respond in English\n"
        "- If segment text is unclear/corrupted → fallback: \"deep space starfield galaxy nebula glowing\"\n"
        "- Optimize for stock footage library search\n\n"
        + SFX_RULES +
        "\nExamples:\n"
        '"En 1977 nous avons reçu un message" → vq: "radio telescope dish night sky receiving signal", sfx: null\n'
        '"quelque chose fonce vers nous" → vq: "bright comet streaking toward camera deep space", sfx: "riser"\n'
        '"Ce signal venait des étoiles" → vq: "stars twinkling vast cosmic void deep space", sfx: "boom"\n'
        '"des décennies de silence" → vq: "empty dark observatory dust gathering silence", sfx: "downlifter"\n'
        '"тёмная материя невидима" → vq: "deep space dark nebula mysterious cosmic void", sfx: null\n'
    ),
    "religion": (
        "You are a visual director and sound designer for a spiritual documentary YouTube channel.\n\n"
        "For each segment return TWO things:\n"
        "  vq  — SHORT English visual description (8-10 words) of what footage should play\n"
        "  sfx — sound effect cue (or null)\n\n"
        "Visual rules:\n"
        "- Name exact places/objects when mentioned (Vatican, Jerusalem, Bible, cross)\n"
        "- For abstract concepts → closest visual analogy\n"
        "- Always positive descriptions\n"
        "- Segments may be in any language — always respond in English\n"
        "- If segment text is unclear/corrupted → fallback: \"ancient church candles golden light glowing\"\n"
        "- Optimize for stock footage library search\n\n"
        + SFX_RULES +
        "\nExamples:\n"
        '"молитва в церкви" → vq: "people praying hands folded inside cathedral", sfx: null\n'
        '"И тогда Бог явился" → vq: "divine light rays burst through cathedral window", sfx: "riser+boom"\n'
        '"Иерусалим был разрушен" → vq: "ancient ruins Jerusalem stones fallen", sfx: "impact"\n'
    ),
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


_VALID_SFX = {"riser", "boom", "riser+boom", "impact", "downlifter"}


def _generate_batch(batch: list[tuple[int, str]], niche: str) -> dict[int, dict]:
    """
    batch: [(seg_id, text), ...]
    Returns: {seg_id: {"vq": "...", "sfx": "riser"|null}}
    """
    base_prompt = NICHE_PROMPTS.get(niche, NICHE_PROMPTS["cosmos"])
    lines = "\n".join(f"{i+1}. {text}" for i, (_, text) in enumerate(batch))
    seg_ids = [seg_id for seg_id, _ in batch]

    prompt = (
        f"{base_prompt}"
        f"Segments:\n{lines}\n\n"
        f"Respond ONLY with valid JSON, no markdown:\n"
        f'{{"1": {{"vq": "visual description", "sfx": null}}, '
        f'"2": {{"vq": "visual description", "sfx": "riser"}}, ...}}'
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
                entry = data.get(key, {})
                # Поддержка старого формата (строка) и нового (объект)
                if isinstance(entry, str):
                    result[seg_id] = {"vq": entry.strip(), "sfx": None}
                elif isinstance(entry, dict):
                    vq  = str(entry.get("vq", "")).strip()
                    sfx = entry.get("sfx")
                    if sfx not in _VALID_SFX:
                        sfx = None
                    result[seg_id] = {"vq": vq, "sfx": sfx}
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
    result_map: dict[int, dict] = {}
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=workers) as exe:
        futures = {exe.submit(_generate_batch, batch, niche): idx for idx, batch in enumerate(batches)}
        for future in as_completed(futures):
            idx = futures[future]
            try:
                result = future.result()
                result_map.update(result)
                sfx_count = sum(1 for v in result.values() if v.get("sfx"))
                print(f"  ✓ Батч {idx+1}/{len(batches)}: {len(result)} queries, {sfx_count} SFX cues", flush=True)
            except Exception as e:
                print(f"  ✗ Батч {idx+1} ошибка: {e}", flush=True)

    elapsed = time.time() - t0
    sfx_total = sum(1 for v in result_map.values() if v.get("sfx"))
    print(f"⏱ Генерация: {elapsed:.1f}s | Получено: {len(result_map)}/{len(segments)} | SFX cues: {sfx_total}", flush=True)

    # Добавить visual_query и sfx_cue в каждый сегмент
    for seg in segments:
        seg_id = seg.get("id")
        entry  = result_map.get(seg_id, {})
        seg["visual_query"] = entry.get("vq") or seg.get("text", "")
        seg["sfx_cue"]      = entry.get("sfx")  # None или "riser"/"boom"/etc

    # ── Post-processing: ограничить плотность SFX ──────────────────────────────
    # Zone A (первые 5 мин): 2 SFX/мин, gap 30s  — удержание зрителя
    # Zone B (остаток):       1 SFX/мин, gap 60s  — поддержка ритма
    total_dur    = float(data.get("total_duration", 0)) or float(segments[-1].get("end", 0))
    # Zone A = первые 10% видео, но не меньше 3 мин и не больше 10 мин
    zone_a_end   = max(180.0, min(600.0, total_dur * 0.10))
    zone_a_max   = max(6, int(zone_a_end / 30))   # ~2/мин в Zone A
    zone_b_max   = max(5, int((total_dur - zone_a_end) / 60))  # ~1/мин в Zone B
    _SFX_PRIORITY = {"riser+boom": 5, "boom": 4, "impact": 3, "riser": 2, "downlifter": 1}

    def _filter_zone(candidates, max_count, min_gap):
        kept_times, kept_ids = [], set()
        for seg, _ in candidates:
            t = float(seg.get("start", 0))
            if all(abs(t - kt) >= min_gap for kt in kept_times):
                kept_ids.add(seg.get("id"))
                kept_times.append(t)
            if len(kept_ids) >= max_count:
                break
        return kept_ids

    sfx_all = sorted(
        [(s, _SFX_PRIORITY.get(s.get("sfx_cue", ""), 0)) for s in segments if s.get("sfx_cue")],
        key=lambda x: -x[1],
    )
    zone_a_cands = [(s, p) for s, p in sfx_all if float(s.get("start", 0)) <= zone_a_end]
    zone_b_cands = [(s, p) for s, p in sfx_all if float(s.get("start", 0)) >  zone_a_end]

    kept_ids = (
        _filter_zone(zone_a_cands, zone_a_max, min_gap=30.0) |
        _filter_zone(zone_b_cands, zone_b_max, min_gap=60.0)
    )

    dropped = 0
    for seg in segments:
        if seg.get("sfx_cue") and seg.get("id") not in kept_ids:
            seg["sfx_cue"] = None
            dropped += 1
    zone_a_kept = sum(1 for s in segments if s.get("sfx_cue") and float(s.get("start",0)) <= zone_a_end)
    zone_b_kept = sum(1 for s in segments if s.get("sfx_cue") and float(s.get("start",0)) >  zone_a_end)
    if dropped:
        print(f"   SFX filter: убрано {dropped} → Zone A: {zone_a_kept} | Zone B: {zone_b_kept} | Итого: {len(kept_ids)}", flush=True)

    data["segments"] = segments
    data["visual_queries_generated"] = True
    data["visual_queries_niche"] = niche

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    covered  = sum(1 for s in segments if s.get("visual_query") and s["visual_query"] != s.get("text", ""))
    sfx_segs = sum(1 for s in segments if s.get("sfx_cue"))
    print(f"💾 result_visual.json сохранён: {out_path}", flush=True)
    print(f"   Visual queries: {covered}/{len(segments)} | SFX cues: {sfx_segs}", flush=True)
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
