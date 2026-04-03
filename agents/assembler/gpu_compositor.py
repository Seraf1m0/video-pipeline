"""
gpu_compositor.py — GPU-рендер субтитров + цветокоррекция на PyTorch/CUDA

Заменяет финальный FFmpeg-проход:
  libass karaoke (CPU, ~600s) → PyTorch CUDA compositing (~60-90s)

Архитектура:
  1. Startup (CPU, ~1-2s):
     - Pillow растеризует каждую строку субтитров → numpy
     - Всё улетает в VRAM как torch.tensor.cuda()

  2. Main loop (GPU):
     FFmpeg decode pipe → torch.cuda → color_grade → subtitle_composite → torch.cpu → FFmpeg NVENC pipe

Использование:
  from gpu_compositor import run as gpu_render
  gpu_render(videotrack, intro, audio, ass_path, output, color_grade="cool_cinematic")
"""

import bisect
import json
import math
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image, ImageDraw, ImageFont

# ─── Разрешение ───────────────────────────────────────────────────────────────
W, H       = 1920, 1080
FRAME_BYTES = W * H * 3  # rgb24

# ─── Шрифты ───────────────────────────────────────────────────────────────────
_FONT_CANDIDATES = [
    "C:/Users/Serafim/AppData/Local/Microsoft/Windows/Fonts/MONTSERRAT-EXTRABOLD.TTF",
    "C:/Users/Serafim/AppData/Local/Microsoft/Windows/Fonts/Montserrat-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
]

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")


# ═══════════════════════════════════════════════════════════════════════════════
# 1. ASS PARSER
# ═══════════════════════════════════════════════════════════════════════════════

def _ass_time_to_sec(t: str) -> float:
    """'0:01:23.45' → секунды"""
    h, m, s = t.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def _strip_ass_tags(text: str) -> str:
    """Убрать {\\...} теги, починить слипшиеся слова от karaoke Whisper."""
    text = re.sub(r"\{[^}]*\}", "", text)
    text = text.replace("\\N", "\n").replace("\\n", "\n").replace("\\h", " ")
    # Whisper karaoke иногда слипает слова без пробела: "WORD.NEXTWORD" → "WORD. NEXTWORD"
    text = re.sub(r"([.!?,])([A-ZÀ-Ÿa-zà-ÿ])", r"\1 \2", text)
    # Схлопнуть повторяющиеся слова подряд (артефакт karaoke дублирования)
    words = text.split()
    clean = [words[0]] if words else []
    for w in words[1:]:
        if w.lower() != clean[-1].lower():
            clean.append(w)
    return " ".join(clean).strip()


_KARAOKE_MAX_CHARS  = 35  # жёсткий лимит символов на группу (с пробелами)
_KARAOKE_GROUP_SIZE = 3   # попытка взять до N слов; снижается если > _KARAOKE_MAX_CHARS


def _parse_karaoke_words(text: str, line_start: float, line_end: float) -> list[dict]:
    """
    Разбивает karaoke строку с \\kf/\\k тегами на группы по _KARAOKE_GROUP_SIZE слов.
    \\kf62}WORD  → длительность слова 0.62s (centiseconds → seconds)
    Каждая группа показывается весь суммарный интервал с fade анимацией.
    Возвращает [{'start': float, 'end': float, 'text': str}, ...]
    """
    pattern = re.compile(r'\{[^}]*\\k[fo]?(\d+)[^}]*\}([^{]*)')
    matches = pattern.findall(text)
    if not matches:
        return []

    # Собираем все слова с их временными метками
    raw_words = []
    cursor = line_start
    for dur_cs, word in matches:
        dur = int(dur_cs) / 100.0
        word = word.strip()
        word_end = min(cursor + dur, line_end)
        if word and word_end > cursor + 0.02:
            raw_words.append({"start": cursor, "end": word_end, "text": word.upper()})
        cursor += dur

    if not raw_words:
        return []

    # Группируем: до _KARAOKE_GROUP_SIZE слов, но не более _KARAOKE_MAX_CHARS символов.
    # Логика: пробуем 3 → 2 → 1 слово; берём первый вариант, который влазит в лимит.
    # 1 слово берём всегда (даже если длиннее лимита — нельзя дробить слова).
    # "words" сохраняем для word_highlight анимации
    groups = []
    i = 0
    while i < len(raw_words):
        chunk = None
        for count in range(_KARAOKE_GROUP_SIZE, 0, -1):
            candidate = raw_words[i : i + count]
            if not candidate:
                break
            text_candidate = " ".join(w["text"] for w in candidate)
            if count == 1 or len(text_candidate) <= _KARAOKE_MAX_CHARS:
                chunk = candidate
                break
        if not chunk:
            i += 1
            continue
        group_text  = " ".join(w["text"] for w in chunk)
        group_start = chunk[0]["start"]
        group_end   = chunk[-1]["end"]
        if group_end > group_start + 0.02:
            groups.append({
                "start": group_start,
                "end":   group_end,
                "text":  group_text,
                "words": chunk,          # per-word timing для word_highlight
            })
        i += len(chunk)

    return groups


def parse_ass(ass_path: Path) -> list[dict]:
    """
    Парсит ASS файл → список событий без перекрытий:
      [{'start': float, 'end': float, 'text': str, 'pos_y': int|None}, ...]
    Karaoke \\kf теги разбиваются на per-word события (одно слово = одно событие).

    Времена в ASS — content-relative (0 = конец интро = начало контента).
    Генераторы (generate_karaoke_ass, generate_ass) уже сдвигают -intro_duration.
    GPU compositor использует их напрямую без дополнительной коррекции.

    pos_y: извлекается из \\pos(x,y) тега (Alignment=2 = bottom-center).
           GPU compositor использует pos_y как нижнюю границу текста.
    """
    _pos_re = re.compile(r'\\pos\(([0-9.]+),([0-9.]+)\)')

    events = []
    in_events = False
    fmt_fields = []

    with open(ass_path, encoding="utf-8-sig", errors="ignore") as f:
        for line in f:
            line = line.rstrip("\n")
            if line.strip() == "[Events]":
                in_events = True
                continue
            if in_events:
                if line.startswith("["):
                    break
                if line.startswith("Format:"):
                    fmt_fields = [x.strip() for x in line[7:].split(",")]
                elif line.startswith("Dialogue:"):
                    parts = line[9:].split(",", len(fmt_fields) - 1)
                    if len(parts) < len(fmt_fields):
                        continue
                    d = dict(zip(fmt_fields, parts))
                    raw_text = d.get("Text", "")
                    try:
                        line_start = _ass_time_to_sec(d["Start"])
                        line_end   = _ass_time_to_sec(d["End"])
                    except Exception:
                        continue

                    # Извлекаем \pos(x,y) — bottom-center якорь для GPU compositor
                    pm = _pos_re.search(raw_text)
                    pos_y = int(float(pm.group(2))) if pm else None

                    # Попытка разбить на per-word события (karaoke \\kf)
                    word_events = _parse_karaoke_words(raw_text, line_start, line_end)
                    if word_events:
                        for ev in word_events:
                            ev["pos_y"] = pos_y
                        events.extend(word_events)
                    else:
                        # Нет \\kf тегов — показываем всю строку целиком
                        text = _strip_ass_tags(raw_text)
                        if text:
                            events.append({
                                "start": line_start,
                                "end":   line_end,
                                "text":  text.upper(),
                                "pos_y": pos_y,
                            })

    # Сортируем по времени и обрезаем перекрытия
    events.sort(key=lambda e: e["start"])
    for i in range(len(events) - 1):
        if events[i]["end"] > events[i + 1]["start"]:
            events[i]["end"] = events[i + 1]["start"]
    # Убираем нулевые/отрицательные/до-интро события (t < 0 = до конца интро)
    events = [e for e in events if e["end"] > max(e["start"] + 0.02, 0.0)]

    return events


# ═══════════════════════════════════════════════════════════════════════════════
# 2. SUBTITLE PRE-RENDER (CPU, один раз при старте)
# ═══════════════════════════════════════════════════════════════════════════════

def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


# ─── Subtitle animation types ─────────────────────────────────────────────────
ANIM_FADE       = "fade"          # простой fade in/out
ANIM_FADE_SCALE = "fade_scale"    # fade + scale 0.85 → 1.0 → 0.85
ANIM_SLIDE_UP   = "slide_up"      # выезд снизу + fade
ANIM_POP        = "pop"           # быстрый bounce scale + fade
ANIM_WORD_HL    = "word_highlight" # karaoke: текущее слово жёлтое, второе — белое dim

_SLIDE_PX    = 22    # пикселей смещения для slide_up
_POP_FRAMES  = 4     # кадров для bounce при pop
_COLOR_BRIGHT = (255, 220, 50,  255)  # жёлтый — активное слово
_COLOR_DIM    = (180, 180, 180, 255)  # серый — неактивное слово
_COLOR_WHITE  = (255, 255, 255, 255)  # белый — обычный текст


def _render_sub_rgba(text: str, font: ImageFont.FreeTypeFont,
                     max_width: int = W - 160,
                     color: tuple = _COLOR_WHITE,
                     outline: int = 3,
                     fixed_h: int | None = None,
                     fixed_by: int | None = None) -> np.ndarray:
    """
    Рендерит строку субтитра в RGBA numpy [H, W, 4] uint8.
    outline=0 → без обводки (для karaoke word_hl).

    fixed_h  : если задан — высота canvas фиксирована (одинакова для всех слов группы).
    fixed_by : если задан — позиция рисования по Y фиксирована (одинаковый baseline).
               Вместе fixed_h+fixed_by обеспечивают baseline-выравнивание в word_hl режиме.
    """
    pad = 12
    dummy = Image.new("RGBA", (1, 1))
    draw  = ImageDraw.Draw(dummy)

    full_text = text
    bb = draw.textbbox((0, 0), full_text, font=font)
    tw = min(bb[2] - bb[0] + pad * 2 + 6, W)

    if fixed_h is not None:
        # Режим karaoke: canvas одинаковой высоты, by фиксирован → единый baseline
        th = fixed_h
        by = fixed_by if fixed_by is not None else (pad + 3)
    else:
        th = bb[3] - bb[1] + pad * 2 + 6
        # Выравниваем по нижнему краю текста (bb[3]) а не по верху canvas
        by = th - bb[3] - pad - 3

    img  = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    bx = pad + 3
    if outline > 0:
        for dx in range(-outline, outline + 1, max(1, outline)):
            for dy in range(-outline, outline + 1, max(1, outline)):
                if dx == 0 and dy == 0:
                    continue
                draw.text((bx + dx, by + dy), full_text, font=font,
                          fill=(0, 0, 0, 220), align="center")
    draw.text((bx, by), full_text, font=font, fill=color, align="center")

    return np.array(img, dtype=np.uint8)


def _scale_tensor(t: torch.Tensor, scale: float) -> torch.Tensor:
    """Масштабирует [H, W, 4] uint8 тензор субтитра на GPU."""
    if abs(scale - 1.0) < 0.005:
        return t
    h, w = t.shape[:2]
    nh, nw = max(1, int(h * scale)), max(1, int(w * scale))
    inp = t.float().permute(2, 0, 1).unsqueeze(0)  # [1, 4, H, W]
    out = F.interpolate(inp, size=(nh, nw), mode="bilinear", align_corners=False)
    return out.squeeze(0).permute(1, 2, 0).clamp(0, 255).byte()


def prerender_subtitles(events: list[dict],
                        font_size: int,
                        fps: float,
                        fade_ms: int = 100,
                        font_path: str | None = None,
                        subtitle_style: str = "default",
                        subtitle_anim: str = ANIM_SLIDE_UP) -> list[dict]:
    """
    CPU: растеризует все субтитры параллельно → GPU: загружает в VRAM (batch, non-blocking).

    subtitle_style: "default"|"karaoke" → снизу экрана
                    "scripture"          → по центру экрана
    subtitle_anim:  ANIM_FADE | ANIM_FADE_SCALE | ANIM_SLIDE_UP | ANIM_POP | ANIM_WORD_HL
    """
    import concurrent.futures

    if font_path and Path(font_path).exists():
        try:
            font = ImageFont.truetype(font_path, font_size)
        except Exception:
            font = _load_font(font_size)
    else:
        font = _load_font(font_size)

    # Scripture style now uses \pos(960,y) tags → bottom anchor, not center
    center_screen = False
    fade_frames   = max(1, int(fade_ms / 1000 * fps))

    # ── Шаг 1: параллельный CPU-рендер ───────────────────────────────────────
    # Каждый event независим — PIL Image объекты не разделяются между потоками.
    def _render_event_cpu(ev):
        """Возвращает (numpy_arrays_dict, meta_dict) без CUDA."""
        start_f = int(ev["start"] * fps)
        end_f   = max(start_f + 1, int(ev["end"] * fps))

        if subtitle_anim == ANIM_WORD_HL and ev.get("words") and len(ev["words"]) > 1:
            words_data = ev["words"]
            bright_arrs, dim_arrs = [], []
            word_start_fs, word_widths = [], []

            # ── Baseline-выравнивание через font.getmetrics() ──────────────────
            # Проблема: разные слова имеют разный bb[1] (расстояние от anchor до
            # верхнего края глифа). "Dieu" (D заглавная) bb[1]=8, "et" bb[1]=15.
            # Это приводит к разному by (позиция рисования) → разный baseline
            # в canvas → слова прыгают вертикально при composite.
            #
            # Решение: все слова рисуются с ФИКСИРОВАННЫМ by=pad+3.
            # Canvas высота = ascent + descent + pad*2 + 6 (одинакова для всех).
            # Тогда baseline у всех = pad+3+ascent = константа → нет прыжков.
            pad = 12
            font_ascent, font_descent = font.getmetrics()
            word_canvas_h = font_ascent + font_descent + pad * 2 + 6
            by_fixed = pad + 3   # фиксированный draw-y: ascender top всегда здесь

            for w in words_data:
                arr_b = _render_sub_rgba(w["text"], font, color=_COLOR_BRIGHT, outline=0,
                                         fixed_h=word_canvas_h, fixed_by=by_fixed)
                arr_d = _render_sub_rgba(w["text"], font, color=_COLOR_DIM,    outline=0,
                                         fixed_h=word_canvas_h, fixed_by=by_fixed)
                bright_arrs.append(arr_b)
                dim_arrs.append(arr_d)
                word_start_fs.append(int(w["start"] * fps))
                word_widths.append(arr_b.shape[1])
            max_th = word_canvas_h  # все слова одинаковой высоты — паддинг не нужен
            gap_px  = 14
            total_w = sum(word_widths) + gap_px * (len(words_data) - 1)
            x_start = (W - total_w) // 2
            x_positions, cx = [], x_start
            for ww in word_widths:
                x_positions.append(cx)
                cx += ww + gap_px
            # Bottom anchor: pos_y из \pos(x,y) тега ASS (Alignment=2 = bottom-center)
            # Если pos_y есть — текст bottom-aligned к pos_y; иначе — к H-50
            _pos_y = ev.get("pos_y")
            if center_screen:
                y_base = (H - max_th) // 2
            elif _pos_y is not None:
                y_base = _pos_y - max_th   # top = bottom_anchor - height
            else:
                y_base = H - max_th - 50
            return ("word_hl", bright_arrs, dim_arrs, {
                "word_start_fs": word_start_fs,
                "x_positions":   x_positions,
                "word_heights":  [a.shape[0] for a in bright_arrs],
                "y_base":        y_base,
                "start_f":       start_f,
                "end_f":         end_f,
                "fade_frames":   fade_frames,
                "tw":            total_w,
                "th":            max_th,
                "x":             x_start,
                "y":             y_base,
                "pos_y":         _pos_y,
            })

        _color = _COLOR_BRIGHT if subtitle_anim == ANIM_WORD_HL else _COLOR_WHITE
        arr = _render_sub_rgba(ev["text"], font, color=_color)
        th, tw = arr.shape[:2]
        x  = (W - tw) // 2
        _pos_y = ev.get("pos_y")
        if center_screen:
            y = (H - th) // 2
        elif _pos_y is not None:
            y = _pos_y - th   # bottom anchor: top = bottom_anchor - height
        else:
            y = H - th - 50
        _anim = ANIM_FADE if subtitle_anim == ANIM_WORD_HL else subtitle_anim
        return ("std", arr, None, {
            "anim": _anim, "start_f": start_f, "end_f": end_f,
            "x": x, "y": y, "tw": tw, "th": th, "fade_frames": fade_frames,
            "pos_y": _pos_y,
        })

    # Параллельный CPU-рендер (I/O bound через PIL + numpy)
    cpu_workers = min(8, len(events)) if events else 1
    with concurrent.futures.ThreadPoolExecutor(max_workers=cpu_workers) as ex:
        cpu_results = list(ex.map(_render_event_cpu, events))

    # ── Глобальная нормализация высоты canvas ────────────────────────────────────
    # Проблема: каждая группа имеет свой max_th (высота canvas группы).
    # Апострофы D'/C', акценты É/È/Â, короткие символы ? дают разный max_th.
    # Разные группы → разный th → разный y → строки прыгают между группами.
    #
    # Решение: паддим ВСЕ canvases до global_max_th, ставим единый y_global.
    #
    # Стратегии паддинга:
    #   "std":      добавляем прозрачные строки СВЕРХУ → bottom-aligned к _bottom.
    #   "word_hl":  добавляем прозрачные строки СНИЗУ → top-aligned.
    #               В _render_sub_rgba glyph TOP всегда на row=pad+3 внутри canvas.
    #               _pad_bottom сохраняет этот offset → все слова на одной высоте.
    #               _pad_top (сверху) сдвигал glyph top по-разному → слова прыгали.
    def _pad_to(arr: np.ndarray, target_h: int) -> np.ndarray:
        """Pad at TOP (bottom-aligned) — для std субтитров."""
        h = arr.shape[0]
        if h >= target_h:
            return arr
        pad = np.zeros((target_h - h, arr.shape[1], 4), dtype=np.uint8)
        return np.vstack([pad, arr])

    def _pad_bottom_to(arr: np.ndarray, target_h: int) -> np.ndarray:
        """Pad at BOTTOM (top-aligned) — для word_hl слов, сохраняет glyph top."""
        h = arr.shape[0]
        if h >= target_h:
            return arr
        pad = np.zeros((target_h - h, arr.shape[1], 4), dtype=np.uint8)
        return np.vstack([arr, pad])

    if cpu_results:
        global_max_th = max(meta.get("th", 1) for _, _, _, meta in cpu_results)
        if center_screen:
            y_global = (H - global_max_th) // 2
        else:
            # Bottom anchor: берём pos_y из первого события с \pos тегом (все одинаковые)
            # pos_y = bottom-center позиция из ASS \pos(x,y); нет тега → fallback H-50
            _all_pos_y = [m.get("pos_y") for _, _, _, m in cpu_results if m.get("pos_y") is not None]
            _bottom = _all_pos_y[0] if _all_pos_y else (H - 50)
            y_global = _bottom - global_max_th
        print(f"[GPU] Y-normalize: global_max_th={global_max_th}px → y={y_global} "
              f"(bottom={y_global + global_max_th}) [anchor={'pos_y=' + str(_bottom) if not center_screen else 'center'}]")
    else:
        global_max_th = 1
        y_global      = H - 51

    # ── Шаг 2: batch VRAM upload + global padding ────────────────────────────
    # pin_memory() позволяет DMA-трансфер без CPU-посредника → быстрее.
    def _to_gpu(arr: np.ndarray) -> torch.Tensor:
        return torch.from_numpy(arr).pin_memory().to(DEVICE, non_blocking=True)

    rendered = []
    vram_mb  = 0.0

    for kind, a1, a2, meta in cpu_results:
        # Обновляем y и th на глобальные значения
        meta["y"]  = y_global
        meta["th"] = global_max_th

        if kind == "word_hl":
            bright_arrs = [_pad_bottom_to(a, global_max_th) for a in a1]
            dim_arrs    = [_pad_bottom_to(a, global_max_th) for a in a2]
            bright_tensors = [_to_gpu(a) for a in bright_arrs]
            dim_tensors    = [_to_gpu(a) for a in dim_arrs]
            for a in bright_arrs + dim_arrs:
                vram_mb += a.nbytes / 1024 / 1024
            rendered.append({
                "anim":            ANIM_WORD_HL,
                "bright_tensors":  bright_tensors,
                "dim_tensors":     dim_tensors,
                **meta,
            })
        else:
            arr = _pad_to(a1, global_max_th)
            vram_mb += arr.nbytes / 1024 / 1024
            rendered.append({"tensor": _to_gpu(arr), **meta})

    # Дождаться завершения всех non_blocking transfers перед рендером
    if DEVICE != "cpu":
        torch.cuda.synchronize()

    print(f"[GPU] Субтитры: {len(rendered)} строк, VRAM: {vram_mb:.0f}MB загружено в {DEVICE}")
    return rendered


# ═══════════════════════════════════════════════════════════════════════════════
# 3. GPU COMPOSITING
# ═══════════════════════════════════════════════════════════════════════════════

class SubtitleIndex:
    """
    Быстрый lookup активных субтитров по номеру кадра.
    O(log N + active_count) вместо O(N) на каждый кадр.
    """
    def __init__(self, events: list[dict]):
        # Сортируем по start_f
        self._ev = sorted(events, key=lambda e: e["start_f"])
        self._starts = [e["start_f"] for e in self._ev]
        self._ptr = 0  # первый кандидат (end_f ещё не прошёл)

    def get_active(self, frame_num: int) -> list[dict]:
        # Сдвигаем ptr мимо истёкших событий
        while self._ptr < len(self._ev) and \
              self._ev[self._ptr]["end_f"] <= frame_num:
            self._ptr += 1

        # Все события от ptr до первого с start_f > frame_num
        hi  = bisect.bisect_right(self._starts, frame_num)
        return [self._ev[i] for i in range(self._ptr, hi)
                if self._ev[i]["end_f"] > frame_num]


def _composite_one(frame: torch.Tensor,
                   tensor: torch.Tensor,
                   x: int, y: int,
                   alpha: float) -> torch.Tensor:
    """Вставляет pre-rendered RGBA тензор [H,W,4] uint8 на кадр с заданной alpha."""
    if alpha <= 0:
        return frame
    sub = tensor.float() / 255.0
    sh, sw = sub.shape[:2]
    x1 = max(x, 0);     y1 = max(y, 0)
    x2 = min(x + sw, W); y2 = min(y + sh, H)
    if x2 <= x1 or y2 <= y1:
        return frame
    sx1, sy1 = x1 - x, y1 - y
    sx2, sy2 = sx1 + (x2 - x1), sy1 + (y2 - y1)
    sub_rgb   = sub[sy1:sy2, sx1:sx2, :3]
    sub_alpha = sub[sy1:sy2, sx1:sx2, 3:4] * alpha
    frame[y1:y2, x1:x2] = (
        sub_rgb * sub_alpha + frame[y1:y2, x1:x2] * (1.0 - sub_alpha)
    )
    return frame


def composite_subtitles(frame: torch.Tensor,
                        frame_num: int,
                        idx: SubtitleIndex) -> torch.Tensor:
    """
    Накладывает активные субтитры на кадр с анимацией. Всё на GPU.
    frame: [H, W, 3] float32, значения 0-1
    """
    for ev in idx.get_active(frame_num):
        sf, ef   = ev["start_f"], ev["end_f"]
        ff       = ev["fade_frames"]
        t        = frame_num - sf
        dur      = ef - sf
        # Универсальный fade: 0→1 за ff кадров, 1→0 за ff кадров до конца
        fade     = min(t / ff, 1.0, (dur - t) / ff)
        if fade <= 0:
            continue

        anim = ev.get("anim", ANIM_FADE)

        # ── word_highlight ────────────────────────────────────────────────────
        if anim == ANIM_WORD_HL:
            word_start_fs = ev["word_start_fs"]
            # Определяем активное слово: последнее слово, чей start_f <= frame_num
            active = 0
            for i, wsf in enumerate(word_start_fs):
                if frame_num >= wsf:
                    active = i
            for i, (bt, dt, wx) in enumerate(zip(
                    ev["bright_tensors"], ev["dim_tensors"], ev["x_positions"])):
                # Bottom anchor: все слова группы выровнены по единой базе (max_th)
                wy = ev["y"]
                tensor = bt if i == active else dt
                frame = _composite_one(frame, tensor, wx, wy, fade)
            continue

        # ── Все остальные анимации используют общий тензор ───────────────────
        tensor = ev["tensor"]
        x_base = ev["x"]
        y_base = ev["y"]

        if anim == ANIM_FADE:
            frame = _composite_one(frame, tensor, x_base, y_base, fade)

        elif anim == ANIM_FADE_SCALE:
            # scale: 0.85 → 1.0 при fade-in, 1.0 → 0.85 при fade-out
            scale  = 0.85 + 0.15 * fade
            scaled = _scale_tensor(tensor, scale)
            sh, sw = scaled.shape[:2]
            dx = (ev["tw"] - sw) // 2
            dy = (ev["th"] - sh) // 2
            frame = _composite_one(frame, scaled, x_base + dx, y_base + dy, fade)

        elif anim == ANIM_SLIDE_UP:
            # y смещается вниз на _SLIDE_PX при fade=0, нет смещения при fade=1
            y_off = int(_SLIDE_PX * (1.0 - fade))
            frame = _composite_one(frame, tensor, x_base, y_base + y_off, fade)

        elif anim == ANIM_POP:
            # Быстрый bounce: за _POP_FRAMES кадров 0.8→1.1→1.0, затем hold
            if t < _POP_FRAMES:
                p = t / _POP_FRAMES
                # эластичный выход: overshoots до 1.1 на середине
                scale = 0.8 + 0.3 * p - 0.1 * math.sin(math.pi * p)
            else:
                scale = 1.0
            alpha_pop = min(fade, 1.0)
            scaled    = _scale_tensor(tensor, scale)
            sh, sw    = scaled.shape[:2]
            dx = (ev["tw"] - sw) // 2
            dy = (ev["th"] - sh) // 2
            frame = _composite_one(frame, scaled, x_base + dx, y_base + dy, alpha_pop)

    return frame


# ─── CTA overlay ──────────────────────────────────────────────────────────────

def prerender_cta(cta_path: Path, fps: float) -> dict | None:
    """Декодирует CTA .mov → numpy RGBA [N, H, W, 4] uint8 на CPU."""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,nb_frames,duration,r_frame_rate",
         "-of", "json", str(cta_path)],
        capture_output=True, text=True, stdin=subprocess.DEVNULL,
    )
    s = json.loads(r.stdout)["streams"][0]
    cw, ch = int(s["width"]), int(s["height"])
    dur = float(s.get("duration", 0) or 0)
    num, den = s.get("r_frame_rate", "25/1").split("/")
    cta_fps  = float(num) / float(den)
    n_frames = int(s.get("nb_frames", 0) or 0) or int(dur * cta_fps)

    dec = subprocess.Popen(
        ["ffmpeg", "-hide_banner", "-loglevel", "error",
         "-i", str(cta_path),
         "-f", "rawvideo", "-pix_fmt", "rgba", "pipe:1"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )
    frame_bytes = cw * ch * 4
    frames = []
    while True:
        raw = dec.stdout.read(frame_bytes)
        if len(raw) < frame_bytes:
            break
        frames.append(np.frombuffer(raw, dtype=np.uint8).reshape(ch, cw, 4).copy())
    dec.stdout.close()
    dec.wait()

    if not frames:
        return None
    return {
        "frames_np": np.stack(frames, axis=0),  # [N, H_cta, W_cta, 4]
        "n": len(frames),
        "dur": len(frames) / cta_fps,
        "fps": cta_fps,
        "w": cw, "h": ch,
    }


def composite_cta(
    frame:      torch.Tensor,
    frame_num:  int,
    fps:        float,
    cta_data:   dict,
    timestamps: list[float],
) -> torch.Tensor:
    """Накладывает CTA если frame_num попадает в одно из временных окон."""
    t = frame_num / fps
    frames_np = cta_data["frames_np"]
    n   = cta_data["n"]
    dur = cta_data["dur"]

    for ts in timestamps:
        if ts <= t < ts + dur:
            cta_f  = min(int((t - ts) * cta_data["fps"]), n - 1)
            tensor = torch.from_numpy(frames_np[cta_f]).to(DEVICE, non_blocking=False)
            return _composite_one(frame, tensor, 0, 0, 1.0)

    return frame


# ═══════════════════════════════════════════════════════════════════════════════
# 4. COLOR GRADE (GPU)
# ═══════════════════════════════════════════════════════════════════════════════

def make_vignette_mask(angle: float = math.pi / 6) -> torch.Tensor:
    """Предвычисляет маску виньетки [H, W, 1] на GPU."""
    ys = torch.linspace(-1.0, 1.0, H, device=DEVICE)
    xs = torch.linspace(-1.0, 1.0, W, device=DEVICE)
    yy, xx = torch.meshgrid(ys, xs, indexing="ij")
    r    = torch.sqrt(xx ** 2 + yy ** 2).clamp(0.0, 1.0)
    mask = torch.cos(r * angle) ** 4
    return mask.unsqueeze(-1)  # [H, W, 1]


_GRADE_PARAMS = {
    "cool_cinematic": dict(
        contrast=1.04, saturation=0.97, gamma=1.03, brightness=0.00,
        vignette=math.pi / 6,
        cb_s=(-0.03, -0.01,  0.04),
        cb_m=(-0.02,  0.00,  0.02),
        cb_h=(-0.03,  0.00,  0.03),
    ),
    "gold_dramatic": dict(
        contrast=1.12, saturation=0.82, gamma=0.94, brightness=0.01,
        vignette=math.pi / 4,
        cb_s=( 0.09,  0.03, -0.09),
        cb_m=( 0.05,  0.02, -0.05),
        cb_h=( 0.06,  0.02, -0.07),
    ),
    "warm_cinematic": dict(
        contrast=1.05, saturation=1.04, gamma=0.97, brightness=0.01,
        vignette=math.pi / 6,
        cb_s=( 0.04,  0.01, -0.03),
        cb_m=( 0.02,  0.01, -0.02),
        cb_h=( 0.03,  0.01, -0.04),
    ),
}


def color_grade_gpu(frame: torch.Tensor,
                    p: dict,
                    vignette_mask: torch.Tensor) -> torch.Tensor:
    """
    Цветокоррекция полностью на GPU.
    frame: [H, W, 3] float32 0-1 RGB
    """
    # 1. Контраст + яркость
    frame = (frame - 0.5) * p["contrast"] + 0.5 + p["brightness"]
    frame = frame.clamp(0.0, 1.0)

    # 2. Гамма
    frame = frame.pow(1.0 / p["gamma"])

    # 3. Насыщенность
    luma  = (0.299 * frame[..., 0] +
             0.587 * frame[..., 1] +
             0.114 * frame[..., 2]).unsqueeze(-1)
    frame = luma + p["saturation"] * (frame - luma)
    frame = frame.clamp(0.0, 1.0)

    # 4. Colorbalance (тени / полутона / света)
    cb_s = torch.tensor(p["cb_s"], device=DEVICE, dtype=torch.float32)
    cb_m = torch.tensor(p["cb_m"], device=DEVICE, dtype=torch.float32)
    cb_h = torch.tensor(p["cb_h"], device=DEVICE, dtype=torch.float32)

    shadows    = (1.0 - frame).pow(2)
    highlights = frame.pow(2)
    midtones   = (1.0 - shadows - highlights).clamp(0.0, 1.0)

    frame = frame + shadows * cb_s + midtones * cb_m + highlights * cb_h
    frame = frame.clamp(0.0, 1.0)

    # 5. Виньетка
    frame = (frame * vignette_mask).clamp(0.0, 1.0)

    return frame


# ═══════════════════════════════════════════════════════════════════════════════
# 5. FFMPEG HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _probe(path: Path) -> tuple[int, float, float]:
    """ffprobe → (total_frames, fps, duration)"""
    r = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=nb_frames,r_frame_rate,duration",
        "-of", "json", str(path)
    ], capture_output=True, text=True, check=True)
    s   = json.loads(r.stdout)["streams"][0]
    dur = float(s.get("duration", 0) or 0)
    num, den = s.get("r_frame_rate", "25/1").split("/")
    fps = float(num) / float(den)
    nb  = int(s.get("nb_frames", 0) or 0)
    return nb or int(dur * fps), fps, dur


def _open_decoder(path: Path) -> subprocess.Popen:
    """FFmpeg → rawvideo rgb24 stdout (software decode для frame-accurate output)"""
    return subprocess.Popen(
        ["ffmpeg", "-hide_banner", "-loglevel", "error",
         "-i", str(path),
         "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-vcodec", "rawvideo", "pipe:1"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )


def _open_encoder(output: Path, fps: float,
                  audio_path: Path | None,
                  intermediate: bool = False) -> subprocess.Popen:
    """rawvideo rgb24 stdin → h264_nvenc → output.mp4
    intermediate=True: промежуточный файл (будет перекодирован) → preset p1 для скорости.
    """
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{W}x{H}", "-r", str(fps), "-i", "pipe:0",
    ]
    if audio_path and Path(audio_path).exists():
        cmd += ["-i", str(audio_path), "-c:a", "aac", "-b:a", "192k", "-shortest"]
    preset = "p1" if intermediate else "p4"
    cmd += [
        "-c:v", "h264_nvenc", "-preset", preset,
        "-rc", "vbr", "-cq", "19", "-b:v", "20M", "-maxrate", "25M", "-bufsize", "40M",
        "-bf", "0", "-g", "50",          # нет B-фреймов → монотонный PTS, плавное воспроизведение
        "-pix_fmt", "yuv420p",
        str(output),
    ]
    return subprocess.Popen(cmd,
                            stdin=subprocess.PIPE,
                            stderr=subprocess.DEVNULL)


def _ffmpeg_merge_intro_clips(
    intro_path:  Path,
    clips_path:  Path,
    audio_path:  Path | None,
    trans_dur:   float,
    fps:         float,
    output:      Path,
) -> None:
    """
    Финальная сборка: интро (оригинальное качество) + GPU-клипы через FFmpeg xfade.
    YUV-путь — без промежуточной конвертации в RGB24.
    Интро не проходит через GPU pipeline → нет деградации качества.
    """
    _, _, intro_dur = _probe(intro_path)
    offset = max(0.0, intro_dur - trans_dur)

    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(intro_path),
        "-i", str(clips_path),
    ]
    if audio_path and Path(audio_path).exists():
        cmd += ["-i", str(audio_path)]

    vf = (
        f"[0:v]settb=1/25000,fps={fps}[v0];"
        f"[1:v]settb=1/25000,fps={fps}[v1];"
        f"[v0][v1]xfade=transition=fade:"
        f"duration={trans_dur:.3f}:offset={offset:.3f}[v]"
    )
    cmd += ["-filter_complex", vf, "-map", "[v]"]
    if audio_path and Path(audio_path).exists():
        cmd += ["-map", "2:a", "-c:a", "aac", "-b:a", "192k"]

    cmd += [
        "-c:v", "h264_nvenc", "-preset", "p4",
        "-rc", "vbr", "-cq", "15",
        "-b:v", "25M", "-maxrate", "40M", "-bufsize", "80M",
        "-bf", "0", "-g", "50",
        "-pix_fmt", "yuv420p",
        "-shortest",
        str(output),
    ]
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(
            f"[GPU] FFmpeg intro merge failed:\n{r.stderr.decode(errors='replace')}"
        )
    print(f"[GPU] Merge intro+clips (FFmpeg YUV): {time.time()-t0:.1f}s")


def _merge_intro_videotrack(intro: Path, videotrack: Path,
                             trans_dur: float, tmp: Path) -> Path:
    """
    Быстрый FFmpeg-проход: intro + videotrack → fade transition → tmp.mp4
    Без субтитров и цветокоррекции (они идут потом на GPU).
    """
    _, _, intro_dur = _probe(intro)
    offset = max(0.0, intro_dur - trans_dur)
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(intro), "-i", str(videotrack),
        "-filter_complex",
        f"[0:v]settb=1/25000,fps=25[v0];[1:v]settb=1/25000,fps=25[v1];"
        f"[v0][v1]xfade=transition=fade:duration={trans_dur:.3f}:offset={offset:.3f}[v]",
        "-map", "[v]",
        "-c:v", "h264_nvenc", "-preset", "p1",
        "-pix_fmt", "yuv420p", "-an",
        str(tmp),
    ]
    t0 = time.time()
    subprocess.run(cmd, check=True)
    print(f"[GPU] Merge intro+videotrack: {time.time()-t0:.1f}s")
    return tmp


# ═══════════════════════════════════════════════════════════════════════════════
# 6. SINGLE-PASS TIMELINE ITERATOR
# ═══════════════════════════════════════════════════════════════════════════════

def _iter_timeline_frames(tl: dict, skip_intro: bool = False):
    """
    Single-pass frame iterator, tpad-стиль (аналог concat_clips_xfade из transitions.py).

    skip_intro=True: пропустить интро полностью, начать сразу с клипов.
    Используется когда интро обрабатывается отдельно через FFmpeg (без деградации качества).

    Yields (raw_a: bytes, raw_b: bytes|None, alpha: float).
    raw_b=None и alpha=1.0 для чистых кадров без перехода.
    """
    fps             = float(tl["fps"])
    intro_path      = Path(tl["intro"]) if tl.get("intro") else None
    intro_trans_dur = float(tl.get("intro_trans_dur", 1.0))
    if skip_intro:
        intro_path = None
    clips           = tl["clips"]
    transitions     = tl["transitions"]
    n               = len(clips)
    _EMPTY          = b"\x00" * FRAME_BYTES

    def _pads(trans_f: int) -> tuple[int, int]:
        p = trans_f // 2
        return p, trans_f - p

    intro_trans_frames = round(intro_trans_dur * fps) if (intro_path and n > 0) else 0

    # ── Фаза 1: Интро ──────────────────────────────────────────────────────────
    if intro_path and intro_path.exists():
        _, _, intro_dur_s   = _probe(intro_path)
        intro_total_frames  = round(intro_dur_s * fps)
        i_pad_out, i_pad_in = _pads(intro_trans_frames)

        intro_dec    = _open_decoder(intro_path)
        last_intro_f = _EMPTY

        # Все кадры интро до blend zone (первые total - pad_out кадров)
        for _ in range(max(0, intro_total_frames - i_pad_out)):
            raw = intro_dec.stdout.read(FRAME_BYTES)
            if len(raw) < FRAME_BYTES:
                break
            last_intro_f = raw
            yield raw, None, 1.0

        if intro_trans_frames > 0 and n > 0:
            first_dec = _open_decoder(Path(clips[0]["path"]))

            # Читаем clips[0][0] для freeze в первой половине
            rb_frz = first_dec.stdout.read(FRAME_BYTES)
            if len(rb_frz) < FRAME_BYTES:
                rb_frz = _EMPTY

            # Первая половина: real intro tail + clip_0 frozen
            for f in range(i_pad_out):
                ra = intro_dec.stdout.read(FRAME_BYTES)
                if len(ra) == FRAME_BYTES:
                    last_intro_f = ra
                else:
                    ra = last_intro_f
                yield ra, rb_frz, (f + 1) / intro_trans_frames

            # Вторая половина: intro freeze + real clip_0 head
            rb_cur   = rb_frz
            consumed = 1                    # rb_frz уже прочитан
            for f in range(i_pad_in):
                if f > 0:
                    rb_new = first_dec.stdout.read(FRAME_BYTES)
                    if len(rb_new) == FRAME_BYTES:
                        rb_cur = rb_new
                        consumed += 1
                yield last_intro_f, rb_cur, (i_pad_out + f + 1) / intro_trans_frames

            intro_dec.stdout.close(); intro_dec.wait()
            current_dec   = first_dec
            current_frame = consumed        # = i_pad_in кадров прочитано из clip_0
        else:
            intro_dec.stdout.close(); intro_dec.wait()
            current_dec   = None
            current_frame = 0
    else:
        current_dec   = None
        current_frame = 0

    # ── Фаза 2: Клипы ──────────────────────────────────────────────────────────
    for ci in range(n):
        clip_frames       = round(float(clips[ci]["duration"]) * fps)
        trans_in_consumed = current_frame

        trans_out        = transitions[ci] if ci < n - 1 else None
        trans_out_frames = (round(float(trans_out["dur"]) * fps)
                            if (trans_out and trans_out["type"] != "cut") else 0)
        pad_out, pad_in  = _pads(trans_out_frames) if trans_out_frames > 0 else (0, 0)

        # Открываем декодер если нет (первый клип без интро или после cut)
        if current_dec is None:
            current_dec       = _open_decoder(Path(clips[ci]["path"]))
            current_frame     = 0
            trans_in_consumed = 0

        # Пропускаем уже прочитанные кадры из предыдущего blend (второй half)
        while current_frame < trans_in_consumed:
            current_dec.stdout.read(FRAME_BYTES)
            current_frame += 1

        last_frame = _EMPTY

        # Чистые кадры: от trans_in_consumed до clip_frames - pad_out
        pure_end = clip_frames - pad_out
        while current_frame < pure_end:
            raw = current_dec.stdout.read(FRAME_BYTES)
            if len(raw) < FRAME_BYTES:
                break
            last_frame = raw
            yield raw, None, 1.0
            current_frame += 1

        if trans_out_frames > 0 and ci < n - 1:
            next_dec = _open_decoder(Path(clips[ci + 1]["path"]))

            # Читаем clips[i+1][0] для freeze
            rb_frz = next_dec.stdout.read(FRAME_BYTES)
            if len(rb_frz) < FRAME_BYTES:
                rb_frz = _EMPTY

            # Первая половина blend: real clip_i tail + next clip frozen
            for f in range(pad_out):
                ra = current_dec.stdout.read(FRAME_BYTES)
                if len(ra) == FRAME_BYTES:
                    last_frame = ra
                else:
                    ra = last_frame
                current_frame += 1
                yield ra, rb_frz, (f + 1) / trans_out_frames

            # Вторая половина blend: clip_i freeze + real next clip head
            rb_cur   = rb_frz
            consumed = 1                    # rb_frz уже прочитан
            for f in range(pad_in):
                if f > 0:
                    rb_new = next_dec.stdout.read(FRAME_BYTES)
                    if len(rb_new) == FRAME_BYTES:
                        rb_cur = rb_new
                        consumed += 1
                yield last_frame, rb_cur, (pad_out + f + 1) / trans_out_frames

            current_dec.stdout.close(); current_dec.wait()
            current_dec   = next_dec
            current_frame = consumed        # = pad_in кадров прочитано из следующего клипа
        else:
            # Cut или последний клип — дочитываем оставшиеся
            while current_frame < clip_frames:
                raw = current_dec.stdout.read(FRAME_BYTES)
                if len(raw) < FRAME_BYTES:
                    break
                yield raw, None, 1.0
                current_frame += 1
            current_dec.stdout.close(); current_dec.wait()
            current_dec   = None
            current_frame = 0

    if current_dec is not None:
        current_dec.stdout.close(); current_dec.wait()


# ═══════════════════════════════════════════════════════════════════════════════
# 7. MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

def run(
    videotrack:       Path | None    = None,
    output:           Path           = None,
    intro:            Path | None    = None,
    audio:            Path | None    = None,
    ass_path:         Path | None    = None,
    color_grade_name: str            = "cool_cinematic",
    fps:              float          = 25.0,
    intro_trans_dur:  float          = 1.0,
    font_size:        int            = 46,
    fade_ms:          int            = 100,
    font_path:        str | None     = None,
    subtitle_style:   str            = "default",
    subtitle_anim:    str            = ANIM_SLIDE_UP,
    timeline:         Path | None    = None,  # single-pass режим
    cta_path:         Path | None    = None,  # CTA .mov (RGBA)
    cta_timestamps:   list[float] | None = None,  # абс. тайминги от начала видео
    cta_intro_dur:    float          = 0.0,   # длительность интро (для offset)
) -> bool:
    """
    GPU финальный рендер.

    videotrack:      готовый видеоряд (без интро)
    intro:           intro.mp4 (опционально)
    audio:           финальный аудиомикс
    ass_path:        .ass файл субтитров
    output:          выходной файл
    color_grade_name: 'cool_cinematic' | 'gold_dramatic' | 'warm_cinematic'
    fps:             кадровая частота
    intro_trans_dur: длительность перехода интро→видеоряд (сек)
    font_size:       размер шрифта субтитров
    fade_ms:         fade in/out субтитров в мс
    """
    t_total = time.time()
    gpu_name = torch.cuda.get_device_name(0) if DEVICE.type == "cuda" else "CPU"
    free_mb  = torch.cuda.mem_get_info()[0] // 1024 // 1024 if DEVICE.type == "cuda" else 0
    print(f"[GPU] Compositor старт | {gpu_name} | VRAM free: {free_mb}MB")

    # ── Получаем параметры цветокоррекции ───────────────────────────────────
    grade_p = _GRADE_PARAMS.get(color_grade_name, _GRADE_PARAMS["warm_cinematic"])

    # ── Предвычисляем GPU константы ─────────────────────────────────────────
    vignette = make_vignette_mask(grade_p["vignette"])

    # ── Субтитры: CPU рендер → GPU upload ────────────────────────────────────
    sub_events: list[dict] = []
    if ass_path and Path(ass_path).exists():
        t0 = time.time()
        raw_events = parse_ass(Path(ass_path))
        sub_events = prerender_subtitles(raw_events, font_size, fps, fade_ms,
                                         font_path=font_path,
                                         subtitle_style=subtitle_style,
                                         subtitle_anim=subtitle_anim)
        print(f"[GPU] Subtitle pre-render: {time.time()-t0:.1f}s "
              f"({len(sub_events)} строк)")

    # ── CTA: декодируем .mov → CPU numpy ─────────────────────────────────────
    _cta_data: dict | None = None
    _cta_ts:   list[float] = []
    if cta_path and cta_timestamps and Path(cta_path).exists():
        t0 = time.time()
        _cta_data = prerender_cta(Path(cta_path), fps)
        if _cta_data:
            # Переводим в content-relative тайминги (вычитаем длительность интро)
            _cta_ts = [max(0.0, ts - cta_intro_dur) for ts in cta_timestamps]
            print(f"[GPU] CTA pre-render: {_cta_data['n']} кадров "
                  f"({_cta_data['dur']:.1f}s), {len(_cta_ts)} плашек — "
                  f"{time.time()-t0:.1f}s")

    # ── Single-pass (timeline) или legacy (merged_tmp) ───────────────────────
    _frame_source:  object    = None   # generator для timeline-режима
    decoder:        object    = None   # FFmpeg subprocess для legacy-режима
    tmp_merged: Path | None   = None

    if timeline and Path(timeline).exists():
        # ── Single-pass: читаем клипы напрямую, без промежуточного encode ──
        with open(timeline, encoding="utf-8") as _f:
            tl_data = json.load(_f)
        fps_actual = float(tl_data["fps"])

        _intro_p = Path(tl_data["intro"]) if tl_data.get("intro") else None
        _has_intro = bool(_intro_p and _intro_p.exists())

        if _has_intro:
            intro_frames, _, intro_dur = _probe(_intro_p)
        else:
            intro_frames = 0
            intro_dur    = 0.0

        # Интро обрабатывается отдельно через FFmpeg (без деградации качества).
        # GPU pipeline рендерит ТОЛЬКО клипы.
        _skip_intro = _has_intro

        # tpad: total = только клипы (интро добавит FFmpeg-шаг)
        total_frames = 0
        for _c in tl_data["clips"]:
            total_frames += round(float(_c["duration"]) * fps_actual)
        if not _skip_intro:
            total_frames += intro_frames
        duration = total_frames / fps_actual

        _frame_source = _iter_timeline_frames(tl_data, skip_intro=_skip_intro)
        print(f"[GPU] Single-pass: {total_frames} кадров @ {fps_actual:.2f}fps "
              f"({duration:.1f}s) | intro={'FFmpeg YUV' if _skip_intro else str(intro_frames)+'f'}")
    else:
        # ── Legacy: merge_intro + decode merged_tmp ─────────────────────────
        if intro and Path(intro).exists():
            tmp_merged  = output.parent / "_gpu_merged_tmp.mp4"
            input_video = _merge_intro_videotrack(
                Path(intro), Path(videotrack), intro_trans_dur, tmp_merged
            )
            intro_frames, _, intro_dur = _probe(Path(intro))
        else:
            input_video  = Path(videotrack)
            intro_frames = 0
            intro_dur    = 0.0
        total_frames, fps_actual, duration = _probe(input_video)
        decoder = _open_decoder(input_video)
        print(f"[GPU] Legacy mode: {total_frames} кадров @ "
              f"{fps_actual:.2f}fps ({duration:.1f}s)")

    # Когда интро обрабатывается отдельно через FFmpeg:
    # GPU пишет только клипы во временный файл (без аудио — аудио добавит FFmpeg)
    _skip_intro = locals().get("_skip_intro", False)
    _gpu_clips_tmp: Path | None = None
    if _skip_intro:
        _gpu_clips_tmp = output.parent / f"_gpu_clips_{output.stem}.mp4"
        # промежуточный файл: будет перекодирован intro merge → p1 для скорости
        encoder = _open_encoder(_gpu_clips_tmp, fps_actual, audio_path=None, intermediate=True)
    else:
        encoder = _open_encoder(output, fps_actual, audio)

    # ── CUDA Streams для async pipeline ──────────────────────────────────────
    # Ping-pong: пока GPU обрабатывает кадр N, CPU читает кадр N+1
    h2d_stream   = torch.cuda.Stream()   # CPU → GPU upload
    comp_stream  = torch.cuda.Stream()   # GPU compute (grade + subs)
    d2h_stream   = torch.cuda.Stream()   # GPU → CPU download

    # Два pinned-буфера для ping-pong (нет копирования пока GPU занят)
    pin_in  = [torch.empty(H * W * 3, dtype=torch.uint8).pin_memory() for _ in range(2)]
    pin_out = [torch.empty(H * W * 3, dtype=torch.uint8).pin_memory() for _ in range(2)]
    gpu_buf = [torch.empty(H, W, 3, dtype=torch.float32, device=DEVICE)  for _ in range(2)]

    # Дополнительные буферы для blend-кадров переходов (single-pass)
    _pin_b = torch.empty(H * W * 3, dtype=torch.uint8).pin_memory()
    _gpu_b = torch.empty(H, W, 3,   dtype=torch.float32, device=DEVICE)

    # ── Subtitle frame offset ────────────────────────────────────────────────
    # ASS-тайминги content-relative: 0 = конец интро = начало контента.
    # generate_karaoke_ass / generate_ass сдвигают -intro_duration при генерации.
    # GPU compositor frame 0 = начало контента (_skip_intro=True) → коррекция НЕ нужна.
    # Фильтруем события с отрицательным или нулевым end_f (до начала контента).
    if sub_events:
        sub_events = [ev for ev in sub_events if ev.get("end_f", 0) > 0]

    # ── Subtitle fast index ───────────────────────────────────────────────────
    sub_idx = SubtitleIndex(sub_events) if sub_events else None

    # ── Reader thread: декодирует в очередь независимо от GPU ─────────────────
    # Всегда yields (raw_a: bytes, raw_b: bytes|None, alpha: float) или None
    READ_Q_SIZE = 16   # увеличен с 6: меньше голода GPU при дисковых паузах
    read_q: queue.Queue = queue.Queue(maxsize=READ_Q_SIZE)

    def _reader():
        if _frame_source is not None:
            # Single-pass: generator управляет декодерами сам
            for item in _frame_source:
                read_q.put(item)
            read_q.put(None)
        else:
            # Legacy: один декодер, оборачиваем в tuple
            while True:
                raw = decoder.stdout.read(FRAME_BYTES)
                if len(raw) < FRAME_BYTES:
                    read_q.put(None)
                    break
                read_q.put((raw, None, 1.0))

    reader_thread = threading.Thread(target=_reader, daemon=True)
    reader_thread.start()

    # ── Writer thread: пишет в encoder независимо от GPU ──────────────────────
    WRITE_Q_SIZE = 6
    write_q: queue.Queue = queue.Queue(maxsize=WRITE_Q_SIZE)

    def _writer():
        while True:
            data = write_q.get()
            if data is None:
                break
            try:
                encoder.stdin.write(data)
            except BrokenPipeError:
                break

    writer_thread = threading.Thread(target=_writer, daemon=True)
    writer_thread.start()

    # ── Основной цикл: GPU compute (перекрывается с I/O) ─────────────────────
    frame_num  = 0
    t_loop     = time.time()
    report_int = max(1, int(fps_actual * 15))

    print(f"[GPU] Рендер начат (streams + threads)...", flush=True)

    with torch.no_grad():
        slot = 0  # ping-pong: 0 или 1

        # Постоянный prefetch-поток: читает из read_q в prefetch_q.
        # Заменяет создание нового Thread на каждый кадр (~23k Thread объектов).
        PREFETCH_Q_SIZE = 2
        prefetch_q: queue.Queue = queue.Queue(maxsize=PREFETCH_Q_SIZE)

        def _prefetcher():
            while True:
                item = read_q.get()
                prefetch_q.put(item)
                if item is None:
                    break

        prefetch_thread = threading.Thread(target=_prefetcher, daemon=True)
        prefetch_thread.start()

        # Читаем первый элемент заранее
        item = prefetch_q.get()

        while item is not None:
            raw_a, raw_b, alpha_blend = item
            s = slot
            p = pin_in[s]
            g = gpu_buf[s]
            po = pin_out[s]

            # 1. CPU → pinned (быстро, без GIL)
            arr = np.frombuffer(raw_a, dtype=np.uint8)
            p.numpy()[:] = arr                        # zero-copy into pinned

            # 2. Pinned → GPU async (H2D stream)
            with torch.cuda.stream(h2d_stream):
                g.copy_(p.reshape(H, W, 3), non_blocking=True)

            # Ждём H2D перед compute
            comp_stream.wait_stream(h2d_stream)

            # 3. GPU compute (comp stream)
            with torch.cuda.stream(comp_stream):
                frame = g.float() / 255.0

                # Blend двух клипов при переходе (single-pass crossfade)
                if raw_b is not None:
                    _arr_b = np.frombuffer(raw_b, dtype=np.uint8)
                    _pin_b.numpy()[:] = _arr_b
                    _gpu_b.copy_(_pin_b.reshape(H, W, 3), non_blocking=False)
                    fb = _gpu_b.float() / 255.0
                    frame = frame * (1.0 - alpha_blend) + fb * alpha_blend

                # intro_frames=0 когда интро обрабатывается отдельно (FFmpeg YUV)
                if frame_num >= intro_frames:
                    frame = color_grade_gpu(frame, grade_p, vignette)
                if sub_idx is not None:
                    frame = composite_subtitles(frame, frame_num, sub_idx)
                if _cta_data is not None and _cta_ts:
                    frame = composite_cta(frame, frame_num, fps_actual, _cta_data, _cta_ts)
                result = (frame.clamp(0.0, 1.0) * 255.0).byte()

            # 4. GPU → pinned CPU (синхронно — CPU должен дождаться D2H перед чтением)
            # non_blocking=True создавал race condition: CPU читал pinned buffer
            # пока D2H копия ещё шла → битые кадры у всех клипов
            torch.cuda.current_stream().wait_stream(comp_stream)
            po.copy_(result.reshape(-1), non_blocking=False)

            # 5. Данные гарантированно готовы — отправляем в writer
            write_q.put(po.numpy().tobytes())

            frame_num += 1
            slot ^= 1  # flip ping-pong

            if frame_num % report_int == 0:
                elapsed = time.time() - t_loop
                fps_r   = frame_num / max(elapsed, 0.001)
                eta     = (total_frames - frame_num) / max(fps_r, 1.0)
                pct     = frame_num / max(total_frames, 1) * 100
                print(f"  [{pct:5.1f}%] кадр {frame_num}/{total_frames} | "
                      f"{fps_r:.0f} fps | ETA {eta:.0f}s", flush=True)

            item = prefetch_q.get()

    write_q.put(None)  # сигнал writer thread завершить работу

    # ── Завершение GPU encode ─────────────────────────────────────────────────
    if decoder is not None:
        decoder.stdout.close()
        decoder.wait()
    encoder.stdin.close()
    encoder.wait()

    if tmp_merged and tmp_merged.exists():
        tmp_merged.unlink()

    # ── FFmpeg merge: интро (оригинал) + GPU-клипы ───────────────────────────
    if _gpu_clips_tmp and _gpu_clips_tmp.exists():
        print(f"[GPU] Клипы готовы ({_gpu_clips_tmp.stat().st_size//1024//1024}MB)"
              f" → объединяем с интро через FFmpeg YUV...")
        try:
            _ffmpeg_merge_intro_clips(
                intro_path = _intro_p,
                clips_path = _gpu_clips_tmp,
                audio_path = Path(audio) if audio else None,
                trans_dur  = float(tl_data.get("intro_trans_dur", 1.0)),
                fps        = fps_actual,
                output     = output,
            )
        finally:
            _gpu_clips_tmp.unlink(missing_ok=True)

    elapsed = time.time() - t_total
    x_rt    = duration / max(elapsed, 0.001)
    ok      = output.exists() and output.stat().st_size > 100_000

    if ok:
        size_mb = output.stat().st_size / 1024 / 1024
        print(f"[GPU] ✅ Готово: {elapsed:.0f}s для {duration:.1f}s видео "
              f"({x_rt:.1f}x реалтайм) | {size_mb:.0f}MB")
    else:
        print(f"[GPU] ❌ Ошибка: выходной файл не создан")

    return ok


# ─── CLI ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="GPU compositor: субтитры + цвет на CUDA")
    ap.add_argument("--video",    required=True,  help="videotrack.mp4")
    ap.add_argument("--output",   required=True,  help="output.mp4")
    ap.add_argument("--intro",    default=None,   help="intro.mp4")
    ap.add_argument("--audio",    default=None,   help="audio.m4a")
    ap.add_argument("--ass",      default=None,   help="subtitles.ass")
    ap.add_argument("--color",    default="cool_cinematic")
    ap.add_argument("--fps",      type=float, default=25.0)
    ap.add_argument("--font-size",type=int,   default=46)
    ap.add_argument("--fade-ms",  type=int,   default=100)
    ap.add_argument("--intro-trans", type=float, default=1.0,
                    help="Длительность перехода intro→videotrack (сек)")
    args = ap.parse_args()

    ok = run(
        videotrack       = Path(args.video),
        output           = Path(args.output),
        intro            = Path(args.intro) if args.intro else None,
        audio            = Path(args.audio) if args.audio else None,
        ass_path         = Path(args.ass)   if args.ass   else None,
        color_grade_name = args.color,
        fps              = args.fps,
        intro_trans_dur  = args.intro_trans,
        font_size        = args.font_size,
        fade_ms          = args.fade_ms,
    )
    sys.exit(0 if ok else 1)
