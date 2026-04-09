# Прогресс-бары в Video Pipeline 📊

## Что установлено?

Я установил пакет `tqdm` и создал удобный модуль **`agents/utils/progress.py`** для использования во всех скриптах.

## Как использовать?

### 1️⃣ **Основное использование в циклах**

```python
from agents.utils.progress import ProgressBar

# Простейший способ - оборачиваем список
for item in ProgressBar.iterate(items, desc="Описание задачи"):
    process(item)
```

**Результат:**
```
Описание задачи: 100%|███████████████████| 20/20 [00:03<00:00]
```

---

### 2️⃣ **Отслеживание времени выполнения задач**

```python
from agents.utils.progress import ProgressBar

# Отмечаем начало
start = ProgressBar.task("Загрузка модели AI")
# ... ваш код ...
ProgressBar.done(start, "Модель загружена")
```

**Результат:**
```
▶ Загрузка модели AI...
✓ Модель загружена (2.3s)
```

---

### 3️⃣ **Ручное управление прогрессом**

Для более сложных сценариев:

```python
pbar = ProgressBar.manual(total=100, desc="Загрузка")
for i in range(10):
    process_chunk()
    pbar.update(10)  # увеличиваем на 10
pbar.close()
```

---

### 4️⃣ **Прогресс с нумерацией**

```python
from agents.utils.progress import progress_enumerate

for i, item in progress_enumerate(items, desc="Обработка файлов"):
    print(f"Файл {i+1}: {item}")
```

---

## Примеры из реальных скриптов

### Пример из `transcriber.py` 🎙️

```python
# Загрузка модели Whisper
start = ProgressBar.task("Загрузка модели 'large-v3-turbo' на GPU")
model = WhisperModel(model_size, device=device, compute_type=compute)
ProgressBar.done(start, "Модель загружена")

# Транскрибирование
start = ProgressBar.task("Транскрипция аудио через Whisper")
segments, duration, all_words = transcribe(model, audio_path)
ProgressBar.done(start, f"Завершено: {len(segments)} сегментов")

# Нарезка
start = ProgressBar.task("Нарезка на блоки")
segments = build_segments(whisper_segs, mode)
ProgressBar.done(start, f"Готово: {len(segments)} блоков")

# Верификация
start = ProgressBar.task("FFmpeg верификация")
segments, meta = verify_with_ffmpeg(audio_path, segments)
ProgressBar.done(start, f"Проверено: {meta['coverage']:.1f}s")
```

---

## Интеграция в другие скрипты

### Для `assembler.py` (видео-ассемблирование)

```python
from agents.utils.progress import ProgressBar

# Обработка видеоклипов
for clip in ProgressBar.iterate(clips, desc="Обработка видеоклипов"):
    process_clip(clip)

# Или с трекингом времени
start = ProgressBar.task("Сборка финального видео")
assemble_video(clips, output)
ProgressBar.done(start, "Видео готово")
```

### Для батж-обработки

```python
from agents.utils.progress import progress_loop

videos = get_all_videos()
for video in progress_loop(videos, desc="Обработка видео"):
    transcribe_and_generate(video)
```

---

## Это дает вам 📍

✅ **Видимость прогресса** — сразу видно, что происходит  
✅ **Оценка времени** — когда закончится (-ч:мм:сс)  
✅ **Процент готовности** — сколько сделано / осталось  
✅ **Скорость обработки** — элементов/час, мин/операция  
✅ **Профилирование** — какие шаги долгие, где оптимизировать  

---

## Где это уже используется?

- ✅ `pipeline.py` — главный скрипт
- ✅ `agents/transcriber/transcriber.py` — основные шаги транскрипции
- 🔄 *Готово к добавлению в другие агенты*

---

## Примеры для копирования

### Вариант 1 (быстро + прогресс)
```python
for item in ProgressBar.iterate(items, desc="Работа"):
    do_something(item)
```

### Вариант 2 (потом трекинг времени)
```python
start = ProgressBar.task("Выполняю операцию X")
result = expensive_operation()
ProgressBar.done(start, "Операция X завершена")
```

### Вариант 3 (комбинированный)
```python
for batch in ProgressBar.iterate(batches, desc="Батчи"):
    start = ProgressBar.task(f"  Обработка батча {batch['id']}")
    process_batch(batch)
    ProgressBar.done(start)
```

---

## Проверка работы

Запустите демо:
```bash
python test_progress_bars.py
```

Вы увидите все виды прогресс-баров в действии! 🎬
