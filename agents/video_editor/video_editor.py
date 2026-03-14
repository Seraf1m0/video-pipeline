"""
Video Editor — автосборка финального видео из premiere_data.json
-----------------------------------------------------------------
Применяет цветокоррекцию Lumetri, миксует аудио, добавляет субтитры.

Запуск:
  py agents/video_editor/video_editor.py
  py agents/video_editor/video_editor.py --session Video_20260307_141938
  py agents/video_editor/video_editor.py --session X --no-subs --no-music
"""

import argparse
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE_DIR        = Path(__file__).resolve().parent.parent.parent
OUTPUT_DIR      = BASE_DIR / "data" / "output"
MEDIA_DIR       = BASE_DIR / "data" / "media"
TRANSCRIPTS_DIR = BASE_DIR / "data" / "transcripts"

ORGANETTO_FONT = r"C:\Windows\Fonts\Organetto-Bold.ttf"

_SESSION_RE = re.compile(r"^Video_(\d{8}_\d{6})$")

# ─── FFmpeg path ──────────────────────────────────────────────────────────────

_FFMPEG_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / (
    "Microsoft/WinGet/Packages/"
    "Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe/"
    "ffmpeg-8.0.1-full_build/bin"
)
if _FFMPEG_DIR.exists():
    os.environ["PATH"] = str(_FFMPEG_DIR) + os.pathsep + os.environ.get("PATH", "")


# ─── Session helpers ──────────────────────────────────────────────────────────

def find_latest_session() -> str | None:
    if not OUTPUT_DIR.exists():
        return None
    candidates = []
    for d in OUTPUT_DIR.iterdir():
        m = _SESSION_RE.match(d.name)
        if m and (d / "premiere_data.json").exists():
            candidates.append((m.group(1), d.name))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def load_premiere_data(session: str) -> dict:
    path = OUTPUT_DIR / session / "premiere_data.json"
    if not path.exists():
        raise FileNotFoundError(f"premiere_data.json не найден: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


# ─── Lumetri → FFmpeg ─────────────────────────────────────────────────────────

def lumetri_to_ffmpeg(lumetri: dict) -> str:
    """
    Конвертирует Premiere Lumetri параметры в строку FFmpeg фильтров.

    Premiere диапазоны:
      temperature : -100..+100  (отрицательный = холоднее/синее)
      exposure    : EV, обычно -5..+5
      contrast    : -100..+100  (0 = норма)
      highlights  : -100..+100  (0 = норма)
      whites      : -100..+100  (0 = норма)
      saturation  : 0..200      (100 = норма)
    """
    temperature = lumetri.get("temperature", 0)
    exposure    = lumetri.get("exposure", 0)
    contrast    = lumetri.get("contrast", 0)
    highlights  = lumetri.get("highlights", 0)
    whites      = lumetri.get("whites", 0)
    saturation  = lumetri.get("saturation", 100)

    filters = []

    # ── eq: brightness / contrast / saturation ────────────────────────────────
    eq_parts = []

    if abs(exposure) > 0.01:
        b = max(-1.0, min(1.0, exposure * 0.075))
        eq_parts.append(f"brightness={b:.4f}")

    if abs(contrast) > 0.1:
        c = max(0.1, min(3.0, 1.0 + contrast * 0.01))
        eq_parts.append(f"contrast={c:.4f}")

    if abs(saturation - 100) > 0.5:
        s = max(0.0, min(3.0, saturation / 100.0))
        eq_parts.append(f"saturation={s:.4f}")

    if eq_parts:
        filters.append(f"eq={':'.join(eq_parts)}")

    # ── colorbalance: temperature ─────────────────────────────────────────────
    # Premiere: отрицательный = холоднее (больше синего, меньше красного)
    if abs(temperature) > 0.5:
        scale = temperature / 100.0 * 0.15   # max ±0.15
        # Тёплый = +красный/-синий; холодный = -красный/+синий
        rs = f"{-scale:.4f}";  bs = f"{scale:.4f}"
        rm = f"{-scale*0.8:.4f}"; bm = f"{scale*0.8:.4f}"
        rh = f"{-scale*0.5:.4f}"; bh = f"{scale*0.5:.4f}"
        filters.append(
            f"colorbalance=rs={rs}:gs=0:bs={bs}"
            f":rm={rm}:gm=0:bm={bm}"
            f":rh={rh}:gh=0:bh={bh}"
        )

    # ── curves: highlights / whites ───────────────────────────────────────────
    if abs(highlights) > 0.5 or abs(whites) > 0.5:
        h_adj = highlights / 100.0 * 0.15
        w_adj = whites    / 100.0 * 0.10
        p_hl  = max(0.01, min(0.99, 0.75 + h_adj))
        p_wh  = max(0.01, min(0.99, 0.95 + w_adj))
        filters.append(
            f"curves=all='0/0 0.5/0.5 0.75/{p_hl:.4f} 0.95/{p_wh:.4f} 1/1'"
        )

    return ",".join(filters)


# ─── Build concat file ────────────────────────────────────────────────────────

def build_concat_file(clip_paths: list[str], out_path: Path) -> Path:
    """Создаёт файл-список для FFmpeg concat demuxer."""
    with open(out_path, "w", encoding="utf-8") as f:
        for p in clip_paths:
            # FFmpeg требует forward slashes и экранированные апострофы
            escaped = str(p).replace("\\", "/").replace("'", "\\'")
            f.write(f"file '{escaped}'\n")
    return out_path


# ─── Check NVENC ──────────────────────────────────────────────────────────────

def check_nvenc() -> bool:
    try:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=10,
        )
        return "h264_nvenc" in r.stdout
    except Exception:
        return False


# ─── Music track pre-mix ──────────────────────────────────────────────────────

def prepare_music_track(data: dict, out_dir: Path, log_fn=None) -> Path | None:
    """
    Возвращает путь к music_track.aac:
    - если файл уже есть в out_dir — возвращает его
    - иначе конкатенирует music_tracks из premiere_data.json в один aac
    """
    music_out = out_dir / "music_track.aac"
    if music_out.exists():
        return music_out

    tracks = [t for t in (data.get("music_tracks") or []) if Path(t).exists()]
    if not tracks:
        return None

    if log_fn:
        log_fn(f"Готовлю музыку: {len(tracks)} треков → music_track.aac")

    concat_txt = out_dir / "music_concat.txt"
    with open(concat_txt, "w", encoding="utf-8") as f:
        for t in tracks:
            escaped = str(t).replace("\\", "/").replace("'", "\\'")
            f.write(f"file '{escaped}'\n")

    r = subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "concat", "-safe", "0", "-i", str(concat_txt),
         "-c:a", "aac", "-b:a", "192k", str(music_out)],
        capture_output=True, encoding="utf-8", errors="replace",
    )
    if r.returncode != 0:
        if log_fn:
            log_fn(f"⚠️ Music mix failed: {r.stderr[-300:]}")
        return None
    return music_out


# ─── Main assembly ────────────────────────────────────────────────────────────

def assemble(
    session: str,
    settings: dict | None = None,
    with_subs: bool = True,
    with_music: bool = True,
    on_line=None,
) -> Path:
    """
    Собирает финальное видео из premiere_data.json.
    settings может содержать override-значения lumetri + audio dB.
    Возвращает путь к готовому файлу.
    """
    data    = load_premiere_data(session)
    out_dir = OUTPUT_DIR / session
    out_dir.mkdir(parents=True, exist_ok=True)

    lumetri = (settings or {}).get("lumetri") or data.get("lumetri", {})
    vo_db    = float((settings or {}).get("voDb",    data.get("voiceover_db",   -6.0)))
    music_db = float((settings or {}).get("musicDb", data.get("music_db",      -33.0)))
    intro_db = float((settings or {}).get("introDb", data.get("intro_audio_db",-37.0)))

    # Intro: prefer videos_upscaled/ if available
    intro_path = data["intro"]["path"]
    intro_p    = Path(intro_path)
    upscaled_intro = intro_p.parent.parent / "videos_upscaled" / intro_p.name
    if upscaled_intro.exists():
        intro_path = str(upscaled_intro)

    intro_dur   = float(data["intro"]["duration"])
    clip_paths  = [c["path"] for c in data["clips"]]
    voiceover   = data["voiceover"]
    music_start = float(data.get("music_start_sec", 88.0))


    # Subtitles: check premiere_data.json first, then transcripts fallback
    srt_path = data.get("subtitles_path") or ""
    if not srt_path or not Path(srt_path).exists():
        srt_fallback = TRANSCRIPTS_DIR / session / "subtitles.srt"
        if srt_fallback.exists():
            srt_path = str(srt_fallback)

    def log(msg: str):
        print(f"[VideoEditor] {msg}", flush=True)
        if on_line:
            on_line(msg)

    # ── Проверки ──────────────────────────────────────────────────────────────
    for label, p in [("intro", intro_path), ("voiceover", voiceover)]:
        if not Path(p).exists():
            raise FileNotFoundError(f"{label} не найден: {p}")

    missing = [c["path"] for c in data["clips"] if not Path(c["path"]).exists()]
    if missing:
        log(f"⚠️  Пропущено клипов (файл не найден): {len(missing)}")
        clip_paths = [c["path"] for c in data["clips"] if Path(c["path"]).exists()]

    # Music: pre-mix if needed
    music_track_path = None
    if with_music:
        music_track_path = prepare_music_track(data, out_dir, log)

    has_music = music_track_path is not None
    has_subs  = with_subs and bool(srt_path) and Path(srt_path).exists()

    log(f"Сессия  : {session}")
    log(f"Клипов  : {len(clip_paths)}")
    log(f"Музыка  : {'✅' if has_music else '❌ нет треков'}")
    log(f"Субтитры: {'✅ ' + Path(srt_path).name if has_subs else '❌'}")

    use_gpu = check_nvenc()
    log(f"Кодек   : {'h264_nvenc (GPU)' if use_gpu else 'libx264 (CPU)'}")

    # ── Concat file ───────────────────────────────────────────────────────────
    concat_file = out_dir / "concat_list.txt"
    build_concat_file([intro_path] + clip_paths, concat_file)


    # ── Filters ───────────────────────────────────────────────────────────────
    color_vf = lumetri_to_ffmpeg(lumetri)
    log(f"Цвет    : {color_vf or '(без коррекции)'}")

    # Subtitle filter
    if has_subs:
        srt_escaped = str(srt_path).replace("\\", "/").replace("'", "\\'").replace(":", "\\:")
        sub_style = (
            "FontName=Organetto,FontSize=52,"
            "PrimaryColour=&H00FFFFFF,BackColour=&H80000000,"
            "BorderStyle=3,Outline=0,Shadow=0,Alignment=2,MarginV=40"
        )
        sub_vf = f"subtitles='{srt_escaped}':force_style='{sub_style}'"
    else:
        sub_vf = ""

    # Combined video filter chain
    vf_chain_parts = [p for p in [color_vf, sub_vf] if p]
    vf_chain = ",".join(vf_chain_parts) if vf_chain_parts else "null"

    # ── FFmpeg command ────────────────────────────────────────────────────────
    output_file = out_dir / "final_premiere.mp4"

    inputs = [
        "-f", "concat", "-safe", "0", "-i", str(concat_file),   # 0: video
        "-i", str(intro_path),                                    # 1: intro audio
        "-i", str(voiceover),                                     # 2: voiceover
    ]
    n_inputs = 3

    if has_music:
        inputs += ["-i", str(music_track_path)]
        music_idx = n_inputs
        n_inputs += 1
    else:
        music_idx = None

    # Build filter_complex
    music_delay_ms = int(music_start * 1000)
    fc_parts = [
        f"[0:v]{vf_chain}[vout]",
        f"[1:a]volume={intro_db:.1f}dB,atrim=0:{intro_dur:.3f},asetpts=PTS-STARTPTS[intro_a]",
        f"[2:a]volume={vo_db:.1f}dB[vo]",
    ]

    if has_music:
        fc_parts.append(
            f"[{music_idx}:a]adelay={music_delay_ms}|{music_delay_ms},"
            f"volume={music_db:.1f}dB[music]"
        )
        # [vo] is FIRST → amix duration=first uses voiceover length
        # (voiceover ~ matches video duration; players stop at video end anyway)
        fc_parts.append(
            "[vo][intro_a][music]amix=inputs=3:duration=first:normalize=0[aout]"
        )
    else:
        fc_parts.append(
            "[vo][intro_a]amix=inputs=2:duration=first:normalize=0[aout]"
        )

    filter_complex = ";".join(fc_parts)

    if use_gpu:
        encode_v = ["-c:v", "h264_nvenc", "-preset", "p4",
                    "-b:v", "25M", "-maxrate", "30M", "-bufsize", "60M",
                    "-pix_fmt", "yuv420p"]
    else:
        encode_v = ["-c:v", "libx264", "-preset", "fast",
                    "-b:v", "25M", "-maxrate", "30M", "-bufsize", "60M"]

    cmd = (
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-stats"]
        + inputs
        + ["-filter_complex", filter_complex,
           "-map", "[vout]", "-map", "[aout]"]
        + encode_v
        + ["-c:a", "aac", "-b:a", "192k",
           str(output_file)]
    )

    log(f"Запускаю FFmpeg...")
    log(f"Выход   : {output_file}")

    t0 = time.monotonic()
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        encoding="utf-8", errors="replace",
        cwd=str(BASE_DIR),
    )
    for line in proc.stdout:
        line = line.rstrip()
        if line:
            print(line, flush=True)
            if on_line:
                on_line(line)
    proc.wait()

    elapsed = time.monotonic() - t0
    mins, secs = divmod(int(elapsed), 60)

    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg завершился с ошибкой (rc={proc.returncode})")

    size_mb = output_file.stat().st_size / 1024 / 1024
    log(f"✅ Готово! {output_file.name} — {size_mb:.1f} MB за {mins}м {secs}с")
    return output_file


# ─── CLI ──────────────────────────────────────────────────────────────────────

def run() -> None:
    parser = argparse.ArgumentParser(description="Автосборка видео из premiere_data.json")
    parser.add_argument("--session",   help="Имя сессии (по умолчанию — последняя)")
    parser.add_argument("--no-subs",   action="store_true", help="Без субтитров")
    parser.add_argument("--no-music",  action="store_true", help="Без музыки")
    args = parser.parse_args()

    session = args.session or find_latest_session()
    if not session:
        print("[VideoEditor] ❌ Нет сессий с premiere_data.json в data/output/")
        sys.exit(1)

    try:
        out = assemble(
            session,
            with_subs=not args.no_subs,
            with_music=not args.no_music,
        )
        print(f"[VideoEditor] 🎬 {out}")
    except Exception as e:
        print(f"[VideoEditor] ❌ {e}")
        sys.exit(1)


if __name__ == "__main__":
    run()
