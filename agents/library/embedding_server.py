"""
Embedding server — загружает multilingual-e5-large один раз,
принимает HTTP запросы от clip_selector.py.

Запуск: py agents/library/embedding_server.py
Порт:  8765 (по умолчанию)
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PORT = 8765
MODEL_NAME = "intfloat/multilingual-e5-large"

app = FastAPI(title="Embedding Server")
_model: SentenceTransformer | None = None


def get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        print(f"📦 Загружаем {MODEL_NAME}...", flush=True)
        _model = SentenceTransformer(MODEL_NAME, model_kwargs={"torch_dtype": "float16"})
        print("✅ Модель загружена, сервер готов", flush=True)
    return _model


class EncodeRequest(BaseModel):
    texts: list[str]
    mode: str = "query"  # "query" или "passage"


class EncodeResponse(BaseModel):
    embeddings: list[list[float]]


@app.on_event("startup")
async def startup():
    get_model()


@app.post("/encode", response_model=EncodeResponse)
def encode(req: EncodeRequest):
    model = get_model()
    prefix = "query: " if req.mode == "query" else "passage: "
    prefixed = [f"{prefix}{t}" for t in req.texts]
    vecs = model.encode(prefixed, normalize_embeddings=True, batch_size=64)
    return EncodeResponse(embeddings=vecs.tolist())


@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL_NAME, "loaded": _model is not None}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()
    print(f"🚀 Запуск embedding server на порту {args.port}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
