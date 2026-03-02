# Telegram Bot — Video Pipeline

Личный Telegram-бот для управления пайплайном генерации видео.
Отвечает **только** указанному в `.env` user_id.

---

## 1. Получи токен бота

1. Открой **[@BotFather](https://t.me/BotFather)** в Telegram
2. Напиши `/newbot`
3. Придумай имя (например: `My Video Pipeline Bot`)
4. Придумай username с суффиксом `_bot` (например: `my_vidpipe_bot`)
5. Скопируй токен формата:
   ```
   1234567890:AABBccDDeeFFggHHiiJJkkLLmmNNoopp
   ```

---

## 2. Узнай свой Telegram User ID

1. Открой **[@userinfobot](https://t.me/userinfobot)**
2. Нажми `/start`
3. Скопируй число из поля **Id** (например: `123456789`)

---

## 3. Добавь токен в config/.env

```env
TELEGRAM_BOT_TOKEN=1234567890:AABBccDDeeFFggHHiiJJkkLLmmNNoopp
TELEGRAM_ALLOWED_USER_ID=123456789
```

---

## 4. Установи зависимости

```bash
pip install "python-telegram-bot[job-queue]" python-dotenv
```

---

## 5. Запусти бота

```bash
py bot/telegram_bot.py
```

Бот пишет в консоль:
```
[Bot] ✅ Запущен
[Bot] Разрешённый user_id: 123456789
[Bot] Отправь /start или /menu в Telegram
```

---

## Использование

Открой бота в Telegram → `/start` или `/menu`

### Порядок работы:

| Шаг | Кнопка | Что делает |
|-----|--------|------------|
| 1 | 🎙 Транскрипция | Принимает MP3, транскрибирует через Whisper |
| 2 | ✍️ Промпты | Генерирует image prompts через Claude CLI |
| 3 | 🖼 Генерация медиа | Генерирует изображения (Gemini API / браузер) |
| 4 | ✅ Валидация | Проверяет корректность данных |
| — | 📊 Статус | Показывает состояние текущего проекта |

---

## Платформы генерации медиа

### ✅ Работает сразу:
- **Gemini API** (Nano Banana) — нужен `GEMINI_API_KEY` в `.env`

### ⚙️ Требует первый вход вручную:
Для Google Flow, Grok, Runway, Kling — при первом запуске нужно авторизоваться в браузере:

```bash
py agents/media_generator.py
```
Выбери нужную платформу → залогинься в открытом Chrome → нажми Enter.
Куки сохранятся, и дальнейшие запуски пойдут через бота.

---

## Безопасность

- Бот **полностью игнорирует** сообщения от любого user_id, кроме указанного в `.env`
- Токен бота и user_id хранятся в `config/.env` (в `.gitignore`)
- Никакие данные не передаются третьим сторонам
