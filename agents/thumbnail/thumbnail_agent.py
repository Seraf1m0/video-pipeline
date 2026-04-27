"""
thumbnail_agent.py — YouTube thumbnail generator for cosmos channel.

Flow:
  1. User provides text (line1 + line2) + script
  2. Flash writes creative TZ (visual concept brief)
  3. TZ → 3 detailed image prompts with text baked in + unique graphic elements
  4. PixelAgent generates all 3 in parallel
  5. Flash evaluates each strictly for CTR potential
  6. If none pass → Flash critiques specific problems → refine prompts → regenerate
  7. Repeat up to MAX_ROUNDS

Usage:
    py agents/thumbnail/thumbnail_agent.py --channel channel_001_cosmos_de --text1 "TESS-DATEN" --text2 "LÜGE"
    py agents/thumbnail/thumbnail_agent.py --channel channel_001_cosmos_de --session Video_20260418_120000 --text1 "NASA LÜGT" --text2 "BEWIESEN"
"""

import argparse
import asyncio
import base64
import io
import json
import os
import re
import sys
import time
from pathlib import Path

import aiohttp
from dotenv import load_dotenv, set_key

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agents" / "utils"))
sys.path.insert(0, str(ROOT / "agents" / "media_generator"))

load_dotenv(ROOT / "config" / ".env")

from paths import get_result_json, get_session_dir, get_last_session  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── Config ────────────────────────────────────────────────────────────────────

FLASH_MODEL    = "gemini-2.5-flash-lite"
_NO_THINKING   = {"thinking_config": {"thinking_budget": 0}}
N_VARIANTS     = 3       # три варианта превью за раунд (пользователь выбирает лучший)
MAX_ROUNDS     = 1       # один раунд — без eval loop
PASS_THRESHOLD = 8       # минимальный overall score (не используется при MAX_ROUNDS=1)

# ── Gemini ────────────────────────────────────────────────────────────────────

_gemini_client = None

def _get_gemini():
    global _gemini_client
    if _gemini_client is None:
        from google import genai
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY не найден в config/.env")
        _gemini_client = genai.Client(api_key=api_key)
    return _gemini_client


def _flash(prompt: str, images: list[bytes] | None = None, max_tokens: int = 1000) -> str:
    from google.genai import types as gtypes
    parts = []
    if images:
        for img_bytes in images:
            from PIL import Image as PILImage
            pil = PILImage.open(io.BytesIO(img_bytes)).convert("RGB")
            buf = io.BytesIO()
            pil.save(buf, format="JPEG", quality=92)
            parts.append(gtypes.Part.from_bytes(data=buf.getvalue(), mime_type="image/jpeg"))
    parts.append(gtypes.Part.from_text(text=prompt))
    attempt = 0
    while True:
        try:
            resp = _get_gemini().models.generate_content(
                model=FLASH_MODEL,
                contents=parts,
                config={"temperature": 0.4, "max_output_tokens": max_tokens, **_NO_THINKING},
            )
            text = (resp.text or "").strip()
            if not text:
                attempt += 1
                print(f"  [Flash] empty response, retry #{attempt} in 10s...", flush=True)
                time.sleep(10)
                continue
            return text
        except Exception as e:
            if "503" in str(e) or "UNAVAILABLE" in str(e) or "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                attempt += 1
                wait = min(15 * attempt, 60)
                print(f"  [Flash] API overloaded, retry #{attempt} in {wait}s...", flush=True)
                time.sleep(wait)
            else:
                raise


def _parse_json(raw: str) -> dict | list:
    raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw, flags=re.DOTALL).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Gemini sometimes returns trailing commas or truncated JSON — find last valid object
        for end in range(len(raw), 0, -1):
            try:
                return json.loads(raw[:end])
            except json.JSONDecodeError:
                continue
        raise


# ── Genre aesthetic templates ─────────────────────────────────────────────────

_GENRE_TEMPLATES = {
    "A": {
        "name": "Intelligence-thriller",
        "description": "Military satellites, classified missions, GSSAP, NRO, black projects",
        "aesthetic": "Deakins/Sicario shadow work + declassified KH-11 satellite photo + Spiegel Titelseite authority",
        "palette": "Dominant 70%: near-black surveillance void | Secondary 25%: cold NRO grey metallic | Accent 5%: dark amber MLI thermal blanket glow",
        "lens": "400mm telephoto — extreme compression, shallow DOF, subject isolated from background",
        "anti_refs": "NOT like Star Wars hero shots | NOT like Armageddon asteroid drama | NOT like NASA artist rendering | IS like: declassified KH-11 reconnaissance photo | IS like: Sicario border crossing wide shot | IS like: leaked Lockheed Skunk Works documentation",
        "negative_space": "The void around the object is not empty — it is the weight of secrecy. Darkness that feels classified, redacted.",
    },
    "B": {
        "name": "Cosmic sublime",
        "description": "Deep space, galaxies, cosmological scale, nebulae, universal vastness",
        "aesthetic": "Denis Villeneuve wide compositions + Terrence Malick slow reverence + frame from Interstellar",
        "palette": "Dominant 70%: deep indigo-black void | Secondary 25%: cold distant starlight white | Accent 5%: single warm nebula amber or pale blue hydrogen glow",
        "lens": "24mm wide — vast, vertiginous scale, viewer feels small and exposed",
        "anti_refs": "NOT like NASA colorized Hubble poster | NOT like screensaver galaxy | NOT like Discovery Channel CGI segment | IS like: Pale Blue Dot compositional weight | IS like: frame from Interstellar docking sequence | IS like: Cassini probe archival photograph",
        "negative_space": "The void is the protagonist. The silence of vacuum has physical weight. The blackness between stars is not absence — it is presence.",
    },
    "C": {
        "name": "Documentary leak",
        "description": "Classified missions, accidents, cover-ups, whistleblower revelations, Spiegel-authority topics",
        "aesthetic": "Film grain + analog artifact aesthetic + leaked Spiegel investigation photo + Challenger inquiry documentation",
        "palette": "Dominant 70%: desaturated archival grey | Secondary 25%: high-contrast white (overexposed edge detail) | Accent 5%: single warning red or yellow indicator",
        "lens": "85mm — documentary journalism distance, not intimate, not epic. The lens of a photojournalist who got too close.",
        "anti_refs": "NOT clean digital broadcast render | NOT polished corporate space imagery | NOT Discovery Channel CGI | IS like: Spiegel Titelseite investigative cover | IS like: leaked NASA Challenger investigation photograph | IS like: Der Spiegel 1969 Apollo skepticism cover",
        "negative_space": "The empty space feels redacted — as if information was deliberately removed from the frame.",
    },
    "D": {
        "name": "Scientific horror",
        "description": "Unknown phenomena, contamination, biological scale horror, things that shouldn't exist",
        "aesthetic": "Clinical sterility gone wrong + Nature/Science journal cover + controlled environment at moment of failure",
        "palette": "Dominant 70%: laboratory white or deep sterile black | Secondary 25%: cold blue-white clinical light | Accent 5%: single anomalous indicator (toxic green, UV violet, bio-orange)",
        "lens": "Macro or 100mm micro — extreme proximity that reveals scale horror. Something too large rendered with microscope intimacy, or something microscopic at monstrous scale.",
        "anti_refs": "NOT Hollywood alien horror | NOT fantasy creature | NOT B-movie aesthetics | IS like: Science journal cover photograph | IS like: CDC documentation image | IS like: Nature magazine deep-sea discovery photo",
        "negative_space": "The empty space feels sterile, controlled, wrong. The cleanliness itself is threatening.",
    },
}


# ── Stage 0: Thumbnail Function ───────────────────────────────────────────────

def write_thumbnail_function(segments: list[dict], thumbnail_text: str) -> dict:
    """
    Шаг 0 — определяем бизнес-задачу превью ДО любого визуального брифа.
    Hook type, click trigger, психологический триггер аудитории, жанровый код.
    """
    print("\n[0/3] Writing thumbnail function (Step 0)...", flush=True)
    script = " ".join(
        s.get("text", "") for s in segments if "[" not in s.get("text", "")
    )[:2000]

    genre_list = "\n".join(
        f"  {k}. {v['name']} — {v['description']}" for k, v in _GENRE_TEMPLATES.items()
    )

    prompt = f"""\
You are a YouTube strategy director. Before ANY visual work, you define the BUSINESS FUNCTION of this thumbnail.

TARGET AUDIENCE: Germans aged 60-70+. ZDF documentary viewers. Intelligent, skeptical, not clickbait-prone.
They click when a thumbnail poses a visual question they CANNOT leave unanswered.

THUMBNAIL TEXT: "{thumbnail_text}"
VIDEO SCRIPT EXCERPT: {script}

Answer these strategic questions:

1. HOOK TYPE — what psychological mechanism makes them click?
   Choose one: revelation (hidden truth exposed) | threat (danger to something they care about) |
   mystery (unexplained anomaly) | authority (official confirmation of something major) |
   scale_shock (confronting incomprehensible size or number)

2. CLICK TRIGGER — the precise visual contradiction or question in the frame:
   E.g.: "A military satellite shown from an angle that shouldn't be possible" or
   "A star that looks exactly like a face — but the scale text says 800 million km"

3. AUDIENCE EMOTION at thumbnail glance — the specific feeling in 0.3 seconds:
   NOT "curiosity" (too vague). E.g.: "the cold dread of realizing something was hidden from you"

4. GENRE CATEGORY — which aesthetic matrix fits this topic:
{genre_list}

Return ONLY valid JSON:
{{
  "hook_type": "<revelation | threat | mystery | authority | scale_shock>",
  "click_trigger": "<the precise visual question or contradiction — one sentence>",
  "audience_emotion": "<specific emotional state in 0.3 seconds — not generic>",
  "genre_category": "<A | B | C | D>",
  "genre_rationale": "<one sentence: why this genre code fits this specific topic>"
}}
"""
    result = _parse_json(_flash(prompt, max_tokens=400))
    cat = result.get("genre_category", "B")
    if cat not in _GENRE_TEMPLATES:
        cat = "B"
        result["genre_category"] = cat
    print(f"  Hook:     {result.get('hook_type','')} — {result.get('click_trigger','')[:60]}", flush=True)
    print(f"  Emotion:  {result.get('audience_emotion','')[:80]}", flush=True)
    print(f"  Genre:    {cat} ({_GENRE_TEMPLATES[cat]['name']})", flush=True)
    return result


# ── Prompt Rules ──────────────────────────────────────────────────────────────

_FORBIDDEN_REPLACEMENTS = """
FORBIDDEN → CORRECT REPLACEMENT (apply automatically):
- "photorealistic, ultra detailed, 8K" → "leaked NASA archival photo aesthetic"
- "professional photography style" → specific cinematographer (Deakins / van Hoytema / Lubezki)
- "dramatic documentary lighting" → direction + temperature ("harsh sun upper-left, 6500K, no fill")
- "vivid natural colors" → palette with percentages (70/25/5)
- "NO sci-fi fantasy elements" → positive anchor ("intelligence-thriller poster")
- "text ~28% frame height" → "monumental billboard scale, Christopher Nolan title weight"
- "dark semi-transparent bar behind text" → "left third: deep shadow gradient — darkness IS the text background"
- "ominously drifting" → specific emotion ("the quiet dread of something indifferent")
- "bold condensed white Impact font" → "letters lit by the same sun as the scene, rim light matching lighting direction"
- "generic spacecraft" → exact name + year + mission
"""

_GENRE_MATRICES_DETAILED = {
    "A": {
        "name": "Intelligence Thriller",
        "refs": "the border-crossing wide in 'Sicario' (2015), Deakins; the Dunkirk dogfight in 'Dunkirk' (2017), van Hoytema; the bin Laden compound in 'Zero Dark Thirty' (2012), Bigelow",
        "palette": "deep blacks 70% / weathered gold 25% / cold steel or red annotation 5%",
        "emotion": "paranoid awe, classified beauty, the weight of something you weren't meant to see",
        "annotations": "OSINT-style red circles and arrows — Bellingcat forensic markup aesthetic",
        "anti_refs": "NOT like Marvel poster / NOT like Tom Clancy cover / NOT like stock military photo",
    },
    "B": {
        "name": "Cosmic Sublime",
        "refs": "the cornfield drone shot in 'Interstellar' (2014), van Hoytema; the Las Vegas wasteland in 'Blade Runner 2049' (2017), Deakins; the shimmer wall approach in 'Annihilation' (2018)",
        "palette": "indigo void 70% / cold distant starlight 25% / single warm nebula amber or pale cyan 5%",
        "emotion": "melancholic awe, beautiful catastrophe, the vertigo of irrelevant scale",
        "annotations": "none — purity of composition",
        "anti_refs": "NOT like NASA colorized Hubble poster / NOT like screensaver galaxy / NOT like Discovery Channel CGI",
    },
    "C": {
        "name": "Documentary Leak",
        "refs": "the planetary surface walk in 'Solaris' (1972), Tarkovsky; the Challenger press conference in 'The Challenger Disaster' (2019); the OSINT source photos in Bellingcat MH17 investigation (2014)",
        "palette": "desaturated archival grey 70% / high-contrast white overexposed edge 25% / single warning red or amber 5%",
        "emotion": "the cold certainty that something was hidden from you, institutional betrayal",
        "annotations": "OSINT-style red circles/arrows — forensic analyst markup over archival photo",
        "anti_refs": "NOT clean digital broadcast render / NOT polished corporate space imagery / NOT Discovery CGI",
    },
    "D": {
        "name": "Medical / Scientific Horror",
        "refs": "the lab containment sequence in 'Contagion' (2011), Soderbergh; the alien-artifact examination in 'Arrival' (2016), Bradford Young; the electron microscopy sequences from 'The Hot Zone' (2019)",
        "palette": "clinical sterile white 70% / cold blue-white lab light 25% / toxic green or bio-orange anomaly 5%",
        "emotion": "the specific horror of something real that shouldn't exist, contamination dread",
        "annotations": "measurement markers, scale bars, clinical annotation — NOT decorative",
        "anti_refs": "NOT Hollywood alien horror / NOT fantasy creature / NOT B-movie aesthetics",
    },
}

_TEXT_INTEGRATION_TEMPLATE = """\
Content: "{text}"
Position: {position}
Scale: monumental billboard scale, Christopher Nolan title weight
Lighting: rim light on {light_direction} edges matching scene sun direction,
          shadow on opposite edges
Atmospheric integration: subtle film grain on letter surfaces, letters cast micro-shadows onto scene geometry
Stroke: refined 2-3px black outer stroke
Shadow: offset 8-10px, blur 16-20px, 80% opacity
NO background bar, NO panel, NO semi-transparent overlay
Reference: {reference}"""

_OSINT_ANNOTATIONS = """\
Red circle: outline only, 2-3px stroke, #FF1A1A, slightly imperfect hand-drawn quality,
            90% opacity, drawn tight around {target}, subtle red inner glow
Red arrow: clean modern design, triangular arrowhead, matching #FF1A1A,
           diagonal from text block toward subject, layered OVER photo (NOT painted into scene)
Aesthetic: Bellingcat-style forensic markup, intelligence analyst annotation"""

# Pre-assigned unique accent colors per genre — one per variant (V1/V2/V3)
_GENRE_ACCENTS = {
    "A": [
        "signal red #CC0000 — annotation marker on classified document",
        "weathered gold #C8A44D — MLI thermal blanket glow",
        "cold steel #4A5568 — satellite body metallic sheen",
    ],
    "B": [
        "pale cyan #87CEEB — cold distant starlight edge",
        "warm amber #FF8C00 — single warm nebula glow",
        "UV violet #8B00FF — hydrogen emission line glow",
    ],
    "C": [
        "danger red #CC0000 — OSINT annotation marker",
        "rust amber #D4730A — degraded chemical warning indicator",
        "scorched orange #CC6600 — oxidation hazard marker",
    ],
    "D": [
        "toxic green #00FF41 — contamination alert indicator",
        "bio-orange #FF6600 — biological hazard glow",
        "UV violet #9400D3 — fluorescence anomaly signal",
    ],
}

# MOMENT — forbidden process language, required instant language
_MOMENT_FORBIDDEN = (
    '"rolling in", "as if about to", "shimmers as it absorbs", "begins to", '
    '"slowly", "gradually", "drifting", "ominously", "is being kicked up"'
)
_MOMENT_REQUIRED = (
    '"The exact frame of...", "The instant...", "The moment...", '
    '"Caught at the precise second...", "Frozen at..."'
)

_ATMOSPHERIC_TEXT_ZONE_RULE = """\
ATMOSPHERIC PHENOMENA TEXT ZONE RULE:
- Dust storms / fog / haze do NOT create dark shadows — they create uniform milky diffusion.
- Text placed INSIDE atmospheric haze will be unreadable (same mid-tone as haze background).
- For any planetary weather, atmospheric event, or haze scene:
  The composition MUST include a dark foreground element (rock, ground, cliff base, rover body)
  that serves as the text zone. If the current description lacks such an element, REDESIGN
  the shot to add it — push camera lower, add a foreground rock face, include terrain.
  Text zone = that dark foreground terrain. NOT the haze. NOT the sky. NOT the atmosphere.
- "A hypothetical dark zone is impossible" is NOT acceptable — redesign the composition.
- The darker the foreground element, the better the text zone."""

_OBJECT_IDENTIFIER_RULE = """\
OBJECT IDENTIFIER MUST BE AN EVENT OR MOMENT — NOT A PROCESS OR METHOD:
  FORBIDDEN (process/method):
    × "OSINT markup of storm"
    × "forensic analysis of dust"
    × "simulated electron microscopy"
    × "OSINT analysis of Martian dust showing..."
  CORRECT (event/moment with date/mission/outcome):
    ✓ "Mars 2018 global dust storm — sky-darkening haze photographed from Opportunity before it went silent"
    ✓ "Opportunity rover final transmission site, Perseverance Valley, June 2018"
    ✓ "Perchlorate crystals in Phoenix lander soil sample — the measurement that changed the colonisation debate"
    ✓ "Perseverance rover wheel tread coated in Jezero Crater regolith, Sol 400"
Identifier must answer: WHAT specific thing, WHEN/WHERE, WHY historically significant."""


# ── Stage 1: Full TZ (Brief) ──────────────────────────────────────────────────

def write_tz(segments: list[dict], thumbnail_text: str, thumb_fn: dict) -> dict:
    """
    Анализирует сценарий и формирует полный бриф для 3 вариантов.
    Level 3 variations: каждый вариант — РАЗНЫЙ ОБЪЕКТ под одну тему (не разные ракурсы).
    """
    print("\n[1/3] Analyzing script → writing full brief (Level 3 variations)...", flush=True)

    script = " ".join(
        s.get("text", "") for s in segments if "[" not in s.get("text", "")
    )[:6000]

    genre_key    = thumb_fn.get("genre_category", "B")
    genre_matrix = _GENRE_MATRICES_DETAILED.get(genre_key, _GENRE_MATRICES_DETAILED["B"])
    hook_type    = thumb_fn.get("hook_type", "revelation")
    trigger      = thumb_fn.get("click_trigger", "")

    prompt = f"""\
You are a senior YouTube creative director. Write a production brief for 3 thumbnail variants.

IMAGE GENERATOR: Google Imagen 2 (via Google Flow).
Strengths: photorealistic textures, scientific accuracy, atmospheric lighting, baked-in text.

━━━ INPUTS ━━━

THUMBNAIL TEXT (exact, do not modify):
"{thumbnail_text}"

HOOK: {hook_type} — {trigger}

GENRE: {genre_key} — {genre_matrix['name']}
  Film references: {genre_matrix['refs']}
  Palette target: {genre_matrix['palette']}
  Emotion target: {genre_matrix['emotion']}
  Annotation style: {genre_matrix['annotations']}

VIDEO SCRIPT:
{script}

━━━ YOUR JOB ━━━

RULE: LEVEL 3 VARIATIONS — each of the 3 variants must feature a DIFFERENT VISUAL OBJECT
that represents the same topic. NOT different camera angles of the same object.
Example for "Martian dust is deadly":
  V1 = perchlorate crystals under UV light (microscopic)
  V2 = NASA rover wheel coated in red dust (medium shot)
  V3 = Mars sunrise dust storm on the horizon (wide cinematic)

Step 1 — Read script. Identify the topic and 3 DISTINCT visual subjects that all represent it.

Step 2 — For each of the 3 objects, define:
- Official scientific name (specific, not generic)
- Object identifier (visual handle — MUST BE AN EVENT OR MOMENT, not a process or method)
  {_OBJECT_IDENTIFIER_RULE}
- 4-6 visual details (shape / color / texture / scale / distinctive feature / what makes it unmistakable)
- MOMENT — ONE FROZEN INSTANT ONLY. NOT a process, NOT ongoing action.
  FORBIDDEN words: "rolling in", "as if about to", "shimmers as it absorbs", "begins to", "slowly", "gradually", "drifting", "is being kicked up"
  REQUIRED opening: "The exact frame of..." / "The instant..." / "The moment..." / "Caught at the precise second..."
- Natural environment + what is ABSENT from frame (important negative space)
- Cinematographer reference + SPECIFIC FILM (year) + SPECIFIC SCENE (e.g. "the planetary surface imagery from 'Solaris' (1972)" or "the border-crossing wide shot in 'Sicario' (2015), Deakins")
- Lens: focal length + DOF
- Lighting: direction + color temperature (e.g. "harsh sun upper-left, 6500K, no fill")
- Dark zone for text:
  {_ATMOSPHERIC_TEXT_ZONE_RULE}
  For non-atmospheric scenes: where in the composition natural darkness exists.
- Text position in that dark zone — verify that the OBJECT and its shadows do NOT dominate that same zone
- OSINT annotation target: exactly what the red arrow/circle should mark
- Variant genre key: can match global genre or differ if this object fits a different aesthetic
- Emotion for THIS variant only (must differ from other variants)

Step 3 — Split "{thumbnail_text}" into display lines:
- Keep ALL words, no additions
- 1 line if short, 2 lines if it improves visual balance
- The FULL phrase must appear in EVERY variant (Level 3 rule)

Return ONLY valid JSON:
{{
  "topic_summary": "<one sentence: exact claim of this video>",
  "text_line1": "<first display line OR empty string>",
  "text_line2": "<second line OR full text if one line>",
  "genre_key": "{genre_key}",
  "variants": [
    {{
      "id": 1,
      "variant_genre_key": "<A | B | C | D — can differ from global if object fits different aesthetic>",
      "object_name": "<official scientific name — specific>",
      "object_identifier": "<visual handle: how viewer identifies this — include mission/year/event if relevant>",
      "object_details": "<4-6 visual details: shape, color, texture, scale, distinctive feature>",
      "moment": "<FROZEN INSTANT ONLY — must open with: The exact frame of / The instant / Caught at the precise second>",
      "environment": "<what surrounds the object + what is ABSENT>",
      "cinematographer": "<specific film (year), scene — e.g. 'the border-crossing wide in Sicario (2015), Deakins'>",
      "lens": "<focal length + DOF + perspective effect>",
      "lighting": "<direction + color temp + shadow quality>",
      "dark_zone": "<where natural darkness lives — confirm object does NOT dominate this zone>",
      "text_position": "<bottom-left | top-left | bottom-right | top-right | bottom-center>",
      "annotation_target": "<exactly what the red arrow/circle marks — be specific>",
      "palette": {{
        "dominant_70": "<color + description>",
        "secondary_25": "<color + description>",
        "accent_5": "<color + description — must differ from other variants>"
      }},
      "emotion": "<specific emotional state in 0.3 seconds — unique, NOT used in other variants>"
    }},
    {{"id": 2, "variant_genre_key": "...", "object_name": "...", "object_identifier": "...",
      "object_details": "...", "moment": "...", "environment": "...",
      "cinematographer": "...", "lens": "...", "lighting": "...", "dark_zone": "...",
      "text_position": "...", "annotation_target": "...",
      "palette": {{"dominant_70": "...", "secondary_25": "...", "accent_5": "..."}},
      "emotion": "..."}},
    {{"id": 3, "variant_genre_key": "...", "object_name": "...", "object_identifier": "...",
      "object_details": "...", "moment": "...", "environment": "...",
      "cinematographer": "...", "lens": "...", "lighting": "...", "dark_zone": "...",
      "text_position": "...", "annotation_target": "...",
      "palette": {{"dominant_70": "...", "secondary_25": "...", "accent_5": "..."}},
      "emotion": "..."}}
  ],
  "emotional_task": "<overall: what viewer feels across all 3 variants>"
}}
"""
    result = _parse_json(_flash(prompt, max_tokens=3000))

    print(f"  Topic:  {result.get('topic_summary','')[:90]}", flush=True)
    print(f"  Text:   '{result.get('text_line1','')}' / '{result.get('text_line2','')}'", flush=True)
    for v in result.get("variants", []):
        print(
            f"  V{v.get('id')}: {v.get('object_name','')[:45]} | "
            f"{v.get('text_position','')} | {v.get('emotion','')[:50]}",
            flush=True,
        )
    return result


# ── Stage 2: 3 Prompts for Google Imagen 2 ───────────────────────────────────

def generate_prompts(
    tz: dict,
    thumb_fn: dict,
    round_num: int = 1,
    prev_critiques: list[str] | None = None,
) -> list[dict]:
    """
    Генерирует N_VARIANTS промптов для Google Imagen 2 (Google Flow).
    Использует Level 3 варианты из write_tz() — каждый вариант РАЗНЫЙ ОБЪЕКТ.
    Применяет 11-блочную структуру + запрещённые замены + TEXT INTEGRATION template.
    """
    print(f"\n[2/3] Generating {N_VARIANTS} prompts for Google Imagen 2 (round {round_num})...", flush=True)

    variants  = tz.get("variants", [])
    line1     = tz.get("text_line1", "")
    line2     = tz.get("text_line2", "")
    if not line2 and line1:
        line2, line1 = line1, ""

    genre_key = tz.get("genre_key", thumb_fn.get("genre_category", "B"))
    genre     = _GENRE_MATRICES_DETAILED.get(genre_key, _GENRE_MATRICES_DETAILED["B"])

    # Fallback если write_tz() вернул старую структуру без variants
    if not variants:
        variants = [
            {"id": 1, "object_name": tz.get("main_object", "subject"),
             "object_details": tz.get("object_accuracy_notes", ""),
             "moment": "", "environment": tz.get("background", ""),
             "cinematographer": "Deakins", "lens": "85mm",
             "lighting": tz.get("lighting", "dramatic"),
             "dark_zone": "lower-left", "text_position": "bottom-left",
             "annotation_target": "the main subject",
             "palette": {}, "emotion": tz.get("mood", "")},
        ] * N_VARIANTS
        for i, v in enumerate(variants):
            variants[i] = {**v, "id": i + 1}

    critique_note = ""
    if prev_critiques:
        critique_note = (
            "\n\nPREVIOUS ROUND PROBLEMS TO FIX:\n"
            + "\n".join(f"- {c}" for c in prev_critiques)
            + "\n"
        )

    # Pre-assign unique accent per variant to guarantee palette uniqueness
    accents = _GENRE_ACCENTS.get(genre_key, _GENRE_ACCENTS["B"])

    prompts = []
    for i, v in enumerate(variants[:N_VARIANTS]):
        vid           = v.get("id", i + 1)
        text_pos      = v.get("text_position", "bottom-left")
        # Per-variant genre (can differ from global if write_tz() set it)
        v_genre_key   = v.get("variant_genre_key", genre_key)
        if v_genre_key not in _GENRE_MATRICES_DETAILED:
            v_genre_key = genre_key
        v_genre       = _GENRE_MATRICES_DETAILED[v_genre_key]

        # ── Palette — unique accent forced per variant ────────────────────────
        palette = v.get("palette", {})
        dom = palette.get("dominant_70") or (
            v_genre["palette"].split("/")[0].strip() if "/" in v_genre["palette"]
            else v_genre["palette"]
        )
        sec = palette.get("secondary_25") or (
            v_genre["palette"].split("/")[1].strip()
            if v_genre["palette"].count("/") >= 1 else "cold light"
        )
        # Force unique accent from pre-assigned list
        acc = accents[i % len(accents)]
        palette_str = f"Dominant 70%: {dom} | Secondary 25%: {sec} | Accent 5%: {acc}"

        # ── Light direction for TEXT INTEGRATION ─────────────────────────────
        lighting_raw = v.get("lighting", "")
        light_dir    = "upper-left"
        for token in lighting_raw.lower().split():
            if token in ("upper-left", "upper-right", "left", "right",
                         "top-left", "top-right", "back", "front", "lateral"):
                light_dir = token
                break

        text_display = f'"{line1}" / "{line2}"' if line1 else f'"{line2}"'
        text_ref     = v_genre["refs"].split(",")[0].strip()
        text_integration_block = _TEXT_INTEGRATION_TEMPLATE.format(
            text=text_display,
            position=text_pos,
            light_direction=light_dir,
            reference=text_ref,
        )

        # ── OSINT (genre A / C) ───────────────────────────────────────────────
        annotation_target = v.get("annotation_target", "the main subject")
        if v_genre_key in ("A", "C"):
            osint_line = (
                f"Red circle (outline only, 2-3px, #FF1A1A, hand-drawn quality) "
                f"tight around: {annotation_target}. "
                f"Red arrow (triangular head, #FF1A1A) diagonal from text block to target. "
                f"Both layered OVER photo — NOT painted into scene. "
                f"Bellingcat forensic markup aesthetic."
            )
            check6 = (
                f'OSINT: red circle + red arrow drawn OVER image, NOT painted in. '
                f'Target: "{annotation_target}". Bellingcat style.'
            )
        else:
            osint_line = f"No OSINT annotations for genre {v_genre_key}."
            check6     = f"Genre {v_genre_key}: no OSINT annotations needed."

        cine = v.get("cinematographer", "the border-crossing wide in Sicario (2015), Deakins")
        obj_identifier = v.get("object_identifier", v.get("object_name", ""))

        # ── Build Gemini prompt ───────────────────────────────────────────────
        gen_prompt = f"""\
You are writing ONE image generation prompt for **Google Imagen 2** (Google Flow).

Google Imagen 2: photorealistic textures, scientific accuracy, atmospheric lighting, baked-in text.

━━━ 8 ADDITIONAL RULES (apply all, no exceptions) ━━━

RULE 1 — GENRE CODE mandatory in [GENRE CODE] block. Include film title, year, specific scene.
RULE 2 — PALETTE unique to this variant. Pre-assigned accent: {acc}. Use ONLY this accent.
RULE 3 — MOMENT = FROZEN INSTANT. Forbidden: {_MOMENT_FORBIDDEN}. Required: {_MOMENT_REQUIRED}
RULE 4 — EMOTION unique: this is variant {vid} of {N_VARIANTS}. Its emotion must differ from others.
RULE 5 — OUTPUT FORMAT: 11 marked blocks with [BLOCK_NAME] prefixes. NOT a flowing paragraph.
RULE 6 — TEXT/OBJECT COMPETITION: text is at [{text_pos}]. Object and shadows must NOT dominate {text_pos}.
RULE 7 — Film reference: include title, year, specific scene. NOT "van Hoytema style" but "the corridor chase in 'Tenet' (2020), van Hoytema".
RULE 8 — Object identifier: use human-recognisable visual handle, not just scientific term.
          "{v.get("object_name","")}" → visual handle: "{obj_identifier}"
RULE 9 — ATMOSPHERIC TEXT ZONE:
{_ATMOSPHERIC_TEXT_ZONE_RULE}
RULE 10 — OBJECT IDENTIFIER FORMAT:
{_OBJECT_IDENTIFIER_RULE}

━━━ 11-BLOCK INPUT DATA ━━━

[1] THUMBNAIL FUNCTION: {thumb_fn.get("hook_type","")} — {thumb_fn.get("click_trigger","")}
[2] GENRE: {v_genre_key} — {v_genre["name"]}
    Film refs: {v_genre["refs"]}
[3] OBJECT: {v.get("object_name","")}
    Visual handle: {obj_identifier}
    Details: {v.get("object_details","")}
[4] MOMENT (frozen instant): {v.get("moment","")}
[5] LIGHTING: {lighting_raw}
[6] LENS: {v.get("lens","")} | Cinematographer: {cine}
[7] ENVIRONMENT: {v.get("environment","")}
[8] TEXT ZONE: {v.get("dark_zone","")} | Position: {text_pos} | Object must NOT fill this zone
[9] PALETTE: {palette_str}
[10] TEXT:
{text_integration_block}
[11] EMOTION (unique): {v.get("emotion","")}
     ANTI-REFS: {v_genre["anti_refs"]}
     OSINT: {osint_line}

━━━ FORBIDDEN → CORRECT REPLACEMENTS ━━━
{_FORBIDDEN_REPLACEMENTS}

━━━ 9 PRE-GENERATION CHECKS ━━━
1. No forbidden phrase appears in output
2. Text verbatim: {text_display} — zero changes
3. Object = "{obj_identifier}" — real shape/details, no generic stand-in
4. Palette 70/25/5, accent MUST be: {acc}
5. Text integration: rim light on letter edges, NO bar/panel/overlay
6. {check6}
7. Film ref includes title + year + scene name
8. If scene has atmospheric haze/storm: text zone is foreground terrain in shadow — NOT the haze itself
9. Object identifier is an EVENT or MOMENT (date/mission/outcome), NOT a process or method
{critique_note}
━━━ OUTPUT: 11 MARKED BLOCKS ━━━

Write the prompt as 11 lines, each starting with [BLOCK_NAME]:

[GENRE CODE] <code> — <name> | Ref: <Film title (Year), specific scene>
[OBJECT] <visual handle> | <scientific name> | <4-6 details: shape, color, texture, scale, distinctive mark>
[MOMENT] <frozen instant — "The exact frame of..." / "The instant..." / "Caught at the precise second...">
[LIGHTING] <direction> | <Kelvin temp> | <shadow quality> | <fill: none/soft/hard>
[LENS] <focal length> | <aperture / DOF> | <perspective effect> | <Cinematographer, Film (Year), scene>
[ENVIRONMENT] <what surrounds> | ABSENT: <what is NOT in frame>
[TEXT ZONE] <position> | <why this zone is dark/clear> | <confirm: object does not fill this corner>
[TEXT] {text_display} | monumental billboard scale, Christopher Nolan title weight | rim light {light_dir} edges | 2-3px black stroke | 8-10px shadow 80% opacity | film grain on letter surfaces | NO bar NO overlay
[OSINT] {osint_line}
[PALETTE] Dominant 70%: <X> | Secondary 25%: <Y> | Accent 5%: {acc}
[EMOTION] <one precise sentence — unique emotional register for variant {vid}>

Return ONLY valid JSON:
{{"concept": "<one-line: object + frozen instant>", "prompt": "<11-block prompt>", "text_position": "{text_pos}"}}
"""
        try:
            raw  = _flash(gen_prompt, max_tokens=1200)
            data = _parse_json(raw)
            concept      = data.get("concept", f"variant {vid}")
            full_p       = data.get("prompt", "")
            text_pos_out = data.get("text_position", text_pos)

            prompts.append({
                "id":      vid,
                "concept": concept,
                "prompt":  full_p,
            })
            print(f"  [{i+1}] {concept} | text: {text_pos_out}", flush=True)
            # Print first block only
            first_line = full_p.split("\n")[0][:100] if full_p else ""
            print(f"       {first_line}...", flush=True)

        except Exception as e:
            print(f"  ⚠ Prompt error for v{vid}: {e}", flush=True)
            fallback = (
                f"[GENRE CODE] {v_genre_key} — {v_genre['name']}\n"
                f"[OBJECT] {obj_identifier} | {v.get('object_name','')}\n"
                f"[MOMENT] The exact frame of: {v.get('moment','')}\n"
                f"[LIGHTING] {lighting_raw}\n"
                f"[LENS] {v.get('lens','')}\n"
                f"[ENVIRONMENT] {v.get('environment','')}\n"
                f"[TEXT ZONE] {text_pos}\n"
                f"[TEXT] {text_display} | monumental billboard scale | NO overlay\n"
                f"[OSINT] {osint_line}\n"
                f"[PALETTE] {palette_str}\n"
                f"[EMOTION] {v.get('emotion','')}"
            )
            prompts.append({
                "id":      vid,
                "concept": v.get("object_name", f"variant {vid}"),
                "prompt":  fallback,
            })

    return prompts


# ── Image provider config ─────────────────────────────────────────────────────

def _get_image_provider() -> str:
    """flow | pixel — из .env THUMBNAIL_IMAGE_PROVIDER, дефолт pixel."""
    return os.environ.get("THUMBNAIL_IMAGE_PROVIDER", "pixel").lower().strip()


def _get_flow_platform() -> dict | None:
    try:
        from utils import PLATFORMS
        p = PLATFORMS["1"].copy()
        p["key"] = "1"
        return p
    except Exception as e:
        print(f"  [Flow] Cannot load platform config: {e}", flush=True)
        return None


# ── Stage 3a: Image Generation via Google Flow ────────────────────────────────

def generate_images_flow(prompts: list[dict], out_dir: Path, round_num: int) -> list[dict]:
    """Генерирует изображения через Google Flow (браузер + куки)."""
    print(f"\n[3/3] Generating {len(prompts)} images via Google Flow (round {round_num})...", flush=True)
    try:
        from flow_agent import generate_flow_images
    except ImportError as e:
        print(f"  ⚠ flow_agent import error: {e} — falling back to PixelAgent", flush=True)
        return generate_images_pixel(prompts, out_dir, round_num)

    platform = _get_flow_platform()
    if not platform:
        print("  ⚠ Flow platform config unavailable — falling back to PixelAgent", flush=True)
        return generate_images_pixel(prompts, out_dir, round_num)

    results = generate_flow_images(platform, prompts, out_dir, round_num)
    ok = sum(1 for r in results if r.get("path") and Path(r["path"]).exists())
    print(f"  Flow done: {ok}/{len(prompts)}", flush=True)
    return results


# ── Stage 3: Parallel Image Generation ───────────────────────────────────────

async def _generate_one(
    session: aiohttp.ClientSession,
    api_url: str,
    prompt_data: dict,
    out_path: Path,
) -> dict:
    pid    = prompt_data["id"]
    prompt = prompt_data["prompt"]
    try:
        async with session.post(
            f"{api_url}/api/v1/image/create",
            json={"prompt": prompt, "aspect_ratio": "16:9"},
            timeout=aiohttp.ClientTimeout(total=200),
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                print(f"  ⚠ [{pid}] HTTP {resp.status}: {text[:80]}", flush=True)
                return {"id": pid, "path": None, "prompt": prompt}
            data = await resp.json()
        img_b64 = data.get("image_b64") or data.get("image")
        if not img_b64:
            print(f"  ⚠ [{pid}] no image in response", flush=True)
            return {"id": pid, "path": None, "prompt": prompt}
        img_bytes = base64.b64decode(img_b64)
        out_path.write_bytes(img_bytes)
        print(f"  ✅ [{pid}] {out_path.name} ({len(img_bytes)//1024}KB)", flush=True)
        return {"id": pid, "path": out_path, "prompt": prompt}
    except Exception as e:
        print(f"  ⚠ [{pid}] {e}", flush=True)
        return {"id": pid, "path": None, "prompt": prompt}


async def _generate_all(prompts: list[dict], out_dir: Path, round_num: int) -> list[dict]:
    api_url = os.environ.get("PIXEL_API_URL", "").rstrip("/")
    api_key = os.environ.get("PIXEL_API_KEY", "")
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": api_key,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    tasks = []
    async with aiohttp.ClientSession(headers=headers) as session:
        for p in prompts:
            out_path = out_dir / f"r{round_num}_v{p['id']}.png"
            tasks.append(_generate_one(session, api_url, p, out_path))
        return await asyncio.gather(*tasks)


def generate_images_pixel(prompts: list[dict], out_dir: Path, round_num: int) -> list[dict]:
    """Генерирует изображения через PixelAgent API (параллельно)."""
    print(f"\n[3/3] Generating {len(prompts)} images via PixelAgent (round {round_num})...", flush=True)
    results = asyncio.run(_generate_all(prompts, out_dir, round_num))
    ok = sum(1 for r in results if r["path"])
    print(f"  Done: {ok}/{len(prompts)}", flush=True)
    return results


def generate_images(prompts: list[dict], out_dir: Path, round_num: int) -> list[dict]:
    """Роутер: Google Flow или PixelAgent — задаётся THUMBNAIL_IMAGE_PROVIDER в .env."""
    provider = _get_image_provider()
    if provider == "flow":
        return generate_images_flow(prompts, out_dir, round_num)
    return generate_images_pixel(prompts, out_dir, round_num)


# ── Stage 4: Flash Evaluation ─────────────────────────────────────────────────

def _build_eval_prompt(thumbnail_text: str, text_line1: str, text_line2: str, script_topic: str) -> str:
    expected = f'"{text_line2}"' if not text_line1 else f'"{text_line1}" (secondary) + "{text_line2}" (hero)'
    return f"""\
You are a YouTube thumbnail critic for the German space/cosmos documentary niche.
Target audience: Germans 60-70+. ZDF documentary viewers. Spiegel/Stern readers.
They click when: (1) text is instantly readable, (2) image creates a specific emotional reaction.
They scroll past: sci-fi fantasy visuals, cluttered compositions, generic stock-photo aesthetics.
Gold standard for this audience: Spiegel magazine covers, Terra X documentary stills, BBC documentary frames.

Evaluate this thumbnail brutally across all dimensions.

EXPECTED TEXT: {expected}
(Original: "{thumbnail_text}")
VIDEO TOPIC: {script_topic}

Score 1-10. Be BRUTAL — a 7 means "competent but forgettable". 8+ means "would genuinely stop the scroll".

TEXT QUALITY:
1. text_correctness: Is "{text_line1}" and "{text_line2}" visible, correctly spelled, no distortion?
   Missing/garbled/merged into image = 1-2. Crisp and correct = 9-10.
2. text_readability: Can a 65-year-old read both lines on a phone screen at 120px thumbnail size?
   Hero text must be BILLBOARD-scale — monumental, not merely "large". Caption-size = 1-3. Monumental = 9-10.
3. text_integration: Does the text LIVE IN the scene or sit ON TOP like a sticker?
   Text architecturally integrated into a naturally dark zone of the frame = 9-10.
   Text pasted over bright busy areas = 1-4. Floating text with obvious digital overlay bar = 3-5.
4. text_font_style: Ultra-bold heavy condensed white — the weight of a cinema title card?
   Thin/decorative/distressed/sci-fi/italic fonts = 1-3. Clean monumental heavy condensed = 8-10.

VISUAL QUALITY:
5. composition: ONE dominant photorealistic subject filling 60-80% of frame?
   Clear visual hierarchy, unambiguous, not cluttered. Empty/generic = 3-5. Decisive = 8-10.
6. cinematic_quality: Does this feel like a frame from a real documentary — tactile, photographic, NOT CGI-rendered?
   GEO magazine / leaked NASA photo quality = 9-10. Concept art / CGI cleanliness = 2-4.
7. visual_impact: What does the viewer FEEL in 0.3 seconds? (not what they see)
   Specific emotional reaction (dread, awe, curiosity, shock) = 8-10. Generic "looks cool" = 4-6.
8. color_palette: 3-4 colors max, high contrast, emotionally weighted?
   Neon chaos / too many colors = 2-4. Deliberate cinematic palette = 8-10.

SEMANTIC + SEO:
9. topic_match: Does the image tell the story of "{script_topic}" WITHOUT the text?
   Unmistakable match = 9-10. Vague/generic space imagery = 3-5.
10. niche_fit: Terra X / ZDF / Spiegel authority aesthetic — not MrBeast, not sci-fi poster?
    Clean documentary authority = 8-10. Generic YouTube clickbait = 2-4.
11. ctr_potential: Would a real German 65+ viewer actually click this?
    Honest assessment — consider text readability + emotional hook + topic clarity together.
12. overall: Strict. 8+ ONLY if: text is monumental + scene is cinematic + topic is unmistakable.

Return ONLY valid JSON:
{{
  "scores": {{
    "text_correctness": <1-10>,
    "text_readability": <1-10>,
    "text_integration": <1-10>,
    "text_font_style": <1-10>,
    "composition": <1-10>,
    "cinematic_quality": <1-10>,
    "visual_impact": <1-10>,
    "color_palette": <1-10>,
    "topic_match": <1-10>,
    "niche_fit": <1-10>,
    "ctr_potential": <1-10>,
    "overall": <1-10>
  }},
  "passed": <true if overall >= 8 else false>,
  "text_issues": "<specific text problems or null>",
  "visual_issues": "<specific visual problems or null>",
  "seo_issues": "<why this won't get clicked, or null>",
  "what_works": "<what is genuinely strong>"
}}
"""

def evaluate_images(
    images: list[dict],
    round_num: int,
    thumbnail_text: str,
    tz: dict,
    script_topic: str,
) -> list[dict]:
    valid = [img for img in images if img["path"] and Path(img["path"]).exists()]
    if not valid:
        print("  ⚠ No valid images to evaluate", flush=True)
        return []

    print(f"\n[Eval] Evaluating {len(valid)} thumbnails (round {round_num})...", flush=True)
    eval_prompt = _build_eval_prompt(
        thumbnail_text,
        tz.get("text_line1", ""),
        tz.get("text_line2", thumbnail_text),
        script_topic,
    )

    results = []
    for img in valid:
        img_bytes = Path(img["path"]).read_bytes()
        try:
            raw    = _flash(eval_prompt, images=[img_bytes], max_tokens=700)
            data   = _parse_json(raw)
            scores  = data.get("scores", {})
            passed  = data.get("passed", False)
            overall = scores.get("overall", 0)

            txt_issues = data.get("text_issues") or ""
            vis_issues = data.get("visual_issues") or ""
            seo_issues = data.get("seo_issues") or ""
            good       = data.get("what_works", "")

            print(
                f"  [r{round_num}_v{img['id']}] overall={overall} passed={passed}",
                flush=True,
            )
            print(
                f"    text: correct={scores.get('text_correctness','?')} "
                f"read={scores.get('text_readability','?')} "
                f"integ={scores.get('text_integration','?')} "
                f"font={scores.get('text_font_style','?')}",
                flush=True,
            )
            print(
                f"    visual: comp={scores.get('composition','?')} "
                f"cinema={scores.get('cinematic_quality','?')} "
                f"impact={scores.get('visual_impact','?')} "
                f"palette={scores.get('color_palette','?')}",
                flush=True,
            )
            print(
                f"    seo: topic={scores.get('topic_match','?')} "
                f"niche={scores.get('niche_fit','?')} "
                f"ctr={scores.get('ctr_potential','?')}",
                flush=True,
            )
            if passed:
                print(f"    ✅ {good}", flush=True)
            else:
                if txt_issues:
                    print(f"    ❌ TEXT:   {txt_issues}", flush=True)
                if vis_issues:
                    print(f"    ❌ VISUAL: {vis_issues}", flush=True)
                if seo_issues:
                    print(f"    ❌ SEO:    {seo_issues}", flush=True)

            # Собрать проблемы как список для рефайна промптов
            problems = [p for p in [txt_issues, vis_issues, seo_issues] if p]

            results.append({
                **img,
                "scores":     scores,
                "overall":    overall,
                "passed":     passed,
                "problems":   problems,
                "what_works": good,
            })
        except Exception as e:
            print(f"  ⚠ Eval error for v{img['id']}: {e}", flush=True)
            results.append({**img, "scores": {}, "overall": 0, "passed": False,
                            "problems": ["eval_error"], "what_works": ""})
    return results


def _collect_critiques(evaluated: list[dict]) -> list[str]:
    """Собрать конкретные проблемы из всех оцененных вариантов."""
    seen = set()
    critiques = []
    for r in evaluated:
        for prob in r.get("problems", []):
            if prob and prob not in seen:
                seen.add(prob)
                critiques.append(prob)
    return critiques[:6]


# ── Edit Pass ─────────────────────────────────────────────────────────────────

ACCURACY_PASS_SCORE  = 9   # порог точности объекта (1-10) — ниже → edit pass (с реальным фото)


def _flash_with_search(prompt: str, image_bytes: bytes | None = None) -> str:
    """Flash с Google Search grounding."""
    from google.genai import types as gtypes
    client = _get_gemini()

    parts = []
    if image_bytes:
        from PIL import Image as PILImage
        pil = PILImage.open(io.BytesIO(image_bytes)).convert("RGB")
        buf = io.BytesIO()
        pil.save(buf, format="JPEG", quality=85)
        parts.append(gtypes.Part.from_bytes(data=buf.getvalue(), mime_type="image/jpeg"))
    parts.append(gtypes.Part.from_text(text=prompt))

    attempt = 0
    while True:
        try:
            resp = client.models.generate_content(
                model=FLASH_MODEL,
                contents=parts,
                config=gtypes.GenerateContentConfig(
                    tools=[gtypes.Tool(google_search=gtypes.GoogleSearch())],
                    temperature=0.3,
                    max_output_tokens=600,
                    thinking_config=gtypes.ThinkingConfig(thinking_budget=0),
                ),
            )
            return (resp.text or "").strip()
        except Exception as e:
            err = str(e)
            if "503" in err or "UNAVAILABLE" in err or "429" in err:
                attempt += 1
                wait = min(15 * attempt, 60)
                print(f"  [Flash+Search] retry #{attempt} in {wait}s...", flush=True)
                time.sleep(wait)
            else:
                raise


def _check_object_accuracy(img_result: dict, tz: dict) -> dict:
    """
    Строгая проверка: соответствует ли изображённый объект реальному по форме, деталям, параметрам.
    Возвращает {"score": 1-10, "accurate": bool, "issues": str, "object_name": str}
    """
    main_object = tz.get("main_object", "")
    img_bytes   = Path(img_result["path"]).read_bytes()

    prompt = f"""\
You are a strict visual fact-checker for space/science imagery.

The intended main object in this thumbnail is: "{main_object}"

Examine the image VERY carefully and answer:
1. Is the depicted object ACCURATELY representing the real "{main_object.split('—')[0].strip()}"?
   Check: exact shape, proportions, key structural details, color, distinctive markings.

   Examples of what to check:
   - James Webb Space Telescope: 18 hexagonal gold mirror segments, 5-layer sunshield (kite-shaped), silver struts — NOT a generic round telescope
   - Hubble: cylindrical silver body, two rectangular blue solar panels — NOT hexagonal mirrors
   - ISS: modular structure, large X-shaped solar arrays, Soyuz/Dragon docked
   - Mars: red/orange rocky surface, thin atmosphere haze, Olympus Mons if wide shot
   - Black hole: accretion disk (orange/yellow ring), photon ring, gravitational lensing — NOT a generic vortex

2. Rate accuracy 1-10 (10 = photographic accuracy, 1 = completely wrong/generic)
3. List the specific shape/detail errors if any

Return ONLY valid JSON:
{{"score": <1-10>, "accurate": <true if score >= {ACCURACY_PASS_SCORE}>, "issues": "<specific shape/detail errors or null>", "object_name": "<exact real name of the object>"}}"""

    try:
        raw  = _flash(prompt, images=[img_bytes], max_tokens=300)
        data = _parse_json(raw)
        score = data.get("score", 10)
        print(
            f"  [Accuracy] {data.get('object_name','')} score={score}/10 "
            f"accurate={data.get('accurate',True)}",
            flush=True,
        )
        if not data.get("accurate", True):
            print(f"    Issues: {data.get('issues','')}", flush=True)
        return data
    except Exception as e:
        print(f"  [Accuracy] check failed: {e}", flush=True)
        return {"score": 10, "accurate": True, "issues": None, "object_name": ""}


def _fetch_reference_image(object_name: str) -> bytes | None:
    """
    Flash с Google Search находит реальное фото объекта.
    Возвращает bytes изображения или None.
    """
    import urllib.request

    prompt = f"""\
Search Google Images for the best official NASA or ESA photograph of "{object_name}".
Find a high-quality photo that clearly shows its real shape and details.
Return ONLY the direct image URL (ending in .jpg or .png). Nothing else."""

    try:
        url = _flash_with_search(prompt)
        # Вытащить URL из ответа
        url = url.strip().strip('"\'').split()[0]
        if not url.startswith("http"):
            print(f"  [RefImg] no valid URL from search", flush=True)
            return None
        print(f"  [RefImg] downloading: {url[:80]}", flush=True)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = r.read()
        if len(data) > 10000:
            print(f"  [RefImg] got {len(data)//1024}KB", flush=True)
            return data
    except Exception as e:
        print(f"  [RefImg] failed: {e}", flush=True)
    return None


def _build_edit_instruction(
    tz: dict,
    ref_image: bytes,
) -> tuple[bytes, str]:
    """
    Базовое изображение = реальное фото объекта.
    Instruction = только добавить текст сверху + лёгкая стилизация.
    Возвращает (base_image_bytes, edit_instruction).
    """
    text_line1  = tz.get("text_line1", "")
    text_line2  = tz.get("text_line2", "")
    placement   = tz.get("text_placement", "upper-left")
    color_palette = tz.get("color_palette", "deep space black")

    if text_line1 and text_line2:
        text_req = (
            f'Add text overlay at {placement}: '
            f'"{text_line1}" in large bold white condensed font with dark shadow, '
            f'and below it "{text_line2}" in even LARGER ultra-bold white condensed font. '
            f'Both texts must be in the same language as written — do NOT translate.'
        )
    else:
        text_req = (
            f'Add text "{text_line2}" at {placement} — '
            f'enormous ultra-bold white condensed font, strong dark shadow.'
        )

    instruction = (
        f"YouTube thumbnail style. {text_req} "
        f"Enhance with dramatic space lighting and {color_palette}. "
        f"Keep the main object clearly visible and photorealistic."
    )

    print(f"  [Edit] Instruction: {instruction}", flush=True)
    return ref_image, instruction


async def _edit_one(
    session: aiohttp.ClientSession,
    api_url: str,
    pid: int | str,
    base_image_bytes: bytes,
    edit_instruction: str,
    out_path: Path,
) -> dict:
    img_b64 = base64.b64encode(base_image_bytes).decode()
    try:
        async with session.post(
            f"{api_url}/api/v1/image/edit",
            json={
                "reference_image_b64": img_b64,
                "edit_instruction":    edit_instruction,
                "aspect_ratio":        "16:9",
                "generation_mode":     "quality",
            },
            timeout=aiohttp.ClientTimeout(total=240),
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                print(f"  ⚠ [edit {pid}] HTTP {resp.status}: {text[:80]}", flush=True)
                return {"id": pid, "path": None}
            data = await resp.json()
        img_b64r = data.get("image_b64") or data.get("image")
        if not img_b64r:
            print(f"  ⚠ [edit {pid}] no image in response", flush=True)
            return {"id": pid, "path": None}
        edited_bytes = base64.b64decode(img_b64r)
        out_path.write_bytes(edited_bytes)
        print(f"  ✅ [edit {pid}] {out_path.name} ({len(edited_bytes)//1024}KB)", flush=True)
        return {"id": pid, "path": out_path, "prompt": edit_instruction}
    except Exception as e:
        print(f"  ⚠ [edit {pid}] {e}", flush=True)
        return {"id": pid, "path": None}


def edit_pass(
    evaluated: list[dict],
    tz: dict,
    script_topic: str,
    round_num: int,
    variants_dir: Path,
) -> list[dict]:
    """
    Строгая проверка точности объектов → если неточно → Flash+Search находит реальное фото →
    PixelAgent edit (на базе реального фото или текущего) → возвращает отредактированные варианты.
    """
    api_url = os.environ.get("PIXEL_API_URL", "").rstrip("/")
    api_key = os.environ.get("PIXEL_API_KEY", "")

    valid = [
        r for r in evaluated
        if r.get("path") and Path(r["path"]).exists()
    ]
    if not valid:
        return []

    # Проверяем точность объектов
    print(f"\n[Edit Pass] Проверка точности объектов ({len(valid)} вариантов)...", flush=True)
    edit_tasks = []
    for r in valid:
        accuracy = _check_object_accuracy(r, tz)
        if accuracy.get("accurate", True):
            continue  # объект точный — не трогаем

        object_name = accuracy.get("object_name", "")
        ref_image   = _fetch_reference_image(object_name) if object_name else None
        if not ref_image:
            print(f"  [Edit Pass] v{r['id']} — референс не найден, пропускаем", flush=True)
            continue  # без реального фото не редактируем

        base_bytes, instruction = _build_edit_instruction(tz, ref_image)
        edit_tasks.append((r, base_bytes, instruction))

    if not edit_tasks:
        print(f"  [Edit Pass] Все объекты точные — пропускаем", flush=True)
        return []

    print(f"  [Edit Pass] {len(edit_tasks)} изображений на редактирование...", flush=True)

    async def _run_all():
        headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
        async with aiohttp.ClientSession(headers=headers) as sess:
            tasks = []
            for r, base_bytes, instr in edit_tasks:
                out_path = variants_dir / f"r{round_num}_v{r['id']}_edit.png"
                tasks.append(_edit_one(sess, api_url, r["id"], base_bytes, instr, out_path))
            return await asyncio.gather(*tasks)

    results = asyncio.run(_run_all())
    return [r for r in results if r.get("path") and Path(r["path"]).exists()]


# ── SEO Report ───────────────────────────────────────────────────────────────

def _write_seo_report(
    out_dir: Path,
    session: str,
    thumbnail_text: str,
    tz: dict,
    winner: dict,
    best_overall: int,
    all_results: list[dict],
) -> None:
    scores = winner.get("scores", {})
    lines = []
    lines.append("=" * 60)
    lines.append("  THUMBNAIL SEO REPORT")
    lines.append("=" * 60)
    lines.append(f"Session:  {session}")
    lines.append(f"Text:     '{thumbnail_text}'")
    lines.append(f"Line1:    '{tz.get('text_line1','')}'")
    lines.append(f"Line2:    '{tz.get('text_line2','')}'  ← main bold")
    lines.append("")

    lines.append("── CREATIVE BRIEF (TZ) ─────────────────────────────────")
    lines.append(f"Topic:            {tz.get('topic_summary','')}")
    lines.append(f"Genre:            {tz.get('genre_key','')} — {_GENRE_MATRICES_DETAILED.get(tz.get('genre_key','B'),{}).get('name','')}")
    lines.append(f"Emotional task:   {tz.get('emotional_task','')}")
    lines.append("")
    for v in tz.get("variants", []):
        palette = v.get("palette", {})
        lines.append(f"  V{v.get('id')}: {v.get('object_name','')}")
        lines.append(f"    Moment:    {v.get('moment','')}")
        lines.append(f"    Lighting:  {v.get('lighting','')}")
        lines.append(f"    Lens:      {v.get('lens','')} | {v.get('cinematographer','')}")
        lines.append(f"    Palette:   70%: {palette.get('dominant_70','')} | 25%: {palette.get('secondary_25','')} | 5%: {palette.get('accent_5','')}")
        lines.append(f"    Text pos:  {v.get('text_position','')} | Emotion: {v.get('emotion','')}")
    lines.append("")

    lines.append("── WINNER SCORES ────────────────────────────────────────")
    lines.append(f"OVERALL:          {best_overall}/10")
    lines.append("")
    lines.append("  TEXT")
    lines.append(f"    text_correctness:  {scores.get('text_correctness','?')}/10  — text visible & correctly spelled")
    lines.append(f"    text_readability:  {scores.get('text_readability','?')}/10  — billboard-scale, readable at 120px")
    lines.append(f"    text_integration:  {scores.get('text_integration','?')}/10  — lives in scene, not pasted on top")
    lines.append(f"    text_font_style:   {scores.get('text_font_style','?')}/10  — ultra bold condensed, cinema title weight")
    lines.append("")
    lines.append("  VISUAL")
    lines.append(f"    composition:       {scores.get('composition','?')}/10  — dominant object, clear visual hierarchy")
    lines.append(f"    cinematic_quality: {scores.get('cinematic_quality','?')}/10  — tactile photo feel, not CGI")
    lines.append(f"    visual_impact:     {scores.get('visual_impact','?')}/10  — emotional reaction in 0.3s")
    lines.append(f"    color_palette:     {scores.get('color_palette','?')}/10  — deliberate 3-4 color cinematic palette")
    lines.append("")
    lines.append("  SEO / CTR")
    lines.append(f"    topic_match:       {scores.get('topic_match','?')}/10  — matches video content")
    lines.append(f"    niche_fit:         {scores.get('niche_fit','?')}/10  — fits top German cosmos channels")
    lines.append(f"    ctr_potential:     {scores.get('ctr_potential','?')}/10  — YouTube click-through potential")
    lines.append("")

    lines.append("── FLASH VERDICT ────────────────────────────────────────")
    lines.append(winner.get("what_works", ""))
    lines.append("")

    lines.append("── ALL VARIANTS ─────────────────────────────────────────")
    for r in all_results:
        s = r.get("scores", {})
        status = "✅ PASSED" if r.get("passed") else "❌ FAILED"
        path = Path(r["path"]).name if r.get("path") else "no image"
        lines.append(
            f"  {path:20s}  overall={r.get('overall','?')}  {status}"
        )
        lines.append(
            f"    text: {s.get('text_correctness','?')}/{s.get('text_readability','?')}/{s.get('text_integration','?')}/{s.get('text_font_style','?')}  "
            f"visual: {s.get('composition','?')}/{s.get('cinematic_quality','?')}/{s.get('visual_impact','?')}  "
            f"seo: {s.get('topic_match','?')}/{s.get('niche_fit','?')}/{s.get('ctr_potential','?')}"
        )
        probs = r.get("problems", [])
        if probs:
            for p in probs:
                lines.append(f"    ⚠ {p}")
    lines.append("")
    lines.append("=" * 60)

    txt_path = out_dir / "seo_report.txt"
    txt_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"   → {txt_path}", flush=True)


# ── Main Pipeline ─────────────────────────────────────────────────────────────

def run_pipeline(channel_id: str, session: str, thumbnail_text: str, out_dir: Path) -> Path:
    result_json = get_result_json(channel_id, session)
    with open(result_json, encoding="utf-8") as f:
        data = json.load(f)
    segments = data if isinstance(data, list) else data.get("segments", [])
    print(f"Loaded {len(segments)} segments", flush=True)
    print(f"Text: '{thumbnail_text}'", flush=True)

    out_dir.mkdir(parents=True, exist_ok=True)
    variants_dir = out_dir / "variants"
    variants_dir.mkdir(exist_ok=True)

    # Stage 0 — Thumbnail function (hook type, click trigger, genre)
    thumb_fn = write_thumbnail_function(segments, thumbnail_text)

    # Stage 1 — TZ (once, not per round)
    tz = write_tz(segments, thumbnail_text, thumb_fn)
    script_topic = tz.get("emotional_task", thumbnail_text)

    # Один раунд — генерируем N_VARIANTS, юзер сам выбирает лучшее
    print(f"\n{'─'*55}", flush=True)
    print(f"  Generating {N_VARIANTS} variants...", flush=True)
    print(f"{'─'*55}", flush=True)

    prompts = generate_prompts(tz, thumb_fn, round_num=1)
    images  = generate_images(prompts, variants_dir, round_num=1)

    valid = [r for r in images if r.get("path") and Path(r["path"]).exists()]
    if not valid:
        raise RuntimeError("No thumbnail generated successfully")

    # Конвертируем все варианты в PNG (из JPEG) и кладём в variants/
    import shutil
    from PIL import Image as _PILImage

    for r in valid:
        src = Path(r["path"])
        if src.suffix.lower() in (".jpg", ".jpeg"):
            png_path = src.with_suffix(".png")
            try:
                _PILImage.open(src).convert("RGB").save(png_path, format="PNG")
                src.unlink()          # удаляем JPEG
                r["path"] = png_path  # обновляем путь в результате
                print(f"  PNG: {png_path.name}", flush=True)
            except Exception as e:
                print(f"  [!] Конвертация PNG: {e}", flush=True)

    best_image = valid[0]
    best_overall = 0
    all_results = valid

    # Первый вариант — дефолтный thumbnail_final.png
    final_path = out_dir / "thumbnail_final.png"
    shutil.copy2(best_image["path"], final_path)

    print(f"\n{'='*55}", flush=True)
    print(f"  Done: {len(valid)}/{N_VARIANTS} images generated", flush=True)
    print(f"  Variants: {variants_dir}", flush=True)
    print(f"  Default:  {final_path}", flush=True)

    # JSON report
    report = {
        "session":        session,
        "channel_id":     channel_id,
        "thumbnail_text": thumbnail_text,
        "tz":             tz,
        "winner": {
            "path":       str(best_image["path"]),
            "overall":    best_overall,
            "scores":     best_image.get("scores", {}),
            "what_works": best_image.get("what_works", ""),
        },
        "all_results": [
            {k: str(v) if isinstance(v, Path) else v for k, v in r.items()}
            for r in all_results
        ],
    }
    (out_dir / "thumbnail_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # SEO TXT report
    _write_seo_report(out_dir, session, thumbnail_text, tz, best_image, best_overall, all_results)

    return final_path


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="YouTube Thumbnail Generator")
    parser.add_argument("--channel",   default="channel_001_cosmos_de")
    parser.add_argument("--session",   default=None)
    parser.add_argument("--text",      required=True, help='Thumbnail text')
    parser.add_argument("--dry-run",   action="store_true", help="Only generate prompts, skip image generation")
    args = parser.parse_args()

    channel_id = args.channel
    session    = args.session or get_last_session(channel_id)
    if not session:
        print("❌ No session found", flush=True)
        sys.exit(1)

    print(f"\n{'='*60}", flush=True)
    print(f"  THUMBNAIL AGENT", flush=True)
    print(f"  Channel: {channel_id}", flush=True)
    print(f"  Session: {session}", flush=True)
    print(f"  Text:    '{args.text}'", flush=True)
    print(f"{'='*60}", flush=True)

    session_dir = get_session_dir(channel_id, session)
    out_dir     = session_dir / "thumbnail"

    if args.dry_run:
        # Только генерация промптов, без картинок
        result_json = get_result_json(channel_id, session)
        with open(result_json, encoding="utf-8") as f:
            data = json.load(f)
        segments = data if isinstance(data, list) else data.get("segments", [])
        print(f"Loaded {len(segments)} segments", flush=True)
        thumb_fn = write_thumbnail_function(segments, args.text)
        tz       = write_tz(segments, args.text, thumb_fn)
        prompts  = generate_prompts(tz, thumb_fn, round_num=1)
        # Сохраняем промпты в файл
        out_dir.mkdir(parents=True, exist_ok=True)
        prompts_path = out_dir / "prompts.json"
        prompts_path.write_text(
            json.dumps(prompts, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n{'='*60}", flush=True)
        print("  PROMPTS:", flush=True)
        print(f"{'='*60}", flush=True)
        for p in prompts:
            print(f"\n--- Variant {p['id']}: {p['concept']}", flush=True)
            print(p['prompt'], flush=True)
        print(f"\n  Saved: {prompts_path}", flush=True)
        return

    t0 = time.time()
    final = run_pipeline(channel_id, session, args.text, out_dir)
    print(f"\n  Total: {round(time.time()-t0, 1)}s", flush=True)
    print(f"{'='*60}\n", flush=True)


if __name__ == "__main__":
    main()
