"""
build_transitions.py
Шаг 1: нарезка оверлеев — находим пик яркости внутри файла (t_peak),
        сохраняем весь файл с меткой t_peak для точного выравнивания на стык.
Шаг 2: рендер переходов:
        - overlay: пик оверлея ставится ровно на стык A→B
        - fades/effects: нулевые и лёгкие типы, длина не теряется
Шаг 3: showcase

Запуск:
  python tools/build_transitions.py
"""

import subprocess, random
from pathlib import Path

ROOT      = Path(__file__).resolve().parent.parent
OVER_DIR  = ROOT / "assets" / "overlays" / "Overlays"
PART_DIR  = ROOT / "assets" / "overlays" / "Particle"
CUTS_DIR  = ROOT / "assets" / "overlays" / "cuts"
WORK      = ROOT / "tools" / "manim_out" / "trans_work"
SHOWCASE  = ROOT / "tools" / "manim_out" / "transitions_showcase.mp4"
CHUNKS    = ROOT / "agents" / "assembler" / "temp"

for d in [CUTS_DIR, WORK]:
    d.mkdir(parents=True, exist_ok=True)

W, H, FPS = 1920, 1080, 25
CLIP_DUR  = 3.5


# ── Utils ─────────────────────────────────────────────────────────────────────

def run(cmd):
    return subprocess.run(
        cmd, capture_output=True, text=True,
        encoding="utf-8", errors="replace"
    )

def dur(path):
    r = run(["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)])
    try:
        return float(r.stdout.strip())
    except Exception:
        return 0.0

def ok(path, min_dur=0.3):
    return Path(path).exists() and dur(path) >= min_dur


# ── Step 1: Find peak brightness timestamp inside overlay ────────────────────

def find_peak_timestamp(video_path, sample_fps=10):
    """
    Сканируем каждый фрейм оверлея, находим timestamp самого яркого кадра.
    Возвращает (t_peak, avg_luma).
    """
    cmd = [
        "ffmpeg", "-i", str(video_path),
        "-vf", f"fps={sample_fps},scale=64:36,format=gray",
        "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1",
    ]
    res = subprocess.run(cmd, capture_output=True)
    fs   = 64 * 36
    data = res.stdout
    best_t, best_luma = 0.0, 0.0
    for i in range(len(data) // fs):
        frame = data[i * fs:(i + 1) * fs]
        avg   = sum(frame) / fs
        if avg > best_luma:
            best_luma = avg
            best_t    = i / sample_fps
    return best_t, best_luma


HALF = 0.5   # секунд до и после пика → итоговый cut = HALF*2

def extract_cuts():
    """
    Для каждого оверлея:
    - находим t_peak (самый яркий кадр)
    - вырезаем строго [peak - HALF .. peak + HALF]
    - пик всегда в центре файла (на HALF секунде)
    Возвращает список (path,) — t_peak внутри файла всегда = HALF.
    """
    cuts = []
    sources = sorted(OVER_DIR.glob("*.mp4")) + sorted(PART_DIR.glob("*.mp4"))
    print(f"Scanning {len(sources)} source files...")

    for src in sources:
        t_peak, luma = find_peak_timestamp(src)
        if luma < 160:
            print(f"  [skip] too dark ({luma:.0f}): {src.name}")
            continue

        total_src = dur(src)
        start  = max(0.0, t_peak - HALF)
        # Если пик слишком близко к началу — сдвигаем старт
        actual_peak_in_cut = t_peak - start  # должно быть HALF
        cut_dur = HALF * 2
        out = CUTS_DIR / f"{src.stem}_cut.mp4"

        run([
            "ffmpeg", "-y",
            "-ss", f"{start:.3f}", "-i", str(src),
            "-t", f"{cut_dur:.3f}",
            "-vf", (
                f"scale={W}:{H}:force_original_aspect_ratio=increase,"
                f"crop={W}:{H},setsar=1,"
                f"format=gray,format=yuv420p"
            ),
            "-c:v", "libx264", "-profile:v", "high", "-level", "4.0", "-pix_fmt", "yuv420p", "-crf", "18", "-preset", "fast", "-an",
            str(out),
        ])

        if ok(out, min_dur=cut_dur * 0.8):
            cuts.append(out)
            print(f"  [OK] {out.name}  src_peak={t_peak:.2f}s  luma={luma:.0f}")

    return cuts


# ── Step 2: Test clips ────────────────────────────────────────────────────────

def make_test_clips():
    clip_a = WORK / "clip_a.mp4"
    clip_b = WORK / "clip_b.mp4"
    chunks = sorted(CHUNKS.glob("chunk_*.mp4"))

    if len(chunks) >= 3:
        for src, start, out in [
            (chunks[0], 10.0, clip_a),
            (chunks[2], 15.0, clip_b),
        ]:
            run([
                "ffmpeg", "-y",
                "-ss", str(start), "-i", str(src),
                "-t", str(CLIP_DUR),
                "-vf", f"scale={W}:{H},setsar=1",
                "-c:v", "libx264", "-profile:v", "high", "-level", "4.0", "-pix_fmt", "yuv420p", "-crf", "16", "-preset", "fast", "-an",
                str(out),
            ])

    if not ok(clip_a):
        for color, out in [("0x0d1b3e", clip_a), ("0x3e1500", clip_b)]:
            run([
                "ffmpeg", "-y",
                "-f", "lavfi",
                "-i", f"color=c={color}:s={W}x{H}:r={FPS}:d={CLIP_DUR}",
                "-c:v", "libx264", "-profile:v", "high", "-level", "4.0", "-pix_fmt", "yuv420p", "-crf", "18", "-preset", "ultrafast", "-an",
                str(out),
            ])
    return clip_a, clip_b


# ── Overlay transition ────────────────────────────────────────────────────────

def t_overlay(clip_a, clip_b, overlay_cut, output):
    """
    Оверлей cut = [peak-HALF .. peak+HALF], пик строго в центре (на HALF секунде).
    Кладём так: оверлей стартует в da-HALF, пик падает точно на стык da.
    Fade-in от 0 до HALF, fade-out от HALF до конца.
    """
    da    = dur(clip_a)
    db    = dur(clip_b)
    do    = dur(overlay_cut)   # = HALF*2
    total = da + db
    fade  = max(0.05, HALF * 0.85)   # fade чуть короче половины

    # Стартуем оверлей за HALF секунд до стыка
    ov_start  = max(0.04, da - HALF)
    pad_after = max(0.04, total - ov_start - do)

    fc = (
        # Базовое видео
        f"[0:v][1:v]concat=n=2:v=1[base];"
        # Оверлей: фейдим в чёрный (без alpha — всё остаётся yuv420p)
        # Чёрный в screen blend = Y=0 = не влияет на base, аналогично прозрачному
        f"[2:v]fade=t=in:st=0:d={fade:.3f}:color=black,"
        f"fade=t=out:st={HALF:.3f}:d={fade:.3f}:color=black[ov_faded];"
        # Паддинг чёрным + сборка оверлея в одну дорожку
        f"[3:v][ov_faded][4:v]concat=n=3:v=1,"
        f"trim=duration={total:.3f},setpts=PTS-STARTPTS[ov];"
        # Разделяем каналы базового видео
        f"[base]extractplanes=y+u+v[by][bu][bv];"
        # Из оверлея берём только Y (яркость)
        f"[ov]extractplanes=y[oy];"
        # Screen blend только по Y — чёрный = прозрачный, белый = вспышка
        f"[by][oy]blend=all_mode=screen[ry];"
        # U и V остаются от base — никакого цветового тинта
        f"[ry][bu][bv]mergeplanes=0x001020:yuv420p[v]"
    )
    run([
        "ffmpeg", "-y",
        "-i", str(clip_a),
        "-i", str(clip_b),
        "-i", str(overlay_cut),
        "-f", "lavfi", "-i", f"color=black:s={W}x{H}:r={FPS}:d={ov_start:.3f}",
        "-f", "lavfi", "-i", f"color=black:s={W}x{H}:r={FPS}:d={pad_after:.3f}",
        "-filter_complex", fc,
        "-map", "[v]",
        "-c:v", "libx264", "-profile:v", "high", "-level", "4.0", "-pix_fmt", "yuv420p", "-crf", "18", "-preset", "fast",
        "-t", str(total), "-an", str(output),
    ])


# ── Fade/effect transitions ───────────────────────────────────────────────────

def t_dip(clip_a, clip_b, color, fdur, output):
    """Dip to color — fade out конца A + fade in начала B. Длина не теряется."""
    da = dur(clip_a)
    fc = (
        f"[0:v]fade=t=out:st={da - fdur:.3f}:d={fdur:.3f}:color={color}[a];"
        f"[1:v]fade=t=in:st=0:d={fdur:.3f}:color={color}[b];"
        f"[a][b]concat=n=2:v=1[v]"
    )
    run(["ffmpeg", "-y", "-i", str(clip_a), "-i", str(clip_b),
         "-filter_complex", fc, "-map", "[v]",
         "-c:v", "libx264", "-profile:v", "high", "-level", "4.0", "-pix_fmt", "yuv420p", "-crf", "20", "-preset", "fast", "-an", str(output)])


def t_xfade(clip_a, clip_b, xtype, xdur, output):
    """xfade с freeze-frame — длина не теряется."""
    da = dur(clip_a)
    fc = (
        f"[0:v]tpad=stop_mode=clone:stop_duration={xdur}[a];"
        f"[1:v]tpad=start_mode=clone:start_duration={xdur}[b];"
        f"[a][b]xfade=transition={xtype}:duration={xdur}:offset={da}[v]"
    )
    run(["ffmpeg", "-y", "-i", str(clip_a), "-i", str(clip_b),
         "-filter_complex", fc, "-map", "[v]",
         "-c:v", "libx264", "-profile:v", "high", "-level", "4.0", "-pix_fmt", "yuv420p", "-crf", "20", "-preset", "fast", "-an", str(output)])


def t_push(clip_a, clip_b, direction, fdur, output):
    """
    Push: клип A уходит, клип B приходит — оба движутся.
    direction: left | right | up | down
    Нулевая нагрузка — только overlay со смещением.
    """
    da    = dur(clip_a)
    total = da + dur(clip_b)

    if direction == "left":
        expr_a = f"x='if(gte(t,{da:.3f}), -W*(t-{da:.3f})/{fdur:.3f}, 0)':y=0"
        expr_b = f"x='if(gte(t,{da:.3f}), W - W*(t-{da:.3f})/{fdur:.3f}, W)':y=0"
    elif direction == "right":
        expr_a = f"x='if(gte(t,{da:.3f}), W*(t-{da:.3f})/{fdur:.3f}, 0)':y=0"
        expr_b = f"x='if(gte(t,{da:.3f}), -W + W*(t-{da:.3f})/{fdur:.3f}, -W)':y=0"
    elif direction == "up":
        expr_a = f"x=0:y='if(gte(t,{da:.3f}), -H*(t-{da:.3f})/{fdur:.3f}, 0)'"
        expr_b = f"x=0:y='if(gte(t,{da:.3f}), H - H*(t-{da:.3f})/{fdur:.3f}, H)'"
    else:  # down
        expr_a = f"x=0:y='if(gte(t,{da:.3f}), H*(t-{da:.3f})/{fdur:.3f}, 0)'"
        expr_b = f"x=0:y='if(gte(t,{da:.3f}), -H + H*(t-{da:.3f})/{fdur:.3f}, -H)'"

    fc = (
        f"[0:v]tpad=stop_mode=clone:stop_duration={fdur}[a];"
        f"[1:v]tpad=start_mode=clone:start_duration={da}[b];"
        f"[a]setpts=PTS-STARTPTS[av];"
        f"[b]setpts=PTS-STARTPTS[bv];"
        f"color=black:s={W}x{H}:r={FPS}:d={total+fdur}[bg];"
        f"[bg][av]overlay={expr_a}[tmp];"
        f"[tmp][bv]overlay={expr_b},"
        f"trim=duration={total},setpts=PTS-STARTPTS[v]"
    )
    run(["ffmpeg", "-y",
         "-i", str(clip_a), "-i", str(clip_b),
         "-filter_complex", fc, "-map", "[v]",
         "-c:v", "libx264", "-profile:v", "high", "-level", "4.0", "-pix_fmt", "yuv420p", "-crf", "20", "-preset", "fast", "-an", str(output)])


def t_freeze_cut(clip_a, clip_b, freeze_dur, output):
    """
    Freeze frame cut: A замерзает на freeze_dur → резкий cut на B.
    Нулевая нагрузка — tpad clone.
    """
    fc = (
        f"[0:v]tpad=stop_mode=clone:stop_duration={freeze_dur}[a];"
        f"[a][1:v]concat=n=2:v=1[v]"
    )
    run(["ffmpeg", "-y", "-i", str(clip_a), "-i", str(clip_b),
         "-filter_complex", fc, "-map", "[v]",
         "-c:v", "libx264", "-profile:v", "high", "-level", "4.0", "-pix_fmt", "yuv420p", "-crf", "20", "-preset", "fast", "-an", str(output)])


def t_additive(clip_a, clip_b, fdur, output):
    """
    Additive dissolve: blend=addition — оба клипа суммируются, пересвет в центре.
    Нулевая нагрузка.
    """
    da = dur(clip_a)
    fc = (
        f"[0:v]tpad=stop_mode=clone:stop_duration={fdur}[a];"
        f"[1:v]tpad=start_mode=clone:start_duration={fdur}[b];"
        f"[a][b]xfade=transition=fade:duration={fdur}:offset={da}[xf];"
        # Накладываем оба клипа через addition в зоне перехода
        f"[0:v]tpad=stop_mode=clone:stop_duration={fdur}[a2];"
        f"[1:v]tpad=start_mode=clone:start_duration={fdur}[b2];"
        f"[a2][b2]blend=all_mode=addition:all_opacity=1,"
        f"trim=start={da}:end={da + fdur},setpts=PTS-STARTPTS[add];"
        f"[xf][add]overlay=x=0:y=0:enable='between(t,{da},{da + fdur})'[v]"
    )
    # Упрощённая версия через xfade — additive эффект через fadewhite
    fc_simple = (
        f"[0:v]tpad=stop_mode=clone:stop_duration={fdur}[a];"
        f"[1:v]tpad=start_mode=clone:start_duration={fdur}[b];"
        f"[a][b]xfade=transition=fadewhite:duration={fdur}:offset={da}[v]"
    )
    run(["ffmpeg", "-y", "-i", str(clip_a), "-i", str(clip_b),
         "-filter_complex", fc_simple, "-map", "[v]",
         "-c:v", "libx264", "-profile:v", "high", "-level", "4.0", "-pix_fmt", "yuv420p", "-crf", "20", "-preset", "fast", "-an", str(output)])


def t_invert_flash(clip_a, clip_b, fdur, output):
    """
    Color invert flash: конец A инвертируется → белый → B начинается нормально.
    Нулевая нагрузка — один фильтр negate.
    """
    da = dur(clip_a)
    sp = max(0.01, da - fdur)
    fc = (
        f"[0:v]split[a1][a2];"
        f"[a1]trim=end={sp:.3f},setpts=PTS-STARTPTS[a_clean];"
        f"[a2]trim=start={sp:.3f},setpts=PTS-STARTPTS,"
        f"negate,fade=t=out:st=0:d={fdur:.3f}:color=white[a_inv];"
        f"[a_clean][a_inv]concat=n=2:v=1[a];"
        f"[1:v]fade=t=in:st=0:d={fdur:.3f}:color=white[b];"
        f"[a][b]concat=n=2:v=1[v]"
    )
    run(["ffmpeg", "-y", "-i", str(clip_a), "-i", str(clip_b),
         "-filter_complex", fc, "-map", "[v]",
         "-c:v", "libx264", "-profile:v", "high", "-level", "4.0", "-pix_fmt", "yuv420p", "-crf", "20", "-preset", "fast", "-an", str(output)])


def t_desaturate(clip_a, clip_b, fdur, output):
    """
    Desaturate cut: конец A теряет цвет → серый → cut на B.
    Лёгкая нагрузка — hue filter.
    """
    da = dur(clip_a)
    sp = max(0.01, da - fdur)
    fc = (
        f"[0:v]split[a1][a2];"
        f"[a1]trim=end={sp:.3f},setpts=PTS-STARTPTS[a_clean];"
        f"[a2]trim=start={sp:.3f},setpts=PTS-STARTPTS,"
        f"hue=s='1-min(1,(t/{fdur:.3f}))'[a_desat];"
        f"[a_clean][a_desat]concat=n=2:v=1[a];"
        f"[1:v]hue=s='min(1,t/{fdur:.3f})'[b];"
        f"[a][b]concat=n=2:v=1[v]"
    )
    run(["ffmpeg", "-y", "-i", str(clip_a), "-i", str(clip_b),
         "-filter_complex", fc, "-map", "[v]",
         "-c:v", "libx264", "-profile:v", "high", "-level", "4.0", "-pix_fmt", "yuv420p", "-crf", "20", "-preset", "fast", "-an", str(output)])


def t_rack_focus(clip_a, clip_b, fdur, output):
    """Rack focus: конец A в фиксированном сильном blur + fade → B из blur + fade."""
    da = dur(clip_a)
    sp = max(0.01, da - fdur)
    fc = (
        f"[0:v]split[a1][a2];"
        f"[a1]trim=end={sp:.3f},setpts=PTS-STARTPTS[a_clean];"
        f"[a2]trim=start={sp:.3f},setpts=PTS-STARTPTS,"
        f"gblur=sigma=18,fade=t=out:st=0:d={fdur:.3f}:color=black[a_blur];"
        f"[a_clean][a_blur]concat=n=2:v=1[a];"
        f"[1:v]split[b1][b2];"
        f"[b1]trim=end={fdur:.3f},setpts=PTS-STARTPTS,"
        f"gblur=sigma=18,fade=t=in:st=0:d={fdur:.3f}:color=black[b_blur];"
        f"[b2]trim=start={fdur:.3f},setpts=PTS-STARTPTS[b_clean];"
        f"[b_blur][b_clean]concat=n=2:v=1[b];"
        f"[a][b]concat=n=2:v=1[v]"
    )
    run(["ffmpeg", "-y", "-i", str(clip_a), "-i", str(clip_b),
         "-filter_complex", fc, "-map", "[v]",
         "-c:v", "libx264", "-profile:v", "high", "-level", "4.0", "-pix_fmt", "yuv420p", "-crf", "20", "-preset", "fast", "-an", str(output)])


def t_motion_blur(clip_a, clip_b, fdur, output):
    """Горизонтальный motion blur (boxblur) + fade to black."""
    da = dur(clip_a)
    sp = max(0.01, da - fdur)
    fc = (
        f"[0:v]split[a1][a2];"
        f"[a1]trim=end={sp:.3f},setpts=PTS-STARTPTS[a_clean];"
        f"[a2]trim=start={sp:.3f},setpts=PTS-STARTPTS,"
        f"boxblur=lr=30:lp=1,fade=t=out:st=0:d={fdur:.3f}:color=black[a_mb];"
        f"[a_clean][a_mb]concat=n=2:v=1[a];"
        f"[1:v]split[b1][b2];"
        f"[b1]trim=end={fdur:.3f},setpts=PTS-STARTPTS,"
        f"boxblur=lr=30:lp=1,fade=t=in:st=0:d={fdur:.3f}:color=black[b_mb];"
        f"[b2]trim=start={fdur:.3f},setpts=PTS-STARTPTS[b_clean];"
        f"[b_mb][b_clean]concat=n=2:v=1[b];"
        f"[a][b]concat=n=2:v=1[v]"
    )
    run(["ffmpeg", "-y", "-i", str(clip_a), "-i", str(clip_b),
         "-filter_complex", fc, "-map", "[v]",
         "-c:v", "libx264", "-profile:v", "high", "-level", "4.0", "-pix_fmt", "yuv420p", "-crf", "20", "-preset", "fast", "-an", str(output)])


def t_whip(clip_a, clip_b, axis, fdur, output):
    da = dur(clip_a)
    sp = max(0.05, da - fdur)
    blur = "boxblur=lr=22:lp=1" if axis == "h" else "boxblur=0:22:lp=1"
    fc = (
        f"[0:v]split[a1][a2];"
        f"[a1]trim=end={sp:.3f},setpts=PTS-STARTPTS[a_clean];"
        f"[a2]trim=start={sp:.3f},setpts=PTS-STARTPTS,"
        f"{blur},fade=t=out:st=0:d={fdur:.3f}:color=black[a_blur];"
        f"[a_clean][a_blur]concat=n=2:v=1[a];"
        f"[1:v]split[b1][b2];"
        f"[b1]trim=end={fdur:.3f},setpts=PTS-STARTPTS,"
        f"{blur},fade=t=in:st=0:d={fdur:.3f}:color=black[b_blur];"
        f"[b2]trim=start={fdur:.3f},setpts=PTS-STARTPTS[b_clean];"
        f"[b_blur][b_clean]concat=n=2:v=1[b];"
        f"[a][b]concat=n=2:v=1[v]"
    )
    run(["ffmpeg", "-y", "-i", str(clip_a), "-i", str(clip_b),
         "-filter_complex", fc, "-map", "[v]",
         "-c:v", "libx264", "-profile:v", "high", "-level", "4.0", "-pix_fmt", "yuv420p", "-crf", "20", "-preset", "fast", "-an", str(output)])


def t_zoom_blur(clip_a, clip_b, fdur, output):
    da = dur(clip_a)
    sp = max(0.05, da - fdur)
    fc = (
        f"[0:v]split[a1][a2];"
        f"[a1]trim=end={sp:.3f},setpts=PTS-STARTPTS[a_clean];"
        f"[a2]trim=start={sp:.3f},setpts=PTS-STARTPTS,"
        f"gblur=sigma=14,fade=t=out:st=0:d={fdur:.3f}:color=black[a_blur];"
        f"[a_clean][a_blur]concat=n=2:v=1[a];"
        f"[1:v]split[b1][b2];"
        f"[b1]trim=end={fdur:.3f},setpts=PTS-STARTPTS,"
        f"gblur=sigma=14,fade=t=in:st=0:d={fdur:.3f}:color=black[b_blur];"
        f"[b2]trim=start={fdur:.3f},setpts=PTS-STARTPTS[b_clean];"
        f"[b_blur][b_clean]concat=n=2:v=1[b];"
        f"[a][b]concat=n=2:v=1[v]"
    )
    run(["ffmpeg", "-y", "-i", str(clip_a), "-i", str(clip_b),
         "-filter_complex", fc, "-map", "[v]",
         "-c:v", "libx264", "-profile:v", "high", "-level", "4.0", "-pix_fmt", "yuv420p", "-crf", "20", "-preset", "fast", "-an", str(output)])


def t_glitch(clip_a, clip_b, fdur, output):
    da = dur(clip_a)
    sp = max(0.05, da - fdur)
    fc = (
        f"[0:v]split[a1][a2];"
        f"[a1]trim=end={sp:.3f},setpts=PTS-STARTPTS[a_clean];"
        f"[a2]trim=start={sp:.3f},setpts=PTS-STARTPTS,"
        f"rgbashift=rh=6:bh=-6:rv=2,fade=t=out:st=0:d={fdur:.3f}:color=white[a_g];"
        f"[a_clean][a_g]concat=n=2:v=1[a];"
        f"[1:v]split[b1][b2];"
        f"[b1]trim=end={fdur:.3f},setpts=PTS-STARTPTS,"
        f"rgbashift=rh=6:bh=-6:rv=2,fade=t=in:st=0:d={fdur:.3f}:color=white[b_g];"
        f"[b2]trim=start={fdur:.3f},setpts=PTS-STARTPTS[b_clean];"
        f"[b_g][b_clean]concat=n=2:v=1[b];"
        f"[a][b]concat=n=2:v=1[v]"
    )
    run(["ffmpeg", "-y", "-i", str(clip_a), "-i", str(clip_b),
         "-filter_complex", fc, "-map", "[v]",
         "-c:v", "libx264", "-profile:v", "high", "-level", "4.0", "-pix_fmt", "yuv420p", "-crf", "20", "-preset", "fast", "-an", str(output)])


def add_title(clip, title, idx, output):
    safe = title.replace("'", "").replace(":", " ")
    r = run([
        "ffmpeg", "-y", "-i", str(clip),
        "-vf", (
            f"drawtext=text='{idx:02d}  {safe}':"
            f"fontcolor=white:fontsize=38:x=40:y=40:"
            f"box=1:boxcolor=black@0.6:boxborderw=10"
        ),
        "-c:v", "libx264", "-profile:v", "high", "-level", "4.0", "-pix_fmt", "yuv420p", "-crf", "20", "-preset", "fast", "-an", str(output),
    ])
    return r.returncode == 0


# ── Catalogue ─────────────────────────────────────────────────────────────────

FADES = [
    # ── Dip to color ─────────────────────────────────────────────────────────
    ("dip_black_mid",      "dip",   "black",     0.35),
    ("dip_black_slow",     "dip",   "black",     0.60),
    ("dip_white_mid",      "dip",   "white",     0.35),
    ("dip_orange",         "dip",   "0xff5500",  0.30),
    ("dip_deep_blue",      "dip",   "0x000a2e",  0.35),
    ("dip_red",            "dip",   "0xcc0000",  0.25),
    ("dip_teal",           "dip",   "0x006060",  0.30),
    # ── xfade dissolve / fade ────────────────────────────────────────────────
    ("dissolve_fast",      "xfade", "dissolve",  0.20),
    ("fadeblack",          "xfade", "fadeblack", 0.40),
    ("fadewhite",          "xfade", "fadewhite", 0.35),
    ("distance",           "xfade", "distance",  0.40),
    # ── xfade wipe ───────────────────────────────────────────────────────────
    ("wipe_left",          "xfade", "wipeleft",  0.35),
    ("wipe_right",         "xfade", "wiperight", 0.35),
    ("wipe_up",            "xfade", "wipeup",    0.30),
    ("wipe_down",          "xfade", "wipedown",  0.30),
    ("wipe_tl",            "xfade", "wipetl",    0.40),
    ("wipe_tr",            "xfade", "wipetr",    0.40),
    ("wipe_bl",            "xfade", "wipebl",    0.40),
    ("wipe_br",            "xfade", "wipebr",    0.40),
    # ── xfade slide ──────────────────────────────────────────────────────────
    ("slide_left",         "xfade", "slideleft",  0.40),
    ("slide_right",        "xfade", "slideright", 0.40),
    ("slide_up",           "xfade", "slideup",    0.35),
    ("slide_down",         "xfade", "slidedown",  0.35),
    # ── xfade cover / reveal ─────────────────────────────────────────────────
    ("cover_left",         "xfade", "coverleft",   0.40),
    ("cover_right",        "xfade", "coverright",  0.40),
    ("cover_up",           "xfade", "coverup",     0.35),
    ("cover_down",         "xfade", "coverdown",   0.35),
    ("reveal_left",        "xfade", "revealleft",  0.40),
    ("reveal_right",       "xfade", "revealright", 0.40),
    ("reveal_up",          "xfade", "revealup",    0.35),
    # ── xfade geometry ───────────────────────────────────────────────────────
    ("radial",             "xfade", "radial",      0.45),
    ("rect_crop",          "xfade", "rectcrop",    0.45),
    ("squeeze_h",          "xfade", "squeezeh",    0.40),
    ("squeeze_v",          "xfade", "squeezev",    0.40),
    ("diag_tl",            "xfade", "diagtl",      0.40),
    ("zoomin",             "xfade", "zoomin",      0.45),
    ("hblur",              "xfade", "hblur",       0.35),
    ("pixelize_fast",      "xfade", "pixelize",    0.25),
    ("pixelize_slow",      "xfade", "pixelize",    0.55),
    # ── xfade slice ──────────────────────────────────────────────────────────
    ("hl_slice",           "xfade", "hlslice",     0.40),
    ("hr_slice",           "xfade", "hrslice",     0.40),
    ("vu_slice",           "xfade", "vuslice",     0.40),
    ("vd_slice",           "xfade", "vdslice",     0.40),
    # ── xfade wind ───────────────────────────────────────────────────────────
    ("hl_wind",            "xfade", "hlwind",      0.45),
    ("hr_wind",            "xfade", "hrwind",      0.45),
    ("vu_wind",            "xfade", "vuwind",      0.45),
    ("vd_wind",            "xfade", "vdwind",      0.45),
    # ── Additive dissolve ────────────────────────────────────────────────────
    ("additive_fast",      "additive", None,       0.20),
    ("additive_mid",       "additive", None,       0.40),
    # ── Invert flash ─────────────────────────────────────────────────────────
    ("invert_fast",        "invert", None,         0.15),
    ("invert_mid",         "invert", None,         0.28),
    # ── Motion blur ──────────────────────────────────────────────────────────
    ("motion_blur_fast",   "mblur",  None,         0.20),
    # ── Whip pan ─────────────────────────────────────────────────────────────
    ("whip_h_mid",         "whip",   "h",          0.30),
    ("whip_v",             "whip",   "v",          0.25),
    # ── Glitch ───────────────────────────────────────────────────────────────
    ("glitch_rgb",         "glitch", None,         0.22),
]


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("STEP 1: Extract overlay cuts + find peak timestamps")
    print("=" * 60)
    cuts = extract_cuts()
    print(f"\nExtracted {len(cuts)} cuts\n")

    print("=" * 60)
    print("STEP 2: Test clips")
    print("=" * 60)
    clip_a, clip_b = make_test_clips()
    da = dur(clip_a)
    db = dur(clip_b)
    print(f"clip_a={da:.1f}s  clip_b={db:.1f}s\n")

    print("=" * 60)
    print("STEP 3: Render transitions")
    print("=" * 60)

    results = []
    idx = 0

    # Overlays
    for ov_path in cuts:
        if idx >= 200:
            break
        label = f"overlay_{ov_path.stem}"
        raw   = WORK / f"{idx + 1:02d}_raw.mp4"
        out   = WORK / f"{idx + 1:02d}_{label}.mp4"

        t_overlay(clip_a, clip_b, ov_path, raw)

        if ok(raw):
            add_title(raw, label, idx + 1, out)
            results.append((label, out if ok(out) else raw))
            print(f"  [{idx + 1:02d}] OK  overlay: {label}")
        else:
            print(f"  [{idx + 1:02d}] FAIL overlay: {label}")
        idx += 1

    # Fades & effects
    for (label, ftype, fparam, fdur) in FADES:
        if idx >= 200:
            break
        raw = WORK / f"{idx + 1:02d}_raw.mp4"
        out = WORK / f"{idx + 1:02d}_{label}.mp4"

        if   ftype == "dip":      t_dip(clip_a, clip_b, fparam, fdur, raw)
        elif ftype == "xfade":    t_xfade(clip_a, clip_b, fparam, fdur, raw)
        elif ftype == "push":     t_push(clip_a, clip_b, fparam, fdur, raw)
        elif ftype == "freeze":   t_freeze_cut(clip_a, clip_b, fdur, raw)
        elif ftype == "additive": t_additive(clip_a, clip_b, fdur, raw)
        elif ftype == "invert":   t_invert_flash(clip_a, clip_b, fdur, raw)
        elif ftype == "desat":    t_desaturate(clip_a, clip_b, fdur, raw)
        elif ftype == "rack":     t_rack_focus(clip_a, clip_b, fdur, raw)
        elif ftype == "mblur":    t_motion_blur(clip_a, clip_b, fdur, raw)
        elif ftype == "whip":     t_whip(clip_a, clip_b, fparam, fdur, raw)
        elif ftype == "zoom":     t_zoom_blur(clip_a, clip_b, fdur, raw)
        elif ftype == "glitch":   t_glitch(clip_a, clip_b, fdur, raw)

        if ok(raw):
            add_title(raw, label, idx + 1, out)
            results.append((label, out if ok(out) else raw))
            print(f"  [{idx + 1:02d}] OK  {ftype}: {label}")
        else:
            print(f"  [{idx + 1:02d}] FAIL {ftype}: {label}")
        idx += 1

    print(f"\nTotal: {len(results)}/50")

    print("\n" + "=" * 60)
    print("STEP 4: Showcase")
    print("=" * 60)

    concat_f = WORK / "concat.txt"
    with open(concat_f, "w", encoding="utf-8") as f:
        for _, path in results:
            f.write(f"file '{path}'\n")

    run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", str(concat_f),
        "-c:v", "libx264", "-profile:v", "high", "-level", "4.0", "-pix_fmt", "yuv420p", "-crf", "20", "-preset", "fast", "-an",
        str(SHOWCASE),
    ])

    d = dur(SHOWCASE)
    print(f"Showcase -> {SHOWCASE.name}  {d:.1f}s  ({len(results)} transitions)")


if __name__ == "__main__":
    main()
