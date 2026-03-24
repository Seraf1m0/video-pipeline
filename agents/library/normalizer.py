"""
Нормализация клипов библиотеки:
1. Конвертация в MP4
2. Апскейл до 1920x1080 16:9
3. Нормализация FPS до 25
4. Убрать аудио
5. Единый битрейт 8M
GPU NVENC + CPU fallback
"""
import subprocess
import json
import shutil
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import threading

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

LIBRARY_DIR  = Path(__file__).resolve().parent.parent.parent / "library"
CLIPS_DIR    = LIBRARY_DIR / "clips"
NORM_DIR     = LIBRARY_DIR / "normalized"
LIBRARY_JSON = LIBRARY_DIR / "library.json"

NORM_DIR.mkdir(parents=True, exist_ok=True)

# Глобальный lock для записи library.json
_json_lock = threading.Lock()


def get_gpu_encoder():
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-encoders"],
        capture_output=True, text=True
    )
    if "h264_nvenc" in r.stdout:
        print("✅ GPU: NVIDIA NVENC")
        return "h264_nvenc", [
            "-preset", "p4",
            "-rc", "vbr",
            "-cq", "19",
            "-b:v", "8M",
            "-maxrate", "10M",
            "-bufsize", "20M"
        ]
    print("⚠️ CPU fallback: libx264")
    return "libx264", [
        "-preset", "fast",
        "-crf", "18",
        "-b:v", "8M",
        "-maxrate", "10M",
        "-bufsize", "20M"
    ]


GPU_ENCODER, GPU_PARAMS = get_gpu_encoder()


def get_video_info(path):
    """FFprobe анализ клипа"""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams", "-show_format",
        str(path)
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    try:
        data = json.loads(r.stdout)
        for s in data.get("streams", []):
            if s.get("codec_type") == "video":
                w = s.get("width", 0)
                h = s.get("height", 0)
                fps_str = s.get("r_frame_rate", "25/1")
                num, den = fps_str.split("/")
                fps = float(num) / float(den)
                codec = s.get("codec_name", "")
                return {
                    "width": w,
                    "height": h,
                    "fps": fps,
                    "codec": codec,
                    "duration": float(data["format"].get("duration", 0))
                }
    except Exception:
        pass
    return None


def get_motion_level(clip_path) -> str:
    """
    Определить уровень движения в клипе через scene detection.
    Анализирует первые 5 секунд.
    Возвращает: "static" / "slow_pan" / "fast_motion"
    """
    try:
        cmd = [
            "ffmpeg", "-i", str(clip_path),
            "-t", "5",
            "-vf", "select='gt(scene,0.15)',metadata=print:file=-",
            "-f", "null", "-",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        scene_changes = result.stderr.count("lavfi.scene_score")
        if scene_changes == 0:
            return "static"
        elif scene_changes <= 3:
            return "slow_pan"
        else:
            return "fast_motion"
    except Exception:
        return "unknown"


def get_quality_score(clip_path) -> str:
    """
    Оценить качество клипа по битрейту из ffprobe.
    < 1 Mbps  → "low"
    1–5 Mbps  → "medium"
    > 5 Mbps  → "high"
    """
    try:
        r = subprocess.run([
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_format", str(clip_path),
        ], capture_output=True, text=True, timeout=10)
        bitrate = int(json.loads(r.stdout)["format"]["bit_rate"])
        if bitrate < 1_000_000:
            return "low"
        elif bitrate < 5_000_000:
            return "medium"
        return "high"
    except Exception:
        return "medium"


def build_vf_filter(w, h):
    """
    Построить FFmpeg vf фильтр
    для конвертации в 1920x1080 16:9
    """
    if w == 0 or h == 0:
        return "scale=1920:1080:flags=lanczos"
    ratio = w / h
    if 1.67 <= ratio <= 1.87:
        # Уже 16:9 → просто апскейл
        return "scale=1920:1080:flags=lanczos"
    elif ratio > 1.87:
        # Слишком широкое → crop по центру
        new_w = int(h * 16 / 9)
        offset = (w - new_w) // 2
        return (
            f"crop={new_w}:{h}:{offset}:0,"
            f"scale=1920:1080:flags=lanczos"
        )
    elif ratio >= 1.3:
        # Почти 16:9 → zoom crop верх/низ
        new_h = int(w * 9 / 16)
        offset = max(0, (h - new_h) // 2)
        return (
            f"crop={w}:{new_h}:0:{offset},"
            f"scale=1920:1080:flags=lanczos"
        )
    elif ratio >= 0.9:
        # Квадрат → crop центр
        new_h = int(w * 9 / 16)
        offset = max(0, (h - new_h) // 2)
        return (
            f"crop={w}:{new_h}:0:{offset},"
            f"scale=1920:1080:flags=lanczos"
        )
    else:
        # Portrait → blur фон по бокам
        return (
            "split[fg][bg];"
            "[bg]scale=1920:1080,"
            "boxblur=30[bgblur];"
            "[fg]scale=-2:1080[fgscaled];"
            "[bgblur][fgscaled]overlay="
            "(W-w)/2:(H-h)/2"
        )


def normalize_clip(clip_path, output_path, info):
    """
    Нормализовать один клип:
    - 1920x1080 16:9
    - 25fps
    - Без аудио
    - MP4 h264
    - 8M битрейт
    """
    vf = build_vf_filter(info["width"], info["height"])
    vf_full = f"{vf},fps=25"
    cmd = [
        "ffmpeg", "-y",
        "-i", str(clip_path),
        "-vf", vf_full,
        "-c:v", GPU_ENCODER,
        *GPU_PARAMS,
        "-pix_fmt", "yuv420p",
        "-an",
        "-loglevel", "warning",
        str(output_path)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        cmd_cpu = [
            "ffmpeg", "-y",
            "-i", str(clip_path),
            "-vf", vf_full,
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "18",
            "-b:v", "8M",
            "-maxrate", "10M",
            "-bufsize", "20M",
            "-pix_fmt", "yuv420p",
            "-an",
            "-loglevel", "warning",
            str(output_path)
        ]
        subprocess.run(cmd_cpu, check=True)
    return output_path.exists() and output_path.stat().st_size > 1000


def needs_normalization(info):
    """Проверить нужна ли нормализация"""
    if not info:
        return True
    w = info["width"]
    h = info["height"]
    fps = info["fps"]
    ratio = w / h if h > 0 else 0
    needs = (
        w != 1920 or h != 1080 or
        abs(fps - 25) > 0.5 or
        not (1.67 <= ratio <= 1.87)
    )
    return needs


def process_single_clip(args):
    """Обработать один клип (для ThreadPoolExecutor)"""
    clip_id, entry, library = args
    clip_file = entry.get("file", "")
    if not clip_file:
        return clip_id, "skip", "нет файла"
    clip_path = CLIPS_DIR / clip_file
    if not clip_path.exists():
        return clip_id, "skip", "файл не найден"
    if entry.get("rejected", False):
        return clip_id, "skip", "rejected"
    if entry.get("normalized", False):
        # Дополнить motion/quality если полей ещё нет
        if "motion" not in entry or "quality" not in entry:
            if "motion" not in entry:
                entry["motion"]  = get_motion_level(clip_path)
            if "quality" not in entry:
                entry["quality"] = get_quality_score(clip_path)
        return clip_id, "skip", "уже норм"
    info = get_video_info(clip_path)
    if not info:
        return clip_id, "error", "ffprobe упал"
    if not needs_normalization(info):
        with _json_lock:
            entry["normalized"] = True
            entry["width"] = info["width"]
            entry["height"] = info["height"]
            entry["fps"] = round(info["fps"], 2)
        return clip_id, "ok_skip", "уже 1920x1080 25fps"
    output_path = (NORM_DIR / clip_file).with_suffix(".mp4")
    success = normalize_clip(clip_path, output_path, info)
    if success:
        import time as _time
        for _attempt in range(5):
            try:
                shutil.move(str(output_path), str(clip_path))
                break
            except PermissionError:
                _time.sleep(0.5)
        else:
            return clip_id, "error", "move locked"
        motion  = get_motion_level(clip_path)
        quality = get_quality_score(clip_path)
        with _json_lock:
            entry["normalized"] = True
            entry["width"]      = 1920
            entry["height"]     = 1080
            entry["fps"]        = 25
            entry["has_audio"]  = False
            entry["motion"]     = motion
            entry["quality"]    = quality
        ratio = info["width"] / info["height"] if info["height"] > 0 else 0
        return clip_id, "ok", (
            f"{info['width']}x{info['height']} "
            f"ratio={ratio:.2f} "
            f"fps={info['fps']:.1f} → "
            f"1920x1080 25fps | motion={motion} quality={quality}"
        )
    else:
        return clip_id, "error", "нормализация упала"


def run_normalization(max_workers=4):
    """
    Параллельная нормализация всех клипов
    4 потока (GPU справится)
    """
    with open(LIBRARY_JSON, encoding="utf-8") as f:
        library = json.load(f)
    clips = library.get("clips", {})
    total = len(clips)
    to_process = [
        (clip_id, entry, library)
        for clip_id, entry in clips.items()
        if not entry.get("rejected", False)
        and (
            not entry.get("normalized", False)
            or "motion"  not in entry
            or "quality" not in entry
        )
    ]
    print(f"""
{'='*50}
🔧 НОРМАЛИЗАЦИЯ БИБЛИОТЕКИ
  Всего клипов:      {total}
  Нужно обработать:  {len(to_process)}
  Потоков:           {max_workers}
  GPU:               {GPU_ENCODER}
{'='*50}
""", flush=True)
    stats = {"ok": 0, "ok_skip": 0, "skip": 0, "error": 0}
    completed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(process_single_clip, args)
            for args in to_process
        ]
        for future in futures:
            clip_id, status, detail = future.result()
            stats[status] = stats.get(status, 0) + 1
            completed += 1
            if status == "ok":
                print(
                    f"  ✅ [{completed}/{len(to_process)}] "
                    f"{clip_id}: {detail}",
                    flush=True
                )
            elif status == "error":
                print(
                    f"  ❌ [{completed}/{len(to_process)}] "
                    f"{clip_id}: {detail}",
                    flush=True
                )
            if completed % 20 == 0:
                with _json_lock:
                    with open(LIBRARY_JSON, "w", encoding="utf-8") as f:
                        json.dump(library, f, indent=2, ensure_ascii=False)
                print(
                    f"\n💾 Сохранено ({completed}/{len(to_process)})\n",
                    flush=True
                )
    with open(LIBRARY_JSON, "w", encoding="utf-8") as f:
        json.dump(library, f, indent=2, ensure_ascii=False)
    print(f"""
{'='*50}
📊 НОРМАЛИЗАЦИЯ ЗАВЕРШЕНА
  ✅ Нормализовано:  {stats.get('ok', 0)}
  ⏭️  Уже норм:      {stats.get('ok_skip', 0)}
  ⏭️  Пропущено:     {stats.get('skip', 0)}
  ❌ Ошибки:         {stats.get('error', 0)}
  💾 library.json обновлён
{'='*50}
""", flush=True)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--workers", type=int, default=4,
        help="Количество параллельных потоков"
    )
    args = parser.parse_args()
    run_normalization(max_workers=args.workers)
