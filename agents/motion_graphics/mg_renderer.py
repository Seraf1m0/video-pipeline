"""
mg_renderer.py — рендерит Remotion-композиции из плана зон.

Для каждой зоны вызывает:
    npx remotion render src/index.ts <CompositionId> <output.mp4> --props '<json>'

Также предоставляет inject_zone() — вставляет зону в видео через ffmpeg:
  - видео переключается на анимацию
  - аудио (озвучка) идёт непрерывно
  - SFX анимации микшируется тихо
  - тихий whoosh_in на входе, whoosh_out на выходе

Usage:
    python mg_renderer.py --channel de --session Video_20260415_193110
"""

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from agents.utils.paths import get_session_dir, TEMP_ROOT

CHANNEL_MAP = {
    "fr": "channel_002_cosmos_fr",
    "de": "channel_001_cosmos_de",
    "es": "channel_003_religion_es",
    "us": "channel_004_cosmos_us",
}

REMOTION_DIR = ROOT / "remotion"
SFX_DIR      = REMOTION_DIR / "public" / "sfx"

VALID_COMPOSITIONS = {
    "SolarParadox", "DataCard", "Statement", "Timeline", "Comparison",
    "Highlight", "List", "BarChart", "RadialChart", "LineChart",
    "ChapterTitle", "DateStamp", "Quote", "BigNumber", "Scorecard",
    "Terminal", "ProCon", "Ranking",
    "HBarChart", "MultiRing",
    # transparent overlay compositions — render to .mov with alpha
    "DateLabel", "DateBookmark",
}

# Compositions that render with transparent bg → ProRes 4444 .mov → ffmpeg alpha overlay
OVERLAY_COMPOSITIONS = {"DateLabel", "DateBookmark"}

# Пул библиотечных вушей
WHOOSH_IN_POOL  = ["whoosh_in_1.wav", "whoosh_in_2.wav", "whoosh_in_3.wav"]
WHOOSH_OUT_POOL = ["whoosh_out_1.wav", "whoosh_out_2.wav", "whoosh_out_3.wav"]


def _pick_whoosh(pool: list[str]) -> Path:
    for name in random.sample(pool, len(pool)):
        p = SFX_DIR / name
        if p.exists():
            return p
    # fallback to synthetic
    return SFX_DIR / pool[0].replace("_1", "").replace("_2", "").replace("_3", "")


# ─── render one zone ──────────────────────────────────────────────────────────

def render_zone(zone: dict, out_dir: Path) -> Path | None:
    zone_id     = zone["zone_id"]
    composition = zone.get("composition", "DataCard")
    props       = zone.get("props", {})

    if composition not in VALID_COMPOSITIONS:
        print(f"  [zone {zone_id}] Unknown composition '{composition}', skipping")
        return None

    if "duration_s" not in props:
        props = {**props, "duration_s": zone.get("duration", 8.0)}

    # Overlay compositions → .mov with ProRes 4444 alpha; others → .mp4
    is_overlay = composition in OVERLAY_COMPOSITIONS
    ext        = ".mov" if is_overlay else ".mp4"
    out_path   = out_dir / f"zone_{zone_id:02d}{ext}"
    if out_path.exists():
        # Проверяем что длительность совпадает с требуемой (±0.5s)
        expected_dur = float(props.get("duration_s", zone.get("duration", 0)))
        if expected_dur > 0:
            try:
                r = subprocess.run(
                    ["ffprobe", "-v", "error", "-select_streams", "v",
                     "-show_entries", "format=duration",
                     "-of", "csv=p=0", str(out_path)],
                    capture_output=True, text=True
                )
                actual_dur = float(r.stdout.strip()) if r.stdout.strip() else 0.0
                if abs(actual_dur - expected_dur) < 0.5:
                    print(f"  [zone {zone_id}] already rendered ({actual_dur:.1f}s), skipping")
                    return out_path
                print(f"  [zone {zone_id}] duration mismatch: {actual_dur:.1f}s vs {expected_dur:.1f}s → re-rendering")
                out_path.unlink()
            except Exception:
                pass
        else:
            print(f"  [zone {zone_id}] already rendered, skipping")
            return out_path

    # Props записываем в temp-файл — избегаем интерпретации < > | в cmd.exe
    # (npx.cmd на Windows запускается через cmd.exe, где < = stdin redirection)
    import tempfile
    props_file = Path(tempfile.mktemp(suffix=".json"))
    props_file.write_text(json.dumps(props, ensure_ascii=False), encoding="utf-8")

    # npx может не быть в PATH при запуске через subprocess на Windows
    npx = shutil.which("npx") or r"C:\Program Files\nodejs\npx.cmd"
    cmd = [
        npx, "remotion", "render",
        str(REMOTION_DIR / "src" / "index.ts"),
        composition,
        str(out_path),
        "--props", str(props_file),
        "--log", "error",
    ]
    if is_overlay:
        # ProRes 4444 preserves alpha channel (transparent bg)
        cmd += ["--codec", "prores"]

    print(f"  [zone {zone_id}] Rendering {composition} ({props.get('duration_s', '?')}s)...")
    result = subprocess.run(
        cmd, cwd=str(REMOTION_DIR),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    props_file.unlink(missing_ok=True)

    if result.returncode == 0 and out_path.exists():
        print(f"  [zone {zone_id}] OK -> {out_path.name}")
        return out_path

    print(f"  [zone {zone_id}] FAILED (rc={result.returncode})")
    stderr_text = (result.stderr or "").encode("utf-8", errors="replace").decode("utf-8")
    print(f"    {stderr_text[-600:]}")
    return None


# ─── inject zone into video ───────────────────────────────────────────────────

PANEL_W     = 640   # ширина панели List (px, ~1/3 экрана)
VIDEO_SHIFT = 384   # сдвиг видео вправо при panel-режиме (px, ~20%)

# Композиции в panel-режиме (левая панель + сдвиг видео)
PANEL_COMPOSITIONS = {"List"}


def inject_zone(
    source_video: Path,
    anim_mp4: Path,
    out_path: Path,
    insert_at: float,           # секунда в source_video
    anim_duration: float,       # длина анимации в секундах
    composition: str = "",      # имя композиции — определяет режим
    whoosh_vol: float   = 0.25,
    anim_sfx_vol: float = 0.10,
    slide_dur: float    = 0.50,
    tmp_dir: Path | None = None,
) -> Path:
    if composition in OVERLAY_COMPOSITIONS:
        return _inject_overlay(
            source_video, anim_mp4, out_path,
            insert_at, anim_duration,
        )
    elif composition in PANEL_COMPOSITIONS:
        return _inject_panel(
            source_video, anim_mp4, out_path,
            insert_at, anim_duration,
            whoosh_vol, anim_sfx_vol, slide_dur,
        )
    else:
        return _inject_fullscreen(
            source_video, anim_mp4, out_path,
            insert_at, anim_duration,
            anim_sfx_vol,
        )


def _inject_overlay(
    source_video: Path,
    anim_mov: Path,       # ProRes 4444 .mov з alpha
    out_path: Path,
    insert_at: float,
    anim_duration: float,
) -> Path:
    """
    Overlay-режим: прозора анімація (.mov RGBA) накладається поверх відео
    через ffmpeg alpha overlay — відео та аудіо не перериваються.
    Аналог apply_cta.py для CTA-плашок.
    """
    ins = insert_at
    end = ins + anim_duration

    filter_complex = (
        f"[1:v]setpts=PTS-STARTPTS[badge];"
        f"[0:v][badge]overlay=0:0:eof_action=pass:"
        f"enable='between(t,{ins:.3f},{end:.3f})'[vout]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", str(source_video),
        "-i", str(anim_mov),
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-map", "0:a",
        "-c:v", "h264_nvenc", "-preset", "p4", "-cq", "23",
        "-c:a", "copy",
        str(out_path), "-hide_banner", "-loglevel", "error",
    ]
    print(f"  [inject/overlay] {anim_mov.name} @ {ins:.1f}s–{end:.1f}s -> {out_path.name}")
    subprocess.run(cmd, check=True)
    return out_path


def _inject_panel(
    source_video: Path,
    anim_mp4: Path,
    out_path: Path,
    insert_at: float,
    anim_duration: float,
    whoosh_vol: float,
    anim_sfx_vol: float,
    slide_dur: float,
) -> Path:
    """
    List-режим: анимация выезжает как панель (~1/3 экрана) слева,
    видео сдвигается на 20% вправо и остаётся видимым.
    Контент анимации стартует после полного въезда панели.
    """
    ins        = insert_at
    sd         = slide_dur
    anim_start = ins + sd
    panel_end  = anim_start + anim_duration
    hold_end   = panel_end - sd

    whoosh_in  = _pick_whoosh(WHOOSH_IN_POOL)
    whoosh_out = _pick_whoosh(WHOOSH_OUT_POOL)

    win_delay_ms  = int(ins * 1000)
    wout_delay_ms = int(hold_end * 1000)
    sfx_delay_ms  = int(anim_start * 1000)
    pw, vs = PANEL_W, VIDEO_SHIFT

    anim_x = (
        f"if(lt(t,{anim_start:.3f}),-{pw}+{pw}*(t-{ins:.3f})/{sd:.3f},"
        f"if(lt(t,{hold_end:.3f}),0,"
        f"-{pw}*(t-{hold_end:.3f})/{sd:.3f}))"
    )
    main_x = (
        f"if(lt(t,{ins:.3f}),0,"
        f"if(lt(t,{anim_start:.3f}),{vs}*(t-{ins:.3f})/{sd:.3f},"
        f"if(lt(t,{hold_end:.3f}),{vs},"
        f"if(lt(t,{panel_end:.3f}),{vs}*(1-(t-{hold_end:.3f})/{sd:.3f}),"
        f"0))))"
    )

    filter_v = (
        f"[1:v]crop={pw}:1080:0:0,fps=25,setpts=PTS-STARTPTS+{anim_start:.3f}/TB[anim];"
        f"color=black:s=1920x1080:r=25[bg];"
        f"[bg][0:v]overlay=x='{main_x}':y=0[base];"
        f"[base][anim]overlay=x='{anim_x}':y=0:"
        f"enable='between(t,{ins:.3f},{panel_end:.3f})'[vout]"
    )

    has_src_audio = _has_audio(source_video)
    if has_src_audio:
        filter_a = (
            f"[0:a]aformat=sample_rates=44100:channel_layouts=stereo[orig];"
            f"[1:a]volume={anim_sfx_vol},adelay={sfx_delay_ms}|{sfx_delay_ms},"
            f"aformat=sample_rates=44100:channel_layouts=stereo[asfx];"
            f"[2:a]volume={whoosh_vol},adelay={win_delay_ms}|{win_delay_ms},"
            f"aformat=sample_rates=44100:channel_layouts=stereo[win];"
            f"[3:a]volume={whoosh_vol*0.85:.3f},adelay={wout_delay_ms}|{wout_delay_ms},"
            f"aformat=sample_rates=44100:channel_layouts=stereo[wout];"
            f"[orig][asfx][win][wout]amix=inputs=4:duration=first:"
            f"dropout_transition=0:normalize=0[aout]"
        )
    else:
        filter_a = (
            f"[1:a]volume={anim_sfx_vol},adelay={sfx_delay_ms}|{sfx_delay_ms},"
            f"aformat=sample_rates=44100:channel_layouts=stereo[asfx];"
            f"[2:a]volume={whoosh_vol},adelay={win_delay_ms}|{win_delay_ms},"
            f"aformat=sample_rates=44100:channel_layouts=stereo[win];"
            f"[3:a]volume={whoosh_vol*0.85:.3f},adelay={wout_delay_ms}|{wout_delay_ms},"
            f"aformat=sample_rates=44100:channel_layouts=stereo[wout];"
            f"[asfx][win][wout]amix=inputs=3:duration=longest:"
            f"dropout_transition=0:normalize=0[aout]"
        )

    cmd = [
        "ffmpeg", "-y",
        "-i", str(source_video), "-i", str(anim_mp4),
        "-i", str(whoosh_in), "-i", str(whoosh_out),
        "-filter_complex", filter_v + ";" + filter_a,
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "fast", "-crf", "17",
        "-c:a", "aac", "-b:a", "192k",
        str(out_path), "-hide_banner", "-loglevel", "error",
    ]
    print(f"  [inject/panel] {anim_mp4.name} @ {ins:.1f}s -> {out_path.name}")
    subprocess.run(cmd, check=True)
    return out_path


def _inject_fullscreen(
    source_video: Path,
    anim_mp4: Path,
    out_path: Path,
    insert_at: float,
    anim_duration: float,
    anim_sfx_vol: float,
) -> Path:
    """
    Fullscreen-режим: анимация играет на весь экран поверх видео.
    Видео продолжается под анимацией (озвучка не прерывается).
    """
    ins     = insert_at
    end     = ins + anim_duration
    ins_ms  = int(ins * 1000)

    filter_v = (
        f"[1:v]scale=1920:1080,fps=25,setpts=PTS-STARTPTS+{ins:.3f}/TB[anim];"
        f"[0:v][anim]overlay=x=0:y=0:enable='between(t,{ins:.3f},{end:.3f})'[vout]"
    )

    has_src_audio = _has_audio(source_video)
    if has_src_audio:
        filter_a = (
            f"[0:a]aformat=sample_rates=44100:channel_layouts=stereo[orig];"
            f"[1:a]volume={anim_sfx_vol},adelay={ins_ms}|{ins_ms},"
            f"aformat=sample_rates=44100:channel_layouts=stereo[asfx];"
            f"[orig][asfx]amix=inputs=2:duration=first:"
            f"dropout_transition=0:normalize=0[aout]"
        )
    else:
        filter_a = (
            f"[1:a]volume={anim_sfx_vol},adelay={ins_ms}|{ins_ms},"
            f"aformat=sample_rates=44100:channel_layouts=stereo[aout]"
        )

    cmd = [
        "ffmpeg", "-y",
        "-i", str(source_video), "-i", str(anim_mp4),
        "-filter_complex", filter_v + ";" + filter_a,
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "fast", "-crf", "17",
        "-c:a", "aac", "-b:a", "192k",
        str(out_path), "-hide_banner", "-loglevel", "error",
    ]
    print(f"  [inject/fullscreen] {anim_mp4.name} @ {ins:.1f}s -> {out_path.name}")
    subprocess.run(cmd, check=True)
    return out_path


def _has_audio(path: Path) -> bool:
    """Проверяет наличие аудио-потока в видеофайле."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    return bool(result.stdout.strip())


# ─── main ─────────────────────────────────────────────────────────────────────

def run(channel: str, session: str):
    channel_id  = CHANNEL_MAP.get(channel, channel)
    session_dir = get_session_dir(channel_id, session)
    plan_path   = session_dir / "motion_graphics_plan.json"
    mg_dir      = TEMP_ROOT / channel_id / session / "motion_graphics"
    mg_dir.mkdir(parents=True, exist_ok=True)

    if not plan_path.exists():
        print(f"ERROR: {plan_path} not found. Run mg_planner.py first.")
        sys.exit(1)

    plan  = json.loads(plan_path.read_text(encoding="utf-8"))
    zones = plan["zones"]

    print(f"[RENDERER] {len(zones)} zones to render -> {mg_dir}")

    for zone in zones:
        mp4 = render_zone(zone, mg_dir)
        zone["rendered_path"] = str(mp4) if mp4 else None

    plan["zones"] = zones
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    ok  = sum(1 for z in zones if z.get("rendered_path"))
    bad = len(zones) - ok
    print(f"\n[RENDERER] Done: {ok} OK, {bad} failed")
    return plan


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", default="de")
    ap.add_argument("--session", required=True)
    args = ap.parse_args()
    run(args.channel, args.session)
