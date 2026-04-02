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
            print(f"  [Audio] {label} failed: {r.stderr.decode(errors='replace')[:300]}",
                  flush=True)
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
        probe_durations_parallel(all_tracks)
        total, idx, tracks = 0.0, 0, []
        while total < needed_dur:
            t = all_tracks[idx % len(all_tracks)]
            tracks.append(t)
            total += get_audio_duration(str(t))
            idx   += 1
            if idx > len(all_tracks) * 3:
                break
        print(f"  [Audio] music: {len(tracks)} tracks ~{total:.0f}s", flush=True)

        out = temp_dir / "_music_for_mix.wav"
        n   = len(tracks)
        cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
        for t in tracks:
            cmd += ["-i", str(t)]
        fc = [f"[0:a]atrim=0:{music_start_sec:.3f}[sil]"]
        for j in range(n):
            fc.append(f"[{j+1}:a]aresample=44100,aformat=channel_layouts=stereo[mt{j}]")
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
            gain     = float(ev.get("gain", ev.get("vol", 0.20)))
            sfx_dur  = get_audio_duration(str(ev["file"]))
            fo_d     = min(0.15, sfx_dur * 0.3)
            fc.append(
                f"[{k}:a]aresample=44100,aformat=channel_layouts=stereo,"
                f"afade=t=in:st=0:d=0.04,"
                f"afade=t=out:st={max(0.0,sfx_dur-fo_d):.3f}:d={fo_d:.3f},"
                f"adelay={delay_ms}|{delay_ms},volume={gain:.6f}[s{k}]"
            )
        ins = "".join(f"[s{k}]" for k in range(len(sfx_events)))
        fc.append(f"{ins}amix=inputs={len(sfx_events)}:"
                  f"duration=longest:normalize=0:dropout_transition=0[out]")
        cmd += ["-filter_complex", ";".join(fc),
                "-map", "[out]", "-c:a", "pcm_f32le", "-ar", "44100", str(out)]
        return out if _run(cmd, "SFX WAV") else None

    def _build_intro_wav() -> "Path | None":
        """Шаг 3: intro_audio.wav — аудио из интро-видео."""
        if not (intro_audio_path and Path(str(intro_audio_path)).exists()):
            return None
        out = temp_dir / "_intro_audio.wav"
        ok  = _run(["ffmpeg", "-y", "-i", str(intro_audio_path),
                    "-vn", "-t", f"{intro_trim_s:.2f}",
                    "-c:a", "pcm_f32le", "-ar", "44100", str(out)],
                   "intro WAV")
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

    # ── Шаг 4: bg_for_mix.wav ────────────────────────────────────────────────
    # amix всех фоновых дорожек с их громкостями
    bg_wav = None
    bg = []   # (path, vol_db)
    if music_wav:
        bg.append((music_wav, music_vol_db))
    if intro_wav:
        bg.append((intro_wav, intro_vol_db))
    if sfx_wav:
        bg.append((sfx_wav, 0.0))   # SFX уже с gain

    if bg:
        bg_wav = temp_dir / "_bg_for_mix.wav"
        _wavs.append(bg_wav)
        if len(bg) == 1:
            path, db = bg[0]
            _run(["ffmpeg", "-y", "-i", str(path),
                  "-af", f"volume={db}dB",
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

    # ── Шаг 5: финальный 2-input amix → AAC 320k ─────────────────────────────
    print(f"  [Audio] final mix: voice"
          + (f" + music" if music_wav else "")
          + (f" + intro" if intro_wav else "")
          + (f" + {len(sfx_events)}×SFX" if sfx_wav else ""),
          flush=True)

    if bg_wav and bg_wav.exists():
        _run([
            "ffmpeg", "-y",
            "-i", str(voice_path),
            "-i", str(bg_wav),
            "-filter_complex",
            "[0:a]aresample=44100,aformat=channel_layouts=stereo[v];"
            "[1:a]aresample=44100,aformat=channel_layouts=stereo[b];"
            "[v][b]amix=inputs=2:duration=first:normalize=0:dropout_transition=0[out]",
            "-map", "[out]",
            "-t", f"{voice_dur:.3f}",
            "-c:a", "aac", "-b:a", "320k", "-ar", "44100",
            "-loglevel", "warning",
            str(output_path),
        ], "final amix")
    else:
        # Нет фоновых дорожек — просто конвертируем голос
        _run(["ffmpeg", "-y", "-i", str(voice_path),
              "-c:a", "aac", "-b:a", "320k", "-ar", "44100",
              "-loglevel", "warning", str(output_path)], "voice-only")

    # Удалить temp WAV файлы
    for w in _wavs:
        try:
            w.unlink(missing_ok=True)
        except Exception:
            pass

    out_dur = get_audio_duration(str(output_path)) if Path(str(output_path)).exists() else 0.0
    print(f"  [Audio] done: {out_dur:.1f}s → {output_path}", flush=True)
    return str(output_path)
