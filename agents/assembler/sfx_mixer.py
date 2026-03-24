"""
sfx_mixer.py — инжект SFX (whoosh / impact / glitch) в аудио на временных метках переходов.

Структура SFX-библиотеки:
    assets/sfx/whoosh/*.mp3   — ~-20 dB, короткий свист/свуш
    assets/sfx/impact/*.mp3   — ~-14 dB, удар/акцент
    assets/sfx/glitch/*.mp3   — ~-18 dB, только для channel_fr (glitch_flash переходы)

Правила (контекстные, по типу перехода):
    smooth_zoom  → whoosh (70% шанс), каждый 5-й → impact
    whip_pan     → whoosh (90% шанс) + impact на 5-м переходе
    glitch_flash → glitch_sfx (если есть), иначе whoosh (50%)
    cross_zoom   → whoosh (40% шанс), каждый 5-й → impact

    - Если SFX-файлов нет → возвращает original path без изменений (graceful fallback)

Уровни:
    whoosh: -20 dB (0.10 линейный)
    impact: -14 dB (0.20 линейный)
    glitch: -18 dB (0.126 линейный)
"""

import math
import os
import random
import subprocess
from pathlib import Path

# ─── Пути к SFX ──────────────────────────────────────────────────────────────

_BASE    = Path(__file__).resolve().parent.parent.parent
_SFX_DIR = _BASE / "assets" / "sfx"

_SFX_WHOOSH = list((_SFX_DIR / "whoosh").glob("*.mp3")) if (_SFX_DIR / "whoosh").exists() else []
_SFX_IMPACT = list((_SFX_DIR / "impact").glob("*.mp3")) if (_SFX_DIR / "impact").exists() else []
_SFX_GLITCH = list((_SFX_DIR / "glitch").glob("*.mp3")) if (_SFX_DIR / "glitch").exists() else []

# dB → linear
def _db(db: float) -> float:
    return 10 ** (db / 20)

_WHOOSH_VOL = _db(-20)   # 0.10
_IMPACT_VOL = _db(-14)   # 0.20
_GLITCH_VOL = _db(-18)   # 0.126


def _pick(pool: list) -> Path | None:
    """Случайно выбрать SFX-файл из пула."""
    return random.choice(pool) if pool else None


def _build_sfx_events(
    transition_times: list[float],
    sfx_enabled: bool,
    whoosh_chance: float,
    channel_id: str,
    transition_types: list[str] | None = None,
) -> list[dict]:
    """
    Построить список событий SFX: [{time, file, vol}, ...].

    transition_times:  временные метки переходов (секунды)
    transition_types:  тип каждого перехода (опционально); если None — используем whoosh_chance
                       Поддерживаемые: "smooth_zoom", "whip_pan", "glitch_flash", "cross_zoom"
    """
    if not sfx_enabled:
        return []
    if not _SFX_WHOOSH and not _SFX_IMPACT and not _SFX_GLITCH:
        return []

    # Контекстные шансы по типу перехода
    _WHOOSH_BY_TYPE = {
        "smooth_zoom":   0.70,
        "whip_pan":      0.90,
        "cross_zoom":    0.40,
        "glitch_flash":  0.50,   # glitch_sfx приоритет, whoosh как fallback
    }

    # Flash-frame types: 1-2 frames — no primary SFX (would sound like a click)
    _NO_SFX_TYPES = frozenset({"fadewhite", "fadeblack", "fade"})

    events = []
    for i, t in enumerate(transition_times):
        is_strong = (i % 5 == 4)   # каждый 5-й переход — сильный (impact)

        tr_type = (transition_types[i] if transition_types and i < len(transition_types)
                   else "cross_zoom")

        # === Flash frames: skip primary SFX ===
        if tr_type in _NO_SFX_TYPES:
            continue

        # ── Primary SFX selection ─────────────────────────────────────────────

        # glitch_flash → glitch_sfx (priority)
        if tr_type == "glitch_flash" and _SFX_GLITCH:
            sfx_file = _pick(_SFX_GLITCH)
            events.append({"time": t, "file": sfx_file, "vol": _GLITCH_VOL})
            continue

        # Strong cut → impact
        if is_strong and _SFX_IMPACT:
            sfx_file = _pick(_SFX_IMPACT)
            events.append({"time": t, "file": sfx_file, "vol": _IMPACT_VOL})

            # ── Secondary layer: quiet whoosh under impact (40% chance) ──────
            if _SFX_WHOOSH and random.random() < 0.40:
                events.append({
                    "time": t,
                    "file": _pick(_SFX_WHOOSH),
                    "vol":  _WHOOSH_VOL * 0.35,   # −9 dB below whoosh = ~−29 dB
                })
            continue

        # Whoosh by contextual chance
        chance = _WHOOSH_BY_TYPE.get(tr_type, whoosh_chance)
        if random.random() < chance and _SFX_WHOOSH:
            events.append({"time": t, "file": _pick(_SFX_WHOOSH), "vol": _WHOOSH_VOL})

            # ── Secondary layer: soft tail for whip_pan (30% chance) ─────────
            # Slightly delayed second whoosh at lower volume — adds depth/space
            if tr_type == "whip_pan" and _SFX_WHOOSH and random.random() < 0.30:
                events.append({
                    "time": t + 0.08,             # +80ms tail
                    "file": _pick(_SFX_WHOOSH),
                    "vol":  _WHOOSH_VOL * 0.45,   # −7 dB below primary = ~−27 dB
                })

    return events


def inject_sfx(
    audio_path: Path,
    output_path: Path,
    transition_times: list[float],
    sfx_enabled: bool,
    whoosh_chance: float,
    channel_id: str,
    transition_types: list[str] | None = None,
) -> Path:
    """
    Наложить SFX на аудио в точках переходов.

    transition_types: опциональный список типов переходов (parallel с transition_times).
                      Если передан — SFX подбирается контекстно по типу перехода.
    Если SFX не нужны или нет файлов — возвращает audio_path без изменений.
    """
    events = _build_sfx_events(
        transition_times, sfx_enabled, whoosh_chance, channel_id, transition_types
    )
    if not events:
        return audio_path

    # Строим filter_complex для FFmpeg:
    # [0:a] — основное аудио
    # [1:a], [2:a], ... — SFX-файлы
    # adelay + volume → amix

    cmd = ["ffmpeg", "-y", "-i", str(audio_path)]
    for ev in events:
        cmd += ["-i", str(ev["file"])]

    filter_parts = ["[0:a]aformat=sample_rates=44100:channel_layouts=stereo[main]"]
    mix_inputs   = "[main]"
    n_inputs     = 1

    for i, ev in enumerate(events, start=1):
        delay_ms = int(ev["time"] * 1000)
        vol      = ev["vol"]
        label    = f"sfx{i}"
        filter_parts.append(
            f"[{i}:a]"
            f"aformat=sample_rates=44100:channel_layouts=stereo,"
            f"adelay={delay_ms}|{delay_ms},"
            f"volume={vol:.4f}"
            f"[{label}]"
        )
        mix_inputs += f"[{label}]"
        n_inputs   += 1

    filter_parts.append(
        f"{mix_inputs}amix=inputs={n_inputs}:duration=first:normalize=0[aout]"
    )
    filter_complex = ";".join(filter_parts)

    result = subprocess.run(
        cmd + [
            "-filter_complex", filter_complex,
            "-map", "[aout]",
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
            "-loglevel", "warning",
            str(output_path),
        ],
        capture_output=True,
    )

    if result.returncode == 0 and output_path.exists():
        print(f"  🔊 SFX: {len(events)} событий → {output_path.name}")
        return output_path

    print(f"  ⚠️ SFX inject ошибка — используем оригинальное аудио")
    return audio_path


def compute_transition_times(
    segments: list[dict],
    intro_dur: float,
    slide_duration: float,
) -> list[float]:
    """
    Вычислить абсолютные временные метки переходов в итоговом видео.

    Переход N происходит в конце сегмента N (с учётом intro offset).
    Возвращает список времён в секундах.
    """
    times = []
    t = intro_dur
    for seg in segments:
        dur = seg.get("end", 0) - seg.get("start", 0)
        t  += dur
        times.append(t - slide_duration * 0.5)  # чуть раньше середины перехода
    return times
