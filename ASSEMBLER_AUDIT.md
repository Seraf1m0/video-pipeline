# АУДИТ ASSEMBLER PIPELINE
**Дата:** 2026-03-27
**Ветка:** refactor/agent-subfolders
**Статус:** Актуально

---

## 1. АРХИТЕКТУРА

### Назначение
`assembler.py` — финальный монтаж: озвучка + клипы → готовое видео с переходами, субтитрами, SFX, музыкой.

### Структура сессии
```
data/channels/{lang}/{session}/
├── input/          ← MP3 озвучка
├── transcripts/    ← result.json (Whisper)
├── intro_clips/    ← нарезанные клипы интро (до монтажа intro.mp4)
├── intro.mp4       ← готовое интро (вставляется целиком)
└── output/         ← финальное видео
```

### Модули
| Файл | Назначение |
|------|-----------|
| `assembler.py` | главный pipeline, entry point |
| `transitions.py` | все переходы (clip-to-clip + intro→main) |
| `audio_mixer.py` | микширование озвучка + музыка + интро-аудио |
| `sfx_mixer.py` | SFX-инжект на переходы |
| `sfx_narrator.py` | контекстный анализ сценария (musical envelope, SFX events) |
| `ass_generator.py` | ASS субтитры (grouped + karaoke) |
| `pipeline_validator.py` | двухуровневая валидация (pre/post render) |
| `style_engine.py` | маппинг channel → стили переходов |
| `channel_styles.json` | конфиг стилей по каналам |

---

## 2. ПОЛНЫЙ PIPELINE (порядок вызовов)

Запуск: `py agents/assembler/assembler.py --channel channel_001_cosmos_de --use-library`

| Шаг | Действие | Время |
|-----|---------|-------|
| 0 | Parse args + ChannelManager + StyleEngine | — |
| 1 | Найти/создать сессию | — |
| 2 | Загрузить сегменты из `result.json` | ~0.1s |
| 3 | Найти озвучку + `detect_silence_regions()` | ~3–5s |
| 4 | Найти `intro.mp4` | — |
| **4б** | **СТОП: нет intro.mp4 → генерировать intro_clips/ → sys.exit(0)** | — |
| 5 | `resolve_library_clips()` (из `clip_selection.json`) | ~0.1s |
| 6 | `prepare_trimmed_clips()` (8 workers, NVENC) | ~60–120s |
| 7 | `compute_transition_times()` + `_build_trans_sequence()` | ~0.1s |
| 8 | **Pre-render validation** | ~0.5s |
| 9 | **3 параллельных потока:** | |
| 9a | → `generate_ass()` / `generate_karaoke_ass()` | ~1–2s |
| 9b | → `concat_all_with_transitions()` (видеоряд) | ~120–240s |
| 9c | → audio: voice + music + analyze_script + final_mix + inject_sfx | ~30–60s |
| 10 | Паддинг видеоряда если < audio_total_dur | — |
| 11 | `intro_trans_fn(intro, videotrack)` | — |
| 12 | Final encode: color_grade + ASS filter + audio mux (NVENC h264) | ~60–120s |
| 13 | Mutagen MP4 metadata | ~0.2s |
| 14 | `commit_clip_history()` | ~0.1s |
| 15 | **Post-render validation** | ~0.5s |
| 16 | Cleanup temp/ + Summary report | — |
| **ИТОГО** | | **~350–500s (5–8 мин)** |

---

## 3. КОНСТАНТЫ

### Видеоряд
| Константа | Значение | Назначение |
|-----------|---------|-----------|
| `MIN_CUT_INTERVAL` | 1.2s | мин. длительность клипа |
| `MAX_CUT_INTERVAL` | 4.0s | макс. длительность клипа (var-zone) |
| `_FIXED_AFTER_SEC` | 300s | до 5 мин — адаптивный темп, после — фиксированный 5s |
| `SLIDE_DURATION` | 0.5s | default clip-to-clip переход |
| `GLITCH_DURATION` | 0.12s | intro→main переход |
| `INTRO_DURATION` | 90.0s | интро сегменты (без субтитров) |

### Аудио
| Параметр | Значение |
|----------|---------|
| `MUSIC_START_SEC` | 88s (задержка старта музыки) |
| `MUSIC_DB` | -17.9 dB |
| `INTRO_AUDIO_DB` | -6.0 dB |

### SFX
| Параметр | Значение |
|----------|---------|
| `_SFX_TARGET_DB` | -27 dB |
| `_SFX_HARD_LIMIT_DB` | -26 dB |
| `MAX_SFX_PER_MIN` | 3 событий/мин |
| `MAX_SFX_PER_TRANSITION_RATIO` | 0.60 (не более 60% переходов) |
| `SFX_MAX_SHIFT` | 100ms |
| `SFX_DROP_IF_MORE` | 250ms |
| `_SFX_START_BUFFER` | 3.0s после intro |

### Субтитры
| Параметр | Значение |
|----------|---------|
| `SUBTITLE_FONT_SIZE` | 32px (base) |
| `SUBTITLE_FADE_IN_MS` | 120ms |
| `SUBTITLE_RISE_PX` | 20px |

---

## 4. CHANNEL_STYLES.JSON

### channel_001_cosmos_de
| Параметр | Значение |
|----------|---------|
| `intro_transition` | `whip_pan` |
| `clip_transition` | `crossfade` |
| `clip_transition_duration` | 0.5s |
| `sfx_enabled` | true |
| `subtitle_size_offset` | +16px (итого 48px) |
| `subtitle_rise_extra_px` | +30px (итого 50px) |
| `subtitle_border_style` | 3 (opaque box) |
| `color_grade` | `warm_cinematic` |

### channel_002_cosmos_fr
| Параметр | Значение |
|----------|---------|
| `intro_transition` | `zoom_blur` |
| `clip_transition` | `fadeblack` |
| `clip_transition_duration` | 0.3s |
| `sfx_enabled` | true |
| `subtitle_style` | `karaoke` |
| `subtitle_size_offset` | +24px |
| `subtitle_rise_extra_px` | +65px |
| `color_grade` | `cool_cinematic` |

---

## 5. ПЕРЕХОДЫ (transitions.py)

### Intro → Main

| Канал | Функция | Описание |
|-------|---------|---------|
| DE | `smooth_zoom_transition` | scale 1.05x + gblur σ=28 + dissolve |
| FR | `zoom_blur_transition` | zoom 1.0→1.35x + blur, ease-in/out |
| Fallback | `intro_to_main_transition` | gblur σ=30 dissolve |

**whip_pan** (DE intro): свайп влево + boxblur, BLUR_IN=0.2s, BLUR_OUT=0.4s, ease sin²/cos²

### Clip-to-Clip

| Тип | Описание |
|-----|---------|
| `crossfade` | opacity ease-in-out: w = 0.5 − 0.5·cos(π·T/dur) |
| `fadeblack` / `fadewhite` | flash frames (5% chance, 40–80ms) |
| `glitch_flash` | rgbashift + fadeblack, 0.12s |
| `cross_zoom` | FR only, 10% chance |

### Flash Frames (anti-strobing)
- 5% chance на любой переход
- Никогда два подряд
- fadewhite (35%), fadeblack (35%), fade (30%)

---

## 6. SFX СИСТЕМА (sfx_mixer.py + sfx_narrator.py)

### SFX Библиотека
| Папка | Тип | Использование |
|-------|-----|--------------|
| `whoosh/` | стандартный свиш | обычные переходы |
| `whoosh_big/` | тяжёлый свиш | whip_pan, zoom_blur |
| `whoosh_fast/` | лёгкий свиш | smooth_zoom, cross_zoom |
| `impact/` | удар | каждый 5-й переход |
| `glitch/` | глитч | glitch_flash переходы |
| `riser/` | нарастание | за 0.8s ДО перехода |
| `downlifter/` | падение | +0.1s после intro_end |
| `boom/` | кинематографический | каждый 10-й переход |

### Chance по типу перехода
| Переход | Whoosh chance |
|---------|--------------|
| `whip_pan` | 85% |
| `zoom_blur` | 75% |
| `crossfade` | 30% |
| `smooth_zoom` | 60% |
| `glitch_flash` | 100% (glitch) |
| `cross_zoom` | 40% |

### Narrator Analysis (sfx_narrator.py)
Анализирует сценарий по ключевым словам (DE/FR/EN) → тип момента:
- `revelation` → riser (за 1s) + boom
- `buildup` → riser (за 0.5s)
- `question` → whoosh
- `calm` → нет SFX + duck музыки -3dB

Возвращает: `sfx[]` + `music_envelope[]` (динамическая огибающая громкости музыки)

---

## 7. АУДИО МИКШИНГ (audio_mixer.py)

### Три дорожки
| Дорожка | Обработка |
|---------|----------|
| Озвучка | volume=1.0, aresample=44100 |
| Музыка | volume={MUSIC_DB}, adelay={MUSIC_START}ms, fade in 2s / out 4s |
| Интро-аудио | atrim=0:90, volume={INTRO_AUDIO_DB} |

**Финальный mix:** `amix=inputs=N:duration=first:normalize=0`
**Выход:** AAC 192k 44100Hz stereo

---

## 8. СУБТИТРЫ (ass_generator.py)

### Два режима

**generate_ass** (DE — grouped):
- Группы по 3–5 слов
- max_line_chars=28
- fade 120ms + rise 20px (+30px DE)
- border_style=3 (opaque box)

**generate_karaoke_ass** (FR):
- Группы по 3 слова
- Word-by-word highlight через `\kf`
- Активное слово белое, остальные серые
- rise_px=120 (+65px FR)

---

## 9. ВАЛИДАЦИЯ (pipeline_validator.py)

### Level 1 — Pre-render (до encode)
- Все клипы существуют и duration > 0.1s
- SFX события отсортированы по времени
- Сумма переходов < 50% видео
- При failure: Exception → abort

### Level 2 — Post-render (после encode)
- `no_clip_reuse` — нет дубликатов в cooldown
- `sfx_aligned` — SFX desync OK
- `sfx_within_limits` — плотность ≤ MAX_SFX_PER_MIN
- `transitions_valid` — pre-render прошёл
- `fallback_count ≤ 3`
- При failure: `sys.exit(1)`

### Логирование
Structured JSONL в `pipeline_log.jsonl`:
```json
{"t": 1234567890, "type": "clip_trim", "clip": "...", "target": 5.0, "delta": -0.02}
{"t": 1234567891, "type": "sfx_drop", "time": 120.5, "reason": "desync"}
```

---

## 10. NVENC AUTO-TUNING (_NvencPool)

Динамический пул NVENC-слотов (2–5):
- **Spike:** последний encode > 2× EMA → reduce slots
- **Smooth:** каждые 5 сегментов: recent/prev ratio → ±1 slot
- **Зажим:** [2, 5] slots

---

## 11. ТЕМП КЛИПОВ (_dynamic_pace_target)

Парабола по позиции в видео:
```
progress=0.0 (начало)  → 2.0s (быстро)
progress=0.5 (середина) → 3.5s (спокойно)
progress=1.0 (финал)    → 2.0s (быстро)
```
Кривая: `cos(π·progress)` — трейлерный ритм.
Только в var-zone (0–300s). После 300s → фиксированные 5s сегменты.

---

## 12. ИЗВЕСТНЫЕ ПРОБЛЕМЫ И РИСКИ

| Приоритет | Проблема | Статус |
|-----------|---------|--------|
| 🔴 High | Clip reuse detection: flagged, но не enforced в assembler | TODO |
| 🔴 High | Нет GPU memory monitoring → может упасть на длинных видео | TODO |
| 🟡 Medium | Intro duration validation отсутствует (паддинг исправляет, не оптимально) | TODO |
| 🟡 Medium | Parallel thread pool failure: видео может быть неполным без одного компонента | TODO |
| 🟡 Medium | SFX desync tolerance консервативна → много dropов | Monitor |
| 🟢 Low | silence_regions пересчитывается каждый запуск (нет кеша) | Низкий impact |
| 🟢 Low | Color grade применяется только после intro_dur → несоответствие тону | Acceptable |

---

## 13. КРИТИЧЕСКИЕ ТРЕБОВАНИЯ СРЕДЫ

| Требование | Fallback |
|-----------|---------|
| NVIDIA GPU | libx264 (10–20× медленнее) |
| `MUSIC_DIR` env var | **нет fallback** — pipeline упадёт |
| `ORGANETTO_FONT_PATH` | generic "Organetto" |
| FFmpeg в PATH | нет fallback |
| Одинаковый формат клипов (1920×1080, 25fps) | simple_concat без переходов |
| `result.json` в transcripts/ | нет fallback |

---

## 14. ПРОИЗВОДИТЕЛЬНОСТЬ

| Этап | Время | Bottleneck |
|------|-------|-----------|
| detect_silence_regions | 3–5s | ffprobe |
| prepare_trimmed_clips | 60–120s | NVENC GPU |
| concat_all_with_transitions | 120–240s | NVENC + xfade |
| audio prepare | 30–60s | ffmpeg |
| final render | 60–120s | NVENC h264 |
| **ИТОГО** | **~350–500s** | GPU-bound |
