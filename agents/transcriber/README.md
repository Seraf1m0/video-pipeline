# Transcriber Agent

Транскрибирует MP3 через Whisper и нарезает на сегменты 3–8 сек.

## Запуск

```bash
py agents/transcriber/transcriber.py
py agents/transcriber/transcriber.py --mode random
py agents/transcriber/transcriber.py --mode sequential --input data/input/file.mp3
```

## Зависимости

```bash
pip install torch openai-whisper
```

GPU (RTX 3060+): использует `large-v2` модель (~10x быстрее CPU).
CPU fallback: `base` модель.

## Выходные данные

`data/transcripts/{session}/result.json` — массив сегментов с `start`, `end`, `text`.
