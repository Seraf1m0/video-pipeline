"""
Vision Server — Qwen2.5-VL-7B-Instruct (NF4)
- 2 кадра на клип: первый + последний
- Батч 4 клипа за 1 inference (8 кадров)
- Размер кадра 336px
- MAX_NEW_TOK = 50

Запуск:
  py -X utf8 agents/vision/vision_server.py --port 8765
  py -X utf8 agents/vision/vision_server.py --port 8766
"""

import argparse
import sys
import time
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image
import torch
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from transformers import (
    Qwen2_5_VLForConditionalGeneration,
    AutoProcessor,
    BitsAndBytesConfig,
)
from qwen_vl_utils import process_vision_info

# ── Константы ──────────────────────────────────────────────────────────────

FRAME_SIZE   = 336
MAX_NEW_TOK  = 80    # увеличено для religion (детальные описания Jesus Christ, biblical scenes)
BATCH_TOK    = 160
BATCH4_TOK   = 320
BLACK_THRESH = 5.0   # mean порог (космос имеет mean 3-5 но max 255)
BLACK_MAX    = 30    # если max пикселя > этого — точно не чёрный экран
FROZEN_DIFF  = 0.5   # нижний порог для тёмного космоса (медленный зум diff ~1.0-1.6)
_MAX_PIXELS  = FRAME_SIZE * FRAME_SIZE  # ~113k px


PROMPTS = {
    "cosmos": (
        "Identify the main subject shown. "
        "Describe in max 50 characters. "
        "Name exact objects: telescope, "
        "planet, galaxy, spacecraft."
    ),
    "religion": (
        "Christian religion footage. "
        "Identify exactly what is shown in max 60 characters. "
        "Name specific Christian elements: Jesus Christ, cross, crucifix, "
        "church, cathedral, altar, Bible, prayer, baptism, crucifixion, "
        "resurrection, Last Supper, disciples, apostles, Virgin Mary, "
        "angels, Holy Spirit, pilgrims, stained glass, candles, rosary, "
        "sermon, icon, fresco, baptismal font, dove, nativity, Golgotha."
    ),
}

_active_niche = "cosmos"  # переопределяется через --niche


def make_batch_prompt(n: int) -> str:
    prompt = PROMPTS.get(_active_niche, PROMPTS["cosmos"])
    lines  = "\n".join(f"Clip{i}: description" for i in range(1, n + 1))
    return f"{prompt}\n\nAnswer for each clip:\n{lines}"

# ── Глобальные переменные (заполняются при старте) ─────────────────────────

_model     = None
_processor = None
_port      = 8765

# ── Статистика скорости ────────────────────────────────────────────────────

_stats = {
    "batches":    0,
    "clips":      0,
    "total_time": 0.0,
    "start_time": None,
}


def _log(msg: str):
    print(f"[:{_port}] {msg}", flush=True)


# ── Извлечение кадров ──────────────────────────────────────────────────────

def extract_frames(video_path: str) -> list[Image.Image]:
    """
    2 кадра: первый (0.5s) и последний (4.5s).
    Размер: 336×336px.
    """
    vf = (
        f"scale={FRAME_SIZE}:{FRAME_SIZE}:"
        f"force_original_aspect_ratio=decrease,"
        f"pad={FRAME_SIZE}:{FRAME_SIZE}:(ow-iw)/2:(oh-ih)/2"
    )
    frames = []
    with tempfile.TemporaryDirectory() as tmp:
        for i, ss in enumerate([0.5, 4.5]):
            fp = f"{tmp}/frame_{i}.jpg"
            subprocess.run([
                "ffmpeg", "-y",
                "-ss", str(ss),
                "-i", str(video_path),
                "-vframes", "1",
                "-vf", vf,
                "-q:v", "2",
                "-loglevel", "error",
                fp,
            ], capture_output=True)
            if Path(fp).exists():
                try:
                    frames.append(Image.open(fp).convert("RGB"))
                except Exception:
                    pass
    return frames


def check_black(frame: Image.Image) -> bool:
    arr      = np.array(frame).astype(float)
    mean_val = arr.mean()
    max_val  = arr.max()
    # Истинно чёрный = И среднее низкое, И максимум низкий
    # (космос: mean~1-3 но max~130-220 из-за звёзд → не чёрный)
    return mean_val < BLACK_THRESH and max_val < BLACK_MAX


def check_frozen(frames: list[Image.Image]) -> bool:
    if len(frames) < 2:
        return False
    diff = float(abs(
        np.array(frames[0]).astype(float) -
        np.array(frames[-1]).astype(float)
    ).mean())
    return diff < FROZEN_DIFF


# ── Inference ──────────────────────────────────────────────────────────────

def _infer(messages: list, max_new_tokens: int) -> str:
    text = _processor.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True,
    )
    image_inputs, video_inputs = process_vision_info(messages)
    inputs = _processor(
        text=[text],
        images=image_inputs,
        videos=video_inputs,
        padding=True,
        return_tensors="pt",
    ).to(_model.device)

    with torch.no_grad():
        output = _model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            do_sample=False,
        )
    generated = output[0][inputs.input_ids.shape[1]:]
    result = _processor.decode(generated, skip_special_tokens=True).strip()
    torch.cuda.empty_cache()
    return result


def _frames_to_content(frames: list[Image.Image]) -> list:
    return [{"type": "image", "image": img} for img in frames]


def _parse_batch_result(result: str, n: int) -> list[str]:
    results = [""] * n
    for line in result.split("\n"):
        stripped = line.strip()
        low = stripped.lower().replace(" ", "")
        for i in range(1, n + 1):
            if low.startswith(f"clip{i}:"):
                results[i - 1] = stripped.split(":", 1)[-1].strip()
                break
    # Fallback: непустые строки по порядку
    if not any(results):
        lines = [l.strip() for l in result.split("\n") if l.strip()]
        for i, line in enumerate(lines[:n]):
            results[i] = line
    return results


def analyze_single(frames: list[Image.Image]) -> str:
    messages = [{"role": "user", "content": [
        *_frames_to_content(frames),
        {"type": "text", "text": make_batch_prompt(1)},
    ]}]
    result = _infer(messages, MAX_NEW_TOK)
    parsed = _parse_batch_result(result, 1)
    return parsed[0] if parsed[0] else result


def analyze_batch_2(frames1: list[Image.Image], frames2: list[Image.Image]) -> tuple[str, str]:
    messages = [{"role": "user", "content": [
        {"type": "text", "text": "Clip 1:"},
        *_frames_to_content(frames1),
        {"type": "text", "text": "Clip 2:"},
        *_frames_to_content(frames2),
        {"type": "text", "text": make_batch_prompt(2)},
    ]}]
    result = _infer(messages, BATCH_TOK)
    parsed = _parse_batch_result(result, 2)
    return parsed[0], parsed[1]


def analyze_batch_4(frames_list: list[list[Image.Image]]) -> list[str]:
    """N клипов за 1 inference (до 8 кадров)."""
    n = len(frames_list)
    content = []
    for i, frames in enumerate(frames_list, 1):
        content.append({"type": "text", "text": f"Clip {i}:"})
        content.extend(_frames_to_content(frames))
    content.append({"type": "text", "text": make_batch_prompt(n)})
    messages = [{"role": "user", "content": content}]
    tok = MAX_NEW_TOK * n + 10
    result = _infer(messages, tok)
    return _parse_batch_result(result, n)


def _update_stats(elapsed: float, n_clips: int):
    _stats["batches"]    += 1
    _stats["clips"]      += n_clips
    _stats["total_time"] += elapsed
    avg = _stats["total_time"] / _stats["clips"] if _stats["clips"] else 0
    rate = 3600 / avg if avg > 0 else 0
    _log(
        f"batch #{_stats['batches']}  "
        f"{elapsed:.1f}s  "
        f"avg {avg:.1f}s/clip  "
        f"~{rate:.0f} clips/hr  "
        f"total: {_stats['clips']} clips"
    )


# ── FastAPI ────────────────────────────────────────────────────────────────

def create_app(port: int) -> FastAPI:
    global _port
    _port = port
    app = FastAPI(title=f"Vision 7B :{port}")

    @app.on_event("startup")
    async def load_model():
        global _model, _processor
        _stats["start_time"] = time.time()
        _log("Loading Qwen2.5-VL-7B (NF4)...")
        bnb = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_compute_dtype=torch.float16,
            bnb_4bit_use_double_quant=True,
            bnb_4bit_quant_type="nf4",
        )
        _processor = AutoProcessor.from_pretrained(
            "Qwen/Qwen2.5-VL-7B-Instruct",
            use_fast=True,
            min_pixels=64 * 28 * 28,
            max_pixels=_MAX_PIXELS,
        )
        _model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
            "Qwen/Qwen2.5-VL-7B-Instruct",
            quantization_config=bnb,
            device_map="auto",
            torch_dtype=torch.float16,
            trust_remote_code=True,
        )
        vram = torch.cuda.memory_allocated() / 1024**3
        _log(f"Ready | VRAM {vram:.1f} GB")

    class AnalyzeRequest(BaseModel):
        video_path:   str
        segment_text: str = ""

    class BatchRequest(BaseModel):
        video_path1: str
        video_path2: str
        video_path3: str = ""
        video_path4: str = ""
        niche:       str = "cosmos"

    @app.post("/analyze")
    async def analyze(req: AnalyzeRequest):
        path = Path(req.video_path)
        if not path.exists():
            raise HTTPException(404, f"Not found: {req.video_path}")

        t = time.time()
        frames = extract_frames(req.video_path)
        if not frames:
            raise HTTPException(500, "No frames extracted")

        black  = check_black(frames[0])
        frozen = check_frozen(frames)

        if black or frozen:
            return {"valid": False, "black_screen": black, "frozen": frozen, "keywords": ""}

        keywords = analyze_single(frames)
        _update_stats(time.time() - t, 1)
        return {"valid": True, "black_screen": False, "frozen": False, "keywords": keywords}

    @app.post("/analyze_batch")
    async def analyze_batch(req: BatchRequest):
        global _active_niche
        _active_niche = req.niche if req.niche in PROMPTS else "cosmos"

        paths = [req.video_path1, req.video_path2]
        if req.video_path3:
            paths.append(req.video_path3)
        if req.video_path4:
            paths.append(req.video_path4)

        t            = time.time()
        clip_results = {}
        all_frames   = []   # (orig_idx, key, frames)

        for i, path in enumerate(paths):
            key = f"clip{i+1}"
            if not path or not Path(path).exists():
                clip_results[key] = {"valid": False, "reason": "not found",    "keywords": ""}
                continue
            frames = extract_frames(path)
            if not frames:
                clip_results[key] = {"valid": False, "reason": "no frames",    "keywords": ""}
                continue
            if check_black(frames[0]):
                clip_results[key] = {"valid": False, "reason": "black_screen", "keywords": ""}
                continue
            if check_frozen(frames):
                clip_results[key] = {"valid": False, "reason": "frozen",       "keywords": ""}
                continue
            all_frames.append((i, key, frames))

        if not all_frames:
            return clip_results

        # Один inference для всех валидных клипов
        content = []
        for idx, (i, key, frames) in enumerate(all_frames):
            content.append({"type": "text", "text": f"Clip {idx+1}:"})
            content.extend(_frames_to_content(frames))
        prompt = PROMPTS.get(_active_niche, PROMPTS["cosmos"])
        content.append({"type": "text", "text": (
            f"{prompt}\n\nAnswer for each clip:\n"
            + "\n".join(f"Clip{idx+1}: description" for idx in range(len(all_frames)))
        )})

        messages = [{"role": "user", "content": content}]
        response = _infer(messages, MAX_NEW_TOK * len(all_frames))

        for idx, (i, key, frames) in enumerate(all_frames):
            prefix = f"Clip{idx+1}:"
            desc   = ""
            for line in response.split("\n"):
                line = line.strip()
                if line.lower().startswith(prefix.lower()):
                    desc = line[len(prefix):].strip()
                    break
            clip_results[key] = {
                "valid":    bool(desc),
                "reason":   "ok" if desc else "no description",
                "keywords": desc,
            }

        if all_frames:
            _update_stats(time.time() - t, len(all_frames))

        return clip_results

    class Batch4FramesRequest(BaseModel):
        clips: list[list[str]]  # [[frame1, frame2], ...] — пути к уже извлечённым кадрам

    @app.post("/analyze_batch4_frames")
    async def analyze_batch4_frames(req: Batch4FramesRequest):
        clips = req.clips[:4]
        t = time.time()

        frames_list = []
        for frame_paths in clips:
            frames = []
            for fp in frame_paths:
                try:
                    frames.append(Image.open(fp).convert("RGB"))
                except Exception:
                    pass
            frames_list.append(frames)

        valids = [bool(f) and not check_black(f[0]) and not check_frozen(f) for f in frames_list]
        valid_indices = [i for i, v in enumerate(valids) if v]

        keywords_list = [""] * len(clips)
        if len(valid_indices) >= 2:
            valid_frames = [frames_list[i] for i in valid_indices]
            kws = analyze_batch_4(valid_frames)
            for idx, kw in zip(valid_indices, kws):
                keywords_list[idx] = kw
        elif len(valid_indices) == 1:
            i = valid_indices[0]
            keywords_list[i] = analyze_single(frames_list[i])

        n = len(valid_indices)
        if n:
            _update_stats(time.time() - t, n)

        return {
            f"clip{i+1}": {"keywords": keywords_list[i], "valid": valids[i]}
            for i in range(len(clips))
        }

    class Batch4Request(BaseModel):
        video_paths: list[str]  # 2–4 пути

    @app.post("/analyze_batch4")
    async def analyze_batch4(req: Batch4Request):
        paths = [Path(p) for p in req.video_paths[:4]]
        for p in paths:
            if not p.exists():
                raise HTTPException(404, f"Not found: {p}")

        t = time.time()
        frames_list = [extract_frames(str(p)) for p in paths]

        valids = [bool(f) and not check_black(f[0]) and not check_frozen(f) for f in frames_list]
        valid_indices = [i for i, v in enumerate(valids) if v]

        keywords_list = [""] * len(paths)
        if len(valid_indices) >= 2:
            valid_frames = [frames_list[i] for i in valid_indices]
            kws = analyze_batch_4(valid_frames)
            for idx, kw in zip(valid_indices, kws):
                keywords_list[idx] = kw
        elif len(valid_indices) == 1:
            i = valid_indices[0]
            keywords_list[i] = analyze_single(frames_list[i])

        n = len(valid_indices)
        if n:
            _update_stats(time.time() - t, n)

        return {
            f"clip{i+1}": {
                "keywords": keywords_list[i],
                "valid": valids[i],
            }
            for i in range(len(paths))
        }

    @app.get("/health")
    async def health():
        avg = _stats["total_time"] / _stats["clips"] if _stats["clips"] else 0
        return {
            "status":         "ok",
            "model":          "Qwen2.5-VL-7B-Instruct",
            "model_loaded":   _model is not None,
            "batch_size":     4,
            "frame_size":     "336px",
            "max_new_tokens": MAX_NEW_TOK,
            "port":           port,
            "clips_done":     _stats["clips"],
            "avg_s_per_clip": round(avg, 2),
        }

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--niche", default="cosmos",
                        choices=list(PROMPTS.keys()),
                        help="Ниша библиотеки (определяет промпт тэггера)")
    args = parser.parse_args()

    _active_niche = args.niche
    print(f"[vision] Ниша: {_active_niche}", flush=True)

    app = create_app(args.port)
    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="warning")
