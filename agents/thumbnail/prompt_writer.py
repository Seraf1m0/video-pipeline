"""
prompt_writer.py — writes image generation prompts for thumbnail, no image generation.

Flow:
  1. User puts thumbnail title in thumbnail_text.txt in the session folder
  2. Script reads it, analyzes transcript, writes creative brief
  3. Generates N image prompts via Gemini Flash
  4. Saves all prompts to thumbnail_prompts.txt

Usage:
    py agents/thumbnail/prompt_writer.py --channel channel_001_cosmos_de
    py agents/thumbnail/prompt_writer.py --channel channel_001_cosmos_de --session Video_20260421_170118
    py agents/thumbnail/prompt_writer.py --channel channel_003_religion_es --session Video_...
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agents" / "utils"))

load_dotenv(ROOT / "config" / ".env")

from paths import get_session_dir, get_last_session  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── Config ────────────────────────────────────────────────────────────────────

FLASH_MODEL  = "gemini-2.5-flash-lite"
_NO_THINKING = {"thinking_config": {"thinking_budget": 0}}
N_VARIANTS   = 5  # prompts per run

IMAGE_STYLE_SUFFIX = (
    "photorealistic, ultra detailed, 8K, dramatic documentary lighting, "
    "vivid natural colors, professional photography style, "
    "NO sci-fi fantasy elements unless topic requires it, "
    "16:9 aspect ratio"
)

# ── Gemini ────────────────────────────────────────────────────────────────────

from google import genai as _genai_client

_gemini = None

def _get_gemini():
    global _gemini
    if _gemini is None:
        _gemini = _genai_client.Client(api_key=os.environ["GEMINI_API_KEY"])
    return _gemini


def _flash(prompt: str, max_tokens: int = 2000) -> str:
    for attempt in range(6):
        try:
            resp = _get_gemini().models.generate_content(
                model=FLASH_MODEL,
                contents=prompt,
                config={"temperature": 0.7, "max_output_tokens": max_tokens, **_NO_THINKING},
            )
            return resp.text
        except Exception as e:
            if "overloaded" in str(e).lower() or "503" in str(e):
                wait = 15 * (attempt + 1)
                print(f"  [Flash] overloaded, retry #{attempt+1} in {wait}s...", flush=True)
                time.sleep(wait)
            else:
                raise
    raise RuntimeError("Gemini Flash unavailable after retries")


def _parse_json(raw: str) -> dict | list:
    import re
    raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw, flags=re.DOTALL).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        for end in range(len(raw), 0, -1):
            try:
                return json.loads(raw[:end])
            except json.JSONDecodeError:
                continue
        raise


# ── Load transcript ────────────────────────────────────────────────────────────

def load_segments(session_dir: Path) -> list[dict]:
    for name in ["transcript.json", "segments.json", "result.json"]:
        f = session_dir / "transcripts" / name
        if f.exists():
            data = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                for key in ("segments", "results", "transcription"):
                    if key in data and isinstance(data[key], list):
                        return data[key]
    return []


# ── Creative brief ─────────────────────────────────────────────────────────────

def write_tz(segments: list[dict], thumbnail_text: str) -> dict:
    print("\n[1/2] Writing creative brief...", flush=True)
    script = " ".join(
        s.get("text", "") for s in segments if "[" not in s.get("text", "")
    )[:4000]

    prompt = f"""\
You are a YouTube thumbnail art director for a documentary channel.

The thumbnail text is: "{thumbnail_text}"

STRICT RULE: You may ONLY use these exact words. Do NOT add, invent, or paraphrase any other text.
You may split into two lines for better readability, or keep as one line.

Analyze the script and return a creative brief as valid JSON:
{{
  "text_line1": "<first part of split, or empty string if single line>",
  "text_line2": "<second part or full text if single line>",
  "text_placement": "<upper-left | lower-left | upper-right>",
  "main_object": "<exact real name of the key visual object — e.g. 'James Webb Space Telescope'>",
  "object_key_details": "<2-3 key visual identifiers of this exact object — shape, color, distinctive features>",
  "atmosphere": "<1 sentence: mood and dramatic moment>",
  "color_palette": "<2-3 specific colors, e.g. 'deep black, gold, electric blue'>"
}}

SCRIPT:
{script}
"""
    result = _parse_json(_flash(prompt, max_tokens=500))
    print(f"  Text:    '{result.get('text_line1','')}' / '{result.get('text_line2','')}'", flush=True)
    print(f"  Object:  {result.get('main_object','')} — {result.get('object_key_details','')}", flush=True)
    print(f"  Mood:    {result.get('atmosphere','')}", flush=True)
    return result


# ── Prompt generation ──────────────────────────────────────────────────────────

def generate_prompts(tz: dict, thumbnail_text: str) -> list[str]:
    print(f"\n[2/2] Generating {N_VARIANTS} prompts...", flush=True)

    line1 = tz.get("text_line1", "")
    line2 = tz.get("text_line2", "")
    if line2 == "" and line1 != "":
        line2, line1 = line1, line2

    placement = tz.get("text_placement", "upper-left")
    side = placement.split("-")[1] if "-" in placement else "left"

    if line1:
        text_suffix = (
            f'Text overlay at {placement}: "{line1}" bold condensed white (~15% frame height) '
            f'then below it "{line2}" ultra-bold condensed white (~28% frame height), '
            f'both flush {side}, Impact-style font, heavy black drop shadow, '
            f'dark semi-transparent bar behind text. No other text.'
        )
    else:
        text_suffix = (
            f'Text overlay at {placement}: "{line2}" — ultra-bold condensed white Impact-style, '
            f'letters ~30% frame height, heavy black drop shadow, '
            f'dark semi-transparent bar behind. No other text.'
        )

    obj       = tz.get("main_object", "")
    obj_det   = tz.get("object_key_details", "")
    atmosphere = tz.get("atmosphere", "")
    colors    = tz.get("color_palette", "")

    prompt = f"""\
You are a prompt engineer for AI image generation (YouTube thumbnails, documentary style).

SUBJECT: {obj} — {obj_det}
MOOD: {atmosphere}
COLORS: {colors}

Generate {N_VARIANTS} DIFFERENT visual concepts. Each = unique angle, composition, or moment.
Visual description only — no text instructions (text will be added separately).

Rules:
- Photorealistic, cinematic, NOT sci-fi fantasy
- ONE dominant subject, dramatically lit
- Strong sense of scale and drama
- Dark background that makes subject pop

Return ONLY valid JSON:
{{
  "prompts": [
    {{
      "id": 1,
      "concept": "<unique angle in one line>",
      "visual": "<50-70 words, visual description only>"
    }}
  ]
}}
"""
    data = _parse_json(_flash(prompt, max_tokens=2000))
    raw_prompts = data.get("prompts", [])

    final = []
    for p in raw_prompts:
        full = f"{p['visual'].rstrip('. ')}. {text_suffix} {IMAGE_STYLE_SUFFIX}"
        final.append({"id": p["id"], "concept": p["concept"], "prompt": full})
        print(f"  [{p['id']}] {p['concept']}", flush=True)
    return final


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", required=True)
    parser.add_argument("--session", default=None)
    parser.add_argument("--text", default=None, help="Thumbnail text (skip file polling)")
    parser.add_argument("--watch", action="store_true",
                        help="Watch thumbnail_text.txt and re-run when it changes")
    args = parser.parse_args()

    session_dir = (
        Path(get_session_dir(args.channel, args.session))
        if args.session
        else Path(get_last_session(args.channel))
    )
    text_file   = session_dir / "thumbnail" / "thumbnail_text.txt"
    output_file = session_dir / "thumbnail" / "thumbnail_prompts.txt"
    output_file.parent.mkdir(parents=True, exist_ok=True)

    def run_once():
        if not text_file.exists():
            print(f"Waiting for {text_file} ...", flush=True)
            return False

        thumbnail_text = text_file.read_text(encoding="utf-8").strip()
        if not thumbnail_text:
            print("thumbnail_text.txt is empty, skipping.", flush=True)
            return False

        print(f"\n{'='*60}", flush=True)
        print(f"  PROMPT WRITER", flush=True)
        print(f"  Channel: {args.channel}", flush=True)
        print(f"  Session: {session_dir.name}", flush=True)
        print(f"  Text:    '{thumbnail_text}'", flush=True)
        print(f"{'='*60}", flush=True)

        segments = load_segments(session_dir)
        print(f"Loaded {len(segments)} segments", flush=True)

        tz = write_tz(segments, thumbnail_text)
        prompts = generate_prompts(tz, thumbnail_text)

        # Save to txt
        lines = [
            f"Channel: {args.channel}",
            f"Session: {session_dir.name}",
            f"Text: {thumbnail_text}",
            f"Object: {tz.get('main_object','')} — {tz.get('object_key_details','')}",
            f"Mood: {tz.get('atmosphere','')}",
            "",
        ]
        for p in prompts:
            lines.append(f"--- Prompt {p['id']}: {p['concept']} ---")
            lines.append(p["prompt"])
            lines.append("")

        output_file.write_text("\n".join(lines), encoding="utf-8")
        print(f"\nSaved {len(prompts)} prompts → {output_file}", flush=True)
        print("=" * 60, flush=True)
        return True

    # If --text passed directly, write it to file and run once
    if args.text:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        text_file.write_text(args.text, encoding="utf-8")
        run_once()
    elif args.watch:
        last_mtime = 0
        print(f"Watching {text_file} ...", flush=True)
        while True:
            try:
                mtime = text_file.stat().st_mtime if text_file.exists() else 0
                if mtime != last_mtime:
                    last_mtime = mtime
                    run_once()
            except Exception as e:
                print(f"Error: {e}", flush=True)
            time.sleep(10)
    else:
        run_once()


if __name__ == "__main__":
    main()
