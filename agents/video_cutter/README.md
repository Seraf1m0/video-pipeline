# Video Cutter + Upscaler Agent

Нарезает исходное видео по сегментам из `result.json` и (опционально) апскейлит клипы.

## Запуск

```bash
py agents/video_cutter/video_cutter.py
py agents/video_cutter/video_cutter.py --mode cut
py agents/video_cutter/video_cutter.py --mode cut+upscale --method lanczos --resolution 1080
py agents/video_cutter/video_cutter.py --project Video_20260227_220628 --source C:/video.mp4
```

## Методы апскейла

| Метод | Описание |
|-------|----------|
| `lanczos` | FFmpeg Lanczos (быстро) |
| `bicubic` | FFmpeg Bicubic |
| `realesrgan` | Real-ESRGAN AI (GPU, лучшее качество) |

## Зависимости

```bash
# ffmpeg (обязательно)
winget install Gyan.FFmpeg

# Real-ESRGAN (опционально)
pip install realesrgan basicsr
```

## Выходные данные

- `data/media/{session}/clips/` — нарезанные клипы
- `data/media/{session}/upscaled/` — апскейленные клипы
