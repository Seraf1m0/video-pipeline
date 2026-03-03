# Media Generator Agent

Генерирует фото и видео через три платформы.

## Структура

| Файл | Описание |
|------|----------|
| `media_generator.py` | Роутер (argparse + dispatch) |
| `utils.py` | Общие хелперы (Chrome, куки, промпты, TG) |
| `pixel_agent.py` | PixelAgent API (asyncio + aiohttp) |
| `grok_agent.py` | Grok image-to-video (Playwright) |
| `flow_agent.py` | Google Flow (Playwright) |

## Платформы

| # | Платформа | Тип | Поддержка |
|---|-----------|-----|-----------|
| 1 | Google Flow | браузер | фото + видео |
| 2 | Grok | браузер | видео (SuperGrok) |
| 3 | PixelAgent | API | фото |

## Запуск

```bash
py agents/media_generator/media_generator.py
py agents/media_generator/media_generator.py --platform 3 --type photo
py agents/media_generator/media_generator.py --platform 2 --type video --session Video_xxx
```

## Зависимости

```bash
pip install aiohttp requests Pillow playwright python-dotenv
playwright install chromium
```

## Конфиг

`config/.env`:
```
PIXEL_API_KEY=...
PIXEL_API_URL=https://voiceapi.csv666.ru
PIXEL_MAX_CONCURRENT=5
CHROME_PATH=C:\Program Files\Google\Chrome\Application\chrome.exe
CHROME_CDP_PORT=9222
```
