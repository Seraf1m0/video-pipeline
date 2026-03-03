# Prompt Generator Agent

Генерирует фото- и видео-промпты через Claude CLI батчами (5 параллельных потоков).

## Запуск

```bash
py agents/prompt_generator/prompt_generator.py
py agents/prompt_generator/prompt_generator.py --type photo --photo-master photo_master_prompt.txt
py agents/prompt_generator/prompt_generator.py --type video --video-master master_video_grok.txt --video-platform grok
py agents/prompt_generator/prompt_generator.py --type both  --photo-master photo_master_prompt.txt --video-master master_video_veo3.txt
```

## Зависимости

Требует Claude CLI (`claude.exe`) в `%APPDATA%/Claude/claude-code/`.
Нет pip-зависимостей.

## Выходные данные

- `data/prompts/{session}/photo_prompts.txt`
- `data/prompts/{session}/video_prompts.txt`
