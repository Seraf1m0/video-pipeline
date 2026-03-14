"""
blip_analyzer.py — анализ видео-кадров через BLIP (локально, без API).

Модель: Salesforce/blip-vqa-base (скачивается один раз, ~1.5GB)
Без интернета после загрузки, без GPU (CPU-режим).

6 критериев оценки каждого кадра:
  1. ARTIFACTS / CLEAN          — артефакты нейросети
  2. SCIENTIFIC / NOT_SCIENTIFIC — научность/достоверность
  3. MATCHES / DOESNT_MATCH     — соответствие промпту
  4. GOOD_QUALITY / BAD_QUALITY  — техническое качество
  5. EXACT_MATCH / WRONG_OBJECT  — точность объектов
  6. MEANINGFUL / NOT_MEANINGFUL — смысловое соответствие

Голосование: 2 из 3 кадров (25% / 50% / 75%).
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import threading
import urllib.request
import urllib.error
from pathlib import Path

from PIL import Image

_BASE_DIR   = Path(__file__).resolve().parent.parent.parent
_TEMP       = _BASE_DIR / "temp"
_TEMP.mkdir(parents=True, exist_ok=True)

_SERVER_PORT    = 5679
_SERVER_URL     = f"http://127.0.0.1:{_SERVER_PORT}"
_SERVER_SCRIPT  = Path(__file__).parent / "blip_server.py"
_server_started = False
_server_lock    = threading.Lock()


def _server_alive() -> bool:
    try:
        with urllib.request.urlopen(f"{_SERVER_URL}/ping", timeout=2) as r:
            return r.read() == b"pong"
    except Exception:
        return False


def _ensure_server():
    global _server_started
    if _server_alive():
        return
    with _server_lock:
        if _server_alive():
            return
        print("  [BLIP] Запускаю сервер (загрузка модели ~1 мин)...", flush=True)
        subprocess.Popen(
            [sys.executable, str(_SERVER_SCRIPT)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
        # Ждём готовности до 120 секунд
        for _ in range(120):
            time.sleep(1)
            if _server_alive():
                print("  [BLIP] Сервер готов ✓", flush=True)
                _server_started = True
                return
        raise RuntimeError("BLIP-сервер не запустился за 120 секунд")


def _ask_blip_via_server(image_path: str, questions: list[str]) -> list[str]:
    """Отправить кадр и вопросы серверу, получить ответы."""
    _ensure_server()
    payload = json.dumps({"image_path": image_path, "questions": questions}).encode()
    req = urllib.request.Request(
        f"{_SERVER_URL}/analyze",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())["answers"]


def _ask_blip(image: Image.Image, question: str) -> str:
    """Задать вопрос BLIP о кадре через сервер."""
    # Сохраняем кадр во временный файл
    tmp = _TEMP / f"blip_srv_{threading.get_ident()}_{id(image)}.jpg"
    image.save(str(tmp), "JPEG", quality=90)
    try:
        answers = _ask_blip_via_server(str(tmp), [question])
        return answers[0].strip().lower()
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


def _ask_blip_batch(image: Image.Image, questions: list[str]) -> list[str]:
    """Задать все вопросы одним запросом к серверу (быстрее)."""
    tmp = _TEMP / f"blip_srv_{threading.get_ident()}_{id(image)}.jpg"
    image.save(str(tmp), "JPEG", quality=90)
    try:
        return _ask_blip_via_server(str(tmp), questions)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


def _blip_yes(image: Image.Image, question: str) -> bool:
    """Вернуть True если BLIP ответил 'yes'."""
    ans = _ask_blip(image, question)
    return ans.startswith("yes")


# ── Утилиты извлечения кадров ─────────────────────────────────────────────────

def extract_frame(video_path: Path, timestamp_pct: float = 0.5) -> Image.Image | None:
    """Извлечь один кадр из видео через FFmpeg, вернуть PIL Image."""
    cmd = ["ffprobe", "-v", "quiet", "-print_format", "json",
           "-show_format", str(video_path)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    try:
        duration = float(json.loads(result.stdout)["format"]["duration"])
    except Exception:
        return None

    timestamp  = max(0.1, duration * timestamp_pct)
    frame_path = str(_TEMP / f"blip_frame_{video_path.stem}_{int(timestamp_pct*100)}.jpg")

    subprocess.run([
        "ffmpeg", "-y", "-ss", str(timestamp),
        "-i", str(video_path), "-vframes", "1",
        "-q:v", "2", "-loglevel", "quiet", frame_path,
    ], capture_output=True)

    p = Path(frame_path)
    if not p.exists():
        return None
    try:
        img = Image.open(frame_path).convert("RGB")
        img.load()   # форсируем загрузку в память
        return img
    except Exception:
        return None


def extract_frames(video_path: Path, pcts: list[float]) -> list[Image.Image]:
    """Извлечь несколько кадров, вернуть список PIL Image."""
    return [img for pct in pcts if (img := extract_frame(video_path, pct))]


# ── Главная функция анализа ───────────────────────────────────────────────────

def analyze_video(
    video_path:      Path,
    original_prompt: str,
    segment_text:    str,
    niche:           str = "cosmos",
) -> dict:
    """
    Анализ видео через BLIP VQA по 6 критериям.
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
            "exact_object":   False,
            "meaningful":     False,
            "reason":         "не удалось извлечь кадры",
            "score":          0,
        }

    # Обрезаем тексты до разумного размера
    seg_short    = segment_text[:120]
    prompt_short = original_prompt[:150]

    frame_results = []
    for image in frames:

        # ── КРИТЕРИЙ 1 — Критические визуальные дефекты ──────────────────────
        # Проверяем реальные проблемы: чёрный экран, полный стоп-кадр, обрезанное изображение
        q1 = (
            "Is this image completely black, completely white, "
            "or showing only a static test pattern or corrupted screen? "
            "Not counting normal dark space backgrounds."
        )

        # ── КРИТЕРИЙ 2 — Космическая/научная тематика ──────────────────────────
        q2 = (
            "Does this image show space, stars, planets, galaxies, nebulae, "
            "spacecraft, astronauts, telescopes, or any astronomy content?"
        )

        # ── КРИТЕРИЙ 3 — Соответствие промпту ────────────────────────────────
        q3 = f'Does this image show: "{prompt_short}"?'

        # ── КРИТЕРИЙ 4 — Базовое качество ────────────────────────────────────
        q4 = (
            "Is this image visible and recognizable, "
            "not completely blurred or fully corrupted?"
        )

        # ── КРИТЕРИЙ 5 — Наличие нужного объекта ─────────────────────────────
        q5 = f'Is there "{seg_short}" visible in this image?'

        # ── КРИТЕРИЙ 6 — Смысловое соответствие ──────────────────────────────
        q6 = f'Does this image relate to the topic: "{seg_short}"?'

        # Батч-запрос: все 6 вопросов за один HTTP-вызов
        batch_answers = _ask_blip_batch(image, [q1, q2, q3, q4, q5, q6])
        answers = {
            "artifacts":    batch_answers[0].startswith("yes"),
            "scientific":   batch_answers[1].startswith("yes"),
            "matches":      batch_answers[2].startswith("yes"),
            "quality":      batch_answers[3].startswith("yes"),
            "exact_object": batch_answers[4].startswith("yes"),
            "meaningful":   batch_answers[5].startswith("yes"),
        }
        frame_results.append(answers)

    # ── Голосование: 2 из 3 кадров решают ─────────────────────────────────────
    def majority(key: str, positive: bool = True) -> bool:
        return sum(1 for r in frame_results if r.get(key) == positive) >= 2

    has_artifacts  = majority("artifacts",    True)   # большинство видят артефакты
    is_scientific  = majority("scientific",   True)
    matches_prompt = majority("matches",      True)
    good_quality   = majority("quality",      True)
    exact_object   = majority("exact_object", True)
    meaningful     = majority("meaningful",   True)

    reasons = []
    if has_artifacts:      reasons.append("критический дефект")
    if not is_scientific:  reasons.append("не космос/наука")
    if not matches_prompt: reasons.append("не соответствует теме")
    if not good_quality:   reasons.append("плохое качество")
    if not exact_object:   reasons.append("неправильный объект")
    if not meaningful:     reasons.append("не соответствует смыслу")

    score = sum([not has_artifacts, is_scientific, matches_prompt,
                 good_quality, exact_object, meaningful])

    # Единственное жёсткое требование: нет критического дефекта (чёрный экран)
    # Если score >= 3 из 6 — видео годится для монтажа
    valid = (not has_artifacts) and score >= 3

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
