# Validator Agent

Проверяет целостность пайплайна: транскрипцию, промпты, медиафайлы.

## Запуск

```bash
py agents/validator/validator.py
py agents/validator/validator.py --project Video_20260227_220628
py agents/validator/validator.py --fix
```

## Проверки

1. Транскрипция — порядок сегментов, покрытие MP3
2. Фото промпты — наличие и корректность `photo_prompts.txt`
3. Видео промпты — наличие и корректность `video_prompts.txt`
4. Медиафайлы — наличие `photo_*.png` и `video_*.mp4`

## Зависимости

ffmpeg (winget): `winget install Gyan.FFmpeg`
