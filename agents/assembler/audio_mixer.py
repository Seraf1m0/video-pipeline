"""
audio_mixer.py — подготовка и микс аудио для Video Pipeline.

Основная функция (новая):
  build_final_audio(voice_path, output_path, ...)
    Единый вызов ffmpeg: voice + music (sidechaincompress) + SFX + intro + loudnorm.
    Заменяет цепочку prepare_voice_track → prepare_music_track → final_mix → inject_sfx.

Устаревшие (сохранены для совместимости):
  prepare_music_track(video_duration, output_path, music_start)
  prepare_voice_track(voice_path, output_path)
  final_mix(voice_path, music_path, output_path, ...)
"""

import math
import os
import json
import subprocess
import threading
import concurrent.futures
from pathlib import Path


# ── Утилиты ──────────────────────────────────────────────────────────────────

_dur_cache: dict[str, float] = {}
_dur_lock  = threading.Lock()


def get_audio_duration(path) -> float:
    """Получить длительность аудио/видео через ffprobe (с кэшем)."""
    key = str(path)
    with _dur_lock:
        if key in _dur_cache:
            return _dur_cache[key]
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        key,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    try:
        dur = float(json.loads(r.stdout)["format"]["duration"])
    except Exception:
        dur = 0.0
    with _dur_lock:
        _dur_cache[key] = dur
    return dur


def probe_durations_parallel(paths, max_workers: int = 8) -> None:
    """Прогреть кэш для списка файлов параллельно (fire-and-forget)."""
    uncached = [p for p in paths if str(p) not in _dur_cache]
    if not uncached:
        return
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        list(ex.map(get_audio_duration, uncached))


# Алиас для совместимости
get_duration = get_audio_duration


# ─── ФУНКЦИЯ 1: Подготовить музыкальную дорожку ──────────────────────────────

def prepare_music_track(video_duration, output_path,
                        music_start=88):
    """
    Собрать музыкальную дорожку из папки треков.
    Длина = video_duration - music_start + 10 сек запас.
    Треки берутся по очереди (не рандомно).
    Никаких loop, никаких разрывов.
    """
    music_dir = Path(os.getenv("MUSIC_DIR", ""))
    if not music_dir.exists():
        print(f"❌ MUSIC_DIR не найден: {music_dir}")
        return None

    # Найти все треки
    tracks = sorted([
        f for f in music_dir.iterdir()
        if f.suffix.lower() in [".mp3", ".wav", ".aac", ".flac"]
    ])
    if not tracks:
        print(f"❌ Треки не найдены в {music_dir}")
        return None

    needed = video_duration - music_start + 10
    print(f"  🎵 Нужно музыки: {needed:.1f}s")
    print(f"  📁 Треков в папке: {len(tracks)}")

    Path("temp").mkdir(exist_ok=True)

    # Конвертировать нужные треки в WAV и склеить
    # пока не наберём нужную длину
    selected_tracks = []
    total_dur = 0.0
    track_idx = 0
    while total_dur < needed:
        track = tracks[track_idx % len(tracks)]
        track_wav = f"temp/track_{track_idx:03d}.wav"
        subprocess.run([
            "ffmpeg", "-y", "-i", str(track),
            "-ar", "44100", "-ac", "2",
            "-c:a", "pcm_s16le",
            track_wav
        ], check=True, capture_output=True)
        dur = get_audio_duration(track_wav)
        selected_tracks.append(track_wav)
        total_dur += dur
        track_idx += 1
        print(f"  ✅ Трек {track_idx}: {track.name} "
              f"({dur:.1f}s) | Итого: {total_dur:.1f}s")

    print(f"  📊 Использовано треков: {track_idx}")

    # Склеить все треки через concat — БЕЗ разрывов
    concat_list = "temp/music_concat.txt"
    with open(concat_list, "w") as f:
        for t in selected_tracks:
            f.write(f"file '{os.path.abspath(t)}'\n")

    # Склеить в один WAV с обрезкой до нужной длины
    merged_wav = "temp/music_merged.wav"
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", concat_list,
        "-t", str(needed),
        "-ar", "44100", "-ac", "2",
        "-c:a", "pcm_s16le",
        merged_wav
    ], check=True)

    merged_dur = get_audio_duration(merged_wav)
    print(f"  ✅ Склеено: {merged_dur:.1f}s")

    # Fade in 2s / Fade out 4s
    fo_start = merged_dur - 4.0
    faded_wav = "temp/music_faded.wav"
    subprocess.run([
        "ffmpeg", "-y", "-i", merged_wav,
        "-af", (
            f"afade=t=in:st=0:d=2,"
            f"afade=t=out:st={fo_start:.2f}:d=4"
        ),
        "-ar", "44100", "-ac", "2",
        "-c:a", "pcm_s16le",
        faded_wav
    ], check=True)

    # WAV → AAC финал
    subprocess.run([
        "ffmpeg", "-y", "-i", faded_wav,
        "-c:a", "aac", "-b:a", "192k",
        "-ar", "44100",
        str(output_path)
    ], check=True)

    final_dur = get_audio_duration(str(output_path))
    print(f"✅ Музыкальная дорожка: {final_dur:.1f}s → {output_path}")
    return str(output_path)


# ─── ФУНКЦИЯ 2: Подготовить озвучку ──────────────────────────────────────────

def prepare_voice_track(voice_path, output_path):
    """
    Конвертировать озвучку в AAC 44100Hz стерео.
    Без изменений громкости.
    """
    subprocess.run([
        "ffmpeg", "-y", "-i", str(voice_path),
        "-c:a", "aac", "-b:a", "192k",
        "-ar", "44100", "-ac", "2",
        str(output_path)
    ], check=True)
    dur = get_audio_duration(str(output_path))
    print(f"✅ Озвучка: {dur:.1f}s → {output_path}")
    return str(output_path)


# ─── ФУНКЦИЯ 3: Финальный микс ───────────────────────────────────────────────

def final_mix(voice_path, music_path, output_path,
              music_start=88, music_vol=0.08,
              intro_audio_path=None, intro_vol=0.8,
              music_envelope=None):
    """
    Смикшировать все дорожки в одну.

    Параметры
    ---------
    voice_path       : подготовленная озвучка AAC
    music_path       : подготовленная музыкальная дорожка AAC (или None)
    output_path      : итоговый аудио-файл
    music_start      : задержка старта музыки от начала (сек)
    music_vol        : базовая громкость музыки (линейный коэффициент)
    intro_audio_path : аудио из интро-видео (или None)
    intro_vol        : громкость интро аудио (линейный коэффициент)
    music_envelope   : список [{time_abs, gain_db, fade_in_s, hold_s, fade_out_s}]
                       для динамического изменения громкости музыки (или None)
    """
    inputs = []
    filter_parts = []
    mix_inputs = []

    # [0] озвучка — полная с 0
    inputs += ["-i", str(voice_path)]
    filter_parts.append(
        "[0:a]volume=1.0,aresample=44100[voice]"
    )
    mix_inputs.append("[voice]")

    n_inputs = 1

    # [1] музыка — с music_start сек (если задана)
    if music_path and Path(str(music_path)).exists():
        inputs += ["-i", str(music_path)]

        # Динамическая огибающая или фиксированная громкость
        if music_envelope:
            try:
                from sfx_narrator import build_volume_expr
                vol_expr = build_volume_expr(music_envelope, base_gain=music_vol)
                vol_filter = f"volume='{vol_expr}':eval=frame"
                print(f"  [Music] Динамическая огибающая: {len(music_envelope)} точек")
            except Exception as _e:
                print(f"  [Music] Огибающая не применена ({_e}) — фиксированная громкость")
                vol_filter = f"volume={music_vol}"
        else:
            vol_filter = f"volume={music_vol}"

        filter_parts.append(
            f"[{n_inputs}:a]{vol_filter},"
            f"adelay={int(music_start * 1000)}|"
            f"{int(music_start * 1000)},"
            f"aresample=44100[music]"
        )
        mix_inputs.append("[music]")
        n_inputs += 1
    else:
        print("  ⚠️ Музыка не задана — только голос")

    # интро аудио — если есть, первые 90 сек
    if intro_audio_path and Path(intro_audio_path).exists():
        inputs += ["-i", str(intro_audio_path)]
        filter_parts.append(
            f"[{n_inputs}:a]atrim=0:90,"
            f"volume={intro_vol},"
            f"aresample=44100[intro]"
        )
        mix_inputs.append("[intro]")
        n_inputs += 1

    # amix всех дорожек
    mix_str = "".join(mix_inputs)
    filter_parts.append(
        f"{mix_str}amix=inputs={n_inputs}:"
        f"duration=longest:"
        f"normalize=0:"
        f"dropout_transition=0[out]"
    )

    filter_complex = ";".join(filter_parts)

    cmd = (
        ["ffmpeg", "-y"]
        + inputs
        + [
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-c:a", "aac", "-b:a", "192k",
            "-ar", "44100",
            str(output_path)
        ]
    )
    subprocess.run(cmd, check=True)
    dur = get_audio_duration(str(output_path))
    print(f"✅ Финальный микс: {dur:.1f}s → {output_path}")
    return str(output_path)


# ─── SFX GAIN TABLE (по категориям) ─────────────────────────────────────────
#
# Все уровни — линейный коэффициент относительно 0 dBFS.
# Целевой уровень голоса в миксе: -6 dBFS (после dynaudnorm).
# Проценты ниже — перцептивная громкость относительно голоса.
#
#   whoosh/swish:  17–20%  (-15.4 … -14.0 dB)
#   riser/boom/impact/hit: 10–15%  (-20.0 … -16.5 dB)
#   default:       10–15%

# ─── SFX loudness normalization ──────────────────────────────────────────────
# Целевой пик по категориям (откалибровано на слух):
#   riser:      -47 dBFS  — нарастание, фоновый элемент
#   boom:       -37 dBFS  — главный удар, самый слышимый
#   downlifter: -47 dBFS  — мягкий спад после бума
#   whoosh/whoosh_big/impact: -39 dBFS  — акценты и переходы
# intensity 1-10 (от Claude) → ±2.2 dB сдвиг относительно цели.

_SFX_CAT_TARGET_DB: dict = {
    "riser":      -47.0,
    "boom":       -37.0,
    "downlifter": -47.0,
    "whoosh":     -39.0,
    "whoosh_fast":-39.0,
    "whoosh_big": -39.0,
    "impact":     -39.0,
    "glitch":     -39.0,
}
_SFX_TARGET_PEAK_DB: float = -39.0   # дефолт для неизвестных категорий

# Fade-in / fade-out по категориям (curve=qsin — плавный синус)
_SFX_CAT_FADE: dict = {
    #               fade_in  fade_out
    "riser":      (0.40,    0.50),   # плавный вход, мягкий аут
    "boom":       (0.00,    0.00),   # без фейдов — бьёт сразу
    "downlifter": (0.50,    1.00),   # очень плавный вход и аут
    "whoosh":     (0.12,    0.00),
    "whoosh_fast":(0.10,    0.00),
    "whoosh_big": (0.15,    0.00),
    "impact":     (0.10,    0.00),
    "glitch":     (0.05,    0.00),
}

_sfx_peak_cache: dict[str, float] = {}


def _sfx_peak_db(path: "Path | str") -> float:
    """Пиковый уровень SFX файла в dBFS (кэшируется)."""
    import subprocess as _sp
    key = str(path)
    if key in _sfx_peak_cache:
        return _sfx_peak_cache[key]
    try:
        r = _sp.run(
            ["ffmpeg", "-i", key, "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True,
        )
        for line in r.stderr.splitlines():
            if "max_volume:" in line:
                val = float(line.split("max_volume:")[1].split("dB")[0].strip())
                _sfx_peak_cache[key] = val
                return val
    except Exception:
        pass
    _sfx_peak_cache[key] = -3.0  # safe fallback
    return -3.0


def _sfx_gain_normalized(path: "Path | str", score: float = 5.0,
                          category: str = "") -> float:
    """
    Линейный gain для SFX: нормализует файл к целевому dBFS по категории.
    score 1-10 → ±2.2 dB offset:
      score=1  → -2.2 dB (тише)
      score=5  →  0 dB  (нейтрально)
      score=10 → +2.2 dB (чуть громче)
    """
    import math as _math
    target_db     = _SFX_CAT_TARGET_DB.get(category, _SFX_TARGET_PEAK_DB)
    peak_db       = _sfx_peak_db(path)
    intensity_db  = (float(score) - 5.0) / 9.0 * 4.0   # ±2.2 dB
    gain_db       = target_db - peak_db + intensity_db
    return 10 ** (gain_db / 20.0)


# ─── НОВЫЙ ЕДИНЫЙ АУДИО ПАСС — ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ──────────────────────

def _vo_volume_for_emotion(emotion: float) -> float:
    """
    Map emotion score (1–10) → VO linear volume.
    1 → 0.55 (-5.2 dB),  10 → 1.0 (0 dB).
    """
    e = max(1.0, min(10.0, float(emotion)))
    return 0.55 + (e - 1.0) * (0.45 / 9.0)


def _sfx_volume_for_emotion(emotion: float) -> float:
    """
    Map emotion score (1–10) → SFX linear volume.
    1 → 0.20,  10 → 0.50.
    """
    e = max(1.0, min(10.0, float(emotion)))
    return 0.20 + (e - 1.0) * (0.30 / 9.0)



# ffmpeg expression parser has a recursion depth limit (~128 stack entries).
# Each nested if() consumes ~3 entries.  Safe limit ≈ 40 nesting levels.
_MAX_DUCK_PEAKS  = 12   # music duck: 12 peaks × 4 nesting levels = 48 — well within limit
_MAX_VO_KFRAMES  = 25   # VO emotion: 25 keyframes — hard cap


def _build_music_duck_expr(peak_times: list, base_lin: float) -> str:
    """
    Build ffmpeg volume= expression that ducks music at PEAK_MOMENT timestamps.

    For each peak T (absolute video time, seconds):
      [T-1.0, T-0.5]: linear ramp  base → 0.05×base  (ramp down, 0.5s)
      [T-0.5, T+0.5]: hold at 0.05×base               (silence window, 1.0s)
      [T+0.5, T+1.0]: linear ramp  0.05×base → base   (ramp up,   0.5s)

    Returns flat "{base_lin}" if no peaks (faster ffmpeg path).
    Peaks are assumed non-overlapping (minimum 2s apart).

    Caps at _MAX_DUCK_PEAKS: if more peaks are given, keeps the ones spaced
    most evenly across the timeline (first, middle, last) so ducking feels
    distributed rather than front-loaded.
    """
    if not peak_times:
        return f"{base_lin:.6f}"

    pts = sorted(peak_times)
    if len(pts) > _MAX_DUCK_PEAKS:
        # Subsample evenly: always keep first and last, then pick from middle
        step   = (len(pts) - 1) / (_MAX_DUCK_PEAKS - 1)
        pts    = [pts[round(i * step)] for i in range(_MAX_DUCK_PEAKS)]
        print(f"  [Audio] duck expr: capped {len(peak_times)} → {_MAX_DUCK_PEAKS} peaks",
              flush=True)

    min_vol = base_lin * 0.05
    base_s  = f"{base_lin:.6f}"
    min_s   = f"{min_vol:.6f}"

    def _one_peak(T: float) -> str:
        t0, t1, t2, t3 = T - 1.0, T - 0.5, T + 0.5, T + 1.0
        # ramp down: base + (min-base)*(t-t0)/0.5
        dn = f"({base_s}+({min_s}-{base_s})*(t-{t0:.3f})/0.5)"
        # ramp up:   min  + (base-min)*(t-t2)/0.5
        up = f"({min_s}+({base_s}-{min_s})*(t-{t2:.3f})/0.5)"
        return (
            f"if(between(t,{t0:.3f},{t1:.3f}),{dn},"
            f"if(between(t,{t1:.3f},{t2:.3f}),{min_s},"
            f"if(between(t,{t2:.3f},{t3:.3f}),{up},"
            f"{base_s})))"
        )

    # Chain: outermost wraps latest peak first so earliest peak is innermost
    expr = base_s
    for T in reversed(pts):
        expr = f"if(between(t,{T-1.0:.3f},{T+1.0:.3f}),{_one_peak(T)},{expr})"
    return expr


def _build_vo_emotion_expr(
    keyframes: list,   # [(start_s, end_s, vol_linear), ...]
    default: float = 1.0,
) -> str:
    """
    Build ffmpeg volume= expression for per-segment VO dynamic range.

    Each entry sets volume for a time window [start_s, end_s].
    Outside all windows: default volume (1.0 = no change).
    Returns flat default if keyframes is empty or all zero-duration.

    Caps at _MAX_VO_KFRAMES: if more segments are given, keeps the N with the
    largest deviation from default (most audible impact), then re-sorts by time.
    """
    valid = [(s, e, v) for s, e, v in (keyframes or []) if e > s + 0.01]
    if not valid:
        return f"{default:.6f}"

    if len(valid) > _MAX_VO_KFRAMES:
        # Keep segments with biggest deviation from default (most impactful)
        valid.sort(key=lambda x: abs(x[2] - default), reverse=True)
        valid = sorted(valid[:_MAX_VO_KFRAMES], key=lambda x: x[0])
        print(f"  [Audio] VO expr: capped to {_MAX_VO_KFRAMES} most-extreme keyframes",
              flush=True)

    expr = f"{default:.6f}"
    for start, end, vol in reversed(valid):
        expr = f"if(between(t,{start:.3f},{end:.3f}),{vol:.6f},{expr})"
    return expr


# ─── АУДИО ПАСС (новая архитектура) ─────────────────────────────────────────
#
# Архитектура (надёжная, без дедлоков):
#   Шаг 1: music_for_mix.wav  — тишина(intro_dur) + треки acrossfade, PCM f32le
#   Шаг 2: sfx_for_mix.wav   — SFX-only amix с adelay, PCM f32le
#   Шаг 3: intro_audio.wav   — аудио из intro видео, PCM f32le
#   Шаг 4: bg_for_mix.wav    — amix(music+intro+sfx) с громкостями, PCM f32le
#   Шаг 5: финальный 2-input amix(voice + bg) → AAC 320k (один encode)
#
# Правило: амикс с 2 входами (voice+bg) — никогда не дедлочит.
# Все задержки запечены в WAV-файлы, никаких adelay в финальном amix.

def build_final_audio(
    voice_path,
    output_path,
    sfx_events=None,
    music_start_sec:      float = 88.0,
    music_vol_db:         float = -22.0,
    intro_audio_path=None,
    intro_trim_s:         float = 90.0,
    intro_vol_db:         float = -20.0,
    video_duration:       float = 0.0,
    lufs_target:          float = -16.0,   # LUFS таргет: -16 YouTube, -18 спокойный
    peak_moment_times=None,      # сохранён для совместимости API, не используется
    vo_emotion_keyframes=None,   # сохранён для совместимости API, не используется
) -> str:
    """
    Надёжный аудио микс без дедлоков.

    Архитектура (5 шагов):
      1. music_for_mix.wav  — тишина(music_start_sec) + треки acrossfade, PCM f32le
      2. sfx_for_mix.wav   — SFX-only amix с adelay, PCM f32le (нет дедлока)
      3. intro_audio.wav   — аудио из intro видео, PCM f32le
      4. bg_for_mix.wav    — amix(music+intro+sfx) с громкостями, PCM f32le
      5. финальный 2-input amix(voice + bg) → AAC 320k (один encode, нет дедлока)

    Правило надёжности: финальный amix всегда 2-входовой (voice + bg).
    Все задержки/офсеты запечены в WAV на шагах 1-4, никаких adelay в финале.
    """
    sfx_events = sfx_events or []
    music_dir  = Path(os.getenv("MUSIC_DIR", ""))
    temp_dir   = Path(str(output_path)).parent
    _wavs      : list[Path] = []   # temp files → удалить после микса
    voice_dur  = get_audio_duration(str(voice_path))

    def _run(cmd, label="ffmpeg"):
        r = subprocess.run(cmd, capture_output=True)
        if r.returncode != 0:
            err = r.stderr.decode(errors='replace')
            # Показываем последние 600 символов (там реальная ошибка, не шапка ffmpeg)
            tail = err[-600:] if len(err) > 600 else err
            print(f"  [Audio] {label} failed:\n{tail}", flush=True)
        return r.returncode == 0

    # ── Шаги 1-3 параллельно: music / SFX / intro независимы ────────────────

    def _build_music_wav() -> "Path | None":
        """Шаг 1: music_for_mix.wav — тишина + треки acrossfade + fade."""
        if not (music_vol_db > -60 and music_dir.exists() and video_duration > 0):
            return None
        all_tracks = sorted([
            f for f in music_dir.iterdir()
            if f.suffix.lower() in (".mp3", ".wav", ".aac", ".flac")
        ])
        if not all_tracks:
            return None
        needed_dur = max(0.0, video_duration - music_start_sec + 10.0)
        if needed_dur <= 0.0:
            return None
        probe_durations_parallel(all_tracks)
        total, idx, tracks = 0.0, 0, []
        while total < needed_dur:
            t = all_tracks[idx % len(all_tracks)]
            tracks.append(t)
            total += get_audio_duration(str(t))
            idx   += 1
            if idx > len(all_tracks) * 3:
                break
        if not tracks:
            return None
        print(f"  [Audio] music: {len(tracks)} tracks ~{total:.0f}s", flush=True)

        out = temp_dir / "_music_for_mix.wav"
        n   = len(tracks)
        cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
        for t in tracks:
            cmd += ["-i", str(t)]
        fc = [f"[0:a]atrim=0:{music_start_sec:.3f}[sil]"]
        for j in range(n):
            # silenceremove на каждом треке: убираем только тихие интро/аутро
            # (-55dB порог — только цифровая тишина, не музыкальные паузы).
            # start_silence=0.1 — оставляем 0.1s в начале для естественного старта.
            fc.append(
                f"[{j+1}:a]aresample=44100,aformat=channel_layouts=stereo,"
                "silenceremove="
                "start_periods=1:start_duration=0.1:start_threshold=-55dB:"
                "start_silence=0.1:"
                "stop_periods=1:stop_duration=0.3:stop_threshold=-55dB:"
                "stop_silence=0.1"
                f"[mt{j}]"
            )
        if n == 1:
            fc.append("[mt0]anull[mcat]")
        elif n == 2:
            fc.append("[mt0][mt1]acrossfade=d=3:c1=tri:c2=tri[mcat]")
        else:
            prev = "mt0"
            for j in range(1, n):
                nxt = "mcat" if j == n - 1 else f"cf{j}"
                fc.append(f"[{prev}][mt{j}]acrossfade=d=3:c1=tri:c2=tri[{nxt}]")
                prev = nxt
        fo = max(0.0, needed_dur - 4.0)
        fc.append(
            f"[mcat]atrim=0:{needed_dur:.2f},"
            f"afade=t=in:st=0:d=2,afade=t=out:st={fo:.2f}:d=4[mtr]"
        )
        fc.append("[sil][mtr]concat=n=2:v=0:a=1[out]")
        cmd += ["-filter_complex", ";".join(fc),
                "-map", "[out]",
                "-t", f"{music_start_sec + needed_dur:.2f}",
                "-c:a", "pcm_f32le", "-ar", "44100", str(out)]
        return out if _run(cmd, "music WAV") else None

    def _build_sfx_wav() -> "Path | None":
        """Шаг 2: sfx_for_mix.wav — все SFX с adelay и gain."""
        if not sfx_events:
            return None
        probe_durations_parallel([ev["file"] for ev in sfx_events])
        out = temp_dir / "_sfx_for_mix.wav"
        cmd = ["ffmpeg", "-y"]
        for ev in sfx_events:
            cmd += ["-i", str(ev["file"])]
        fc = []
        for k, ev in enumerate(sfx_events):
            delay_ms = max(0, int(float(ev.get("time_s", ev.get("time", 0))) * 1000))
            cat     = str(ev.get("category", ""))
            score   = float(ev.get("score", 5.0))
            gain    = _sfx_gain_normalized(ev["file"], score, category=cat)
            sfx_dur = get_audio_duration(str(ev["file"]))
            fi_d, fo_d = _SFX_CAT_FADE.get(cat, (0.12, 0.00))
            # Строим цепочку фейдов только если ненулевые
            fade_chain = "aresample=44100,aformat=channel_layouts=stereo"
            if fi_d > 0:
                fade_chain += f",afade=t=in:st=0:d={fi_d:.3f}:curve=qsin"
            if fo_d > 0:
                fo_st = max(0.0, sfx_dur - fo_d)
                fade_chain += f",afade=t=out:st={fo_st:.3f}:d={fo_d:.3f}:curve=qsin"
            fc.append(
                f"[{k}:a]{fade_chain},"
                f"adelay={delay_ms}|{delay_ms},volume={gain:.6f}[s{k}]"
            )
        ins = "".join(f"[s{k}]" for k in range(len(sfx_events)))
        fc.append(f"{ins}amix=inputs={len(sfx_events)}:"
                  f"duration=longest:normalize=0:dropout_transition=0[out]")
        cmd += ["-filter_complex", ";".join(fc),
                "-map", "[out]", "-c:a", "pcm_f32le", "-ar", "44100", str(out)]
        return out if _run(cmd, "SFX WAV") else None

    def _build_intro_wav() -> "Path | None":
        """
        Шаг 3: intro_audio.wav — аудио из интро-видео.

        Нормализует интро к единому пику перед применением громкости в миксе.
        Это гарантирует что intro_audio_vol_db даёт предсказуемый уровень:
          тихий исходник → поднимается до нормы
          громкий исходник → остаётся на норме
        Без нормализации -34 dB от тихого файла = почти тишина.
        """
        if not (intro_audio_path and Path(str(intro_audio_path)).exists()):
            return None
        out = temp_dir / "_intro_audio.wav"
        ok  = _run([
            "ffmpeg", "-y", "-i", str(intro_audio_path),
            "-vn", "-t", f"{intro_trim_s:.2f}",
            # dynaudnorm: нормализуем пик к -1 dBFS (p=0.891 ≈ -1dBFS)
            # Теперь intro_audio_vol_db всегда отсчитывается от -1 dBFS
            "-af", "dynaudnorm=f=500:g=31:p=0.891:m=30",
            "-c:a", "pcm_f32le", "-ar", "44100", str(out),
        ], "intro WAV (normalized)")
        return out if ok else None

    # Запускаем все три задачи параллельно
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as _ex:
        _fut_music = _ex.submit(_build_music_wav)
        _fut_sfx   = _ex.submit(_build_sfx_wav)
        _fut_intro = _ex.submit(_build_intro_wav)
        music_wav  = _fut_music.result()
        sfx_wav    = _fut_sfx.result()
        intro_wav  = _fut_intro.result()

    for _w in (music_wav, sfx_wav, intro_wav):
        if _w:
            _wavs.append(_w)

    # ── Шаг 4a: voice_proc.wav — голосовой процессинг ────────────────────────
    # Предварительное декодирование в PCM: некоторые MP3-файлы (особенно с большим
    # timebase, например 14.112 MHz у DE) создают нестабильные DTS при прямой
    # подаче в dynaudnorm=f=4000:g=31. Это приводит к "Non-monotonic DTS" и
    # обрезке выхода до ~25% от реальной длины. Решение: декодировать MP3 в PCM
    # WAV (с sample-accurate timestamps) ДО применения filter complex.
    voice_raw_wav  = temp_dir / "_voice_raw.wav"
    _wavs.append(voice_raw_wav)
    _run([
        "ffmpeg", "-y", "-i", str(voice_path),
        "-c:a", "pcm_f32le", "-ar", "44100",
        "-loglevel", "warning",
        str(voice_raw_wav),
    ], "voice pre-decode PCM")
    # Если pre-decode упал — работаем с оригиналом (FR, ES обычно не требуют)
    _voice_input = voice_raw_wav if voice_raw_wav.exists() else voice_path

    # Stage 1: EQ         — HP 80Hz, warmth +1dB@150Hz, mud -3dB@200Hz,
    #                        presence +3dB@3kHz, air +2dB@8kHz, shelf +1dB@12kHz
    # Stage 2: dynaudnorm — мягкое выравнивание к -6dBFS (f=4000ms — очень
    #                        медленное окно, без pumping и warmup-артефакта)
    # Stage 3: acompressor— прозрачная компрессия 2.5:1, attack 20ms
    # Stage 4: alimiter   — потолок -3 dBFS
    # Stage 5: de-esser   — sidechaincompress HP>7kHz
    # Stage 6: room reverb— ранние отражения 80ms+150ms, 6% wet
    #                        (выше 40ms Haas-порога → нет удвоения, звучит как студия)
    voice_proc_wav = temp_dir / "_voice_proc.wav"
    _wavs.append(voice_proc_wav)

    _voice_fc = (
        # Stage 1: EQ
        "[0:a]"
        "highpass=f=80,"
        "equalizer=f=150:width_type=o:width=1.2:g=1,"
        "equalizer=f=200:width_type=o:width=1.0:g=-3,"
        "equalizer=f=3000:width_type=o:width=1.4:g=3,"
        "equalizer=f=8000:width_type=o:width=1.0:g=2,"
        "equalizer=f=12000:width_type=o:width=0.8:g=1,"
        # Stage 2: Мягкое выравнивание уровня к -6dBFS
        #   f=4000ms — 4-секундное окно, gain меняется очень медленно (не слышно)
        #   g=31     — максимальное Gaussian сглаживание
        #   p=0.501  — target peak -6dBFS (0.501 = 10^(-6/20))
        #   m=10     — макс. буст +10dB (не перегоняет тихие паузы)
        "dynaudnorm=f=4000:g=31:p=0.501:m=10,"
        # Stage 3: Прозрачная компрессия
        "acompressor=threshold=0.126:ratio=2.5:attack=20:release=200:"
        "knee=2.828:makeup=1.2,"
        # Stage 4: Hard ceiling -3 dBFS
        "alimiter=limit=0.708:attack=5:release=20:level=false,"
        # Stage 5: De-esser
        "asplit=2[main][sc_src];"
        "[sc_src]highpass=f=7000[sc_hf];"
        "[main][sc_hf]sidechaincompress="
        "threshold=0.025:ratio=3:attack=1:release=60:"
        "level_sc=2.5[deessed];"
        # Stage 6: Room reverb — студийное помещение, 6% wet
        #   80ms + 150ms отражения (> 40ms Haas-порог → нет удвоения)
        #   decay 0.08/0.04 — короткое затухание (маленькая комната, не зал)
        "[deessed]asplit=2[dry][wet_in];"
        "[wet_in]aecho=0.8:0.88:80:0.08[r1];"
        "[r1]aecho=0.9:0.88:150:0.04[room];"
        "[dry][room]amix=inputs=2:weights=0.94 0.06[voice_out]"
    )

    voice_proc_ok = _run([
        "ffmpeg", "-y", "-i", str(_voice_input),
        "-filter_complex", _voice_fc,
        "-map", "[voice_out]",
        "-c:a", "pcm_f32le", "-ar", "44100",
        str(voice_proc_wav),
    ], "voice gate+EQ+mcomp+deess")
    # Если EQ/comp упал — используем исходный голос
    _voice_for_mix = voice_proc_wav if voice_proc_ok and voice_proc_wav.exists() else voice_path

    # ── Шаг 4b: music_sc.wav — sidechain ducking музыки от голоса ───────────
    # acompressor с sidechain: голос = детектор, музыка = сигнал
    # Когда голос активен → музыка duck до -8dB (ratio=4, threshold=-30dB)
    # attack=10ms (быстро реагирует), release=300ms (плавно возвращается)
    music_sc_wav = None
    if music_wav and music_wav.exists():
        music_sc_wav = temp_dir / "_music_sc.wav"
        _wavs.append(music_sc_wav)
        sc_ok = _run([
            "ffmpeg", "-y",
            "-i", str(music_wav),           # [0] музыка (сигнал)
            "-i", str(_voice_for_mix),       # [1] голос (sidechain детектор)
            "-filter_complex",
            # sidechaincompress: AA->A (музыка + голос → duck музыки)
            # EQ notch на музыке: -4dB @ 300Hz (фундаментал голоса) + -3dB @ 2500Hz (разборчивость)
            # Создаёт spectral space для голоса без агрессивного level ducking
            f"[0:a]aresample=44100,aformat=channel_layouts=stereo,volume={music_vol_db}dB,"
            # Срез низов музыки: освобождает пространство для голоса (особенно на наушниках)
            "highpass=f=120:poles=2,"
            # Notch EQ: убираем частоты где голос наиболее активен
            "equalizer=f=300:width_type=o:width=1.5:g=-4,"
            "equalizer=f=2500:width_type=o:width=1.2:g=-3[music_in];"
            "[1:a]aresample=44100,aformat=channel_layouts=stereo[sc];"
            # Sidechain ducking: attack=20ms (плавнее), release=500ms (мягче возврат)
            "[music_in][sc]sidechaincompress="
            "threshold=0.03:ratio=4:attack=20:release=500:"
            "makeup=1:mix=0.9[music_out]",
            "-map", "[music_out]",
            "-c:a", "pcm_f32le", "-ar", "44100", str(music_sc_wav),
        ], "music sidechain")
        if not sc_ok:
            # Fallback: просто применяем volume без sidechain
            music_sc_wav = None

    # ── Шаг 4c: bg_for_mix.wav — финальный bg микс ───────────────────────────
    bg_wav = None
    bg = []   # (path, vol_db_or_None_if_already_applied)
    if music_sc_wav:
        bg.append((music_sc_wav, 0.0))     # громкость уже применена в sidechain шаге
    elif music_wav:
        bg.append((music_wav, music_vol_db))  # fallback без sidechain
    if intro_wav:
        bg.append((intro_wav, intro_vol_db))
    if sfx_wav:
        bg.append((sfx_wav, 0.0))

    if bg:
        bg_wav = temp_dir / "_bg_for_mix.wav"
        _wavs.append(bg_wav)
        if len(bg) == 1:
            path, db = bg[0]
            vf = f"volume={db}dB" if db != 0.0 else "anull"
            _run(["ffmpeg", "-y", "-i", str(path),
                  "-af", vf,
                  "-c:a", "pcm_f32le", "-ar", "44100", str(bg_wav)], "bg WAV")
        else:
            cmd = ["ffmpeg", "-y"]
            for path, _ in bg:
                cmd += ["-i", str(path)]
            fc, lbls = [], []
            for i, (_, db) in enumerate(bg):
                vf = f"volume={db}dB" if db != 0.0 else "anull"
                fc.append(f"[{i}:a]aresample=44100,aformat=channel_layouts=stereo,{vf}[b{i}]")
                lbls.append(f"[b{i}]")
            n = len(bg)
            fc.append(f"{''.join(lbls)}amix=inputs={n}:"
                      f"duration=longest:normalize=0:dropout_transition=0[out]")
            cmd += ["-filter_complex", ";".join(fc),
                    "-map", "[out]", "-c:a", "pcm_f32le", "-ar", "44100", str(bg_wav)]
            if not _run(cmd, "bg WAV"):
                bg_wav = None

    # ── Шаг 5: финальный микс → loudnorm (2 прохода) → AAC 320k ─────────────
    print(f"  [Audio] final mix: voice[EQ+mcomp+deess+reverb]"
          + (f" + music[sidechain]" if music_sc_wav else " + music" if music_wav else "")
          + (f" + intro" if intro_wav else "")
          + (f" + {len(sfx_events)}xSFX" if sfx_wav else ""),
          flush=True)

    # Собираем raw mix во временный WAV перед loudnorm
    raw_mix_wav = temp_dir / "_raw_mix.wav"
    _wavs.append(raw_mix_wav)

    if bg_wav and bg_wav.exists():
        _run([
            "ffmpeg", "-y",
            "-i", str(_voice_for_mix),
            "-i", str(bg_wav),
            "-filter_complex",
            "[0:a]aresample=44100,aformat=channel_layouts=stereo[v];"
            "[1:a]aresample=44100,aformat=channel_layouts=stereo[b];"
            "[v][b]amix=inputs=2:duration=first:normalize=0:dropout_transition=0[out]",
            "-map", "[out]",
            "-t", f"{voice_dur:.3f}",
            "-c:a", "pcm_f32le", "-ar", "44100",
            "-loglevel", "warning",
            str(raw_mix_wav),
        ], "raw mix WAV")
    else:
        _run(["ffmpeg", "-y", "-i", str(_voice_for_mix),
              "-c:a", "pcm_f32le", "-ar", "44100",
              "-loglevel", "warning", str(raw_mix_wav)], "voice-only WAV")

    # Loudnorm двумя проходами:
    # Проход 1: измеряем реальные LUFS / LRA / TP
    # Проход 2: применяем точную коррекцию с measured_ параметрами
    _lufs_target = lufs_target  # из параметра функции
    _measured = {}
    if raw_mix_wav.exists():
        r_probe = subprocess.run([
            "ffmpeg", "-y", "-i", str(raw_mix_wav),
            "-af", f"loudnorm=I={_lufs_target}:TP=-1.5:LRA=11:print_format=json",
            "-f", "null", "-",
        ], capture_output=True, text=True)
        # loudnorm пишет JSON в stderr
        stderr = r_probe.stderr
        try:
            j_start = stderr.rfind("{")
            j_end   = stderr.rfind("}") + 1
            if j_start >= 0 and j_end > j_start:
                ln_data = json.loads(stderr[j_start:j_end])
                _measured = {
                    "input_i":   ln_data.get("input_i",   "-99"),
                    "input_tp":  ln_data.get("input_tp",  "-99"),
                    "input_lra": ln_data.get("input_lra", "0"),
                    "input_thresh": ln_data.get("input_thresh", "-99"),
                }
                print(f"  [Audio] loudnorm pass1: I={_measured['input_i']} LUFS  "
                      f"TP={_measured['input_tp']}  LRA={_measured['input_lra']}",
                      flush=True)
        except Exception as _e:
            print(f"  [Audio] loudnorm pass1 parse error: {_e}", flush=True)

    # Проход 2: применяем loudnorm + alimiter
    if raw_mix_wav.exists():
        if _measured:
            ln_filter = (
                f"loudnorm=I={_lufs_target}:TP=-1.5:LRA=11:linear=true:"
                f"measured_I={_measured['input_i']}:"
                f"measured_TP={_measured['input_tp']}:"
                f"measured_LRA={_measured['input_lra']}:"
                f"measured_thresh={_measured['input_thresh']},"
                f"alimiter=limit=0.891:attack=5:release=50:level=false"
            )
        else:
            # Fallback: single-pass dynamic loudnorm
            ln_filter = (
                f"loudnorm=I={_lufs_target}:TP=-1.5:LRA=11:linear=false,"
                f"alimiter=limit=0.891:attack=5:release=50:level=false"
            )
        _run([
            "ffmpeg", "-y", "-i", str(raw_mix_wav),
            "-af", ln_filter,
            "-c:a", "aac", "-b:a", "320k", "-ar", "44100",
            "-loglevel", "warning",
            str(output_path),
        ], f"loudnorm pass2 → AAC")
    else:
        # raw mix не создался — используем исходный голос напрямую
        _run(["ffmpeg", "-y", "-i", str(_voice_for_mix),
              "-c:a", "aac", "-b:a", "320k", "-ar", "44100",
              "-loglevel", "warning", str(output_path)], "voice-only fallback")

    # Удалить temp WAV файлы
    for w in _wavs:
        try:
            w.unlink(missing_ok=True)
        except Exception:
            pass

    out_dur = get_audio_duration(str(output_path)) if Path(str(output_path)).exists() else 0.0
    print(f"  [Audio] done: {out_dur:.1f}s -> {output_path}", flush=True)
    return str(output_path)
