"""
duration_cache.py — единый потокобезопасный кэш длительностей аудио/видео.

Заменяет дублирующиеся _dur_cache в audio_mixer.py, transitions.py, sfx_mixer.py.
Все модули импортируют get_duration() и probe_durations_parallel() отсюда.
"""

import json
import subprocess
import threading
import concurrent.futures

_dur_cache: dict[str, float] = {}
_dur_lock = threading.Lock()


def get_duration(path) -> float:
    """Получить длительность аудио/видео через ffprobe (с потокобезопасным кэшем)."""
    key = str(path)
    with _dur_lock:
        if key in _dur_cache:
            return _dur_cache[key]
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        key,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    try:
        dur = float(json.loads(r.stdout)["format"]["duration"])
    except Exception:
        dur = 0.0
    with _dur_lock:
        _dur_cache[key] = dur
    return dur


def probe_durations_parallel(paths, max_workers: int = 8) -> None:
    """Прогреть кэш для списка файлов параллельно."""
    with _dur_lock:
        uncached = [p for p in paths if str(p) not in _dur_cache]
    if not uncached:
        return
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        list(ex.map(get_duration, uncached))
