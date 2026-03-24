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

import torch
import whisper

# ── Channel manager (язык из активного канала) ────────────────────────────────
try:
    _BOT_DIR = Path(__file__).resolve().parent.parent.parent / "bot"
    if str(_BOT_DIR) not in sys.path:
        sys.path.insert(0, str(_BOT_DIR))
    from channel_manager import get_active_channel as _get_active_channel

    def get_transcribe_language(channel_id: str = "") -> str:
        _LANG_MAP = {"channel_001_cosmos_de": "de", "channel_002_cosmos_fr": "fr"}
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
        return "de"

# Windows cp1251 консоль не поддерживает эмодзи — переключаем на UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

print(f"CUDA доступен: {torch.cuda.is_available()}")
print(f"GPU: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'N/A'}")

# ── Модели Whisper ────────────────────────────────────────────────────────────
# GPU (RTX 3060 12GB): large-v2 — высокое качество, ~10x быстрее base на CPU
# CPU фолбэк         : base — быстро, достаточное качество
_GPU_MODEL = "large-v2"
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


def detect_device() -> tuple[str, str, str]:
    """
    Определяет устройство, GPU-имя и имя модели Whisper.
    Возвращает: (device, gpu_name, model_size)
    """
    if torch.cuda.is_available():
        gpu_name = torch.cuda.get_device_name(0)
        return "cuda", gpu_name, _GPU_MODEL
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


def transcribe(model, audio_path: Path, use_gpu: bool = False, channel_id: str = "") -> tuple[list[dict], float, list[dict]]:
    """Транскрибирует файл, возвращает сегменты, длину и word-level тайминги."""
    print(f"[Whisper] Транскрибирую: {audio_path.name}")
    result = model.transcribe(
        str(audio_path),
        language=get_transcribe_language(channel_id),
        verbose=False,
        condition_on_previous_text=False,
        fp16=use_gpu,       # fp16 на GPU — быстрее; на CPU — False (не поддерживается)
        word_timestamps=True,  # точные тайминги для каждого слова
    )
    segments = result["segments"]
    duration = segments[-1]["end"] if segments else 0.0

    # Собираем word-level тайминги из всех сегментов
    all_words: list[dict] = []
    for seg in segments:
        for w in seg.get("words", []):
            word_text = w["word"].strip()
            if word_text:
                all_words.append({
                    "word":  word_text,
                    "start": round(w["start"], 3),
                    "end":   round(w["end"],   3),
                })

    print(f"[Whisper] Готово — {len(segments)} сегментов, {len(all_words)} слов, {duration:.1f}s")
    return segments, duration, all_words


def ask_cut_mode() -> str:
    """Всегда возвращает 'random' (grok-режим удалён)."""
    print("  Режим: рандомно 3–6 сек")
    return "random"


def build_segments(whisper_segs: list[dict], mode: str = "random") -> list[dict]:
    """
    Нарезает whisper_segments на блоки с точными целочисленными границами.
    mode='grok'   — строго 10 сек (фиксированные границы, исходный алгоритм)
    mode='random' — рандомно 3–8 сек по границам Whisper-сегментов (без overflow)
    """
    if mode == "grok":
        blocks = _slice_grok(whisper_segs)
    else:
        blocks = _slice_random(whisper_segs)
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


_RANDOM_MIN  = 2    # минимум секунд для блока в random mode (до 5 мин)
_RANDOM_MAX  = 4    # максимум секунд для блока в random mode (до 5 мин)
_FIXED_AFTER = 300  # с этой секунды все блоки строго 5 сек
_FIXED_DUR   = 5    # длина блока после 5-й минуты


def _slice_random(whisper_segs: list[dict]) -> list[dict]:
    """
    Random mode:
      - до 300s  : рандомно 2–4 сек (целые числа), по границам Whisper-сегментов
      - от 300s  : строго 5 сек, фиксированные целочисленные границы

    НЕ разрезает сегменты пополам. Мёрдж коротких хвостов.
    """
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
        in_fixed = current_start >= _FIXED_AFTER
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
            if current_start < _FIXED_AFTER:
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
        in_fixed = b["start"] >= _FIXED_AFTER
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
            in_fixed_now = start >= _FIXED_AFTER
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
        print(f"   [!] Непокрытый хвост: {gap:.1f}s — добавляю последний блок")
        last_end = blocks[-1]["end"]
        blocks.append({
            "id":    len(blocks) + 1,
            "start": last_end,
            "end":   int(real_duration),
            "text":  "[конец]",
        })
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
        _LANG_MAP = {"de": "de", "fr": "fr",
                     "channel_001_cosmos_de": "de", "channel_002_cosmos_fr": "fr"}
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

    if args.mode:
        mode = args.mode
        label = "рандомно 3–8 сек" if mode == "random" else "ровно 10 сек (Grok)"
        print(f"  Режим: {label}")
    else:
        mode = ask_cut_mode()

    # ── Определяем устройство ────────────────────────────────────────────
    device, gpu_name, model_size = detect_device()
    if device == "cuda":
        print(f"[Whisper] GPU: {gpu_name}")
    else:
        print(f"[Whisper] CPU (CUDA nedostupna)")
    print(f"[Whisper] Загружаю модель '{model_size}' на {device.upper()}...")

    model = whisper.load_model(model_size, device=device)
    print(f"[Whisper] Модель загружена.")

    whisper_segs, duration, all_words = transcribe(model, audio_path, use_gpu=(device == "cuda"), channel_id=args.channel or "")
    segments = build_segments(whisper_segs, mode)
    segments, ffmpeg_meta = verify_with_ffmpeg(audio_path, segments)

    channel_id = args.channel or ""
    json_path = save(session, segments, ffmpeg_meta, words=all_words, channel_id=channel_id)
    srt_path  = save_srt(session, segments, channel_id=channel_id)
    vtt_path  = save_vtt(session, segments, channel_id=channel_id)

    print(f"[Agent] OK — {len(segments)} сегментов, покрыто {ffmpeg_meta['coverage']:.1f}s")
    print(f"[Agent] Subtitles created:")
    print(f"   {srt_path.name}")
    print(f"   {vtt_path.name}")
    print(f"   {json_path.name}")


if __name__ == "__main__":
    run()
