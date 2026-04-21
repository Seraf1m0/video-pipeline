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

FLASH_MODEL    = "gemini-2.5-flash-lite"
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

The user wants EXACTLY this text on the thumbnail — nothing more, nothing less: "{thumbnail_text}"

STRICT RULE: You may only use these exact words. You CANNOT add, invent, or paraphrase any other text.

Your job:
1. Analyze the script to understand the video topic and emotional hook
2. Decide HOW to display this exact text for maximum CTR impact:
   - Split into lines if it improves readability (e.g. "DIE SONNE IST WACH" → "DIE SONNE IST" + "WACH")
   - OR keep as one dominant line (text_line1="" in that case, text_line2=full text)
   - Main text must be MASSIVE — the kind a 65-year-old can read on a phone without glasses
   - Decide text hierarchy, size ratio, and placement
3. Choose a PHOTOREALISTIC main object that directly represents the video topic
   - Use the EXACT real object — if it's Hubble, describe Hubble's actual shape precisely
   - Describe its real visual appearance (shape, color, size, markings) so the image generator renders it accurately
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
  "text_line1": "<first part of the user's text split across lines, OR empty string if single line. ONLY words from the original text — NO invented text>",
  "text_line2": "<second part or full text if single line — ONLY words from the original text>",
  "text_hierarchy": "<BOTH lines must be large. Max ratio 1.5x. E.g. 'line2 is 1.3x larger than line1 — both are big bold headlines'>",
  "text_placement": "<upper-left | lower-left | upper-right — where text block sits>",
  "main_object": "<ONE dominant object — use EXACT real name + precise visual description (shape, color, size, markings). E.g. NOT 'space telescope' but 'Hubble Space Telescope — silver cylindrical body, gold thermal blanket, two rectangular blue solar panels, large aperture end facing viewer'. Dramatic moment, not static>",
  "emotional_tone": "<shock | revelation | urgency | awe | confrontation — strong and specific>",
  "color_palette": "<vivid, high contrast, e.g. 'deep space black + dramatic orange glow + sharp white highlights'>",
  "text_style": "<ultra-bold, clean white sans-serif — NO decorative fonts. Strong dark shadow or semi-transparent backing>",
  "graphic_elements": "<Choose 1-2 from these YouTube-proven techniques (or none if not fitting): (A) PNG cutout — a person, astronaut, or key object with NO background, placed in foreground at large scale for depth; (B) Red/yellow circle with thick border highlighting a specific detail in the image; (C) Bold arrow (thick, white or red) pointing at something surprising or key; (D) Split composition — image divided into two contrasting halves; (E) Zoom inset — a circular magnified detail somewhere in the corner. Describe EXACTLY which technique and where>",
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
    # If TZ put everything in line1 with empty line2, swap — line2 is always the hero
    if line2 == "" and line1 != "":
        line2, line1 = line1, line2

    placement = tz.get("text_placement", "upper-left")
    side = placement.split("-")[1] if "-" in placement else "left"

    # Text suffix injected programmatically into every prompt — never lost
    if line1:
        text_suffix = (
            f'Text overlay at {placement}: "{line1}" in bold condensed white (~15% frame height), '
            f'then below it "{line2}" in ultra-bold condensed white (~28% frame height). '
            f'Both flush to {side} edge. Impact-style font, heavy black drop shadow, '
            f'dark semi-transparent bar behind text. No other text or logos.'
        )
    else:
        text_suffix = (
            f'Text overlay at {placement}: "{line2}" — ultra-bold condensed white Impact-style font, '
            f'letters ~30% frame height, heavy black drop shadow, '
            f'dark semi-transparent bar behind text. No other text or logos.'
        )

    object_name = tz.get("main_object", "").split("—")[0].strip()
    object_detail = tz.get("main_object", "").split("—")[1].strip() if "—" in tz.get("main_object", "") else ""

    prompt = f"""\
You are a prompt engineer creating YouTube thumbnails for a German space documentary channel.

Generate {N_VARIANTS} DIFFERENT visual concepts for this thumbnail.
Each concept = unique angle, composition, or moment. VISUAL ONLY — no text instructions.

SUBJECT: {object_name}{f" — {object_detail[:150]}" if object_detail else ""}
MOOD: {tz.get("atmosphere", "")}
COLORS: {tz.get("color_palette", "")}

RULES:
- Photorealistic, cinematic documentary style — NOT sci-fi fantasy
- ONE dominant subject, dramatically lit
- Strong sense of scale and drama — something is happening NOW
- Dark background that makes the subject pop

{critique_block}{round_escalation}

Return ONLY valid JSON:
{{
  "prompts": [
    {{
      "id": 1,
      "concept": "<one line — unique angle>",
      "prompt": "<60-80 words, visual description only, no text instructions>"
    }}
  ]
}}
"""
    data    = _parse_json(_flash(prompt, max_tokens=2000))
    prompts = data.get("prompts", [])
    # Inject text + style suffix into every prompt
    for p in prompts:
        p["prompt"] = f"{p['prompt'].rstrip('. ')}. {text_suffix} {IMAGE_STYLE_SUFFIX}"
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

        # ── Edit Pass: пробуем отредактировать лучших кандидатов ─────────────────
        edited = edit_pass(evaluated, tz, script_topic, round_num, variants_dir)
        if edited:
            print(f"  [Edit Pass] re-evaluating {len(edited)} edited image(s)...", flush=True)
            edited_eval = evaluate_images(edited, round_num, thumbnail_text, tz, script_topic)
            all_results.extend(edited_eval)
            evaluated = evaluated + edited_eval
            round_best_edit = max(edited_eval, key=lambda x: x["overall"]) if edited_eval else None
            if round_best_edit and round_best_edit["overall"] > best_overall:
                best_overall = round_best_edit["overall"]
                best_image   = round_best_edit

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
