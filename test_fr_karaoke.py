"""
test_fr_karaoke.py — тест субтитров FR канала (karaoke word-highlight).

Что тестируем:
  A) HEIGHT  — одинаковая y-позиция слов между группами (акцентированные vs нет)
  B) TIMING  — жёлтое слово переключается синхронно с аудио

Рендерит 60 секунд реального FR контента:
  - видео: speedtest FR videotrack (61s) с TIMECODE в правом нижнем углу
  - аудио: первые 60s реального audio.mp3
  - субтитры: настоящий ASS (intro_duration=0, данные из result_visual.json)

Запуск:
    python test_fr_karaoke.py
    python test_fr_karaoke.py --start 90 --dur 60   # другой отрезок аудио
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "agents" / "assembler"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from ass_generator import generate_karaoke_ass
from gpu_compositor import run as gpu_run, ANIM_WORD_HL

# ─── Пути ────────────────────────────────────────────────────────────────────
SESSION       = Path(r"D:\Video-pipeline-data\channels\fr\Video_20260402_180801")
RESULT_JSON   = SESSION / "transcripts" / "result_visual.json"
AUDIO_SRC     = SESSION / "input" / "audio.mp3"
VIDEOTRACK_SRC = Path("C:/Projects/Video-pipeline/data/speed_test/fr/videotrack.mp4")

OUT_DIR       = Path("D:/Video-pipeline-temp/test_karaoke_fr")
FONT_PATH     = "C:/Users/Serafim/AppData/Local/Microsoft/Windows/Fonts/MONTSERRAT-EXTRABOLD.TTF"
FONT_SIZE     = 70
FPS           = 25.0


def ffprobe_dur(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def make_timecode_video(src: Path, out: Path, duration: float) -> Path:
    """
    Trim videotrack до `duration` сек, прожигает timecode HH:MM:SS.ff
    в правом нижнем углу (белый текст, чёрная обводка, 28px).
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    # Windows FFmpeg: экранируем ':' в пути через '\\:' для drawtext
    font_win = "C\\:/Windows/Fonts/arialbd.ttf"
    # %{pts\:hms} — встроенный форматтер FFmpeg drawtext
    tc_filter = (
        f"drawtext=fontfile='{font_win}':fontsize=28:fontcolor=white"
        f":borderw=2:bordercolor=black"
        f":x=w-tw-20:y=h-th-20"
        r":text='%{pts\:hms}'"
    )
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(src),
        "-t", str(duration),
        "-vf", tc_filter,
        "-c:v", "h264_nvenc", "-preset", "p2", "-cq", "20",
        "-pix_fmt", "yuv420p", "-an",
        str(out),
    ], check=True)
    print(f"  Timecode video: {out.name} ({ffprobe_dur(out):.1f}s)")
    return out


def trim_audio(src: Path, out: Path, start: float, duration: float) -> Path:
    """Обрезает аудио: start → start+duration."""
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", str(start), "-t", str(duration),
        "-i", str(src),
        "-c:a", "aac", "-b:a", "192k",
        str(out),
    ], check=True)
    print(f"  Audio: {out.name} ({start:.0f}s–{start+duration:.0f}s)")
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=float, default=0.0,
                        help="Старт в аудио (сек), default=0")
    parser.add_argument("--dur",   type=float, default=60.0,
                        help="Длительность теста (сек), default=60")
    args = parser.parse_args()

    start_s = args.start
    dur_s   = args.dur

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print("\n" + "=" * 60)
    print(f"TEST FR KARAOKE  [{start_s:.0f}s – {start_s+dur_s:.0f}s]")
    print("=" * 60)

    # 1. ASS — генерируем с intro_duration=start_s так чтобы слова
    #    начинались с start_s. GPU compositor получит ASS с абсолютными
    #    временами (относительно аудио), а _skip_intro=False (нет intro).
    print(f"\n[1/4] Генерируем ASS (words {start_s:.0f}s–{start_s+dur_s:.0f}s)...")
    ass_path = OUT_DIR / f"test_karaoke_{int(start_s)}.ass"
    generate_karaoke_ass(
        result_json_path = str(RESULT_JSON),
        output_ass_path  = str(ass_path),
        font_name        = "Montserrat ExtraBold",
        font_size        = FONT_SIZE,
        fade_ms          = 80,
        rise_px          = 0,        # без сдвига — чистый тест позиции
        intro_duration   = start_s,  # показываем слова начиная с start_s
        max_words        = 3,
        col_active       = "&H0000FFFF",
        col_passive      = "&H00707070",
        border_style     = 1,
    )

    # Проверяем что ASS создан и не пустой
    if not ass_path.exists():
        print("  FAIL: ASS не создан")
        sys.exit(1)

    ass_lines = ass_path.read_text(encoding="utf-8").count("\nDialogue:")
    print(f"  OK: {ass_lines} строк в ASS")

    # Показываем несколько строк для диагностики
    print(f"\n  Первые 6 строк ASS:")
    for line in ass_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("Dialogue:"):
            print(f"    {line[:120]}")
        if line.count("Dialogue:") and len([l for l in ass_path.read_text(encoding='utf-8').splitlines() if l.startswith("Dialogue:")]) > 6:
            break
    ass_lines_all = [l for l in ass_path.read_text(encoding="utf-8").splitlines() if l.startswith("Dialogue:")]
    for l in ass_lines_all[:6]:
        print(f"    {l[:120]}")

    # 2. Видео с тайм-кодом
    print(f"\n[2/4] Создаём видео с тайм-кодом ({dur_s:.0f}s)...")
    vt_src_dur = ffprobe_dur(VIDEOTRACK_SRC)
    print(f"  Исходный videotrack: {vt_src_dur:.1f}s")

    # Если speedtest videotrack короче нужного — loop
    if vt_src_dur < dur_s:
        loops = int(dur_s / vt_src_dur) + 1
        loop_list = OUT_DIR / "_loop_list.txt"
        loop_list.write_text("\n".join(f"file '{VIDEOTRACK_SRC}'" for _ in range(loops)), encoding="utf-8")
        looped = OUT_DIR / "_looped.mp4"
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "concat", "-safe", "0", "-i", str(loop_list),
            "-c", "copy", str(looped),
        ], check=True)
        vt_source = looped
    else:
        vt_source = VIDEOTRACK_SRC

    tc_video = OUT_DIR / f"video_tc_{int(start_s)}.mp4"
    make_timecode_video(vt_source, tc_video, dur_s)

    # 3. Аудио — вырезаем нужный отрезок
    print(f"\n[3/4] Обрезаем аудио ({start_s:.0f}s → {start_s+dur_s:.0f}s)...")
    audio_trim = OUT_DIR / f"audio_{int(start_s)}.m4a"
    trim_audio(AUDIO_SRC, audio_trim, start_s, dur_s)

    # Сдвигаем ASS тайминги если start_s > 0:
    # ASS события сейчас в абсолютных временах (напр. 90s–150s),
    # но видео/аудио обрезаны и начинаются с 0.
    # → Нужно вычесть start_s из всех Start/End в ASS.
    if start_s > 0:
        print(f"\n  Сдвигаем ASS тайминги: -{start_s:.1f}s")

        def _shift_ass_time(ts: str, delta: float) -> str:
            """'0:01:30.50' → сдвигаем на -delta секунд."""
            h, m, rest = ts.split(":")
            s, cs = rest.split(".")
            total = int(h) * 3600 + int(m) * 60 + int(s) + int(cs) / 100
            total = max(0.0, total - delta)
            nh = int(total // 3600)
            nm = int((total % 3600) // 60)
            ns = int(total % 60)
            ncs = int(round((total % 1) * 100))
            return f"{nh}:{nm:02d}:{ns:02d}.{ncs:02d}"

        content = ass_path.read_text(encoding="utf-8")
        import re
        def _shift_line(m: re.Match) -> str:
            prefix = m.group(1)
            ts1    = m.group(2)
            ts2    = m.group(3)
            rest   = m.group(4)
            return f"{prefix}{_shift_ass_time(ts1, start_s)},{_shift_ass_time(ts2, start_s)}{rest}"
        # Dialogue: 0,H:MM:SS.cs,H:MM:SS.cs,...
        content = re.sub(
            r"(Dialogue:\s*\d+,)(\d:\d{2}:\d{2}\.\d{2}),(\d:\d{2}:\d{2}\.\d{2})(.*)",
            _shift_line, content
        )
        ass_path.write_text(content, encoding="utf-8")
        print(f"  OK: тайминги сдвинуты")

    # 4. GPU рендер
    print(f"\n[4/4] GPU рендер (karaoke)...")
    out_video = OUT_DIR / f"test_karaoke_fr_{int(start_s)}.mp4"
    t0 = time.time()

    ok = gpu_run(
        videotrack       = tc_video,
        output           = out_video,
        intro            = None,
        audio            = audio_trim,
        ass_path         = ass_path,
        color_grade_name = "none",   # без цветокоррекции — чистый тест
        fps              = FPS,
        intro_trans_dur  = 0.0,
        font_size        = FONT_SIZE,
        fade_ms          = 80,
        font_path        = FONT_PATH,
        subtitle_style   = "karaoke",
        subtitle_anim    = ANIM_WORD_HL,
    )

    elapsed = time.time() - t0
    size_mb = out_video.stat().st_size // 1024 // 1024 if out_video.exists() else 0
    status  = "OK" if ok else "FAIL"

    print(f"\n{'='*60}")
    print(f"РЕЗУЛЬТАТ: {status}  {elapsed:.1f}s  {size_mb}MB")
    print(f"Файл: {out_video}")
    print(f"{'='*60}")
    print(f"\nЧТО СМОТРЕТЬ:")
    print(f"  HEIGHT: слова в соседних группах — одинаковая высота снизу?")
    print(f"  TIMING: жёлтое слово переключается до / в момент / после произношения?")
    print(f"  TIMECODE: правый нижний угол — точное время кадра")
    print(f"  Если слово прыгает вверх/вниз — Height баг подтверждён")
    print(f"  Если жёлтое опережает/отстаёт от аудио — Timing баг подтверждён")


if __name__ == "__main__":
    main()
