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

SFX_RULES_COSMOS = (
    "Also decide if a sound effect cue should trigger at this segment.\n"
    "Available sfx values: \"riser\", \"boom\", \"riser+boom\", \"impact\", \"downlifter\", null\n"
    "- riser      → tension building, anticipation before a reveal\n"
    "- boom       → big reveal, climax, jaw-dropping moment\n"
    "- riser+boom → full combo: tension → hit (for the most dramatic moments)\n"
    "- impact     → sudden shock, unexpected fact, surprise\n"
    "- downlifter → sad news, disappointment, let-down\n"
    "- null       → normal segment, no SFX\n"
    "Use EXTREMELY sparingly — only 0-2 SFX total per entire batch of 20 segments.\n"
    "At least 90% of segments MUST have sfx: null.\n"
    "Reserve ONLY for the single most jaw-dropping or emotionally devastating moment in the batch.\n"
    "When in doubt, choose null. Most batches should have 0 or 1 SFX.\n"
)

SFX_RULES_RELIGION = (
    "Also decide if a soft sound effect cue should trigger at this segment.\n"
    "Available sfx values: \"riser\", \"downlifter\", null\n"
    "- riser      → very soft gentle swell, quiet moment of spiritual revelation or hope\n"
    "- downlifter → sorrowful moment, loss, hardship, lament\n"
    "- null       → normal segment, no SFX (most segments should be this)\n"
    "This is a calm spiritual channel — NO boom, NO impact, NO riser+boom.\n"
    "Use EXTREMELY sparingly — only 0-1 SFX total per entire batch of 20 segments.\n"
    "At least 95% of segments MUST have sfx: null.\n"
    "Reserve ONLY for the single most spiritually significant moment in the entire batch.\n"
    "Most batches should have 0 SFX.\n"
)

NICHE_PROMPTS = {
    "cosmos": (
        "You are a visual director and sound designer for a space documentary YouTube channel.\n\n"
        "For each segment return TWO things:\n"
        "  vq  — English visual description (10-14 words) of what footage should play\n"
        "  sfx — sound effect cue (or null)\n\n"
        "LIBRARY CONTENT — these are the ONLY clip categories available:\n"
        "  1. NEBULAS/GALAXIES: colorful gas clouds, spiral/elliptical galaxies, star clusters, deep space\n"
        "  2. SPACE PHENOMENA: black holes, supernovas, solar flares, cosmic plasma, energy fields, radiation\n"
        "  3. SOLAR SYSTEM: Sun surface, planets (Mars/Saturn/Jupiter), moons, asteroids, comets, orbits\n"
        "  4. SPACECRAFT: rockets launching, satellites orbiting, probes drifting (Voyager/New Horizons style)\n"
        "  5. ASTRONAUTS: floating in ISS, spacewalk, working at consoles, suits in space\n"
        "  6. SCIENTISTS/ENGINEERS: at computer screens, analyzing data, in labs/clean rooms, discussing\n"
        "  7. EARTH FROM SPACE: blue marble, clouds, continents, aurora, atmosphere edge\n"
        "  8. TELESCOPES/INSTRUMENTS: observatory domes, radio dishes, satellite dishes, space telescopes\n\n"
        "Visual rules:\n"
        "- ALWAYS map to one of the 8 categories above — choose the nearest visual category\n"
        "- If segment describes abstract physics (dark energy, heliosphere boundary, quantum effects) → use visual analogy from category 2 (plasma/energy fields) or category 1 (deep space)\n"
        "- If segment mentions specific mission/probe → use category 4; if mentions scientists → use category 6\n"
        "- Match the SCALE: cosmic/galactic vs planetary vs human-scale scenes\n"
        "- Include visual atmosphere: colors, lighting, motion (spinning, drifting, exploding)\n"
        "- Always positive visual descriptions, never use \"not\", \"unlike\", \"without\"\n"
        "- Segments may be in any language — always respond in English\n"
        "- If segment text is unclear/corrupted → fallback: \"deep space starfield galaxy nebula glowing purple blue\"\n\n"
        + SFX_RULES_COSMOS +
        "\nExamples:\n"
        '"En 1977 nous avons reçu un signal" → vq: "radio telescope dish rotating night sky stars signal beam", sfx: null\n'
        '"Voyager traverse les confins du système solaire" → vq: "Voyager spacecraft drifting endless dark void distant sun tiny", sfx: "riser"\n'
        '"une explosion cent fois plus grande que le Soleil" → vq: "massive supernova explosion shockwave expanding orange red plasma", sfx: "riser+boom"\n'
        '"des milliards d\'années de silence cosmique" → vq: "empty dark universe slow drift ancient starfield cold void", sfx: "downlifter"\n'
        '"la matière noire invisible" → vq: "deep space dark nebula mysterious gravitational lensing blue glow", sfx: null\n'
        '"la vie est peut-être là-bas" → vq: "exoplanet blue green atmosphere ocean swirling clouds orbit", sfx: "boom"\n'
        '"la Terre vue depuis la Lune" → vq: "Earth rising over lunar horizon blue marble fragile beautiful", sfx: null\n'
        '"тёмная энергия ускоряет расширение вселенной" → vq: "universe expanding galaxy clusters accelerating infinite dark energy glow", sfx: null\n'
        '"heliosphere boundary ultraviolet wall" → vq: "glowing plasma energy field cosmic boundary shimmering blue void", sfx: null\n'
        '"scientists analyzing probe data" → vq: "scientists focused computer screens data analysis blue ambient light", sfx: null\n'
        '"solar wind charged particles streaming" → vq: "sun corona plasma flare streaming outward glowing golden dynamic", sfx: null\n'
    ),
    "religion": (
        "You are a visual director for a CHRISTIAN documentary YouTube channel about Jesus Christ and the Gospel.\n\n"
        "For each segment return TWO things:\n"
        "  vq  — English visual description (10-14 words) of what footage should play\n"
        "  sfx — soft sound effect cue (or null)\n\n"
        "LIBRARY CONTENT: footage is a MIX of two visual styles:\n"
        "  A) Renaissance/Baroque OIL PAINTINGS of biblical scenes (Rembrandt, Caravaggio style)\n"
        "  B) Cinematic MODERN footage: nature (sunsets, rivers, forests), cathedrals, candles, people praying\n\n"
        "Visual rules:\n"
        "- For SPECIFIC biblical events (Crucifixion, Resurrection, Last Supper, Baptism, Sermon, Gethsemane) → use style A\n"
        "- For general spiritual themes (faith, hope, prayer, eternity) → use style B\n"
        "- Style A format: \"[scene] [figures] Renaissance oil painting dramatic chiaroscuro\"\n"
        "- Style B format: \"[mood] [natural element or place] cinematic warm golden light\"\n"
        "- Always warm, reverent, uplifting — never dark or negative framing\n"
        "- Segments may be in any language — always respond in English\n"
        "- If segment text is unclear/corrupted → fallback: \"golden sunlight rays cathedral interior warm peaceful\"\n\n"
        + SFX_RULES_RELIGION +
        "\nExamples (biblical → style A):\n"
        '"Jesús murió en la cruz" → vq: "Jesus crucifixion cross Golgotha crowd weeping Renaissance oil painting", sfx: "downlifter"\n'
        '"resucitó al tercer día" → vq: "risen Christ empty tomb radiant white light angels Renaissance painting", sfx: "riser"\n'
        '"la Última Cena con sus discípulos" → vq: "Last Supper Jesus twelve apostles candlelit table bread wine painting", sfx: null\n'
        '"fue bautizado por Juan" → vq: "Baptism of Jesus Jordan river John dove Holy Spirit painting", sfx: null\n'
        '"el Sermón de la Montaña" → vq: "Jesus teaching crowds hillside Sermon Mount disciples listening painting", sfx: null\n'
        '"oró en el huerto de Getsemaní" → vq: "Jesus praying Gethsemane garden night anguish disciples sleeping painting", sfx: null\n'
        "\nExamples (general → style B):\n"
        '"la fe mueve montañas" → vq: "person kneeling prayer hands folded warm candlelight church golden", sfx: null\n'
        '"Dios creó el cielo y la tierra" → vq: "breathtaking sunrise mountain valley golden rays mist cinematic", sfx: null\n'
        '"el amor de Dios es eterno" → vq: "peaceful river forest golden light flowing serene cinematic", sfx: "riser"\n'
        '"catedral de Notre Dame" → vq: "Notre Dame cathedral gothic stone architecture Paris exterior sunlight", sfx: null\n'
        '"[unclear/corrupted text]" → vq: "golden sunlight rays cathedral interior warm peaceful cinematic", sfx: null\n'
    ),
}


def _call_claude(prompt: str) -> str:
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    r = subprocess.run(
        ["claude", "-p", prompt, "--model", "claude-haiku-4-5-20251001"],
        capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        timeout=90, env=env,
    )
    if r.returncode != 0:
        raise RuntimeError(f"claude CLI error {r.returncode}: {r.stderr[:300]}")
    return r.stdout.strip()


_VALID_SFX = {"riser", "boom", "riser+boom", "impact", "downlifter"}


def _extract_global_context(segments: list, niche: str) -> str:
    """
    Один вызов Haiku на весь скрипт — получить 2-3 строки контекста нарратива.
    Используется потом в каждом батче чтобы Haiku понимал общий смысл видео.
    """
    # Берём первые 60 сегментов для контекста (обычно ~5-8 мин)
    sample = segments[:60]
    text_block = " ".join(seg.get("text", "") for seg in sample if seg.get("text", "").strip())
    if not text_block.strip():
        return ""

    if niche == "religion":
        instruction = (
            "You are analyzing a Christian documentary script. "
            "In 2-3 sentences describe: (1) the main topic/story, (2) the emotional arc, (3) key visual themes. "
            "Be specific — name Jesus, biblical events, places if present. "
            "Respond in English only, plain text, no lists."
        )
    else:
        instruction = (
            "You are analyzing a space documentary script. "
            "In 2-3 sentences describe: (1) the main topic, (2) the emotional arc, (3) key visual themes. "
            "Be specific — name celestial objects, missions, phenomena if present. "
            "Respond in English only, plain text, no lists."
        )

    prompt = f"{instruction}\n\nScript excerpt:\n{text_block[:3000]}"
    try:
        ctx = _call_claude(prompt)
        ctx = ctx.strip()
        print(f"🧠 Global context: {ctx[:120]}...", flush=True)
        return ctx
    except Exception as e:
        print(f"  ⚠ Global context failed: {e}", flush=True)
        return ""


def _generate_batch(batch: list[tuple[int, str]], niche: str, global_context: str = "") -> dict[int, dict]:
    """
    batch: [(seg_id, text), ...]
    Returns: {seg_id: {"vq": "...", "sfx": "riser"|null}}
    """
    base_prompt = NICHE_PROMPTS.get(niche, NICHE_PROMPTS["cosmos"])
    lines = "\n".join(f"{i+1}. {text}" for i, (_, text) in enumerate(batch))
    seg_ids = [seg_id for seg_id, _ in batch]

    context_block = (
        f"SCRIPT CONTEXT (use this to understand the overall narrative):\n{global_context}\n\n"
        if global_context else ""
    )

    prompt = (
        f"{base_prompt}"
        f"{context_block}"
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
            wait = 5 * (attempt + 1)
            if attempt < RETRY_LIMIT:
                print(f"  ↩ Batch attempt {attempt+1} failed ({e}) — retry in {wait}s", flush=True)
                time.sleep(wait)
            else:
                print(f"  ✗ Batch FAILED after {RETRY_LIMIT+1} attempts: {e}", flush=True)
                return {}  # возвращаем пустой — retry на уровне generate_visual_queries


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

    # Один вызов для понимания общего нарратива
    global_context = _extract_global_context(segments, niche)

    # Разбить на батчи
    batches = []
    for i in range(0, len(segments), BATCH_SIZE):
        batch = [(seg["id"], seg.get("text", "")) for seg in segments[i:i + BATCH_SIZE]]
        batches.append(batch)

    print(f"📦 Батчей: {len(batches)} × {BATCH_SIZE}", flush=True)

    # Параллельная генерация с retry на уровне провалившихся батчей
    result_map: dict[int, dict] = {}
    t0 = time.time()
    all_seg_ids = {seg["id"] for seg in segments}

    def _run_batches(batch_list: list) -> list:
        """Запустить батчи параллельно, вернуть список провалившихся."""
        failed = []
        with ThreadPoolExecutor(max_workers=workers) as exe:
            futures = {exe.submit(_generate_batch, b, niche, global_context): b for b in batch_list}
            for future in as_completed(futures):
                batch = futures[future]
                try:
                    result = future.result()
                    if result:
                        result_map.update(result)
                        sfx_count = sum(1 for v in result.values() if v.get("sfx"))
                        print(f"  ✓ {len(result)} queries, {sfx_count} SFX", flush=True)
                    else:
                        failed.append(batch)
                except Exception as e:
                    print(f"  ✗ Batch exception: {e}", flush=True)
                    failed.append(batch)
        return failed

    failed_batches = _run_batches(batches)

    # Retry провалившихся батчей (до 2 раундов)
    for retry_round in range(1, 3):
        if not failed_batches:
            break
        missing = all_seg_ids - set(result_map.keys())
        print(f"🔄 Retry round {retry_round}: {len(failed_batches)} батч(ей), {len(missing)} сегментов без vq", flush=True)
        time.sleep(10 * retry_round)
        failed_batches = _run_batches(failed_batches)

    # Проверка покрытия
    missing_final = all_seg_ids - set(result_map.keys())
    if missing_final:
        pct = len(missing_final) / len(segments) * 100
        if pct > 15:
            raise RuntimeError(
                f"Visual queries: {len(missing_final)}/{len(segments)} сегментов ({pct:.0f}%) "
                f"не получили vq после всех retry — остановка."
            )
        print(f"  ⚠ {len(missing_final)} сегментов без vq ({pct:.0f}%) — допустимо, продолжаем", flush=True)

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

    # Strip SFX from garbage/short segments before zone filtering
    for seg in segments:
        if seg.get("sfx_cue") and len(seg.get("text", "").strip()) < 5:
            seg["sfx_cue"] = None

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
