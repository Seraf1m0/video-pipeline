"""
molmo_analyzer.py — анализ видео-кадров через Molmo 2 API (OpenRouter).

Модель: allenai/molmo-2-8b:free (бесплатно через openrouter.ai)
Без локальной загрузки модели, без GPU — просто HTTP-запрос.

6 критериев оценки каждого кадра:
  1. ARTIFACTS / CLEAN          — артефакты нейросети
  2. SCIENTIFIC / NOT_SCIENTIFIC — научность/достоверность
  3. MATCHES / DOESNT_MATCH     — соответствие промпту
  4. GOOD_QUALITY / BAD_QUALITY  — техническое качество
  5. EXACT_MATCH / WRONG_OBJECT  — точность объектов
  6. MEANINGFUL / NOT_MEANINGFUL — смысловое соответствие

Голосование: 2 из 3 кадров (25% / 50% / 75%).
"""

import base64
import json
import os
import subprocess
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

# ── Загрузка .env ─────────────────────────────────────────────────────────────
_BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(_BASE_DIR / "config" / ".env")

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
OPENROUTER_MODEL   = os.environ.get("OPENROUTER_MODEL", "allenai/molmo-2-8b:free").strip()
OPENROUTER_URL     = "https://openrouter.ai/api/v1/chat/completions"

# ── Temp папка ────────────────────────────────────────────────────────────────
_TEMP = _BASE_DIR / "temp"
_TEMP.mkdir(parents=True, exist_ok=True)


# ── Утилиты ───────────────────────────────────────────────────────────────────

def extract_frame(video_path: Path, timestamp_pct: float = 0.5) -> str | None:
    """Извлечь один кадр из видео через FFmpeg."""
    cmd = ["ffprobe", "-v", "quiet", "-print_format", "json",
           "-show_format", str(video_path)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    try:
        duration = float(json.loads(result.stdout)["format"]["duration"])
    except Exception:
        return None

    timestamp  = max(0.1, duration * timestamp_pct)
    frame_path = str(_TEMP / f"frame_{video_path.stem}_{int(timestamp_pct*100)}.jpg")

    r = subprocess.run([
        "ffmpeg", "-y", "-ss", str(timestamp),
        "-i", str(video_path), "-vframes", "1",
        "-q:v", "2", "-loglevel", "quiet", frame_path,
    ], capture_output=True)

    return frame_path if Path(frame_path).exists() else None


def extract_frames(video_path: Path, pcts: list[float]) -> list[str]:
    """Извлечь несколько кадров из видео."""
    return [fp for pct in pcts if (fp := extract_frame(video_path, pct))]


def _image_to_b64(frame_path: str) -> str:
    """Конвертировать JPEG-кадр в base64 data URL."""
    with open(frame_path, "rb") as f:
        data = base64.b64encode(f.read()).decode("utf-8")
    return f"data:image/jpeg;base64,{data}"


def _ask_api(frame_path: str, question: str, retries: int = 3) -> str:
    """
    Задать вопрос модели Molmo 2 через OpenRouter API.
    Возвращает ответ (strip, upper).
    """
    if not OPENROUTER_API_KEY:
        raise RuntimeError(
            "OPENROUTER_API_KEY не задан в config/.env!\n"
            "Зарегистрируйся на https://openrouter.ai и добавь ключ."
        )

    image_url = _image_to_b64(frame_path)

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type":  "application/json",
        "HTTP-Referer":  "https://github.com/video-pipeline",
        "X-Title":       "Video Pipeline Validator",
    }
    payload = {
        "model": OPENROUTER_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image_url}},
                    {"type": "text",      "text": question},
                ],
            }
        ],
        "max_tokens": 10,
        "temperature": 0.0,
    }

    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(
                OPENROUTER_URL, headers=headers,
                json=payload, timeout=30,
            )
            if resp.status_code == 200:
                answer = resp.json()["choices"][0]["message"]["content"]
                return answer.strip().upper()
            elif resp.status_code == 429:
                # Rate limit — ждём
                wait = 5 * attempt
                time.sleep(wait)
            else:
                # Другая ошибка — логируем и повторяем
                if attempt == retries:
                    return "UNKNOWN"
                time.sleep(2)
        except requests.RequestException:
            if attempt == retries:
                return "UNKNOWN"
            time.sleep(2)

    return "UNKNOWN"


# ── Главная функция ───────────────────────────────────────────────────────────

def analyze_video(
    video_path:      Path,
    original_prompt: str,
    segment_text:    str,
    niche:           str = "science",
) -> dict:
    """
    Анализ видео через Molmo 2 API по 4 критериям.
    Голосование 2 из 3 кадров (25% / 50% / 75%).

    Возвращает:
      valid, has_artifacts, is_scientific, matches_prompt,
      good_quality, exact_object, meaningful, reason, score (0-6)
    """
    frames = extract_frames(video_path, [0.25, 0.5, 0.75])

    if not frames:
        return {
            "valid":          False,
            "has_artifacts":  False,
            "is_scientific":  False,
            "matches_prompt": False,
            "good_quality":   False,
            "reason":         "не удалось извлечь кадры",
            "score":          0,
        }

    frame_results = []
    for frame_path in frames:

        # ── КРИТЕРИЙ 1 — Артефакты нейросети ──────────────────────────────────
        q1 = (
            "Analyze this image for AI generation artifacts. "
            "Look for: distorted faces, impossible geometry, garbled text, "
            "morphing body parts, floating objects, unnatural lighting, "
            "duplicate limbs, blurry transitions, color glitches. "
            "Answer ONLY one word: ARTIFACTS or CLEAN"
        )

        # ── КРИТЕРИЙ 2 — Научность ─────────────────────────────────────────────
        q2 = (
            f'This image should show: "{segment_text[:150]}" '
            "Is the content scientifically accurate and realistic? "
            "No cartoons, no fantasy, no abstract art. "
            "Answer ONLY one word: SCIENTIFIC or NOT_SCIENTIFIC"
        )

        # ── КРИТЕРИЙ 3 — Соответствие промпту ────────────────────────────────
        q3 = (
            f'Does this image visually match this description: "{original_prompt[:200]}" '
            "Answer ONLY one word: MATCHES or DOESNT_MATCH"
        )

        # ── КРИТЕРИЙ 4 — Техническое качество ────────────────────────────────
        q4 = (
            "Rate the technical quality of this image: "
            "is it sharp, well-lit, properly composed? "
            "No extreme blur, no black frames, no corrupted pixels. "
            "Answer ONLY one word: GOOD_QUALITY or BAD_QUALITY"
        )

        # ── КРИТЕРИЙ 5 — Точность объектов ───────────────────────────────────
        q5 = (
            f'This video should show: "{segment_text}"\n'
            "Are the specific objects/subjects EXACTLY correct?\n"
            "Examples:\n"
            "- If segment says \"James Webb Telescope\" — "
            "does video show James Webb specifically "
            "(not Hubble, not random telescope)?\n"
            "- If segment says \"Saturn rings\" — "
            "does video show Saturn specifically "
            "(not Jupiter, not random planet)?\n"
            "- If segment says \"Mars rover Perseverance\" — "
            "does video show Perseverance specifically?\n"
            "Be strict — similar but wrong object = WRONG.\n"
            "Answer ONLY: EXACT_MATCH or WRONG_OBJECT"
        )

        # ── КРИТЕРИЙ 6 — Смысловое соответствие ──────────────────────────────
        q6 = (
            f'Segment text: "{segment_text}"\n'
            "Does this video accurately represent "
            "the MEANING and CONTEXT of the segment?\n"
            "- The visual story matches what is being described\n"
            "- No contradictions between video and text\n"
            "- The mood/tone matches (scientific, serious, epic)\n"
            "Answer ONLY: MEANINGFUL or NOT_MEANINGFUL"
        )

        answers = {}
        for key, question in [
            ("artifacts",     q1),
            ("scientific",    q2),
            ("matches",       q3),
            ("quality",       q4),
            ("exact_object",  q5),
            ("meaningful",    q6),
        ]:
            answers[key] = _ask_api(frame_path, question)

        frame_results.append(answers)

    # ── Голосование: 2 из 3 кадров решают ─────────────────────────────────────
    def majority(key: str, positive_value: str) -> bool:
        return sum(1 for r in frame_results if positive_value in r.get(key, "")) >= 2

    has_artifacts  = majority("artifacts",    "ARTIFACTS")
    is_scientific  = majority("scientific",   "SCIENTIFIC")
    matches_prompt = majority("matches",      "MATCHES")
    good_quality   = majority("quality",      "GOOD_QUALITY")
    exact_object   = majority("exact_object", "EXACT_MATCH")
    meaningful     = majority("meaningful",   "MEANINGFUL")

    valid = (
        not has_artifacts and
        is_scientific and
        matches_prompt and
        good_quality and
        exact_object and
        meaningful
    )

    reasons = []
    if has_artifacts:      reasons.append("артефакты нейросети")
    if not is_scientific:  reasons.append("ненаучный контент")
    if not matches_prompt: reasons.append("не соответствует теме")
    if not good_quality:   reasons.append("плохое качество")
    if not exact_object:   reasons.append("неправильный объект")
    if not meaningful:     reasons.append("не соответствует смыслу")

    score = sum([not has_artifacts, is_scientific, matches_prompt, good_quality,
                 exact_object, meaningful])

    # Удаляем временные кадры
    for fp in frames:
        try:
            Path(fp).unlink(missing_ok=True)
        except Exception:
            pass

    return {
        "valid":          valid,
        "has_artifacts":  has_artifacts,
        "is_scientific":  is_scientific,
        "matches_prompt": matches_prompt,
        "good_quality":   good_quality,
        "exact_object":   exact_object,
        "meaningful":     meaningful,
        "reason":         ", ".join(reasons) if reasons else "ok",
        "score":          score,
    }
