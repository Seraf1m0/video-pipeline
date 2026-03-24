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
import os
import random
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# ── Утилиты ──────────────────────────────────────────────────────────────────

def get_audio_duration(path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", str(path)],
        capture_output=True, text=True)
    try:
        return float(json.loads(r.stdout)["format"]["duration"])
    except Exception:
        return 0.0


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


GPU_ENCODER, GPU_PARAMS = get_gpu_encoder()


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
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
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
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
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


def _final_concat(parts, output_path):
    """Склейка через concat demuxer."""
    concat_f = "temp/_concat_list.txt"
    with open(concat_f, "w") as f:
        for p in parts:
            if Path(p).exists() and Path(p).stat().st_size > 1000:
                f.write(f"file '{os.path.abspath(str(p))}'\n")
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_f,
        "-c:v", GPU_ENCODER, *GPU_PARAMS,
        "-pix_fmt", "yuv420p", "-an", str(output_path),
    ], check=True)


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
    Path("temp").mkdir(exist_ok=True)

    fps       = 25
    clip1_dur = get_video_duration(str(clip1_path))
    c1_main   = max(0.04, clip1_dur - duration)

    t_c1main = "temp/cf_c1main.mp4"
    t_blend  = "temp/cf_blend.mp4"
    t_c2main = "temp/cf_c2main.mp4"

    # ease-in-out: w плавно идёт 0→1 через cosine
    w  = f"0.5-0.5*cos(3.14159265*T/{duration:.4f})"
    fc = (
        f"[0:v]fps={fps},setpts=PTS-STARTPTS[a];"
        f"[1:v]fps={fps},setpts=PTS-STARTPTS[b];"
        f"[a][b]blend=all_expr='A*(1-({w}))+B*({w})'[out]"
    )

    try:
        # 1. clip1 main — до зоны перехода
        subprocess.run([
            "ffmpeg", "-y", "-i", str(clip1_path),
            "-t", f"{c1_main:.4f}",
            "-vf", f"fps={fps}",
            "-c:v", GPU_ENCODER, *GPU_PARAMS,
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
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-pix_fmt", "yuv420p", "-an", "-r", str(fps), t_blend,
        ], check=True, capture_output=True)

        # 3. clip2 main — остаток после зоны перехода
        subprocess.run([
            "ffmpeg", "-y", "-i", str(clip2_path),
            "-ss", f"{duration:.4f}",
            "-vf", f"fps={fps}",
            "-c:v", GPU_ENCODER, *GPU_PARAMS,
            "-pix_fmt", "yuv420p", "-an", t_c2main,
        ], check=True, capture_output=True)

        _final_concat([t_c1main, t_blend, t_c2main], output_path)
        print(f"  OK crossfade (opacity ease-in-out): {output_path.name}", flush=True)
        return True

    except subprocess.CalledProcessError as e:
        print(f"  !! crossfade fail: {e}", flush=True)
        return False


# ── FALLBACK: intro→main (gblur dissolve) ────────────────────────────────────

def intro_to_main_transition(intro_path, main_path,
                              output_path, duration=0.75):
    """
    Fallback для обоих каналов: сильный gblur на зонах перехода + dissolve.
    Никаких zoompan, никаких артефактов.
    """
    intro_path  = Path(intro_path)
    main_path   = Path(main_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    Path("temp").mkdir(exist_ok=True)

    intro_dur = get_video_duration(str(intro_path))
    offset    = max(0.1, intro_dur - duration)
    fps       = 25
    SIGMA     = 30

    ib = "temp/it_ib.mp4"
    iz = "temp/it_iz.mp4"
    mz = "temp/it_mz.mp4"
    ma = "temp/it_ma.mp4"
    bm = "temp/it_bm.mp4"

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
        _final_concat([ib, bm, ma], output_path)

        print(f"  OK blur dissolve: {output_path.name}", flush=True)
        return True

    except subprocess.CalledProcessError as e:
        print(f"  !! intro_to_main fallback concat: {e}", flush=True)
        concat_f = "temp/it_raw.txt"
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
                            output_path, duration=0.32):
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
    Path("temp").mkdir(exist_ok=True)

    fps       = 25
    intro_dur = get_video_duration(str(intro_path))
    offset    = max(0.1, intro_dur - duration)
    SIGMA     = 28

    # 1.05x zoom через scale+crop (без zoompan)
    SW, SH = int(1920 * 1.05), int(1080 * 1.05)   # 2016 x 1134
    CX, CY = (SW - 1920) // 2, (SH - 1080) // 2   # 48 x 27
    scale_f = f"scale={SW}:{SH},crop=1920:1080:{CX}:{CY}"

    ib = "temp/sz_ib.mp4"
    iz = "temp/sz_iz.mp4"
    mz = "temp/sz_mz.mp4"
    ma = "temp/sz_ma.mp4"
    bm = "temp/sz_bm.mp4"

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
        _final_concat([ib, bm, ma], output_path)

        print(f"  OK smooth_zoom (scale+gblur): {output_path.name}", flush=True)
        return True

    except subprocess.CalledProcessError as e:
        print(f"  !! smooth_zoom fail — fallback: {e}", flush=True)
        return intro_to_main_transition(intro_path, main_path, output_path, duration)


# ── WHIP PAN (FR intro→main) ──────────────────────────────────────────────────

def whip_pan_transition(intro_path, main_path,
                         output_path, duration=0.20):
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
    Path("temp").mkdir(exist_ok=True)

    fps       = 25
    intro_dur = get_video_duration(str(intro_path))

    t_c1main = "temp/wp_c1main.mp4"
    t_c1blur = "temp/wp_c1blur.mp4"
    t_c2blur = "temp/wp_c2blur.mp4"
    t_c2main = "temp/wp_c2main.mp4"

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
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-pix_fmt", "yuv420p", "-an", "-r", str(fps), t_c1blur,
        ], check=True, capture_output=True)

        # 3. clip2 blur zone — первые BLUR_OUT секунд, blur max→0 + доводка
        subprocess.run([
            "ffmpeg", "-y",
            "-i", str(main_path),
            "-t", f"{BLUR_OUT:.4f}",
            "-filter_complex", _make_fc(BLUR_OUT, "down"),
            "-map", "[out]",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
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
        _final_concat([t_c1main, t_c1blur, t_c2blur, t_c2main], output_path)
        print(f"  OK whip_pan (swipe+blur): {output_path.name}", flush=True)
        return True

    except subprocess.CalledProcessError as e:
        print(f"  !! whip_pan fail — fallback: {e}", flush=True)
        return intro_to_main_transition(intro_path, main_path, output_path, duration)


# ── ZOOM BLUR (FR intro→main) ────────────────────────────────────────────────

def zoom_blur_transition(intro_path, main_path, output_path, duration=0.20):
    """
    FR-style: zoom in/out + blur ramp (ease sin²/cos²). Без свайпа, без dissolve.

    Структура: [c1_main] + [c1_zoom_blur 0.2s] + [c2_zoom_blur 0.4s] + [c2_main]

    clip1 конец: blur 0→max + zoom in  (1.0 → 1+ZOOM_MAX, ease-in)
    clip2 начало: blur max→0 + zoom out (1+ZOOM_MAX → 1.0, ease-out)
    Hard cut между зонами — скрыт максимальным blur.
    """
    BLUR_IN   = 0.20
    BLUR_OUT  = 0.60   # дольше → плавнее исчезает блюр
    BLUR_R    = 55
    BLUR_PEAK = 0.65   # пик блюра: 65%
    ZOOM_MAX  = 0.35   # максимальный зум на intro-стороне: 1.35x

    intro_path  = Path(intro_path)
    main_path   = Path(main_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    Path("temp").mkdir(exist_ok=True)

    fps       = 25
    intro_dur = get_video_duration(str(intro_path))

    t_c1main = "temp/zb_c1main.mp4"
    t_c1blur = "temp/zb_c1blur.mp4"
    t_c2blur = "temp/zb_c2blur.mp4"
    t_c2main = "temp/zb_c2main.mp4"

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
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-pix_fmt", "yuv420p", "-an", "-r", str(fps), t_c1blur,
        ], check=True, capture_output=True)

        # 3. clip2 blur+zoom zone — первые BLUR_OUT секунд
        subprocess.run([
            "ffmpeg", "-y", "-i", str(main_path),
            "-t", f"{BLUR_OUT:.4f}",
            "-filter_complex", _make_fc(BLUR_OUT, "down"),
            "-map", "[out]",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
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
        _final_concat([t_c1main, t_c1blur, t_c2blur, t_c2main], output_path)
        print(f"  OK zoom_blur (zoom+blur): {output_path.name}", flush=True)
        return True

    except subprocess.CalledProcessError as e:
        print(f"  !! zoom_blur fail — fallback: {e}", flush=True)
        return intro_to_main_transition(intro_path, main_path, output_path, duration)


# ── CHUNK CONCAT ─────────────────────────────────────────────────────────────

def _simple_concat(clip_paths, output_path):
    Path("temp").mkdir(exist_ok=True)
    concat_file = "temp/simple_concat.txt"
    with open(concat_file, "w", encoding="utf-8") as f:
        for p in clip_paths:
            f.write(f"file '{os.path.abspath(str(p))}'\n")
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", concat_file,
        "-c:v", GPU_ENCODER, *GPU_PARAMS,
        "-pix_fmt", "yuv420p", "-an", "-loglevel", "warning",
        str(output_path),
    ], check=True)


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


def _crossfade_chunk(clip_paths, durations, output_path, duration=0.5, fps=25):
    """
    Склейка N клипов с crossfade по opacity (ease-in-out, без xfade/пикселизации).
    Строит единый filter_complex с trim+split+blend+concat.
    w = 0.5 - 0.5·cos(π·T/dur) — плавный ease-in-out.
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
        print(f"  !! crossfade chunk error — fallback simple_concat", flush=True)
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
        print(f"  !! xfade ({transition}) error — fallback simple_concat", flush=True)
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

    if not clip_paths:
        return False
    if len(clip_paths) == 1:
        shutil.copy(str(clip_paths[0]), str(output_path))
        return True

    n = len(clip_paths)

    with ThreadPoolExecutor(max_workers=8) as ex:
        durations = list(ex.map(get_video_duration, [str(p) for p in clip_paths]))

    CHUNK_SIZE = 20
    if n <= CHUNK_SIZE:
        return _concat_chunk(clip_paths, durations, output_path,
                             transition, duration, trans_seq)

    Path("temp").mkdir(exist_ok=True)
    chunks: list[Path] = []
    n_chunks = (n + CHUNK_SIZE - 1) // CHUNK_SIZE
    for ci, i in enumerate(range(0, n, CHUNK_SIZE)):
        chunk_clips = clip_paths[i : i + CHUNK_SIZE]
        chunk_durs  = durations[i : i + CHUNK_SIZE]
        chunk_out   = Path(f"temp/chunk_{ci:03d}.mp4")
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
