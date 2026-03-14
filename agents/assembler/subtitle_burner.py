"""
subtitle_burner.py — вжечь субтитры в видео через FFmpeg subtitles filter.

Шрифт по умолчанию: Organetto-Bold (берётся из ORGANETTO_FONT_PATH в .env).
Стиль: белый текст, полупрозрачный тёмный бокс, нижнее положение.

Использование:
  from subtitle_burner import burn_subtitles
  burn_subtitles(
      video_path  = Path("with_audio.mp4"),
      srt_path    = Path("subtitles.srt"),
      output_path = Path("final_complete.mp4"),
  )

Запуск из командной строки:
  py subtitle_burner.py --video with_audio.mp4 --srt subtitles.srt
                        --output final.mp4 --font-size 52
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

# ── Загрузка .env ────────────────────────────────────────────────────────────

_BASE_DIR = Path(__file__).resolve().parent.parent.parent
try:
    from dotenv import load_dotenv
    load_dotenv(_BASE_DIR / "config" / ".env")
except ImportError:
    pass

# Путь к шрифту из .env или системный fallback
DEFAULT_FONT_PATH = os.environ.get(
    "ORGANETTO_FONT_PATH",
    r"C:\Windows\Fonts\Organetto-Bold.ttf",
).strip()

# ── Параметры энкодинга ──────────────────────────────────────────────────────

OUTPUT_BITRATE  = os.environ.get("OUTPUT_BITRATE",  "25M")
OUTPUT_MAXRATE  = os.environ.get("OUTPUT_MAXRATE",  "30M")
OUTPUT_BUFSIZE  = os.environ.get("OUTPUT_BUFSIZE",  "60M")

# NVENC (GPU) — если недоступен, упадём на libx264
USE_NVENC      = True
NVENC_PRESET   = "p4"
X264_PRESET    = "fast"


# ── Вспомогательные функции ──────────────────────────────────────────────────

def _video_encode_args(use_nvenc: bool) -> list[str]:
    """Параметры видеокодека."""
    if use_nvenc:
        return [
            "-c:v", "h264_nvenc",
            "-preset", NVENC_PRESET,
            "-b:v", OUTPUT_BITRATE,
            "-maxrate", OUTPUT_MAXRATE,
            "-bufsize", OUTPUT_BUFSIZE,
            "-pix_fmt", "yuv420p",
        ]
    return [
        "-c:v", "libx264",
        "-preset", X264_PRESET,
        "-b:v", OUTPUT_BITRATE,
        "-maxrate", OUTPUT_MAXRATE,
        "-bufsize", OUTPUT_BUFSIZE,
        "-pix_fmt", "yuv420p",
    ]


def _make_srt_relative(srt_path: Path, output_path: Path, base_dir: Path) -> tuple[str, Path | None]:
    """
    FFmpeg subtitles filter на Windows не переваривает пути вида C:\\...
    Решение: передаём путь ОТНОСИТЕЛЬНО base_dir и запускаем FFmpeg с cwd=base_dir.

    Возвращает (srt_rel_posix, копия_если_нужна_или_None).
    """
    try:
        srt_rel = srt_path.relative_to(base_dir).as_posix()
        return srt_rel, None
    except ValueError:
        # SRT вне base_dir — копируем рядом с выходным файлом
        srt_copy = output_path.parent / "sub.srt"
        shutil.copy2(str(srt_path), str(srt_copy))
        try:
            srt_rel = srt_copy.relative_to(base_dir).as_posix()
            return srt_rel, srt_copy
        except ValueError:
            # Совсем плохой путь — передаём абсолютный (может не работать)
            return str(srt_copy).replace("\\", "/"), srt_copy


# ── Главная функция ──────────────────────────────────────────────────────────

def burn_subtitles(
    video_path:   Path | str,
    srt_path:     Path | str,
    output_path:  Path | str,
    font_path:    str | None  = None,
    font_size:    int         = 28,
    primary_color: str        = "&H00FFFFFF",   # белый
    back_color:   str         = "&H80000000",   # полупрозрачный чёрный
    margin_v:     int         = 25,
    alignment:    int         = 2,              # 2 = низ по центру
    bold:         bool        = True,           # жирный шрифт
    use_nvenc:    bool        = USE_NVENC,
) -> bool:
    """
    Вжечь SRT-субтитры в видео через FFmpeg subtitles filter.

    Параметры
    ---------
    video_path    : исходное видео (с аудио)
    srt_path      : файл субтитров (.srt)
    output_path   : итоговый файл
    font_path     : путь к .ttf/.otf (None → DEFAULT_FONT_PATH из .env)
    font_size     : размер шрифта в пикселях
    primary_color : цвет текста в ASS hex (&H00RRGGBB или &H00FFFFFF)
    back_color    : фон бокса (&H80000000 = 50% прозрачный чёрный)
    margin_v      : отступ снизу в пикселях
    alignment     : выравнивание ASS (2=центр-низ, 5=центр-середина)
    use_nvenc     : использовать GPU-кодирование (h264_nvenc)

    Возвращает True при успехе, False при ошибке.
    """
    video_path  = Path(video_path)
    srt_path    = Path(srt_path)
    output_path = Path(output_path)

    if font_path is None:
        font_path = DEFAULT_FONT_PATH

    # ── Валидация ──────────────────────────────────────────────────────────
    if not video_path.exists():
        print(f"  ❌ Видео не найдено: {video_path}")
        return False
    if not srt_path.exists():
        print(f"  ❌ SRT не найден: {srt_path}")
        return False

    # Предупреждение если шрифт не найден
    if not Path(font_path).exists():
        print(f"  ⚠️ Шрифт не найден: {font_path} — используем системный Arial")
        font_name = "Arial"
    else:
        font_name = Path(font_path).stem

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # ── Подготовка пути SRT ────────────────────────────────────────────────
    srt_rel, srt_copy = _make_srt_relative(srt_path, output_path, _BASE_DIR)

    # ── force_style ASS параметры ──────────────────────────────────────────
    force_style = (
        f"FontName={font_name},"
        f"FontSize={font_size},"
        f"Bold={'1' if bold else '0'},"
        f"PrimaryColour={primary_color},"
        f"BackColour={back_color},"
        f"BorderStyle=3,"
        f"Outline=0,"
        f"Shadow=0,"
        f"Alignment={alignment},"
        f"MarginV={margin_v}"
    )

    vf = f"subtitles={srt_rel}:force_style='{force_style}'"

    # ── FFmpeg команда ─────────────────────────────────────────────────────
    enc_label = "h264_nvenc" if use_nvenc else "libx264"
    print(f"  📝 Субтитры: {srt_path.name}")
    print(f"  🔤 Шрифт: {font_name}, {font_size}px")
    print(f"  🎬 Кодек: {enc_label} → {output_path.name}")

    cmd = (
        ["ffmpeg", "-y",
         "-i", str(video_path),
         "-vf", vf]
        + _video_encode_args(use_nvenc)
        + ["-c:a", "copy",
           "-loglevel", "warning",
           str(output_path)]
    )

    result = subprocess.run(cmd, cwd=str(_BASE_DIR))

    # ── Fallback: libx264 если NVENC не сработал ───────────────────────────
    if result.returncode != 0 and use_nvenc:
        print(f"  ⚠️ NVENC ошибка (rc={result.returncode}), пробую libx264 ...")
        cmd2 = (
            ["ffmpeg", "-y",
             "-i", str(video_path),
             "-vf", vf]
            + _video_encode_args(False)
            + ["-c:a", "copy",
               "-loglevel", "warning",
               str(output_path)]
        )
        result = subprocess.run(cmd2, cwd=str(_BASE_DIR))

    # Удалить временную копию SRT если создавали
    if srt_copy and srt_copy.exists():
        srt_copy.unlink(missing_ok=True)

    if result.returncode == 0 and output_path.exists():
        size_mb = output_path.stat().st_size / 1024 / 1024
        print(f"  ✅ Субтитры вожжены: {output_path.name} ({size_mb:.1f} МБ)")
        return True
    else:
        print(f"  ❌ Ошибка наложения субтитров (rc={result.returncode})")
        return False


# ── burn_ass: ASS-субтитры (анимированные) ───────────────────────────────────

def burn_ass(
    video_path:  "Path | str",
    ass_path:    "Path | str",
    output_path: "Path | str",
    use_nvenc:   bool = USE_NVENC,
) -> bool:
    """
    Вжечь ASS-субтитры в видео через FFmpeg ass= filter.

    Поддерживает Windows-пути с буквой диска (C:\\...) и кастомный fontsdir.
    В отличие от burn_subtitles, не требует force_style — стиль задаётся в .ass файле.

    Параметры
    ---------
    video_path  : исходное видео (с аудио)
    ass_path    : файл субтитров (.ass)
    output_path : итоговый файл
    use_nvenc   : использовать GPU-кодирование (h264_nvenc)

    Возвращает True при успехе, False при ошибке.
    """
    video_path  = Path(video_path)
    ass_path    = Path(ass_path)
    output_path = Path(output_path)

    if not video_path.exists():
        print(f"  ❌ Видео не найдено: {video_path}")
        return False
    if not ass_path.exists():
        print(f"  ❌ ASS не найден: {ass_path}")
        return False

    # ── Windows-совместимый путь для FFmpeg ass= filter ────────────────────
    # FFmpeg ожидает: C\:/path/to/sub.ass  (двоеточие экранируется)
    ass_posix = ass_path.as_posix()                  # backslashes → /
    if len(ass_posix) >= 2 and ass_posix[1] == ":":  # Windows drive letter
        ass_posix = ass_posix[0] + "\\:" + ass_posix[2:]

    # ── fontsdir — папка со шрифтом Organetto ──────────────────────────────
    font_dir     = ""
    font_env_str = DEFAULT_FONT_PATH  # уже загружен из .env
    if font_env_str and Path(font_env_str).exists():
        fd = Path(font_env_str).parent.as_posix()
        if len(fd) >= 2 and fd[1] == ":":
            fd = fd[0] + "\\:" + fd[2:]
        font_dir = fd

    if font_dir:
        vf = f"ass='{ass_posix}':fontsdir='{font_dir}'"
    else:
        vf = f"ass='{ass_posix}'"

    output_path.parent.mkdir(parents=True, exist_ok=True)

    enc_label = "h264_nvenc" if use_nvenc else "libx264"
    print(f"  📝 ASS субтитры: {ass_path.name}")
    print(f"  🎬 Кодек: {enc_label} → {output_path.name}")

    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-vf", vf,
        "-c:v", "h264_nvenc",
        "-preset", "p4",
        "-tune", "hq",
        "-rc", "vbr",
        "-cq", "19",
        "-c:a", "copy",
        "-pix_fmt", "yuv420p",
        "-loglevel", "warning",
        str(output_path),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        result_ok = True
    except subprocess.CalledProcessError:
        result_ok = False
        # CPU fallback
        cmd_cpu = [c for c in cmd
                   if c not in ["-hwaccel", "cuda",
                                 "-hwaccel_output_format", "cuda"]]
        cmd_cpu = ["libx264" if c == "h264_nvenc" else c for c in cmd_cpu]
        try:
            subprocess.run(cmd_cpu, check=True)
            result_ok = True
        except subprocess.CalledProcessError as e:
            print(f"  ⚠️ CPU fallback тоже не удался: {e}")

    if result_ok and output_path.exists():
        size_mb = output_path.stat().st_size / 1024 / 1024
        print(f"  ✅ ASS вожжены: {output_path.name} ({size_mb:.1f} МБ)")
        return True
    else:
        print(f"  ❌ Ошибка ASS наложения")
        return False


# ── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Windows: UTF-8 консоль
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="subtitle_burner — вжечь SRT-субтитры в видео через FFmpeg"
    )
    parser.add_argument("--video",   required=True, help="Исходное видео (с аудио)")
    parser.add_argument("--srt",     required=True, help="Файл субтитров (.srt)")
    parser.add_argument("--output",  required=True, help="Выходной файл")
    parser.add_argument("--font",    default=None,  help="Путь к .ttf/.otf шрифту")
    parser.add_argument("--font-size", type=int, default=28, help="Размер шрифта (px)")
    parser.add_argument("--margin-v",  type=int, default=25, help="Отступ снизу (px)")
    parser.add_argument("--no-bold",   action="store_true",  help="Не жирный шрифт")
    parser.add_argument("--no-nvenc",  action="store_true",  help="Использовать libx264")
    args = parser.parse_args()

    ok = burn_subtitles(
        video_path  = Path(args.video),
        srt_path    = Path(args.srt),
        output_path = Path(args.output),
        font_path   = args.font,
        font_size   = args.font_size,
        margin_v    = args.margin_v,
        bold        = not args.no_bold,
        use_nvenc   = not args.no_nvenc,
    )
    sys.exit(0 if ok else 1)
