"""
sfx_mixer.py — инжект SFX (whoosh / glitch / riser / boom / downlifter / impact) в аудио.

Структура SFX-библиотеки (assets/sfx/):
    whoosh/       — только для glitch_flash (100% на глитч) + под boom (50%)
    whoosh_big/   — тяжёлые свиши, для whip_pan / zoom_blur / whip_pan tail
    whoosh_fast/  — лёгкие быстрые, для smooth_zoom / cross_zoom
    impact/       — удары: семантический триггер через sfx_mapper (action+energy>0.8)
                    НЕ использует механический триггер "каждый 5-й переход"
    glitch/       — глитч-звуки (для glitch_flash переходов, 100%)
    riser/        — нарастание ПЕРЕД сильным переходом (ends exactly at transition)
    downlifter/   — падение ПОСЛЕ перехода интро→видеоряд (+0.1s после метки)
    boom/         — кинематографический удар (каждый 10-й переход + PEAK_MOMENT)

Правила по типу перехода:
    smooth_zoom  → whoosh_fast (60%)
    whip_pan     → whoosh_big  (85%),  tail whoosh_big (30% шанс, +80ms)
    zoom_blur    → whoosh_big  (75%)
    cross_zoom   → whoosh_fast (40%)   ← был whoosh_fast + whoosh, теперь только whoosh_fast
    glitch_flash → glitch (100%)
    crossfade    → ничего (0%)

Дополнительные слои:
    - Riser: за 0.8s ДО каждого 10-го перехода
    - Boom: НА каждом 10-м переходе (кинематографический удар)
    - Под boom: тихий whoosh (50% шанс, пул whoosh_big + whoosh_fast)
    - Downlifter: через 0.1s после intro_end_time

Уровни (нормализованные к голосу):
    Цель: SFX = 35% громкости озвучки (-27 dBFS при voice mean=-17.9 dB)
    Каждый файл нормализуется автоматически через _sfx_gain().
    Коэффициенты категорий: whoosh=1.0×, impact=1.3×, boom=1.5×, riser=0.6×
"""

import math
import os
import random
import subprocess
from pathlib import Path

# ─── Пути к SFX ──────────────────────────────────────────────────────────────

_BASE    = Path(__file__).resolve().parent.parent.parent
_SFX_DIR = _BASE / "assets" / "sfx"


def _load(subdir: str) -> list[Path]:
    d = _SFX_DIR / subdir
    if not d.exists():
        return []
    return [f for f in d.iterdir() if f.suffix.lower() in (".mp3", ".wav", ".ogg", ".flac")]


_SFX_WHOOSH      = _load("whoosh")
_SFX_WHOOSH_BIG  = _load("whoosh_big")
_SFX_WHOOSH_FAST = _load("whoosh_fast")
_SFX_IMPACT      = _load("impact")
_SFX_GLITCH      = _load("glitch")
_SFX_RISER       = _load("riser")
_SFX_DOWNLIFTER  = _load("downlifter")
_SFX_BOOM        = _load("boom")

# Объединённый пул "любой whoosh" (фолбэк)
_SFX_WHOOSH_ALL  = _SFX_WHOOSH + _SFX_WHOOSH_BIG + _SFX_WHOOSH_FAST


# ─── Уровни dB → linear ──────────────────────────────────────────────────────

def _db(db: float) -> float:
    return 10 ** (db / 20)

# Относительные коэффициенты категорий (1.0 = точно -27dB / 35% голоса)
# boom/impact чуть громче для акцента, riser/downlifter тише
_VOL_WHOOSH      = 0.20  # 20% от базового нормализованного уровня
_VOL_IMPACT      = 0.26
_VOL_GLITCH      = 0.20
_VOL_RISER       = 0.12
_VOL_DOWNLIFTER  = 0.16
_VOL_BOOM        = 0.30


# ─── Утилиты ─────────────────────────────────────────────────────────────────

def _pick(pool: list) -> Path | None:
    return random.choice(pool) if pool else None

def _pick_best(pools: list[list]) -> Path | None:
    """Выбрать случайный файл из первого непустого пула."""
    for p in pools:
        if p:
            return random.choice(p)
    return None

# Целевой уровень SFX: 35% от громкости озвучки (-17.9 dB среднее)
# 20*log10(0.35) ≈ -9.1 dB относительно голоса → -17.9 + (-9.1) = -27 dB
_SFX_TARGET_DB = -27.0

_dur_cache:   dict[str, float] = {}
_level_cache: dict[str, float] = {}


def _sfx_dur(path: Path) -> float:
    """Длительность SFX файла (кэшируется)."""
    key = str(path)
    if key not in _dur_cache:
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                 "-of", "csv=p=0", str(path)],
                capture_output=True, text=True,
            )
            _dur_cache[key] = float(r.stdout.strip())
        except Exception:
            _dur_cache[key] = 1.0
    return _dur_cache[key]


def _sfx_mean_db(path: Path) -> float:
    """Средний уровень SFX файла в dBFS (кэшируется)."""
    key = str(path)
    if key not in _level_cache:
        try:
            r = subprocess.run(
                ["ffmpeg", "-i", str(path), "-af", "volumedetect",
                 "-f", "null", "/dev/null"],
                capture_output=True, text=True,
            )
            for line in r.stderr.splitlines():
                if "mean_volume:" in line:
                    _level_cache[key] = float(line.split("mean_volume:")[1].split("dB")[0].strip())
                    break
            else:
                _level_cache[key] = -20.0  # fallback
        except Exception:
            _level_cache[key] = -20.0
    return _level_cache[key]


_SFX_HARD_LIMIT_DB = -26.0   # SFX не может быть громче этого после всех gain
MAX_SFX_PER_MIN    = 3        # максимальная плотность SFX событий в минуту

def _sfx_gain(path: Path, category_vol: float) -> float:
    """
    Вычислить линейный volume для SFX файла.
    Нормализует к _SFX_TARGET_DB, затем применяет category_vol.
    Бросает ValueError если итоговый уровень превышает _SFX_HARD_LIMIT_DB.
    """
    mean_db  = _sfx_mean_db(path)
    gain_db  = _SFX_TARGET_DB - mean_db
    linear   = (10 ** (gain_db / 20)) * category_vol
    final_db = mean_db + gain_db + 20 * math.log10(max(linear, 1e-9))
    if final_db > _SFX_HARD_LIMIT_DB:
        raise ValueError(
            f"SFX {path.name}: final level {final_db:.1f}dB exceeds hard limit "
            f"{_SFX_HARD_LIMIT_DB}dB — reduce category_vol or _SFX_TARGET_DB"
        )
    return linear


MAX_SFX_PER_TRANSITION_RATIO = 0.6   # не более 60% переходов получают SFX
SFX_SYNC_TOLERANCE           = 0.05  # секунд — максимальный допустимый десинк SFX↔переход
SFX_MAX_SHIFT                = 0.10  # секунд — максимальный сдвиг (100ms, выше уже слышно)
SFX_DROP_IF_MORE             = 0.25  # секунд — дропаем если до ближайшего перехода > 250ms
SFX_MIN_TRANSITION_DUR       = 0.30  # секунд — короткие переходы без звука (< 300ms)

# Приоритет категорий для прунинга: меньше = важнее
_SFX_PRIORITY: dict[str, int] = {
    "boom":        1,
    "riser":       2,
    "impact":      3,
    "glitch":      4,
    "downlifter":  5,
    "whoosh_big":  6,
    "whoosh":      7,
    "whoosh_fast": 8,
}

# Категории, которые намеренно смещены от перехода (не проверяем sync)
_SFX_SYNC_EXEMPT = {"riser", "downlifter"}


def _prune_sfx_by_ratio(
    events:           list[dict],
    transition_times: list[float],
) -> list[dict]:
    """
    Оставить SFX только для MAX_SFX_PER_TRANSITION_RATIO переходов.
    Приоритет: boom/riser > impact > glitch > whoosh.
    Лишние события дропаются с логом в pipeline_validator.
    """
    if not transition_times or not events:
        return events
    max_sfx = max(1, int(len(transition_times) * MAX_SFX_PER_TRANSITION_RATIO))
    if len(events) <= max_sfx:
        return events

    def _prio(ev: dict) -> int:
        cat = Path(ev["file"]).parent.name if ev.get("file") else "z"
        return _SFX_PRIORITY.get(cat, 99)

    sorted_ev = sorted(events, key=_prio)
    kept       = sorted_ev[:max_sfx]
    dropped    = sorted_ev[max_sfx:]

    try:
        from pipeline_validator import log_sfx_drop, get_stats
        for ev in dropped:
            fname = Path(ev["file"]).name if ev.get("file") else "?"
            log_sfx_drop(
                ev["time"], fname,
                f"ratio_pruning: limit={max_sfx}/{len(transition_times)} "
                f"({MAX_SFX_PER_TRANSITION_RATIO:.0%} of transitions)",
            )
        get_stats().sfx_within_limits = True  # pruning enforced
    except ImportError:
        pass

    kept.sort(key=lambda e: e["time"])
    return kept


def _enforce_sfx_sync(
    events:            list[dict],
    transition_times:  list[float],
    transition_durs:   list[float] | None = None,
) -> list[dict]:
    """
    Гарантировать профессиональный sync SFX↔переход:
    - delta <= SFX_SYNC_TOLERANCE (50ms) → OK без изменений
    - delta <= SFX_MAX_SHIFT (100ms)     → сдвиг на переход (человек не слышит)
    - delta <= SFX_DROP_IF_MORE (250ms)  → дроп (слышно, не сдвигаем)
    - delta > SFX_DROP_IF_MORE           → дроп
    - transition.dur < SFX_MIN_TRANSITION_DUR → дроп (короткие переходы без звука)
    Exempt: riser, downlifter (намеренно смещены от момента перехода).
    """
    if not transition_times:
        return events

    try:
        from pipeline_validator import log_sfx_drop, log_sfx_shift
        _have_validator = True
    except ImportError:
        _have_validator = False

    # Индексируем длительности переходов по времени
    _dur_map: dict[float, float] = {}
    if transition_durs and len(transition_durs) == len(transition_times):
        _dur_map = dict(zip(transition_times, transition_durs))

    result: list[dict] = []
    for ev in events:
        cat   = Path(ev["file"]).parent.name if ev.get("file") else ""
        fname = Path(ev["file"]).name        if ev.get("file") else "?"

        if cat in _SFX_SYNC_EXEMPT:
            result.append(ev)
            continue

        t       = ev["time"]
        nearest = min(transition_times, key=lambda x: abs(x - t))
        delta   = abs(nearest - t)

        # Проверяем длительность перехода — короткие без SFX
        trans_dur = _dur_map.get(nearest, 1.0)
        if trans_dur < SFX_MIN_TRANSITION_DUR:
            if _have_validator:
                log_sfx_drop(t, fname,
                             f"short_transition: dur={trans_dur:.3f}s < {SFX_MIN_TRANSITION_DUR}s")
            continue

        if delta <= SFX_SYNC_TOLERANCE:
            result.append(ev)
        elif delta <= SFX_MAX_SHIFT:
            result.append({**ev, "time": nearest})
            if _have_validator:
                log_sfx_shift(t, nearest, fname)
        else:
            if _have_validator:
                log_sfx_drop(t, fname,
                             f"desync: delta={delta:.3f}s > max_shift={SFX_MAX_SHIFT}s")

    return result


def _validate_sfx_density(events: list[dict], transition_times: list[float] | None = None) -> None:
    """Бросает ValueError если плотность SFX превышает MAX_SFX_PER_MIN после прунинга."""
    if not events:
        return
    max_t = max(e["time"] for e in events)
    n_min = max(1.0, max_t / 60.0)
    rate  = len(events) / n_min
    if rate > MAX_SFX_PER_MIN:
        try:
            from pipeline_validator import get_stats
            get_stats().sfx_within_limits = False
        except ImportError:
            pass
        raise ValueError(
            f"SFX density {rate:.1f} events/min exceeds MAX_SFX_PER_MIN={MAX_SFX_PER_MIN} "
            f"({len(events)} events over {max_t:.0f}s) — increase MAX_SFX_PER_MIN or reduce events"
        )


# ─── Построение событий SFX ──────────────────────────────────────────────────

_SFX_START_BUFFER = 3.0  # секунд после окончания интро до первого SFX


def _build_sfx_events(
    transition_times:  list[float],
    sfx_enabled:       bool,
    whoosh_chance:     float,
    channel_id:        str,
    transition_types:  list[str] | None = None,
    intro_end_time:    float | None     = None,
    riser_offset_s:    float            = 0.8,
) -> list[dict]:
    """
    Построить список событий SFX: [{time, file, vol}, ...].

    transition_times : временные метки переходов (секунды, абсолютные)
    transition_types : тип каждого перехода (parallel с transition_times)
    intro_end_time   : время конца интро — добавляет downlifter
    riser_offset_s   : за сколько секунд ДО перехода ставить riser (default 0.8)
    """
    if not sfx_enabled:
        return []
    if not any([_SFX_WHOOSH_ALL, _SFX_IMPACT, _SFX_GLITCH]):
        return []

    # SFX не раньше intro_end_time + buffer
    sfx_min_t = (intro_end_time + _SFX_START_BUFFER) if intro_end_time else 0.0

    # Контекстные whoosh-шансы и пулы по типу перехода
    _TYPE_CFG: dict[str, dict] = {
        "smooth_zoom":  {"chance": 0.60, "pools": [_SFX_WHOOSH_FAST]},
        "whip_pan":     {"chance": 0.85, "pools": [_SFX_WHOOSH_BIG]},
        "zoom_blur":    {"chance": 0.75, "pools": [_SFX_WHOOSH_BIG]},
        "cross_zoom":   {"chance": 0.40, "pools": [_SFX_WHOOSH_FAST]},
        "crossfade":    {"chance": 0.0,  "pools": []},   # no whoosh under opacity crossfade
        "glitch_flash": {"chance": 0.0,  "pools": []},   # glitch only — no whoosh fallback
        "gray_wipe":    {"chance": 0.55, "pools": [_SFX_WHOOSH_FAST]},
        "fadewhite":    None,   # flash-frame: no SFX
        "fadeblack":    None,
        "fade":         None,
    }

    events: list[dict] = []

    for i, t in enumerate(transition_times):
        # Пропускаем SFX до sfx_min_t (buffer после интро)
        if t < sfx_min_t:
            continue

        tr_type  = (transition_types[i] if transition_types and i < len(transition_types)
                    else "cross_zoom")
        cfg      = _TYPE_CFG.get(tr_type)
        is_10th  = (i % 10 == 9)   # каждый 10-й → boom + riser

        # ── Flash-frame: нет SFX ──────────────────────────────────────────────
        if cfg is None:
            continue

        # ── Glitch ────────────────────────────────────────────────────────────
        if tr_type == "glitch_flash" and _SFX_GLITCH:
            events.append({"time": t, "file": _pick(_SFX_GLITCH), "vol": _VOL_GLITCH})
            continue

        # ── Boom + Riser (каждый 10-й) ────────────────────────────────────────
        if is_10th:
            # Riser: за riser_offset_s до перехода
            riser_t = max(0.0, t - riser_offset_s)
            if _SFX_RISER and riser_t > 0:
                events.append({"time": riser_t, "file": _pick(_SFX_RISER), "vol": _VOL_RISER})

            # Boom: прямо на переходе
            if _SFX_BOOM:
                events.append({"time": t, "file": _pick(_SFX_BOOM), "vol": _VOL_BOOM})

            # Тихий whoosh под boom (50%)
            if _SFX_WHOOSH_ALL and random.random() < 0.50:
                events.append({
                    "time": t,
                    "file": _pick_best([_SFX_WHOOSH_BIG, _SFX_WHOOSH_FAST]),
                    "vol":  _VOL_WHOOSH * 0.45,
                })
            continue

        # ── Whoosh (обычный переход) ───────────────────────────────────────────
        chance = cfg["chance"] if whoosh_chance <= 0 else whoosh_chance
        if random.random() < chance:
            sfx_file = _pick_best(cfg["pools"])
            if sfx_file:
                events.append({"time": t, "file": sfx_file, "vol": _VOL_WHOOSH})

                # Delayed tail для whip_pan (30%)
                if tr_type == "whip_pan" and _SFX_WHOOSH_BIG and random.random() < 0.30:
                    events.append({
                        "time": t + 0.08,
                        "file": _pick_best([_SFX_WHOOSH_BIG]),
                        "vol":  _VOL_WHOOSH * 0.45,
                    })

    # ── Downlifter после окончания интро ─────────────────────────────────────
    if intro_end_time is not None and intro_end_time > 0 and _SFX_DOWNLIFTER:
        events.append({
            "time": intro_end_time + 0.1,
            "file": _pick(_SFX_DOWNLIFTER),
            "vol":  _VOL_DOWNLIFTER,
        })

    # Сортировка по времени
    events.sort(key=lambda e: e["time"])
    return events


# ─── Haiku SFX cues → события ────────────────────────────────────────────────

def build_haiku_sfx_events(
    segments: list[dict],
    intro_duration: float = 0.0,
    riser_offset_s: float = 0.8,
) -> list[dict]:
    """
    Конвертирует sfx_cue поля сегментов (от Haiku) в список событий SFX.

    segments: список сегментов из result_visual.json с полем sfx_cue
    intro_duration: длительность интро — события раньше него игнорируются
    riser_offset_s: riser ставится за это время ДО начала сегмента

    Возвращает [{time, file, vol}, ...]
    """
    events: list[dict] = []
    sfx_min_t = intro_duration + _SFX_START_BUFFER if intro_duration > 0 else 0.0

    for seg in segments:
        cue = seg.get("sfx_cue")
        if not cue:
            continue

        seg_start = float(seg.get("start", 0))
        if seg_start < sfx_min_t:
            continue

        if cue == "riser" or cue == "riser+boom":
            riser_t = max(sfx_min_t, seg_start - riser_offset_s)
            if _SFX_RISER:
                events.append({"time": riser_t, "file": _pick(_SFX_RISER), "vol": _VOL_RISER})

        if cue == "boom" or cue == "riser+boom":
            if _SFX_BOOM:
                events.append({"time": seg_start, "file": _pick(_SFX_BOOM), "vol": _VOL_BOOM})

        if cue == "impact":
            if _SFX_IMPACT:
                events.append({"time": seg_start, "file": _pick(_SFX_IMPACT), "vol": _VOL_IMPACT})

        if cue == "downlifter":
            if _SFX_DOWNLIFTER:
                events.append({"time": seg_start, "file": _pick(_SFX_DOWNLIFTER), "vol": _VOL_DOWNLIFTER})

    events.sort(key=lambda e: e["time"])
    print(f"[SFX] Haiku cues: {len(events)} событий из {sum(1 for s in segments if s.get('sfx_cue'))} cues", flush=True)
    return events


# ─── Инжект SFX в аудио ──────────────────────────────────────────────────────

def inject_sfx(
    audio_path:        Path,
    output_path:       Path,
    transition_times:  list[float],
    sfx_enabled:       bool,
    whoosh_chance:     float,
    channel_id:        str,
    transition_types:  list[str]  | None = None,
    intro_end_time:    float      | None = None,
    narrator_events:   list[dict] | None = None,
    transition_durs:   list[float]| None = None,  # длительности переходов для short-trans фильтра
    zone_a_end_s:      float             = 0.0,   # 0 = no zoning
    zone_b_max_per_min: float            = 0.8,   # events/min after zone_a_end_s
) -> Path:
    """
    Наложить SFX на аудио в точках переходов + контекстные нарратив-события.

    narrator_events: дополнительные [{time, file, vol}] от sfx_narrator.
    Возвращает output_path при успехе, иначе audio_path (graceful fallback).
    """
    events = _build_sfx_events(
        transition_times = transition_times,
        sfx_enabled      = sfx_enabled,
        whoosh_chance    = whoosh_chance,
        channel_id       = channel_id,
        transition_types = transition_types,
        intro_end_time   = intro_end_time,
    )

    # Добавляем нарратив-события (уже с реальными файлами)
    if narrator_events:
        events = sorted(events + narrator_events, key=lambda e: e["time"])
    if not events:
        return audio_path

    # 0. Zone-based pruning: Zone B (after zone_a_end_s) gets much sparser SFX
    if zone_a_end_s > 0:
        zone_b_max = zone_b_max_per_min / 60.0  # events per second
        zone_b_events = [e for e in events if e["time"] > zone_a_end_s]
        zone_a_events = [e for e in events if e["time"] <= zone_a_end_s]
        # Prune zone B: keep only transitions with cuts (type=="cut" handled upstream);
        # apply a hard density cap — keep at most zone_b_max events/s
        if zone_b_events and zone_b_max > 0:
            max_b = max(1, int((zone_b_events[-1]["time"] - zone_a_end_s) * zone_b_max))
            # Keep evenly spaced subset (prefer spread over first-N)
            if len(zone_b_events) > max_b:
                step = len(zone_b_events) / max_b
                zone_b_events = [zone_b_events[int(i * step)] for i in range(max_b)]
        elif zone_b_max == 0:
            zone_b_events = []
        events = sorted(zone_a_events + zone_b_events, key=lambda e: e["time"])
        print(f"  [SFX zones] A(0–{zone_a_end_s:.0f}s): {len(zone_a_events)} events | "
              f"B({zone_a_end_s:.0f}s+): {len(zone_b_events)} events", flush=True)

    # 1. Прунинг по соотношению: не более 60% переходов получают SFX
    events = _prune_sfx_by_ratio(events, transition_times)

    # 2. Синхронизация: shift ≤ 100ms, drop ≤ 250ms, короткие переходы без SFX
    events = _enforce_sfx_sync(events, transition_times, transition_durs)

    # 3. Финальная проверка плотности (после прунинга должна пройти)
    _validate_sfx_density(events, transition_times)

    # ── Строим filter_complex ─────────────────────────────────────────────────
    # [0:a] — основное аудио; [N:a] — SFX-файлы
    cmd = ["ffmpeg", "-y", "-i", str(audio_path)]
    for ev in events:
        cmd += ["-i", str(ev["file"])]

    filter_parts = ["[0:a]aformat=sample_rates=44100:channel_layouts=stereo[main]"]
    mix_inputs   = "[main]"

    for i, ev in enumerate(events, start=1):
        delay_ms  = max(0, int(ev["time"] * 1000))
        vol       = _sfx_gain(ev["file"], ev["vol"])
        label     = f"s{i}"
        dur       = _sfx_dur(ev["file"])
        fade_in   = 0.04                          # 40ms плавное появление
        fade_out  = min(0.15, dur * 0.3)          # 15% от длины, но не > 150ms
        fo_start  = max(0.0, dur - fade_out)
        filter_parts.append(
            f"[{i}:a]"
            f"aformat=sample_rates=44100:channel_layouts=stereo,"
            f"afade=t=in:st=0:d={fade_in:.3f},"
            f"afade=t=out:st={fo_start:.3f}:d={fade_out:.3f},"
            f"adelay={delay_ms}|{delay_ms},"
            f"volume={vol:.5f}"
            f"[{label}]"
        )
        mix_inputs += f"[{label}]"

    n = len(events) + 1
    filter_parts.append(
        f"{mix_inputs}amix=inputs={n}:duration=first:normalize=0[aout]"
    )

    result = subprocess.run(
        cmd + [
            "-filter_complex", ";".join(filter_parts),
            "-map", "[aout]",
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
            "-loglevel", "warning",
            str(output_path),
        ],
        capture_output=True,
    )

    if result.returncode == 0 and output_path.exists():
        _cat_counts: dict[str, int] = {}
        for ev in events:
            cat = Path(ev["file"]).parent.name
            _cat_counts[cat] = _cat_counts.get(cat, 0) + 1
        _summary = "  ".join(f"{k}×{v}" for k, v in sorted(_cat_counts.items()))
        print(f"  SFX: {len(events)} событий [{_summary}] -> {output_path.name}")
        return output_path

    print(f"  SFX inject error — используем оригинальное аудио")
    if result.stderr:
        print(f"  stderr: {result.stderr[-300:].decode('utf-8', 'replace')}")
    return audio_path


# ─── Вычисление временных меток переходов ────────────────────────────────────

def compute_transition_times(
    segments:       list[dict],
    intro_dur:      float,
    slide_duration: float,
) -> list[float]:
    """
    Вычислить абсолютные временные метки переходов в итоговом видео.
    Переход N происходит в конце сегмента N (с учётом intro offset).
    """
    times = []
    t = intro_dur
    for seg in segments:
        dur = seg.get("end", 0) - seg.get("start", 0)
        t  += dur
        times.append(t - slide_duration * 0.5)
    return times
