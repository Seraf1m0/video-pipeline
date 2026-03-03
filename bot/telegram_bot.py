"""
Telegram Bot — Video Pipeline Control Panel
--------------------------------------------
Личный бот — отвечает только разрешённому user_id.

Запуск: py bot/telegram_bot.py
"""

import asyncio
import io
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

# UTF-8 вывод в Windows-терминале (cp1251 не поддерживает эмодзи)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ─── Логирование ─────────────────────────────────────────────────────────────

Path("logs").mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("logs/bot.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)

# ─── Пути ────────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv, dotenv_values

ENV_FILE = BASE_DIR / "config" / ".env"
load_dotenv(ENV_FILE)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
_uid = os.environ.get("TELEGRAM_ALLOWED_USER_ID", "0").strip()
ALLOWED_USER_ID = int(_uid) if _uid.isdigit() else 0

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ParseMode

# ─── Директории ──────────────────────────────────────────────────────────────

INPUT_DIR            = BASE_DIR / "data" / "input"
TRANSCRIPTS_DIR      = BASE_DIR / "data" / "transcripts"
PROMPTS_DIR          = BASE_DIR / "data" / "prompts"
MEDIA_DIR            = BASE_DIR / "data" / "media"
CONFIG_DIR           = BASE_DIR / "config"
AGENTS_DIR           = BASE_DIR / "agents"
PROMPT_PROGRESS_FILE = BASE_DIR / "temp" / "prompt_progress.json"
PIXEL_PROGRESS_FILE  = BASE_DIR / "temp" / "pixel_progress.json"

# ─── Константы платформ ───────────────────────────────────────────────────────

BROWSER_COOKIES = {
    "1": CONFIG_DIR / "flow_cookies.json",
    "2": CONFIG_DIR / "grok_cookies.json",
}

PLATFORM_NAMES = {
    "1": "Google Flow",
    "2": "Grok",
    "3": "PixelAgent",
}

# ─── Глобальный трекер запущенных пайплайнов ─────────────────────────────────

running_pipelines: dict[int, asyncio.Task] = {}

# ─── Auth ─────────────────────────────────────────────────────────────────────

async def is_allowed(update: Update) -> bool:
    uid = update.effective_user.id if update.effective_user else 0
    return uid == ALLOWED_USER_ID

# ─── HTML helpers ─────────────────────────────────────────────────────────────

def h(text: str) -> str:
    """Escape HTML special chars for Telegram HTML mode."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )

# ─── Keyboards ────────────────────────────────────────────────────────────────

BACK_BTN = InlineKeyboardButton("« Главное меню", callback_data="back:main")
KB_STOP  = InlineKeyboardMarkup([
    [InlineKeyboardButton("⏹ Стоп", callback_data="pipeline:stop")],
])


def kb_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Запустить всё",    callback_data="pipeline:start")],
        [InlineKeyboardButton("──────────────────",  callback_data="noop")],
        [InlineKeyboardButton("🎙 Транскрипция",     callback_data="menu:transcription")],
        [InlineKeyboardButton("✍️ Промпты",           callback_data="menu:prompts")],
        [
            InlineKeyboardButton("📷 Фото",          callback_data="menu:photo"),
            InlineKeyboardButton("🎬 Видео",         callback_data="menu:video"),
        ],
        [InlineKeyboardButton("✂️ Нарезка видео",    callback_data="menu:cutter")],
        [InlineKeyboardButton("✅ Валидация",          callback_data="menu:validation")],
        [InlineKeyboardButton("📊 Статус проекта",    callback_data="menu:status")],
    ])


def kb_cut_mode() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎲 3–8 сек (рандом)", callback_data="cut:random"),
            InlineKeyboardButton("📏 10 сек (Grok)",     callback_data="cut:grok"),
        ],
        [BACK_BTN],
    ])


def kb_prompt_type() -> InlineKeyboardMarkup:
    """Выбор типа промптов: фото или видео."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📷 Фото промпты",  callback_data="ptype:photo")],
        [InlineKeyboardButton("🎬 Видео промпты", callback_data="ptype:video")],
        [BACK_BTN],
    ])


def kb_photo_platform() -> InlineKeyboardMarkup:
    """Платформы для генерации фото-промптов."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🖼 PixelAgent", callback_data="pphoto:gemini")],
        [InlineKeyboardButton("🌊 Flow",    callback_data="pphoto:flow")],
        [InlineKeyboardButton("🤖 Grok",    callback_data="pphoto:grok")],
        [BACK_BTN],
    ])


def kb_video_platform() -> InlineKeyboardMarkup:
    """Платформы для генерации видео-промптов."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🤖 Grok",  callback_data="pvideo:grok")],
        [InlineKeyboardButton("🌊 Flow",  callback_data="pvideo:flow")],
        [BACK_BTN],
    ])


def kb_prompt_platform() -> InlineKeyboardMarkup:
    """Устаревший: оставлен для совместимости."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌊 Flow / Veo 3", callback_data="platform:1")],
        [InlineKeyboardButton("🎨 Midjourney",   callback_data="platform:2")],
        [InlineKeyboardButton("🖼 PixelAgent",     callback_data="platform:gemini")],
        [InlineKeyboardButton("✏️ Другое",         callback_data="platform:other")],
        [BACK_BTN],
    ])


def kb_media_platform() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🖼 PixelAgent",   callback_data="mplatform:3")],
        [InlineKeyboardButton("🤖 Grok",         callback_data="mplatform:2")],
        [InlineKeyboardButton("🌊 Google Flow",  callback_data="mplatform:1")],
        [BACK_BTN],
    ])


def kb_media_type(is_api: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("📷 Фото",  callback_data="mtype:photo")],
        [InlineKeyboardButton("🎬 Видео", callback_data="mtype:video")],
    ]
    if not is_api:
        rows.append([InlineKeyboardButton("📷+🎬 Фото + Видео", callback_data="mtype:both")])
    rows.append([BACK_BTN])
    return InlineKeyboardMarkup(rows)




def kb_validation() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎙 Транскрипцию", callback_data="validate:transcription")],
        [InlineKeyboardButton("✍️ Промпты",       callback_data="validate:prompts")],
        [InlineKeyboardButton("🔍 Всё сразу",     callback_data="validate:all")],
        [BACK_BTN],
    ])


def kb_done() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[BACK_BTN]])


def kb_cutter_mode() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✂️ Только нарезка",      callback_data="cutter:cut")],
        [InlineKeyboardButton("✂️+🔍 Нарезка + Апскейл", callback_data="cutter:cut+upscale")],
        [BACK_BTN],
    ])


def kb_cutter_method() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🤖 Real-ESRGAN (AI, GPU)", callback_data="cupscale:realesrgan")],
        [InlineKeyboardButton("⚡ Lanczos (быстро)",       callback_data="cupscale:lanczos")],
        [InlineKeyboardButton("💨 Bicubic (очень быстро)", callback_data="cupscale:bicubic")],
        [BACK_BTN],
    ])


def kb_cutter_resolution() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("720p",  callback_data="cres:720"),
            InlineKeyboardButton("1080p", callback_data="cres:1080"),
        ],
        [
            InlineKeyboardButton("2K",    callback_data="cres:2k"),
            InlineKeyboardButton("4K",    callback_data="cres:4k"),
        ],
        [BACK_BTN],
    ])


def kb_pipeline_cutter() -> InlineKeyboardMarkup:
    """Вопрос в конце пайплайна — нарезать видео?"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✂️+🔍 Да, с апскейлом",  callback_data="pcutter:upscale")],
        [InlineKeyboardButton("✂️ Да, без апскейла",     callback_data="pcutter:cut")],
        [InlineKeyboardButton("⏭ Пропустить",            callback_data="pcutter:skip")],
    ])


# ── Pipeline keyboards ────────────────────────────────────────────────────────

def kb_p_cut() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎲 3–8 сек (рандом)", callback_data="pcut:random"),
            InlineKeyboardButton("📏 10 сек (Grok)",     callback_data="pcut:grok"),
        ],
        [BACK_BTN],
    ])


def kb_p_prompt_platform() -> InlineKeyboardMarkup:
    """Платформа для фото-промптов в пайплайне."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🖼 PixelAgent", callback_data="ppform:gemini")],
        [InlineKeyboardButton("🌊 Flow",    callback_data="ppform:flow")],
        [InlineKeyboardButton("🤖 Grok",    callback_data="ppform:grok")],
        [BACK_BTN],
    ])


def kb_p_media_platform() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🖼 PixelAgent",   callback_data="pmedia:3")],
        [InlineKeyboardButton("🤖 Grok",         callback_data="pmedia:2")],
        [InlineKeyboardButton("🌊 Google Flow",  callback_data="pmedia:1")],
        [BACK_BTN],
    ])




# ─── Статус проекта ───────────────────────────────────────────────────────────

def get_project_status() -> str:
    """Собирает состояние последнего проекта."""
    _SESSION_RE = re.compile(r"^Video_\d{8}_\d{6}$")
    session = None
    if INPUT_DIR.exists():
        folders = [
            d for d in INPUT_DIR.iterdir()
            if d.is_dir() and _SESSION_RE.match(d.name)
        ]
        if folders:
            session = max(folders, key=lambda f: f.name).name

    if not session:
        return (
            "📂 <b>Нет активных проектов</b>\n\n"
            "Загрузи MP3 через 🎙 Транскрипция"
        )

    lines = [f"📁 <b>Проект:</b> <code>{h(session)}</code>\n"]

    tf = TRANSCRIPTS_DIR / session / "result.json"
    if tf.exists():
        try:
            segs = json.loads(tf.read_text(encoding="utf-8"))
            dur = segs[-1]["end"] if segs else 0
            lines.append(f"🎙 Транскрипция: ✅ {len(segs)} сегм. ({dur:.0f}s)")
        except Exception:
            lines.append("🎙 Транскрипция: ⚠️ ошибка файла")
    else:
        lines.append("🎙 Транскрипция: ❌ нет")

    pf = PROMPTS_DIR / session / "photo_prompts.json"
    total_pr = 0
    if pf.exists():
        try:
            pr = json.loads(pf.read_text(encoding="utf-8"))
            total_pr = len(pr)
            lines.append(f"✍️ Промпты: ✅ {total_pr} промптов")
        except Exception:
            lines.append("✍️ Промпты: ⚠️ ошибка файла")
    else:
        lines.append("✍️ Промпты: ❌ нет")

    photos_dir = MEDIA_DIR / session / "photos"
    videos_dir = MEDIA_DIR / session / "videos"
    total_str  = str(total_pr) if total_pr else "?"

    if photos_dir.exists():
        n = len(list(photos_dir.glob("photo_*.*")))
        icon = "✅" if n and n == total_pr else "⏳"
        lines.append(f"📷 Фото: {icon} {n}/{total_str}")
    else:
        lines.append("📷 Фото: ❌ нет")

    if videos_dir.exists():
        n = len(list(videos_dir.glob("video_*.*")))
        icon = "✅" if n and n == total_pr else "⏳"
        lines.append(f"🎬 Видео: {icon} {n}/{total_str}")

    vf = TRANSCRIPTS_DIR / session / "validation_report.json"
    if vf.exists():
        try:
            rep = json.loads(vf.read_text(encoding="utf-8"))
            ok  = rep.get("passed", False)
            lines.append(f"✅ Валидация: {'✅ пройдена' if ok else '❌ есть ошибки'}")
        except Exception:
            lines.append("✅ Валидация: ⚠️ ошибка файла")
    else:
        lines.append("✅ Валидация: ❌ не запускалась")

    return "\n".join(lines)

# ─── Agent runner ─────────────────────────────────────────────────────────────

async def run_agent(
    args: list[str],
    stdin_text: str | None = None,
    on_line=None,
) -> tuple[int, str]:
    """
    Запускает агент subprocess, стримит stdout построчно через on_line.
    При asyncio.CancelledError — убивает процесс и пробрасывает исключение.
    Возвращает (returncode, full_output).
    """
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(BASE_DIR),
        env=env,
    )

    if stdin_text:
        proc.stdin.write(stdin_text.encode("utf-8"))
        await proc.stdin.drain()
    proc.stdin.close()

    lines: list[str] = []
    try:
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                break
            text = raw.decode("utf-8", errors="replace").rstrip()
            lines.append(text)
            if on_line and text:
                try:
                    await on_line(text)
                except Exception:
                    pass
    except asyncio.CancelledError:
        try:
            proc.kill()
        except Exception:
            pass
        await proc.wait()
        raise

    await proc.wait()
    return proc.returncode, "\n".join(lines)


def make_progress_cb(msg, header: str, interval: float = 2.5, kb=None):
    """
    Возвращает async-callback, который раз в interval секунд
    обновляет сообщение msg с последними строками прогресса.
    """
    state = {"last": 0.0, "buf": []}

    async def cb(line: str):
        state["buf"].append(line)
        now = time.monotonic()
        if now - state["last"] >= interval:
            state["last"] = now
            preview = h("\n".join(state["buf"][-12:]))
            try:
                await msg.edit_text(
                    f"{header}\n\n<pre>{preview}</pre>",
                    reply_markup=kb,
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass

    return cb


def _prompt_bar(current: int, total: int) -> str:
    filled = int(current / total * 16) if total else 0
    return "█" * filled + "░" * (16 - filled)


def _read_prompt_progress() -> dict | None:
    try:
        return json.loads(PROMPT_PROGRESS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_pixel_progress() -> dict | None:
    try:
        return json.loads(PIXEL_PROGRESS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


# ─── Общие агент-функции ──────────────────────────────────────────────────────

async def _run_prompts_agent(progress_msg, stdin: str, label: str,
                             photo_platform: str = "gemini") -> None:
    # Сбрасываем старый прогресс
    try:
        PROMPT_PROGRESS_FILE.unlink(missing_ok=True)
    except Exception:
        pass

    task = asyncio.create_task(run_agent(
        ["py", str(AGENTS_DIR / "prompt_generator" / "prompt_generator.py"),
         "--type", "photo",
         "--photo-master", "photo_master_prompt.txt",
         "--photo-platform", photo_platform],
    ))

    t0 = time.monotonic()
    while not task.done():
        await asyncio.sleep(3.0)
        p = _read_prompt_progress()
        if p and p.get("total", 0) > 0:
            cur   = p.get("current", 0)
            total = p["total"]
            bar   = _prompt_bar(cur, total)
            pct   = int(cur / total * 100)
            master = p.get("master_photo", "photo_master_prompt.txt")
            now_t  = p.get("current_text", "")
            text = (
                f"⏳ <b>Генерирую фото-промпты...</b>\n"
                f"📋 Мастер-промпт: <code>{h(master)}</code>\n"
                f"🎯 Платформа: {h(label)}\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"📊 Промптов готово: <b>{cur}/{total}</b>\n"
                f"<code>{bar}</code> {pct}%"
            )
            if now_t:
                text += f"\n\n🔄 Сейчас: {h(now_t[:55])}..."
        else:
            elapsed = int(time.monotonic() - t0)
            text = (
                f"⏳ <b>Генерирую фото-промпты...</b>\n"
                f"🎯 Платформа: {h(label)}\n\n"
                f"<pre>запускаю... ({elapsed}s)</pre>"
            )
        try:
            await progress_msg.edit_text(text, parse_mode=ParseMode.HTML)
        except Exception:
            pass

    rc, output = task.result()

    seg_m  = re.search(r"Сегментов:\s*(\d+)", output)
    prom_m = re.search(r"Фото промпты готовы:\s*(\d+)", output, re.IGNORECASE)
    seg_count  = seg_m.group(1)  if seg_m  else "?"
    prom_count = prom_m.group(1) if prom_m else "?"

    if rc != 0:
        err_tail = "\n".join(output.splitlines()[-15:])
        await progress_msg.edit_text(
            f"❌ <b>Ошибка генерации промптов!</b>\n"
            f"📋 Сегментов: <b>{seg_count}</b>\n\n"
            f"<pre>{h(err_tail)}</pre>",
            reply_markup=kb_done(),
            parse_mode=ParseMode.HTML,
        )
        return

    await progress_msg.edit_text(
        f"✅ <b>Фото промпты готовы!</b>\n"
        f"📋 Сегментов: <b>{seg_count}</b>\n"
        f"✍️ Промптов: <b>{prom_count}</b>\n"
        f"🖹 Файл: <code>photo_prompts.txt</code>",
        reply_markup=kb_done(),
        parse_mode=ParseMode.HTML,
    )


async def _run_video_prompts_agent(progress_msg, stdin: str, label: str,
                                    video_platform: str = "grok") -> None:
    try:
        PROMPT_PROGRESS_FILE.unlink(missing_ok=True)
    except Exception:
        pass

    task = asyncio.create_task(run_agent(
        ["py", str(AGENTS_DIR / "prompt_generator" / "prompt_generator.py"),
         "--type", "video",
         "--video-master", "master_video_grok.txt",
         "--video-platform", video_platform],
    ))

    t0 = time.monotonic()
    while not task.done():
        await asyncio.sleep(3.0)
        p = _read_prompt_progress()
        if p and p.get("total", 0) > 0:
            cur   = p.get("current", 0)
            total = p["total"]
            bar   = _prompt_bar(cur, total)
            pct   = int(cur / total * 100)
            master = p.get("master_video", "master_video_grok.txt")
            now_t  = p.get("current_text", "")
            text = (
                f"⏳ <b>Генерирую видео-промпты...</b>\n"
                f"📋 Мастер-промпт: <code>{h(master)}</code>\n"
                f"🤖 Платформа: {h(label)}\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"📊 Промптов готово: <b>{cur}/{total}</b>\n"
                f"<code>{bar}</code> {pct}%"
            )
            if now_t:
                text += f"\n\n🔄 Сейчас: {h(now_t[:55])}..."
        else:
            elapsed = int(time.monotonic() - t0)
            text = (
                f"⏳ <b>Генерирую видео-промпты...</b>\n"
                f"🤖 Платформа: {h(label)}\n\n"
                f"<pre>запускаю... ({elapsed}s)</pre>"
            )
        try:
            await progress_msg.edit_text(text, parse_mode=ParseMode.HTML)
        except Exception:
            pass

    rc, output = task.result()

    seg_m  = re.search(r"Сегментов:\s*(\d+)", output)
    prom_m = re.search(r"Видео промпты готовы:\s*(\d+)", output, re.IGNORECASE)
    seg_count  = seg_m.group(1)  if seg_m  else "?"
    prom_count = prom_m.group(1) if prom_m else "?"

    if rc != 0:
        err_tail = "\n".join(output.splitlines()[-15:])
        await progress_msg.edit_text(
            f"❌ <b>Ошибка генерации видео-промптов!</b>\n"
            f"📋 Сегментов: <b>{seg_count}</b>\n\n"
            f"<pre>{h(err_tail)}</pre>",
            reply_markup=kb_done(),
            parse_mode=ParseMode.HTML,
        )
        return

    await progress_msg.edit_text(
        f"✅ <b>Видео промпты готовы!</b>\n"
        f"📋 Сегментов: <b>{seg_count}</b>\n"
        f"✍️ Промптов: <b>{prom_count}</b>\n"
        f"🖹 Файл: <code>video_prompts.txt</code>",
        reply_markup=kb_done(),
        parse_mode=ParseMode.HTML,
    )


async def _run_combined_prompts_agent(progress_msg) -> None:
    """Запускает prompt_generator.py — генерирует фото + видео промпты за один проход."""
    try:
        PROMPT_PROGRESS_FILE.unlink(missing_ok=True)
    except Exception:
        pass

    task = asyncio.create_task(run_agent(
        ["py", str(AGENTS_DIR / "prompt_generator" / "prompt_generator.py"),
         "--type", "both",
         "--photo-master", "photo_master_prompt.txt",
         "--video-master", "master_video_grok.txt"],
    ))

    t0 = time.monotonic()
    while not task.done():
        await asyncio.sleep(3.0)
        p = _read_prompt_progress()
        if p and p.get("total", 0) > 0:
            cur    = p.get("current", 0)
            total  = p["total"]
            bar    = _prompt_bar(cur, total)
            pct    = int(cur / total * 100)
            ptype  = p.get("type", "photo")
            icon   = "📷" if ptype == "photo" else "🎬"
            now_t  = p.get("current_text", "")
            text = (
                f"⏳ <b>Генерирую промпты...</b>\n"
                f"{icon} Этап: {'Фото' if ptype == 'photo' else 'Видео'}\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"📊 Готово: <b>{cur}/{total}</b>\n"
                f"<code>{bar}</code> {pct}%"
            )
            if now_t:
                text += f"\n\n🔄 Сейчас: {h(now_t[:55])}..."
        else:
            elapsed = int(time.monotonic() - t0)
            text = (
                f"⏳ <b>Генерирую промпты...</b>\n"
                f"📷 Фото + 🎬 Видео\n\n"
                f"<pre>запускаю... ({elapsed}s)</pre>"
            )
        try:
            await progress_msg.edit_text(text, parse_mode=ParseMode.HTML)
        except Exception:
            pass

    rc, output = task.result()

    seg_m   = re.search(r"Сегментов:\s*(\d+)", output)
    photo_m = re.search(r"Фото промпты готовы:\s*(\d+)", output, re.IGNORECASE)
    video_m = re.search(r"Видео промпты готовы:\s*(\d+)", output, re.IGNORECASE)
    seg_count   = seg_m.group(1)   if seg_m   else "?"
    photo_count = photo_m.group(1) if photo_m else "?"
    video_count = video_m.group(1) if video_m else "?"

    if rc != 0:
        err_tail = "\n".join(output.splitlines()[-15:])
        await progress_msg.edit_text(
            f"❌ <b>Ошибка генерации промптов!</b>\n"
            f"📋 Сегментов: <b>{seg_count}</b>\n\n"
            f"<pre>{h(err_tail)}</pre>",
            reply_markup=kb_done(),
            parse_mode=ParseMode.HTML,
        )
        return

    await progress_msg.edit_text(
        f"✅ <b>Промпты готовы!</b>\n"
        f"📋 Сегментов: <b>{seg_count}</b>\n"
        f"📷 Фото [Google Nano]: <b>{photo_count}</b>\n"
        f"🎬 Видео [Grok]: <b>{video_count}</b>",
        reply_markup=kb_done(),
        parse_mode=ParseMode.HTML,
    )


async def _run_media_agent(
    progress_msg,
    stdin: str,          # не используется, оставлен для совместимости
    platform_num: str,
    mtype: str,
    model_num: str = "",
    session: str = "",
) -> None:
    pname      = PLATFORM_NAMES.get(platform_num, platform_num)
    type_icons = {"photo": "📷 Фото", "video": "🎬 Видео", "both": "📷+🎬 Фото + Видео"}
    type_label = type_icons.get(mtype, mtype)

    cmd = ["py", str(AGENTS_DIR / "media_generator" / "media_generator.py"),
           "--platform", platform_num,
           "--type", mtype]
    if session:
        cmd += ["--session", session]

    if platform_num == "3":
        # ── PixelAgent: polling из pixel_progress.json ──────────────────────
        try:
            PIXEL_PROGRESS_FILE.unlink(missing_ok=True)
        except Exception:
            pass

        task = asyncio.create_task(run_agent(cmd))
        t0   = time.monotonic()

        while not task.done():
            await asyncio.sleep(3.0)
            p = _read_pixel_progress()
            if p and p.get("total", 0) > 0:
                cur     = p.get("current", 0)
                total   = p["total"]
                failed  = p.get("failed", [])
                threads = p.get("threads", 3)
                status  = p.get("status", "running")

                if status == "autoretry":
                    ar_round = p.get("autoretry_round", 1)
                    ar_done  = p.get("autoretry_done", 0)
                    ar_total = p.get("autoretry_total", 1)
                    bar = _prompt_bar(ar_done, ar_total)
                    pct = int(ar_done / ar_total * 100) if ar_total else 0
                    text = (
                        f"🔄 <b>Автоповтор #{ar_round}/3...</b>\n"
                        f"🖼 {h(pname)}\n"
                        f"━━━━━━━━━━━━━━━━\n"
                        f"📊 Фото: <b>{ar_done}/{ar_total}</b>\n"
                        f"<code>{bar}</code> {pct}%"
                    )
                else:
                    bar = _prompt_bar(cur, total)
                    pct = int(cur / total * 100)
                    text = (
                        f"⏳ <b>Генерирую фото...</b>\n"
                        f"🖼 {h(pname)} ({threads} потоков)\n"
                        f"━━━━━━━━━━━━━━━━\n"
                        f"📊 Готово: <b>{cur}/{total}</b>\n"
                        f"<code>{bar}</code> {pct}%"
                    )
                    if failed:
                        text += f"\n⚠️ Ошибок: {len(failed)}"
            else:
                elapsed = int(time.monotonic() - t0)
                text = (
                    f"⏳ <b>Генерирую фото...</b>\n"
                    f"🖼 {h(pname)}\n\n"
                    f"<pre>запускаю... ({elapsed}s)</pre>"
                )
            try:
                await progress_msg.edit_text(text, parse_mode=ParseMode.HTML)
            except Exception:
                pass

        rc, output = task.result()

        p_fin    = _read_pixel_progress()
        done_n   = p_fin.get("current", 0) if p_fin else 0
        total_n  = p_fin.get("total", 0)   if p_fin else 0
        fail_lst = p_fin.get("failed", []) if p_fin else []
        failed_n = len(fail_lst)
        elapsed  = int(time.monotonic() - t0)
        icon     = "✅" if rc == 0 and not failed_n else ("⚠️" if done_n else "❌")
        fail_txt = (
            f"\n⚠️ После 3 кругов retry осталось {failed_n} ошибок: {fail_lst}"
            if failed_n else ""
        )
        await progress_msg.edit_text(
            f"{icon} <b>Генерация завершена!</b>\n"
            f"🖼 {h(pname)}\n"
            f"📊 Сохранено: <b>{done_n}/{total_n}</b>"
            + fail_txt
            + f"\n⏱ Время: {elapsed // 60}м {elapsed % 60}с",
            reply_markup=kb_done(),
            parse_mode=ParseMode.HTML,
        )
    else:
        # ── Browser-платформы (Flow, Grok): streaming ───────────────────────
        header = f"⏳ <b>Генерирую медиа...</b>\n{type_label} | {h(pname)}"
        cb     = make_progress_cb(progress_msg, header, interval=2.0)
        rc, output = await run_agent(cmd, on_line=cb)

        gen_m   = re.search(r"Сгенерировано:\s*(\d+)", output, re.IGNORECASE)
        total_m = re.search(r"Всего:\s*(\d+)",          output, re.IGNORECASE)
        gen_count = gen_m.group(1)   if gen_m   else "?"
        total_v   = total_m.group(1) if total_m else "?"

        icon = "✅" if rc == 0 else "❌"
        await progress_msg.edit_text(
            f"{icon} <b>Генерация завершена!</b>\n"
            f"{type_label} | {h(pname)}\n"
            f"🖼 Сохранено: <b>{gen_count}/{total_v}</b>",
            reply_markup=kb_done(),
            parse_mode=ParseMode.HTML,
        )

async def _run_cutter_agent(
    progress_msg,
    mode: str,
    method: str = "",
    resolution: str = "",
    session: str = "",
) -> None:
    """Запускает video_cutter.py и показывает прогресс."""
    res_labels = {"720": "720p", "1080": "1080p", "2k": "2K", "4k": "4K"}
    method_labels = {"realesrgan": "Real-ESRGAN", "lanczos": "Lanczos", "bicubic": "Bicubic"}

    if mode == "cut+upscale":
        header = (
            f"⏳ <b>Нарезка + Апскейл...</b>\n"
            f"Метод: {method_labels.get(method, method)} → {res_labels.get(resolution, resolution)}"
        )
    else:
        header = "⏳ <b>Нарезка видео...</b>"

    cb = make_progress_cb(progress_msg, header, interval=3.0)

    # Строим stdin: mode\nmethod\nresolution\n (агент читает интерактивно только без аргументов)
    # Передаём через аргументы командной строки
    cmd = ["py", str(AGENTS_DIR / "video_cutter" / "video_cutter.py"), "--mode", mode]
    if session:
        cmd += ["--project", session]
    if mode == "cut+upscale":
        cmd += ["--method", method, "--resolution", resolution]

    rc, output = await run_agent(cmd, on_line=cb)

    cut_m = re.search(r"Нарезано[:\s]+(\d+)", output)
    up_m  = re.search(r"Апскейл[:\s]+(\d+)", output)
    time_m = re.search(r"Общее время[:\s]+([\w\s]+)", output)

    cut_count  = cut_m.group(1)  if cut_m  else "?"
    up_count   = up_m.group(1)   if up_m   else None
    total_time = time_m.group(1).strip() if time_m else "?"

    icon = "✅" if rc == 0 else "⚠️"
    lines = [
        f"{icon} <b>Нарезка завершена!</b>",
        f"✂️ Нарезано: <b>{cut_count}</b> клипов",
    ]
    if up_count:
        lines.append(f"🔍 Апскейл: <b>{up_count}</b> клипов → {res_labels.get(resolution, '')}")
    lines.append(f"⏱ Время: {h(total_time)}")

    await progress_msg.edit_text(
        "\n".join(lines),
        reply_markup=kb_done(),
        parse_mode=ParseMode.HTML,
    )


# ─── Утилиты ──────────────────────────────────────────────────────────────────

def _find_mp3_in_input() -> Path | None:
    """Ищет MP3/WAV прямо в data/input/ (не в подпапках)."""
    if not INPUT_DIR.exists():
        return None
    for ext in ("*.mp3", "*.wav"):
        files = [f for f in INPUT_DIR.glob(ext) if f.parent == INPUT_DIR]
        if files:
            return files[0]
    return None

# ═══════════════════════════════════════════════════════════════════════════════
# PIPELINE — полный авто-запуск всех этапов
# ═══════════════════════════════════════════════════════════════════════════════

async def run_pipeline(user_id: int, msg, cfg: dict) -> None:
    """
    Запускает 4 этапа последовательно.
    cfg ключи: cut_stdin, prompt_stdin, prompt_label,
               media_label, media_platform_num
    """
    t0          = time.monotonic()
    completed   : list[str] = []   # строки завершённых этапов
    session_name = None
    seg_count    = 0
    prom_count   = 0
    photo_count  = 0

    def build_status(current: str = "", preview: str = "") -> str:
        parts = list(completed)
        if current:
            parts.append(current)
        if preview:
            parts.append(f"<pre>{h(preview)}</pre>")
        return "\n".join(parts) if parts else "<i>инициализация...</i>"

    async def safe_edit(text: str, kb=KB_STOP) -> None:
        try:
            await msg.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
        except Exception:
            pass

    try:
        # ══ ЭТАП 1 — ТРАНСКРИПЦИЯ ════════════════════════════════════════════
        hdr1 = "⏳ <b>Этап 1/4 — Транскрипция...</b>"
        await safe_edit(build_status(hdr1))

        buf1, last1 = [], [0.0]

        async def on1(line: str) -> None:
            buf1.append(line)
            now = time.monotonic()
            if now - last1[0] >= 2.5:
                last1[0] = now
                await safe_edit(build_status(hdr1, "\n".join(buf1[-10:])))

        cut_mode = cfg.get("cut_mode", "random")
        cmd1 = ["py", str(AGENTS_DIR / "transcriber" / "transcriber.py"), "--mode", cut_mode]
        if cfg.get("audio_input_path"):
            cmd1 += ["--input", cfg["audio_input_path"]]
        rc1, out1 = await run_agent(cmd1, on_line=on1)

        if rc1 != 0:
            await safe_edit(
                build_status() + f"\n❌ <b>Этап 1/4 — Транскрипция провалилась!</b>\n"
                f"<pre>{h(chr(10).join(buf1[-8:]))}</pre>",
                kb_done(),
            )
            return

        m = re.search(r"(\d+)\s+сегментов", out1)
        seg_count = int(m.group(1)) if m else 0
        m2 = re.search(r"Video_\d{8}_\d{6}", out1)
        session_name = m2.group(0) if m2 else None

        completed.append(f"✅ <b>Этап 1/4 — Транскрипция:</b> {seg_count} сегм.")
        await safe_edit(build_status())
        await asyncio.sleep(0.5)

        # ══ ЭТАП 2 — ПРОМПТЫ ═════════════════════════════════════════════════
        hdr2 = f"⏳ <b>Этап 2/4 — Промпты</b> ({h(cfg['prompt_label'])})..."
        await safe_edit(build_status(hdr2))

        try:
            PROMPT_PROGRESS_FILE.unlink(missing_ok=True)
        except Exception:
            pass

        photo_platform = cfg.get("photo_platform", "gemini")
        task2 = asyncio.create_task(run_agent(
            ["py", str(AGENTS_DIR / "prompt_generator" / "prompt_generator.py"),
             "--type", "photo",
             "--photo-master", "photo_master_prompt.txt",
             "--photo-platform", photo_platform],
        ))

        while not task2.done():
            await asyncio.sleep(3.0)
            p = _read_prompt_progress()
            if p and p.get("total", 0) > 0:
                cur   = p.get("current", 0)
                total = p["total"]
                bar   = _prompt_bar(cur, total)
                pct   = int(cur / total * 100)
                prog  = f"📊 <b>{cur}/{total}</b> <code>{bar}</code> {pct}%"
            else:
                prog  = "<pre>запускаю...</pre>"
            await safe_edit(build_status(hdr2, prog))

        rc2, out2 = task2.result()

        if rc2 != 0:
            await safe_edit(
                build_status() + f"\n❌ <b>Этап 2/4 — Промпты провалились!</b>\n"
                f"<pre>{h(chr(10).join(out2.splitlines()[-6:]))}</pre>",
                kb_done(),
            )
            return

        pm = re.search(r"Фото промпты готовы:\s*(\d+)", out2, re.IGNORECASE)
        prom_count = int(pm.group(1)) if pm else 0

        completed.append(f"✅ <b>Этап 2/4 — Промпты:</b> {prom_count} промптов")
        await safe_edit(build_status())
        await asyncio.sleep(0.5)

        # ══ ЭТАП 3 — ВАЛИДАЦИЯ ═══════════════════════════════════════════════
        hdr3 = "⏳ <b>Этап 3/4 — Валидация...</b>"
        await safe_edit(build_status(hdr3))

        rc3, out3 = await run_agent(
            ["py", str(AGENTS_DIR / "validator" / "validator.py")],
            stdin_text=None,
            on_line=None,
        )

        passed = "Валидация пройдена" in out3 and "ОШИБКА:" not in out3

        if not passed:
            err_lines = [l for l in out3.splitlines()
                         if "ОШИБКА:" in l or "❌" in l]
            err_text = "\n".join(err_lines[:12]) if err_lines else out3[-600:]
            await safe_edit(
                build_status() + f"\n❌ <b>Этап 3/4 — Найдены ошибки!</b>\n"
                f"<pre>{h(err_text)}</pre>\n\n"
                f"<i>Исправь и запусти пайплайн снова.</i>",
                kb_done(),
            )
            return

        completed.append("✅ <b>Этап 3/4 — Валидация:</b> пройдена")
        await safe_edit(build_status())
        await asyncio.sleep(0.5)

        # ══ ЭТАП 4 — ГЕНЕРАЦИЯ МЕДИА ═════════════════════════════════════════
        hdr4 = f"⏳ <b>Этап 4/4 — Генерация медиа</b> ({h(cfg['media_label'])})..."
        await safe_edit(build_status(hdr4))

        media_platform = cfg.get("media_platform_num", "3")
        cmd4 = ["py", str(AGENTS_DIR / "media_generator" / "media_generator.py"),
                "--platform", media_platform,
                "--type", "photo"]
        if session_name:
            cmd4 += ["--session", session_name]

        if media_platform == "3":
            # PixelAgent — polling
            try:
                PIXEL_PROGRESS_FILE.unlink(missing_ok=True)
            except Exception:
                pass

            task4 = asyncio.create_task(run_agent(cmd4))

            while not task4.done():
                await asyncio.sleep(3.0)
                p = _read_pixel_progress()
                if p and p.get("total", 0) > 0:
                    cur    = p.get("current", 0)
                    total  = p["total"]
                    status = p.get("status", "running")
                    fail   = len(p.get("failed", []))

                    if status == "autoretry":
                        ar_round = p.get("autoretry_round", 1)
                        ar_done  = p.get("autoretry_done", 0)
                        ar_total = p.get("autoretry_total", 1)
                        bar = _prompt_bar(ar_done, ar_total)
                        pct = int(ar_done / ar_total * 100) if ar_total else 0
                        prog = (
                            f"🔄 <b>Автоповтор #{ar_round}/3</b> "
                            f"<b>{ar_done}/{ar_total}</b> <code>{bar}</code> {pct}%"
                        )
                    else:
                        bar  = _prompt_bar(cur, total)
                        pct  = int(cur / total * 100)
                        prog = f"📊 <b>{cur}/{total}</b> <code>{bar}</code> {pct}%" + (f" ⚠️{fail}" if fail else "")
                else:
                    prog = "<pre>запускаю...</pre>"
                await safe_edit(build_status(hdr4, prog))

            rc4, out4 = task4.result()
        else:
            # Browser-платформа — streaming
            last_photo_n   = [0]
            last_media_upd = [0.0]
            cur_photo      = [0]
            tot_photo      = [seg_count or 1]

            async def on4(line: str) -> None:
                m4 = re.search(r"Генерирую\s+\S+\s+(\d+)/(\d+)", line)
                if m4:
                    cur_photo[0] = int(m4.group(1))
                    tot_photo[0] = int(m4.group(2))
                    now   = time.monotonic()
                    delta = cur_photo[0] - last_photo_n[0]
                    if delta >= 10 or (now - last_media_upd[0] >= 30 and delta >= 1):
                        last_photo_n[0]   = cur_photo[0]
                        last_media_upd[0] = now
                        prog_line = (
                            f"🖼 <b>Этап 4/4 — Генерация:</b> "
                            f"{cur_photo[0]}/{tot_photo[0]} фото..."
                        )
                        await safe_edit(build_status(prog_line))

            rc4, out4 = await run_agent(cmd4, on_line=on4)

        gm = re.search(r"Сгенерировано:\s*(\d+)", out4, re.IGNORECASE)
        photo_count = int(gm.group(1)) if gm else cur_photo[0]

        # Извлекаем имя сессии если не нашли раньше
        if not session_name:
            for out in (out1, out2, out3, out4):
                m_s = re.search(r"Video_\d{8}_\d{6}", out)
                if m_s:
                    session_name = m_s.group(0)
                    break

        completed.append(
            f"{'✅' if rc4 == 0 else '⚠️'} <b>Этап 4/4 — Медиа:</b> {photo_count} фото"
        )

        # ══ ФИНАЛ — предлагаем нарезку видео ════════════════════════════════
        elapsed = time.monotonic() - t0
        mins, secs = divmod(int(elapsed), 60)

        await safe_edit(
            f"✅ <b>Пайплайн завершён! 🎉</b>\n\n"
            f"📁 Проект: <code>{h(session_name or '?')}</code>\n"
            f"🎙 {seg_count} сегментов\n"
            f"✍️ {prom_count} промптов\n"
            f"🖼 {photo_count} фото\n"
            f"⏱ Основной пайплайн: {mins} мин {secs} сек\n\n"
            f"<b>Нарезать исходное видео по сегментам?</b>",
            kb_pipeline_cutter(),
        )

    except asyncio.CancelledError:
        elapsed = time.monotonic() - t0
        mins, secs = divmod(int(elapsed), 60)
        done_text = "\n".join(completed) if completed else "<i>ни один этап не завершён</i>"
        try:
            await msg.edit_text(
                f"⏹ <b>Пайплайн остановлен</b>\n\n"
                f"{done_text}\n\n"
                f"⏱ Прошло: {mins} мин {secs} сек",
                reply_markup=kb_done(),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
        raise

    finally:
        running_pipelines.pop(user_id, None)


# ─── Pipeline setup handlers ──────────────────────────────────────────────────

async def cb_pipeline_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_allowed(update):
        return
    q       = update.callback_query
    await q.answer()
    user_id = update.effective_user.id

    # Уже запущен?
    task = running_pipelines.get(user_id)
    if task and not task.done():
        await q.edit_message_text(
            "🚀 <b>Пайплайн уже запущен!</b>\n\n" + get_project_status(),
            reply_markup=KB_STOP,
            parse_mode=ParseMode.HTML,
        )
        return

    # Проверяем наличие MP3
    mp3 = _find_mp3_in_input()
    if not mp3:
        await q.edit_message_text(
            "❌ <b>MP3 файл не найден!</b>\n\n"
            "Положи файл в папку <code>data/input/</code> и нажми снова.\n"
            "(или загрузи через 🎙 Транскрипция)",
            reply_markup=InlineKeyboardMarkup([[BACK_BTN]]),
            parse_mode=ParseMode.HTML,
        )
        return

    # Инициализируем конфиг пайплайна
    context.user_data["pipeline_cfg"] = {"audio_input_path": str(mp3)}

    await q.edit_message_text(
        f"🚀 <b>Запустить всё</b>\n\n"
        f"📁 Файл: <code>{h(mp3.name)}</code>\n\n"
        f"<b>Шаг 1/3 — Режим нарезки:</b>",
        reply_markup=kb_p_cut(),
        parse_mode=ParseMode.HTML,
    )


async def cb_pcut(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pipeline: выбор режима нарезки."""
    if not await is_allowed(update):
        return
    q    = update.callback_query
    await q.answer()
    mode = q.data.split(":")[1]   # "random" or "grok"
    cfg  = context.user_data.setdefault("pipeline_cfg", {})
    cfg["cut_mode"]  = mode
    cfg["cut_stdin"] = "1\n" if mode == "random" else "2\n"
    cfg["cut_label"] = "3–8 сек" if mode == "random" else "10 сек (Grok)"

    await q.edit_message_text(
        f"🚀 <b>Запустить всё</b>\n\n"
        f"✅ Нарезка: {cfg['cut_label']}\n\n"
        f"<b>Шаг 2/3 — Платформа промптов:</b>",
        reply_markup=kb_p_prompt_platform(),
        parse_mode=ParseMode.HTML,
    )


async def cb_ppform(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pipeline: выбор платформы фото-промптов."""
    if not await is_allowed(update):
        return
    q   = update.callback_query
    await q.answer()
    val = q.data.split(":", 1)[1]    # "gemini","flow","grok"
    cfg = context.user_data.setdefault("pipeline_cfg", {})

    labels = {"gemini": "🖼 PixelAgent", "flow": "🌊 Flow", "grok": "🤖 Grok"}
    cfg["photo_platform"] = val
    cfg["prompt_label"]   = labels.get(val, val)

    await q.edit_message_text(
        f"🚀 <b>Запустить всё</b>\n\n"
        f"✅ Нарезка: {cfg.get('cut_label', '?')}\n"
        f"✅ Промпты: {cfg['prompt_label']}\n\n"
        f"<b>Шаг 3/3 — Платформа генерации медиа:</b>",
        reply_markup=kb_p_media_platform(),
        parse_mode=ParseMode.HTML,
    )


async def cb_pmedia(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pipeline: выбор медиа-платформы."""
    if not await is_allowed(update):
        return
    q   = update.callback_query
    await q.answer()
    num = q.data.split(":")[1]    # "1","2","3"
    cfg = context.user_data.setdefault("pipeline_cfg", {})
    cfg["media_platform_num"] = num
    pname = PLATFORM_NAMES.get(num, num)

    # Браузерные платформы — проверяем куки
    if num != "3":
        cf = BROWSER_COOKIES.get(num)
        if cf and not (cf.exists() and cf.stat().st_size > 20):
            await q.edit_message_text(
                f"⚠️ <b>{h(pname)} — требуется авторизация</b>\n\n"
                f"Куки не найдены. Сначала авторизуйся вручную:\n"
                f"<pre>py agents/media_generator/media_generator.py</pre>",
                reply_markup=kb_done(),
                parse_mode=ParseMode.HTML,
            )
            return
        cfg["media_label"] = pname
        await _launch_pipeline(q, context)
    else:
        # PixelAgent → проверяем ключ и сразу запускаем
        env_cfg = dotenv_values(ENV_FILE)
        api_key = env_cfg.get("PIXEL_API_KEY", "").strip()
        if not api_key:
            await q.edit_message_text(
                "⚠️ <b>PIXEL_API_KEY не задан</b>\n\n"
                "Добавь ключ в <code>config/.env</code>:\n"
                "<pre>PIXEL_API_KEY=ваш_ключ</pre>",
                reply_markup=kb_done(),
                parse_mode=ParseMode.HTML,
            )
            return
        cfg["media_label"] = "PixelAgent"
        await _launch_pipeline(q, context)


async def cb_pmodel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pipeline: (устарело — модель PixelAgent не выбирается, запуск сразу)."""
    if not await is_allowed(update):
        return
    q   = update.callback_query
    await q.answer()
    cfg = context.user_data.setdefault("pipeline_cfg", {})
    cfg["media_label"] = "PixelAgent"
    await _launch_pipeline(q, context)


async def _launch_pipeline(q, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает итоговый конфиг и стартует asyncio Task."""
    cfg     = context.user_data.get("pipeline_cfg", {})
    user_id = q.from_user.id

    status_msg = await q.edit_message_text(
        f"🚀 <b>Пайплайн запускается...</b>\n\n"
        f"📋 Нарезка: {h(cfg.get('cut_label', '?'))}\n"
        f"📋 Промпты: {h(cfg.get('prompt_label', '?'))}\n"
        f"📋 Медиа:   {h(cfg.get('media_label', '?'))}\n\n"
        f"<i>Нажми ⏹ Стоп чтобы прервать в любой момент</i>",
        reply_markup=KB_STOP,
        parse_mode=ParseMode.HTML,
    )

    task = asyncio.create_task(run_pipeline(user_id, status_msg, cfg))
    running_pipelines[user_id] = task


async def cb_pipeline_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Останавливает запущенный пайплайн."""
    if not await is_allowed(update):
        return
    q       = update.callback_query
    await q.answer("Останавливаю...", show_alert=False)
    user_id = update.effective_user.id

    task = running_pipelines.get(user_id)
    if task and not task.done():
        task.cancel()
    else:
        await q.edit_message_text(
            "⏹ <b>Пайплайн не запущен.</b>",
            reply_markup=kb_done(),
            parse_mode=ParseMode.HTML,
        )

async def cb_pcutter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Опциональный этап пайплайна — нарезка видео после медиа."""
    if not await is_allowed(update):
        return
    q      = update.callback_query
    await q.answer()
    choice = q.data.split(":")[1]   # "cut", "upscale", "skip"

    if choice == "skip":
        await q.edit_message_text(
            "⏭ <b>Нарезка видео пропущена.</b>\n\nПайплайн полностью завершён! 🎉",
            reply_markup=kb_done(),
            parse_mode=ParseMode.HTML,
        )
        return

    if choice == "cut":
        progress = await q.edit_message_text(
            "✂️ <b>Нарезка видео...</b>\n\n<pre>запускаю...</pre>",
            parse_mode=ParseMode.HTML,
        )
        await _run_cutter_agent(progress, mode="cut")

    else:   # upscale — спрашиваем метод и разрешение
        context.user_data["cutter_mode"] = "cut+upscale"
        await q.edit_message_text(
            "✂️+🔍 <b>Нарезка + Апскейл</b>\n\nМетод апскейла?",
            reply_markup=kb_cutter_method(),
            parse_mode=ParseMode.HTML,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# ИНДИВИДУАЛЬНЫЕ РАЗДЕЛЫ (🎙 ✍️ 🖼 ✅)
# ═══════════════════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_allowed(update):
        return
    context.user_data.clear()
    await update.message.reply_text(
        "🎬 <b>Video Pipeline Bot</b>\n\nВыбери действие:",
        reply_markup=kb_main(),
        parse_mode=ParseMode.HTML,
    )


async def cb_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_allowed(update):
        return
    q = update.callback_query
    await q.answer()
    context.user_data.pop("state", None)
    context.user_data.pop("pipeline_cfg", None)
    await q.edit_message_text(
        "🎬 <b>Video Pipeline Bot</b>\n\nВыбери действие:",
        reply_markup=kb_main(),
        parse_mode=ParseMode.HTML,
    )


async def cb_noop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()


async def cb_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_allowed(update):
        return
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        get_project_status(),
        reply_markup=kb_done(),
        parse_mode=ParseMode.HTML,
    )


# ── Транскрипция ──────────────────────────────────────────────────────────────

async def cb_transcription(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_allowed(update):
        return
    q = update.callback_query
    await q.answer()
    context.user_data["state"] = "waiting_mp3"
    await q.edit_message_text(
        "🎙 <b>Транскрипция</b>\n\nОтправь MP3 или WAV файл в чат как документ.",
        reply_markup=InlineKeyboardMarkup([[BACK_BTN]]),
        parse_mode=ParseMode.HTML,
    )


async def _save_audio_file(update, tg_obj, fname: str, context) -> None:
    """Общая логика скачивания и сохранения аудиофайла."""
    status = await update.message.reply_text(
        f"⬇️ Скачиваю <code>{h(fname)}</code>...", parse_mode=ParseMode.HTML,
    )
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    dest    = INPUT_DIR / fname
    tg_file = await tg_obj.get_file()
    await tg_file.download_to_drive(str(dest))

    size_kb = dest.stat().st_size // 1024
    context.user_data["state"] = "ready_transcription"
    context.user_data["audio_input_path"] = str(dest)
    await status.edit_text(
        f"✅ Файл получен: <code>{h(fname)}</code> ({size_kb} KB)\n\nКак нарезать сегменты?",
        reply_markup=kb_cut_mode(),
        parse_mode=ParseMode.HTML,
    )


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_allowed(update):
        return
    if context.user_data.get("state") != "waiting_mp3":
        return

    doc   = update.message.document
    if not doc:
        return
    fname = doc.file_name or "audio.mp3"
    if not (fname.lower().endswith(".mp3") or fname.lower().endswith(".wav")):
        await update.message.reply_text(
            "⚠️ Нужен файл <b>MP3</b> или <b>WAV</b>. Попробуй снова.",
            parse_mode=ParseMode.HTML,
        )
        return
    await _save_audio_file(update, doc, fname, context)


async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Принимает MP3/WAV отправленный как аудио (с плеером)."""
    if not await is_allowed(update):
        return
    if context.user_data.get("state") != "waiting_mp3":
        return

    audio = update.message.audio
    if not audio:
        return
    fname = audio.file_name or "audio.mp3"
    if not fname.lower().endswith((".mp3", ".wav")):
        fname = "audio.mp3"  # принудительно .mp3 если имя неизвестно
    await _save_audio_file(update, audio, fname, context)


async def cb_cut(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_allowed(update):
        return
    q     = update.callback_query
    await q.answer()
    mode  = q.data.split(":")[1]   # "random" or "grok"
    label = "🎲 3–8 сек (рандом)" if mode == "random" else "📏 10 сек (Grok)"

    progress = await q.edit_message_text(
        f"⏳ <b>Транскрибирую...</b>\nРежим: {label}\n\n<pre>запускаю...</pre>",
        parse_mode=ParseMode.HTML,
    )
    cb = make_progress_cb(progress, f"⏳ <b>Транскрибирую...</b>\nРежим: {label}", interval=2.5)
    cmd = ["py", str(AGENTS_DIR / "transcriber" / "transcriber.py"), "--mode", mode]
    input_path = context.user_data.get("audio_input_path")
    if input_path:
        cmd += ["--input", input_path]

    logging.info(f"[cb_cut] mode={mode!r} cmd={cmd}")

    rc, output = await run_agent(cmd, on_line=cb)

    # Финальный вывод — показать все строки процесса
    final_lines = h("\n".join(output.splitlines()[-20:]))
    await progress.edit_text(
        f"⏳ <b>Транскрибирую...</b>\nРежим: {label}\n\n<pre>{final_lines}</pre>",
        parse_mode=ParseMode.HTML,
    )
    await asyncio.sleep(1.5)

    m         = re.search(r"(\d+)\s+сегментов", output)
    seg_count = m.group(1) if m else "?"
    icon      = "✅" if rc == 0 else "❌"
    await progress.edit_text(
        f"{icon} <b>Транскрипция завершена!</b>\n🎙 Сегментов: <b>{seg_count}</b>",
        reply_markup=kb_done(), parse_mode=ParseMode.HTML,
    )

    # Таблица сегментов из result.json
    session_m = re.search(r"\[Agent\] Сессия: (Video_\S+)", output)
    if session_m and rc == 0:
        session = session_m.group(1)
        json_path = BASE_DIR / "data" / "transcripts" / session / "result.json"
        if json_path.exists():
            with open(json_path, encoding="utf-8") as f:
                _raw = json.load(f)
            segs = _raw["segments"] if isinstance(_raw, dict) else _raw
            durs     = [s["end"] - s["start"] for s in segs]
            min_dur  = min(durs)
            max_dur  = max(durs)
            avg_dur  = sum(durs) / len(durs)
            table    = f"Режим: {mode} | Сессия: {session}\n"
            table   += f"{'#':>3} | {'Старт':>6} | {'Конец':>6} | {'Длина':>5}\n"
            table   += "-" * 32 + "\n"
            for seg in segs[:10]:
                dur    = seg["end"] - seg["start"]
                table += f"{seg['id']:>3} | {seg['start']:>5.1f}s | {seg['end']:>5.1f}s | {dur:>4.1f}s\n"
            if len(segs) > 10:
                table += f"... (ещё {len(segs) - 10})\n"
            table += "-" * 32 + "\n"
            table += f"Всего: {len(segs)} сег | Мин: {min_dur:.1f}s | Макс: {max_dur:.1f}s | Ср: {avg_dur:.1f}s"
            await update.effective_message.reply_text(
                f"<pre>{h(table)}</pre>", parse_mode=ParseMode.HTML
            )

    context.user_data.pop("state", None)


# ── Промпты ───────────────────────────────────────────────────────────────────

async def cb_prompts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_allowed(update):
        return
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "✍️ <b>Промпты</b>\n\nЧто генерировать?",
        reply_markup=kb_prompt_type(),
        parse_mode=ParseMode.HTML,
    )


async def cb_prompt_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Выбор типа промптов: фото или видео → выбор платформы."""
    if not await is_allowed(update):
        return
    q     = update.callback_query
    await q.answer()
    ptype = q.data.split(":")[1]  # "photo" or "video"
    if ptype == "photo":
        await q.edit_message_text(
            "📷 <b>Фото промпты</b>\n\nВыбери платформу:",
            reply_markup=kb_photo_platform(),
            parse_mode=ParseMode.HTML,
        )
    else:
        await q.edit_message_text(
            "🎬 <b>Видео промпты</b>\n\nВыбери платформу:",
            reply_markup=kb_video_platform(),
            parse_mode=ParseMode.HTML,
        )


async def cb_photo_platform(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Генерация фото-промптов: платформа = gemini(PixelAgent) | flow | grok."""
    if not await is_allowed(update):
        return
    q        = update.callback_query
    await q.answer()
    platform = q.data.split(":")[1]   # "gemini", "flow", "grok"
    labels   = {"gemini": "🖼 PixelAgent", "flow": "🌊 Flow", "grok": "🤖 Grok"}
    label    = labels.get(platform, platform)

    progress = await q.edit_message_text(
        f"⏳ <b>Генерирую фото-промпты...</b>\nПлатформа: {h(label)}\n\n<pre>запускаю...</pre>",
        parse_mode=ParseMode.HTML,
    )
    await _run_prompts_agent(progress, "", label, photo_platform=platform)


async def cb_video_platform(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Генерация видео-промптов: платформа = grok | flow."""
    if not await is_allowed(update):
        return
    q        = update.callback_query
    await q.answer()
    platform = q.data.split(":")[1]   # "grok", "flow"
    labels   = {"grok": "🤖 Grok", "flow": "🌊 Flow"}
    label    = labels.get(platform, platform)

    progress = await q.edit_message_text(
        f"⏳ <b>Генерирую видео-промпты...</b>\nПлатформа: {h(label)}\n\n<pre>запускаю...</pre>",
        parse_mode=ParseMode.HTML,
    )
    await _run_video_prompts_agent(progress, "", label, video_platform=platform)


async def cb_platform(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_allowed(update):
        return
    q   = update.callback_query
    await q.answer()
    val = q.data.split(":", 1)[1]

    if val == "other":
        context.user_data["state"] = "waiting_platform_name"
        await q.edit_message_text(
            "✍️ <b>Промпты</b>\n\nВведи название платформы в чат:",
            parse_mode=ParseMode.HTML,
        )
        return

    if val == "gemini":
        stdin, label = "5\nPixelAgent\n", "PixelAgent"
    else:
        stdin = f"{val}\n"
        label = {"1": "Flow / Veo 3", "2": "Midjourney", "3": "SD", "4": "DALL-E"}.get(val, val)

    progress = await q.edit_message_text(
        f"⏳ <b>Генерирую промпты...</b>\nПлатформа: {h(label)}\n\n<pre>запускаю...</pre>",
        parse_mode=ParseMode.HTML,
    )
    await _run_prompts_agent(progress, stdin, label)


# ── Медиа ─────────────────────────────────────────────────────────────────────

async def cb_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_allowed(update):
        return
    q = update.callback_query
    await q.answer()
    context.user_data.pop("media_type", None)   # сбрасываем пре-выбор типа
    await q.edit_message_text(
        "🖼 <b>Генерация медиа</b>\n\nВыбери платформу:",
        reply_markup=kb_media_platform(), parse_mode=ParseMode.HTML,
    )


async def cb_media_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Прямой вход в генерацию фото — тип уже выбран."""
    if not await is_allowed(update):
        return
    q = update.callback_query
    await q.answer()
    context.user_data["media_type"] = "photo"
    await q.edit_message_text(
        "📷 <b>Генерация фото</b>\n\nВыбери платформу:",
        reply_markup=kb_media_platform(), parse_mode=ParseMode.HTML,
    )


async def cb_media_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Прямой вход в генерацию видео — тип уже выбран."""
    if not await is_allowed(update):
        return
    q = update.callback_query
    await q.answer()
    context.user_data["media_type"] = "video"
    await q.edit_message_text(
        "🎬 <b>Генерация видео</b>\n\nВыбери платформу:",
        reply_markup=kb_media_platform(), parse_mode=ParseMode.HTML,
    )


async def cb_cutter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """✂️ Нарезка видео — выбор режима."""
    if not await is_allowed(update):
        return
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "✂️ <b>Нарезка видео</b>\n\nЧто делать с видео?",
        reply_markup=kb_cutter_mode(),
        parse_mode=ParseMode.HTML,
    )


async def cb_cutter_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Выбор режима: cut / cut+upscale."""
    if not await is_allowed(update):
        return
    q    = update.callback_query
    await q.answer()
    mode = q.data.split(":")[1]    # "cut" или "cut+upscale"
    context.user_data["cutter_mode"] = mode

    if mode == "cut":
        progress = await q.edit_message_text(
            "✂️ <b>Нарезка видео...</b>\n\n<pre>запускаю...</pre>",
            parse_mode=ParseMode.HTML,
        )
        await _run_cutter_agent(progress, mode="cut")
    else:
        await q.edit_message_text(
            "✂️+🔍 <b>Нарезка + Апскейл</b>\n\nМетод апскейла?",
            reply_markup=kb_cutter_method(),
            parse_mode=ParseMode.HTML,
        )


async def cb_cutter_upscale_method(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Выбор метода апскейла."""
    if not await is_allowed(update):
        return
    q      = update.callback_query
    await q.answer()
    method = q.data.split(":")[1]
    context.user_data["cutter_method"] = method
    await q.edit_message_text(
        "✂️+🔍 <b>Нарезка + Апскейл</b>\n\nЦелевое разрешение?",
        reply_markup=kb_cutter_resolution(),
        parse_mode=ParseMode.HTML,
    )


async def cb_cutter_resolution(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Выбор разрешения → запуск."""
    if not await is_allowed(update):
        return
    q          = update.callback_query
    await q.answer()
    resolution = q.data.split(":")[1]
    method     = context.user_data.get("cutter_method", "lanczos")
    res_labels = {"720": "720p", "1080": "1080p", "2k": "2K", "4k": "4K"}

    progress = await q.edit_message_text(
        f"⏳ <b>Нарезка + Апскейл</b>\n"
        f"Метод: {method} → {res_labels.get(resolution, resolution)}\n\n"
        f"<pre>запускаю...</pre>",
        parse_mode=ParseMode.HTML,
    )
    await _run_cutter_agent(progress, mode="cut+upscale", method=method, resolution=resolution)


async def cb_mplatform(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_allowed(update):
        return
    q      = update.callback_query
    await q.answer()
    num    = q.data.split(":")[1]
    pname  = PLATFORM_NAMES.get(num, num)
    is_api = (num == "3")
    context.user_data["media_platform"] = num

    if not is_api:
        cf = BROWSER_COOKIES.get(num)
        if cf and not (cf.exists() and cf.stat().st_size > 20):
            await q.edit_message_text(
                f"⚠️ <b>{h(pname)} — требуется авторизация</b>\n\n"
                f"<pre>py agents/media_generator/media_generator.py</pre>",
                reply_markup=kb_done(), parse_mode=ParseMode.HTML,
            )
            return

    # Если тип уже выбран (кнопка 📷 Фото / 🎬 Видео) — пропускаем шаг выбора
    preselected = context.user_data.get("media_type")
    if preselected:
        context.user_data["media_type"] = preselected
        if is_api:
            cfg_env = dotenv_values(ENV_FILE)
            api_key = cfg_env.get("PIXEL_API_KEY", "").strip()
            if not api_key:
                await q.edit_message_text(
                    "⚠️ <b>PIXEL_API_KEY не задан</b>\n\nДобавь ключ в <code>config/.env</code>",
                    reply_markup=kb_done(), parse_mode=ParseMode.HTML,
                )
                return
            type_label = {"photo": "📷 Фото", "video": "🎬 Видео", "both": "📷 Фото"}[preselected]
            progress   = await q.edit_message_text(
                f"⏳ <b>Генерирую медиа...</b>\n{type_label} | PixelAgent\n\n<pre>запускаю...</pre>",
                parse_mode=ParseMode.HTML,
            )
            await _run_media_agent(progress, "", num, preselected)
        else:
            type_label = {"photo": "📷 Фото", "video": "🎬 Видео", "both": "📷+🎬"}[preselected]
            progress   = await q.edit_message_text(
                f"⏳ <b>Генерирую медиа...</b>\n{type_label} | {h(pname)}\n\n<pre>запускаю...</pre>",
                parse_mode=ParseMode.HTML,
            )
            await _run_media_agent(progress, "", num, preselected)
    else:
        await q.edit_message_text(
            f"🖼 <b>{h(pname)}</b>\n\nЧто генерировать?",
            reply_markup=kb_media_type(is_api=is_api), parse_mode=ParseMode.HTML,
        )


async def cb_mtype(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_allowed(update):
        return
    q            = update.callback_query
    await q.answer()
    mtype        = q.data.split(":")[1]
    platform_num = context.user_data.get("media_platform", "3")
    context.user_data["media_type"] = mtype

    if platform_num == "3":
        cfg     = dotenv_values(ENV_FILE)
        api_key = cfg.get("PIXEL_API_KEY", "").strip()
        if not api_key:
            await q.edit_message_text(
                "⚠️ <b>PIXEL_API_KEY не задан</b>\n\nДобавь ключ в <code>config/.env</code>",
                reply_markup=kb_done(), parse_mode=ParseMode.HTML,
            )
            return
        type_label = {"photo": "📷 Фото", "video": "🎬 Видео", "both": "📷 Фото"}[mtype]
        progress   = await q.edit_message_text(
            f"⏳ <b>Генерирую медиа...</b>\n{type_label} | PixelAgent\n\n<pre>запускаю...</pre>",
            parse_mode=ParseMode.HTML,
        )
        await _run_media_agent(progress, "", platform_num, mtype)
    else:
        pname      = PLATFORM_NAMES.get(platform_num, platform_num)
        type_label = {"photo": "📷 Фото", "video": "🎬 Видео", "both": "📷+🎬 Фото + Видео"}[mtype]
        progress   = await q.edit_message_text(
            f"⏳ <b>Генерирую медиа...</b>\n{type_label} | {h(pname)}\n\n<pre>запускаю...</pre>",
            parse_mode=ParseMode.HTML,
        )
        await _run_media_agent(progress, "", platform_num, mtype)


async def cb_gmodel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """(устарело — PixelAgent не требует выбора модели, запуск сразу)."""
    if not await is_allowed(update):
        return
    q            = update.callback_query
    await q.answer()
    platform_num = context.user_data.get("media_platform", "3")
    mtype        = context.user_data.get("media_type", "photo")

    cfg     = dotenv_values(ENV_FILE)
    api_key = cfg.get("PIXEL_API_KEY", "").strip()
    if not api_key:
        await q.edit_message_text(
            "⚠️ <b>PIXEL_API_KEY не задан</b>\n\nДобавь ключ в <code>config/.env</code>",
            reply_markup=kb_done(), parse_mode=ParseMode.HTML,
        )
        return

    type_label = {"photo": "📷 Фото", "video": "🎬 Видео", "both": "📷 Фото"}[mtype]
    progress   = await q.edit_message_text(
        f"⏳ <b>Генерирую медиа...</b>\n{type_label} | PixelAgent\n\n<pre>запускаю...</pre>",
        parse_mode=ParseMode.HTML,
    )
    await _run_media_agent(progress, "", platform_num, mtype)


# ── Валидация ─────────────────────────────────────────────────────────────────

async def cb_validation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_allowed(update):
        return
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "✅ <b>Валидация</b>\n\nЧто проверить?",
        reply_markup=kb_validation(), parse_mode=ParseMode.HTML,
    )


async def cb_validate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_allowed(update):
        return
    q          = update.callback_query
    await q.answer()
    check_type = q.data.split(":")[1]
    label      = {"transcription": "🎙 Транскрипция", "prompts": "✍️ Промпты", "all": "🔍 Всё"}[check_type]

    progress = await q.edit_message_text(
        f"⏳ <b>Валидирую...</b>\n{label}\n\n<pre>запускаю...</pre>",
        parse_mode=ParseMode.HTML,
    )
    rc, output = await run_agent(["py", str(AGENTS_DIR / "validator" / "validator.py")])

    all_lines = output.splitlines()
    if check_type == "transcription":
        relevant, ins = [], False
        for line in all_lines:
            if "Проверка 1" in line: ins = True
            if "Проверка 2" in line: ins = False
            if ins or "✅" in line or "❌" in line: relevant.append(line)
        filtered = relevant or all_lines
    elif check_type == "prompts":
        relevant, ins = [], False
        for line in all_lines:
            if "Проверка 2" in line: ins = True
            if ins or "✅" in line or "❌" in line: relevant.append(line)
        filtered = relevant or all_lines
    else:
        filtered = all_lines

    report = "\n".join(filtered[-40:])
    if len(report) > 2000:
        report = "...\n" + report[-2000:]

    icon = "✅" if rc == 0 else "⚠️"
    await progress.edit_text(
        f"{icon} <b>Результат ({label}):</b>\n\n<pre>{h(report)}</pre>",
        reply_markup=kb_done(), parse_mode=ParseMode.HTML,
    )


# ── Текстовые сообщения ───────────────────────────────────────────────────────

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_allowed(update):
        return
    state = context.user_data.get("state", "")

    if state == "waiting_platform_name":
        # Индивидуальный раздел Промпты → Другое
        name = update.message.text.strip()
        if not name:
            await update.message.reply_text("Введи название платформы.")
            return
        context.user_data.pop("state", None)
        stdin    = f"5\n{name}\n"
        progress = await update.message.reply_text(
            f"⏳ <b>Генерирую промпты...</b>\nПлатформа: {h(name)}\n\n<pre>запускаю...</pre>",
            parse_mode=ParseMode.HTML,
        )
        await _run_prompts_agent(progress, stdin, name)

    elif state == "pipeline_waiting_platform_name":
        # Пайплайн → Другое платформа
        name = update.message.text.strip()
        if not name:
            await update.message.reply_text("Введи название платформы.")
            return
        context.user_data.pop("state", None)
        cfg = context.user_data.setdefault("pipeline_cfg", {})
        cfg["prompt_stdin"] = f"5\n{name}\n"
        cfg["prompt_label"] = name

        msg = await update.message.reply_text(
            f"🚀 <b>Запустить всё</b>\n\n"
            f"✅ Нарезка: {cfg.get('cut_label', '?')}\n"
            f"✅ Промпты: {h(name)}\n\n"
            f"<b>Шаг 3/3 — Платформа генерации медиа:</b>",
            reply_markup=kb_p_media_platform(),
            parse_mode=ParseMode.HTML,
        )

    elif state == "waiting_mp3":
        await update.message.reply_text(
            "📎 Отправь MP3 или WAV файл как <b>документ</b> (скрепка → файл).",
            parse_mode=ParseMode.HTML,
        )

# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        print("ОШИБКА: TELEGRAM_BOT_TOKEN не задан в config/.env")
        sys.exit(1)
    if not ALLOWED_USER_ID:
        print("ОШИБКА: TELEGRAM_ALLOWED_USER_ID не задан в config/.env")
        sys.exit(1)

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler(["start", "menu"], cmd_start))

    # Утилиты
    app.add_handler(CallbackQueryHandler(cb_noop,  pattern=r"^noop$"))
    app.add_handler(CallbackQueryHandler(cb_back,  pattern=r"^back:"))

    # 🚀 Пайплайн
    app.add_handler(CallbackQueryHandler(cb_pipeline_start, pattern=r"^pipeline:start$"))
    app.add_handler(CallbackQueryHandler(cb_pipeline_stop,  pattern=r"^pipeline:stop$"))
    app.add_handler(CallbackQueryHandler(cb_pcut,           pattern=r"^pcut:"))
    app.add_handler(CallbackQueryHandler(cb_ppform,         pattern=r"^ppform:"))
    app.add_handler(CallbackQueryHandler(cb_pmedia,         pattern=r"^pmedia:"))
    app.add_handler(CallbackQueryHandler(cb_pmodel,         pattern=r"^pmodel:"))

    # Индивидуальные разделы
    app.add_handler(CallbackQueryHandler(cb_status,         pattern=r"^menu:status$"))
    app.add_handler(CallbackQueryHandler(cb_transcription,  pattern=r"^menu:transcription$"))
    app.add_handler(CallbackQueryHandler(cb_prompts,         pattern=r"^menu:prompts$"))
    app.add_handler(CallbackQueryHandler(cb_prompt_type,     pattern=r"^ptype:"))
    app.add_handler(CallbackQueryHandler(cb_photo_platform,  pattern=r"^pphoto:"))
    app.add_handler(CallbackQueryHandler(cb_video_platform,  pattern=r"^pvideo:"))
    app.add_handler(CallbackQueryHandler(cb_media,           pattern=r"^menu:media$"))
    app.add_handler(CallbackQueryHandler(cb_media_photo,    pattern=r"^menu:photo$"))
    app.add_handler(CallbackQueryHandler(cb_media_video,    pattern=r"^menu:video$"))
    app.add_handler(CallbackQueryHandler(cb_cutter,              pattern=r"^menu:cutter$"))
    app.add_handler(CallbackQueryHandler(cb_cutter_mode,         pattern=r"^cutter:"))
    app.add_handler(CallbackQueryHandler(cb_cutter_upscale_method, pattern=r"^cupscale:"))
    app.add_handler(CallbackQueryHandler(cb_cutter_resolution,   pattern=r"^cres:"))
    app.add_handler(CallbackQueryHandler(cb_pcutter,             pattern=r"^pcutter:"))
    app.add_handler(CallbackQueryHandler(cb_validation,     pattern=r"^menu:validation$"))
    app.add_handler(CallbackQueryHandler(cb_cut,            pattern=r"^cut:"))
    app.add_handler(CallbackQueryHandler(cb_platform,       pattern=r"^platform:"))
    app.add_handler(CallbackQueryHandler(cb_mplatform,      pattern=r"^mplatform:"))
    app.add_handler(CallbackQueryHandler(cb_mtype,          pattern=r"^mtype:"))
    app.add_handler(CallbackQueryHandler(cb_gmodel,         pattern=r"^gmodel:"))
    app.add_handler(CallbackQueryHandler(cb_validate,       pattern=r"^validate:"))

    # Сообщения
    app.add_handler(MessageHandler(filters.Document.ALL,            handle_document))
    app.add_handler(MessageHandler(filters.AUDIO,                   handle_audio))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print(f"[Bot] ✅ Запущен")
    print(f"[Bot] Разрешённый user_id: {ALLOWED_USER_ID}")
    print(f"[Bot] Отправь /start или /menu в Telegram")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
