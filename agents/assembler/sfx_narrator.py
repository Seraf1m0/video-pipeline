"""
sfx_narrator.py — контекстный SFX и динамика музыки по анализу сценария.

Анализирует result.json (сегменты Whisper) и определяет:
  - Моменты "revelation" (факт/открытие)  → boom + duck музыки
  - Моменты "buildup"   (нагнетание)       → riser + лёгкий swell музыки
  - Моменты "question"  (вопрос аудитории) → whoosh или riser
  - Моменты "calm"      (тихий/задумчивый) → duck музыки -3dB

Поддерживаемые языки: de, fr, en (базовый).

Использование:
    from sfx_narrator import analyze_script
    result = analyze_script("result.json", lang="de", intro_dur=90.0)
    sfx_events      = result["sfx"]           # [{time, file, vol}, ...]
    music_envelope  = result["music_envelope"] # [{time_abs, gain_db, fade_in_s, hold_s, fade_out_s}]
"""

import json
import os
import random
import re as _re
import subprocess
from pathlib import Path

# ─── Claude-based SFX analysis ───────────────────────────────────────────────

def _parse_time_val(raw) -> float:
    """Parse time from float/int/string (supports 'mm:ss' and plain seconds)."""
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip().lstrip("[").rstrip("s]")
    if ":" in s:
        parts = s.split(":")
        try:
            return int(parts[0]) * 60 + float(parts[1])
        except Exception:
            pass
    try:
        return float(s)
    except Exception:
        return 0.0


def _call_claude_for_sfx(
    segments:  list[dict],
    lang:      str,
    intro_dur: float,
) -> "list[dict] | None":
    """
    Анализирует транскрипт через Claude CLI и возвращает SFX события.
    [{time, category, score}] или None при ошибке (fallback на keyword matching).

    Категории для Claude:
      riser      — нагнетание, 0.8s ДО кульминации
      boom       — сильная кульминация / главное откровение
      impact     — умеренный акцент / важный факт
      whoosh_big — смена темы / переход между секциями
      whoosh_fast— лёгкий переход / риторический вопрос
      downlifter — спад после кульминации, 3-5s ПОСЛЕ boom
    """
    # Фильтруем: только реальные сегменты с текстом (без [конец], пустых, музыки)
    _skip = {"[конец]", "[musik]", "[music]", "[applause]", "[gelächter]", "[lachen]"}
    lines = []
    for seg in segments:
        t    = float(seg.get("start", 0))
        text = str(seg.get("text", "")).strip()
        if t < intro_dur or not text:
            continue
        if text.lower() in _skip or text.startswith("[") and text.endswith("]"):
            continue
        lines.append(f"[{t:.1f}s] {text}")

    if len(lines) < 3:
        return None

    transcript = "\n".join(lines[:200])  # max ~200 реальных сегментов

    prompt = (
        "You are a professional audio post-production editor. "
        "Analyze this video transcript and decide WHERE to place sound effects (SFX) "
        "to enhance the storytelling. The SFX must complement the narration — "
        "subtle and purposeful, never distracting from the voice.\n\n"
        "Available SFX categories:\n"
        "  riser       — tension build-up, place 0.8s BEFORE the climax moment\n"
        "  boom        — strong climax / major reveal (at the exact moment)\n"
        "  impact      — moderate emphasis / important fact (at the moment)\n"
        "  whoosh_big  — major topic shift / section change (at transition)\n"
        "  whoosh_fast — subtle transition / rhetorical question\n"
        "  downlifter  — emotional comedown, 3-5s AFTER a boom/impact\n\n"
        "Rules:\n"
        "  - Minimum 20 seconds between any two events\n"
        "  - 8 to 14 events total for the whole video\n"
        "  - 'riser' always precedes 'boom'/'impact' by 0.8-1.0s\n"
        "  - 'downlifter' always follows 'boom' by 3-5s\n"
        "  - Only mark genuinely dramatic or emotionally significant moments\n"
        "  - intensity 1-10 (1=barely audible hint, 10=strongest moment)\n"
        "  - Timestamps are in seconds as shown in the transcript\n\n"
        f"Language: {lang}\n\n"
        f"Transcript:\n{transcript}\n\n"
        "Return ONLY a valid JSON array, no markdown, no explanation:\n"
        '[{"time_s": 124.5, "category": "riser", "intensity": 7}, ...]'
    )

    env = os.environ.copy()
    env.pop("CLAUDECODE", None)

    try:
        r = subprocess.run(
            ["claude.cmd", "--model", "claude-haiku-4-5", "-p", "-"],
            input=prompt,
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            env=env, timeout=300,
        )
        if r.returncode != 0:
            print(f"  [SFX-Claude] exit {r.returncode} — fallback to keywords")
            return None

        raw = r.stdout.strip()
        # Извлекаем JSON массив (Claude иногда добавляет markdown)
        m = _re.search(r"\[.*?\]", raw, _re.DOTALL)
        if not m:
            return None

        items = json.loads(m.group())
        result = []
        for item in items:
            if not isinstance(item, dict):
                continue
            t   = _parse_time_val(item.get("time_s", item.get("time", 0)))
            cat = str(item.get("category", "whoosh_fast")).lower().strip()
            ins = float(item.get("intensity", 5))
            if t > intro_dur:
                result.append({"time": t, "category": cat, "score": ins})

        if result:
            result.sort(key=lambda e: e["time"])
            print(f"  [SFX-Claude] {len(result)} событий")
        return result if result else None

    except Exception as exc:
        print(f"  [SFX-Claude] ошибка: {exc} — fallback to keywords")
        return None


# ─── Словари ключевых слов ────────────────────────────────────────────────────

_KEYWORDS: dict[str, dict[str, list[str]]] = {
    "de": {
        "revelation": [
            "tatsächlich", "wirklich", "enthüllt", "enthüllte", "entdeckt",
            "unglaublich", "erstaunlich", "verblüffend", "unerwartet",
            "plötzlich", "niemals", "unmöglich", "bewiesen", "beweis",
            "tatsache", "niemand weiß", "kaum zu glauben", "zum ersten mal",
        ],
        "buildup": [
            "geheimnis", "geheimnisvoll", "mysteriös", "rätsel", "rätselhaft",
            "gefährlich", "dunkel", "seltsam", "merkwürdig", "beunruhigend",
            "warnung", "gefahr", "bedrohung", "verschwörung", "verborgen",
            "erschreckend", "schrecklich", "unheimlich",
        ],
        "calm": [
            "stille", "ruhe", "erinnerung", "vergessen", "vergangenheit",
            "langsam", "leise", "sanft", "friedlich", "entspannt",
        ],
    },
    "fr": {
        "revelation": [
            "incroyable", "fascinant", "étonnant", "révèle", "révélé",
            "découverte", "découvert", "jamais", "impossible", "prouvé",
            "soudain", "personne ne", "vraiment", "véritable",
            "pour la première fois", "personne n'",
        ],
        "buildup": [
            "mystère", "mystérieux", "mystérieuse", "énigme", "énigmatique",
            "étrange", "dangereux", "dangereuse", "sombre", "caché", "cachée",
            "inquiétant", "terrifying", "terrifiant", "sinistre", "obscur",
        ],
        "calm": [
            "silence", "calme", "souvenir", "oublié", "oubliée", "passé",
            "doucement", "lentement", "paisible",
        ],
    },
    "en": {
        "revelation": [
            "actually", "incredible", "unbelievable", "revealed", "discovered",
            "impossible", "proven", "suddenly", "nobody", "never before",
            "first time", "shocking",
        ],
        "buildup": [
            "mysterious", "mystery", "secret", "hidden", "dangerous", "dark",
            "strange", "terrifying", "conspiracy", "forbidden", "unknown",
        ],
        "calm": [
            "silence", "quiet", "calm", "memory", "forgotten", "past",
            "slowly", "gently", "peaceful",
        ],
    },
}


# ─── Скоринг сегментов ────────────────────────────────────────────────────────

def _score_segment(text: str, keywords: dict) -> tuple[float, str]:
    """
    Оценить «драматичность» текстового сегмента.
    Возвращает (score, type).
    type: 'revelation' | 'buildup' | 'question' | 'calm' | 'neutral'
    """
    t = text.lower().strip()
    score = 0.0

    # Пунктуация
    if "?" in text:
        score += 2.5
    if "!" in text:
        score += 3.0

    # Ключевые слова
    for kw in keywords.get("revelation", []):
        if kw in t:
            score += 4.0
            break
    for kw in keywords.get("buildup", []):
        if kw in t:
            score += 2.5
            break
    for kw in keywords.get("calm", []):
        if kw in t:
            score -= 3.0
            break

    # Классификация
    if "?" in text and score >= 2.5:
        return score, "question"
    if score >= 6.0:
        return score, "revelation"
    if score >= 3.0:
        return score, "buildup"
    if score <= -1.5:
        return score, "calm"
    return score, "neutral"


# ─── Анализ сценария ──────────────────────────────────────────────────────────

def analyze_script(
    result_json_path: "Path | str",
    lang:             str   = "de",
    intro_dur:        float = 90.0,
    min_interval_s:   float = 12.0,
) -> dict:
    """
    Анализировать result.json и вернуть SFX-события + огибающую музыки.

    Параметры
    ---------
    result_json_path : путь к result.json (Whisper)
    lang             : язык ('de', 'fr', 'en')
    intro_dur        : секунды интро (события до этой метки игнорируются)
    min_interval_s   : минимальный интервал между событиями (чтобы не частить)

    Возвращает
    ----------
    {
      "sfx":           [{time, category, score}, ...],
      "music_envelope": [{time_abs, gain_db, fade_in_s, hold_s, fade_out_s}, ...],
      "moments":        [{time, end, text, score, type}, ...]
    }
    """
    result_json_path = Path(result_json_path)
    if not result_json_path.exists():
        return {"sfx": [], "music_envelope": [], "moments": []}

    # Если рядом есть result_visual.json — берём его (содержит sfx_cue от Haiku)
    result_visual = result_json_path.parent / "result_visual.json"
    load_path = result_visual if result_visual.exists() else result_json_path
    if load_path != result_json_path:
        print(f"  [SFX] Загружаем result_visual.json (Haiku уже разметил sfx_cue)")

    with open(load_path, encoding="utf-8") as f:
        data = json.load(f)

    segments = data.get("segments", [])

    # Определяем язык из result.json если не задан явно
    detected_lang = data.get("language", lang).lower()[:2]
    kw = _KEYWORDS.get(detected_lang, _KEYWORDS.get(lang, _KEYWORDS["en"]))

    moments = []
    for seg in segments:
        t_start = float(seg.get("start", 0))
        t_end   = float(seg.get("end", t_start + 0.5))
        text    = str(seg.get("text", "")).strip()

        if t_start < intro_dur or not text:
            continue

        score, kind = _score_segment(text, kw)

        if kind != "neutral":
            moments.append({
                "time":  t_start,
                "end":   t_end,
                "text":  text,
                "score": score,
                "type":  kind,
            })

    # Deduplicate: убираем события ближе min_interval_s друг к другу
    moments.sort(key=lambda m: m["time"])
    filtered = []
    last_t = -999.0
    for m in moments:
        if m["time"] - last_t >= min_interval_s:
            filtered.append(m)
            last_t = m["time"]
    moments = filtered

    # ── SFX события ───────────────────────────────────────────────────────────
    # Приоритет 1: result_visual.json уже содержит sfx_cue от Haiku (visual_query_generator)
    #              → берём оттуда, Haiku не вызываем повторно
    # Приоритет 2: вызов Haiku через claude.cmd (если visual_query_generator не запускался)
    # Приоритет 3: keyword matching fallback

    _SFX_CUE_MAP = {
        "riser":      "riser",
        "boom":       "boom",
        "riser+boom": "boom",   # riser ставим отдельно ниже
        "impact":     "impact",
        "downlifter": "downlifter",
        "whoosh_big": "whoosh_big",
        "whoosh_fast":"whoosh_fast",
        "glitch":     "glitch",
    }

    # Проверяем: есть ли sfx_cue хотя бы в одном сегменте
    visual_sfx = [s for s in segments if s.get("sfx_cue") and float(s.get("start", 0)) >= intro_dur]

    if visual_sfx:
        print(f"  [SFX] result_visual.json: {len(visual_sfx)} sfx_cue сегментов → без повторного вызова Haiku")
        sfx_events = []
        for seg in visual_sfx:
            t   = float(seg.get("start", 0))
            cue = str(seg.get("sfx_cue", "")).lower().strip()
            cat = _SFX_CUE_MAP.get(cue, cue)
            # riser+boom → добавляем riser за 0.8s до бума
            if seg.get("sfx_cue") == "riser+boom":
                sfx_events.append({"time": max(intro_dur, t - 0.8), "category": "riser", "score": 7})
            sfx_events.append({"time": t, "category": cat, "score": 7})
    else:
        # Haiku вызов — только если visual_query_generator не запускался
        claude_sfx = _call_claude_for_sfx(segments, lang, intro_dur)
        if claude_sfx:
            sfx_events = claude_sfx
        else:
            # Keyword matching fallback
            sfx_events = []
            for m in moments:
                t = m["time"]
                if m["type"] == "revelation":
                    sfx_events.append({"time": max(0, t - 1.0), "category": "riser",      "score": m["score"]})
                    sfx_events.append({"time": t,               "category": "boom",        "score": m["score"]})
                    sfx_events.append({"time": t + 4.0,         "category": "downlifter",  "score": m["score"] * 0.6})
                elif m["type"] == "buildup":
                    sfx_events.append({"time": max(0, t - 0.5), "category": "riser",       "score": m["score"]})
                elif m["type"] == "question":
                    sfx_events.append({"time": t,               "category": "whoosh_fast", "score": m["score"]})
                # calm → только музыкальный envelope, без SFX

    # ── Огибающая музыки ──────────────────────────────────────────────────────
    music_envelope = []
    for m in moments:
        t = m["time"]
        seg_dur = max(0.5, m["end"] - m["time"])

        if m["type"] == "revelation":
            # Небольшой swell +2dB перед словом → duck -4dB после (даём голосу звучать)
            music_envelope.append({
                "time_abs":   max(0, t - 0.8),
                "gain_db":    2.0,
                "fade_in_s":  0.4,
                "hold_s":     0.5,
                "fade_out_s": 0.3,
            })
            music_envelope.append({
                "time_abs":   t + 0.3,
                "gain_db":    -4.0,
                "fade_in_s":  0.2,
                "hold_s":     max(1.5, seg_dur),
                "fade_out_s": 0.6,
            })

        elif m["type"] == "buildup":
            # Лёгкий swell +1.5dB
            music_envelope.append({
                "time_abs":   max(0, t - 0.3),
                "gain_db":    1.5,
                "fade_in_s":  0.3,
                "hold_s":     max(1.0, seg_dur * 0.5),
                "fade_out_s": 0.4,
            })

        elif m["type"] == "question":
            # Duck -2dB (вопрос — голос важнее)
            music_envelope.append({
                "time_abs":   t,
                "gain_db":    -2.0,
                "fade_in_s":  0.3,
                "hold_s":     seg_dur + 0.5,
                "fade_out_s": 0.5,
            })

        elif m["type"] == "calm":
            # Duck -3dB на всё время тихого момента
            music_envelope.append({
                "time_abs":   t,
                "gain_db":    -3.0,
                "fade_in_s":  0.5,
                "hold_s":     seg_dur,
                "fade_out_s": 0.5,
            })

    sfx_events.sort(key=lambda e: e["time"])
    music_envelope.sort(key=lambda e: e["time_abs"])

    print(f"  [Narrator] {len(moments)} моментов: "
          f"revelation={sum(1 for m in moments if m['type']=='revelation')}  "
          f"buildup={sum(1 for m in moments if m['type']=='buildup')}  "
          f"question={sum(1 for m in moments if m['type']=='question')}  "
          f"calm={sum(1 for m in moments if m['type']=='calm')}")

    return {
        "sfx":            sfx_events,
        "music_envelope": music_envelope,
        "moments":        moments,
    }


# ─── Построение ffmpeg volume expression ─────────────────────────────────────

def build_volume_expr(envelope: list[dict], base_gain: float = 1.0) -> str:
    """
    Построить ffmpeg volume expression из огибающей.

    envelope: [{time_abs, gain_db, fade_in_s, hold_s, fade_out_s}, ...]
    base_gain: базовый линейный коэффициент (обычно music_vol)

    Возвращает строку вида:
        {base_gain}+delta1*ramp1+delta2*ramp2+...

    Для использования в ffmpeg: volume=volume='{expr}':eval=frame
    """
    if not envelope:
        return str(base_gain)

    terms = []
    for ev in envelope:
        t0       = float(ev["time_abs"])
        gain_db  = float(ev["gain_db"])
        fade_in  = max(0.05, float(ev.get("fade_in_s",  0.3)))
        hold     = max(0.0,  float(ev.get("hold_s",     1.0)))
        fade_out = max(0.05, float(ev.get("fade_out_s", 0.3)))

        t_up   = t0
        t_hold = t0 + fade_in
        t_down = t0 + fade_in + hold
        t_end  = t0 + fade_in + hold + fade_out

        # Линейный коэффициент относительно base_gain:
        # swell +2dB при base_gain=0.08 → add delta = 0.08*(10^(2/20)-1)
        import math
        delta = base_gain * (10 ** (gain_db / 20) - 1.0)

        # ramp_up   = clamp((t - t_up) / fade_in, 0, 1)
        # ramp_down = clamp((t_end - t) / fade_out, 0, 1)
        # hold region is naturally captured by min of both ramps
        term = (
            f"{delta:.6f}"
            f"*max(0,min(1,(t-{t_up:.3f})/{fade_in:.3f}))"
            f"*max(0,min(1,({t_end:.3f}-t)/{fade_out:.3f}))"
        )
        terms.append(term)

    return f"{base_gain:.6f}+" + "+".join(terms)


# ─── Резолюция SFX-событий в реальные файлы ──────────────────────────────────

def resolve_sfx_events(sfx_events: list[dict], sfx_dir: "Path | str") -> list[dict]:
    """
    Конвертировать [{time, category, score}] → [{time, file, category, score}]
    готовых для build_final_audio().

    Все 8 SFX категорий задействованы.
    category и score передаются дальше для правильного gain и логирования.
    """
    from sfx_mixer import (
        _SFX_WHOOSH, _SFX_WHOOSH_BIG, _SFX_WHOOSH_FAST, _SFX_WHOOSH_ALL,
        _SFX_RISER, _SFX_BOOM, _SFX_IMPACT, _SFX_DOWNLIFTER, _SFX_GLITCH,
        _pick,
    )

    # Маппинг категории → пул файлов
    _CAT_POOL: dict[str, list] = {
        "riser":       _SFX_RISER,
        "boom":        _SFX_BOOM       or _SFX_IMPACT,
        "impact":      _SFX_IMPACT     or _SFX_BOOM,
        "whoosh":      _SFX_WHOOSH_ALL or _SFX_WHOOSH,
        "whoosh_big":  _SFX_WHOOSH_BIG or _SFX_WHOOSH_ALL,
        "whoosh_fast": _SFX_WHOOSH_FAST or _SFX_WHOOSH_ALL,
        "downlifter":  _SFX_DOWNLIFTER,
        "glitch":      _SFX_GLITCH,
    }
    _FALLBACK = _SFX_WHOOSH_FAST or _SFX_WHOOSH_ALL or _SFX_WHOOSH

    resolved = []
    for ev in sfx_events:
        cat  = str(ev.get("category", "whoosh_fast")).lower().strip()
        pool = _CAT_POOL.get(cat) or _FALLBACK
        if not pool:
            continue
        file = _pick(pool, category=cat)
        if file:
            resolved.append({
                "time":     float(ev["time"]),
                "file":     file,
                "category": cat,
                "score":    float(ev.get("score", 5.0)),
            })
    resolved.sort(key=lambda e: e["time"])
    return resolved
