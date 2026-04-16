"""
gemini_reranker.py — Context-Aware Flash reranker для топ-N кандидатов.

Получает:
  - текст сегмента
  - описание предыдущего выбранного клипа (для визуальной связности)
  - список кандидатов (clip_id + description)

Возвращает clip_id победителя.

Параллельный запуск через ThreadPoolExecutor для скорости.
"""

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

FLASH_MODEL   = "gemini-2.5-flash"
MAX_WORKERS   = 16     # параллельных запросов к Flash
RETRY_DELAY   = 1.0

_client = None

def _get_client():
    global _client
    if _client is None:
        import google.genai
        from dotenv import load_dotenv
        load_dotenv(ROOT / "config" / ".env")
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY не найден в config/.env")
        _client = google.genai.Client(api_key=api_key)
    return _client


_RERANK_PROMPT = """\
You are a video editor selecting the best stock footage clip for a documentary segment.

NARRATOR TEXT: "{segment}"

PREVIOUS CLIP: {prev_clip}

CANDIDATES (clip_id: description):
{candidates}

Rules:
- Pick the clip that best matches the narrator's topic visually
- Prefer visual continuity with the previous clip (avoid jarring jumps)
- Prefer clips showing the actual subject mentioned (rocket = rocket, not a person talking)
- Output ONLY the clip_id number, nothing else

Best clip_id:"""


def rerank_one(segment_text: str,
               candidates: list[tuple[str, str]],
               prev_clip_desc: str = "") -> str | None:
    """
    Реранк одного сегмента через Gemini Flash.

    candidates: [(clip_id, description), ...]
    Возвращает clip_id победителя или None если не распарсилось.
    """
    if not candidates:
        return None

    prev_str = f'"{prev_clip_desc}"' if prev_clip_desc else "none (first clip)"
    cands_str = "\n".join(f'{cid}: {desc[:120]}' for cid, desc in candidates)

    prompt = _RERANK_PROMPT.format(
        segment=segment_text[:200],
        prev_clip=prev_str,
        candidates=cands_str,
    )

    client = _get_client()
    for attempt in range(3):
        try:
            resp = client.models.generate_content(
                model=FLASH_MODEL,
                contents=prompt,
                config={"temperature": 0.0, "max_output_tokens": 16},
            )
            result = resp.text.strip().replace('"', '').replace("'", "").strip()
            # Проверяем что это валидный clip_id из наших кандидатов
            valid_ids = {cid for cid, _ in candidates}
            if result in valid_ids:
                return result
            # Попытка извлечь число если Flash написал "clip_id: 1234"
            import re
            m = re.search(r'\b(\d{4})\b', result)
            if m and m.group(1) in valid_ids:
                return m.group(1)
            return candidates[0][0]  # fallback: первый кандидат
        except Exception as e:
            if attempt < 2:
                time.sleep(RETRY_DELAY * (attempt + 1))
            else:
                return candidates[0][0]  # fallback при ошибке


def rerank_batch(tasks: list[dict]) -> dict[int, str]:
    """
    Параллельный реранк нескольких сегментов.

    tasks: [{"seg_idx": int, "segment_text": str, "candidates": [...], "prev_clip_desc": str}, ...]
    Возвращает {seg_idx: clip_id}
    """
    results = {}

    def _run(task):
        clip_id = rerank_one(
            task["segment_text"],
            task["candidates"],
            task.get("prev_clip_desc", ""),
        )
        return task["seg_idx"], clip_id

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(_run, t): t["seg_idx"] for t in tasks}
        for fut in as_completed(futures):
            try:
                seg_idx, clip_id = fut.result()
                results[seg_idx] = clip_id
            except Exception:
                pass

    return results
