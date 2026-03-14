"""
Video Cutter + Upscaler Agent
------------------------------
Шаг 1 — Нарезает исходное видео по сегментам из result.json
         → data/media/<session>/clips/clip_001.mp4 ...

Шаг 2 — (опционально) Апскейл каждого клипа:
         Real-ESRGAN (AI/GPU) | Lanczos (FFmpeg) | Bicubic (FFmpeg)
         → data/media/<session>/upscaled/clip_001.mp4 ...

Запуск:
  py agents/video_cutter/video_cutter.py
  py agents/video_cutter/video_cutter.py --mode cut
  py agents/video_cutter/video_cutter.py --mode cut+upscale --method lanczos --resolution 1080
  py agents/video_cutter/video_cutter.py --project Video_20260227_220628 --source C:/video.mp4
  py agents/video_cutter/video_cutter.py --mode normalize+upscale --session Video_20260309_190235
  py agents/video_cutter/video_cutter.py --mode normalize+upscale --duration 10 --target-resolution 1920x1080 --method lanczos
"""

import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ffmpeg в PATH (winget-установка)
_FFMPEG_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / (
    "Microsoft/WinGet/Packages/"
    "Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe/"
    "ffmpeg-8.0.1-full_build/bin"
)
if _FFMPEG_DIR.exists():
    os.environ["PATH"] = str(_FFMPEG_DIR) + os.pathsep + os.environ.get("PATH", "")

BASE_DIR        = Path(__file__).parent.parent.parent
INPUT_DIR       = BASE_DIR / "data" / "input"
TRANSCRIPTS_DIR = BASE_DIR / "data" / "transcripts"
MEDIA_DIR       = BASE_DIR / "data" / "media"

# Поддерживаемые разрешения
RESOLUTIONS = {
    "720":  (1280,  720),
    "1080": (1920, 1080),
    "2k":   (2560, 1440),
    "4k":   (3840, 2160),
}

# Пути к Real-ESRGAN (проверяем несколько мест)
_REALESRGAN_NAMES = [
    "realesrgan-ncnn-vulkan",
    "realesrgan-ncnn-vulkan.exe",
]
_REALESRGAN_SEARCH = [
    Path(os.environ.get("LOCALAPPDATA", "")) / "realesrgan-ncnn-vulkan",
    Path("C:/tools/realesrgan-ncnn-vulkan"),
    Path("C:/realesrgan-ncnn-vulkan"),
]


# ---------------------------------------------------------------------------
# Утилиты
# ---------------------------------------------------------------------------

def find_latest_session() -> str | None:
    if not INPUT_DIR.exists():
        return None
    sessions = sorted(
        (d.name for d in INPUT_DIR.iterdir()
         if d.is_dir() and d.name.startswith("Video_")),
        reverse=True,
    )
    return sessions[0] if sessions else None


def find_latest_media_session() -> str | None:
    """Последняя сессия в data/media/ с видеофайлами.
    Сортируем по дате в имени (Video_YYYYMMDD_HHMMSS) — Video_TEST_001 игнорируется.
    """
    _DATE_RE = re.compile(r"^Video_(\d{8}_\d{6})$")
    if not MEDIA_DIR.exists():
        return None
    candidates = []
    for d in MEDIA_DIR.iterdir():
        if not d.is_dir():
            continue
        m = _DATE_RE.match(d.name)
        if not m:
            continue  # пропускаем Video_TEST_001 и прочие нестандартные имена
        if any(d.rglob("*.mp4")):
            candidates.append((m.group(1), d.name))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def find_video_in_session(session: str) -> Path | None:
    d = INPUT_DIR / session
    if not d.exists():
        return None
    for ext in ("*.mp4", "*.mov", "*.avi", "*.mkv", "*.webm"):
        files = list(d.glob(ext))
        if files:
            return files[0]
    return None


def load_segments(session: str) -> list[dict]:
    path = TRANSCRIPTS_DIR / session / "result.json"
    if not path.exists():
        raise FileNotFoundError(f"result.json не найден: {path}")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data["segments"] if isinstance(data, dict) else data


def check_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except Exception:
        return False


def check_nvenc() -> bool:
    """Проверяет, доступен ли NVIDIA NVENC энкодер в текущей сборке FFmpeg."""
    try:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True,
        )
        return "h264_nvenc" in r.stdout
    except Exception:
        return False


def find_realesrgan() -> Path | None:
    """Ищет realesrgan-ncnn-vulkan в PATH и известных местах."""
    # Сначала в PATH
    for name in _REALESRGAN_NAMES:
        try:
            result = subprocess.run(
                [name, "--help"],
                capture_output=True,
            )
            return Path(name)   # нашли в PATH
        except FileNotFoundError:
            pass

    # Потом в известных папках
    for folder in _REALESRGAN_SEARCH:
        for name in _REALESRGAN_NAMES:
            p = folder / name
            if p.exists():
                return p

    return None


def fmt_time(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return f"{h}ч {m}м {s}с"
    return f"{m}м {s}с"


# ---------------------------------------------------------------------------
# ВОПРОСЫ (интерактив)
# ---------------------------------------------------------------------------

def ask_mode() -> str:
    """Возвращает 'cut', 'cut+upscale' или 'upscale'."""
    print("\nЧто делать с видео?")
    print("  1. Только нарезка")
    print("  2. Нарезка + Апскейл")
    print("  3. Только апскейл (Grok-видео из videos/)")
    while True:
        c = input("Номер (1/2/3): ").strip()
        if c == "1":
            return "cut"
        if c == "2":
            return "cut+upscale"
        if c == "3":
            return "upscale"
        print("  Неверный ввод.")


def ask_method() -> str:
    """Возвращает 'realesrgan', 'lanczos' или 'bicubic'."""
    print("\nМетод апскейла?")
    print("  1. Real-ESRGAN  (AI, GPU RTX 3060) — лучшее качество, медленно")
    print("  2. Lanczos      (FFmpeg)            — хорошее качество, быстро")
    print("  3. Bicubic      (FFmpeg)            — среднее качество, очень быстро")
    while True:
        c = input("Номер (1/2/3): ").strip()
        if c == "1": return "realesrgan"
        if c == "2": return "lanczos"
        if c == "3": return "bicubic"
        print("  Неверный ввод.")


def ask_resolution() -> str:
    """Возвращает '720', '1080', '2k' или '4k'."""
    print("\nЦелевое разрешение?")
    print("  1. 720p   (1280×720)")
    print("  2. 1080p  (1920×1080)")
    print("  3. 2K     (2560×1440)")
    print("  4. 4K     (3840×2160)")
    while True:
        c = input("Номер (1-4): ").strip()
        if c == "1": return "720"
        if c == "2": return "1080"
        if c == "3": return "2k"
        if c == "4": return "4k"
        print("  Неверный ввод.")


# ---------------------------------------------------------------------------
# ШАГ 1 — НАРЕЗКА
# ---------------------------------------------------------------------------

def cut_clip(source: Path, start: float, end: float, out: Path,
             use_gpu: bool = False) -> bool:
    """
    Нарезает клип с перекодированием.
    -ss ПЕРЕД -i = быстрый поиск до ближайшего кейфрейма.
    -t  = длительность от точки поиска (не -to).
    GPU: h264_nvenc (NVIDIA CUDA декодирование + NVENC кодирование).
    CPU: libx264 — фолбэк если NVENC недоступен.
    """
    if use_gpu:
        cmd = [
            "ffmpeg", "-y",
            "-hwaccel", "cuda",             # декодирование на GPU
            "-ss", str(start),
            "-i", str(source),
            "-t", str(round(end - start, 6)),
            "-c:v", "h264_nvenc",           # кодирование на GPU (NVENC)
            "-preset", "p4",                # p1=быстрее, p7=качественнее
            "-rc:v", "vbr",
            "-cq", "18",                    # качество (аналог -crf для nvenc)
            "-b:v", "0",
            "-c:a", "aac",
            "-avoid_negative_ts", "1",
            str(out),
        ]
    else:
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start),
            "-i", str(source),
            "-t", str(round(end - start, 6)),
            "-c:v", "libx264",
            "-crf", "18",
            "-preset", "fast",
            "-c:a", "aac",
            "-avoid_negative_ts", "1",
            str(out),
        ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode == 0 and out.exists() and out.stat().st_size > 0


def run_cutting(
    source: Path,
    segments: list[dict],
    clips_dir: Path,
    use_gpu: bool = False,
) -> tuple[int, list[int]]:
    clips_dir.mkdir(parents=True, exist_ok=True)
    success, failed = 0, []
    total = len(segments)

    for seg in segments:
        sid   = seg["id"]
        fname = f"clip_{sid:03d}.mp4"
        out   = clips_dir / fname
        print(f"  ✂️  [{sid:3d}/{total}] {seg['start']:.1f}s→{seg['end']:.1f}s  {fname}", end=" ", flush=True)
        ok = cut_clip(source, seg["start"], seg["end"], out, use_gpu=use_gpu)
        if ok:
            print("✓")
            success += 1
        else:
            print("✗ ОШИБКА")
            failed.append(sid)

    return success, failed


# ---------------------------------------------------------------------------
# ШАГ 2 — АПСКЕЙЛ
# ---------------------------------------------------------------------------

def upscale_ffmpeg(clip: Path, out: Path, width: int, height: int, flags: str,
                   use_gpu: bool = False) -> bool:
    """
    Апскейл через FFmpeg (lanczos / bicubic).
    GPU: CUDA декодирование + scale на CPU (для качественного lanczos) + NVENC кодирование.
    CPU: полностью программный рендеринг через libx264.
    """
    if use_gpu:
        cmd = [
            "ffmpeg", "-y",
            "-hwaccel", "cuda",             # декодирование на GPU
            "-i", str(clip),
            "-vf", f"scale={width}:{height}:flags={flags}",   # масштаб на CPU
            "-c:v", "h264_nvenc",           # кодирование на GPU
            "-preset", "p4",
            "-rc:v", "vbr",
            "-cq", "18",
            "-b:v", "0",
            "-c:a", "copy",
            str(out),
        ]
    else:
        cmd = [
            "ffmpeg", "-y",
            "-i", str(clip),
            "-vf", f"scale={width}:{height}:flags={flags}",
            "-c:v", "libx264",
            "-crf", "18",
            "-preset", "fast",
            "-c:a", "copy",
            str(out),
        ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode == 0 and out.exists() and out.stat().st_size > 0


def upscale_realesrgan(
    clip: Path,
    out: Path,
    realesrgan_exe: Path,
    width: int,
    height: int,
    use_gpu: bool = False,
) -> bool:
    """
    Апскейл через Real-ESRGAN-ncnn-vulkan.
    Работает с видео через кадры: extract → upscale frames → encode.
    """
    import tempfile, shutil

    tmp = Path(tempfile.mkdtemp())
    frames_in  = tmp / "frames_in"
    frames_out = tmp / "frames_out"
    frames_in.mkdir();  frames_out.mkdir()

    try:
        # 1. Извлекаем кадры
        r1 = subprocess.run(
            ["ffmpeg", "-y", "-i", str(clip),
             str(frames_in / "frame_%06d.png")],
            capture_output=True,
        )
        if r1.returncode != 0:
            return False

        # 2. Real-ESRGAN на каждый кадр
        r2 = subprocess.run(
            [str(realesrgan_exe),
             "-i", str(frames_in),
             "-o", str(frames_out),
             "-n", "realesr-animevideov3",
             "-s", "4",          # масштаб ×4
             "-f", "png"],
            capture_output=True,
        )
        if r2.returncode != 0:
            return False

        # 3. Получаем fps исходного клипа
        probe = subprocess.run(
            ["ffprobe", "-v", "0", "-select_streams", "v:0",
             "-show_entries", "stream=r_frame_rate",
             "-of", "csv=p=0", str(clip)],
            capture_output=True, text=True,
        )
        fps_raw = probe.stdout.strip()       # e.g. "30000/1001"
        try:
            num, den = fps_raw.split("/")
            fps = float(num) / float(den)
        except Exception:
            fps = 30.0

        # 4. Собираем видео с апскейленных кадров + масштаб до нужного разрешения
        encode_args = (
            ["-c:v", "h264_nvenc", "-preset", "p4", "-rc:v", "vbr", "-cq", "18", "-b:v", "0"]
            if use_gpu else
            ["-c:v", "libx264", "-crf", "18", "-preset", "fast"]
        )
        r3 = subprocess.run(
            ["ffmpeg", "-y",
             "-framerate", str(fps),
             "-i", str(frames_out / "frame_%06d.png"),
             "-i", str(clip),          # аудио из оригинала
             "-vf", f"scale={width}:{height}:flags=lanczos",
             *encode_args,
             "-c:a", "copy",
             "-map", "0:v:0", "-map", "1:a?",
             str(out)],
            capture_output=True,
        )
        return r3.returncode == 0 and out.exists() and out.stat().st_size > 0

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def get_video_height(path: Path) -> int | None:
    """Возвращает высоту видео в пикселях через ffprobe, или None при ошибке."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "0", "-select_streams", "v:0",
             "-show_entries", "stream=height",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        val = r.stdout.strip()
        return int(val) if val.isdigit() else None
    except Exception:
        return None


def get_video_duration(path: Path) -> float:
    """Возвращает длительность видео в секундах через ffprobe, или 0.0 при ошибке."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet",
             "-print_format", "json",
             "-show_format", str(path)],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode == 0:
            return float(json.loads(r.stdout)["format"]["duration"])
    except Exception:
        pass
    return 0.0


# ── Прогресс для нормализации/апскейла ───────────────────────────────────────
_NORM_PROGRESS_FILE = BASE_DIR / "temp" / "normalize_upscale_progress.json"


def _write_norm_progress(data: dict) -> None:
    _NORM_PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _NORM_PROGRESS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Нормализация и апскейл видео (для Grok-видео)
# ---------------------------------------------------------------------------

def normalize_to_10sec(input_path: Path, output_path: Path,
                       target_duration: int = 10) -> bool:
    """
    Привести видео к ровно target_duration секундам:
    - Если видео >= target_duration → обрезать
    - Если видео < target_duration → зациклить через concat
    Убирает аудио, сохраняет разрешение.
    """
    actual_dur = get_video_duration(input_path)
    if actual_dur <= 0:
        print(f"  ❌ Не удалось определить длительность: {input_path.name}")
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)

    if actual_dur >= target_duration:
        cmd = [
            "ffmpeg", "-y",
            "-i", str(input_path),
            "-t", str(target_duration),
            "-c:v", "libx264", "-preset", "fast",
            "-an",
            "-pix_fmt", "yuv420p",
            str(output_path),
        ]
    else:
        # Зациклить через concat
        loops = int(target_duration / actual_dur) + 2
        concat_file = BASE_DIR / "temp" / "loop_concat.txt"
        concat_file.parent.mkdir(parents=True, exist_ok=True)
        with open(concat_file, "w") as f:
            for _ in range(loops):
                f.write(f"file '{input_path.as_posix()}'\n")
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_file),
            "-t", str(target_duration),
            "-c:v", "libx264", "-preset", "fast",
            "-an",
            "-pix_fmt", "yuv420p",
            str(output_path),
        ]

    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode == 0 and output_path.exists():
        final_dur = get_video_duration(output_path)
        print(f"  ✅ Нормализовано: {actual_dur:.1f}s → {final_dur:.1f}s")
        return True
    else:
        print(f"  ❌ Ошибка нормализации: {r.stderr[-200:]}")
        return False


def upscale_video(input_path: Path, output_path: Path,
                  target_resolution: str = "1920x1080",
                  method: str = "lanczos") -> bool:
    """
    Апскейл видео до target_resolution.
    method: realesrgan / lanczos
    """
    w_str, h_str = target_resolution.split("x")
    w, h = int(w_str), int(h_str)

    use_gpu = check_nvenc()

    if method == "realesrgan":
        realesrgan_exe = find_realesrgan()
        if realesrgan_exe:
            ok = upscale_realesrgan(input_path, output_path,
                                    realesrgan_exe, w, h, use_gpu=use_gpu)
            if ok:
                print(f"  ✅ Real-ESRGAN → {target_resolution}")
                return True
        print("  ⚠️ Real-ESRGAN не найден — переключаюсь на Lanczos")

    # Lanczos через FFmpeg (fallback)
    ok = upscale_ffmpeg(input_path, output_path, w, h, "lanczos", use_gpu=use_gpu)
    if ok:
        print(f"  ✅ Lanczos → {target_resolution}")
    return ok


def process_all_videos(session: str, target_duration: int = 10,
                       target_resolution: str = "1920x1080",
                       method: str = "lanczos") -> bool:
    """
    Прогнать ВСЕ видео через:
    1. Нормализация до target_duration сек
    2. Апскейл до target_resolution
    3. Сохранить в data/media/{session}/upscaled/
    """
    videos_dir   = MEDIA_DIR / session / "videos"
    upscaled_dir = MEDIA_DIR / session / "upscaled"
    temp_dir     = BASE_DIR / "temp"
    upscaled_dir.mkdir(parents=True, exist_ok=True)
    temp_dir.mkdir(parents=True, exist_ok=True)

    video_files = sorted(videos_dir.glob("video_*.mp4"))
    total = len(video_files)

    print(f"\n{'═'*52}")
    print(f"  НОРМАЛИЗАЦИЯ + АПСКЕЙЛ")
    print(f"  Сессия : {session}")
    print(f"  Видео  : {total}")
    print(f"  Цель   : {target_duration}s, {target_resolution}")
    print(f"  Метод  : {method}")
    print(f"{'═'*52}\n")

    _write_norm_progress({
        "session": session, "current": 0, "total": total,
        "status": "running", "ok": 0, "failed": 0,
    })

    t0 = time.monotonic()
    ok_count = 0

    for i, video_path in enumerate(video_files, 1):
        print(f"[{i}/{total}] {video_path.name}", flush=True)

        # Шаг 1 — нормализация
        normalized = temp_dir / f"normalized_{video_path.name}"
        norm_ok = normalize_to_10sec(video_path, normalized, target_duration)
        src = normalized if norm_ok else video_path

        # Шаг 2 — апскейл
        output_path = upscaled_dir / video_path.name
        up_ok = upscale_video(src, output_path, target_resolution, method)

        # Очистить temp
        if normalized.exists():
            normalized.unlink(missing_ok=True)

        if up_ok:
            ok_count += 1
            elapsed = time.monotonic() - t0
            speed = elapsed / i
            eta = int((total - i) * speed)
            print(f"  ✅ [{i}/{total}] ⚡ ~{speed:.0f}с/видео | ETA ~{fmt_time(eta)}")
        else:
            print(f"  ❌ [{i}/{total}] Ошибка: {video_path.name}")

        _write_norm_progress({
            "session": session, "current": i, "total": total,
            "status": "running", "ok": ok_count, "failed": i - ok_count,
        })

    total_time = time.monotonic() - t0
    _write_norm_progress({
        "session": session, "current": total, "total": total,
        "status": "done", "ok": ok_count, "failed": total - ok_count,
    })

    print(f"\n{'═'*52}")
    print(f"  ✅ Обработано: {ok_count}/{total}")
    print(f"  ⏱  Время: {fmt_time(total_time)}")
    print(f"  📁 {upscaled_dir}")
    print(f"{'═'*52}\n")

    return ok_count == total


def run_upscaling(
    clips_dir: Path,
    upscaled_dir: Path,
    method: str,
    resolution: str,
    realesrgan_exe: Path | None,
    use_gpu: bool = False,
    glob_pattern: str = "clip_*.mp4",
) -> tuple[int, list[str]]:
    import shutil as _shutil
    upscaled_dir.mkdir(parents=True, exist_ok=True)
    w, h    = RESOLUTIONS[resolution]
    clips   = sorted(clips_dir.glob(glob_pattern))
    total   = len(clips)
    success = 0
    failed: list[str] = []

    for i, clip in enumerate(clips, 1):
        out = upscaled_dir / clip.name

        cur_h = get_video_height(clip)

        if cur_h is not None and cur_h == h:
            # Точное совпадение — просто копируем
            print(f"  ⏭ [{i:3d}/{total}] {clip.name} уже {cur_h}p — копирую", flush=True)
            _shutil.copy2(clip, out)
            success += 1
            continue
        elif cur_h is not None and cur_h > h:
            # Больше цели — даунскейл через lanczos
            print(f"  ⬇ [{i:3d}/{total}] {clip.name} {cur_h}p → {h}p (даунскейл)", end=" ", flush=True)
            ok = upscale_ffmpeg(clip, out, w, h, "lanczos", use_gpu=use_gpu)
        else:
            # Меньше цели — апскейл выбранным методом
            gpu_tag = " (GPU)" if method == "realesrgan" else ""
            print(f"  🔍 [{i:3d}/{total}] {clip.name} → {h}p{gpu_tag}", end=" ", flush=True)
            if method == "realesrgan" and realesrgan_exe:
                ok = upscale_realesrgan(clip, out, realesrgan_exe, w, h, use_gpu=use_gpu)
            elif method == "lanczos":
                ok = upscale_ffmpeg(clip, out, w, h, "lanczos", use_gpu=use_gpu)
            else:
                ok = upscale_ffmpeg(clip, out, w, h, "bicubic", use_gpu=use_gpu)

        if ok:
            print("✓")
            success += 1
        else:
            print("✗ ОШИБКА")
            failed.append(clip.name)

    return success, failed


# ---------------------------------------------------------------------------
# Запуск
# ---------------------------------------------------------------------------

def run() -> None:
    parser = argparse.ArgumentParser(description="Нарезка и апскейл видео по сегментам")
    parser.add_argument("--mode",       choices=["cut", "cut+upscale", "upscale", "normalize+upscale"],
                        help="Режим: cut | cut+upscale | upscale | normalize+upscale")
    parser.add_argument("--method",     choices=["realesrgan", "lanczos", "bicubic"],
                        help="Метод апскейла")
    parser.add_argument("--resolution", choices=["720", "1080", "2k", "4k"],
                        help="Целевое разрешение (для cut+upscale/upscale)")
    parser.add_argument("--target-resolution", default="1920x1080",
                        help="Целевое разрешение WxH для normalize+upscale (по умолчанию: 1920x1080)")
    parser.add_argument("--duration",   type=int, default=10,
                        help="Целевая длительность в секундах для normalize+upscale (по умолчанию: 10)")
    parser.add_argument("--project",    help="Имя папки сессии (по умолчанию — последняя)")
    parser.add_argument("--session",    help="Имя сессии (алиас для --project)")
    parser.add_argument("--source",     help="Путь к исходному видеофайлу")
    args = parser.parse_args()

    # --session как алиас для --project
    if args.session and not args.project:
        args.project = args.session

    # ── Проверяем ffmpeg ──────────────────────────────────────────────────
    if not check_ffmpeg():
        print("❌ ffmpeg не найден. Установи ffmpeg и добавь в PATH.")
        sys.exit(1)

    # ── GPU / CPU ────────────────────────────────────────────────────────
    use_gpu = check_nvenc()
    gpu_label = "🟢 NVIDIA NVENC (GPU)" if use_gpu else "🔵 libx264 (CPU)"
    print(f"Энкодер : {gpu_label}")

    # ── Определяем сессию ─────────────────────────────────────────────────
    mode_for_session = args.mode  # нужен чтобы выбрать правильный поиск
    if args.project:
        session = args.project
    elif mode_for_session == "upscale":
        # Для апскейла ищем сессию где уже есть видео в data/media/
        session = find_latest_media_session() or find_latest_session()
    else:
        session = find_latest_session()
    if not session:
        print("❌ Нет сессий в data/input/ или data/media/")
        sys.exit(1)
    print(f"Проект  : {session}")

    # ══ РЕЖИМ: НОРМАЛИЗАЦИЯ + АПСКЕЙЛ GROK-ВИДЕО ═════════════════════════
    if args.mode == "normalize+upscale":
        if not check_ffmpeg():
            print("❌ ffmpeg не найден.")
            sys.exit(1)

        target_res = getattr(args, "target_resolution", "1920x1080")
        method_n   = args.method or "lanczos"
        duration_n = args.duration or 10

        # Определяем сессию
        if args.project:
            session_n = args.project
        else:
            session_n = find_latest_media_session()
        if not session_n:
            print("❌ Нет сессий с видео в data/media/")
            sys.exit(1)

        videos_dir_n = MEDIA_DIR / session_n / "videos"
        if not videos_dir_n.exists() or not any(videos_dir_n.glob("video_*.mp4")):
            print(f"❌ Нет видео в {videos_dir_n}")
            sys.exit(1)

        ok = process_all_videos(session_n, duration_n, target_res, method_n)
        sys.exit(0 if ok else 1)

    # ── Режим upscale не требует исходного видео и сегментов ──────────────
    mode_early = args.mode  # может быть None (спросим позже), но upscale передаётся явно
    if mode_early == "upscale":
        source   = None
        segments = []
    else:
        # ── Исходное видео ────────────────────────────────────────────────
        if args.source:
            source = Path(args.source)
            if not source.exists():
                print(f"❌ Файл не найден: {source}")
                sys.exit(1)
        else:
            source = find_video_in_session(session)
            if not source:
                print(
                    f"❌ Видеофайл не найден в data/input/{session}/\n"
                    f"   Укажи --source путь/к/видео.mp4"
                )
                sys.exit(1)

        print(f"Видео   : {source.name}  ({source.stat().st_size // 1_048_576} MB)")

        # ── Сегменты ──────────────────────────────────────────────────────
        try:
            segments = load_segments(session)
        except FileNotFoundError as e:
            print(f"❌ {e}")
            sys.exit(1)
        print(f"Сегментов: {len(segments)}\n")

    # ── Вопросы (если не переданы аргументами) ────────────────────────────
    mode = args.mode or ask_mode()

    method     = None
    resolution = None
    realesrgan_exe = None

    if mode in ("cut+upscale", "upscale"):
        method     = args.method     or ask_method()
        resolution = args.resolution or ask_resolution()

        if method == "realesrgan":
            realesrgan_exe = find_realesrgan()
            if not realesrgan_exe:
                print(
                    "⚠️  Real-ESRGAN не найден. Переключаюсь на Lanczos.\n"
                    "   Установи realesrgan-ncnn-vulkan и добавь в PATH для AI-апскейла."
                )
                method = "lanczos"

    # ── Директории ────────────────────────────────────────────────────────
    clips_dir        = MEDIA_DIR / session / "clips"
    upscaled_dir     = MEDIA_DIR / session / "upscaled"
    videos_dir       = MEDIA_DIR / session / "videos"
    videos_up_dir    = MEDIA_DIR / session / "videos_upscaled"

    t0 = time.monotonic()

    # ══ РЕЖИМ: ТОЛЬКО АПСКЕЙЛ ГОТОВЫХ ВИДЕО ══════════════════════════════
    if mode == "upscale":
        # Ищем видео в нескольких возможных папках
        _candidates = [
            (videos_dir,                    videos_up_dir,                  "video_*.mp4"),
            (MEDIA_DIR / session / "videos_upscaled", MEDIA_DIR / session / "videos_upscaled_2k", "video_*.mp4"),
            (MEDIA_DIR / session,           MEDIA_DIR / session / "videos_upscaled", "video_*.mp4"),
            (MEDIA_DIR / session,           MEDIA_DIR / session / "videos_upscaled", "*.mp4"),
        ]
        videos_dir    = None
        videos_up_dir = None
        glob_pat      = "video_*.mp4"
        for _src, _dst, _pat in _candidates:
            if _src.exists() and any(_src.glob(_pat)):
                videos_dir    = _src
                videos_up_dir = _dst
                glob_pat      = _pat
                break

        if not videos_dir:
            session_dir = MEDIA_DIR / session
            print(f"❌ Нет видео в сессии {session}")
            if session_dir.exists():
                subs = [d.name for d in session_dir.iterdir() if d.is_dir()]
                print(f"   Найденные папки: {subs}")
            else:
                print(f"   Папка сессии не существует: {session_dir}")
            print("   Сначала сгенерируй видео через Grok (платформа 2).")
            sys.exit(1)

        video_count  = len(list(videos_dir.glob(glob_pat)))
        res_label    = {"720": "720p", "1080": "1080p", "2k": "2K", "4k": "4K"}[resolution]
        method_label = {"realesrgan": "Real-ESRGAN (AI)", "lanczos": "Lanczos", "bicubic": "Bicubic"}[method]

        print(f"\n{'─'*52}")
        print(f"АПСКЕЙЛ  {video_count} видео  ({method_label} → {res_label})")
        print(f"Вход : {videos_dir}")
        print(f"Выход: {videos_up_dir}")
        print(f"{'─'*52}")

        t1 = time.monotonic()
        up_ok, up_fail = run_upscaling(
            videos_dir, videos_up_dir, method, resolution,
            realesrgan_exe, use_gpu=use_gpu,
            glob_pattern=glob_pat,
        )
        up_time = time.monotonic() - t1

        print(f"\n{'═'*52}")
        print(f"✅ Апскейл завершён!")
        print(f"   🔍 Обработано: {up_ok}/{video_count} → {res_label}")
        print(f"   ⏱  Время: {fmt_time(up_time)}")
        print(f"   📁 {videos_up_dir}")
        print(f"{'═'*52}")
        if up_fail:
            print(f"❌ Ошибки: {up_fail}")
            sys.exit(1)
        return

    # ══ ШАГ 1 — НАРЕЗКА ══════════════════════════════════════════════════
    print(f"{'─'*52}")
    print(f"ШАГ 1 — НАРЕЗКА  ({len(segments)} клипов)")
    print(f"{'─'*52}")

    t1 = time.monotonic()
    cut_ok, cut_fail = run_cutting(source, segments, clips_dir, use_gpu=use_gpu)
    cut_time = time.monotonic() - t1

    print(f"\n✂️  Нарезано: {cut_ok}/{len(segments)}  ({fmt_time(cut_time)})")
    if cut_fail:
        print(f"❌ Ошибки в сегментах: {cut_fail}")

    # ══ ШАГ 2 — АПСКЕЙЛ ══════════════════════════════════════════════════
    up_ok, up_fail = 0, []
    up_time = 0.0

    if mode == "cut+upscale" and cut_ok > 0:
        res_label = {"720": "720p", "1080": "1080p", "2k": "2K", "4k": "4K"}[resolution]
        method_label = {"realesrgan": "Real-ESRGAN (AI)", "lanczos": "Lanczos", "bicubic": "Bicubic"}[method]
        print(f"\n{'─'*52}")
        print(f"ШАГ 2 — АПСКЕЙЛ  ({method_label} → {res_label})")
        print(f"{'─'*52}")

        t2 = time.monotonic()
        up_ok, up_fail = run_upscaling(clips_dir, upscaled_dir, method, resolution, realesrgan_exe, use_gpu=use_gpu)
        up_time = time.monotonic() - t2

        print(f"\n🔍 Апскейл: {up_ok}/{cut_ok}  ({fmt_time(up_time)})")
        if up_fail:
            print(f"❌ Ошибки: {up_fail}")

    # ══ ИТОГ ═════════════════════════════════════════════════════════════
    total_time = time.monotonic() - t0
    final_dir  = upscaled_dir if mode == "cut+upscale" else clips_dir

    print(f"\n{'═'*52}")
    print(f"✅ Готово!")
    print(f"   ✂️  Нарезано: {cut_ok} клипов")
    if mode == "cut+upscale":
        res_label = {"720": "720p", "1080": "1080p", "2k": "2K", "4k": "4K"}[resolution]
        print(f"   🔍 Апскейл: {up_ok} клипов → {res_label}")
    print(f"   ⏱  Общее время: {fmt_time(total_time)}")
    print(f"   📁 {final_dir}")
    print(f"{'═'*52}")

    if cut_fail or up_fail:
        sys.exit(1)


if __name__ == "__main__":
    run()
