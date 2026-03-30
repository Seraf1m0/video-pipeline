# PIPELINE AUDIT — Video Pipeline Project
> Дата аудита: 2026-03-18
> Ветка: `refactor/agent-subfolders`
> Аудитор: Claude Sonnet 4.6

---

## 1. СТРУКТУРА ПРОЕКТА

```
Video-pipeline/
├── agents/
│   ├── assembler/          ✅ Монтаж финального видео (FFmpeg)
│   ├── blip_validator/     ✅ BLIP-2 FastAPI-сервер (порт 8765)
│   ├── media_generator/    ✅ Генерация фото/видео (Pixel/Grok/Flow)
│   ├── prompt_generator/   ✅ Генерация промптов через Claude CLI
│   ├── regen_agent/        ⚠️ Регенерация плохих фото (использует blip_report.json — устарело)
│   ├── transcriber/        ✅ Транскрипция MP3 через Whisper
│   ├── upscaler/           ⚠️ Апскейл фото (используется редко, video_cutter делает то же)
│   ├── utils/              ✅ Централизованные пути (paths.py)
│   ├── validator/          ✅ Проверка целостности пайплайна (4 критерия)
│   ├── video_cutter/       ✅ Нарезка + нормализация + апскейл видео
│   └── video_validator/    ✅ Валидация видео через BLIP+Claude (порт 5679)
├── bot/
│   ├── channels/
│   │   ├── channel_001_cosmos_de/config.json   ✅ Канал DE
│   │   └── channel_002_cosmos_fr/config.json   ✅ Канал FR
│   ├── active_channel.json     ✅ Текущий активный канал
│   ├── channel_manager.py      ✅ Управление каналами
│   ├── telegram_bot.py         ✅ Telegram Control Panel
│   ├── logs/bot.log
│   └── README.md
├── config/
│   ├── .env                    ✅ Все переменные окружения
│   ├── bot_config.py           ⚠️ Старый конфиг (заменён .env)
│   ├── grok_cookies.json       ✅ Куки Grok (авторизация)
│   └── master_prompts/
│       ├── photo/
│       │   ├── photo_master_prompt.txt        (базовый)
│       │   ├── photo_master_prompt_de.txt     (DE канал)
│       │   └── photo_master_prompt_fr.txt     (FR канал)
│       └── video/
│           ├── master_video_grok.txt          (DE канал)
│           ├── master_video_grok_fr.txt       (FR канал)
│           └── master_video_veo3.txt          (не используется)
├── data/
│   ├── channels/
│   │   ├── de/                 ✅ Данные канала DE (media, prompts, transcripts, input)
│   │   └── fr/                 ✅ Данные канала FR
│   ├── input/                  ⚠️ Старые входные MP3 (корень, не каналы — устаревшая структура)
│   ├── media/                  ⚠️ Старые медиафайлы (не привязаны к каналу — устаревшая структура)
│   └── music/                  (фоновая музыка для монтажа)
├── temp/                       Runtime-файлы прогресса, скриншоты Grok, BLIP-кадры
├── logs/
│   ├── bot.log
│   └── pipeline_72.log
├── pipeline.py                 ⚠️ Stub — запускает только транскрибер, не полный пайплайн
├── watch_bot.py                ⚠️ Вотчер бота (назначение не ясно без чтения)
├── fix_stocks.py               💡 Одноразовый фикс-скрипт
├── fix_de_stocks.py            💡 Одноразовый фикс-скрипт (DE)
├── fix_fr_stocks.py            💡 Одноразовый фикс-скрипт (FR)
├── blip_analyze_only.py        💡 Утилита для отладки BLIP
├── kill_agents.ps1             ✅ Скрипт остановки Chrome-процессов
├── assembler_log.txt           (лог монтажа)
├── tmp_*.png                   (временные скриншоты Grok — мусор)
├── tmp_media_output.txt        (временный лог — мусор)
├── nul                         ❌ Артефакт — пустой файл, создан случайно
└── PIPELINE_AUDIT.md           (этот файл)
```

### Активно используется
- `agents/assembler/`, `agents/media_generator/`, `agents/prompt_generator/`, `agents/transcriber/`, `agents/video_validator/`, `agents/video_cutter/`, `agents/blip_validator/`, `agents/utils/`, `agents/validator/`
- `bot/telegram_bot.py`, `bot/channel_manager.py`
- `config/.env`, `config/master_prompts/`

### Устарело / не используется
- `agents/upscaler/` — функционал дублируется в `video_cutter.py` (режим `normalize+upscale`)
- `config/master_prompts/video/master_video_veo3.txt` — Veo3 не используется в текущем флоу
- `config/bot_config.py` — заменён `.env` + `channel_manager.py`
- `pipeline.py` — stub, запускает только шаг 1
- `fix_*.py`, `blip_analyze_only.py`, `tmp_*.png`, `tmp_media_output.txt`, `nul` — временные/одноразовые файлы

---

## 2. АГЕНТЫ — статус каждого

### Transcriber (`agents/transcriber/transcriber.py`)
**Что делает:** Берёт MP3 из `data/channels/{lang}/input/`, создаёт сессию `Video_YYYYMMDD_HHMMSS`, транскрибирует через Whisper (`large-v2` на GPU / `base` на CPU), нарезает на блоки по 10с (grok-режим) или 3–8с (random-режим), верифицирует FFprobe, сохраняет `result.json`, `subtitles.srt`, `subtitles.vtt`.

**Статус:** ✅ Активен

**Зависимости:**
- `torch`, `openai-whisper`
- FFmpeg (winget `Gyan.FFmpeg`)
- GPU (CUDA): RTX 3060 — модель `large-v2`; CPU fallback — `base`
- `channel_manager.py` — язык транскрипции из активного канала

**Известные проблемы / особенности:**
- Режим `grok` — строго 10с блоки (используется для Grok)
- Режим `random` — 3–8с (для Flow / другие платформы)
- Последний блок обрезается до реального конца аудио через FFprobe
- Блоки <3с мёрджатся со следующим; блоки >8с разрезаются по word-ratio

---

### Prompt Generator (`agents/prompt_generator/prompt_generator.py`)
**Что делает:** Генерирует фото- и видео-промпты для каждого сегмента через Claude CLI (`claude -p`). Батчами по 10 сегментов, 5 параллельных батчей. Сохраняет `photo_prompts.json`, `photo_prompts.txt`, `video_prompts.json`, `video_prompts.txt`.

**Статус:** ✅ Активен

**Зависимости:**
- `claude.exe` (`%APPDATA%/Claude/claude-code/{version}/claude.exe`)
- Переменная `CLAUDECODE` должна быть убрана из env перед запуском (иначе "nested session" error)
- `channel_manager.py` — авто-подстановка мастер-промпта и языка из активного канала

**Особенности:**
- Фото-промпты: формат `SEGMENT N` → парсинг по разделителю
- Видео-промпты: формат `SEGMENT N (Photo Prompt)` → `Final Video Prompt`
- Промпты видео сохраняются с `.replace("\n\n", "\n")` — иначе ломается парсинг в `read_prompts()`
- Структура выходных файлов: `data/channels/{lang}/prompts/{session}/photo/` и `video/`

---

### Media Generator (`agents/media_generator/`)
**Что делает:** Роутер генерации медиа. Поддерживает 3 платформы:

| Платформа | Тип | Что генерирует | Файл |
|-----------|-----|----------------|------|
| 1 = Google Flow | браузер (Playwright + CDP) | фото + видео | `flow_agent.py` |
| 2 = Grok | браузер (Playwright + CDP + multi-tab) | видео (image-to-video) | `grok_agent.py` |
| 3 = PixelAgent | API (asyncio + aiohttp) | фото | `pixel_agent.py` |

**Статус:** ✅ Активен

**Режимы запуска:**
```
py agents/media_generator/media_generator.py --platform 3 --type photo
py agents/media_generator/media_generator.py --platform 2 --type video --session Video_xxx
py agents/media_generator/media_generator.py --platform 2 --type video --tabs 3
py agents/media_generator/media_generator.py --streaming   # 7 потоков фото → Grok
```

**Зависимости:**
- `aiohttp`, `requests`, `Pillow`, `playwright`, `python-dotenv`
- Chrome (CDP) для Grok/Flow
- `PIXEL_API_KEY`, `PIXEL_API_URL` из `.env`

---

### Grok Agent (`agents/media_generator/grok_agent.py`)
**Что делает:** Генерация image-to-video через `grok.com/imagine`. Multi-tab параллельный режим: каждая "вкладка" — отдельный Chrome-процесс на своём CDP-порту (9222, 9223, ...) и профиле (`~/.chrome-grok-profile-tab1`, `-tab2`, ...).

**Статус:** ✅ Активен

**Флоу генерации одного видео:**
1. Navigate → `grok.com/imagine`
2. Upload photo (`input[type='file']`)
3. Insert prompt (`div[contenteditable='true']` + `execCommand('insertText')`)
4. Click "Видео" inline-button (`button:has-text('Видео')`) — новый UI 2026-03
5. Configure: 10s, 720p, 16:9
6. Submit (кнопка `[aria-label='Отправить']` или Enter)
7. Wait for `<video src="...generated_video.mp4?cache=1">` — до `GROK_VIDEO_TIMEOUT=180с`
8. Download via Python `urllib` (primary) или browser `fetch` (fallback)
9. Save to `videos/video_NNN.mp4`

**Прогресс:** `temp/grok_progress.json` — возобновляемый (пропускает уже готовые)

**Известные исправленные баги:**
- `_grok_find_video_url` теперь ищет только `generated_video.mp4` — исправлен баг с "енотом" (первый `<video>` на странице)
- Turnstile обходится через ожидание и повтор вставки промпта

**Известные проблемы:**
- Grok UI меняется — селекторы периодически ламаются (кнопка "Видео", dropdown)
- Требует SuperGrok подписку
- Chrome-профили (`-tab1`, `-tab2`, `-tab3`) нужно создавать вручную через setup-режим

---

### PixelAgent (`agents/media_generator/pixel_agent.py`)
**Что делает:** Параллельная генерация фото через API `voiceapi.csv666.ru`. Поддерживает 2 версии API:
- `v1`: синхронный POST → `image_b64` (4 параллельных потока)
- `v2`: task-based polling POST → task_id → poll → download (3 потока)
- `both`: роутер v1/v2 по чётности `seg_id` (итого 7 потоков)

**Статус:** ✅ Активен

**Особенности:**
- Авто-retry до 7 попыток (RETRY_DELAYS: 5, 10, 20, 30, 60, 120с)
- 3 круга авто-retry для провалившихся (AUTO_RETRY_ROUNDS=3, пауза 60с)
- Валидация изображений: вертикальные → retry; не-16:9 → crop по центру; полностью чёрное (max_brightness<15) → retry
- 401 → `_AuthError` (abort всей генерации)
- Прогресс: `temp/pixel_progress.json`

**Известные проблемы:**
- Промпты с "NASA", "JPL", "Carl Sagan" дают мультяшных персонажей/енотов → мастер-промпт запрещает эти слова
- Иногда возвращает вертикальные изображения — обрабатывается через `_validate_and_fix_image`

---

### Pipeline Runner (`agents/media_generator/pipeline_runner.py`)
**Что делает:** Streaming-режим — параллельная генерация 7 потоков фото (v1×4 + v2×3), затем последовательно Grok видео.

**Статус:** ✅ Активен (используется через `--streaming`)

---

### Multi Channel Runner (`agents/media_generator/multi_channel_runner.py`)
**Что делает:** Параллельная генерация для двух каналов (DE + FR) с общими семафорами:
- Pixel: 7 потоков суммарно (PIXEL_V1_SEMAPHORE=4, PIXEL_V2_SEMAPHORE=3)
- Grok: 1 слот (Chrome не поддерживает параллельный запуск с разными профилями одновременно на одной машине)
- FFmpeg монтаж: 1 GPU-слот последовательно

**Статус:** ✅ Новый (ветка `refactor/agent-subfolders`), не полностью интегрирован с ботом

---

### BLIP Validator — blip_validator/ (`agents/blip_validator/`)
**Что делает:** FastAPI-сервер BLIP-2 (`blip2-flan-t5-xl`) на порту **8765**. Принимает видео, извлекает 3 кадра через FFmpeg, задаёт вопросы через BLIP-2, возвращает описания.

**Статус:** ⚠️ Опциональный (тяжёлый, ~22 мин загрузка). Используется как primary analyzer в `video_validator.py`, но при недоступности падает на `blip_analyzer` (порт 5679).

**Зависимости:** `fastapi`, `uvicorn`, `torch`, `transformers`, `Pillow`, `accelerate`

**Модели:**
- Primary: `Salesforce/blip2-flan-t5-xl` (~15GB VRAM)
- Fallback: `Salesforce/blip2-opt-2.7b` (~8GB VRAM)

---

### Video Validator (`agents/video_validator/video_validator.py`)
**Что делает:** Валидация всех видео сессии через BLIP+Claude. 3 параллельных потока анализа.

**Архитектура анализа:**
1. Если BLIP-2 (порт 8765) доступен → `blip_client.analyze_video` → описания кадров
2. Иначе → `blip_analyzer` (порт 5679, blip-vqa-base) → готовый dict valid/score/reason
3. В обоих случаях → `claude_analyzer.analyze_with_claude` → финальное решение

**6 критериев оценки (blip_analyzer):**
1. Критический дефект (чёрный экран, стоп-кадр)
2. Космическая/научная тематика
3. Соответствие промпту
4. Базовое качество (не смазано)
5. Правильный объект
6. Смысловое соответствие

`valid = (not has_artifacts) and score >= 3`

**Стратегии при плохих видео:**
- `≤ 50% плохих` → `handle_partial_replacement`: замена стоками (Pexels/Pixabay/NASA) + Grok перегенерация как fallback
- `> 50% плохих` → `handle_critical_failure`: чётные → регенерация, нечётные → стоки

**После валидации:** вызывает `video_cutter.process_all_videos` (нормализация + апскейл)

**Статус:** ✅ Активен

**Известные исправленные баги:**
- German `segment_keywords` вызывал over-rejection в BLIP (немецкие термины не распознавались) → исправлено через `segment_keywords=""`

---

### Stock Finder (`agents/video_validator/stock_finder.py`)
**Что делает:** Поиск стоковых видео через Pexels, Pixabay, NASA APIs. BLIP pre-check (порт 5679) для проверки найденного стока перед использованием. Thread-safe через `_stock_lock`.

**Статус:** ✅ Активен

**Stock History (`agents/video_validator/stock_history.py`):** Хранит историю использованных стоков за последние 3 ролика. Кросс-сессионная дедупликация — стоки не повторяются.

---

### BLIP Server — video_validator/ (`agents/video_validator/blip_server.py`)
**Что делает:** Лёгкий HTTP-сервер (stdlib `HTTPServer`) на порту **5679** с моделью `Salesforce/blip-vqa-base` (~1.5GB). Принимает `{image_path, questions}`, возвращает `{answers}`. Запускается автоматически из `blip_analyzer._ensure_server()`.

**Статус:** ✅ Активен (основной BLIP-сервер в текущем флоу)

**Разница от `/agents/blip_validator/blip_server.py`:** Это лёгкая версия (VQA, порт 5679), тогда как `blip_validator/blip_server.py` — тяжёлая FastAPI (BLIP-2, порт 8765).

---

### Assembler (`agents/assembler/assembler.py`)
**Что делает:** Полный монтаж финального видео через FFmpeg.

**12-шаговый пайплайн:**
1. Найти сессию
2. Загрузить сегменты из `result.json`
3. Найти MP3 озвучки в `data/{ch}/input/{session}/`
4. Найти интро в `videos_upscaled/Intro_*.mp4`
5. Определить папку клипов (`clips_upscaled` → `clips` → `videos_upscaled` → `videos`)
6. Собрать `project.json` для `montage.py`
7. `montage.py` → `scenes_video.mp4`
8. Если есть интро: склеить `intro + scenes_video → montage_base.mp4`
9. Подобрать 3–5 треков из `data/music/` (fade-in 2s на 1:28, crossfade 3s, fade-out 4s)
10. Смикшировать аудио: озвучка -6dB / интро -37dB / музыка -33dB
11. Сгенерировать word-level SRT субтитры
12. Наложить субтитры FFmpeg → `final_complete.mp4` (1920×1080, 25 Mbps)

**Статус:** ✅ Активен

**Зависимости:**
- FFmpeg (обязательно)
- `ass_generator.py` — word-level ASS-субтитры
- `montage.py`, `subtitle_burner.py`, `audio_mixer.py`, `transitions.py`
- Шрифт: `Organetto.ttf` (путь из `ORGANETTO_FONT_PATH`)

---

### Video Cutter (`agents/video_cutter/video_cutter.py`)
**Что делает:** Нарезка исходного видео по сегментам из `result.json`, нормализация (длина, разрешение, FPS) и апскейл через FFmpeg (Lanczos/Bicubic) или Real-ESRGAN (GPU). Вызывается из `video_validator.py` после валидации.

**Статус:** ✅ Активен

---

### Validator (`agents/validator/validator.py`)
**Что делает:** Проверка целостности пайплайна (4 шага): транскрипция → фото-промпты → видео-промпты → медиафайлы. Опция `--fix` пытается исправить проблемы.

**Статус:** ✅ Вспомогательный

---

### Regen Agent (`agents/regen_agent/regen_agent.py`)
**Что делает:** Перегенерирует плохие фото из `blip_report.json`. Обновляет фото- и видео-промпты, удаляет старые фото, запускает PixelAgent.

**Статус:** ⚠️ Использует `blip_report.json` — формат от `blip_validator/blip_validator.py`. Актуально только если `blip_validator` (старый) используется как отдельный шаг. В новом флоу эту роль выполняет `video_validator.py` (через `handle_partial_replacement`/`handle_critical_failure`).

---

### Photo Upscaler (`agents/upscaler/photo_upscaler.py`)
**Что делает:** Апскейл фотографий через Pillow (Lanczos/Bicubic) до 1080/2K/4K.

**Статус:** ⚠️ Дублирует функционал `video_cutter.py`. Используется редко.

---

### Telegram Bot (`bot/telegram_bot.py`)
**Что делает:** Личный Telegram-бот (только для `TELEGRAM_ALLOWED_USER_ID`). Control Panel пайплайна.

**Команды/функционал:**
- Управление каналами (DE/FR) через inline-кнопки
- Запуск каждого агента отдельно (транскрибер, промпты, Pixel, Grok, валидатор, монтаж)
- Мониторинг прогресса (опрос progress-файлов в `temp/`)
- Уведомления (Grok каждые 10 видео, Pixel при ошибках)
- Остановка процессов

**Прогресс-файлы, которые читает бот:**
- `temp/prompt_progress.json`
- `temp/pixel_progress.json`
- `temp/grok_progress.json`
- `temp/blip_progress.json`
- `temp/regen_progress.json`
- `temp/video_validator_progress.json`
- `temp/normalize_upscale_progress.json`

**Статус:** ✅ Активен

---

## 3. ПАЙПЛАЙН — полный флоу

```
INPUT: MP3 аудио (озвучка ElevenLabs) → data/channels/{lang}/input/
```

### Шаг 1 — Транскрипция
```
py agents/transcriber/transcriber.py
```
- **Вход:** MP3 в `data/channels/{lang}/input/`
- **Выход:** `data/channels/{lang}/transcripts/{session}/result.json` (сегменты 10с)
- **Время:** ~2–5 мин (GPU large-v2) / ~15–30 мин (CPU base)
- **Побочно:** `subtitles.srt`, `subtitles.vtt`

### Шаг 2 — Генерация промптов
```
py agents/prompt_generator/prompt_generator.py --type both
```
- **Вход:** `result.json` (сегменты)
- **Выход:** `data/channels/{lang}/prompts/{session}/photo/photo_prompts.json`, `video/video_prompts.json`
- **Время:** ~5–15 мин (зависит от числа сегментов и скорости Claude CLI)
- **Батчинг:** 10 сегментов на батч, 5 параллельных батчей

### Шаг 3 — Генерация фото (PixelAgent)
```
py agents/media_generator/media_generator.py --platform 3 --type photo
```
- **Вход:** `photo_prompts.json`
- **Выход:** `data/channels/{lang}/media/{session}/photos/photo_NNN.png`
- **Время:** ~30–90 мин (в зависимости от числа фото)
- **Параллелизм:** 3–7 потоков (v1×4 + v2×3 при `PIXEL_API_VERSION=both`)

### Шаг 4 — Генерация видео (Grok)
```
py agents/media_generator/media_generator.py --platform 2 --type video --tabs 3
```
- **Вход:** `photo_NNN.png` + `video_prompts.json`
- **Выход:** `data/channels/{lang}/media/{session}/videos/video_NNN.mp4`
- **Время:** ~2–3 мин на видео × N видео / количество_вкладок
- **Параллелизм:** 3 вкладки (GROK_NUM_TABS=3), каждая — отдельный Chrome на порту 9222/9223/9224
- **Возобновляемость:** `temp/grok_progress.json`

### Шаг 5 — Валидация видео
```
py agents/video_validator/video_validator.py --channel channel_001_cosmos_de
```
- **Вход:** `videos/video_NNN.mp4`
- **Выход:** `data/channels/{lang}/transcripts/{session}/video_validation_report.json`
- **Анализ:** BLIP (порт 5679) + Claude → valid/invalid, score 0–6
- **При невалидных:** замена стоками (Pexels/Pixabay/NASA) или Grok перегенерация
- **После валидации:** автоматическая нормализация + апскейл через video_cutter
- **Время:** ~1–5 мин на видео × N / 3 потока

### Шаг 6 — Монтаж
```
py agents/assembler/assembler.py --session Video_xxx
```
- **Вход:** нормализованные клипы, MP3 озвучки, `result.json`, фоновая музыка, интро (опц.)
- **Выход:** `data/channels/{lang}/media/{session}/final_complete.mp4` (1920×1080, 25 Mbps, субтитры)
- **Время:** ~5–15 мин

```
OUTPUT: final_complete.mp4 → готов к публикации
```

### Альтернативный режим: Streaming (фото+видео одновременно)
```
py agents/media_generator/media_generator.py --streaming
```
7 потоков фото параллельно → по завершении всех фото → Grok видео

### Двойной канал: DE+FR параллельно
```
py agents/media_generator/multi_channel_runner.py
```
Оба канала параллельно с общими семафорами. Монтаж последовательно через GPU.

---

## 4. КОНФИГУРАЦИЯ

Файл: `config/.env`

### Используемые переменные

| Переменная | Значение | Используется в |
|-----------|----------|----------------|
| `CHROME_PATH` | `C:\Program Files\Google\Chrome\Application\chrome.exe` | `grok_agent.py`, `flow_agent.py` |
| `CHROME_CDP_PORT` | `9222` | `grok_agent.py` (base port, +N для tab-N) |
| `PIXEL_API_KEY` | (hex-encoded) | `pixel_agent.py` |
| `PIXEL_API_URL` | `https://voiceapi.csv666.ru` | `pixel_agent.py` |
| `PIXEL_MAX_CONCURRENT` | `3` | `pixel_agent.py` (asyncio semaphore) |
| `PIXEL_API_VERSION` | `both` | `pixel_agent.py` (v1/v2 routing) |
| `PIXEL_V1_MAX_CONCURRENT` | `4` | `pixel_agent.py` (threading semaphore) |
| `PIXEL_V2_MAX_CONCURRENT` | `3` | `pixel_agent.py` (threading semaphore) |
| `ORGANETTO_FONT_PATH` | путь к ttf | `assembler.py` (субтитры) |
| `TRANSITION_TYPE` | `fade` | `assembler.py` (xfade переход) |
| `TRANSITION_DURATION` | `1.0` | `assembler.py` |
| `OUTPUT_BITRATE` | `25M` | `assembler.py` |
| `OUTPUT_MAXRATE` | `30M` | `assembler.py` |
| `OUTPUT_BUFSIZE` | `60M` | `assembler.py` |
| `MUSIC_DIR` | `C:\Users\Serafim\Music\background` | `assembler.py` |
| `PREMIERE_EXE` | путь к Premiere Pro | (зарезервировано) |
| `TELEGRAM_BOT_TOKEN` | токен бота | `telegram_bot.py` |
| `TELEGRAM_ALLOWED_USER_ID` | ID пользователя | `telegram_bot.py`, `utils.py` (уведомления) |
| `PEXELS_API_KEY` | ключ | `stock_finder.py` |
| `PIXABAY_API_KEY` | ключ | `stock_finder.py` |
| `NASA_API_KEY` | `DEMO_KEY` | `stock_finder.py` |
| `SUBTITLE_FADE_IN_MS` | `100` | `ass_generator.py` |
| `SUBTITLE_FADE_OUT_MS` | `100` | `ass_generator.py` |
| `SUBTITLE_RISE_PX` | `15` | `ass_generator.py` |
| `SUBTITLE_FONT_SIZE` | `28` | `ass_generator.py` |

### Не используются / пустые

| Переменная | Причина |
|-----------|---------|
| `OPENROUTER_API_KEY` | Пусто — Molmo 2 не подключён |
| `OPENROUTER_MODEL` | Пусто — не используется (заменён BLIP+Claude) |
| `FLOW_PROJECT_ID` | Пусто — Flow не активен |
| `RECAPTCHA_SITE_KEY` | Устарело |
| `MOGRT_*` | Пусто — MOGRT шаблоны не настроены |
| `PC_*` | Пусто — Premiere Composer не используется |
| `SUBTITLE_STYLE_PATH/NAME` | Пусто — Premiere субтитры не используются |
| `CHROME_FLOW_PROFILE_DIR` | Не читается кодом |

### Что нужно проверить/настроить
- `OPENROUTER_API_KEY` — если нужен Molmo 2 (заменит Claude в video_validator)
- `NASA_API_KEY` — `DEMO_KEY` лимитирован (60 req/hour) → зарегистрируй собственный
- `MUSIC_DIR` — путь должен существовать и содержать MP3/WAV треки
- Grok-профили (`~/.chrome-grok-profile-tab1`, `-tab2`, `-tab3`) — должны быть авторизованы вручную

---

## 5. ИЗВЕСТНЫЕ ПРОБЛЕМЫ

### Критические ❌

**5.1 `video_validator.py` жёстко кодирует пути БЕЗ канала:**
```python
TRANSCRIPTS   = BASE_DIR / "data" / "transcripts"
PROMPTS_DIR   = BASE_DIR / "data" / "prompts"
MEDIA_DIR     = BASE_DIR / "data" / "media"
```
Функция `_get_channel_base` существует, но `TRANSCRIPTS`, `PROMPTS_DIR`, `MEDIA_DIR` как глобальные переменные указывают на старые пути. Запуск без `--channel` использует `data/media/` (старая структура), а не `data/channels/de/media/`. Это ломает video_validator при работе с DE/FR каналами без явного `--channel`.

**5.2 `regen_agent.py` использует `blip_report.json` (устаревший формат):**
Этот файл создаётся старым `blip_validator/blip_validator.py`, но не создаётся новым `video_validator.py`. Regen agent несовместим с текущим флоу.

**5.3 `video_cutter.py` не знает о структуре каналов:**
```python
INPUT_DIR       = BASE_DIR / "data" / "input"
TRANSCRIPTS_DIR = BASE_DIR / "data" / "transcripts"
MEDIA_DIR       = BASE_DIR / "data" / "media"
```
При вызове из `video_validator.py` через `process_all_videos(session)` будет использовать глобальные пути без учёта канала. Работает только если активный канал = default.

**5.4 `pipeline.py` — неполная реализация:**
Запускает только транскрибер. Остальные 5 шагов не вызываются.

---

### Важные ⚠️

**5.5 Дублирование BLIP-серверов:**
Есть два BLIP-сервера с разными портами и моделями:
- `agents/video_validator/blip_server.py` — порт 5679, `blip-vqa-base` (лёгкий, VQA)
- `agents/blip_validator/blip_server.py` — порт 8765, `blip2-flan-t5-xl` (тяжёлый, FastAPI)

Оба могут быть запущены одновременно. Нет единой точки управления.

**5.6 Grok UI нестабилен:**
Grok периодически меняет UI. Текущий код (март 2026) ожидает inline-кнопку `button:has-text('Видео')`. Если UI обновится — нужна правка селекторов в `_grok_open_video_panel`.

**5.7 `multi_channel_runner.py` использует `agents/utils/paths.py` (session-first структура):**
`paths.py` описывает session-first структуру (`data/channels/{lang}/{session}/input/`), но реальные данные хранятся в category-first (`data/channels/{lang}/media/{session}/`). `get_last_session()` в `paths.py` поддерживает оба варианта через fallback, но остальные функции (`get_input_dir`, `get_media_dir`) используют session-first.

**5.8 Grok таймаут 180с может быть недостаточен:**
При высокой нагрузке Grok генерирует видео дольше. `GROK_VIDEO_TIMEOUT=180` задан в `utils.py` как константа, но не вынесен в `.env`.

**5.9 `blip_validator/blip_validator.py` — устаревший агент:**
Создаёт `blip_report.json` в формате, который не поддерживается новым `video_validator.py`. Должен быть либо обновлён, либо помечен как deprecated.

**5.10 Промпты с "NASA"/"JPL" в PixelAgent:**
Мастер-промпты должны явно запрещать эти слова (PixelAgent возвращает мультяшных персонажей). Проверь актуальные `photo_master_prompt_de.txt` и `photo_master_prompt_fr.txt`.

---

### Мелкие 💡

**5.11 Временные файлы в корне проекта:**
`tmp_*.png`, `tmp_media_output.txt`, `nul` — артефакты отладки Grok. Нужно убрать в `.gitignore` или удалить.

**5.12 `assembler_log.txt`, `prproj_analysis.txt`, `temp_prproj.xml`, `tempconcat_final.txt` в корне:**
Временные файлы. Следует добавить в `.gitignore`.

**5.13 `config/bot_config.py` — устарело:**
Содержит старые настройки. Полностью заменён `.env` + `channel_manager.py`.

**5.14 `agents/upscaler/` — дублирует `video_cutter.py`:**
Обе системы делают апскейл фото. `photo_upscaler.py` работает с data/media/ без учёта каналов.

**5.15 `watch_bot.py` — назначение непонятно:**
Файл существует но не задокументирован в основном флоу.

**5.16 Hardcoded путь FFmpeg в нескольких файлах:**
Путь `Microsoft/WinGet/Packages/Gyan.FFmpeg_...` продублирован в: `transcriber.py`, `video_cutter.py`, `blip_validator.py`. Должен быть вынесен в `.env` (`FFMPEG_DIR`).

---

## 6. ЗАВИСИМОСТИ

### Python пакеты (по агентам)

| Агент | Пакеты |
|-------|--------|
| transcriber | `torch`, `openai-whisper` |
| prompt_generator | stdlib only (Claude CLI внешний) |
| media_generator | `aiohttp`, `requests`, `Pillow`, `playwright`, `python-dotenv`, `numpy` |
| video_validator | `transformers>=4.40`, `torch>=2.0`, `Pillow`, `requests`, `python-dotenv`, `einops`, `timm`, `accelerate` |
| blip_validator | `fastapi`, `uvicorn[standard]`, `torch`, `transformers`, `Pillow`, `requests`, `accelerate` |
| assembler | stdlib + FFmpeg |
| video_cutter | stdlib + FFmpeg (+ `realesrgan` опционально) |
| validator | stdlib + FFmpeg |
| bot | `python-telegram-bot`, `python-dotenv` |

### Внешние инструменты

| Инструмент | Версия | Где используется |
|-----------|--------|-----------------|
| FFmpeg | 8.0.1 (winget) | assembler, transcriber, video_cutter, blip_validator, stock_finder |
| Google Chrome | последняя | grok_agent, flow_agent (CDP) |
| Playwright | Python | grok_agent, flow_agent |
| Claude CLI | `claude.exe` 2.1.51+ | prompt_generator, claude_analyzer |

### Внешние API

| API | Ключ | Лимиты |
|-----|------|--------|
| PixelAgent (`voiceapi.csv666.ru`) | `PIXEL_API_KEY` | нет известных жёстких лимитов |
| Grok (`grok.com/imagine`) | Куки (SuperGrok) | ~unlimited при наличии подписки |
| Telegram Bot API | `TELEGRAM_BOT_TOKEN` | стандартные Telegram лимиты |
| Pexels | `PEXELS_API_KEY` | 200 req/hour |
| Pixabay | `PIXABAY_API_KEY` | 5000 req/hour |
| NASA APOD/Video | `NASA_API_KEY=DEMO_KEY` | 60 req/hour (DEMO_KEY), 1000/hour (личный) |
| Claude API | через `claude.exe` (Claude Code) | зависит от подписки |

### Модели ML

| Модель | Размер | Где используется | Порт |
|--------|--------|-----------------|------|
| Whisper `large-v2` | ~3GB | transcriber (GPU) | — |
| Whisper `base` | ~150MB | transcriber (CPU fallback) | — |
| `blip-vqa-base` | ~1.5GB | blip_server (video_validator) | 5679 |
| `blip2-flan-t5-xl` | ~15GB VRAM | blip_server (blip_validator) | 8765 |
| `blip2-opt-2.7b` | ~8GB VRAM | blip_server fallback | 8765 |

---

## 7. КАНАЛЫ

### channel_001_cosmos_de (DE)

| Параметр | Значение |
|---------|---------|
| ID | `channel_001_cosmos_de` |
| Имя | Cosmos DE |
| Data subdir | `data/channels/de/` |
| Язык транскрипции | `de` |
| Source language | `de` |
| System context | "Scientific space documentary in German" |
| Niche | `cosmos` |
| Photo master | `photo_master_prompt_de.txt` |
| Video master | `master_video_grok.txt` |
| Subtitle language | `de` |
| Intro | включён |
| TG channel | не настроен (пусто) |

### channel_002_cosmos_fr (FR)

| Параметр | Значение |
|---------|---------|
| ID | `channel_002_cosmos_fr` |
| Имя | Cosmos FR |
| Data subdir | `data/channels/fr/` |
| Язык транскрипции | `fr` |
| Source language | `fr` |
| System context | "Scientific space documentary in French" |
| Niche | `cosmos` |
| Photo master | `photo_master_prompt_fr.txt` |
| Video master | `master_video_grok_fr.txt` |
| Subtitle language | `fr` |
| Intro | включён |
| TG channel | не настроен (пусто) |

### Stock History
Хранится в:
- DE: `data/channels/de/stock_history.json`
- FR: `data/channels/fr/stock_history.json`

Дедупликация стоков за последние **3 ролика** (`HISTORY_DEPTH=3`).

---

## 8. ПРОИЗВОДИТЕЛЬНОСТЬ

### Типичное время на ролик (~30 сегментов / ~5 мин аудио)

| Этап | Время | Узкое место |
|------|-------|-------------|
| Транскрипция (GPU large-v2) | 2–5 мин | Whisper, только GPU |
| Транскрипция (CPU base) | 15–30 мин | CPU bound |
| Генерация промптов (фото+видео) | 5–15 мин | Claude CLI latency |
| Генерация фото — 30 шт (7 потоков) | 20–40 мин | PixelAgent API rate |
| Генерация видео — 30 шт (3 Grok вкладки) | 60–90 мин | Grok 2–3 мин/видео |
| Валидация — 30 видео (3 потока BLIP+Claude) | 15–30 мин | Claude CLI per-video |
| Монтаж (FFmpeg, 30 клипов) | 5–15 мин | FFmpeg encoding |
| **ИТОГО (одиночный канал)** | **~2–3.5 часа** | Grok video gen |
| **ИТОГО (streaming: фото||видео)** | **~1.5–2.5 часа** | Grok video gen |
| **ИТОГО (два канала параллельно)** | **~2–3 часа** | Grok (последовательно) |

### Узкие места

1. **Grok видео-генерация** — главный bottleneck (2–3 мин/видео). 3 вкладки = 3x ускорение, но требует 3 авторизованных Chrome-профиля.
2. **PixelAgent** — 7 потоков достаточно для ~30 фото за 20–40 мин. При увеличении числа сегментов время растёт линейно.
3. **Claude CLI** — каждый вызов subprocess, latency ~5–15с. При генерации промптов для 30 сегментов (3 батча × 5 параллельных) — ~15 мин.
4. **BLIP-2 (порт 8765)** — загрузка ~22 мин. Если не предзапущен — первая валидация очень долгая. BLIP-VQA (порт 5679) загружается за ~1 мин.
5. **Whisper large-v2** — только GPU. На CPU base (fallback) в 5–10x медленнее.

### Оптимизации
- `--streaming` режим: фото генерируются параллельно пока Claude делает промпты → экономит 15–30 мин
- `multi_channel_runner.py`: общие семафоры Pixel (7 потоков суммарно) → одновременная генерация фото для DE+FR
- Grok прогресс (`grok_progress.json`) — возобновляемость при сбое без потери прогресса
- PixelAgent авто-retry (3 круга) — снижает число ручных перезапусков

---

## 9. TODO

### Высокий приоритет

- [ ] **Исправить `video_validator.py`** — использовать `_get_channel_base` для ВСЕХ путей, не только при обращении к `ch_media`. Глобальные `TRANSCRIPTS`, `PROMPTS_DIR`, `MEDIA_DIR` должны учитывать активный канал.

- [ ] **Исправить `video_cutter.py`** — принимать `channel_id` как аргумент и использовать правильные пути каналов при вызове из `video_validator.py`.

- [ ] **Интеграция `multi_channel_runner.py` в Telegram-бот** — кнопка "Запустить оба канала" в боте.

- [ ] **Настроить TG-каналы** — заполнить `tg_channel` в `config.json` обоих каналов для авто-публикации.

- [ ] **NASA API ключ** — заменить `DEMO_KEY` на личный ключ (60 req/hour vs 1000/hour).

### Средний приоритет

- [ ] **Вынести `GROK_VIDEO_TIMEOUT` в `.env`** — сейчас hardcoded 180с в `utils.py`.

- [ ] **Вынести путь FFmpeg в `.env`** — сейчас дублируется в 4+ файлах.

- [ ] **Обновить `regen_agent.py`** — совместить с форматом `video_validation_report.json` (от нового `video_validator.py`), или задокументировать как deprecated.

- [ ] **Завершить `pipeline.py`** — добавить остальные 5 шагов пайплайна или удалить stub.

- [ ] **Добавить `paths.py` во все агенты** — централизованное управление путями вместо дублирования `BASE_DIR / "data" / "channels" / ...`.

- [ ] **Тест Grok UI** — после каждого обновления Grok проверять: `button:has-text('Видео')`, `[aria-label='Отправить']`.

### Низкий приоритет

- [ ] **Очистить корень проекта** — удалить `tmp_*.png`, `nul`, `assembler_log.txt`, `prproj_analysis.txt`, `temp_prproj.xml`, `tempconcat_final.txt`, добавить в `.gitignore`.

- [ ] **Удалить/архивировать `fix_*.py`** — одноразовые скрипты после завершения миграции.

- [ ] **Задокументировать `watch_bot.py`** — что делает, зачем нужен.

- [ ] **Обновить `master_video_veo3.txt`** — либо удалить (если Veo3 не используется), либо подключить.

- [ ] **`config/bot_config.py`** — удалить или пометить deprecated.

---

## 10. РЕКОМЕНДАЦИИ

### Убрать

1. **`agents/upscaler/photo_upscaler.py`** — полностью дублируется в `video_cutter.py`. Вызов `py agents/video_cutter/video_cutter.py --mode normalize+upscale` делает всё то же самое.

2. **`config/bot_config.py`** — мёртвый файл, заменён `.env`.

3. **`config/master_prompts/video/master_video_veo3.txt`** — не используется. Если Veo3 не планируется, удалить.

4. **Временные файлы в корне** (`tmp_*.png`, `nul`, логи) — добавить в `.gitignore`.

### Улучшить

5. **Централизация путей** — все агенты должны использовать `agents/utils/paths.py` вместо собственных вычислений `BASE_DIR / "data" / "channels" / ...`. Сейчас дублируется в ~8 файлах.

6. **BLIP-сервер — единая точка запуска** — рассмотреть supervisord или Windows Service для BLIP (порт 5679). Сейчас он запускается лениво из `_ensure_server()`, что добавляет 60с задержки при первом запуске.

7. **`GROK_VIDEO_TIMEOUT` в `.env`** — разные видео могут требовать разного времени ожидания. 180с — иногда мало при нагрузке.

8. **NASA API ключ** — зарегистрировать на [api.nasa.gov](https://api.nasa.gov) для лимита 1000 req/hour.

9. **Telegram-канал ID** — заполнить в конфигах каналов для авто-публикации готовых видео.

10. **Версионирование данных** — рассмотреть добавление `manifest.json` в каждую сессию с метаданными (дата, версия агентов, параметры).

### Добавить

11. **Health-check endpoint** для мониторинга — один скрипт, который проверяет: Chrome доступен, BLIP-сервер жив, `.env` заполнен, Grok куки не просрочены.

12. **Авто-очистка `temp/`** — удалять `blip_pre_*.jpg`, `blip_frame_*.jpg`, `blip_srv_*.jpg` старше 24ч. Сейчас temp/ растёт неконтролируемо.

13. **Retry для Telegram-публикации** — сейчас TG-канал не заполнен; когда заполнится, нужна логика retry при ошибках публикации.
