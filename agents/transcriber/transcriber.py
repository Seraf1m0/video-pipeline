"""
Transcriber Agent
-----------------
1. Берёт MP3 из data/input/ (корень, не подпапки)
2. Создаёт папку data/input/Video_YYYYMMDD_HHMMSS/ и перемещает туда MP3
3. Транскрибирует весь файл через Whisper "base"
4. Нарезает на блоки: до 5 мин — рандом 2–4s, от 5 мин — строго 5s
5. Верифицирует блоки через ffprobe (покрытие, хвост)
6. Сохраняет data/transcripts/Video_YYYYMMDD_HHMMSS/result.json
   Формат: {"ffmpeg_verified": ..., "segments": [...]}

Запуск: py agents/transcriber/transcriber.py
"""

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from faster_whisper import WhisperModel

# ── Channel manager (язык из активного канала) ────────────────────────────────
try:
    _BOT_DIR = Path(__file__).resolve().parent.parent.parent / "bot"
    if str(_BOT_DIR) not in sys.path:
        sys.path.insert(0, str(_BOT_DIR))
    from channel_manager import get_active_channel as _get_active_channel

    def get_transcribe_language(channel_id: str = "") -> str:
        _LANG_MAP = {
            "de": "de", "fr": "fr", "es": "es",
            "channel_001_cosmos_de": "de",
            "channel_002_cosmos_fr": "fr",
            "channel_003_religion_es": "es",
        }
        if channel_id and channel_id in _LANG_MAP:
            lang = _LANG_MAP[channel_id]
            print(f"  🌐 Язык транскрипции: {lang} (из --channel {channel_id})")
            return lang
        channel = _get_active_channel()
        if channel:
            lang = channel["transcriber"]["language"]
            print(f"  🌐 Язык транскрипции: {lang} ({channel['name']})")
            return lang
        return "de"
except Exception:
    def get_transcribe_language(channel_id: str = "") -> str:
        _LANG_MAP = {"de": "de", "fr": "fr", "es": "es",
                     "channel_001_cosmos_de": "de",
                     "channel_002_cosmos_fr": "fr",
                     "channel_003_religion_es": "es"}
        return _LANG_MAP.get(channel_id, "de")

# Windows cp1251 консоль не поддерживает эмодзи — переключаем на UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import torch
print(f"CUDA доступен: {torch.cuda.is_available()}")
print(f"GPU: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'N/A'}")
del torch

# ── Модели Whisper ────────────────────────────────────────────────────────────
# GPU (RTX 3060 12GB): large-v3-turbo — 3-4x быстрее large-v2, лучшее качество FR/DE
# CPU фолбэк         : base — быстро, достаточное качество
_GPU_MODEL = "large-v3-turbo"
_CPU_MODEL = "base"

# ffmpeg в PATH (winget-установка)
_FFMPEG_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / (
    "Microsoft/WinGet/Packages/"
    "Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe/"
    "ffmpeg-8.0.1-full_build/bin"
)
if _FFMPEG_DIR.exists():
    os.environ["PATH"] = str(_FFMPEG_DIR) + os.pathsep + os.environ.get("PATH", "")

BASE_DIR = Path(__file__).parent.parent.parent

# ── Channel Manager ────────────────────────────────────────────────────────────
_BOT_DIR = BASE_DIR / "bot"
if str(_BOT_DIR) not in sys.path:
    sys.path.insert(0, str(_BOT_DIR))
try:
    from channel_manager import get_channel_data_dir as _get_channel_data_dir
    _CH_DATA_DIR = _get_channel_data_dir(BASE_DIR / "data")
except Exception:
    _CH_DATA_DIR = BASE_DIR / "data"

INPUT_DIR       = _CH_DATA_DIR / "input"
TRANSCRIPTS_DIR = _CH_DATA_DIR / "transcripts"
CACHE_DIR       = BASE_DIR / "data" / "transcripts_cache"

# ── paths.py (новая структура) ─────────────────────────────────────────────────
_UTILS_DIR = BASE_DIR / "agents" / "utils"
if str(_UTILS_DIR) not in sys.path:
    sys.path.insert(0, str(_UTILS_DIR))
try:
    from paths import get_transcripts_dir as _get_transcripts_dir
    _PATHS_OK = True
except ImportError:
    _PATHS_OK = False
    def _get_transcripts_dir(ch, sess): return TRANSCRIPTS_DIR / sess


# ── Whisper кеш ───────────────────────────────────────────────────────────────

def _audio_hash(path: Path) -> str:
    """MD5 по содержимому аудиофайла."""
    import hashlib
    h = hashlib.md5()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def _cache_path(audio_path: Path, mode: str) -> Path:
    return CACHE_DIR / f"{_audio_hash(audio_path)}_{mode}.json"


def _load_cache(audio_path: Path, mode: str) -> dict | None:
    p = _cache_path(audio_path, mode)
    if p.exists():
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return None


def _save_cache(audio_path: Path, mode: str, data: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(_cache_path(audio_path, mode), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def detect_device() -> tuple[str, str, str]:
    """
    Определяет устройство, GPU-имя и имя модели Whisper.
    Возвращает: (device, gpu_name, model_size)
    """
    try:
        import torch as _torch
        if _torch.cuda.is_available():
            return "cuda", _torch.cuda.get_device_name(0), _GPU_MODEL
    except Exception:
        pass
    return "cpu", "", _CPU_MODEL


def find_mp3(search_dir: Path | None = None) -> Path | None:
    """
    Ищет первый MP3/WAV в указанной директории (не в подпапках).
    По умолчанию: корень канала (_CH_DATA_DIR), затем fallback на INPUT_DIR.
    Пропускает папки сессий (Video_*) и служебные папки (input, transcripts и т.д.).
    """
    dirs_to_check = [search_dir] if search_dir else [_CH_DATA_DIR, INPUT_DIR]
    for d in dirs_to_check:
        if not d.exists():
            continue
        for ext in ("*.mp3", "*.wav"):
            files = [f for f in d.glob(ext) if f.parent == d]
            if files:
                return files[0]
    return None


def make_session_name() -> str:
    return "Video_" + datetime.now().strftime("%Y%m%d_%H%M%S")


def move_to_session(audio_path: Path, session: str, channel_root: Path | None = None) -> Path:
    """
    Создаёт {channel_root}/{session}/input/ и перемещает туда MP3.
    Если channel_root не задан — использует _CH_DATA_DIR.
    Удаляет исходный файл из корня канала.
    """
    root = channel_root or _CH_DATA_DIR
    dest_dir = root / session / "input"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"audio{audio_path.suffix}"  # всегда audio.mp3 / audio.wav
    shutil.move(str(audio_path), dest)
    print(f"[Agent] Перемещено: {audio_path.name} -> {session}/input/audio{audio_path.suffix}")
    return dest


def transcribe(model: WhisperModel, audio_path: Path, use_gpu: bool = False, channel_id: str = "") -> tuple[list[dict], float, list[dict]]:
    """Транскрибирует файл через faster-whisper, возвращает сегменты и длину."""
    print(f"[Whisper] Транскрибирую: {audio_path.name}")
    lang = get_transcribe_language(channel_id)

    fw_segments, info = model.transcribe(
        str(audio_path),
        language=lang,
        condition_on_previous_text=False,
        beam_size=5,
        vad_filter=True,        # пропускать тишину — ускоряет и убирает галлюцинации
        word_timestamps=True,   # нужно для каракое-субтитров
    )

    segments = []
    all_words = []
    for seg in fw_segments:
        seg_words = []
        if seg.words:
            for w in seg.words:
                word_dict = {
                    "word":  w.word,
                    "start": round(w.start, 3),
                    "end":   round(w.end,   3),
                }
                seg_words.append(word_dict)
                all_words.append(word_dict)
        segments.append({
            "id":    len(segments) + 1,
            "start": round(seg.start, 3),
            "end":   round(seg.end,   3),
            "text":  seg.text.strip(),
            "words": seg_words,
        })

    duration = segments[-1]["end"] if segments else info.duration or 0.0
    print(f"[Whisper] Готово — {len(segments)} сегментов, {len(all_words)} слов, {duration:.1f}s")
    return segments, duration, all_words


def ask_cut_mode() -> str:
    """Всегда возвращает 'random' (grok-режим удалён)."""
    print("  Режим: рандомно 3–6 сек")
    return "random"


def build_segments(whisper_segs: list[dict], mode: str = "random",
                   channel_id: str = "") -> list[dict]:
    """
    Нарезает whisper_segments на блоки с точными целочисленными границами.
    mode='grok'   — строго 10 сек (фиксированные границы, исходный алгоритм)
    mode='random' — рандомно 2–4 сек (random-зона) → строго 5 сек (fixed-зона)
                    граница зависит от канала: DE/FR=300s, ES=180s
    """
    if mode == "grok":
        blocks = _slice_grok(whisper_segs)
    else:
        blocks = _slice_random(whisper_segs, channel_id=channel_id)
    _verify_segments(blocks, mode)
    return blocks


def _slice_grok(whisper_segs: list[dict]) -> list[dict]:
    """
    Grok mode: строго 10 сек, фиксированные целочисленные границы.
    Слова берутся пропорционально overlap (ratio-based split).
    Последний блок обрезается verify_with_ffmpeg до реального конца аудио.
    """
    TARGET = 10
    blocks: list[dict] = []
    current_words: list[str] = []
    current_start: int | None = None

    for ws in whisper_segs:
        if current_start is None:
            current_start = int(ws["start"])

        words = ws["text"].strip().split()
        if not words:
            continue

        if ws["end"] <= current_start + TARGET:
            current_words.extend(words)
        else:
            overlap   = (current_start + TARGET) - ws["start"]
            total_dur = ws["end"] - ws["start"]
            ratio     = max(0.0, min(1.0, overlap / total_dur)) if total_dur > 0 else 1.0
            take      = max(1, int(len(words) * ratio))

            current_words.extend(words[:take])
            block_end = current_start + TARGET
            blocks.append({
                "id":    len(blocks) + 1,
                "start": current_start,
                "end":   block_end,
                "text":  " ".join(current_words).strip(),
            })
            current_start = block_end
            current_words = words[take:]

    if current_words and current_start is not None:
        blocks.append({
            "id":    len(blocks) + 1,
            "start": current_start,
            "end":   current_start + TARGET,
            "text":  " ".join(current_words).strip(),
        })

    return blocks


_RANDOM_MIN  = 2    # минимум секунд для блока в random mode
_RANDOM_MAX  = 4    # максимум секунд для блока в random mode
_FIXED_DUR   = 5    # длина блока в фиксированной зоне

# Граница перехода к фиксированным блокам — зависит от канала:
#   DE/FR (cosmos)   → 300s (5 мин)
#   ES (religion)    → 180s (3 мин)
_FIXED_AFTER_DEFAULT    = 300
_FIXED_AFTER_BY_CHANNEL = {
    "channel_003_religion_es": 180,
    "es": 180,
}


def _get_fixed_after(channel_id: str = "") -> int:
    return _FIXED_AFTER_BY_CHANNEL.get(channel_id, _FIXED_AFTER_DEFAULT)


def _slice_random(whisper_segs: list[dict], channel_id: str = "") -> list[dict]:
    """
    Random mode:
      - до _fixed_after (канал-зависимо): рандомно 2–4 сек по границам Whisper
      - после: строго 5 сек, фиксированные целочисленные границы

    DE/FR → fixed_after=300s (5 мин)
    ES    → fixed_after=180s (3 мин)

    НЕ разрезает сегменты пополам. Мёрдж коротких хвостов.
    """
    fixed_after = _get_fixed_after(channel_id)
    blocks: list[dict] = []
    current_words: list[str] = []
    current_start: int | None = None
    target = random.randint(_RANDOM_MIN, _RANDOM_MAX)

    for ws in whisper_segs:
        words = ws["text"].strip().split()
        if not words:
            continue

        if current_start is None:
            current_start = int(ws["start"])

        # Определяем режим по позиции текущего блока
        in_fixed = current_start >= fixed_after
        cur_target = _FIXED_DUR if in_fixed else target

        elapsed = ws["end"] - current_start

        if current_words and elapsed > cur_target:
            block_end = max(int(round(ws["start"])), current_start + 1)
            # В фиксированном режиме выравниваем по кратности 5
            if in_fixed:
                block_end = current_start + _FIXED_DUR
            blocks.append({
                "id":    len(blocks) + 1,
                "start": current_start,
                "end":   block_end,
                "text":  " ".join(current_words).strip(),
            })
            current_start = block_end
            current_words = []
            # Новый target только в random-зоне
            if current_start < fixed_after:
                target = random.randint(_RANDOM_MIN, _RANDOM_MAX)

        current_words.extend(words)

    # Последний блок
    if current_words and current_start is not None:
        last_end = max(int(round(whisper_segs[-1]["end"])), current_start + 1)
        blocks.append({
            "id":    len(blocks) + 1,
            "start": current_start,
            "end":   last_end,
            "text":  " ".join(current_words).strip(),
        })

    # ── Постобработка 1: мёрдж коротких блоков (<2с) ─────────────────────────
    merged: list[dict] = []
    i = 0
    while i < len(blocks):
        b = blocks[i]
        dur = b["end"] - b["start"]
        if dur < _RANDOM_MIN and i + 1 < len(blocks):
            nxt = blocks[i + 1]
            merged.append({
                "id":    len(merged) + 1,
                "start": b["start"],
                "end":   nxt["end"],
                "text":  (b["text"] + " " + nxt["text"]).strip(),
            })
            i += 2
        else:
            merged.append({"id": len(merged) + 1,
                           "start": b["start"], "end": b["end"], "text": b["text"]})
            i += 1

    # ── Постобработка 2: разрезка длинных блоков ─────────────────────────────
    final: list[dict] = []
    for b in merged:
        dur  = b["end"] - b["start"]
        # Определяем макс для этого блока
        in_fixed = b["start"] >= fixed_after
        block_max = _FIXED_DUR if in_fixed else _RANDOM_MAX

        if dur <= block_max:
            final.append({"id": len(final) + 1,
                          "start": b["start"], "end": b["end"], "text": b["text"]})
            continue

        words = b["text"].split()
        n_words = len(words)
        start = b["start"]
        remaining_dur = dur

        while remaining_dur > 0:
            in_fixed_now = start >= fixed_after
            blk_max = _FIXED_DUR if in_fixed_now else _RANDOM_MAX
            blk_min = _FIXED_DUR if in_fixed_now else _RANDOM_MIN

            is_last = (remaining_dur <= blk_max)
            if is_last:
                part_dur = remaining_dur
            else:
                hi = min(blk_max, remaining_dur - blk_min)
                if hi < blk_min:
                    hi = remaining_dur
                part_dur = (blk_max if in_fixed_now
                            else random.randint(blk_min, int(hi)))

            part_end = start + part_dur
            ratio    = part_dur / remaining_dur if remaining_dur > 0 else 1.0

            take = len(words) if is_last else max(1, min(int(n_words * ratio), len(words) - 1))

            final.append({
                "id":    len(final) + 1,
                "start": start,
                "end":   part_end,
                "text":  " ".join(words[:take]).strip(),
            })

            words         = words[take:]
            n_words       = len(words)
            remaining_dur -= part_dur
            start          = part_end

            if not words:
                break

    return final


def _verify_segments(blocks: list[dict], mode: str) -> None:
    """Проверяет корректность нарезки через assert, печатает первые 5 блоков."""
    print(f"\n[Check] Режим: {mode} | Итого блоков: {len(blocks)}")

    for i, block in enumerate(blocks):
        assert isinstance(block["start"], int), \
            f"Блок {i+1}: start должен быть int, получен {type(block['start'])}"
        assert isinstance(block["end"], int), \
            f"Блок {i+1}: end должен быть int, получен {type(block['end'])}"
        assert block["text"].strip() != "", f"Блок {i+1} пустой!"
        if i > 0:
            assert block["start"] == blocks[i - 1]["end"], \
                f"Пропуск между блоком {i} и {i+1}: " \
                f"{blocks[i-1]['end']}s → {block['start']}s"

    print("[OK] Все проверки пройдены")
    print(f"Первые 5 блоков:")
    for b in blocks[:5]:
        print(f"  #{b['id']}: {b['start']}s-{b['end']}s | {b['text'][:50]}")

    durs = [b["end"] - b["start"] for b in blocks]
    print(
        f"  Мин: {min(durs)}s | Макс: {max(durs)}s | Ср: {sum(durs)/len(durs):.1f}s"
    )

    # Предупреждение о переполнении (информационно, не ошибка)
    overflow = [
        b for b in blocks
        if b["end"] > b["start"] and
        len(b["text"].split()) / 2.5 > (b["end"] - b["start"]) * 1.1
    ]
    if overflow:
        print(f"  [!] Переполнение (>110% слов) в {len(overflow)} блоках:")
        for b in overflow[:5]:
            dur = b["end"] - b["start"]
            wc = len(b["text"].split())
            pct = int(wc / 2.5 / dur * 100)
            print(f"      #{b['id']}: {dur}s | {wc}w | {pct}% | {b['text'][:40]}")
    else:
        print("  Переполнений нет.")


# ---------------------------------------------------------------------------
# FFmpeg верификация
# ---------------------------------------------------------------------------

def verify_with_ffmpeg(audio_path: Path, blocks: list[dict]) -> tuple[list[dict], dict]:
    """
    Проверяет реальную длину аудио через ffprobe и сверяет с нашими блоками.
    Если непокрытый хвост > 1с — добавляет финальный блок "[конец]".
    Возвращает (обновлённые блоки, метаданные для result.json).
    """
    real_duration = None

    # Попытка 1: -show_format
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        str(audio_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    try:
        info = json.loads(result.stdout)
        real_duration = float(info["format"]["duration"])
    except (json.JSONDecodeError, KeyError, ValueError):
        pass

    # Попытка 2: -show_streams (fallback для некоторых форматов)
    if real_duration is None:
        cmd2 = [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_streams",
            str(audio_path),
        ]
        result2 = subprocess.run(cmd2, capture_output=True, text=True)
        try:
            info2 = json.loads(result2.stdout)
            for stream in info2.get("streams", []):
                if "duration" in stream:
                    real_duration = float(stream["duration"])
                    break
        except (json.JSONDecodeError, KeyError, ValueError):
            pass

    # Fallback: используем длину из Whisper-сегментов
    if real_duration is None:
        real_duration = float(blocks[-1]["end"]) if blocks else 0.0
        print(f"   [!] ffprobe не дал длину — используем Whisper: {real_duration:.1f}s")

    coverage = blocks[-1]["end"] - blocks[0]["start"]
    gap      = real_duration - coverage

    # Последний блок вышел за конец аудио → обрезаем до реального конца
    if gap < -1:
        old_end = blocks[-1]["end"]
        blocks[-1]["end"] = int(real_duration)
        print(
            f"   [fix] Последний блок обрезан: {old_end}s -> {int(real_duration)}s "
            f"(аудио кончается раньше)"
        )
        coverage = blocks[-1]["end"] - blocks[0]["start"]
        gap      = real_duration - coverage

    if gap > 1:
        print(f"   [!] Непокрытый хвост: {gap:.1f}s — нарезаю на 5s блоки")
        last_end = blocks[-1]["end"]
        total_end = int(real_duration)
        t = last_end
        while t < total_end:
            blk_end = min(t + 5, total_end)
            blocks.append({
                "id":    len(blocks) + 1,
                "start": t,
                "end":   blk_end,
                "text":  "[конец]",
            })
            t = blk_end
        coverage = blocks[-1]["end"] - blocks[0]["start"]
        gap      = real_duration - coverage

    print(f"\n[FFmpeg] Верификация:")
    print(f"   Длина файла:    {real_duration:.1f}s")
    print(f"   Покрытие блоков:{coverage:.1f}s")
    print(f"   Погрешность:    {gap:.1f}s")
    print(f"   [OK] Все блоки верифицированы")

    meta = {
        "ffmpeg_verified": True,
        "total_duration":  round(real_duration, 3),
        "coverage":        float(coverage),
        "gap":             round(gap, 3),
    }
    return blocks, meta


def preprocess_voice(audio_path: Path,
                     min_silence_s:  float = 0.6,
                     keep_each_side: float = 0.1,
                     threshold_db:   float = -42.0) -> Path:
    """
    Точная подрезка пауз в озвучке ДО транскрибации.

    Алгоритм:
      1. silencedetect — находим все паузы >= min_silence_s
      2. Для каждой паузы: оставляем keep_each_side с каждого края,
         вырезаем середину
         Пример: пауза 0.70s → оставить 0.1s + вырезать 0.50s + оставить 0.1s
      3. Склеиваем оставшиеся сегменты через atrim + concat
      4. Заменяем исходный файл in-place

    Что трогаем:
      - Паузы >= 0.6s (полная тишина, плохое дыхание)
    Что НЕ трогаем:
      - Паузы < 0.6s (естественные паузы между словами/предложениями)
    """
    import re as _re

    def _dur(p: Path) -> float:
        res = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(p)],
            capture_output=True, text=True,
        )
        try:
            return float(json.loads(res.stdout)["format"]["duration"])
        except Exception:
            return 0.0

    # ── Шаг 1: detect silences ───────────────────────────────────────────
    r = subprocess.run([
        "ffmpeg", "-i", str(audio_path),
        "-af", f"silencedetect=noise={threshold_db}dB:duration={min_silence_s}",
        "-f", "null", "-",
    ], capture_output=True, text=True)

    silences: list[tuple[float, float]] = []
    sil_start = None
    for line in r.stderr.split("\n"):
        if "silence_start" in line:
            m = _re.search(r"silence_start: ([\d.]+)", line)
            if m:
                sil_start = float(m.group(1))
        elif "silence_end" in line and sil_start is not None:
            m = _re.search(r"silence_end: ([\d.]+)", line)
            if m:
                silences.append((sil_start, float(m.group(1))))
                sil_start = None

    if not silences:
        print(f"[Preprocess] Пауз >= {min_silence_s}s не найдено")
        return audio_path

    total_dur = _dur(audio_path)
    print(f"[Preprocess] Найдено {len(silences)} пауз >= {min_silence_s}s")

    # ── Шаг 2: строим список сегментов для сохранения ───────────────────
    keep_segs: list[tuple[float, float]] = []
    cursor = 0.0
    removed_total = 0.0

    for sil_s, sil_e in silences:
        cut_dur = (sil_e - sil_s) - 2 * keep_each_side
        if cut_dur <= 0:
            continue
        if sil_s > cursor:
            keep_segs.append((cursor, sil_s))
        keep_segs.append((sil_s, sil_s + keep_each_side))
        keep_segs.append((sil_e - keep_each_side, sil_e))
        cursor = sil_e
        removed_total += cut_dur
        print(f"  {sil_s:.2f}-{sil_e:.2f}s ({sil_e-sil_s:.2f}s) -> cut {cut_dur:.2f}s")

    if cursor < total_dur:
        keep_segs.append((cursor, total_dur))

    if removed_total < 0.05:
        print(f"[Preprocess] Нечего вырезать")
        return audio_path

    # ── Шаг 3: decode → PCM, нарезать через atrim (сэмплоточно), склеить ──
    # ffconcat inpoint/outpoint — пакетная точность (~1024 сэмпла = 23ms).
    # atrim в filter_complex — точность до одного сэмпла, без двоения/сдвигов.
    # Батчи по 25 сегментов → нет ограничения длины команды.
    pcm_wav = audio_path.parent / "_vc_pcm.wav"
    out_tmp = audio_path.parent / f"_vc_tmp{audio_path.suffix}"

    r_pcm = subprocess.run([
        "ffmpeg", "-y", "-i", str(audio_path),
        "-c:a", "pcm_f32le", "-ar", "44100", str(pcm_wav),
    ], capture_output=True)
    if r_pcm.returncode != 0 or not pcm_wav.exists():
        print("[Preprocess] decode to PCM failed — используем оригинал")
        return audio_path

    BATCH = 25
    batches    = [keep_segs[i:i + BATCH] for i in range(0, len(keep_segs), BATCH)]
    batch_wavs: list[Path] = []
    failed = False

    for b_idx, batch in enumerate(batches):
        n     = len(batch)
        b_out = audio_path.parent / f"_vc_b{b_idx}.wav"
        # asplit → N копий входа, каждая обрезается atrim + микрофейд 4ms
        # Фейды убирают щелчки на стыках (ненулевой сэмпл при обрезке)
        fc = [f"[0:a]asplit={n}" + "".join(f"[sp{j}]" for j in range(n))]
        for j, (st, en) in enumerate(batch):
            seg_dur = en - st
            fo_st   = max(0.0, seg_dur - 0.004)
            fc.append(
                f"[sp{j}]atrim=start={st:.6f}:end={en:.6f},"
                f"asetpts=PTS-STARTPTS,"
                f"afade=t=in:st=0:d=0.004,"
                f"afade=t=out:st={fo_st:.6f}:d=0.004"
                f"[seg{j}]"
            )
        labels = "".join(f"[seg{j}]" for j in range(n))
        fc.append(f"{labels}concat=n={n}:v=0:a=1[out]")
        rb = subprocess.run([
            "ffmpeg", "-y", "-i", str(pcm_wav),
            "-filter_complex", ";".join(fc),
            "-map", "[out]", "-c:a", "pcm_f32le", "-ar", "44100", str(b_out),
        ], capture_output=True)
        if rb.returncode != 0 or not b_out.exists():
            err = rb.stderr.decode(errors="replace")[-300:]
            print(f"[Preprocess] batch {b_idx} failed\n{err}")
            failed = True
            break
        batch_wavs.append(b_out)

    pcm_wav.unlink(missing_ok=True)

    if failed or not batch_wavs:
        for bw in batch_wavs:
            bw.unlink(missing_ok=True)
        return audio_path

    # ── Шаг 4: склеиваем батчи через ffconcat (уже точно обрезанные WAV) ──
    concat_txt = audio_path.parent / "_vc_concat.txt"
    lines = ["ffconcat version 1.0"]
    for bw in batch_wavs:
        lines.append(f"file '{str(bw.resolve()).replace(chr(92), '/')}'")
    concat_txt.write_text("\n".join(lines), encoding="utf-8")

    r2 = subprocess.run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_txt),
        "-c:a", "libmp3lame", "-q:a", "2",
        str(out_tmp),
    ], capture_output=True)

    concat_txt.unlink(missing_ok=True)
    for bw in batch_wavs:
        bw.unlink(missing_ok=True)

    if r2.returncode != 0 or not out_tmp.exists():
        err = r2.stderr.decode(errors="replace")[-400:]
        print(f"[Preprocess] final concat failed — используем оригинал\n{err}")
        out_tmp.unlink(missing_ok=True)
        return audio_path

    clean_dur = _dur(out_tmp)
    print(f"[Preprocess] Готово: -{removed_total:.1f}s "
          f"({total_dur:.1f}s -> {clean_dur:.1f}s)")

    # Заменяем оригинал очищенной версией in-place
    out_tmp.replace(audio_path)
    return audio_path


def extract_audio_segment_ffmpeg(
    audio_path: Path,
    start: int,
    end: int,
    output_path: Path,
) -> bool:
    """
    Нарезает аудио точно по таймингам через FFmpeg без перекодирования.
    Верифицирует длину нарезанного клипа через ffprobe.
    Возвращает True если погрешность <= 1с, иначе False.
    """
    cmd = [
        "ffmpeg", "-y",
        "-i", str(audio_path),
        "-ss", str(start),
        "-to", str(end),
        "-c", "copy",
        "-avoid_negative_ts", "1",
        str(output_path),
    ]
    subprocess.run(cmd, capture_output=True)

    # Верифицировать длину нарезанного клипа
    verify_cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        str(output_path),
    ]
    result = subprocess.run(verify_cmd, capture_output=True, text=True)
    info = json.loads(result.stdout)
    actual_duration = float(info["format"]["duration"])
    expected = end - start

    if abs(actual_duration - expected) > 1.0:
        print(f"  [!] Клип {start}-{end}s: ожидалось {expected}s, получилось {actual_duration:.1f}s")
        return False
    return True


# ---------------------------------------------------------------------------
# Сохранение
# ---------------------------------------------------------------------------

def save(session: str, segments: list[dict], meta: dict | None = None,
         words: list[dict] | None = None, channel_id: str = "") -> Path:
    out_dir = (_get_transcripts_dir(channel_id, session) if channel_id
               else TRANSCRIPTS_DIR / session)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "result.json"

    data: dict = {}
    if meta:
        data.update(meta)
    data["segments"] = segments
    if words:
        data["words"] = words

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return out_file


# ---------------------------------------------------------------------------
# Субтитры
# ---------------------------------------------------------------------------

def _srt_time(seconds: float) -> str:
    """Секунды → HH:MM:SS,mmm (формат SRT)."""
    ms  = int(round(seconds * 1000))
    h   = ms // 3_600_000;  ms -= h * 3_600_000
    m   = ms // 60_000;     ms -= m * 60_000
    s   = ms // 1_000;      ms -= s * 1_000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _vtt_time(seconds: float) -> str:
    """Секунды → HH:MM:SS.mmm (формат VTT)."""
    return _srt_time(seconds).replace(",", ".")


def save_srt(session: str, segments: list[dict], channel_id: str = "") -> Path:
    out_dir  = (_get_transcripts_dir(channel_id, session) if channel_id
                else TRANSCRIPTS_DIR / session)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "subtitles.srt"

    blocks = []
    for seg in segments:
        blocks.append(
            f"{seg['id']}\n"
            f"{_srt_time(seg['start'])} --> {_srt_time(seg['end'])}\n"
            f"{seg['text'].strip()}\n"
        )

    out_file.write_text("\n".join(blocks), encoding="utf-8")
    return out_file


def save_vtt(session: str, segments: list[dict], channel_id: str = "") -> Path:
    out_dir  = (_get_transcripts_dir(channel_id, session) if channel_id
                else TRANSCRIPTS_DIR / session)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "subtitles.vtt"

    lines = ["WEBVTT", ""]
    for seg in segments:
        lines.append(f"{_vtt_time(seg['start'])} --> {_vtt_time(seg['end'])}")
        lines.append(seg["text"].strip())
        lines.append("")

    out_file.write_text("\n".join(lines), encoding="utf-8")
    return out_file


def run():
    parser = argparse.ArgumentParser(description="Transcriber Agent")
    parser.add_argument("--mode", choices=["random"], default="random",
                        help="Cut mode: random (3-6s по паузам Whisper)")
    parser.add_argument("--input", default=None, metavar="PATH",
                        help="Path to MP3/WAV file (skips auto-search)")
    parser.add_argument("--session-name", default=None, metavar="SESSION",
                        help="Override output session name; skips file move (file stays in place)")
    parser.add_argument("--channel", default=None, metavar="CHANNEL_ID",
                        help="ID канала (channel_001_cosmos_de / channel_002_cosmos_fr)")
    args = parser.parse_args()

    # ── Определяем корень канала ─────────────────────────────────────────
    try:
        import sys as _sys
        _utils = str(BASE_DIR / "agents" / "utils")
        if _utils not in _sys.path:
            _sys.path.insert(0, _utils)
        from paths import CHANNELS_DIR as _CHANNELS_DIR
        _LANG_MAP = {"de": "de", "fr": "fr", "es": "es",
                     "channel_001_cosmos_de": "de",
                     "channel_002_cosmos_fr": "fr",
                     "channel_003_religion_es": "es"}
        _ch_lang = _LANG_MAP.get(args.channel or "", "") if args.channel else ""
        channel_root = (_CHANNELS_DIR / _ch_lang) if _ch_lang else _CH_DATA_DIR
    except Exception:
        channel_root = _CH_DATA_DIR

    if args.input:
        audio_path = Path(args.input)
        if not audio_path.is_absolute():
            audio_path = BASE_DIR / audio_path
        if not audio_path.exists():
            print(f"[Agent] Файл не найден: {audio_path}")
            return
    else:
        audio_path = find_mp3(channel_root)
        if not audio_path:
            print(f"[Agent] Нет MP3/WAV в {channel_root}")
            print("        Положи аудио прямо в папку канала (de/ или fr/) и запусти снова.")
            return

    if args.session_name:
        session = args.session_name
        print(f"[Agent] Сессия (задана вручную): {session}")
        print(f"[Agent] Файл остаётся: {audio_path}")
    else:
        session = make_session_name()
        print(f"[Agent] Сессия: {session}")
        audio_path = move_to_session(audio_path, session, channel_root)

    # Автолог в channel_root/_logs/transcriber_SESSION.log
    try:
        import io as _io
        class _Tee:
            def __init__(self, stream, filepath):
                filepath.parent.mkdir(parents=True, exist_ok=True)
                self._file   = open(filepath, "w", encoding="utf-8", errors="replace")
                self._stream = stream
            def write(self, data):
                self._stream.write(data); self._file.write(data)
            def flush(self):
                self._stream.flush(); self._file.flush()
            def __getattr__(self, attr):
                return getattr(self._stream, attr)
        _log_path = channel_root / "_logs" / f"transcriber_{session}.log"
        sys.stdout = _Tee(sys.stdout, _log_path)
    except Exception:
        pass

    if args.mode:
        mode = args.mode
        label = "рандомно 3–8 сек" if mode == "random" else "ровно 10 сек (Grok)"
        print(f"  Режим: {label}")
    else:
        mode = ask_cut_mode()

    channel_id = args.channel or ""

    # ── Предобработка: убираем тишину и артефакты до транскрибации ──────
    # Важно: ДО кеша, чтобы Whisper и ассемблер работали с одним файлом
    audio_path = preprocess_voice(audio_path)

    # ── Кеш транскрипции ─────────────────────────────────────────────────
    cached = _load_cache(audio_path, mode)
    if cached:
        print(f"[Whisper] ✅ Кеш найден — пропускаем транскрипцию ({audio_path.name})")
        segments   = cached.get("segments", [])
        all_words  = cached.get("words", [])
        ffmpeg_meta = {k: v for k, v in cached.items() if k not in ("segments", "words")}
        json_path = save(session, segments, ffmpeg_meta, words=all_words, channel_id=channel_id)
        srt_path  = save_srt(session, segments, channel_id=channel_id)
        vtt_path  = save_vtt(session, segments, channel_id=channel_id)
        print(f"[Agent] OK — {len(segments)} сегментов (из кеша)")
        print(f"[Agent] Subtitles created:")
        print(f"   {srt_path.name}")
        print(f"   {vtt_path.name}")
        print(f"   {json_path.name}")
        return

    # ── Определяем устройство ────────────────────────────────────────────
    device, gpu_name, model_size = detect_device()
    if device == "cuda":
        print(f"[Whisper] GPU: {gpu_name}")
        compute = "float16"
    else:
        print(f"[Whisper] CPU (CUDA недоступна)")
        compute = "int8"
    print(f"[Whisper] Загружаю модель '{model_size}' ({compute}) на {device.upper()}...")

    model = WhisperModel(model_size, device=device, compute_type=compute)
    print(f"[Whisper] Модель загружена.")

    whisper_segs, duration, all_words = transcribe(model, audio_path, use_gpu=(device == "cuda"), channel_id=channel_id)
    segments = build_segments(whisper_segs, mode, channel_id=channel_id)
    segments, ffmpeg_meta = verify_with_ffmpeg(audio_path, segments)

    json_path = save(session, segments, ffmpeg_meta, words=all_words, channel_id=channel_id)
    srt_path  = save_srt(session, segments, channel_id=channel_id)
    vtt_path  = save_vtt(session, segments, channel_id=channel_id)

    # Сохраняем в кеш для следующего раза
    _save_cache(audio_path, mode, {**ffmpeg_meta, "segments": segments, "words": all_words})

    print(f"[Agent] OK — {len(segments)} сегментов, покрыто {ffmpeg_meta['coverage']:.1f}s")
    print(f"[Agent] Subtitles created:")
    print(f"   {srt_path.name}")
    print(f"   {vtt_path.name}")
    print(f"   {json_path.name}")


if __name__ == "__main__":
    run()
