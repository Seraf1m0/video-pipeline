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
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agents" / "utils"))

load_dotenv(ROOT / "config" / ".env")

from paths import get_result_json, get_session_dir, get_last_session  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── Config ────────────────────────────────────────────────────────────────────

FLASH_MODEL    = "gemini-2.5-flash"
_NO_THINKING   = {"thinking_config": {"thinking_budget": 0}}
N_VARIANTS     = 3       # изображений за раунд
MAX_ROUNDS     = 4       # максимум раундов генерации+рефайна
PASS_THRESHOLD = 8       # минимальный overall score чтобы принять

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
            return resp.text.strip()
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


# ── Stage 1: Creative TZ ──────────────────────────────────────────────────────

def write_tz(segments: list[dict], thumbnail_text: str) -> dict:
    """Flash пишет визуальное ТЗ — сам решает иерархию и расположение текста."""
    print("\n[1/3] Writing creative brief (TZ)...", flush=True)
    script = " ".join(
        s.get("text", "") for s in segments if "[" not in s.get("text", "")
    )[:4000]

    prompt = f"""\
You are a YouTube thumbnail art director for a German space/cosmos documentary channel.

TARGET AUDIENCE: Germans aged 60-70+. They watch ZDF Dokumentationen, read Spiegel and Stern.
They are curious, intelligent — but they will NOT click on something they cannot READ instantly.
They do NOT respond well to sci-fi movie poster aesthetics, neon colors, or chaotic designs.
They respond to: CLEAR bold text, photorealistic imagery that matches the topic, authoritative mood.

The user wants this text on the thumbnail: "{thumbnail_text}"

Your job:
1. Analyze the script to understand the video topic and emotional hook
2. Decide HOW to use this text for maximum CTR impact:
   - Split into lines if needed (e.g. "DIE SONNE IST WACH" → "DIE SONNE IST" + "WACH")
   - OR keep as one dominant line
   - Main text must be MASSIVE — the kind a 65-year-old can read on a phone without glasses
   - Decide text hierarchy, size ratio, and placement
3. Choose a PHOTOREALISTIC main object that directly represents the video topic
   - Real spacecraft, real planets, real scientists — not fantasy/sci-fi designs
   - The image must tell the story BEFORE the viewer reads the text
   - Add a DRAMATIC MOMENT: something is happening, being revealed, or confronted — not static
4. Add INTRIGUE and BAIT — details that make the viewer lean in:
   - A second element in the background that raises questions
   - Dramatic lighting contrast (spotlight, rim light, glowing edge)
   - A subtle visual "secret" or detail that rewards close inspection
   - Clear sense of scale, danger, or revelation
5. Colors: vivid, high contrast, dramatic — deep blues, glowing oranges, sharp whites

Return ONLY valid JSON:
{{
  "text_line1": "<supporting text — context/setup, or empty string if single line. Must be large and bold — NOT tiny>",
  "text_line2": "<MAIN bold word(s) — the emotional hook, LARGEST on screen>",
  "text_hierarchy": "<BOTH lines must be large. Max ratio 1.5x. E.g. 'line2 is 1.3x larger than line1 — both are big bold headlines'>",
  "text_placement": "<upper-left | lower-left | upper-right — where text block sits>",
  "main_object": "<ONE dominant photorealistic object filling 60-80% of frame — dramatic moment, not static pose>",
  "emotional_tone": "<shock | revelation | urgency | awe | confrontation — strong and specific>",
  "color_palette": "<vivid, high contrast, e.g. 'deep space black + dramatic orange glow + sharp white highlights'>",
  "text_style": "<ultra-bold, clean white sans-serif — NO decorative fonts. Strong dark shadow or semi-transparent backing>",
  "graphic_elements": "<1-2 specific details that add intrigue: e.g. a glowing circle highlighting a detail, a subtle flag/symbol, dramatic rim lighting, a half-hidden second object>",
  "atmosphere": "<1 sentence: mood — tense, revelatory, urgent — documentary authority with emotional punch>",
  "why_people_click": "<specific psychological hook: what visual question does this thumbnail pose that DEMANDS an answer?>"
}}

SCRIPT:
{script}
"""
    result = _parse_json(_flash(prompt, max_tokens=700))
    print(f"  Line1:    '{result.get('text_line1','')}'", flush=True)
    print(f"  Line2:    '{result.get('text_line2','')}'", flush=True)
    print(f"  Hierarch: {result.get('text_hierarchy','')}", flush=True)
    print(f"  Object:   {result.get('main_object','')}", flush=True)
    print(f"  Elements: {result.get('graphic_elements','')}", flush=True)
    print(f"  Hook:     {result.get('why_people_click','')}", flush=True)
    return result


# ── Stage 2: Prompt Generation from TZ ───────────────────────────────────────

IMAGE_STYLE_SUFFIX = (
    "photorealistic, ultra detailed, 8K, dramatic documentary lighting, "
    "vivid natural colors, professional photography style, "
    "NO sci-fi fantasy elements unless topic requires it, "
    "16:9 aspect ratio"
)

def generate_prompts(
    tz: dict,
    round_num: int = 1,
    prev_critiques: list[str] | None = None,
) -> list[dict]:
    """Flash генерирует N детальных промптов из ТЗ."""
    print(f"\n[2/3] Generating {N_VARIANTS} prompts (round {round_num})...", flush=True)

    critique_block = ""
    if prev_critiques:
        joined = "\n".join(f"- {c}" for c in prev_critiques)
        critique_block = f"""\
CRITICAL — previous attempts FAILED. Specific problems identified:
{joined}

Fix ALL of these. Make text even BIGGER and CLEANER. Image must be more photorealistic.
Each prompt must be COMPLETELY DIFFERENT from previous attempts.
"""

    round_escalation = ""
    if round_num == 2:
        round_escalation = "ESCALATE: Text even larger. Simpler composition. Maximum clarity and readability."
    elif round_num == 3:
        round_escalation = "FINAL PUSH: Strip everything non-essential. ONE massive image, ONE huge text. Zero ambiguity."
    elif round_num == 4:
        round_escalation = "FINAL ATTEMPT: Completely rethink. Bold, simple, unmistakably readable. Like a Spiegel magazine cover."

    line1 = tz.get("text_line1", "")
    line2 = tz.get("text_line2", "")
    hierarchy = tz.get("text_hierarchy", "line2 is larger")

    prompt = f"""\
You are a master prompt engineer for AI image generation.
You create YouTube thumbnails for a German space documentary channel targeting viewers aged 60-70+.
These viewers use phones and tablets — text MUST be readable at thumbnail size without zooming.

CREATIVE BRIEF (TZ):
- Main object: {tz.get('main_object','')}
- Colors: {tz.get('color_palette','')}
- Text placement: {tz.get('text_placement','')}
- Text style: {tz.get('text_style','')}
- Graphic elements: {tz.get('graphic_elements','')}
- Atmosphere: {tz.get('atmosphere','')}

TEXT TO INCLUDE (mandatory, exact spelling):
{f'- Supporting text (line 1): "{line1}" — LARGE AND BOLD, minimum 15% of frame height' if line1 else '- No line 1 — single dominant line only'}
- MAIN bold text (line 2): "{line2}" — the HERO text
- Text hierarchy: {hierarchy} — MAX size ratio 1.5x between lines, BOTH must be headline-sized

TEXT RENDERING RULES (non-negotiable):
- "{line2}" must be ENORMOUS — letters 25-35% of frame HEIGHT, ultra-thick strokes
- "{line1}" if present — must ALSO be large, at least 12-15% of frame HEIGHT — NOT a tiny caption
- Both lines: ultra-bold heavy condensed, pure white, ZERO thin strokes, ZERO decorative serifs
- MANDATORY: solid dark semi-transparent bar or heavy drop shadow behind ALL text lines
- A 65-year-old must read BOTH lines instantly on a phone without zooming
- Text block in: {tz.get('text_placement','')}
- NO other text, NO logos, NO watermarks, NO digital/distressed fonts

IMAGE RULES:
- Photorealistic style — documentary/cinematic, grounded in reality
- The image tells the story BEFORE the text is read
- ONE dominant subject + 1-2 secondary details that add intrigue (not clutter)
- MANDATORY dramatic lighting: strong contrast, rim light, spotlight, or atmospheric glow
- Add SMALL DETAILS that reward attention: a subtle flag, a half-hidden object, a glowing trail, dramatic exhaust/atmosphere — things that make the viewer look closer
- Sense of SCALE and TENSION — something is at stake, something is happening RIGHT NOW
- NO neon grid overlays, NO generic stock space photos, NO flat/static compositions

{critique_block}{round_escalation}

Generate {N_VARIANTS} DIFFERENT prompt concepts. Each must have a unique composition/angle.
Every prompt must end with: "{IMAGE_STYLE_SUFFIX}"

Return ONLY valid JSON:
{{
  "prompts": [
    {{
      "id": 1,
      "concept": "<one line — what makes this variant unique>",
      "prompt": "<full generation prompt, 120-180 words, extremely detailed>"
    }}
  ]
}}
"""
    data    = _parse_json(_flash(prompt, max_tokens=3000))
    prompts = data.get("prompts", [])
    for p in prompts:
        print(f"  [{p['id']}] {p['concept']}", flush=True)
    return prompts


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


def generate_images(prompts: list[dict], out_dir: Path, round_num: int) -> list[dict]:
    print(f"\n[3/3] Generating {len(prompts)} images in parallel...", flush=True)
    results = asyncio.run(_generate_all(prompts, out_dir, round_num))
    ok = sum(1 for r in results if r["path"])
    print(f"  Done: {ok}/{len(prompts)}", flush=True)
    return results


# ── Stage 4: Flash Evaluation ─────────────────────────────────────────────────

def _build_eval_prompt(thumbnail_text: str, text_line1: str, text_line2: str, script_topic: str) -> str:
    expected = f'"{text_line2}"' if not text_line1 else f'"{text_line1}" (small) + "{text_line2}" (large bold)'
    return f"""\
You are a YouTube thumbnail analyst specializing in the German space/cosmos niche.
The target audience is Germans aged 60-70+. They watch ZDF documentaries, read Stern/Spiegel.
They will NOT click on thumbnails with tiny text or sci-fi movie poster aesthetics.
They click when they can INSTANTLY READ the text and the image clearly shows what the video is about.

Evaluate this thumbnail across ALL dimensions — visual, textual, semantic, and SEO.

EXPECTED TEXT ON THIS THUMBNAIL: {expected}
(Original user text: "{thumbnail_text}")

VIDEO TOPIC: {script_topic}

Score 1-10 on EACH criterion. Be BRUTAL — most thumbnails deserve 5-6.

TEXT QUALITY:
1. text_correctness: Is the exact text "{text_line1}" and "{text_line2}" actually visible and correctly spelled?
   Score 1 if text is missing, distorted, misspelled, or merged into the background.
2. text_readability: Can a 65-year-old read BOTH lines instantly on a phone screen (120px thumbnail)?
   BOTH lines must be large and bold. If line1 is tiny/caption-sized = score 1-3. Both lines giant and clear = 8-10.
3. text_font_style: Is the font ULTRA BOLD HEAVY CONDENSED, pure white, with strong dark shadow?
   Thin fonts, decorative/distressed/digital/sci-fi fonts = 1-3. Clean heavy condensed white = 8-10.
4. text_placement: Is text in a clean area with strong contrast backing?
   Does the hierarchy look intentional? Is there a visible dark background/shadow behind the text?

VISUAL QUALITY:
5. composition: Does ONE dominant photorealistic object fill 60-80% of the frame?
   Clear visual hierarchy, unambiguous subject, NOT busy or cluttered.
6. graphic_elements: Any graphic elements (arrows, glows, overlays)?
   Clean and intentional = 7-10. Neon grids, dragon motifs, laser beams = 1-3 (looks cheap).
   No graphic elements = 7 (neutral).
7. visual_impact: Does this image make a 65+ German viewer curious/shocked/amazed?
   Photorealistic and emotionally resonant = 8-10. Fantasy sci-fi = 3-5 for this audience.
8. color_saturation: Vivid and high contrast? Natural vivid colors = 8-10. Dark neon chaos = 3-5.

SEMANTIC + SEO:
9. topic_match: Does the image DIRECTLY REPRESENT the video topic ("{script_topic}")?
   The image should tell the story without reading the text.
10. niche_fit: Does this fit how top German cosmos channels (Terra X, Lesch & Co, ZDF) look?
    Clean documentary authority style = 8-10. Generic sci-fi poster = 2-4.
11. ctr_potential: Would a real German 65+ viewer scrolling YouTube click this?
    Consider: is the text readable, is the image intriguing, does it promise a clear topic?
12. overall: Strict. Only 8+ if this thumbnail is INSTANTLY READABLE and CLEARLY MATCHES the topic.

Return ONLY valid JSON:
{{
  "scores": {{
    "text_correctness": <1-10>,
    "text_readability": <1-10>,
    "text_font_style": <1-10>,
    "text_placement": <1-10>,
    "composition": <1-10>,
    "graphic_elements": <1-10>,
    "visual_impact": <1-10>,
    "color_saturation": <1-10>,
    "topic_match": <1-10>,
    "niche_fit": <1-10>,
    "ctr_potential": <1-10>,
    "overall": <1-10>
  }},
  "passed": <true if overall >= 8 else false>,
  "text_issues": "<specific text problems: missing/misspelled/wrong font/bad placement, or null>",
  "visual_issues": "<specific visual problems, or null>",
  "seo_issues": "<why this won't rank/get clicked in the space niche, or null>",
  "what_works": "<what is genuinely strong about this thumbnail>"
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
                f"font={scores.get('text_font_style','?')} "
                f"place={scores.get('text_placement','?')}",
                flush=True,
            )
            print(
                f"    visual: comp={scores.get('composition','?')} "
                f"elements={scores.get('graphic_elements','?')} "
                f"impact={scores.get('visual_impact','?')} "
                f"sat={scores.get('color_saturation','?')}",
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
    lines.append(f"Main object:      {tz.get('main_object','')}")
    lines.append(f"Color palette:    {tz.get('color_palette','')}")
    lines.append(f"Graphic elements: {tz.get('graphic_elements','')}")
    lines.append(f"Atmosphere:       {tz.get('atmosphere','')}")
    lines.append(f"Why people click: {tz.get('why_people_click','')}")
    lines.append("")

    lines.append("── WINNER SCORES ────────────────────────────────────────")
    lines.append(f"OVERALL:          {best_overall}/10")
    lines.append("")
    lines.append("  TEXT")
    lines.append(f"    text_correctness:  {scores.get('text_correctness','?')}/10  — text visible & correctly spelled")
    lines.append(f"    text_readability:  {scores.get('text_readability','?')}/10  — readable at 120px mobile size")
    lines.append(f"    text_font_style:   {scores.get('text_font_style','?')}/10  — ultra bold condensed YouTube style")
    lines.append(f"    text_placement:    {scores.get('text_placement','?')}/10  — balanced, dark area, clear hierarchy")
    lines.append("")
    lines.append("  VISUAL")
    lines.append(f"    composition:       {scores.get('composition','?')}/10  — dominant object, clear visual hierarchy")
    lines.append(f"    graphic_elements:  {scores.get('graphic_elements','?')}/10  — arrows/stripes/glows look intentional")
    lines.append(f"    visual_impact:     {scores.get('visual_impact','?')}/10  — stops the scroll")
    lines.append(f"    color_saturation:  {scores.get('color_saturation','?')}/10  — vivid, high-contrast, deep blacks")
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
            f"    text: {s.get('text_correctness','?')}/{s.get('text_readability','?')}/{s.get('text_font_style','?')}/{s.get('text_placement','?')}  "
            f"visual: {s.get('composition','?')}/{s.get('visual_impact','?')}/{s.get('color_saturation','?')}  "
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

    # Stage 1 — TZ (once, not per round)
    tz = write_tz(segments, thumbnail_text)
    script_topic = tz.get("atmosphere", thumbnail_text)

    best_overall  = 0
    best_image    = None
    all_results   = []
    all_critiques: list[str] = []

    for round_num in range(1, MAX_ROUNDS + 1):
        print(f"\n{'─'*55}", flush=True)
        print(f"  ROUND {round_num}/{MAX_ROUNDS}", flush=True)
        print(f"{'─'*55}", flush=True)

        # Stage 2 — промпты (с критикой из предыдущих раундов)
        prompts = generate_prompts(
            tz,
            round_num=round_num,
            prev_critiques=all_critiques if round_num > 1 else None,
        )

        # Stage 3 — генерация
        images = generate_images(prompts, variants_dir, round_num)

        # Stage 4 — оценка
        evaluated = evaluate_images(images, round_num, thumbnail_text, tz, script_topic)
        all_results.extend(evaluated)

        # Лучший в этом раунде
        round_best = max(evaluated, key=lambda x: x["overall"]) if evaluated else None
        if round_best and round_best["overall"] > best_overall:
            best_overall = round_best["overall"]
            best_image   = round_best

        passed = [r for r in evaluated if r["passed"]]
        if passed:
            winner = max(passed, key=lambda x: x["overall"])
            print(f"\n✅ Round {round_num}: winner found! overall={winner['overall']}", flush=True)
            best_image = winner
            break
        else:
            print(
                f"\n⚠ Round {round_num}: no pass "
                f"(best={round_best['overall'] if round_best else 0}/{PASS_THRESHOLD})",
                flush=True,
            )
            # Собираем критику для следующего раунда
            new_critiques = _collect_critiques(evaluated)
            all_critiques = new_critiques  # только из последнего раунда — свежие проблемы
            if round_num < MAX_ROUNDS:
                print(f"  Critiques for next round: {all_critiques}", flush=True)
                print(f"  Refining prompts...", flush=True)

    # Финальный winner
    if not best_image or not best_image.get("path"):
        raise RuntimeError("No thumbnail generated successfully")

    final_path = out_dir / "thumbnail_final.png"
    import shutil
    shutil.copy2(best_image["path"], final_path)

    print(f"\n{'='*55}", flush=True)
    print(f"🏆 WINNER: overall={best_overall}/{PASS_THRESHOLD}", flush=True)
    print(f"   {best_image.get('what_works','')}", flush=True)
    print(f"   → {final_path}", flush=True)

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
    parser.add_argument("--channel", default="channel_001_cosmos_de")
    parser.add_argument("--session", default=None)
    parser.add_argument("--text",    required=True, help='Thumbnail text, e.g. "DIE SONNE IST WACH"')
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

    t0 = time.time()
    final = run_pipeline(channel_id, session, args.text, out_dir)
    print(f"\n⏱ Total: {round(time.time()-t0, 1)}s", flush=True)
    print(f"{'='*60}\n", flush=True)


if __name__ == "__main__":
    main()
