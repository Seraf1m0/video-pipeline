"""
test_subtitle_channels.py — тест субтитров с реальной озвучкой для всех каналов.

Для каждого канала (DE / FR / ES):
  1. Берёт 5 рандомных клипов из последней сессии
  2. Обрезает audio.mp3: 30 секунд начиная с intro_duration (90s)
  3. Генерирует ASS из реального result.json, сдвигает тайминги на -intro_duration
  4. GPU-рендер: видеоряд + аудио + субтитры → test_ch_<канал>.mp4

Запуск:
    python test_subtitle_channels.py [--out D:/Video-pipeline-temp] [--seed 42]
"""
import argparse
import json
import random
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "agents" / "assembler"))
from gpu_compositor import run, ANIM_SLIDE_UP, ANIM_FADE_SCALE, ANIM_WORD_HL, ANIM_FADE
from ass_generator import generate_ass, generate_karaoke_ass, generate_scripture_ass

# ─── Константы ────────────────────────────────────────────────────────────────
CHANNELS_ROOT  = Path("D:/Video-pipeline-data/channels")
OUT_DIR        = Path("D:/Video-pipeline-temp")
INTRO_DURATION = 90.0   # секунд интро (пропускаем при генерации субтитров)
TEST_DURATION  = 30.0   # секунд теста после интро
N_CLIPS        = 5
CLIP_DUR       = 6.0    # секунд из каждого клипа
SUBTITLE_BASE_SIZE = 28

FONT_MONTSERRAT = "C:/Users/Serafim/AppData/Local/Microsoft/Windows/Fonts/MONTSERRAT-EXTRABOLD.TTF"
FONT_ARIAL      = "C:/Windows/Fonts/ariblk.ttf"  # Arial Black для ES

# ─── Конфиг каналов ───────────────────────────────────────────────────────────
CHANNEL_CFG = {
    "de": {
        "dir":            "de",
        "label":          "DE",
        "subtitle_style": "default",
        "subtitle_anim":  ANIM_SLIDE_UP,
        "color_grade":    "warm_cinematic",
        "font_size":      SUBTITLE_BASE_SIZE + 16,   # 44
        "font_path":      FONT_MONTSERRAT,
        "fade_ms":        120,
        "ass_gen":        "default",
    },
    "fr": {
        "dir":            "fr",
        "label":          "FR",
        "subtitle_style": "karaoke",
        "subtitle_anim":  ANIM_WORD_HL,
        "color_grade":    "cool_cinematic",
        "font_size":      SUBTITLE_BASE_SIZE + 24,   # 52
        "font_path":      FONT_MONTSERRAT,
        "fade_ms":        60,
        "ass_gen":        "karaoke",
    },
    "es": {
        "dir":            "es",
        "label":          "ES",
        "subtitle_style": "scripture",
        "subtitle_anim":  ANIM_FADE_SCALE,
        "color_grade":    "gold_dramatic",
        "font_size":      SUBTITLE_BASE_SIZE + 26,   # 54
        "font_path":      FONT_ARIAL,
        "fade_ms":        150,
        "ass_gen":        "scripture",
    },
}


# ─── Helpers ──────────────────────────────────────────────────────────────────

def latest_session(channel_dir: Path) -> Path:
    sessions = sorted(
        [d for d in channel_dir.iterdir() if d.is_dir() and d.name.startswith("Video_")],
        reverse=True,
    )
    if not sessions:
        raise FileNotFoundError(f"Нет сессий в {channel_dir}")
    return sessions[0]


SPEED_TEST_ROOT = Path("D:/Video-pipeline-data/speed_test")

def pick_clips(session: Path, count: int, clip_dur: float) -> list[Path]:
    """Ищет клипы: intro_clips сессии → intro_clips других сессий → speed_test videotrack."""
    # 1. intro_clips текущей сессии
    clips_dir = session / "intro_clips"
    if clips_dir.exists():
        all_clips = sorted(clips_dir.glob("*.mp4"))
        if all_clips:
            return random.sample(all_clips, min(count, len(all_clips)))

    # 2. intro_clips других сессий того же канала
    channel_dir = session.parent
    for other in sorted(channel_dir.iterdir(), reverse=True):
        if not other.is_dir() or other == session:
            continue
        other_clips = sorted((other / "intro_clips").glob("*.mp4"))
        if other_clips:
            return random.sample(other_clips, min(count, len(other_clips)))

    # 3. Fallback: speed_test videotrack для этого канала (нарезаем кусочками)
    ch_name = channel_dir.name   # "de" / "fr" / "es"
    vt = SPEED_TEST_ROOT / ch_name / "videotrack.mp4"
    if vt.exists():
        print(f"         (fallback: speed_test/{ch_name}/videotrack.mp4)")
        return [vt] * count      # один файл, concat_clips возьмёт из него N кусочков

    raise FileNotFoundError(f"Нет клипов для {session}")


def concat_clips(clips: list[Path], out: Path, clip_dur: float) -> float:
    """Обрезать каждый клип до clip_dur и склеить в один файл.
    Если все clips одинаковые (fallback), нарезаем с разными offset'ами."""
    trimmed = []
    unique = len(set(clips)) == 1 and len(clips) > 1
    for i, c in enumerate(clips):
        t = out.parent / f"_trim_{out.stem}_{i:02d}.mp4"
        ss = ["-ss", str(i * clip_dur)] if unique else []
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            *ss, "-i", str(c), "-t", str(clip_dur),
            "-c:v", "h264_nvenc", "-preset", "p1",
            "-pix_fmt", "yuv420p", "-an", str(t),
        ], check=True)
        trimmed.append(t)

    lst = out.parent / f"_concat_{out.stem}.txt"
    lst.write_text("\n".join(f"file '{p}'" for p in trimmed), encoding="utf-8")
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "concat", "-safe", "0", "-i", str(lst),
        "-c", "copy", str(out),
    ], check=True)
    for t in trimmed:
        t.unlink(missing_ok=True)
    lst.unlink(missing_ok=True)

    r = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=duration", "-of", "csv=p=0", str(out),
    ], capture_output=True, text=True)
    return float(r.stdout.strip() or 0)


def trim_audio(src: Path, out: Path, start: float, duration: float) -> Path:
    """Обрезать аудио: start → start+duration секунд."""
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", str(start), "-t", str(duration),
        "-i", str(src), "-c:a", "copy", str(out),
    ], check=True)
    return out


def _ass_time_to_sec(t: str) -> float:
    h, m, s = t.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def _sec_to_ass_time(secs: float) -> str:
    secs = max(0.0, secs)
    h  = int(secs // 3600)
    m  = int((secs % 3600) // 60)
    s  = int(secs % 60)
    cs = int((secs % 1) * 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def shift_ass(src: Path, out: Path, offset_s: float) -> Path:
    """
    Сдвигает все Dialogue-тайминги в ASS на offset_s секунд.
    Используется чтобы перевести абсолютные тайминги (от начала аудио)
    в относительные (от начала теста = INTRO_DURATION).
    """
    pattern = re.compile(
        r"^(Dialogue:\s*\d+,)"
        r"(\d+:\d{2}:\d{2}\.\d{2})"
        r","
        r"(\d+:\d{2}:\d{2}\.\d{2})"
        r","
    )
    lines_out = []
    with open(src, encoding="utf-8") as f:
        for line in f:
            m = pattern.match(line)
            if m:
                t_start = _ass_time_to_sec(m.group(2)) + offset_s
                t_end   = _ass_time_to_sec(m.group(3)) + offset_s
                if t_end <= 0:          # субтитр полностью до начала — пропускаем
                    continue
                t_start = max(0.0, t_start)
                line = (
                    m.group(1)
                    + _sec_to_ass_time(t_start) + ","
                    + _sec_to_ass_time(t_end) + ","
                    + line[m.end():]
                )
            lines_out.append(line)
    out.write_text("".join(lines_out), encoding="utf-8")
    return out


# ─── Основная логика ──────────────────────────────────────────────────────────

def render_channel(ch_key: str, cfg: dict, out_dir: Path, seed: int | None) -> dict:
    if seed is not None:
        random.seed(seed)

    label    = cfg["label"]
    ch_dir   = CHANNELS_ROOT / cfg["dir"]
    session  = latest_session(ch_dir)
    print(f"\n{'─'*60}")
    print(f"[{label}] Сессия: {session.name}")

    result_json = session / "transcripts" / "result.json"
    audio_src   = session / "input" / "audio.mp3"
    if not audio_src.exists():
        audio_src = session / "input" / "Audio.mp3"  # ES капсом

    # 1. Клипы
    print(f"[{label}] Выбираем {N_CLIPS} клипов...")
    clips = pick_clips(session, N_CLIPS, CLIP_DUR)
    for c in clips:
        print(f"         {c.name}")

    videotrack = out_dir / f"_test_{label}_track.mp4"
    vid_dur = concat_clips(clips, videotrack, CLIP_DUR)
    print(f"[{label}] Videotrack: {vid_dur:.1f}s")

    # 2. Аудио
    audio_test = out_dir / f"_test_{label}_audio.mp3"
    trim_audio(audio_src, audio_test, INTRO_DURATION, TEST_DURATION)
    print(f"[{label}] Аудио обрезано: {INTRO_DURATION:.0f}s → {INTRO_DURATION+TEST_DURATION:.0f}s")

    # 3. ASS субтитры
    ass_raw = out_dir / f"_test_{label}_raw.ass"
    ass_gen = cfg["ass_gen"]
    print(f"[{label}] Генерируем ASS ({ass_gen})...")

    if ass_gen == "default":
        generate_ass(
            result_json_path = result_json,
            output_ass_path  = ass_raw,
            font_name        = "Montserrat",
            font_size        = cfg["font_size"],
            intro_duration   = INTRO_DURATION,
        )
    elif ass_gen == "karaoke":
        generate_karaoke_ass(
            result_json_path = result_json,
            output_ass_path  = ass_raw,
            font_name        = "Montserrat Bold",
            font_size        = cfg["font_size"],
            intro_duration   = INTRO_DURATION,
        )
    elif ass_gen == "scripture":
        generate_scripture_ass(
            result_json_path = result_json,
            output_ass_path  = ass_raw,
            font_name        = "Arial Black",
            font_size        = cfg["font_size"],
            intro_duration   = INTRO_DURATION,
        )

    # Сдвигаем тайминги: субтитры начинаются с INTRO_DURATION в ASS,
    # но наше тестовое видео начинается с 0s → сдвиг = -INTRO_DURATION
    ass_final = out_dir / f"_test_{label}_final.ass"
    shift_ass(ass_raw, ass_final, offset_s=-INTRO_DURATION)
    print(f"[{label}] ASS сдвинут на -{INTRO_DURATION:.0f}s")

    # 4. GPU рендер
    output = out_dir / f"test_ch_{label.lower()}.mp4"
    print(f"[{label}] GPU рендер → {output.name}")
    t0 = time.time()

    ok = run(
        videotrack       = videotrack,
        output           = output,
        intro            = None,
        audio            = audio_test,
        ass_path         = ass_final,
        color_grade_name = cfg["color_grade"],
        fps              = 25.0,
        intro_trans_dur  = 0.0,
        font_size        = cfg["font_size"],
        fade_ms          = cfg["fade_ms"],
        font_path        = cfg["font_path"],
        subtitle_style   = cfg["subtitle_style"],
        subtitle_anim    = cfg["subtitle_anim"],
    )

    elapsed = time.time() - t0
    size_mb = output.stat().st_size // 1024 // 1024 if output.exists() else 0

    # Чистим временные файлы
    for tmp in [videotrack, audio_test, ass_raw]:
        tmp.unlink(missing_ok=True)

    return {
        "label":   label,
        "ok":      ok,
        "elapsed": elapsed,
        "size_mb": size_mb,
        "output":  output,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out",  default=str(OUT_DIR))
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--ch",   default=None,
                        help="Только один канал: de / fr / es")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    channels = list(CHANNEL_CFG.items())
    if args.ch:
        channels = [(k, v) for k, v in channels if k == args.ch]

    print("\n" + "=" * 60)
    print(f"TEST: субтитры с озвучкой — {len(channels)} канал(а)")
    print(f"Клипов: {N_CLIPS} x {CLIP_DUR:.0f}s = {N_CLIPS*CLIP_DUR:.0f}s видео")
    print(f"Аудио: {INTRO_DURATION:.0f}s → {INTRO_DURATION+TEST_DURATION:.0f}s")
    print("=" * 60)

    results = []
    for ch_key, cfg in channels:
        try:
            r = render_channel(ch_key, cfg, out_dir, args.seed)
            results.append(r)
        except Exception as e:
            print(f"[{cfg['label']}] ОШИБКА: {e}")
            results.append({"label": cfg["label"], "ok": False,
                            "elapsed": 0, "size_mb": 0, "output": None})

    print("\n" + "=" * 60)
    print("ИТОГ:")
    for r in results:
        status = "OK " if r["ok"] else "ERR"
        out_name = r["output"].name if r["output"] else "—"
        print(f"  {status}  {r['label']:<4}  {r['elapsed']:.1f}s  {r['size_mb']}MB  → {out_name}")
    print(f"\nФайлы: {out_dir}")


if __name__ == "__main__":
    main()
