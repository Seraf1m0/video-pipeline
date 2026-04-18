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
    for attempt in range(5):
        try:
            resp = _get_gemini().models.generate_content(
                model=FLASH_MODEL,
                contents=parts,
                config={"temperature": 0.4, "max_output_tokens": max_tokens, **_NO_THINKING},
            )
            return resp.text.strip()
        except Exception as e:
            if "503" in str(e) or "UNAVAILABLE" in str(e):
                wait = 15 * (attempt + 1)
                print(f"  [Flash] 503 overloaded, retry {attempt+1}/5 in {wait}s...", flush=True)
                time.sleep(wait)
            else:
                raise
    raise RuntimeError("Flash API unavailable after 5 retries")


def _parse_json(raw: str) -> dict | list:
    raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw, flags=re.DOTALL).strip()
    return json.loads(raw)


# ── Stage 1: Creative TZ ──────────────────────────────────────────────────────

def write_tz(segments: list[dict], thumbnail_text: str) -> dict:
    """Flash пишет визуальное ТЗ — сам решает иерархию и расположение текста."""
    print("\n[1/3] Writing creative brief (TZ)...", flush=True)
    script = " ".join(
        s.get("text", "") for s in segments if "[" not in s.get("text", "")
    )[:4000]

    prompt = f"""\
You are a YouTube thumbnail art director for a German space documentary channel.

The user wants this text on the thumbnail: "{thumbnail_text}"

Your job:
1. Analyze the script to understand the video topic and emotional hook
2. Decide HOW to use this text for maximum CTR impact:
   - Split it into lines if needed (e.g. "DIE SONNE IST WACH" → "DIE SONNE IST" + "WACH")
   - OR keep as one dominant line
   - Decide which part is the MAIN bold word and which is supporting context
   - Decide the text hierarchy, size ratio, and placement
3. Create a complete visual brief for the image generator

Return ONLY valid JSON:
{{
  "text_line1": "<smaller supporting text — context/setup, or empty string if single line>",
  "text_line2": "<MAIN bold word(s) — the emotional hook, LARGEST on screen>",
  "text_hierarchy": "<describe: which is bigger, size ratio, e.g. 'line2 is 3x larger than line1'>",
  "text_placement": "<upper-left | lower-left | upper-right — where text block sits>",
  "main_object": "<the ONE dominant visual object that fills 60-80% of frame>",
  "emotional_tone": "<fear | awe | shock | urgency | mystery | revelation>",
  "color_palette": "<vivid palette, e.g. 'electric red + deep black'>",
  "text_style": "<font weight, glow, shadow, color — be specific>",
  "graphic_elements": "<arrows, stripes, glows, overlays — be SPECIFIC and CREATIVE>",
  "atmosphere": "<1 sentence: overall mood>",
  "why_people_click": "<psychological hook — why someone stops scrolling>"
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
    "ultra vivid saturated colors, cinematic sci-fi movie poster style, "
    "deep black space background, ultra detailed, 8K, dramatic lighting, "
    "professional digital art, 16:9 aspect ratio"
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

You MUST fix ALL of these problems. Be MORE dramatic, MORE saturated, MORE impactful.
Each prompt must be COMPLETELY DIFFERENT from previous attempts.
"""

    round_escalation = ""
    if round_num == 2:
        round_escalation = "ESCALATE: Make it 2x more dramatic. Extreme contrast. Overwhelming visual power."
    elif round_num == 3:
        round_escalation = "MAXIMUM DRAMA. This is your last chance. Like the greatest movie poster ever made. No compromises."
    elif round_num == 4:
        round_escalation = "FINAL ATTEMPT. Completely rethink the concept. Break all rules. Make it IMPOSSIBLE to ignore."

    line1 = tz.get("text_line1", "")
    line2 = tz.get("text_line2", "")
    hierarchy = tz.get("text_hierarchy", "line2 is larger")

    prompt = f"""\
You are a master prompt engineer for AI image generation (YouTube thumbnails for space channel).

CREATIVE BRIEF (TZ):
- Main object: {tz.get('main_object','')}
- Colors: {tz.get('color_palette','')}
- Text placement: {tz.get('text_placement','')}
- Text style: {tz.get('text_style','')}
- Graphic elements: {tz.get('graphic_elements','')}
- Atmosphere: {tz.get('atmosphere','')}

TEXT TO INCLUDE (mandatory, exact):
{f'- Smaller supporting text: "{line1}"' if line1 else '- No small text — single dominant line only'}
- MAIN bold text: "{line2}" — this is the HERO text
- Text hierarchy: {hierarchy}

TEXT RENDERING RULES (critical):
- "{line2}" must be MASSIVE — taking 25-35% of frame width
- Font: ultra-bold heavy condensed, white, no thin strokes
- Strong dark glow/drop shadow behind text for readability
- Text block in: {tz.get('text_placement','')}
- NO other text, logos, watermarks

{critique_block}{round_escalation}

Generate {N_VARIANTS} DIFFERENT prompt concepts. Each must have a unique composition/angle.
Every prompt must end with: "{IMAGE_STYLE_SUFFIX}"

Return ONLY valid JSON:
{{
  "prompts": [
    {{
      "id": 1,
      "concept": "<one line — what makes this variant unique>",
      "prompt": "<full generation prompt, 100-150 words, extremely detailed>"
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
You are a YouTube thumbnail analyst with deep expertise in the space/cosmos niche.
You are integrated into YouTube and understand exactly what drives CTR in this category.
You have analyzed millions of thumbnails across German, Russian, English space channels.

Evaluate this thumbnail across ALL dimensions — visual, textual, semantic, and SEO.

EXPECTED TEXT ON THIS THUMBNAIL: {expected}
(Original user text: "{thumbnail_text}")

VIDEO TOPIC: {script_topic}

Score 1-10 on EACH criterion. Be BRUTAL — most thumbnails deserve 5-6:

TEXT QUALITY:
1. text_correctness: Is the exact text "{text1}" and "{text2}" actually visible and correctly spelled?
   Score 1 if text is missing, distorted, misspelled, or merged into the background.
2. text_readability: Instantly readable on a 120px mobile thumbnail? Bold enough? High contrast?
3. text_font_style: Does the font look ULTRA BOLD HEAVY CONDENSED (YouTube/tabloid style)?
   Thin, outlined, serif, or decorative fonts = 1-3. Heavy condensed white = 8-10.
4. text_placement: Is text positioned in a VISUALLY BALANCED way?
   - Is it in a clean dark area with clear separation from the main visual object?
   - Does the text layout look intentional and designed (not accidental)?
   - Is the hierarchy clear (small text above, large text below)?

VISUAL QUALITY:
5. composition: Does ONE dominant object fill 60-80% of the frame?
   Is there clear visual hierarchy? Does the eye know where to go first?
6. graphic_elements: If arrows, stripes, circles, glows, or other UI elements are present —
   do they look intentional and enhance the image, or do they look clutter/cheap?
   Score N/A (7) if no graphic elements are present.
7. visual_impact: Does this image make you STOP scrolling? Dramatic? Powerful? Cinematic?
8. color_saturation: VIVID and HIGH-CONTRAST? Deep blacks + glowing highlights?
   Muted/gray/dark-without-glow = 1-3.

SEMANTIC + SEO:
9. topic_match: Does the thumbnail VISUALLY REPRESENT the video topic ("{script_topic}")?
   Would someone who clicked this thumbnail feel the video delivered what the thumbnail promised?
10. niche_fit: Does this thumbnail FIT the top-performing German space/cosmos channel style?
    Compare to channels with millions of views in this niche. Generic stock space photo = 3.
    Dramatic, emotional, story-driven visual = 8-10.
11. ctr_potential: Based on your YouTube integration knowledge — how likely is a real German
    viewer scrolling their feed to click THIS thumbnail vs. skip it?
    Consider: thumbnail size on mobile, competition in this niche, emotional trigger strength.
12. overall: Strict weighted overall. Only 8+ if this thumbnail could genuinely compete with
    top-tier German cosmos channels (Kurzgesagt-level production quality for thumbnails).

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
