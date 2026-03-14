"""
BLIP Server — запускается один раз, держит модель в памяти.
Принимает POST /analyze с {image_path, questions: [...]}
Возвращает {answers: [...]}

Запуск: py agents/video_validator/blip_server.py
"""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from io import BytesIO

PORT = 5679

_processor = None
_model     = None
_lock      = threading.Lock()

def load_model():
    global _processor, _model
    if _processor is not None:
        return
    print("[BLIP-server] Загружаю модель...", flush=True)
    from transformers import BlipProcessor, BlipForQuestionAnswering
    import torch
    _processor = BlipProcessor.from_pretrained("Salesforce/blip-vqa-base")
    _model     = BlipForQuestionAnswering.from_pretrained(
        "Salesforce/blip-vqa-base",
        torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
    )
    if torch.cuda.is_available():
        _model = _model.cuda()
    _model.eval()
    print("[BLIP-server] Модель готова!", flush=True)


def answer_questions(image_path: str, questions: list[str]) -> list[str]:
    from PIL import Image
    import torch

    img = Image.open(image_path).convert("RGB")
    answers = []
    with _lock:
        for q in questions:
            inputs = _processor(img, q, return_tensors="pt")
            if torch.cuda.is_available():
                inputs = {k: v.cuda() for k, v in inputs.items()}
            with torch.no_grad():
                out = _model.generate(**inputs, max_new_tokens=10)
            ans = _processor.decode(out[0], skip_special_tokens=True).strip().lower()
            answers.append(ans)
    return answers


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # тихий режим

    def do_GET(self):
        if self.path == "/ping":
            self._respond(200, b"pong")
        else:
            self._respond(404, b"not found")

    def do_POST(self):
        if self.path != "/analyze":
            self._respond(404, b"not found")
            return
        length = int(self.headers.get("Content-Length", 0))
        body   = json.loads(self.rfile.read(length))
        image_path = body.get("image_path", "")
        questions  = body.get("questions", [])
        try:
            answers = answer_questions(image_path, questions)
            self._respond(200, json.dumps({"answers": answers}).encode())
        except Exception as e:
            self._respond(500, json.dumps({"error": str(e)}).encode())

    def _respond(self, code, data: bytes):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    load_model()
    server = HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"[BLIP-server] Слушаю на порту {PORT} ...", flush=True)
    server.serve_forever()
