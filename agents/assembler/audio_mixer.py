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
from pathlib import Path


# ── Утилиты ──────────────────────────────────────────────────────────────────

def get_audio_duration(path) -> float:
    """Получить длительность аудио/видео через ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        str(path)
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return float(json.loads(r.stdout)["format"]["duration"])
    except Exception:
        return 0.0


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


# ─── НОВЫЙ ЕДИНЫЙ АУДИО ПАСС ─────────────────────────────────────────────────

def build_final_audio(
    voice_path,
    output_path,
    sfx_events=None,
    music_start_sec:      float = 88.0,
    music_vol_db:         float = -18.0,
    intro_audio_path=None,
    intro_trim_s:         float = 90.0,
    intro_vol_db:         float = -6.0,
    video_duration:       float = 0.0,
    peak_moment_times=None,      # list[float] | None — timestamps for music duck
    vo_emotion_keyframes=None,   # list[tuple(start_s,end_s,emotion_1_10)] | None
) -> str:
    """
    Единый FFmpeg вызов:  voice + music (sidechaincompress) + SFX + intro + loudnorm.

    Заменяет цепочку:
      prepare_voice_track → prepare_music_track → final_mix → inject_sfx
    Время сборки: ~15–25s вместо ~45–60s (нет промежуточных AAC файлов).

    Новые параметры
    ---------------
    peak_moment_times     : абсолютные времена пиков (секунды) для duck музыки.
                            На каждом пике музыка рампируется до 5% за 0.5s,
                            держится 1s и восстанавливается за 0.5s.
    vo_emotion_keyframes  : [(start_s, end_s, emotion_1_10), ...] — per-scene emotion.
                            emotion 1→ -5.2dB, emotion 10→ 0dB (LRA 8-12 LU target).

    Parameters
    ----------
    voice_path       : MP3/AAC озвучки (вход)
    output_path      : итоговый AAC файл
    sfx_events       : [{time_s, file, gain}] — gain = готовый линейный коэффициент
    music_start_sec  : задержка старта музыки от начала видео (сек)
    music_vol_db     : базовая громкость музыки (dB), напр. -18.0
    intro_audio_path : аудио из intro.mp4 (или None)
    intro_trim_s     : обрезать интро аудио до этой длины
    intro_vol_db     : громкость интро аудио (dB), напр. -6.0
    video_duration   : полная длительность видео (нужна для расчёта длины музыки)

    SFX events format
    -----------------
    Каждое событие: {"time_s": float, "file": str|Path, "gain": float}
    - time_s : абсолютное время в секундах
    - file   : путь к WAV/MP3
    - gain   : линейный коэффициент громкости (уже нормализованный, напр. 0.18)
    """
    sfx_events = sfx_events or []
    music_dir = Path(os.getenv("MUSIC_DIR", ""))

    # ── Собрать список музыкальных треков ─────────────────────────────────────
    music_tracks: list[Path] = []
    if music_dir.exists() and video_duration > 0:
        all_tracks = sorted([
            f for f in music_dir.iterdir()
            if f.suffix.lower() in (".mp3", ".wav", ".aac", ".flac")
        ])
        if all_tracks:
            needed_dur = max(0.0, video_duration - music_start_sec + 10.0)
            total = 0.0
            idx   = 0
            while total < needed_dur:
                track = all_tracks[idx % len(all_tracks)]
                music_tracks.append(track)
                total += get_audio_duration(str(track))
                idx   += 1
                if idx > len(all_tracks) * 3:
                    break  # safety: never loop more than 3× the library
            print(f"  🎵 Музыка: {len(music_tracks)} треков, ~{total:.0f}s", flush=True)

    # ── Собрать команду ffmpeg ────────────────────────────────────────────────
    cmd = ["ffmpeg", "-y"]

    # [0] голос
    cmd += ["-i", str(voice_path)]

    # [1..M] музыкальные треки
    music_input_start = 1
    for t in music_tracks:
        cmd += ["-i", str(t)]

    # [M+1..M+N] SFX файлы
    sfx_input_start = music_input_start + len(music_tracks)
    for ev in sfx_events:
        cmd += ["-i", str(ev["file"])]

    # [M+N+1] интро аудио (опционально)
    intro_input_idx = None
    if intro_audio_path and Path(str(intro_audio_path)).exists():
        intro_input_idx = sfx_input_start + len(sfx_events)
        cmd += ["-i", str(intro_audio_path)]

    # ── Предварительные вычисления для динамики ──────────────────────────────
    # VO emotion expression (per-scene volume)
    _vo_kf_linear = [
        (float(s), float(e), _vo_volume_for_emotion(float(em)))
        for s, e, em in (vo_emotion_keyframes or [])
    ]
    _vo_expr    = _build_vo_emotion_expr(_vo_kf_linear, default=1.0)
    _has_vo_dyn = (_vo_expr != "1.000000")

    # Music peak-moment duck expression
    _safe_peaks  = [float(t) for t in (peak_moment_times or []) if float(t) > 0]
    _peak_count  = len(_safe_peaks)

    # Notify pipeline_validator (for QA checks)
    if _peak_count > 0:
        try:
            from pipeline_validator import _emit as _pv_emit
            _pv_emit("audio_music_peaks", count=_peak_count,
                     times=[round(t, 2) for t in _safe_peaks])
        except Exception:
            pass

    # ── filter_complex ────────────────────────────────────────────────────────
    fc: list[str] = []

    # [0] Voice → resample → (optional emotion volume) → split for sidechain
    if music_tracks:
        if _has_vo_dyn:
            # Apply emotion volume BEFORE split so sidechain also has natural level
            fc.append(
                f"[0:a]aresample=44100,aformat=channel_layouts=stereo,"
                f"volume='{_vo_expr}':eval=frame,"
                f"asplit=2[voice][voice_sc]"
            )
        else:
            fc.append(
                "[0:a]aresample=44100,aformat=channel_layouts=stereo,"
                "asplit=2[voice][voice_sc]"
            )
    else:
        if _has_vo_dyn:
            fc.append(
                f"[0:a]aresample=44100,aformat=channel_layouts=stereo,"
                f"volume='{_vo_expr}':eval=frame[voice]"
            )
        else:
            fc.append("[0:a]aresample=44100,aformat=channel_layouts=stereo[voice]")

    # Музыка: resample каждый трек → concat → trim → fade → adelay → volume
    music_out_label = None
    if music_tracks:
        n_mt = len(music_tracks)
        for j in range(n_mt):
            idx = music_input_start + j
            fc.append(
                f"[{idx}:a]aresample=44100,"
                f"aformat=channel_layouts=stereo[mt{j}]"
            )

        if n_mt > 1:
            mt_inputs = "".join(f"[mt{j}]" for j in range(n_mt))
            fc.append(f"{mt_inputs}concat=n={n_mt}:v=0:a=1[music_cat]")
        else:
            fc.append("[mt0]anull[music_cat]")

        needed_dur = max(0.0, video_duration - music_start_sec + 10.0)
        fo_start   = max(0.0, needed_dur - 4.0)
        delay_ms   = int(music_start_sec * 1000)
        music_lin  = 10 ** (music_vol_db / 20)

        # Dynamic duck expression at PEAK_MOMENT timestamps; flat if no peaks
        _music_vol_expr = _build_music_duck_expr(_safe_peaks, music_lin)
        if _music_vol_expr == f"{music_lin:.6f}":
            # No peaks — flat volume (faster ffmpeg path, avoids eval=frame overhead)
            _music_vol_filter = f"volume={music_lin:.6f}"
        else:
            _music_vol_filter = f"volume='{_music_vol_expr}':eval=frame"
            print(f"  [Audio] music duck: {_peak_count} peaks → keyframe volume", flush=True)

        fc.append(
            f"[music_cat]"
            f"atrim=0:{needed_dur:.2f},"
            f"afade=t=in:st=0:d=2,"
            f"afade=t=out:st={fo_start:.2f}:d=4,"
            f"adelay={delay_ms}|{delay_ms},"
            f"{_music_vol_filter}"
            f"[music_base]"
        )

        # Sidechaincompress: music_base (сигнал) + voice_sc (sidechain) → music_sc
        # threshold=0.50: срабатывает только на пиках голоса (~-6dBFS и выше)
        # ratio=1.5: мягкое сжатие (~1dB на пиках) — музыка всегда слышна
        # attack=50ms/release=1500ms: медленный отклик, не заметен на слух
        fc.append(
            "[music_base][voice_sc]"
            "sidechaincompress="
            "threshold=0.50:ratio=1.5:attack=50:release=1500:level_sc=0.8"
            "[music_sc]"
        )
        music_out_label = "music_sc"

    # SFX дорожки: adelay + fade in/out + volume
    sfx_labels: list[str] = []
    for k, ev in enumerate(sfx_events):
        delay_ms  = max(0, int(float(ev.get("time_s", ev.get("time", 0))) * 1000))
        gain      = float(ev.get("gain", ev.get("vol", 0.20)))
        sfx_file  = Path(str(ev["file"]))

        # Длина SFX для fade_out (быстрый ffprobe — результат кэшируется)
        try:
            _r = subprocess.run(
                ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                 "-of", "csv=p=0", str(sfx_file)],
                capture_output=True, text=True,
            )
            sfx_dur = float(_r.stdout.strip() or "1.0")
        except Exception:
            sfx_dur = 1.0

        fade_out_d = min(0.15, sfx_dur * 0.3)
        fo_st      = max(0.0, sfx_dur - fade_out_d)
        lbl        = f"sfx{k}"
        sfx_idx    = sfx_input_start + k

        fc.append(
            f"[{sfx_idx}:a]"
            f"aresample=44100,aformat=channel_layouts=stereo,"
            f"afade=t=in:st=0:d=0.04,"
            f"afade=t=out:st={fo_st:.3f}:d={fade_out_d:.3f},"
            f"adelay={delay_ms}|{delay_ms},"
            f"volume={gain:.6f}"
            f"[{lbl}]"
        )
        sfx_labels.append(f"[{lbl}]")

    # Интро аудио
    intro_label = None
    if intro_input_idx is not None:
        intro_lin = 10 ** (intro_vol_db / 20)
        fc.append(
            f"[{intro_input_idx}:a]"
            f"atrim=0:{intro_trim_s:.1f},"
            f"aresample=44100,aformat=channel_layouts=stereo,"
            f"volume={intro_lin:.6f}"
            f"[intro_a]"
        )
        intro_label = "intro_a"

    # amix: voice + music_sc + sfx* + intro
    mix_parts: list[str] = ["[voice]"]
    if music_out_label:
        mix_parts.append(f"[{music_out_label}]")
    mix_parts.extend(sfx_labels)
    if intro_label:
        mix_parts.append(f"[{intro_label}]")

    n_mix = len(mix_parts)
    mix_str = "".join(mix_parts)
    fc.append(
        f"{mix_str}"
        f"amix=inputs={n_mix}:duration=longest:normalize=0:dropout_transition=0,"
        f"loudnorm=I=-16:TP=-1:LRA=11:linear=false"
        f"[mix_out]"
    )

    _voice_dur = get_audio_duration(str(voice_path))
    _fc_str = ";".join(fc)
    _fc_file = Path(str(output_path)).parent / "_audio_filter.txt"
    _fc_file.write_text(_fc_str, encoding="utf-8")

    print(
        f"  [Audio] mix+loudnorm: voice + {len(music_tracks)}×music + {len(sfx_events)}×SFX"
        + (" + intro" if intro_label else ""),
        flush=True,
    )

    result = subprocess.run(cmd + [
        "-filter_complex_script", str(_fc_file),
        "-map", "[mix_out]",
        "-t", f"{_voice_dur:.3f}",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
        "-loglevel", "warning",
        str(output_path),
    ])
    _fc_file.unlink(missing_ok=True)

    _out_dur = get_audio_duration(str(output_path)) if Path(str(output_path)).exists() else 0.0
    if result.returncode != 0 or _out_dur < _voice_dur * 0.8:
        print(f"  [Audio] build_final_audio bad output ({_out_dur:.1f}s < {_voice_dur*0.8:.1f}s) — fallback", flush=True)
        # Graceful fallback: use old pipeline without SFX
        _v_tmp = Path(str(output_path)).parent / "_voice_tmp.aac"
        _m_tmp = Path(str(output_path)).parent / "_music_tmp.aac"
        prepare_voice_track(voice_path, _v_tmp)
        _m_ok = None
        if music_tracks and video_duration > 0:
            _m_ok = prepare_music_track(
                video_duration=video_duration,
                output_path=_m_tmp,
                music_start=int(music_start_sec),
            )
        final_mix(
            voice_path=_v_tmp,
            music_path=_m_tmp if _m_ok else None,
            output_path=output_path,
            music_start=music_start_sec,
            music_vol=10 ** (music_vol_db / 20),
            intro_audio_path=str(intro_audio_path) if intro_audio_path else None,
            intro_vol=10 ** (intro_vol_db / 20),
        )
        for tmp in [_v_tmp, _m_tmp]:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
        return str(output_path)

    dur = get_audio_duration(str(output_path))
    print(f"✅ build_final_audio: {dur:.1f}s → {output_path}", flush=True)
    return str(output_path)
