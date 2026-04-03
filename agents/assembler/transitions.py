"""
transitions.py — FFmpeg-based video transitions for the pipeline.

Cross-clip transitions (between montage clips):
  concat_all_with_transitions — chunk-based xfade with per-boundary trans_seq

Intro→main transitions:
  smooth_zoom_transition  — DE: scale 1.05x + gblur ramp dissolve (no zoompan)
  whip_pan_transition     — FR: boxblur=60 ramp dissolve + overshoot (no slideleft)

Fallback:
  intro_to_main_transition — gblur dissolve (used by both if above fail)
"""

import json
import logging
import os
import random
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

_fallback_counter: dict[str, int] = {"count": 0}

# Глобальная temp-директория — устанавливается из gosha_rubchinskiy через set_temp_dir()
_TEMP_DIR = Path("temp")

def set_temp_dir(path):
    """Установить корневую temp-директорию для всех переходов (D: SSD)."""
    global _TEMP_DIR
    _TEMP_DIR = Path(path)
    _TEMP_DIR.mkdir(parents=True, exist_ok=True)

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# ── Утилиты ──────────────────────────────────────────────────────────────────

_DUR_CACHE: dict[str, float] = {}

def get_audio_duration(path) -> float:
    key = str(path)
    if key in _DUR_CACHE:
        return _DUR_CACHE[key]
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", key],
        capture_output=True, text=True)
    try:
        val = float(json.loads(r.stdout)["format"]["duration"])
    except Exception:
        val = 0.0
    _DUR_CACHE[key] = val
    return val


def get_video_duration(path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_streams", str(path)],
        capture_output=True, text=True)
    try:
        for s in json.loads(r.stdout).get("streams", []):
            if s.get("codec_type") == "video":
                return float(s.get("duration", 0))
    except Exception:
        pass
    return 0.0


# ── GPU энкодер ───────────────────────────────────────────────────────────────

def get_gpu_encoder():
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-encoders"],
        capture_output=True, text=True)
    if "h264_nvenc" in result.stdout:
        print("GPU: NVIDIA NVENC", flush=True)
        return "h264_nvenc", [
            "-preset", "p4", "-tune", "hq",
            "-rc", "vbr", "-cq", "19", "-b:v", "0",
        ]
    print("CPU fallback: libx264", flush=True)
    return "libx264", ["-preset", "fast", "-crf", "18"]


# Быстрый NVENC для blur-зон переходов (короткие сегменты, качество не критично)
def get_gpu_encoder_fast():
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-encoders"],
        capture_output=True, text=True)
    if "h264_nvenc" in result.stdout:
        return "h264_nvenc", ["-preset", "p1", "-cq", "22"]
    return "libx264", ["-preset", "ultrafast", "-crf", "20"]


GPU_ENCODER, GPU_PARAMS           = get_gpu_encoder()
GPU_ENCODER_FAST, GPU_PARAMS_FAST = get_gpu_encoder_fast()


def _set_encoder_x264():
    """Принудительно переключить на libx264 (вызывается при NVENC truncation)."""
    global GPU_ENCODER, GPU_PARAMS, GPU_ENCODER_FAST, GPU_PARAMS_FAST
    GPU_ENCODER      = "libx264"
    GPU_PARAMS       = ["-preset", "fast", "-crf", "18"]
    GPU_ENCODER_FAST = "libx264"
    GPU_PARAMS_FAST  = ["-preset", "fast", "-crf", "20"]
    print("⚠️  Encoder switched to libx264 (NVENC truncation detected)", flush=True)


# ── Micro-jitter (используется в smooth_zoom) ────────────────────────────────

def _jitter_xy(frames: int) -> tuple[str, str]:
    """
    3-компонентный micro-jitter для zoompan x/y:
    1. Phase-shifted primary sin
    2. Second harmonic (ноль на границах)
    3. Micro drift pow(in/F, 0.7)
    """
    ax = round(random.uniform(0.5, 1.3), 3)
    ay = round(random.uniform(0.2, 0.6), 3)
    phase_x = round(random.uniform(-0.35, 0.35), 3)
    phase_y = round(random.uniform(-0.30, 0.30), 3)
    h2x = round(random.uniform(-0.35, 0.35) * ax, 4)
    h2y = round(random.uniform(-0.30, 0.30) * ay, 4)
    sx = random.choice([-1, 1])
    sy = random.choice([-1, 1])
    drift_x = round(random.uniform(-0.5, 0.5), 3)
    drift_y = round(random.uniform(-0.3, 0.3), 3)

    p1x = f"{sx * ax:.3f}*sin(3.14159265*in/{frames}+{phase_x:.3f})"
    p2x = f"{h2x:+.4f}*sin(6.28318531*in/{frames})"
    drx = f"{drift_x:+.3f}*pow(in/{frames},0.7)"
    x   = f"iw/2-(iw/zoom/2)+{p1x}{p2x}{drx}"

    p1y = f"{sy * ay:.3f}*sin(3.14159265*in/{frames}+{phase_y:.3f})"
    p2y = f"{h2y:+.4f}*sin(6.28318531*in/{frames})"
    dry = f"{drift_y:+.3f}*pow(in/{frames},0.7)"
    y   = f"ih/2-(ih/zoom/2)+{p1y}{p2y}{dry}"

    return x, y


# ── Общий помощник: blur-ramp через split+gblur+blend ───────────────────────

def _blur_ramp_zone(src_path, dst_path, duration, sigma, ramp,
                    pre_filter="", fps=25):
    """
    Вырезать duration секунд из src_path и применить blur с нарастанием/спаданием.

    ramp='up'   — blur нарастает к концу   (0→max): для конца intro
    ramp='down' — blur спадает с начала    (max→0): для начала main

    Используется в smooth_zoom и whip_pan для замены zoompan и slideleft.
    blend=all_expr с T-переменной даёт плавный переход (T=0..duration).
    """
    # ramp up:   weight = pow(sin(PI/2 * T/D), 2)  → 0 at T=0,  1 at T=D
    # ramp down: weight = pow(cos(PI/2 * T/D), 2)  → 1 at T=0,  0 at T=D
    if ramp == "up":
        w = f"pow(sin(1.5708*T/{duration:.4f}),2)"
    else:
        w = f"pow(cos(1.5708*T/{duration:.4f}),2)"

    pre = f"{pre_filter}," if pre_filter else ""
    fc  = (
        f"[0:v]fps={fps},setpts=PTS-STARTPTS,{pre}split[sh][sb];"
        f"[sb]gblur=sigma={sigma}[blr];"
        f"[sh][blr]blend=all_expr='A*(1-{w})+B*{w}'[out]"
    )
    subprocess.run([
        "ffmpeg", "-y", "-i", str(src_path),
        "-t", str(duration),
        "-filter_complex", fc,
        "-map", "[out]",
        "-c:v", GPU_ENCODER_FAST, *GPU_PARAMS_FAST,
        "-pix_fmt", "yuv420p", "-an", "-r", str(fps), str(dst_path),
    ], check=True, capture_output=True)


def _blur_ramp_zone_ss(src_path, dst_path, ss, duration, sigma, ramp,
                       pre_filter="", fps=25):
    """Как _blur_ramp_zone, но с -ss (для конца intro)."""
    if ramp == "up":
        w = f"pow(sin(1.5708*T/{duration:.4f}),2)"
    else:
        w = f"pow(cos(1.5708*T/{duration:.4f}),2)"

    pre = f"{pre_filter}," if pre_filter else ""
    fc  = (
        f"[0:v]fps={fps},setpts=PTS-STARTPTS,{pre}split[sh][sb];"
        f"[sb]gblur=sigma={sigma}[blr];"
        f"[sh][blr]blend=all_expr='A*(1-{w})+B*{w}'[out]"
    )
    subprocess.run([
        "ffmpeg", "-y", "-i", str(src_path),
        "-ss", str(ss), "-t", str(duration),
        "-filter_complex", fc,
        "-map", "[out]",
        "-c:v", GPU_ENCODER_FAST, *GPU_PARAMS_FAST,
        "-pix_fmt", "yuv420p", "-an", "-r", str(fps), str(dst_path),
    ], check=True, capture_output=True)


def _dissolve_merge(a_path, b_path, out_path, dur_a):
    """Dissolve между двумя clip'ами: offset = dur_a - blend_dur."""
    blend_dur = max(0.04, min(0.16, get_video_duration(str(a_path)) * 0.5))
    blend_off = max(0.01, dur_a - blend_dur)
    subprocess.run([
        "ffmpeg", "-y", "-i", str(a_path), "-i", str(b_path),
        "-filter_complex",
        f"[0:v][1:v]xfade=transition=dissolve:"
        f"duration={blend_dur:.3f}:offset={blend_off:.3f}[out]",
        "-map", "[out]",
        "-c:v", GPU_ENCODER, *GPU_PARAMS,
        "-pix_fmt", "yuv420p", "-an", str(out_path),
    ], check=True, capture_output=True)


def _final_concat(parts, output_path, post_vf: str = "", audio_path: str = "",
                  metadata_flags: list = None, overlay_path: str = ""):
    """
    Склейка через concat demuxer.
    post_vf       — видеофильтры после concat (цвет; без ass= если overlay_path задан).
    audio_path    — путь к аудио (mux вместе с видео), опционально.
    metadata_flags — список ffmpeg флагов [-metadata key=val ...], опционально.
    overlay_path  — пре-рендеренный прозрачный субтитровый оверлей (yuva420p VP9).
                    Если задан, накладывается через filter_complex overlay вместо ass=.
    """
    concat_f = str(_TEMP_DIR / "_concat_list.txt")
    with open(concat_f, "w") as f:
        for p in parts:
            if Path(p).exists() and Path(p).stat().st_size > 1000:
                f.write(f"file '{os.path.abspath(str(p))}'\n")

    _has_overlay = bool(overlay_path and Path(overlay_path).exists())
    _has_audio   = bool(audio_path   and Path(audio_path).exists())

    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_f]
    if _has_overlay:
        cmd += ["-i", str(overlay_path)]
    if _has_audio:
        cmd += ["-i", str(audio_path)]

    _WIN_CMD_LIMIT = 8000   # Windows CommandLineToArgvW safe limit
    _using_fc_script = False

    if _has_overlay:
        # filter_complex: color-grade concat stream, then overlay subtitle track
        if post_vf:
            fc = f"[0:v]{post_vf}[_c];[_c][1:v]overlay=format=auto[v]"
        else:
            fc = "[0:v][1:v]overlay=format=auto[v]"
        cmd += ["-filter_complex", fc, "-map", "[v]"]
    elif post_vf:
        if len(post_vf) > _WIN_CMD_LIMIT:
            # Фильтр слишком длинный для командной строки Windows →
            # пишем в temp-файл и передаём через -filter_complex_script
            _fc_file = _TEMP_DIR / "_post_vf.txt"
            _fc_file.parent.mkdir(parents=True, exist_ok=True)
            _fc_file.write_text(f"[0:v]{post_vf}[_vout]", encoding="utf-8")
            cmd += ["-filter_complex_script", str(_fc_file), "-map", "[_vout]"]
            _using_fc_script = True
        else:
            cmd += ["-vf", post_vf]

    cmd += ["-threads", "4", "-c:v", GPU_ENCODER, *GPU_PARAMS, "-pix_fmt", "yuv420p"]

    if _has_audio:
        audio_idx = 2 if _has_overlay else 1
        if not _has_overlay and not _using_fc_script:
            # filter_complex / filter_complex_script уже маппит [v]; без них нужен явный map видео
            cmd += ["-map", "0:v:0"]
        cmd += ["-map", f"{audio_idx}:a:0", "-c:a", "copy", "-shortest"]
    else:
        cmd += ["-an"]

    if metadata_flags:
        cmd += ["-map_metadata", "-1"] + metadata_flags

    cmd += ["-loglevel", "warning", str(output_path)]

    result = subprocess.run(cmd)
    # If NVENC fails or output is significantly too short, retry with libx264
    if GPU_ENCODER != "libx264":
        _out_dur = get_video_duration(str(output_path)) if Path(str(output_path)).exists() else 0.0
        _video_dur = sum(get_video_duration(str(p)) for p in parts if Path(str(p)).exists())
        _audio_dur = get_audio_duration(str(audio_path)) if audio_path and Path(str(audio_path)).exists() else None
        _expected_dur = min(_video_dur, _audio_dur) if _audio_dur else _video_dur
        if result.returncode != 0 or (_expected_dur > 10 and _out_dur < _expected_dur - 3.0):
            import logging as _logging
            _logging.warning(
                f"_final_concat NVENC issue (got {_out_dur:.1f}s expected ~{_expected_dur:.1f}s), retrying x264"
            )
            # Rebuild cmd from scratch with libx264 — не трогаем токен-реконструкцию
            # (она ломается когда "0" из GPU_PARAMS совпадает с "0" аргументами concat/safe/etc.)
            cmd_x264 = cmd[:]  # полная копия
            # Заменяем encoder и его параметры
            enc_idx = next((i for i, t in enumerate(cmd_x264) if t == GPU_ENCODER), None)
            if enc_idx is not None:
                cmd_x264[enc_idx] = "libx264"
                # Убираем все токены GPU_PARAMS (флаги и их значения)
                gpu_params_set = set(GPU_PARAMS)
                result2 = []
                skip = False
                for tok in cmd_x264:
                    if skip:
                        skip = False
                        continue
                    if tok in gpu_params_set and tok.startswith("-"):
                        skip = True  # пропустить этот флаг и следующий (значение)
                        continue
                    result2.append(tok)
                cmd_x264 = result2
            cmd_x264 += ["-preset", "fast", "-crf", "18"]
            subprocess.run(cmd_x264, check=True)


# ── CROSSFADE (opacity ease-in-out, без пикселизации) ────────────────────────

def crossfade_transition(clip1_path, clip2_path, output_path, duration=0.5):
    """
    Crossfade по opacity с ease-in-out (без xfade/пикселизации).
    Используется между клипами основного видеоряда.

    Структура: [c1_main] + [blend_zone] + [c2_main]
    blend_zone = blend(конец clip1, начало clip2) с:
      w = 0.5 - 0.5*cos(π*T/dur)  — плавный ease-in-out
    """
    clip1_path  = Path(clip1_path)
    clip2_path  = Path(clip2_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _TEMP_DIR.mkdir(parents=True, exist_ok=True)

    fps       = 25
    clip1_dur = get_video_duration(str(clip1_path))
    c1_main   = max(0.04, clip1_dur - duration)

    t_c1main = str(_TEMP_DIR / "cf_c1main.mp4")
    t_blend  = str(_TEMP_DIR / "cf_blend.mp4")
    t_c2main = str(_TEMP_DIR / "cf_c2main.mp4")

    # ease-in-out: w плавно идёт 0→1 через cosine
    w  = f"0.5-0.5*cos(3.14159265*T/{duration:.4f})"
    fc = (
        f"[0:v]fps={fps},setpts=PTS-STARTPTS[a];"
        f"[1:v]fps={fps},setpts=PTS-STARTPTS[b];"
        f"[a][b]blend=all_expr='A*(1-({w}))+B*({w})'[out]"
    )

    try:
        # 1. clip1 main — до зоны перехода (FAST: этот файл сразу ре-кодируется в _final_concat)
        subprocess.run([
            "ffmpeg", "-y", "-i", str(clip1_path),
            "-t", f"{c1_main:.4f}",
            "-vf", f"fps={fps}",
            "-c:v", GPU_ENCODER_FAST, *GPU_PARAMS_FAST,
            "-pix_fmt", "yuv420p", "-an", t_c1main,
        ], check=True, capture_output=True)

        # 2. blend zone: конец clip1 + начало clip2
        subprocess.run([
            "ffmpeg", "-y",
            "-ss", f"{c1_main:.4f}", "-i", str(clip1_path),
            "-i", str(clip2_path),
            "-t", f"{duration:.4f}",
            "-filter_complex", fc,
            "-map", "[out]",
            "-c:v", GPU_ENCODER_FAST, *GPU_PARAMS_FAST,
            "-pix_fmt", "yuv420p", "-an", "-r", str(fps), t_blend,
        ], check=True, capture_output=True)

        # 3. clip2 main — остаток после зоны перехода (FAST: тоже ре-кодируется в _final_concat)
        subprocess.run([
            "ffmpeg", "-y", "-i", str(clip2_path),
            "-ss", f"{duration:.4f}",
            "-vf", f"fps={fps}",
            "-c:v", GPU_ENCODER_FAST, *GPU_PARAMS_FAST,
            "-pix_fmt", "yuv420p", "-an", t_c2main,
        ], check=True, capture_output=True)

        _final_concat([t_c1main, t_blend, t_c2main], output_path)
        # Удаляем промежуточные файлы сразу — output уже записан
        for _tmp in [t_c1main, t_blend, t_c2main]:
            Path(_tmp).unlink(missing_ok=True)
        print(f"  OK crossfade (opacity ease-in-out): {output_path.name}", flush=True)
        return True

    except subprocess.CalledProcessError as e:
        print(f"  !! crossfade fail: {e}", flush=True)
        return False


# ── FALLBACK: intro→main (gblur dissolve) ────────────────────────────────────

def intro_to_main_transition(intro_path, main_path,
                              output_path, duration=0.75, **fc_kwargs):
    """
    Fallback для обоих каналов: сильный gblur на зонах перехода + dissolve.
    Никаких zoompan, никаких артефактов.
    """
    intro_path  = Path(intro_path)
    main_path   = Path(main_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _TEMP_DIR.mkdir(parents=True, exist_ok=True)

    intro_dur = get_video_duration(str(intro_path))
    offset    = max(0.1, intro_dur - duration)
    fps       = 25
    SIGMA     = 30

    ib = str(_TEMP_DIR / "it_ib.mp4")
    iz = str(_TEMP_DIR / "it_iz.mp4")
    mz = str(_TEMP_DIR / "it_mz.mp4")
    ma = str(_TEMP_DIR / "it_ma.mp4")
    bm = str(_TEMP_DIR / "it_bm.mp4")

    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", str(intro_path), "-t", str(offset),
            "-vf", f"fps={fps}", "-c:v", GPU_ENCODER, *GPU_PARAMS,
            "-pix_fmt", "yuv420p", "-an", ib,
        ], check=True, capture_output=True)

        _blur_ramp_zone_ss(intro_path, iz, offset, duration, SIGMA, "up",  fps=fps)
        _blur_ramp_zone   (main_path,  mz,          duration, SIGMA, "down", fps=fps)

        subprocess.run([
            "ffmpeg", "-y", "-i", str(main_path), "-ss", str(duration),
            "-vf", f"fps={fps}", "-c:v", GPU_ENCODER, *GPU_PARAMS,
            "-pix_fmt", "yuv420p", "-an", ma,
        ], check=True, capture_output=True)

        _dissolve_merge(iz, mz, bm, duration)
        _final_concat([ib, bm, ma], output_path, **fc_kwargs)

        print(f"  OK blur dissolve: {output_path.name}", flush=True)
        return True

    except subprocess.CalledProcessError as e:
        print(f"  !! intro_to_main fallback concat: {e}", flush=True)
        concat_f = str(_TEMP_DIR / "it_raw.txt")
        with open(concat_f, "w") as f:
            f.write(f"file '{os.path.abspath(str(intro_path))}'\n")
            f.write(f"file '{os.path.abspath(str(main_path))}'\n")
        subprocess.run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_f,
            "-c:v", GPU_ENCODER, *GPU_PARAMS, "-pix_fmt", "yuv420p", "-an",
            str(output_path),
        ])
        return False


# ── SMOOTH ZOOM (DE intro→main) ───────────────────────────────────────────────

def smooth_zoom_transition(intro_path, main_path,
                            output_path, duration=0.32, **fc_kwargs):
    """
    DE-style: scale 1.05x + gblur sigma=28 ramp (tblend+blend) + dissolve.

    Заменяет zoompan:
      - Нет CPU-артефактов (нет zoompan)
      - 5% zoom через статичный scale+crop — чуть глубже кадр
      - Blur нарастает к концу intro и спадает в начале main
      - Dissolve между двумя blur-зонами — кинематичный переход
    """
    intro_path  = Path(intro_path)
    main_path   = Path(main_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _TEMP_DIR.mkdir(parents=True, exist_ok=True)

    fps       = 25
    intro_dur = get_video_duration(str(intro_path))
    offset    = max(0.1, intro_dur - duration)
    SIGMA     = 28

    # 1.05x zoom через scale+crop (без zoompan)
    SW, SH = int(1920 * 1.05), int(1080 * 1.05)   # 2016 x 1134
    CX, CY = (SW - 1920) // 2, (SH - 1080) // 2   # 48 x 27
    scale_f = f"scale={SW}:{SH},crop=1920:1080:{CX}:{CY}"

    ib = str(_TEMP_DIR / "sz_ib.mp4")
    iz = str(_TEMP_DIR / "sz_iz.mp4")
    mz = str(_TEMP_DIR / "sz_mz.mp4")
    ma = str(_TEMP_DIR / "sz_ma.mp4")
    bm = str(_TEMP_DIR / "sz_bm.mp4")

    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", str(intro_path), "-t", str(offset),
            "-vf", f"fps={fps}", "-c:v", GPU_ENCODER, *GPU_PARAMS,
            "-pix_fmt", "yuv420p", "-an", ib,
        ], check=True, capture_output=True)

        # intro zone: scale + blur ramp UP (нарастает к концу)
        _blur_ramp_zone_ss(intro_path, iz, offset, duration,
                           SIGMA, "up", pre_filter=scale_f, fps=fps)

        # main zone: scale + blur ramp DOWN (спадает от начала)
        _blur_ramp_zone(main_path, mz, duration,
                        SIGMA, "down", pre_filter=scale_f, fps=fps)

        subprocess.run([
            "ffmpeg", "-y", "-i", str(main_path), "-ss", str(duration),
            "-vf", f"fps={fps}", "-c:v", GPU_ENCODER, *GPU_PARAMS,
            "-pix_fmt", "yuv420p", "-an", ma,
        ], check=True, capture_output=True)

        _dissolve_merge(iz, mz, bm, duration)
        _final_concat([ib, bm, ma], output_path, **fc_kwargs)

        print(f"  OK smooth_zoom (scale+gblur): {output_path.name}", flush=True)
        return True

    except subprocess.CalledProcessError as e:
        print(f"  !! smooth_zoom fail — fallback: {e}", flush=True)
        return intro_to_main_transition(intro_path, main_path, output_path, duration)


# ── WHIP PAN (FR intro→main) ──────────────────────────────────────────────────

def whip_pan_transition(intro_path, main_path,
                         output_path, duration=0.20, **fc_kwargs):
    """
    Swipe LEFT + blur:
      - Последние 0.2s clip1: blur плавно нарастает 0→max + свайп влево
      - Hard cut (без dissolve, без overshoot, без пикселизации)
      - Первые 0.3s clip2: blur max→0 + доводка движения
    """
    BLUR_IN  = 0.20   # секунд в конце clip1
    BLUR_OUT = 0.40   # секунд в начале clip2
    BLUR_R   = 55     # максимальный boxblur radius
    SWIPE_PX = 60     # пикселей горизонтального смещения

    intro_path  = Path(intro_path)
    main_path   = Path(main_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _TEMP_DIR.mkdir(parents=True, exist_ok=True)

    fps       = 25
    intro_dur = get_video_duration(str(intro_path))

    t_c1main = str(_TEMP_DIR / "wp_c1main.mp4")
    t_c1blur = str(_TEMP_DIR / "wp_c1blur.mp4")
    t_c2blur = str(_TEMP_DIR / "wp_c2blur.mp4")
    t_c2main = str(_TEMP_DIR / "wp_c2main.mp4")

    # filter_complex: scale up → crop с движением → blur рампа через blend
    def _make_fc(dur: float, ramp: str) -> str:
        """
        ramp='up'   → blur 0→max (ease-in),  свайп влево ускоряется
        ramp='down' → blur max→0 (ease-out), свайп замедляется и останавливается
        Easing через sin²/cos² (плавный разгон и торможение).
        """
        if ramp == "up":
            # ease-in: медленно начинается, быстро нарастает к cut
            ease     = f"pow(sin(1.5708*T/{dur:.4f}),2)"
            crop_x   = f"{SWIPE_PX}*pow(t/{dur:.4f},2)"
            blend_w  = ease
        else:
            # ease-out: blur max→0, без движения — клип стоит на месте
            ease    = f"pow(cos(1.5708*T/{dur:.4f}),2)"
            blend_w = ease
            return (
                f"[0:v]fps={fps},setpts=PTS-STARTPTS,split[a][b];"
                f"[b]boxblur=luma_radius={BLUR_R}:luma_power=1:"
                f"chroma_radius={BLUR_R//2}:chroma_power=1[bl];"
                f"[a][bl]blend=all_expr='A*(1-({blend_w}))+B*({blend_w})'[out]"
            )
        W = 1920 + SWIPE_PX * 2
        # blend сначала (split → blur → blend), crop движения — после
        return (
            f"[0:v]fps={fps},setpts=PTS-STARTPTS,split[a][b];"
            f"[b]boxblur=luma_radius={BLUR_R}:luma_power=1:"
            f"chroma_radius={BLUR_R//2}:chroma_power=1[bl];"
            f"[a][bl]blend=all_expr='A*(1-({blend_w}))+B*({blend_w})'[blended];"
            f"[blended]scale={W}:1080,"
            f"crop=1920:1080:'{crop_x}':0[out]"
        )

    try:
        # 1. clip1 main — всё до последних BLUR_IN секунд
        c1_main_dur = max(0.1, intro_dur - BLUR_IN)
        subprocess.run([
            "ffmpeg", "-y", "-i", str(intro_path),
            "-t", f"{c1_main_dur:.4f}",
            "-vf", f"fps={fps}",
            "-c:v", GPU_ENCODER, *GPU_PARAMS,
            "-pix_fmt", "yuv420p", "-an", t_c1main,
        ], check=True, capture_output=True)

        # 2. clip1 blur zone — последние BLUR_IN секунд, blur 0→max + свайп
        c1_blur_ss = max(0.0, intro_dur - BLUR_IN)
        subprocess.run([
            "ffmpeg", "-y",
            "-ss", f"{c1_blur_ss:.4f}",
            "-i", str(intro_path),
            "-t", f"{BLUR_IN:.4f}",
            "-filter_complex", _make_fc(BLUR_IN, "up"),
            "-map", "[out]",
            "-c:v", GPU_ENCODER_FAST, *GPU_PARAMS_FAST,
            "-pix_fmt", "yuv420p", "-an", "-r", str(fps), t_c1blur,
        ], check=True, capture_output=True)

        # 3. clip2 blur zone — первые BLUR_OUT секунд, blur max→0 + доводка
        subprocess.run([
            "ffmpeg", "-y",
            "-i", str(main_path),
            "-t", f"{BLUR_OUT:.4f}",
            "-filter_complex", _make_fc(BLUR_OUT, "down"),
            "-map", "[out]",
            "-c:v", GPU_ENCODER_FAST, *GPU_PARAMS_FAST,
            "-pix_fmt", "yuv420p", "-an", "-r", str(fps), t_c2blur,
        ], check=True, capture_output=True)

        # 4. clip2 main — остаток после BLUR_OUT секунд
        subprocess.run([
            "ffmpeg", "-y", "-i", str(main_path),
            "-ss", f"{BLUR_OUT:.4f}",
            "-vf", f"fps={fps}",
            "-c:v", GPU_ENCODER, *GPU_PARAMS,
            "-pix_fmt", "yuv420p", "-an", t_c2main,
        ], check=True, capture_output=True)

        # 5. Склеиваем 4 части
        _final_concat([t_c1main, t_c1blur, t_c2blur, t_c2main], output_path, **fc_kwargs)
        print(f"  OK whip_pan (swipe+blur): {output_path.name}", flush=True)
        return True

    except subprocess.CalledProcessError as e:
        print(f"  !! whip_pan fail — fallback: {e}", flush=True)
        return intro_to_main_transition(intro_path, main_path, output_path, duration)


# ── ZOOM BLUR (FR intro→main) ────────────────────────────────────────────────

def zoom_blur_transition(intro_path, main_path, output_path, duration=0.20, **fc_kwargs):
    """
    FR-style: zoom in/out + blur ramp (ease sin²/cos²). Без свайпа, без dissolve.

    Структура: [c1_main] + [c1_zoom_blur 0.2s] + [c2_zoom_blur 0.4s] + [c2_main]

    clip1 конец: blur 0→max + zoom in  (1.0 → 1+ZOOM_MAX, ease-in)
    clip2 начало: blur max→0 + zoom out (1+ZOOM_MAX → 1.0, ease-out)
    Hard cut между зонами — скрыт максимальным blur.
    """
    BLUR_IN   = 0.20
    BLUR_OUT  = 0.30   # нормализовано: было 0.60 — слишком затянуто
    BLUR_R    = 55
    BLUR_PEAK = 0.65   # пик блюра: 65%
    ZOOM_MAX  = 0.35   # максимальный зум на intro-стороне: 1.35x

    intro_path  = Path(intro_path)
    main_path   = Path(main_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _TEMP_DIR.mkdir(parents=True, exist_ok=True)

    fps       = 25
    intro_dur = get_video_duration(str(intro_path))

    t_c1main = str(_TEMP_DIR / "zb_c1main.mp4")
    t_c1blur = str(_TEMP_DIR / "zb_c1blur.mp4")
    t_c2blur = str(_TEMP_DIR / "zb_c2blur.mp4")
    t_c2main = str(_TEMP_DIR / "zb_c2main.mp4")

    def _make_fc(dur: float, ramp: str) -> str:
        N      = max(1, int(round(dur * fps)))
        last_t = (N - 1) / fps

        # offset кропа = (1920*ZOOM_MAX/2) * ease  — совпадает с формулой scale
        cx = int(1920 * ZOOM_MAX / 2)   # макс offset по x = 336
        cy = int(1080 * ZOOM_MAX / 2)   # макс offset по y = 189

        if ramp == "up":
            # intro конец: zoom IN + blur нарастает (ease-in sin²)
            blend_w = f"{BLUR_PEAK:.4f}*pow(sin(1.5708*T/{dur:.4f}),2)"
            ease_sc = f"(1+{ZOOM_MAX}*(0.5-0.5*cos(3.14159*t/{dur:.4f})))"
            crop_x  = f"{cx}*(0.5-0.5*cos(3.14159*t/{dur:.4f}))"
            crop_y  = f"{cy}*(0.5-0.5*cos(3.14159*t/{dur:.4f}))"
            scale_f = (
                f"scale=w='1920*{ease_sc}':h='1080*{ease_sc}':eval=frame,"
                f"crop=1920:1080:x='{crop_x}':y='{crop_y}'"
            )
            return (
                f"[0:v]fps={fps},setpts=PTS-STARTPTS,split[a][b];"
                f"[b]boxblur=luma_radius={BLUR_R}:luma_power=1:"
                f"chroma_radius={BLUR_R//2}:chroma_power=1[bl];"
                f"[a][bl]blend=all_expr='A*(1-({blend_w}))+B*({blend_w})'[blended];"
                f"[blended]{scale_f}[out]"
            )
        else:
            # main начало: ТОЛЬКО blur уходит (cos²), БЕЗ zoom → нет двойного движения
            blend_w = f"{BLUR_PEAK:.4f}*pow(cos(1.5708*min(T,{last_t:.4f})/{last_t:.4f}),2)"
            return (
                f"[0:v]fps={fps},setpts=PTS-STARTPTS,split[a][b];"
                f"[b]boxblur=luma_radius={BLUR_R}:luma_power=1:"
                f"chroma_radius={BLUR_R//2}:chroma_power=1[bl];"
                f"[a][bl]blend=all_expr='A*(1-({blend_w}))+B*({blend_w})'[out]"
            )

    try:
        # 1. clip1 main
        c1_main_dur = max(0.1, intro_dur - BLUR_IN)
        subprocess.run([
            "ffmpeg", "-y", "-i", str(intro_path),
            "-t", f"{c1_main_dur:.4f}",
            "-vf", f"fps={fps}",
            "-c:v", GPU_ENCODER, *GPU_PARAMS,
            "-pix_fmt", "yuv420p", "-an", t_c1main,
        ], check=True, capture_output=True)

        # 2. clip1 blur+zoom zone — последние BLUR_IN секунд
        c1_blur_ss = max(0.0, intro_dur - BLUR_IN)
        subprocess.run([
            "ffmpeg", "-y",
            "-ss", f"{c1_blur_ss:.4f}", "-i", str(intro_path),
            "-t", f"{BLUR_IN:.4f}",
            "-filter_complex", _make_fc(BLUR_IN, "up"),
            "-map", "[out]",
            "-c:v", GPU_ENCODER_FAST, *GPU_PARAMS_FAST,
            "-pix_fmt", "yuv420p", "-an", "-r", str(fps), t_c1blur,
        ], check=True, capture_output=True)

        # 3. clip2 blur+zoom zone — первые BLUR_OUT секунд
        subprocess.run([
            "ffmpeg", "-y", "-i", str(main_path),
            "-t", f"{BLUR_OUT:.4f}",
            "-filter_complex", _make_fc(BLUR_OUT, "down"),
            "-map", "[out]",
            "-c:v", GPU_ENCODER_FAST, *GPU_PARAMS_FAST,
            "-pix_fmt", "yuv420p", "-an", "-r", str(fps), t_c2blur,
        ], check=True, capture_output=True)

        # 4. clip2 main
        subprocess.run([
            "ffmpeg", "-y", "-i", str(main_path),
            "-ss", f"{BLUR_OUT:.4f}",
            "-vf", f"fps={fps}",
            "-c:v", GPU_ENCODER, *GPU_PARAMS,
            "-pix_fmt", "yuv420p", "-an", t_c2main,
        ], check=True, capture_output=True)

        # 5. Склейка
        _final_concat([t_c1main, t_c1blur, t_c2blur, t_c2main], output_path, **fc_kwargs)
        print(f"  OK zoom_blur (zoom+blur): {output_path.name}", flush=True)
        return True

    except subprocess.CalledProcessError as e:
        print(f"  !! zoom_blur fail — fallback: {e}", flush=True)
        return intro_to_main_transition(intro_path, main_path, output_path, duration)


# ── CHUNK CONCAT ─────────────────────────────────────────────────────────────

def _simple_concat(clip_paths, output_path):
    _tmp = _TEMP_DIR
    _tmp.mkdir(parents=True, exist_ok=True)
    uid = abs(hash(str(output_path))) % 10_000_000
    concat_file = str(_tmp / f"simple_concat_{uid}.txt")
    with open(concat_file, "w", encoding="utf-8") as f:
        for p in clip_paths:
            f.write(f"file '{os.path.abspath(str(p))}'\n")
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", concat_file,
        "-c", "copy",
        "-loglevel", "warning",
        str(output_path),
    ], check=True)
    try:
        os.remove(concat_file)
    except OSError:
        pass


# ── Маппинг типов переходов → xfade имена ────────────────────────────────────
_XFADE_MAP = {
    "cross_zoom":    "dissolve",   # DE: gblur+dissolve (нет pixelize!)
    "glitch_flash":  "fadeblack",  # FR: chromatic+fadeblack или plain fadeblack
    "glitch_chroma": "fadeblack",  # FR хроматический вариант
    "dissolve":      "dissolve",
    "fadewhite":     "fadewhite",
    "fadeblack":     "fadeblack",
    "fade":          "fade",
}

_GLITCH_MAX_DUR  = 0.12
_FLASH_TYPES     = frozenset({"fadewhite", "fadeblack", "fade"})
_FLASH_DUR_RANGE = (0.04, 0.08)
_SCALE_TRANSITIONS: set = set()  # не нужно — используем gblur-подход

_CROSS_ZOOM_SIGMA = 18   # gblur sigma для cross_zoom зон
_GLITCH_RH        = 10  # rgbashift сдвиг пикселей для хроматик


def concat_clips_xfade(
    clip_paths,
    durations,
    output_path,
    xf_dur: float = 1.0,
    fps: int = 25,
    chunk_size: int = 100,
) -> bool:
    """
    Crossfade с tpad clone handles — Premiere Pro архитектура.

    Каждый клип получает:
      - первый в run:  tpad tail  = xf_dur/2 (freeze последнего кадра)
      - последний:     tpad head  = xf_dur/2 (freeze первого кадра)
      - средние:       tpad head + tail = xf_dur (оба)

    Timeline math для двух клипов по 5.0s с xf_dur=1.0:
      pad = 0.5s
      A_padded = 5.5s (5.0 + 0.5 tail)
      B_padded = 5.5s (0.5 head + 5.0)
      xfade(offset=4.5, duration=1.0) → output = 4.5 + 5.5 = 10.0s ✓
      Viewer: 0-4.5 чистый А | 4.5-5.0 А тает + Б freeze | 5.0-5.5 Б движение + А исчезает | 5.5-10.0 чистый Б

    Никакой потери контента. Клипы на диске не трогаются.
    Chunks at chunk_size clips to stay within Windows cmd-line limit.
    """
    clip_paths  = [Path(p) for p in clip_paths]
    output_path = Path(output_path)
    n = len(clip_paths)
    pad = xf_dur / 2.0  # 0.5s с каждой стороны

    if n == 1:
        shutil.copy(str(clip_paths[0]), str(output_path))
        return True

    # ── Chunk recursion for large sets ────────────────────────────────────────
    if n > chunk_size:
        _TEMP_DIR.mkdir(parents=True, exist_ok=True)
        chunks: list[Path] = []
        n_chunks = (n + chunk_size - 1) // chunk_size
        for ci, start in enumerate(range(0, n, chunk_size)):
            cclips = clip_paths[start : start + chunk_size]
            cdurs  = durations[start : start + chunk_size]
            cout   = _TEMP_DIR / f"xf_chunk_{ci:03d}.mp4"
            concat_clips_xfade(cclips, cdurs, cout, xf_dur, fps, chunk_size)
            chunks.append(cout)
            print(f"  xfade чанк {ci + 1}/{n_chunks}", flush=True)
        chunk_durs = [get_video_duration(str(c)) for c in chunks]
        return concat_clips_xfade(chunks, chunk_durs, output_path, xf_dur, fps, chunk_size)

    # ── Single filter_complex xfade chain с tpad handles ─────────────────────
    fc: list[str] = []

    # tpad для каждого клипа по позиции в run
    padded_durs: list[float] = []
    for i in range(n):
        base = f"[{i}:v]fps={fps},setpts=PTS-STARTPTS"
        if n == 1:
            fc.append(f"{base}[nv{i}]")
            padded_durs.append(durations[i])
        elif i == 0:
            # Первый: только tail pad (freeze последнего кадра)
            fc.append(f"{base},tpad=stop_mode=clone:stop_duration={pad:.3f}[nv{i}]")
            padded_durs.append(durations[i] + pad)
        elif i == n - 1:
            # Последний: только head pad (freeze первого кадра)
            fc.append(f"{base},tpad=start_mode=clone:start_duration={pad:.3f}[nv{i}]")
            padded_durs.append(durations[i] + pad)
        else:
            # Средние: head + tail pad
            fc.append(
                f"{base},"
                f"tpad=start_mode=clone:start_duration={pad:.3f}"
                f":stop_mode=clone:stop_duration={pad:.3f}[nv{i}]"
            )
            padded_durs.append(durations[i] + 2 * pad)

    # Build xfade chain используя padded_durs для правильного offset
    cumulative = padded_durs[0]
    prev_label = "nv0"

    for i in range(1, n):
        offset    = max(0.01, cumulative - xf_dur)
        out_label = f"xf{i}"
        fc.append(
            f"[{prev_label}][nv{i}]"
            f"xfade=transition=fade:"
            f"duration={xf_dur:.3f}:offset={offset:.3f}"
            f"[{out_label}]"
        )
        prev_label  = out_label
        cumulative += padded_durs[i] - xf_dur

    cmd = ["ffmpeg", "-y"]
    for p in clip_paths:
        cmd += ["-i", str(p)]
    cmd += [
        "-filter_complex", ";".join(fc),
        "-map", f"[{prev_label}]",
        "-c:v", GPU_ENCODER, *GPU_PARAMS,
        "-pix_fmt", "yuv420p", "-r", str(fps), "-an",
        "-loglevel", "warning",
        str(output_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        _fallback_counter["count"] += 1
        err = (result.stderr[-300:] if result.stderr else "")
        logging.warning(
            f"Fallback #{_fallback_counter['count']}: "
            f"concat_clips_xfade failed → simple_concat (err: {err})"
        )
        try:
            from pipeline_validator import log_fallback_transition
            log_fallback_transition(_fallback_counter["count"], "crossfade_xfade", err)
        except ImportError:
            pass
        print(f"  !! xfade concat error — fallback simple_concat "
              f"[total={_fallback_counter['count']}]", flush=True)
        _simple_concat(clip_paths, output_path)
    return True


def concat_crossfade_respecting_cuts(
    clip_paths,
    durations,
    output_path,
    xf_dur: float = 0.5,
    trans_seq: list = None,
    fps: int = 25,
) -> bool:
    """
    Crossfade с уважением к trans_seq:
      - Границы с type="cut" → hard cut (не xfade)
      - Остальные границы → xfade

    Клипы на переходных границах должны иметь handle = xf_dur в хвосте.
    Разбивает поток клипов на «runs» (группы без cuts), применяет
    concat_clips_xfade к каждому run, затем склеивает runs через simple_concat.

    Без этой функции concat_clips_xfade применяет xfade ко ВСЕМ границам,
    включая cut-границы, что съедает xf_dur × n_cuts секунд из таймлайна.
    """
    clip_paths  = [Path(p) for p in clip_paths]
    output_path = Path(output_path)
    n = len(clip_paths)

    if n == 1:
        import shutil as _sh
        _sh.copy(str(clip_paths[0]), str(output_path))
        return True

    if not trans_seq:
        return concat_clips_xfade(clip_paths, durations, output_path, xf_dur, fps)

    cut_indices = {i for i, t in enumerate(trans_seq) if t.get("type") == "cut"}
    if not cut_indices:
        return concat_clips_xfade(clip_paths, durations, output_path, xf_dur, fps)

    # Split into contiguous runs at cut boundaries
    runs_clips: list[list[Path]]  = []
    runs_durs:  list[list[float]] = []
    gc: list[Path]  = [clip_paths[0]]
    gd: list[float] = [durations[0]]

    for i, t in enumerate(trans_seq):
        if i in cut_indices:
            runs_clips.append(gc); runs_durs.append(gd)
            gc = [clip_paths[i + 1]]; gd = [durations[i + 1]]
        else:
            gc.append(clip_paths[i + 1]); gd.append(durations[i + 1])
    runs_clips.append(gc)
    runs_durs.append(gd)

    n_runs = len(runs_clips)
    n_xf   = sum(len(rc) - 1 for rc in runs_clips)
    n_cut  = len(cut_indices)
    print(f"  [xfade+cut] {n} клипов → {n_runs} runs ({n_xf} xfade, {n_cut} cuts)",
          flush=True)

    if n_runs == 1:
        return concat_clips_xfade(runs_clips[0], runs_durs[0], output_path, xf_dur, fps)

    _TEMP_DIR.mkdir(parents=True, exist_ok=True)
    temp_files: list[Path] = []
    for ri, (rc, rd) in enumerate(zip(runs_clips, runs_durs)):
        if len(rc) == 1:
            temp_files.append(rc[0])
        else:
            run_out = _TEMP_DIR / f"xf_run_{ri:03d}.mp4"
            concat_clips_xfade(rc, rd, run_out, xf_dur, fps)
            temp_files.append(run_out)

    _simple_concat(temp_files, output_path)

    for f in temp_files:
        if f.name.startswith("xf_run_") and f.exists():
            f.unlink(missing_ok=True)

    return True


def _crossfade_chunk(clip_paths, durations, output_path, duration=0.5, fps=25):
    """
    Склейка N клипов с crossfade по opacity (ease-in-out, без xfade/пикселизации).
    Строит единый filter_complex с trim+split+blend+concat.
    w = 0.5 - 0.5·cos(π·T/dur) — плавный ease-in-out.

    Fast path: если все клипы одной длины (±0.1s) — используем -c copy без перекодирования.
    """
    n = len(clip_paths)
    if n == 1:
        shutil.copy(str(clip_paths[0]), str(output_path))
        return True


    d = duration
    w = f"0.5-0.5*cos(3.14159265*T/{d:.4f})"
    fc = []
    concat_labels = []

    for i in range(n):
        dur_i = durations[i]
        fc.append(f"[{i}:v]fps={fps},setpts=PTS-STARTPTS[nv{i}]")

        if i == 0:
            main_end = max(0.04, dur_i - d)
            fc.append(f"[nv{i}]split[nv{i}a][nv{i}b]")
            fc.append(f"[nv{i}a]trim=0:{main_end:.4f},setpts=PTS-STARTPTS[m{i}]")
            fc.append(f"[nv{i}b]trim={main_end:.4f},setpts=PTS-STARTPTS[e{i}]")
            concat_labels.append(f"m{i}")

        elif i == n - 1:
            fc.append(f"[nv{i}]split[nv{i}a][nv{i}b]")
            fc.append(f"[nv{i}a]trim=0:{d:.4f},setpts=PTS-STARTPTS[s{i}]")
            fc.append(f"[nv{i}b]trim={d:.4f},setpts=PTS-STARTPTS[m{i}]")
            fc.append(f"[e{i-1}][s{i}]blend=all_expr='A*(1-({w}))+B*({w})'[b{i}]")
            concat_labels.append(f"b{i}")
            concat_labels.append(f"m{i}")

        else:
            main_start = d
            main_end   = max(d + 0.04, dur_i - d)
            fc.append(f"[nv{i}]split=3[nv{i}a][nv{i}b][nv{i}c]")
            fc.append(f"[nv{i}a]trim=0:{d:.4f},setpts=PTS-STARTPTS[s{i}]")
            fc.append(f"[nv{i}b]trim={main_start:.4f}:{main_end:.4f},setpts=PTS-STARTPTS[m{i}]")
            fc.append(f"[nv{i}c]trim={main_end:.4f},setpts=PTS-STARTPTS[e{i}]")
            fc.append(f"[e{i-1}][s{i}]blend=all_expr='A*(1-({w}))+B*({w})'[b{i}]")
            concat_labels.append(f"b{i}")
            concat_labels.append(f"m{i}")

    n_segs     = len(concat_labels)
    inputs_str = "".join(f"[{lbl}]" for lbl in concat_labels)
    fc.append(f"{inputs_str}concat=n={n_segs}:v=1:a=0[out]")

    cmd = ["ffmpeg", "-y"]
    for p in clip_paths:
        cmd += ["-i", str(p)]
    cmd += [
        "-filter_complex", ";".join(fc),
        "-map", "[out]",
        "-c:v", GPU_ENCODER, *GPU_PARAMS,
        "-pix_fmt", "yuv420p", "-an", "-r", str(fps),
        "-loglevel", "warning",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        _fallback_counter["count"] += 1
        err = result.stderr[-200:] if isinstance(result.stderr, str) else result.stderr.decode("utf-8", "replace")[-200:]
        logging.warning(
            f"Fallback #{_fallback_counter['count']}: crossfade disabled → simple_concat (stderr: {err})"
        )
        try:
            from pipeline_validator import log_fallback_transition
            log_fallback_transition(_fallback_counter["count"], "crossfade", err)
        except ImportError:
            pass
        print(f"  !! crossfade chunk error — fallback simple_concat [total={_fallback_counter['count']}]", flush=True)
        _simple_concat(clip_paths, output_path)
    return True


def _fadeblack_chunk(clip_paths, durations, output_path, fade_dur=0.2, color="black"):
    """
    Склейка клипов с fade-to-color / fade-from-color в один проход.

    color="black"   → стандартный fadeblack (FR)
    color="0xEAD5B0" → пастельный бежевый (ES Religion)

    Один FFmpeg вызов: fade per-input → concat video filter → NVENC.
    Нет промежуточных файлов, нет потери длительности (нет xfade overlap).
    Линейный fade: выглядит гладко при fade_dur=0.5s, не требует кастомных кривых.
    """
    n = len(clip_paths)
    if n == 1:
        shutil.copy2(str(clip_paths[0]), str(output_path))
        return True

    # Получить реальные длительности
    actual_durs = []
    for clip, dur in zip(clip_paths, durations):
        d = get_video_duration(str(clip))
        actual_durs.append(d if d > 0 else dur)

    cmd = ["ffmpeg", "-y"]
    for p in clip_paths:
        cmd.extend(["-i", str(p)])

    filter_parts = []
    for i, dur in enumerate(actual_durs):
        first = (i == 0)
        last  = (i == n - 1)
        fi    = 0.0 if first else min(fade_dur, dur * 0.3)
        fo    = 0.0 if last  else min(fade_dur, dur * 0.3)
        fo_st = max(0.0, dur - fo - 0.01)

        vf = []
        color_arg = f":color={color}" if color != "black" else ""
        if fi > 0:
            vf.append(f"fade=t=in:st=0:d={fi:.3f}{color_arg}")
        if fo > 0:
            vf.append(f"fade=t=out:st={fo_st:.3f}:d={fo:.3f}{color_arg}")

        if vf:
            filter_parts.append(f"[{i}:v]{','.join(vf)}[v{i}]")
        else:
            filter_parts.append(f"[{i}:v]null[v{i}]")

    concat_inputs = "".join(f"[v{i}]" for i in range(n))
    filter_parts.append(f"{concat_inputs}concat=n={n}:v=1:a=0,format=yuv420p[out]")

    cmd += [
        "-filter_complex", ";".join(filter_parts),
        "-map", "[out]",
        "-c:v", GPU_ENCODER, *GPU_PARAMS, "-pix_fmt", "yuv420p", "-an",
        "-loglevel", "warning", str(output_path),
    ]

    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        err = result.stderr.decode("utf-8", "replace")[-300:]
        logging.warning(f"_fadeblack_chunk NVENC failed, retrying with libx264: {err}")
        # Retry with CPU encoder (same filter_complex, preserves fade effect)
        cmd_cpu = []
        for arg in cmd:
            if arg == GPU_ENCODER:
                cmd_cpu.append("libx264")
            elif arg in GPU_PARAMS:
                continue
            else:
                cmd_cpu.append(arg)
        # Replace GPU_PARAMS block: after libx264 add cpu params
        cpu_params = ["-preset", "faster", "-crf", "20"]
        # Rebuild cleanly
        cmd_cpu = ["ffmpeg", "-y"]
        for p in clip_paths:
            cmd_cpu.extend(["-i", str(p)])
        cmd_cpu += [
            "-filter_complex", ";".join(filter_parts),
            "-map", "[out]",
            "-c:v", "libx264", "-preset", "faster", "-crf", "20",
            "-pix_fmt", "yuv420p", "-an",
            "-loglevel", "warning", str(output_path),
        ]
        result2 = subprocess.run(cmd_cpu, capture_output=True)
        if result2.returncode != 0:
            err2 = result2.stderr.decode("utf-8", "replace")[-300:]
            logging.warning(f"_fadeblack_chunk libx264 also failed: {err2}")
            _simple_concat(clip_paths, output_path)
    return True


def _gray_wipe_fast(clip_paths, durations, output_path, duration=0.4, fps=25):
    """
    FAST gray_wipe: only encode the ~0.4s overlap segments via blend filter.
    Clip bodies are encoded with ultrafast preset (parallel, 8 workers).
    Final concat uses -c copy → ~10-20x faster than _gray_wipe_chunk.

    Steps:
      1. Parallel encode of N-1 transition segments (blend last-d from A + first-d from B)
      2. Parallel ultrafast re-encode of N body segments (each clip minus overlap tails)
      3. Concat: body[0] + trans[0] + body[1] + trans[1] + ... + body[N-1]
    """
    n = len(clip_paths)
    if n == 1:
        shutil.copy(str(clip_paths[0]), str(output_path))
        return True

    d     = duration
    uid   = abs(hash(str(output_path))) % 100000
    tmp   = _TEMP_DIR
    tmp.mkdir(parents=True, exist_ok=True)

    wipe_expr = (
        f"clip(((W+100)*(1-cos(3.14159265*T/{d:.4f}))/2-X)/100,0,1)"
        f"*B+(1-clip(((W+100)*(1-cos(3.14159265*T/{d:.4f}))/2-X)/100,0,1))*A"
    )

    def _encode_transition(i):
        out  = tmp / f"gw_t_{uid}_{i:04d}.mp4"
        da   = durations[i]
        tail_start = max(0.0, da - d)
        # Read full clip_i (no seek = no GOP decode overhead), trim in filtergraph
        cmd  = [
            "ffmpeg", "-y",
            "-i", str(clip_paths[i]),
            "-t", f"{d:.4f}", "-i", str(clip_paths[i + 1]),
            "-filter_complex",
            # A: extract tail, reset PTS to 0
            f"[0:v]fps={fps},format=yuv420p,"
            f"trim={tail_start:.4f}:{da:.4f},setpts=PTS-STARTPTS[ta];"
            # B: read first d seconds from input B, reset PTS
            f"[1:v]fps={fps},format=yuv420p,setpts=PTS-STARTPTS[tb];"
            f"[ta][tb]blend={wipe_blend}[out]",
            "-map", "[out]",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-an", "-r", str(fps),
            "-loglevel", "error",
            str(out),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"transition {i} failed: {result.stderr[-200:]}")
        return out

    def _encode_body(i):
        out         = tmp / f"gw_b_{uid}_{i:04d}.mp4"
        has_left    = (i > 0)
        has_right   = (i < n - 1)
        body_start  = d      if has_left  else 0.0
        body_end    = max(body_start + 0.04,
                          durations[i] - (d if has_right else 0.0))
        # Read full clip (no seek), trim in filtergraph to avoid GOP decode overhead
        cmd = [
            "ffmpeg", "-y",
            "-i", str(clip_paths[i]),
            "-vf", f"trim={body_start:.4f}:{body_end:.4f},setpts=PTS-STARTPTS,fps={fps},format=yuv420p",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "22",
            "-an", "-loglevel", "error",
            str(out),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"body {i} failed: {result.stderr[-200:]}")
        return out

    # Separate pools so transitions (blend+encode) and bodies (decode+encode) run in parallel
    with ThreadPoolExecutor(max_workers=8) as tx, ThreadPoolExecutor(max_workers=8) as bx:
        trans_futures = [tx.submit(_encode_transition, i) for i in range(n - 1)]
        body_futures  = [bx.submit(_encode_body,       i) for i in range(n)]
        trans_outs    = [f.result() for f in trans_futures]
        body_outs     = [f.result() for f in body_futures]

    # Interleave: body[0], trans[0], body[1], trans[1], ..., body[n-1]
    concat_parts: list[Path] = []
    for i in range(n):
        concat_parts.append(body_outs[i])
        if i < n - 1:
            concat_parts.append(trans_outs[i])

    concat_file = str(tmp / f"gw_concat_{uid}.txt")
    with open(concat_file, "w", encoding="utf-8") as f:
        for p in concat_parts:
            f.write(f"file '{os.path.abspath(str(p))}'\n")

    result = subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", concat_file,
        "-c", "copy",
        "-loglevel", "warning",
        str(output_path),
    ], capture_output=True, text=True)

    if result.returncode != 0:
        _fallback_counter["count"] += 1
        print(f"  !! gw_fast concat error — fallback gw_chunk: {result.stderr[-200:]}", flush=True)
        return _gray_wipe_chunk(clip_paths, durations, output_path, duration, fps)

    return True


def _gray_wipe_chunk(clip_paths, durations, output_path, duration=0.4, fps=25):
    """
    Склейка N клипов с позиционным вайпом через серый (gray_wipe).

    На каждой границе: левая половина кадра уже показывает следующий клип,
    правая — ещё предыдущий. Граница движется слева направо с ease-in-out.
    Soft edge 100px. Без xfade — чистый blend, нет пикселизации.

    FREEZE FRAME PAD: хвост каждого клипа (d секунд) генерируется как заморозка
    последнего кадра через tpad. Реальный контент клипа не обрезается — тело
    показывает полное содержимое от head_d до конца. Длина видео = sum(durations).

    Формула: wipe_pos = (W+100)*(1-cos(PI*T/dur))/2 - 50
             alpha(X) = clip((wipe_pos - X) / 100, 0, 1)
             out = B*alpha + A*(1-alpha)
    """
    n = len(clip_paths)
    if n == 1:
        shutil.copy(str(clip_paths[0]), str(output_path))
        return True

    d   = duration
    fpf = 1.0 / fps  # длительность одного кадра
    # Beige wipe: позиция вайпа + мягкий бежевый ореол на границе
    # RGB(245,225,200) → YCbCr(228,112,140) по BT.601
    _wp   = f"((W+100)*(1-cos(3.14159265*T/{d:.4f}))/2)"
    _al   = f"clip(({_wp}-X)/100,0,1)"
    _bell = f"({_al}*(1-{_al})*4*0.55)"   # пик 0.55 в центре мягкого края
    _base = f"({_al}*B+(1-{_al})*A)"
    wipe_y  = f"({_bell}*228+(1-{_bell})*{_base})"
    wipe_cb = f"({_bell}*112+(1-{_bell})*{_base})"
    wipe_cr = f"({_bell}*140+(1-{_bell})*{_base})"
    wipe_blend = f"c0_expr='{wipe_y}':c1_expr='{wipe_cb}':c2_expr='{wipe_cr}'"

    fc = []
    concat_labels = []

    for i in range(n):
        dur_i  = durations[i]
        # head_d — сколько секунд "головы" клипа идёт в предыдущий переход (B-сторона)
        # Ограничиваем: не больше 45% длины клипа, минимум 0.04s
        head_d = min(d, max(0.04, dur_i * 0.45)) if i > 0 else 0.0
        # Позиция последнего кадра для freeze tail (2 кадра до конца)
        last_t = max(head_d, dur_i - 2 * fpf)

        fc.append(f"[{i}:v]fps={fps},format=yuv420p,setpts=PTS-STARTPTS[nv{i}]")

        if i == 0:
            # Первый клип: полное тело + freeze tail для следующего перехода
            fc.append(f"[nv{i}]split[nv{i}a][nv{i}b]")
            fc.append(f"[nv{i}a]trim=0:{dur_i:.4f},setpts=PTS-STARTPTS[m{i}]")
            fc.append(
                f"[nv{i}b]trim={last_t:.4f}:{dur_i:.4f},setpts=PTS-STARTPTS,"
                f"tpad=stop=-1:stop_mode=clone:stop_duration={d:.4f},"
                f"trim=0:{d:.4f},setpts=PTS-STARTPTS[e{i}]"
            )
            concat_labels.append(f"m{i}")

        elif i == n - 1:
            # Последний клип: голова идёт в предыдущий переход, тело = всё остальное
            fc.append(f"[nv{i}]split[nv{i}a][nv{i}b]")
            fc.append(f"[nv{i}a]trim=0:{head_d:.4f},setpts=PTS-STARTPTS[s{i}]")
            fc.append(f"[nv{i}b]trim={head_d:.4f},setpts=PTS-STARTPTS[m{i}]")
            fc.append(f"[e{i-1}][s{i}]blend={wipe_blend}[b{i}]")
            concat_labels += [f"b{i}", f"m{i}"]

        else:
            # Средний клип: голова → предыдущий переход, тело = полный контент от head_d до конца,
            # freeze tail → следующий переход
            body_end = max(head_d + 0.04, dur_i)
            fc.append(f"[nv{i}]split=3[nv{i}a][nv{i}b][nv{i}c]")
            fc.append(f"[nv{i}a]trim=0:{head_d:.4f},setpts=PTS-STARTPTS[s{i}]")
            fc.append(f"[nv{i}b]trim={head_d:.4f}:{body_end:.4f},setpts=PTS-STARTPTS[m{i}]")
            fc.append(
                f"[nv{i}c]trim={last_t:.4f}:{dur_i:.4f},setpts=PTS-STARTPTS,"
                f"tpad=stop=-1:stop_mode=clone:stop_duration={d:.4f},"
                f"trim=0:{d:.4f},setpts=PTS-STARTPTS[e{i}]"
            )
            fc.append(f"[e{i-1}][s{i}]blend={wipe_blend}[b{i}]")
            concat_labels += [f"b{i}", f"m{i}"]

    inputs_str = "".join(f"[{lbl}]" for lbl in concat_labels)
    fc.append(f"{inputs_str}concat=n={len(concat_labels)}:v=1:a=0[out]")

    cmd = ["ffmpeg", "-y"]
    for p in clip_paths:
        if GPU_ENCODER_FAST == "h264_nvenc":
            cmd += ["-hwaccel", "auto"]
        cmd += ["-i", str(p)]
    cmd += [
        "-filter_complex", ";".join(fc),
        "-map", "[out]",
        "-c:v", GPU_ENCODER_FAST, *GPU_PARAMS_FAST,
        "-pix_fmt", "yuv420p", "-an", "-r", str(fps),
        "-loglevel", "warning",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        _fallback_counter["count"] += 1
        err = result.stderr[-300:] if isinstance(result.stderr, str) else result.stderr.decode("utf-8", "replace")[-300:]
        logging.warning(f"Fallback #{_fallback_counter['count']}: gray_wipe disabled → simple_concat (stderr: {err})")
        print(f"  !! gray_wipe chunk error — fallback simple_concat [total={_fallback_counter['count']}]", flush=True)
        _simple_concat(clip_paths, output_path)
    return True


def _concat_chunk(clip_paths, durations, output_path,
                  transition, duration, trans_seq=None):
    """
    Склеить один чанк клипов одной командой FFmpeg (filter_complex xfade).

    crossfade (DE):
      opacity blend с ease-in-out (trim+split+blend+concat). Без пикселизации.

    cross_zoom:
      gblur=sigma=18 с enable='' на зонах перехода каждого клипа + dissolve.

    glitch_flash (FR):
      50% → rgbashift хроматик (±10px R и B каналы) + fadeblack
      50% → plain fadeblack без пре-обработки

    Flash types (fadewhite/fadeblack/fade в trans_seq): без пре-обработки, чистый xfade.
    """
    n = len(clip_paths)
    if n == 1:
        shutil.copy(str(clip_paths[0]), str(output_path))
        return True

    # crossfade — отдельный путь через blend (без xfade)
    if transition == "crossfade":
        return _crossfade_chunk(clip_paths, durations, output_path, duration)

    # fadeblack — fade-to-black через vf fade (без xfade, работает с NVENC)
    # fade_dur = duration/2: 1s total → 0.5s fade-out + 0.5s fade-in = 1s черного экрана
    if transition == "fadeblack":
        return _fadeblack_chunk(clip_paths, durations, output_path, fade_dur=duration / 2)

    # fadebeige — fade-to-pastel-beige (ES Religion стиль)
    if transition == "fadebeige":
        return _fadeblack_chunk(clip_paths, durations, output_path,
                                fade_dur=duration / 2, color="0xEAD5B0")

    # gray_wipe — позиционный вайп через серый (без xfade/пикселизации)
    if transition == "gray_wipe":
        return _gray_wipe_chunk(clip_paths, durations, output_path, duration)

    n_bdrs = n - 1

    # Строим per-boundary последовательность
    if trans_seq and len(trans_seq) == n_bdrs:
        seq = []
        for b in trans_seq:
            t, d = b["type"], b["dur"]
            if t == "glitch_flash":
                d = min(d, _GLITCH_MAX_DUR)
            if t in _FLASH_TYPES:
                d = min(d, _FLASH_DUR_RANGE[1])
            seq.append({"type": t, "dur": max(0.04, d)})
    else:
        d0 = min(duration, _GLITCH_MAX_DUR) if transition == "glitch_flash" else duration
        seq = [{"type": transition, "dur": d0}] * n_bdrs

    filter_parts = []
    D = duration  # ширина зоны blur

    # ── Пре-обработка в зависимости от типа ──────────────────────────────────
    if transition == "cross_zoom":
        # gblur только в зонах перехода через enable= (остаток клипа — острый)
        for i in range(n):
            d = durations[i]
            if i == 0:
                # только конец
                enable = f"gte(t,{max(0.0, d - D):.3f})"
            elif i == n - 1:
                # только начало
                enable = f"lte(t,{D:.3f})"
            else:
                # начало И конец
                enable = f"lte(t,{D:.3f})+gte(t,{max(0.0, d - D):.3f})"
            filter_parts.append(
                f"[{i}:v]gblur=sigma={_CROSS_ZOOM_SIGMA}:enable='{enable}'[g{i}]"
            )
        in_labels = [f"g{i}" for i in range(n)]

    elif transition == "glitch_flash":
        use_chroma = random.random() < 0.5
        if use_chroma:
            D_g = min(D, _GLITCH_MAX_DUR)
            for i in range(n):
                d  = durations[i]
                rh = _GLITCH_RH if i % 2 == 0 else -_GLITCH_RH
                bh = -rh
                if i == 0:
                    enable = f"gte(t,{max(0.0, d - D_g):.3f})"
                elif i == n - 1:
                    enable = f"lte(t,{D_g:.3f})"
                else:
                    enable = f"lte(t,{D_g:.3f})+gte(t,{max(0.0, d - D_g):.3f})"
                filter_parts.append(
                    f"[{i}:v]rgbashift=rh={rh}:bh={bh}:enable='{enable}'[g{i}]"
                )
            # Помечаем glitch_flash границы как glitch_chroma
            # (копируем seq чтобы не мутировать caller's list)
            seq = [
                {"type": "glitch_chroma" if b["type"] == "glitch_flash" else b["type"],
                 "dur": b["dur"]}
                for b in seq
            ]
            in_labels = [f"g{i}" for i in range(n)]
        else:
            # Plain fadeblack — без пре-обработки
            in_labels = [f"{i}:v" for i in range(n)]

    else:
        # dissolve / flash-типы — без пре-обработки
        in_labels = [f"{i}:v" for i in range(n)]

    # ── xfade цепочка с накопительным offset ─────────────────────────────────
    sum_durs = durations[0]
    sum_tds  = 0.0

    for i, b in enumerate(seq):
        td    = b["dur"]
        xname = _XFADE_MAP.get(b["type"], "dissolve")

        sum_tds  += td
        offset_i  = max(0.01, sum_durs - sum_tds)

        lhs = in_labels[0] if i == 0 else f"v{i}"
        rhs = in_labels[i + 1]
        out = f"v{i + 1}"

        filter_parts.append(
            f"[{lhs}][{rhs}]xfade=transition={xname}:"
            f"duration={td:.3f}:offset={offset_i:.3f}[{out}]"
        )
        sum_durs += durations[i + 1]

    filter_complex = ";".join(filter_parts)
    last_label     = f"v{n - 1}"

    cmd = ["ffmpeg", "-y"]
    for p in clip_paths:
        cmd += ["-i", str(p)]
    cmd += [
        "-filter_complex", filter_complex,
        "-map", f"[{last_label}]",
        "-c:v", GPU_ENCODER, *GPU_PARAMS,
        "-pix_fmt", "yuv420p", "-r", "25", "-an",
        "-loglevel", "warning",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        _fallback_counter["count"] += 1
        err = result.stderr[-200:] if isinstance(result.stderr, str) else result.stderr.decode("utf-8", "replace")[-200:]
        logging.warning(
            f"Fallback #{_fallback_counter['count']}: xfade ({transition}) disabled → simple_concat (stderr: {err})"
        )
        try:
            from pipeline_validator import log_fallback_transition
            log_fallback_transition(_fallback_counter["count"], transition, err)
        except ImportError:
            pass
        print(f"  !! xfade ({transition}) error — fallback simple_concat [total={_fallback_counter['count']}]", flush=True)
        _simple_concat(clip_paths, output_path)
    return True


def concat_all_with_transitions(
    clip_paths,
    output_path,
    transition: str   = "slideleft",
    duration:   float = 0.5,
    trans_seq:  list  = None,
) -> bool:
    """
    Склеить все клипы с xfade-переходами.
    Разбивает на чанки по 20 при большом количестве клипов.
    trans_seq: список {"type": str, "dur": float}, длина = len(clip_paths) - 1.
    """
    clip_paths  = [Path(p) for p in clip_paths]
    output_path = Path(output_path)

    # ── Фильтрация: убираем отсутствующие файлы ───────────────────────────────
    missing = [p for p in clip_paths if not p.exists()]
    if missing:
        print(f"  ⚠ concat_all_with_transitions: {len(missing)} клипов не найдено, пропускаем:", flush=True)
        for m in missing[:10]:
            print(f"    {m.name}", flush=True)
        clip_paths = [p for p in clip_paths if p.exists()]
        # Обрезаем trans_seq под новый размер (убираем записи для пропущенных позиций)
        if trans_seq and len(trans_seq) >= len(clip_paths):
            trans_seq = trans_seq[:max(0, len(clip_paths) - 1)]

    if not clip_paths:
        raise ValueError("concat_all_with_transitions: все клипы отсутствуют")
    if len(clip_paths) == 1:
        shutil.copy(str(clip_paths[0]), str(output_path))
        return True

    # ── Pre-validation ────────────────────────────────────────────────────────
    try:
        from pipeline_validator import get_stats
        get_stats().transitions_valid = True
    except ImportError:
        pass

    n = len(clip_paths)

    with ThreadPoolExecutor(max_workers=8) as ex:
        durations = list(ex.map(get_video_duration, [str(p) for p in clip_paths]))

    # ── Fast path: crossfade via built-in xfade (10–20× faster than blend) ────
    if transition == "crossfade":
        print(f"  [xfade] {n} клипов, dur={duration:.2f}s → {output_path.name}", flush=True)
        return concat_crossfade_respecting_cuts(
            clip_paths, durations, output_path,
            xf_dur=duration, trans_seq=trans_seq, fps=25,
        )

    if transition == "gray_wipe":
        # ── gray_wipe: split at "cut" boundaries, _gray_wipe_fast for each zone ──
        cut_indices: set[int] = set()
        if trans_seq:
            cut_indices = {i for i, t in enumerate(trans_seq) if t.get("type") == "cut"}

        if cut_indices:
            # Split clips into contiguous wipe-zones at cut boundaries
            zones_clips: list[list[Path]] = []
            zones_durs:  list[list[float]] = []
            g_c: list[Path]  = [clip_paths[0]]
            g_d: list[float] = [durations[0]]
            for i, t in enumerate(trans_seq):
                if i in cut_indices:
                    zones_clips.append(g_c); zones_durs.append(g_d)
                    g_c = [clip_paths[i + 1]]; g_d = [durations[i + 1]]
                else:
                    g_c.append(clip_paths[i + 1]); g_d.append(durations[i + 1])
            zones_clips.append(g_c); zones_durs.append(g_d)

            n_zones = len(zones_clips)
            n_wipe  = sum(1 for zc in zones_clips if len(zc) > 1)
            n_cut   = len(cut_indices)
            print(f"  [gray_wipe] {n} клипов → {n_zones} зон "
                  f"({n_wipe} с вайпом, {n_cut} хардкат) → {output_path.name}", flush=True)

            _gw_tmp = _TEMP_DIR
            _gw_tmp.mkdir(parents=True, exist_ok=True)
            CHUNK_SIZE = 20

            def _process_one_zone(zi_zc_zd):
                zi, zc, zd = zi_zc_zd
                if len(zc) == 1:
                    return zc[0]
                zone_out = _gw_tmp / f"gw_zone_{zi:03d}.mp4"
                if len(zc) <= CHUNK_SIZE:
                    _gray_wipe_chunk(zc, zd, zone_out, duration)
                else:
                    # Parallel sub-chunks for large zones
                    sub_args = [
                        (ci, zc[j:j+CHUNK_SIZE], zd[j:j+CHUNK_SIZE])
                        for ci, j in enumerate(range(0, len(zc), CHUNK_SIZE))
                    ]

                    def _sub(args):
                        ci, sc, sd = args
                        scout = _gw_tmp / f"gw_zone_{zi:03d}_{ci:03d}.mp4"
                        _gray_wipe_chunk(sc, sd, scout, duration)
                        return scout

                    with ThreadPoolExecutor(max_workers=min(len(sub_args), 6)) as sex:
                        sub_chunks = list(sex.map(_sub, sub_args))
                    if len(sub_chunks) == 1:
                        shutil.copy(str(sub_chunks[0]), str(zone_out))
                    else:
                        sc_durs = [get_video_duration(str(c)) for c in sub_chunks]
                        _gray_wipe_chunk(sub_chunks, sc_durs, zone_out, duration)
                print(f"  OK зона {zi + 1}/{n_zones} ({len(zc)} клипов)", flush=True)
                return zone_out

            # Process all zones in parallel
            zone_args = [(zi, zc, zd) for zi, (zc, zd) in enumerate(zip(zones_clips, zones_durs))]
            with ThreadPoolExecutor(max_workers=min(n_wipe, 4)) as zex:
                zone_outs = list(zex.map(_process_one_zone, zone_args))

            if len(zone_outs) == 1:
                shutil.copy(str(zone_outs[0]), str(output_path))
            else:
                _simple_concat([str(p) for p in zone_outs], str(output_path))
            print(f"  OK все {n} клипов: {Path(output_path).name}", flush=True)
            return True

        # No cuts — full gray_wipe on all clips
        print(f"  [gray_wipe] {n} клипов, dur={duration:.2f}s → {output_path.name}", flush=True)
        CHUNK_SIZE = 20
        if n <= CHUNK_SIZE:
            return _gray_wipe_chunk(clip_paths, durations, output_path, duration)
        _TEMP_DIR.mkdir(parents=True, exist_ok=True)
        def _sub_nc(args):
            ci, sc, sd = args
            scout = _TEMP_DIR / f"chunk_{ci:03d}.mp4"
            _gray_wipe_chunk(sc, sd, scout, duration)
            return scout
        sub_args = [
            (ci, clip_paths[j:j+CHUNK_SIZE], durations[j:j+CHUNK_SIZE])
            for ci, j in enumerate(range(0, n, CHUNK_SIZE))
        ]
        with ThreadPoolExecutor(max_workers=min(len(sub_args), 6)) as sex:
            chunks = list(sex.map(_sub_nc, sub_args))
        if len(chunks) == 1:
            shutil.copy(str(chunks[0]), str(output_path))
        else:
            chunk_durs2 = [get_video_duration(str(c)) for c in chunks]
            _gray_wipe_chunk(chunks, chunk_durs2, output_path, duration)
        print(f"  OK все {n} клипов: {Path(output_path).name}", flush=True)
        return True

    # ── Legacy chunk-based path for other transitions ─────────────────────────
    CHUNK_SIZE = 20
    if n <= CHUNK_SIZE:
        return _concat_chunk(clip_paths, durations, output_path,
                             transition, duration, trans_seq)

    _TEMP_DIR.mkdir(parents=True, exist_ok=True)
    chunks: list[Path] = []
    n_chunks = (n + CHUNK_SIZE - 1) // CHUNK_SIZE
    for ci, i in enumerate(range(0, n, CHUNK_SIZE)):
        chunk_clips = clip_paths[i : i + CHUNK_SIZE]
        chunk_durs  = durations[i : i + CHUNK_SIZE]
        chunk_out   = _TEMP_DIR / f"chunk_{ci:03d}.mp4"
        chunk_seq   = trans_seq[i : i + len(chunk_clips) - 1] if trans_seq else None
        _concat_chunk(chunk_clips, chunk_durs, chunk_out,
                      transition, duration, chunk_seq)
        chunks.append(chunk_out)
        print(f"  OK чанк {ci + 1}/{n_chunks}", flush=True)

    if len(chunks) == 1:
        shutil.copy(str(chunks[0]), str(output_path))
    else:
        chunk_durs2 = [get_video_duration(str(c)) for c in chunks]
        _concat_chunk(chunks, chunk_durs2, output_path, transition, duration)

    print(f"  OK все {n} клипов: {Path(output_path).name}", flush=True)
    return True


# ── Legacy aliases ────────────────────────────────────────────────────────────

def slide_transition(clip1_path, clip2_path, output_path, duration=0.5):
    clip1_path  = Path(clip1_path)
    clip2_path  = Path(clip2_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    clip1_dur = get_video_duration(str(clip1_path))
    offset    = clip1_dur - duration
    r = subprocess.run([
        "ffmpeg", "-y",
        "-i", str(clip1_path), "-i", str(clip2_path),
        "-filter_complex",
        f"[0:v][1:v]xfade=transition=slideleft:"
        f"duration={duration:.3f}:offset={offset:.3f}[out]",
        "-map", "[out]",
        "-c:v", GPU_ENCODER, *GPU_PARAMS,
        "-pix_fmt", "yuv420p", "-r", "25", "-an",
        str(output_path),
    ], capture_output=True)
    return r.returncode == 0

def glitch_transition(c1, c2, out, duration=0.5):
    return intro_to_main_transition(c1, c2, out, duration)

def slide_motionblur_transition(c1, c2, out, duration=0.5):
    return slide_transition(c1, c2, out, duration)

def simple_slide(c1, c2, out, duration=0.5):
    return slide_transition(c1, c2, out, duration)
