"""
Claude Analyzer — получает описания кадров от Qwen и решает: хорошее видео или нет.
Возвращает match_score, what_qwen_sees, what_segment_needs + search_queries для замены.

Использует Claude CLI (claude.exe) через subprocess.
"""

import json
import os
import subprocess
from pathlib import Path

# Добавить claude.exe в PATH
_CLAUDE_DIR = Path(os.environ.get("APPDATA", "")) / "Claude/claude-code"
if _CLAUDE_DIR.exists():
    for _v in sorted(_CLAUDE_DIR.iterdir(), reverse=True):
        _exe = _v / "claude.exe"
        if _exe.exists():
            os.environ["PATH"] = str(_v) + os.pathsep + os.environ.get("PATH", "")
            break

_BASE_DIR = Path(__file__).resolve().parent.parent.parent


def _call_claude(prompt: str) -> str:
    """Вызвать Claude CLI и вернуть текст ответа."""
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    r = subprocess.run(
        ["claude", "-p", prompt],
        capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        timeout=60, env=env,
    )
    if r.returncode != 0:
        raise RuntimeError(f"claude CLI code {r.returncode}: {r.stderr[:200]}")
    return r.stdout.strip()


def _default_queries(segment_text: str) -> list[str]:
    """Fallback запросы если Claude недоступен — берём первые слова + space."""
    words = segment_text.lower().split()[:4]
    main = " ".join(words)
    return [
        f"{main} space footage",
        "deep space cosmos astronomy",
        "space universe stars galaxy",
    ]


def generate_search_queries(
    segment_text: str,
    niche: str = "cosmos",
) -> list[str]:
    """
    Сгенерировать 3 поисковых запроса для стока через Claude CLI.
    Fallback: возвращает _default_queries.
    """
    prompt = (
        f"You are a stock footage search expert for a SPACE & ASTRONOMY YouTube channel.\n\n"
        f"Generate exactly 3 Pixabay stock video search queries for this segment:\n"
        f"\"{segment_text}\"\n\n"
        f"Rules:\n"
        f"- Query 1: most specific (exact object + context)\n"
        f"- Query 2: slightly broader\n"
        f"- Query 3: general space fallback\n"
        f"- ALL queries MUST be space-related\n"
        f"- Use English only, 2-5 words per query\n"
        f"- NEVER use: trees, ocean, people, buildings, nature\n\n"
        f"Respond ONLY with valid JSON array (no markdown):\n"
        f'["query 1", "query 2", "query 3"]'
    )
    try:
        text = _call_claude(prompt)
        text = text.replace("```json", "").replace("```", "").strip()
        start = text.find("[")
        end   = text.rfind("]") + 1
        if start >= 0 and end > start:
            queries = json.loads(text[start:end])
            if isinstance(queries, list) and queries:
                return [str(q) for q in queries[:3]]
    except Exception as e:
        print(f"  ⚠️  generate_search_queries ошибка: {e}", flush=True)
    return _default_queries(segment_text)


def analyze_with_claude(
    vision_result: dict | None,
    segment_text: str,
    niche: str = "cosmos",
    channel_lang: str = "de",
) -> dict:
    """
    Claude анализирует описания кадров от Qwen.

    Входные данные:
      vision_result: {
        "frames_description": [{"frame": int, "question": str, "description": str}],
        "descriptions": [str, str, str],   # shorthand
        "black_screen": bool,
        "frozen": bool,
      }
      segment_text: текст сегмента
      niche: тематика (cosmos / science)
      channel_lang: язык канала (de / fr)

    Возвращает:
      {
        "valid": bool,
        "match_score": int,          # 0-10
        "score": int,                # 0-4
        "reason": str,               # причина на русском
        "what_qwen_sees": str,       # что видит Qwen (1 предложение)
        "what_segment_needs": str,   # что нужно (1 предложение)
        "search_queries": [str, ...],
      }
    """
    blip_result = vision_result  # внутренний алиас для совместимости
    if not blip_result:
        return {
            "valid": False,
            "reason": "нет данных Vision",
            "score": 0,
            "match_score": 0,
            "what_qwen_sees": "нет данных",
            "what_segment_needs": segment_text[:80],
            "search_queries": _default_queries(segment_text),
        }

    # Немедленный reject на технические проблемы
    if blip_result.get("black_screen"):
        return {
            "valid": False,
            "reason": "чёрный экран",
            "score": 0,
            "match_score": 0,
            "what_qwen_sees": "чёрный экран",
            "what_segment_needs": segment_text[:80],
            "search_queries": _default_queries(segment_text),
        }
    if blip_result.get("frozen"):
        return {
            "valid": False,
            "reason": "стоп-кадр (видео заморожено)",
            "score": 0,
            "match_score": 0,
            "what_qwen_sees": "стоп-кадр",
            "what_segment_needs": segment_text[:80],
            "search_queries": _default_queries(segment_text),
        }

    # Собрать описания кадров
    frames_desc = blip_result.get("frames_description", [])
    if frames_desc:
        # Новый формат: список {frame, question, description}
        frames_text = "\n\n".join(
            f"КАДР {f['frame']}:\n"
            f"Вопрос: {f['question']}\n"
            f"Qwen ответ: {f['description']}"
            for f in frames_desc
        )
    else:
        # Backward compat: старый формат descriptions
        descs = blip_result.get("descriptions", [])
        if not descs:
            return {
                "valid": True,
                "reason": "нет описаний Qwen — принято по умолчанию",
                "score": 3,
                "match_score": 5,
                "what_qwen_sees": "нет данных",
                "what_segment_needs": segment_text[:80],
                "search_queries": [],
            }
        frames_text = "\n".join(
            f"Frame {i+1} ({['25%','50%','75%'][i]}): {d}"
            for i, d in enumerate(descs)
        )

    prompt = (
        f"You are a quality controller for a SPACE & ASTRONOMY YouTube channel.\n\n"

        f"SEGMENT (what the footage MUST show):\n"
        f"\"{segment_text}\"\n\n"

        f"Qwen VISION ANALYSIS OF THE FOOTAGE:\n"
        f"{frames_text}\n\n"

        f"YOUR TASK:\n"
        f"1. Determine if footage MATCHES the segment\n"
        f"2. Check if it's space/astronomy content\n"
        f"3. Generate search queries IF invalid\n\n"

        f"MATCHING RULES:\n"
        f"- Specific objects MUST match exactly:\n"
        f"  * Segment says 'James Webb Telescope' → must show Webb, not Hubble\n"
        f"  * Segment says 'Saturn' → must show Saturn, not Jupiter\n"
        f"  * Segment says 'Mars rover' → must show rover on Mars\n"
        f"- General space content is OK for abstract segments\n"
        f"- NO Earth footage unless segment specifically mentions Earth\n\n"

        f"INVALID if footage shows:\n"
        f"- Wrong specific object (Hubble instead of Webb)\n"
        f"- Non-space content (nature, people, buildings, offices)\n"
        f"- Black screen or frozen frame\n"
        f"- AI artifacts or distortions\n"
        f"- Portrait orientation content\n\n"

        f"FOR search_queries IF INVALID:\n"
        f"- Extract the MAIN OBJECT from segment\n"
        f"- Think: what stock footage exists for this topic?\n"
        f"- Query 1: most specific (exact object + context)\n"
        f"- Query 2: slightly broader\n"
        f"- Query 3: general space fallback\n"
        f"- ALL queries MUST be space-related\n"
        f"- NEVER use: trees, ocean, people, buildings, nature\n\n"

        f"Respond ONLY with valid JSON (no markdown, no extra text):\n"
        f"{{\n"
        f'  "valid": true,\n'
        f'  "match_score": 8,\n'
        f'  "score": 4,\n'
        f'  "reason": "причина на русском",\n'
        f'  "what_qwen_sees": "what Qwen sees (1 sentence in English)",\n'
        f'  "what_segment_needs": "what is needed (1 sentence in English)",\n'
        f'  "search_queries": [\n'
        f'    "specific query 1",\n'
        f'    "broader query 2",\n'
        f'    "fallback query 3"\n'
        f'  ]\n'
        f"}}"
    )

    try:
        text = _call_claude(prompt)
        text = text.replace("```json", "").replace("```", "").strip()
        start = text.find("{")
        end   = text.rfind("}") + 1
        if start >= 0 and end > start:
            text = text[start:end]
        result = json.loads(text)

        valid       = bool(result.get("valid", True))
        match_score = int(result.get("match_score", 5))
        score       = int(result.get("score", 3))
        reason      = result.get("reason", "")
        what_sees   = result.get("what_qwen_sees", "")
        what_needs  = result.get("what_segment_needs", "")
        queries     = result.get("search_queries", [])

        # Если match_score < 5 → тоже invalid
        if match_score < 5:
            valid = False
            if not reason or reason == "ok":
                reason = f"низкое совпадение с сегментом ({match_score}/10)"

        # Если invalid и нет запросов — fallback
        if not valid and not queries:
            queries = _default_queries(segment_text)

        emoji = "✅" if valid else "❌"
        print(
            f"  🤖 Claude: {emoji} match={match_score}/10 score={score}/4",
            flush=True,
        )
        print(f"  👁️  Qwen видит: {what_sees}", flush=True)
        print(f"  🎯 Нужно: {what_needs}", flush=True)
        print(f"  📝 Причина: {reason}", flush=True)

        return {
            "valid":             valid,
            "match_score":       match_score,
            "score":             score,
            "reason":            reason,
            "what_qwen_sees":    what_sees,
            "what_segment_needs": what_needs,
            "search_queries":    queries,
        }

    except Exception as e:
        print(f"  ⚠️  Claude ошибка ({e}) — принимаем видео", flush=True)
        return {
            "valid":             True,
            "reason":            f"Claude недоступен: {e}",
            "score":             3,
            "match_score":       5,
            "what_qwen_sees":    "",
            "what_segment_needs": "",
            "search_queries":    [],
        }
