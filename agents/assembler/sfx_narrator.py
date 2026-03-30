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
import random
from pathlib import Path

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

    with open(result_json_path, encoding="utf-8") as f:
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
    sfx_events = []
    for m in moments:
        t = m["time"]
        if m["type"] == "revelation":
            # Riser за 1.0s до момента + boom на моменте
            sfx_events.append({"time": max(0, t - 1.0), "category": "riser",  "score": m["score"]})
            sfx_events.append({"time": t,               "category": "boom",   "score": m["score"]})
        elif m["type"] == "buildup":
            sfx_events.append({"time": max(0, t - 0.5), "category": "riser",  "score": m["score"]})
        elif m["type"] == "question":
            sfx_events.append({"time": t,               "category": "whoosh", "score": m["score"]})
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
    Конвертировать [{time, category, score}] → [{time, file, vol}]
    готовых для inject_sfx().
    """
    from sfx_mixer import (
        _SFX_WHOOSH, _SFX_WHOOSH_BIG, _SFX_WHOOSH_FAST,
        _SFX_RISER, _SFX_BOOM, _SFX_IMPACT,
        _VOL_WHOOSH, _VOL_RISER, _VOL_BOOM, _VOL_IMPACT,
        _pick, _pick_best,
    )

    _CAT_POOL = {
        "whoosh": ([_SFX_WHOOSH,     _SFX_WHOOSH_FAST], _VOL_WHOOSH),
        "riser":  ([_SFX_RISER                       ], _VOL_RISER),
        "boom":   ([_SFX_BOOM,       _SFX_IMPACT     ], _VOL_BOOM),
        "impact": ([_SFX_IMPACT                      ], _VOL_IMPACT),
    }

    resolved = []
    for ev in sfx_events:
        cat = ev.get("category", "whoosh")
        pools, vol = _CAT_POOL.get(cat, (_CAT_POOL["whoosh"]))
        file = _pick_best(pools)
        if file:
            resolved.append({"time": ev["time"], "file": file, "vol": vol})
    return resolved
