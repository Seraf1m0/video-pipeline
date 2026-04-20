"""
mg_planner_gemini.py — анализирует Whisper-транскрипт через Gemini 2.0 Flash,
планирует MG-зоны и формирует готовые props для каждой Remotion-композиции.

Gemini получает полный каталог анимаций и возвращает финальный JSON с props.
Python отвечает только за: zone rules A/B, palette assignment, валидацию.

Zone rules:
  A-zone (0-300s): 4-5 анимаций, первая в окне 15-45s, зазор ≥45s
  B-zone (300s+):  1-3 самых важных, зазор ≥90s

Output: session_dir/motion_graphics_plan.json

Usage:
    python mg_planner_gemini.py --channel de --session Video_20260415_193110
    python mg_planner_gemini.py --channel de --session Video_20260415_193110 --force
"""

import argparse
import json
import os
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from agents.utils.paths import get_session_dir, TEMP_ROOT

# ─── Config ───────────────────────────────────────────────────────────────────

CHANNEL_MAP = {
    "fr": "channel_002_cosmos_fr",
    "de": "channel_001_cosmos_de",
    "es": "channel_003_religion_es",
}

CHANNEL_LANG = {
    "channel_001_cosmos_de": "German",
    "channel_002_cosmos_fr": "French",
    "channel_003_religion_es": "Spanish",
}

GEMINI_MODEL = "gemini-2.5-flash"

# ─── Zone A/B constants ───────────────────────────────────────────────────────

ZONE_A_END       = 300.0
ZONE_A_MAX       = 5
ZONE_A_GAP       = 45.0
ZONE_A_FIRST_MIN = 15.0
ZONE_A_FIRST_MAX = 45.0
ZONE_B_GAP       = 90.0

# ─── Palettes ─────────────────────────────────────────────────────────────────

PALETTES = {
    "cosmic_blue":   {"bg_color": "#00050F", "accent_color": "#00C8FF"},
    "neon_green":    {"bg_color": "#000F08", "accent_color": "#4DFFB4"},
    "hot_pink":      {"bg_color": "#0F0008", "accent_color": "#FF3CAC"},
    "orange_fire":   {"bg_color": "#0F0500", "accent_color": "#FF6B35"},
    "gold":          {"bg_color": "#0F0C00", "accent_color": "#FFD700"},
    "purple":        {"bg_color": "#08000F", "accent_color": "#A855F7"},
    "electric_cyan": {"bg_color": "#00080F", "accent_color": "#00E5FF"},
    "deep_pink":     {"bg_color": "#0F0005", "accent_color": "#FF0099"},
}

SEMANTIC_PALETTE = {
    "wow_fact_record":    "orange_fire",
    "data_statistics":    "cosmic_blue",
    "growth_positive":    "neon_green",
    "composition_shares": "gold",
    "drama_statement":    "hot_pink",
    "process_timeline":   "purple",
    "comparison":         "electric_cyan",
    "list_enumeration":   "neon_green",
}

COMPOSITION_DEFAULT_PALETTE = {
    "DataCard":    "cosmic_blue",
    "Statement":   "hot_pink",
    "Timeline":    "purple",
    "Comparison":  "electric_cyan",
    "Highlight":   "orange_fire",
    "List":        "neon_green",
    "BarChart":    "cosmic_blue",
    "RadialChart": "gold",
    "LineChart":   "neon_green",
}

COMPOSITION_MAX_DURATION = {
    "DataCard":    10.0,
    "Statement":    8.0,
    "Timeline":    14.0,
    "Comparison":  10.0,
    "Highlight":    9.0,
    "List":        14.0,
    "BarChart":    14.0,
    "RadialChart": 14.0,
    "LineChart":   14.0,
}

# Минимальная длительность — чтобы многоэлементные композиции
# не выглядели смятыми и не ломали ритм основного ряда
COMPOSITION_MIN_DURATION = {
    "DataCard":   6.0,
    "Statement":  5.0,
    "Timeline":   8.0,
    "Comparison": 6.0,
    "Highlight":  5.0,
    "List":       8.0,
    "BarChart":   8.0,
    "RadialChart":8.0,
    "LineChart":  8.0,
}

VALID_COMPOSITIONS = set(COMPOSITION_DEFAULT_PALETTE.keys())

# ─── Gemini system prompt (full catalog) ──────────────────────────────────────

GEMINI_SYSTEM = """\
You are a motion graphics director for science YouTube videos.
Your job: read a timestamped transcript and return a JSON plan of Remotion animation zones.

══════════════════════════════════════════
ZONE PLACEMENT RULES (enforce strictly):
══════════════════════════════════════════
Zone A  (0 – 300 s):  pick 4-5 zones, first must start between 15-45 s, gap between zones ≥ 45 s
Zone B  (300 s – end): pick 1-3 zones ONLY for truly exceptional moments, gap ≥ 90 s
Never place a zone in the first 10 s or last 10 s of the video.
Zone duration: min 5 s, max 14 s.
Only use animations where content is DATA-RICH (numbers, stats, steps, comparisons)
or EMOTIONALLY KEY (central thesis the viewer must not miss).

══════════════════════════════════════════
AVAILABLE COMPOSITIONS + PROPS SCHEMAS:
══════════════════════════════════════════

1. DataCard
   When to use: 1-3 concrete numbers, measurements, temperatures, sizes, ages.
   Props:
   {
     "title": "short label (5-8 words)",
     "items": [
       {
         "label": "what this number means",
         "value": "display string e.g. '15 Mio.' or '5.500°C'",
         "numeric": 5500,        // optional integer — animates counter 0→N
         "suffix": "°C"          // optional — appended after animated counter
       }
     ],  // 1-3 items
     "duration_s": 8.0
   }

2. Statement
   When to use: one powerful KEY thesis, dramatic claim, or emotional peak.
   Props:
   {
     "text": "the main sentence (max 80 chars)",
     "highlight": "one word from text to accent (omit if none obvious)",
     "sub": "optional subtitle/source (max 60 chars) or null",
     "duration_s": 6.0
   }

3. Timeline
   When to use: step-by-step process, historical sequence, cause-effect chain (2-5 steps).
   Props:
   {
     "title": "short label for the sequence",
     "steps": [
       { "label": "step name (max 30 chars)", "desc": "optional one-liner (max 50 chars)" }
     ],  // 2-5 steps
     "duration_s": 10.0
   }

4. Comparison
   When to use: directly contrasting two quantities, states, eras.
   Props:
   {
     "title": "optional section label or null",
     "left":  { "label": "name (max 15 chars)", "value": "value string", "sub": "optional note or null" },
     "right": { "label": "name (max 15 chars)", "value": "value string", "sub": "optional note or null" },
     "duration_s": 8.0
   }

5. Highlight
   When to use: ONE extraordinary number/fact the viewer must remember. Most dramatic.
   Props:
   {
     "value": "the number/short value e.g. '15 Mio.' or '×1.000'",
     "label": "what this measures (max 40 chars)",
     "sub": "optional context sentence (max 80 chars) or null",
     "duration_s": 7.0
   }

6. List
   When to use: bullet enumeration of 2-5 items — causes, features, types, facts.
   Props:
   {
     "title": "list heading (max 40 chars)",
     "items": [
       { "text": "item text (max 50 chars)", "sub": "optional detail or null" }
     ],  // 2-5 items
     "duration_s": 10.0
   }

7. BarChart
   When to use: ranking, comparison by year/category, distribution — up to 8 bars.
   Props:
   {
     "title": "chart heading",
     "bars": [
       { "label": "category (max 10 chars)", "value": 1200 }  // numeric value
     ],  // 2-8 bars
     "unit": "optional unit string e.g. 'km/h' or ''",
     "duration_s": 10.0
   }

8. RadialChart
   When to use: composition, shares, percentages — how something is made up.
   Props:
   {
     "title": "chart heading",
     "segments": [
       { "label": "segment name (max 20 chars)", "value": 73 }  // numeric, auto-normalised to %
     ],  // 2-6 segments
     "center_text": "optional center label e.g. '73%' or null",
     "duration_s": 10.0
   }

9. LineChart
   When to use: trend, dynamics, growth/decline over time — 3-8 data points.
   Props:
   {
     "title": "chart heading",
     "points": [
       { "label": "x-axis label e.g. year", "value": 340 }  // numeric
     ],  // 3-8 points
     "unit": "optional unit string or ''",
     "duration_s": 12.0
   }

══════════════════════════════════════════
CHARACTER LIMITS — MANDATORY, NEVER EXCEED:
══════════════════════════════════════════
Every text field in props MUST fit within these hard limits.
If the natural phrasing is too long → REPHRASE, abbreviate, use numerals instead
of words, drop articles. The viewer reads the animation in 1-2 seconds maximum.
A shorter, punchier phrase is always better than an overflowing one.

  Statement   text          → 80 chars max  (rephrase to one punchy sentence)
  Statement   sub           → 60 chars max
  Statement   highlight     → 1 word only
  Timeline    title         → 40 chars max
  Timeline    steps[].label → 30 chars max  (verb + noun, no filler)
  Timeline    steps[].desc  → 50 chars max
  Comparison  title         → 40 chars max
  Comparison  left/right.label → 15 chars max  (abbreviate: "Planck-Teleskop" → "Planck")
  Comparison  left/right.sub   → 50 chars max
  Highlight   value         → 12 chars max  (e.g. "15 Mio." "×1.000" "99,8 %")
  Highlight   label         → 40 chars max
  Highlight   sub           → 80 chars max
  List        title         → 40 chars max
  List        items[].text  → 50 chars max  (cut to the essential noun phrase)
  List        items[].sub   → 60 chars max
  DataCard    title         → 40 chars max
  DataCard    items[].label → 30 chars max
  DataCard    items[].suffix → 8 chars max
  BarChart    title         → 40 chars max
  BarChart    bars[].label  → 10 chars max  (abbreviate aggressively: "Andromedagalaxie" → "Andromeda")
  BarChart    unit          → 8 chars max
  RadialChart title         → 40 chars max
  RadialChart segments[].label → 20 chars max
  RadialChart center_text   → 10 chars max
  LineChart   title         → 40 chars max
  LineChart   points[].label → 8 chars max  (years: "2024", short: "Jan")
  LineChart   unit          → 8 chars max

REWRITING RULES:
- Count characters mentally before writing each field.
- Prefer numerals: "fünfzehn Millionen" → "15 Mio."
- Drop articles/prepositions when possible: "Die Temperatur des Kerns" → "Kerntemperatur"
- For labels: use the shortest recognisable noun: "Neutronenstern-Rotation" → "Rotation"
- NEVER truncate mid-word — always produce a complete, readable phrase.

══════════════════════════════════════════
COLOR SEMANTICS (for your reference only — Python will assign palettes):
wow_fact_record → orange/fire tones
data_statistics → blue tones
growth_positive → green tones
composition_shares → gold tones
drama_statement → pink tones
process_timeline → purple tones
comparison → cyan tones
list_enumeration → green tones
══════════════════════════════════════════

OUTPUT FORMAT — return ONLY a valid JSON array, no markdown fences, no explanation:
[
  {
    "start_time": 18.5,
    "end_time": 26.0,
    "composition": "Highlight",
    "semantic": "wow_fact_record",
    "props": {
      "value": "15 Mio.",
      "label": "Grad Celsius im Kern der Sonne",
      "sub": "Das ist 2.700× heißer als die Oberfläche",
      "duration_s": 7.5
    }
  }
]

semantic must be one of:
  wow_fact_record | data_statistics | growth_positive | composition_shares |
  drama_statement | process_timeline | comparison | list_enumeration

All text in props must be in the VIDEO LANGUAGE specified in the user message.
duration_s in props must equal (end_time - start_time), clamped to composition max.
Do NOT include bg_color or accent_color in props — Python assigns those.
"""

GEMINI_USER_TEMPLATE = """\
Video language: {lang}
All prop text fields must be in {lang}.

Transcript segments (index, start_s, end_s, text):
{segments_block}

Return the MG zone plan as a JSON array."""


# ─── API ──────────────────────────────────────────────────────────────────────

_gemini_client = None

def _get_client():
    global _gemini_client
    if _gemini_client is None:
        import google.genai
        from dotenv import load_dotenv
        load_dotenv(ROOT / "config" / ".env")
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY not found in config/.env")
        _gemini_client = google.genai.Client(api_key=api_key)
    return _gemini_client


def _call_gemini(system: str, user: str) -> str:
    import time
    client = _get_client()
    for attempt in range(5):
        try:
            resp = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=user,
                config={
                    "system_instruction": system,
                    "temperature": 0.15,
                    "max_output_tokens": 4096,
                    "thinking_config": {"thinking_budget": 0},
                },
            )
            return resp.text.strip()
        except Exception as e:
            es = str(e)
            if ("503" in es or "UNAVAILABLE" in es or "429" in es or "EXHAUSTED" in es) and attempt < 4:
                wait = [5, 15, 30, 60][attempt]
                print(f"  [retry {attempt+1}/4] {es[:80]} — waiting {wait}s...", flush=True)
                time.sleep(wait)
            else:
                raise


def _parse_json_response(raw: str) -> list[dict]:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        text = text.rsplit("```", 1)[0]
    return json.loads(text.strip())


# ─── Zone rule enforcement ────────────────────────────────────────────────────

def _enforce_zone_rules(raw: list[dict], video_duration: float) -> list[dict]:
    zones = sorted(raw, key=lambda z: z["start_time"])
    a_raw = [z for z in zones if z["start_time"] < ZONE_A_END]
    b_raw = [z for z in zones if z["start_time"] >= ZONE_A_END]

    def _filter(candidates: list[dict], gap: float, limit: int | None = None) -> list[dict]:
        out, last_end = [], -9999.0
        for z in candidates:
            if z["start_time"] - last_end < gap:
                continue
            out.append(z)
            last_end = z["end_time"]
            if limit and len(out) >= limit:
                break
        return out

    a = _filter(a_raw, ZONE_A_GAP, ZONE_A_MAX)

    # enforce first-zone window
    if a:
        first = a[0]
        if first["start_time"] < ZONE_A_FIRST_MIN:
            delta = ZONE_A_FIRST_MIN - first["start_time"]
            first["start_time"] = ZONE_A_FIRST_MIN
            first["end_time"]   = min(first["end_time"] + delta, video_duration - 10)
        elif first["start_time"] > ZONE_A_FIRST_MAX and len(a) > 1:
            a = a[1:]   # first zone is too late — drop, next one takes lead

    b = _filter(b_raw, ZONE_B_GAP)
    return a + b


# ─── Palette assignment ───────────────────────────────────────────────────────

def _assign_palette(zones: list[dict]) -> list[dict]:
    used_last = None
    for zone in zones:
        preferred = (
            SEMANTIC_PALETTE.get(zone.get("semantic", ""))
            or COMPOSITION_DEFAULT_PALETTE.get(zone.get("composition", ""), "cosmic_blue")
        )
        if preferred == used_last:
            alternates   = [p for p in PALETTES if p != used_last]
            palette_name = alternates[hash(zone.get("composition","") + zone.get("semantic","")) % len(alternates)]
        else:
            palette_name = preferred

        zone["palette"] = palette_name
        zone["props"].update(PALETTES[palette_name])
        used_last = palette_name
    return zones


# ─── Props clamping (safety net if Gemini exceeds char limits) ───────────────

def _trunc(s: str | None, n: int) -> str | None:
    """Trim string to n chars at word boundary, append … if cut."""
    if s is None:
        return None
    s = str(s)
    if len(s) <= n:
        return s
    cut = s[:n - 1].rsplit(" ", 1)[0].rstrip(",.;:")
    return cut + "…"


_LIMITS: dict[str, dict[str, int]] = {
    "Statement":  {"text": 80, "sub": 60, "highlight": 20},
    "Timeline":   {"title": 40},
    "Comparison": {"title": 40},
    "Highlight":  {"value": 12, "label": 40, "sub": 80},
    "List":       {"title": 40},
    "DataCard":   {"title": 40},
    "BarChart":   {"title": 40, "unit": 8},
    "RadialChart":{"title": 40, "center_text": 10},
    "LineChart":  {"title": 40, "unit": 8},
}

_LIST_LIMITS: dict[str, dict[str, dict[str, int]]] = {
    "Timeline":   {"steps":    {"label": 30, "desc": 50}},
    "Comparison": {"left":     {"label": 15, "sub": 50},
                   "right":    {"label": 15, "sub": 50}},
    "List":       {"items":    {"text": 50, "sub": 60}},
    "DataCard":   {"items":    {"label": 30, "suffix": 8}},
    "BarChart":   {"bars":     {"label": 10}},
    "RadialChart":{"segments": {"label": 20}},
    "LineChart":  {"points":   {"label": 8}},
}


def _clamp_props(comp: str, props: dict) -> dict:
    """Enforce character limits on all text fields. Trims at word boundary."""
    for field, limit in _LIMITS.get(comp, {}).items():
        if field in props and props[field] is not None:
            props[field] = _trunc(props[field], limit)

    for list_field, subfields in _LIST_LIMITS.get(comp, {}).items():
        items = props.get(list_field)
        if not isinstance(items, list):
            continue
        if isinstance(items, dict):
            # Comparison: left/right are dicts, not list
            for key, item in items.items():
                if isinstance(item, dict):
                    for f, lim in subfields.items():
                        if f in item and item[f] is not None:
                            item[f] = _trunc(item[f], lim)
        else:
            for item in items:
                if isinstance(item, dict):
                    for f, lim in subfields.items():
                        if f in item and item[f] is not None:
                            item[f] = _trunc(item[f], lim)

    # Comparison left/right are top-level dict fields, not a list
    if comp == "Comparison":
        for side in ("left", "right"):
            obj = props.get(side)
            if isinstance(obj, dict):
                for f, lim in _LIST_LIMITS["Comparison"].get(side, {}).items():
                    if f in obj and obj[f] is not None:
                        obj[f] = _trunc(obj[f], lim)

    return props


# ─── Main ─────────────────────────────────────────────────────────────────────

def _segments_block(segments: list[dict]) -> str:
    return "\n".join(
        f"  [{i:03d}] {s['start']:.1f}s-{s['end']:.1f}s: {s['text']}"
        for i, s in enumerate(segments)
    )


def plan_zones(segments: list[dict], lang: str = "German") -> list[dict]:
    video_duration = segments[-1]["end"] if segments else 600.0

    user_msg = GEMINI_USER_TEMPLATE.format(
        lang=lang,
        segments_block=_segments_block(segments),
    )

    print(f"[PLANNER/Gemini] Calling {GEMINI_MODEL}  (lang={lang}, {len(segments)} segments)...")
    raw_text = _call_gemini(GEMINI_SYSTEM, user_msg)
    raw      = _parse_json_response(raw_text)
    print(f"[PLANNER/Gemini] Gemini returned {len(raw)} candidate zones")

    filtered = _enforce_zone_rules(raw, video_duration)
    print(f"[PLANNER/Gemini] After zone rules: {len(filtered)} zones")

    final = []
    for idx, z in enumerate(filtered, start=1):
        comp = z.get("composition", "Statement")
        if comp not in VALID_COMPOSITIONS:
            comp = "Statement"

        props = z.get("props", {})
        props = _clamp_props(comp, props)

        # clamp duration — min per composition, max per composition,
        # and never let animation extend past end of B-roll
        raw_dur  = z["end_time"] - z["start_time"]
        min_dur  = COMPOSITION_MIN_DURATION.get(comp, 5.0)
        max_dur  = COMPOSITION_MAX_DURATION.get(comp, 12.0)
        # hard ceiling: animation must end ≥10s before video end
        hard_max = max(min_dur, video_duration - z["start_time"] - 10.0)
        dur      = round(max(min_dur, min(raw_dur, max_dur, hard_max)), 2)
        props["duration_s"] = dur

        final.append({
            "zone_id":     idx,
            "start_time":  round(z["start_time"], 2),
            "end_time":    round(z["end_time"],   2),
            "duration":    dur,
            "composition": comp,
            "semantic":    z.get("semantic", "data_statistics"),
            "title":       str(props.get("title") or props.get("text") or props.get("value") or "")[:50],
            "props":       props,
        })

    final = _assign_palette(final)
    return final


def run(channel: str, session: str, force: bool = False):
    channel_id  = CHANNEL_MAP.get(channel, channel)
    session_dir = get_session_dir(channel_id, session)
    plan_path   = session_dir / "motion_graphics_plan.json"

    if plan_path.exists() and not force:
        print(f"[PLANNER] Loading existing plan: {plan_path}")
        return json.loads(plan_path.read_text(encoding="utf-8"))

    whisper_path = session_dir / "transcripts" / "result.json"
    if not whisper_path.exists():
        print(f"ERROR: {whisper_path} not found. Run transcriber first.")
        sys.exit(1)

    data     = json.loads(whisper_path.read_text(encoding="utf-8"))
    segments = data.get("segments", data) if isinstance(data, dict) else data

    lang  = CHANNEL_LANG.get(channel_id, "German")
    zones = plan_zones(segments, lang=lang)

    plan = {"channel_id": channel_id, "session": session, "zones": zones}
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n[PLANNER] {len(zones)} zones planned:")
    for z in zones:
        print(
            f"  Zone {z['zone_id']:02d}: {z['start_time']:.0f}s-{z['end_time']:.0f}s  "
            f"{z['composition']:<12}  palette={z['palette']:<14}  {z['title'][:40]}"
        )
    print(f"\n[PLANNER] Saved → {plan_path}")
    return plan


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", default="de")
    ap.add_argument("--session", required=True)
    ap.add_argument("--force",   action="store_true")
    args = ap.parse_args()
    run(args.channel, args.session, force=args.force)
