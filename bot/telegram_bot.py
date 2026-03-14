

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
import subprocess
import sys
import threading
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
# bot/ dir в path для channel_manager
_BOT_DIR = Path(__file__).resolve().parent
if str(_BOT_DIR) not in sys.path:
    sys.path.insert(0, str(_BOT_DIR))

from dotenv import load_dotenv, dotenv_values
from channel_manager import (
    get_all_channels,
    get_active_channel,
    set_active_channel,
    get_channel_config,
)

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
BLIP_PROGRESS_FILE        = BASE_DIR / "temp" / "blip_progress.json"
REGEN_PROGRESS_FILE       = BASE_DIR / "temp" / "regen_progress.json"
GROK_PROGRESS_FILE        = BASE_DIR / "temp" / "grok_progress.json"
VIDEO_VALIDATOR_PROGRESS  = BASE_DIR / "temp" / "video_validator_progress.json"
NORM_UPSCALE_PROGRESS     = BASE_DIR / "temp" / "normalize_upscale_progress.json"

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


def _get_channel_btn_label() -> str:
    """Метка кнопки канала: emoji + name активного канала."""
    try:
        ch = get_active_channel()
        if ch:
            return f"📡 {ch['emoji']} {ch['name']}"
    except Exception:
        pass
    return "📡 Канал"


def get_main_menu_text() -> str:
    """Текст главного меню с активным каналом."""
    try:
        ch = get_active_channel()
        ch_name = f"{ch['emoji']} {ch['name']}" if ch else "не выбран"
    except Exception:
        ch_name = "не выбран"
    return (
        f"🎬 <b>Video Pipeline Bot</b>\n\n"
        f"📡 Канал: <b>{ch_name}</b>\n\n"
        f"Выбери действие:"
    )


def kb_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(_get_channel_btn_label(), callback_data="channels:show")],
        [InlineKeyboardButton("──────────────────",  callback_data="noop")],
        [InlineKeyboardButton("🚀 Запустить всё",    callback_data="pipeline:start")],
        [InlineKeyboardButton("──────────────────",  callback_data="noop")],
        [InlineKeyboardButton("🎙 Транскрипция",     callback_data="menu:transcription")],
        [InlineKeyboardButton("✍️ Промпты",           callback_data="menu:prompts")],
        [
            InlineKeyboardButton("📷 Фото",          callback_data="menu:photo"),
            InlineKeyboardButton("🎬 Видео",         callback_data="menu:video"),
        ],
        [InlineKeyboardButton("✂️ Нарезка видео",    callback_data="menu:cutter")],
        [InlineKeyboardButton("🔍 Апскейл",          callback_data="menu:upscale")],
        [InlineKeyboardButton("🎬 FFmpeg монтаж",      callback_data="menu:montage")],
        [InlineKeyboardButton("🖥 Редактор",          callback_data="menu:editor")],
        [InlineKeyboardButton("✅ Валидация",          callback_data="menu:validation")],
        [InlineKeyboardButton("🔍 BLIP анализ",       callback_data="menu:blip")],
        [InlineKeyboardButton("🎬 Валидация видео",   callback_data="menu:video_validator")],
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
    """Выбор типа промптов: фото, видео или оба."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📷 Фото промпты",        callback_data="ptype:photo")],
        [InlineKeyboardButton("🎬 Видео промпты",       callback_data="ptype:video")],
        [InlineKeyboardButton("📷🎬 Фото + Видео",      callback_data="ptype:both")],
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




def kb_pixel_menu(version: str) -> InlineKeyboardMarkup:
    """PixelAgent: выбор типа генерации + переключатель версии API."""
    v1_label = "✅ v1" if version == "v1" else "◻️ v1"
    v2_label = "✅ v2" if version == "v2" else "◻️ v2"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📷 Фото",  callback_data="mtype:photo")],
        [InlineKeyboardButton("🎬 Видео", callback_data="mtype:video")],
        [InlineKeyboardButton("──────────────────", callback_data="noop")],
        [
            InlineKeyboardButton(f"⚙️ API: {v1_label}", callback_data="pixelver:v1"),
            InlineKeyboardButton(f"⚙️ API: {v2_label}", callback_data="pixelver:v2"),
        ],
        [BACK_BTN],
    ])


def kb_upscale_type() -> InlineKeyboardMarkup:
    """Выбор что апскейлить: фото или видео."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📷 Фото",  callback_data="uptype:photo")],
        [InlineKeyboardButton("🎬 Видео", callback_data="uptype:video")],
        [BACK_BTN],
    ])


def kb_upscale_resolution() -> InlineKeyboardMarkup:
    """Разрешение для апскейла фото (без 720 — смысла нет)."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("1080p", callback_data="upres:1080"),
            InlineKeyboardButton("2K",    callback_data="upres:2k"),
            InlineKeyboardButton("4K",    callback_data="upres:4k"),
        ],
        [BACK_BTN],
    ])


def kb_validation() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎙 Транскрипцию", callback_data="validate:transcription")],
        [InlineKeyboardButton("✍️ Промпты",       callback_data="validate:prompts")],
        [InlineKeyboardButton("🔍 Всё сразу",     callback_data="validate:all")],
        [BACK_BTN],
    ])


def kb_done() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[BACK_BTN]])


def kb_done_with_blip() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 BLIP анализ", callback_data="menu:blip")],
        [BACK_BTN],
    ])


def kb_blip_type() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📸 Фото",   callback_data="blip:photo")],
        [InlineKeyboardButton("🎬 Видео",  callback_data="blip:video")],
        [InlineKeyboardButton("📸🎬 Оба",  callback_data="blip:both")],
        [BACK_BTN],
    ])


def kb_blip_regen(has_bad_photos: bool, has_bad_videos: bool) -> InlineKeyboardMarkup:
    rows = []
    if has_bad_photos and has_bad_videos:
        rows.append([InlineKeyboardButton("✅ Да, фото",  callback_data="blip_regen:photo")])
        rows.append([InlineKeyboardButton("✅ Да, видео", callback_data="blip_regen:video")])
        rows.append([InlineKeyboardButton("✅ Оба",       callback_data="blip_regen:both")])
    elif has_bad_photos:
        rows.append([InlineKeyboardButton("✅ Да, фото",  callback_data="blip_regen:photo")])
    elif has_bad_videos:
        rows.append([InlineKeyboardButton("✅ Да, видео", callback_data="blip_regen:video")])
    rows.append([InlineKeyboardButton("❌ Нет", callback_data="back:main")])
    return InlineKeyboardMarkup(rows)


def kb_cutter_mode() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✂️ Только нарезка",        callback_data="cutter:cut")],
        [InlineKeyboardButton("✂️+🔍 Нарезка + Апскейл",  callback_data="cutter:cut+upscale")],
        [InlineKeyboardButton("🔍 Апскейл видео (Grok)",  callback_data="cutter:upscale")],
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


def kb_montage_subs() -> InlineKeyboardMarkup:
    """Выбор режима монтажа: субтитры × музыка."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Субтитры + музыка",    callback_data="montage:subs_music")],
        [InlineKeyboardButton("📝 Только субтитры",      callback_data="montage:subs_nomusic")],
        [InlineKeyboardButton("🎵 Только музыка",        callback_data="montage:nosubs_music")],
        [InlineKeyboardButton("❌ Без субтитров/музыки", callback_data="montage:nosubs_nomusic")],
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

    # Новая структура: photo/photo_prompts.json; fallback: photo_prompts.json
    pf = PROMPTS_DIR / session / "photo" / "photo_prompts.json"
    if not pf.exists():
        pf = PROMPTS_DIR / session / "photo_prompts.json"
    vf = PROMPTS_DIR / session / "video" / "video_prompts.json"
    if not vf.exists():
        vf = PROMPTS_DIR / session / "video_prompts.json"
    total_pr = 0
    if pf.exists():
        try:
            pr = json.loads(pf.read_text(encoding="utf-8"))
            total_pr = len(pr)
            vid_n = len(json.loads(vf.read_text(encoding="utf-8"))) if vf.exists() else 0
            vid_str = f" | 🎬 {vid_n}" if vid_n else ""
            lines.append(f"✍️ Промпты: ✅ 📷 {total_pr}{vid_str}")
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
    env["TQDM_DISABLE"] = "1"   # отключаем tqdm прогресс-бары (они не делают \n → переполняют буфер)

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(BASE_DIR),
        env=env,
        limit=10 * 1024 * 1024,  # 10MB буфер (по умолчанию 64KB — мало для BLIP-2)
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


def _read_blip_progress() -> dict | None:
    try:
        return json.loads(BLIP_PROGRESS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_regen_progress() -> dict | None:
    try:
        return json.loads(REGEN_PROGRESS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_grok_progress() -> dict | None:
    try:
        return json.loads(GROK_PROGRESS_FILE.read_text(encoding="utf-8"))
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

    if platform_num == "2" and mtype == "video":
        # ── Grok видео: polling из grok_progress.json (multi-tab) ───────────
        try:
            GROK_PROGRESS_FILE.unlink(missing_ok=True)
        except Exception:
            pass

        task = asyncio.create_task(run_agent(cmd))
        t0   = time.monotonic()

        while not task.done():
            await asyncio.sleep(3.0)
            p = _read_grok_progress()
            elapsed = int(time.monotonic() - t0)
            if p and p.get("total", 0) > 0:
                completed = len(p.get("completed", []))
                total     = p["total"]
                pct       = int(completed / total * 100) if total else 0
                bar       = _prompt_bar(completed, total)

                tabs_status = p.get("tabs_status", {})
                if tabs_status:
                    parts = [
                        f"Tab-{k}: {v['done']}/{v['assigned']}"
                        for k, v in sorted(tabs_status.items(), key=lambda x: int(x[0]))
                    ]
                    tabs_text = "\n🗂 " + "  |  ".join(parts)
                else:
                    tabs_text = ""

                text = (
                    f"🎬 <b>Grok: генерирую видео...</b>\n"
                    f"━━━━━━━━━━━━━━━━\n"
                    f"📊 Готово: <b>{completed}/{total}</b>\n"
                    f"<code>{bar}</code> {pct}%\n"
                    f"⏱ {elapsed // 60}м {elapsed % 60}с"
                    + tabs_text
                )
            else:
                text = (
                    f"🎬 <b>Grok: запуск вкладок...</b>\n"
                    f"⏳ {elapsed}с — открываю Chrome..."
                )
            try:
                await progress_msg.edit_text(text, parse_mode=ParseMode.HTML)
            except Exception:
                pass

        rc, _ = task.result()
        elapsed = int(time.monotonic() - t0)
        p_fin = _read_grok_progress()
        done_n  = len(p_fin.get("completed", [])) if p_fin else 0
        total_n = p_fin.get("total", 0) if p_fin else 0
        icon    = "✅" if rc == 0 else "❌"
        await progress_msg.edit_text(
            f"{icon} <b>Генерация завершена!</b>\n"
            f"🎬 Grok видео\n"
            f"📊 Сохранено: <b>{done_n}/{total_n}</b>\n"
            f"⏱ Время: {elapsed // 60}м {elapsed % 60}с",
            reply_markup=kb_done(),
            parse_mode=ParseMode.HTML,
        )

    elif platform_num == "3":
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
        # ── Browser-платформы (Flow и др.): streaming ───────────────────────
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

async def _run_blip_agent(
    progress_msg,
    gen_type: str,
    session: str = "",
    threshold: float = 0.15,
) -> None:
    try:
        BLIP_PROGRESS_FILE.unlink(missing_ok=True)
    except Exception:
        pass

    type_icons = {"photo": "📸 Фото", "video": "🎬 Видео", "both": "📸🎬 Оба"}
    type_label = type_icons.get(gen_type, gen_type)
    cmd = ["py", str(AGENTS_DIR / "blip_validator" / "blip_validator.py"),
           "--type", gen_type, "--threshold", str(threshold)]
    if session:
        cmd += ["--project", session]

    task = asyncio.create_task(run_agent(cmd))
    t0   = time.monotonic()

    while not task.done():
        await asyncio.sleep(3.0)
        p = _read_blip_progress()
        if p and p.get("total", 0) > 0:
            cur    = p.get("current", 0)
            total  = p["total"]
            status = p.get("status", "running")
            speed  = p.get("speed", 0.0)
            ptype  = p.get("type", gen_type)
            bar    = _prompt_bar(cur, total)
            pct    = int(cur / total * 100) if total else 0
            icon   = "📸" if ptype == "photo" else "🎬"
            if status == "loading":
                text = f"🔍 <b>BLIP анализ...</b>\n{type_label}\n\n<pre>Загружаю модель...</pre>"
            else:
                eta_str = f"  ETA ~{int((total-cur)/speed)}с" if speed > 0 and cur < total else ""
                text = (
                    f"🔍 <b>BLIP анализирует {icon}...</b>\n{type_label}\n"
                    f"━━━━━━━━━━━━━━━━\n"
                    f"📊 Готово: <b>{cur}/{total}</b>\n"
                    f"<code>{bar}</code> {pct}%\n"
                    f"⚡ {speed:.1f} шт/с на RTX 3060{eta_str}"
                )
        else:
            elapsed = int(time.monotonic() - t0)
            text = f"🔍 <b>BLIP анализ...</b>\n{type_label}\n\n<pre>запускаю... ({elapsed}с)</pre>"
        try:
            await progress_msg.edit_text(text, parse_mode=ParseMode.HTML)
        except Exception:
            pass

    rc, output = task.result()

    summary: dict = {}
    m_sum = re.search(r"BLIP_SUMMARY:\s*(\{.+\})", output)
    if m_sum:
        try:
            summary = json.loads(m_sum.group(1))
        except Exception:
            pass

    if rc != 0 and not summary:
        err_tail = "\n".join(output.splitlines()[-10:])
        await progress_msg.edit_text(
            f"❌ <b>BLIP завершился с ошибкой!</b>\n\n<pre>{h(err_tail)}</pre>",
            reply_markup=kb_done(), parse_mode=ParseMode.HTML,
        )
        return

    lines = ["🔍 <b>BLIP анализ завершён!</b>\n"]
    ph_tot  = summary.get("photos_total", 0)
    ph_ok   = summary.get("photos_ok",    0)
    ph_bad  = summary.get("photos_bad",   0)
    vid_tot = summary.get("videos_total", 0)
    vid_ok  = summary.get("videos_ok",    0)
    vid_bad = summary.get("videos_bad",   0)

    if ph_tot > 0:
        bad_ids = summary.get("bad_photos", [])
        ids_str = ", ".join(f"#{n}" for n in bad_ids[:15])
        if len(bad_ids) > 15:
            ids_str += f"... ещё {len(bad_ids)-15}"
        lines += [f"📸 <b>Фото:</b>", f"  ✅ Хорошие: <b>{ph_ok}/{ph_tot}</b>"]
        if ph_bad:
            lines.append(f"  ⚠️ Плохие: <b>{ph_bad}/{ph_tot}</b> → {ids_str}")

    if vid_tot > 0:
        bad_ids = summary.get("bad_videos", [])
        ids_str = ", ".join(f"#{n}" for n in bad_ids[:15])
        if len(bad_ids) > 15:
            ids_str += f"... ещё {len(bad_ids)-15}"
        lines += [f"\n🎬 <b>Видео:</b>", f"  ✅ Хорошие: <b>{vid_ok}/{vid_tot}</b>"]
        if vid_bad:
            lines.append(f"  ⚠️ Плохие: <b>{vid_bad}/{vid_tot}</b> → {ids_str}")

    has_bad_p = ph_bad > 0
    has_bad_v = vid_bad > 0
    if has_bad_p or has_bad_v:
        lines.append("\n<b>Перегенерировать плохие?</b>")
        kb = kb_blip_regen(has_bad_p, has_bad_v)
    else:
        kb = kb_done()

    await progress_msg.edit_text(
        "\n".join(lines), reply_markup=kb, parse_mode=ParseMode.HTML,
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
    elif mode == "upscale":
        header = (
            f"⏳ <b>Апскейл видео...</b>\n"
            f"Метод: {method_labels.get(method, method)} → {res_labels.get(resolution, resolution)}"
        )
    else:
        header = "⏳ <b>Нарезка видео...</b>"

    cb = make_progress_cb(progress_msg, header, interval=3.0)

    cmd = ["py", str(AGENTS_DIR / "video_cutter" / "video_cutter.py"), "--mode", mode]
    if session:
        cmd += ["--project", session]
    if mode in ("cut+upscale", "upscale"):
        cmd += ["--method", method, "--resolution", resolution]

    rc, output = await run_agent(cmd, on_line=cb)

    icon = "✅" if rc == 0 else "⚠️"

    # Последние строки с ошибкой (показываем если rc != 0)
    err_lines = [l for l in output.splitlines() if l.strip()][-3:] if rc != 0 else []

    if mode == "upscale":
        up_m   = re.search(r"Обработано:\s*(\d+(?:/\d+)?)", output)
        time_m = re.search(r"Время:\s*([^\n]+)", output)
        up_count   = up_m.group(1).strip()  if up_m   else None
        total_time = time_m.group(1).strip() if time_m else None
        lines = [f"{icon} <b>Апскейл завершён!</b>"]
        if up_count:
            lines.append(f"🔍 Обработано: <b>{up_count}</b> → {res_labels.get(resolution, resolution)}")
        if total_time:
            lines.append(f"⏱ Время: {h(total_time)}")
        if err_lines and not up_count:
            lines.append(f"\n<pre>{h(chr(10).join(err_lines))}</pre>")
    else:
        cut_m  = re.search(r"Нарезано:\s*(\d+)", output)
        up_m   = re.search(r"Апскейл:\s*(\d+)", output)
        time_m = re.search(r"Общее время:\s*([^\n]+)", output)
        cut_count  = cut_m.group(1)  if cut_m  else "?"
        up_count   = up_m.group(1)   if up_m   else None
        total_time = time_m.group(1).strip() if time_m else "?"
        lines = [
            f"{icon} <b>Нарезка завершена!</b>",
            f"✂️ Нарезано: <b>{cut_count}</b> клипов",
        ]
        if up_count:
            lines.append(f"🔍 Апскейл: <b>{up_count}</b> клипов → {res_labels.get(resolution, '')}")
        lines.append(f"⏱ Время: {h(total_time)}")
        if err_lines and cut_count == "?":
            lines.append(f"\n<pre>{h(chr(10).join(err_lines))}</pre>")

    await progress_msg.edit_text(
        "\n".join(lines),
        reply_markup=kb_done(),
        parse_mode=ParseMode.HTML,
    )


async def _run_photo_upscaler(
    progress_msg,
    method: str = "lanczos",
    resolution: str = "2k",
    session: str = "",
) -> None:
    """Запускает photo_upscaler.py и показывает прогресс."""
    res_labels    = {"1080": "1080p", "2k": "2K", "4k": "4K"}
    method_labels = {"realesrgan": "Real-ESRGAN", "lanczos": "Lanczos", "bicubic": "Bicubic"}
    header = (
        f"⏳ <b>Апскейл фото...</b>\n"
        f"Метод: {method_labels.get(method, method)} → {res_labels.get(resolution, resolution)}"
    )
    cb = make_progress_cb(progress_msg, header, interval=2.0)

    cmd = [
        "py", str(AGENTS_DIR / "upscaler" / "photo_upscaler.py"),
        "--method", method, "--resolution", resolution,
    ]
    if session:
        cmd += ["--project", session]

    rc, output = await run_agent(cmd, on_line=cb)

    icon    = "✅" if rc == 0 else "⚠️"
    done_m  = re.search(r"Обработано[:\s]+(\d+)", output)
    time_m  = re.search(r"Время[:\s]+([\w\s]+)", output)
    done_n  = done_m.group(1).strip() if done_m else "?"
    elapsed = time_m.group(1).strip() if time_m else "?"
    lines = [
        f"{icon} <b>Апскейл фото завершён!</b>",
        f"📷 Обработано: <b>{done_n}</b> фото → {res_labels.get(resolution, resolution)}",
        f"⏱ Время: {h(elapsed)}",
    ]
    await progress_msg.edit_text(
        "\n".join(lines),
        reply_markup=kb_done(),
        parse_mode=ParseMode.HTML,
    )


async def _run_assembler(
    progress_msg,
    with_subs:  bool = True,
    with_music: bool = True,
    session:    str  = "",
) -> None:
    """
    Запускает assembler.py и показывает структурированный прогресс в Telegram.
    Парсит строки [АССЕМБЛЕР] для отображения этапов.
    """
    parts = []
    if with_subs:  parts.append("субтитры")
    if with_music: parts.append("музыка")
    label = " + ".join(parts) if parts else "без субтитров/музыки"

    # Маппинг ключевых фраз → красивые названия этапов
    _STAGE_MAP = [
        ("Загрузка данных",          "📂 Загрузка данных"),
        ("Генерация project.json",   "🗂 project.json"),
        ("Монтаж сцен",              "✂️ Монтаж сцен"),
        ("Склейка",                  "🔗 Склейка интро + сцены"),
        ("Музыкальный трек",         "🎵 Музыкальный трек"),
        ("Аудио-микс",               "🎚 Аудио-микс"),
        ("Финальный экспорт",        "📤 Финальный экспорт"),
        ("Субтитры + экспорт",       "📝 Субтитры"),
    ]

    state = {
        "done_stages": [],       # [("✅ Монтаж сцен", detail)]
        "cur_stage":   "🚀 Запуск...",
        "cur_detail":  "",
        "last_edit":   0.0,
    }

    def _detect_stage(line: str) -> str | None:
        """Определить, начался ли новый этап по строке лога."""
        for keyword, label in _STAGE_MAP:
            if keyword.lower() in line.lower():
                return label
        return None

    def _build_text() -> str:
        lines_out = [f"🎬 <b>FFmpeg монтаж ({h(label)})...</b>", ""]
        for stage, detail in state["done_stages"]:
            lines_out.append(f"✅ {h(stage)}" + (f"  <i>{h(detail)}</i>" if detail else ""))
        lines_out.append(f"🔄 {h(state['cur_stage'])}")
        if state["cur_detail"]:
            lines_out.append(f"<pre>{h(state['cur_detail'][-180:])}</pre>")
        return "\n".join(lines_out)

    async def on_line(line: str) -> None:
        # Убираем префикс [АССЕМБЛЕР]
        clean = re.sub(r"^\[АССЕМБЛЕР\]\s*", "", line).strip()
        if not clean:
            return

        # Определяем смену этапа
        new_stage = _detect_stage(clean)
        if new_stage:
            # Завершаем предыдущий этап
            if state["cur_stage"] != "🚀 Запуск...":
                state["done_stages"].append((state["cur_stage"], ""))
            state["cur_stage"]  = new_stage
            state["cur_detail"] = ""
        else:
            # Накапливаем детали текущего этапа
            state["cur_detail"] = clean

        # Обновляем Telegram раз в 4 секунды
        now = time.monotonic()
        if now - state["last_edit"] >= 4.0:
            state["last_edit"] = now
            try:
                await progress_msg.edit_text(
                    _build_text(),
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass

    cmd = ["py", str(AGENTS_DIR / "assembler" / "assembler.py")]
    if session:
        cmd += ["--session", session]
    if not with_subs:
        cmd += ["--no-subs"]
    if not with_music:
        cmd += ["--no-music"]

    rc, output = await run_agent(cmd, on_line=on_line)

    # Парсим итоговые данные из вывода
    time_m = re.search(r"Время[:\s]+([\dм\sс]+)", output)
    file_m = re.search(r"📁\s+(.+\.mp4)", output)
    size_m = re.search(r"Размер[:\s]+([\d.,]+\s*МБ)", output)

    total_time = time_m.group(1).strip() if time_m else "?"
    out_file   = Path(file_m.group(1).strip()).name if file_m else "final_complete.mp4"
    out_size   = size_m.group(1).strip() if size_m else ""

    if rc == 0:
        result_lines = [
            f"✅ <b>Монтаж завершён!</b>",
            f"🎬 <b>{h(out_file)}</b>",
            f"⚙️ Режим: {h(label)}",
        ]
        if out_size:
            result_lines.append(f"📦 Размер: {h(out_size)}")
        result_lines.append(f"⏱ Время: {h(total_time)}")
        # Показать все пройденные этапы
        result_lines.append("")
        for stage, _ in state["done_stages"]:
            result_lines.append(f"  ✅ {h(stage)}")
        if state["cur_stage"] not in ("🚀 Запуск...",):
            result_lines.append(f"  ✅ {h(state['cur_stage'])}")
    else:
        tail = "\n".join(output.splitlines()[-8:])
        result_lines = [
            f"❌ <b>Ошибка монтажа!</b>",
            f"⚙️ Режим: {h(label)}",
            f"<pre>{h(tail)}</pre>",
        ]

    await progress_msg.edit_text(
        "\n".join(result_lines),
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
            "⏭ <b>Нарезка видео пропущена.</b>\n\nПайплайн полностью завершён! 🎉\n\n"
            "Запустить BLIP анализ медиа?",
            reply_markup=kb_done_with_blip(),
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
        get_main_menu_text(),
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
        get_main_menu_text(),
        reply_markup=kb_main(),
        parse_mode=ParseMode.HTML,
    )


async def cb_channels_show(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показать список каналов для переключения."""
    if not await is_allowed(update):
        return
    q = update.callback_query
    await q.answer()

    channels = get_all_channels()
    active   = get_active_channel()

    text = "📡 <b>Выберите канал:</b>\n\n"
    for ch in channels:
        is_active = active and ch["id"] == active["id"]
        mark = "✅ " if is_active else "   "
        text += f"{mark}{ch['emoji']} <b>{ch['name']}</b> ({ch['language'].upper()})\n"

    rows = []
    for ch in channels:
        is_active = active and ch["id"] == active["id"]
        label = f"{'✅ ' if is_active else ''}{ch['emoji']} {ch['name']}"
        rows.append([InlineKeyboardButton(label, callback_data=f"channels:set:{ch['id']}")])
    rows.append([BACK_BTN])

    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(rows), parse_mode=ParseMode.HTML)


async def cb_channels_set(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Установить активный канал и вернуться в главное меню."""
    if not await is_allowed(update):
        return
    q = update.callback_query
    channel_id = q.data.split(":", 2)[2]
    set_active_channel(channel_id)
    ch = get_channel_config(channel_id)
    await q.answer(f"✅ Канал: {ch['emoji']} {ch['name']}" if ch else "✅ Канал выбран")
    await q.edit_message_text(
        get_main_menu_text(),
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
    """Выбор типа промптов: фото, видео или оба → выбор платформы."""
    if not await is_allowed(update):
        return
    q     = update.callback_query
    await q.answer()
    ptype = q.data.split(":")[1]  # "photo", "video", "both"
    context.user_data["prompt_type"] = ptype
    if ptype == "photo":
        await q.edit_message_text(
            "📷 <b>Фото промпты</b>\n\nВыбери платформу:",
            reply_markup=kb_photo_platform(),
            parse_mode=ParseMode.HTML,
        )
    elif ptype == "video":
        await q.edit_message_text(
            "🎬 <b>Видео промпты</b>\n\nВыбери платформу:",
            reply_markup=kb_video_platform(),
            parse_mode=ParseMode.HTML,
        )
    else:  # both
        # Для "оба" — сразу запускаем с PixelAgent для фото + Grok для видео
        progress = await q.edit_message_text(
            "📷🎬 <b>Фото + Видео промпты</b>\n\n<pre>запускаю...</pre>",
            parse_mode=ParseMode.HTML,
        )
        await _run_combined_prompts_agent(progress)


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
    """Выбор режима: cut / cut+upscale / upscale."""
    if not await is_allowed(update):
        return
    q    = update.callback_query
    await q.answer()
    mode = q.data.split(":")[1]    # "cut" | "cut+upscale" | "upscale"
    context.user_data["cutter_mode"] = mode
    context.user_data["upscale_kind"] = "video"   # cutter всегда работает с видео

    if mode == "cut":
        progress = await q.edit_message_text(
            "✂️ <b>Нарезка видео...</b>\n\n<pre>запускаю...</pre>",
            parse_mode=ParseMode.HTML,
        )
        await _run_cutter_agent(progress, mode="cut")
    elif mode == "upscale":
        await q.edit_message_text(
            "🔍 <b>Апскейл видео (Grok)</b>\n\nМетод апскейла?",
            reply_markup=kb_cutter_method(),
            parse_mode=ParseMode.HTML,
        )
    else:
        await q.edit_message_text(
            "✂️+🔍 <b>Нарезка + Апскейл</b>\n\nМетод апскейла?",
            reply_markup=kb_cutter_method(),
            parse_mode=ParseMode.HTML,
        )


async def cb_cutter_upscale_method(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Выбор метода апскейла (видео: cutter, или фото: upscaler)."""
    if not await is_allowed(update):
        return
    q      = update.callback_query
    await q.answer()
    method = q.data.split(":")[1]
    context.user_data["cutter_method"] = method

    kind = context.user_data.get("upscale_kind", "video")
    if kind == "photo":
        # Фото-апскейл: свои разрешения (без 720)
        context.user_data["upscale_method"] = method
        await q.edit_message_text(
            "📷 <b>Апскейл фото</b>\n\nЦелевое разрешение?",
            reply_markup=kb_upscale_resolution(),
            parse_mode=ParseMode.HTML,
        )
    else:
        cutter_mode = context.user_data.get("cutter_mode", "cut+upscale")
        title = "🎬 <b>Апскейл видео</b>" if cutter_mode == "upscale" else "✂️+🔍 <b>Нарезка + Апскейл</b>"
        await q.edit_message_text(
            f"{title}\n\nЦелевое разрешение?",
            reply_markup=kb_cutter_resolution(),
            parse_mode=ParseMode.HTML,
        )


async def cb_cutter_resolution(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Выбор разрешения → запуск (cut+upscale или upscale)."""
    if not await is_allowed(update):
        return
    q          = update.callback_query
    await q.answer()
    resolution = q.data.split(":")[1]
    method     = context.user_data.get("cutter_method", "lanczos")
    mode       = context.user_data.get("cutter_mode", "cut+upscale")
    res_labels = {"720": "720p", "1080": "1080p", "2k": "2K", "4k": "4K"}
    method_labels = {"realesrgan": "Real-ESRGAN", "lanczos": "Lanczos", "bicubic": "Bicubic"}

    if mode == "upscale":
        header = (
            f"⏳ <b>Апскейл видео (Grok)</b>\n"
            f"Метод: {method_labels.get(method, method)} → {res_labels.get(resolution, resolution)}\n\n"
            f"<pre>запускаю...</pre>"
        )
    else:
        header = (
            f"⏳ <b>Нарезка + Апскейл</b>\n"
            f"Метод: {method_labels.get(method, method)} → {res_labels.get(resolution, resolution)}\n\n"
            f"<pre>запускаю...</pre>"
        )

    progress = await q.edit_message_text(header, parse_mode=ParseMode.HTML)
    await _run_cutter_agent(progress, mode=mode, method=method, resolution=resolution)


async def cb_upscale_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """🔍 Апскейл — выбор типа: фото или видео."""
    if not await is_allowed(update):
        return
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "🔍 <b>Апскейл</b>\n\nЧто апскейлить?",
        reply_markup=kb_upscale_type(),
        parse_mode=ParseMode.HTML,
    )


async def cb_upscale_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Выбор типа апскейла: photo | video."""
    if not await is_allowed(update):
        return
    q    = update.callback_query
    await q.answer()
    kind = q.data.split(":")[1]   # "photo" | "video"
    context.user_data["upscale_kind"] = kind

    if kind == "photo":
        await q.edit_message_text(
            "📷 <b>Апскейл фото</b>\n\nМетод апскейла?",
            reply_markup=kb_cutter_method(),
            parse_mode=ParseMode.HTML,
        )
    else:
        context.user_data["cutter_mode"] = "upscale"
        await q.edit_message_text(
            "🎬 <b>Апскейл видео</b>\n\nМетод апскейла?",
            reply_markup=kb_cutter_method(),
            parse_mode=ParseMode.HTML,
        )


async def cb_upscale_method(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Выбор метода апскейла фото → выбор разрешения."""
    if not await is_allowed(update):
        return
    q      = update.callback_query
    await q.answer()
    method = q.data.split(":")[1]
    context.user_data["upscale_method"] = method
    await q.edit_message_text(
        "🔍 <b>Апскейл фото</b>\n\nЦелевое разрешение?",
        reply_markup=kb_upscale_resolution(),
        parse_mode=ParseMode.HTML,
    )


async def cb_upscale_resolution(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Выбор разрешения → запуск photo_upscaler."""
    if not await is_allowed(update):
        return
    q          = update.callback_query
    await q.answer()
    resolution = q.data.split(":")[1]
    method     = context.user_data.get("upscale_method", "lanczos")
    res_labels = {"1080": "1080p", "2k": "2K", "4k": "4K"}
    method_labels = {"realesrgan": "Real-ESRGAN", "lanczos": "Lanczos", "bicubic": "Bicubic"}
    progress = await q.edit_message_text(
        f"⏳ <b>Апскейл фото...</b>\n"
        f"Метод: {method_labels.get(method, method)} → {res_labels.get(resolution, resolution)}\n\n"
        f"<pre>запускаю...</pre>",
        parse_mode=ParseMode.HTML,
    )
    await _run_photo_upscaler(progress, method=method, resolution=resolution)


async def cb_editor(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """🖥 Открыть редактор — запускает web/editor.py и даёт ссылку."""
    if not await is_allowed(update):
        return
    q = update.callback_query
    await q.answer()

    import subprocess, sys
    # Проверяем, запущен ли уже редактор
    check = subprocess.run(
        ["py", "-c",
         "import urllib.request; urllib.request.urlopen('http://localhost:5000', timeout=2)"],
        capture_output=True,
    )
    if check.returncode != 0:
        # Запускаем редактор в фоне
        subprocess.Popen(
            ["py", str(BASE_DIR / "web" / "editor.py")],
            cwd=str(BASE_DIR),
            creationflags=subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0,
        )
        msg = "🖥 <b>Редактор запущен!</b>\n\n<a href='http://localhost:5000'>http://localhost:5000</a>"
    else:
        msg = "🖥 <b>Редактор уже работает</b>\n\n<a href='http://localhost:5000'>http://localhost:5000</a>"

    await q.edit_message_text(
        msg,
        reply_markup=kb_done(),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def cb_montage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """🎬 FFmpeg монтаж — выбор: с субтитрами или без."""
    if not await is_allowed(update):
        return
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "🎬 <b>FFmpeg монтаж</b>\n\n"
        "Выберите режим сборки финального видео:\n"
        "• <i>Субтитры</i> — word-level SRT через Organetto\n"
        "• <i>Музыка</i> — фоновый трек из data/music/ (+88с)",
        reply_markup=kb_montage_subs(),
        parse_mode=ParseMode.HTML,
    )


def _run_assembler_background(session, chat_id, message_id, bot,
                               with_subs=True, with_music=True, loop=None):
    """Запускает assembler.py в фоновом потоке, обновляет TG каждые 3 сек."""
    def _tg_edit(text: str) -> None:
        if loop is None:
            return
        try:
            future = asyncio.run_coroutine_threadsafe(
                bot.edit_message_text(
                    text, chat_id=chat_id, message_id=message_id,
                    parse_mode=ParseMode.HTML,
                ),
                loop,
            )
            future.result(timeout=5)
        except Exception:
            pass

    cmd = [
        "py", str(AGENTS_DIR / "assembler" / "assembler.py"),
    ]
    if session:
        cmd += ["--session", session]
    if not with_subs:
        cmd += ["--no-subs"]
    if not with_music:
        cmd += ["--no-music"]

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )

    last_update   = time.time()
    last_line     = ""
    progress_lines: list[str] = []

    for line in process.stdout:
        line = line.strip()
        if not line:
            continue
        last_line = line
        progress_lines.append(line)

        # Обновлять TG каждые 3 сек
        if time.time() - last_update >= 3:
            text = "🎬 <b>FFmpeg монтаж (фон)...</b>\n\n"
            recent = progress_lines[-8:]
            for pl in recent:
                if "✅" in pl:
                    text += f"✅ {pl}\n"
                elif "⏳" in pl:
                    text += f"⏳ {pl}\n"
                elif "⏱️" in pl:
                    text += f"  {pl}\n"
                elif "❌" in pl or "✗" in pl:
                    text += f"❌ {pl}\n"
                elif "---" in pl or "===" in pl:
                    text += f"<b>{pl}</b>\n"
                else:
                    text += f"  {pl}\n"
            _tg_edit(text)
            last_update = time.time()

    # Финальное сообщение
    rc = process.wait()
    if rc == 0:
        final_text = (
            "🎉 <b>Монтаж завершён!</b>\n\n"
            f"✅ {last_line}\n"
            f"📁 data/output/{session}/"
        )
    else:
        final_text = f"❌ <b>Ошибка монтажа!</b>\n<pre>{last_line}</pre>"
    _tg_edit(final_text)


def start_assembler(chat_id, session, with_subs, with_music, bot, loop=None):
    """Запустить assembler в фоне. Возвращает сразу."""
    # Отправляем сообщение-прогресс синхронно через event loop
    msg_id = None
    if loop:
        try:
            future = asyncio.run_coroutine_threadsafe(
                bot.send_message(chat_id, "🎬 Запускаю монтаж в фоне..."),
                loop,
            )
            msg = future.result(timeout=5)
            msg_id = msg.message_id
        except Exception:
            pass

    thread = threading.Thread(
        target=_run_assembler_background,
        args=(session, chat_id, msg_id, bot, with_subs, with_music, loop),
        daemon=True,
    )
    thread.start()
    return "✅ Монтаж запущен в фоне! Бот свободен."


async def cb_montage_subs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Запустить assembler.py: subs_music | subs_nomusic | nosubs_music | nosubs_nomusic."""
    if not await is_allowed(update):
        return
    q      = update.callback_query
    await q.answer()
    # choice: "subs_music" | "subs_nomusic" | "nosubs_music" | "nosubs_nomusic"
    choice    = q.data.split(":")[1]
    with_subs  = "nosubs" not in choice
    with_music = "nomusic" not in choice

    parts = []
    if with_subs:  parts.append("субтитры")
    if with_music: parts.append("музыка")
    label = " + ".join(parts) if parts else "без всего"

    # Запустить в фоне через threading
    loop = asyncio.get_event_loop()
    result_msg = start_assembler(
        chat_id    = q.message.chat_id,
        session    = "",
        with_subs  = with_subs,
        with_music = with_music,
        bot        = context.bot,
        loop       = loop,
    )

    await q.edit_message_text(
        f"🎬 <b>Монтаж ({label}) запущен в фоне</b>\n\n"
        f"✅ {result_msg}\n"
        f"<i>Прогресс придёт отдельным сообщением</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_done(),
    )


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

    # PixelAgent — всегда показываем меню v1/v2 (даже если тип уже предвыбран)
    if is_api:
        cfg_env = dotenv_values(ENV_FILE)
        api_key = cfg_env.get("PIXEL_API_KEY", "").strip()
        if not api_key:
            await q.edit_message_text(
                "⚠️ <b>PIXEL_API_KEY не задан</b>\n\nДобавь ключ в <code>config/.env</code>",
                reply_markup=kb_done(), parse_mode=ParseMode.HTML,
            )
            return
        ver = cfg_env.get("PIXEL_API_VERSION", "v2").strip()
        await q.edit_message_text(
            f"🖼 <b>PixelAgent</b>\n\n⚙️ API версия: <b>{h(ver)}</b>\n\nЧто генерировать?",
            reply_markup=kb_pixel_menu(ver), parse_mode=ParseMode.HTML,
        )
        return

    # Если тип уже выбран (кнопка 📷 Фото / 🎬 Видео) — пропускаем шаг выбора
    preselected = context.user_data.get("media_type")
    if preselected:
        type_label = {"photo": "📷 Фото", "video": "🎬 Видео", "both": "📷+🎬"}[preselected]
        progress   = await q.edit_message_text(
            f"⏳ <b>Генерирую медиа...</b>\n{type_label} | {h(pname)}\n\n<pre>запускаю...</pre>",
            parse_mode=ParseMode.HTML,
        )
        await _run_media_agent(progress, "", num, preselected)
    else:
        await q.edit_message_text(
            f"🖼 <b>{h(pname)}</b>\n\nЧто генерировать?",
            reply_markup=kb_media_type(is_api=False), parse_mode=ParseMode.HTML,
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


async def cb_pixelver(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Переключение версии PixelAgent API (v1/v2) — записывает в config/.env."""
    if not await is_allowed(update):
        return
    q   = update.callback_query
    await q.answer()
    ver = q.data.split(":")[1]  # "v1" или "v2"

    from dotenv import set_key as _set_key
    _set_key(str(ENV_FILE), "PIXEL_API_VERSION", ver)

    await q.edit_message_text(
        f"🖼 <b>PixelAgent</b>\n\n⚙️ API версия: <b>{h(ver)}</b>\n\nЧто генерировать?",
        reply_markup=kb_pixel_menu(ver), parse_mode=ParseMode.HTML,
    )


# ── BLIP анализ ───────────────────────────────────────────────────────────────

async def cb_blip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_allowed(update):
        return
    q = update.callback_query
    await q.answer()
    _SESSION_RE_BOT = re.compile(r"^Video_\d{8}_\d{6}$")
    session = None
    if MEDIA_DIR.exists():
        folders = [d for d in MEDIA_DIR.iterdir()
                   if d.is_dir() and _SESSION_RE_BOT.match(d.name)]
        if folders:
            session = max(folders, key=lambda f: f.name).name
    if not session:
        await q.edit_message_text(
            "❌ <b>Нет медиа для анализа</b>\n\nСначала сгенерируй фото или видео.",
            reply_markup=kb_done(), parse_mode=ParseMode.HTML,
        )
        return
    context.user_data["blip_session"] = session
    await q.edit_message_text(
        f"🔍 <b>BLIP анализ</b>\n\n📁 Сессия: <code>{h(session)}</code>\n\nЧто анализировать?",
        reply_markup=kb_blip_type(), parse_mode=ParseMode.HTML,
    )


async def cb_blip_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_allowed(update):
        return
    q        = update.callback_query
    await q.answer()
    gen_type = q.data.split(":")[1]
    session  = context.user_data.get("blip_session", "")
    type_icons = {"photo": "📸 Фото", "video": "🎬 Видео", "both": "📸🎬 Оба"}
    progress = await q.edit_message_text(
        f"🔍 <b>BLIP анализ...</b>\n{type_icons.get(gen_type, gen_type)}\n\n<pre>запускаю...</pre>",
        parse_mode=ParseMode.HTML,
    )
    await _run_blip_agent(progress, gen_type, session=session)


async def _run_regen_agent(
    progress_msg,
    session: str,
    with_video: bool = False,
) -> None:
    """Запускает regen_agent: перегенерирует промпты + фото для плохих ID из blip_report."""
    # Читаем имена мастер-промптов из последнего запуска prompt_generator
    pp = _read_prompt_progress()
    photo_master = (pp or {}).get("master_photo", "")
    video_master = (pp or {}).get("master_video", "") if with_video else ""

    if not photo_master:
        # Fallback: берём первый доступный
        photo_dir = BASE_DIR / "config" / "master_prompts" / "photo"
        masters = sorted(f.name for f in photo_dir.glob("*.txt")) if photo_dir.exists() else []
        photo_master = masters[0] if masters else ""

    if not photo_master:
        await progress_msg.edit_text(
            "❌ <b>Не удалось определить мастер-промпт для фото.</b>\n"
            "Запусти генерацию промптов через меню.",
            reply_markup=kb_done(), parse_mode=ParseMode.HTML,
        )
        return

    try:
        REGEN_PROGRESS_FILE.unlink(missing_ok=True)
    except Exception:
        pass

    cmd = [
        "py", str(AGENTS_DIR / "regen_agent" / "regen_agent.py"),
        "--project",      session,
        "--photo-master", photo_master,
    ]
    if video_master:
        cmd += ["--video-master", video_master]

    task = asyncio.create_task(run_agent(cmd))
    t0   = time.monotonic()

    phase_labels = {
        "prompts":       "♻️ Регенерирую фото-промпты...",
        "video_prompts": "♻️ Регенерирую видео-промпты...",
        "photos":        "🎨 Генерирую фото (PixelAgent)...",
    }

    while not task.done():
        await asyncio.sleep(3.0)
        p = _read_regen_progress()
        elapsed = int(time.monotonic() - t0)
        if p and p.get("total", 0) > 0:
            phase   = p.get("phase", "prompts")
            cur     = p.get("current", 0)
            total   = p["total"]
            bar     = _prompt_bar(cur, total)
            pct     = int(cur / total * 100) if total else 0
            label   = phase_labels.get(phase, "♻️ Регенерация...")
            bad_n   = len(p.get("bad_ids", []))
            if phase == "photos":
                # Читаем pixel_progress для фото-фазы
                px = _read_pixel_progress()
                if px and px.get("total", 0) > 0:
                    px_cur = px.get("current", 0)
                    px_tot = px["total"]
                    px_bar = _prompt_bar(px_cur, px_tot)
                    px_pct = int(px_cur / px_tot * 100) if px_tot else 0
                    failed_n = len(px.get("failed", []))
                    text = (
                        f"🎨 <b>PixelAgent генерирует...</b>\n"
                        f"━━━━━━━━━━━━━━━━\n"
                        f"📊 Готово: <b>{px_cur}/{px_tot}</b>\n"
                        f"<code>{px_bar}</code> {px_pct}%"
                        + (f"\n❌ Ошибок: {failed_n}" if failed_n else "")
                    )
                else:
                    text = f"🎨 <b>Генерирую фото...</b>\n\n<pre>запускаю PixelAgent... ({elapsed}с)</pre>"
            else:
                # cur=0 пока батчи идут параллельно — показываем "батчи запущены"
                if cur == 0 and total > 0:
                    text = (
                        f"{label}\n"
                        f"━━━━━━━━━━━━━━━━\n"
                        f"📊 Плохих: <b>{bad_n}</b> | ⏳ Claude генерирует... ({elapsed}с)\n"
                        f"<i>Батчи запущены параллельно, ждём результата</i>"
                    )
                else:
                    text = (
                        f"{label}\n"
                        f"━━━━━━━━━━━━━━━━\n"
                        f"📊 Промптов: <b>{cur}/{total}</b> (плохих: {bad_n})\n"
                        f"<code>{bar}</code> {pct}%"
                    )
        else:
            text = f"♻️ <b>Регенерация...</b>\n\n<pre>запускаю... ({elapsed}с)</pre>"
        try:
            await progress_msg.edit_text(text, parse_mode=ParseMode.HTML)
        except Exception:
            pass

    rc, output = task.result()

    m_sum = re.search(r"REGEN_SUMMARY:\s*(\{.+\})", output)
    summary: dict = {}
    if m_sum:
        try:
            summary = json.loads(m_sum.group(1))
        except Exception:
            pass

    if rc != 0 and not summary:
        err_tail = "\n".join(output.splitlines()[-10:])
        await progress_msg.edit_text(
            f"❌ <b>Регенерация завершилась с ошибкой!</b>\n\n<pre>{h(err_tail)}</pre>",
            reply_markup=kb_done(), parse_mode=ParseMode.HTML,
        )
        return

    regen_n  = summary.get("regenerated", 0)
    bad_n    = len(summary.get("bad_ids",  []))
    failed   = summary.get("failed", [])
    with_vid = summary.get("with_video", False)

    lines = [f"✅ <b>Регенерация завершена!</b>\n"]
    lines.append(f"🔄 Плохих фото было: <b>{bad_n}</b>")
    lines.append(f"✅ Перегенерировано: <b>{regen_n}</b>")
    if failed:
        lines.append(f"❌ Не удалось: <b>{len(failed)}</b> → {failed[:10]}")
    if with_vid:
        lines.append("🎬 Видео-промпты также обновлены")
    lines.append("\n<i>Рекомендуется повторить BLIP анализ для проверки</i>")

    await progress_msg.edit_text(
        "\n".join(lines),
        reply_markup=kb_done_with_blip(), parse_mode=ParseMode.HTML,
    )


async def cb_blip_regen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_allowed(update):
        return
    q          = update.callback_query
    await q.answer()
    regen_type = q.data.split(":")[1]
    session    = context.user_data.get("blip_session", "")
    type_icons = {"photo": "📸 Фото", "video": "🎬 Видео", "both": "📸🎬 Оба"}
    progress = await q.edit_message_text(
        f"♻️ <b>Перегенерация ({type_icons.get(regen_type, regen_type)})...</b>\n\n<pre>запускаю...</pre>",
        parse_mode=ParseMode.HTML,
    )
    if regen_type in ("photo", "both"):
        # Полный цикл: промпты (фото + видео) → фото
        with_video = regen_type == "both"
        await _run_regen_agent(progress, session, with_video=with_video)
    elif regen_type == "video":
        # Только видео-медиа (без смены промптов)
        await _run_media_agent(progress, "", "2", "video", session=session)


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


# ── Video Validator ───────────────────────────────────────────────────────────

def _read_video_validator_progress() -> dict | None:
    try:
        if VIDEO_VALIDATOR_PROGRESS.exists():
            return json.loads(VIDEO_VALIDATOR_PROGRESS.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


def _read_norm_progress() -> dict | None:
    try:
        if NORM_UPSCALE_PROGRESS.exists():
            return json.loads(NORM_UPSCALE_PROGRESS.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


async def cb_video_validator(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Запускает агент валидации видео через Molmo 2."""
    if not await is_allowed(update):
        return
    q = update.callback_query
    await q.answer()

    session = _current_session()
    if not session:
        await q.edit_message_text(
            "❌ Нет активной сессии. Сначала запусти транскрипцию.",
            reply_markup=InlineKeyboardMarkup([[BACK_BTN]]),
        )
        return

    videos_dir = MEDIA_DIR / session / "videos"
    total = len(list(videos_dir.glob("video_*.mp4"))) if videos_dir.exists() else 0

    msg = await q.edit_message_text(
        f"🔍 <b>Валидация видео (Molmo 2)</b>\n\n"
        f"Сессия: <code>{session}</code>\n"
        f"Видео:  {total}\n\n"
        f"⏳ Загружаю модель и запускаю анализ...",
        parse_mode=ParseMode.HTML,
    )

    cmd = [
        "py",
        str(AGENTS_DIR / "video_validator" / "video_validator.py"),
        "--session", session,
    ]

    async def _stream_progress():
        bar_chars = "█"
        last_text  = ""
        while True:
            await asyncio.sleep(8)
            p = _read_video_validator_progress()
            if not p or p.get("session") != session:
                continue

            cur   = p.get("current", 0)
            tot   = p.get("total", 1)
            valid = p.get("valid", 0)
            rep   = p.get("replaced", 0)
            fail  = p.get("failed", 0)
            pct   = int(cur / tot * 16) if tot else 0
            bar   = bar_chars * pct + "░" * (16 - pct)

            new_text = (
                f"🔍 <b>Molmo 2 анализирует видео...</b>\n\n"
                f"<code> {bar} {cur}/{tot}</code>\n\n"
                f"✅ Хорошие:        {valid}\n"
                f"🔄 Заменены стоком: {rep}\n"
                f"❌ Плохие:          {fail}"
            )
            if new_text != last_text:
                try:
                    await msg.edit_text(new_text, parse_mode=ParseMode.HTML)
                    last_text = new_text
                except Exception:
                    pass

            if p.get("status") == "done":
                break

    # Запускаем агент и стриминг прогресса параллельно
    rc, output = await asyncio.gather(
        run_agent(cmd),
        _stream_progress(),
    )
    rc, output = rc  # run_agent возвращает (rc, output)

    p = _read_video_validator_progress()
    valid = p.get("valid", 0)   if p else 0
    rep   = p.get("replaced", 0) if p else 0
    fail  = p.get("failed", 0)  if p else 0

    # Найти плохие ID для кнопки
    bad_ids: list[int] = []
    try:
        rp = (BASE_DIR / "data" / "transcripts" / session / "video_validation_report.json")
        if rp.exists():
            report = json.loads(rp.read_text(encoding="utf-8"))
            bad_ids = [v["id"] for v in report.get("videos", [])
                       if not v.get("valid") and not v.get("replaced")]
    except Exception:
        pass

    icon = "✅" if rc == 0 else "⚠️"
    kb_rows = [[BACK_BTN]]
    if bad_ids:
        kb_rows.insert(0, [InlineKeyboardButton(
            "👁 Показать плохие", callback_data="vidval:show_bad",
        )])

    await msg.edit_text(
        f"{icon} <b>Валидация завершена!</b>\n\n"
        f"📊 Всего:    {total}\n"
        f"✅ Хорошие:  {valid}\n"
        f"🔄 Заменены: {rep}\n"
        f"❌ Не заменены: {fail}"
        + (f"\n\n⚠️ Плохие: #{', #'.join(str(i) for i in bad_ids[:20])}" if bad_ids else ""),
        reply_markup=InlineKeyboardMarkup(kb_rows),
        parse_mode=ParseMode.HTML,
    )

    # ── Автозапуск нормализации + апскейла ───────────────────────────────
    norm_msg = await q.message.answer(
        "⏳ <b>Нормализую и апскейлю видео...</b>\n\n"
        "<code> ░░░░░░░░░░░░░░░░ 0/" + str(total) + "</code>\n\n"
        "⚡ ~15 сек/видео на RTX 3060",
        parse_mode=ParseMode.HTML,
    )

    norm_cmd = [
        "py",
        str(AGENTS_DIR / "video_cutter" / "video_cutter.py"),
        "--mode", "normalize+upscale",
        "--session", session,
        "--duration", "10",
        "--target-resolution", "1920x1080",
        "--method", "lanczos",
    ]

    async def _stream_norm_progress():
        bar_chars = "█"
        last_text = ""
        while True:
            await asyncio.sleep(6)
            p = _read_norm_progress()
            if not p or p.get("session") != session:
                continue

            cur  = p.get("current", 0)
            tot  = p.get("total", 1)
            ok   = p.get("ok", 0)
            fail = p.get("failed", 0)
            pct  = int(cur / tot * 16) if tot else 0
            bar  = bar_chars * pct + "░" * (16 - pct)

            new_text = (
                f"⏳ <b>Нормализую и апскейлю видео...</b>\n\n"
                f"<code> {bar} {cur}/{tot}</code>\n\n"
                f"✅ Готово: {ok}\n"
                f"❌ Ошибки: {fail}\n\n"
                f"⚡ ~15 сек/видео на RTX 3060"
            )
            if new_text != last_text:
                try:
                    await norm_msg.edit_text(new_text, parse_mode=ParseMode.HTML)
                    last_text = new_text
                except Exception:
                    pass

            if p.get("status") == "done":
                break

    norm_rc, _ = await asyncio.gather(
        run_agent(norm_cmd),
        _stream_norm_progress(),
    )
    norm_rc, _ = norm_rc

    p2 = _read_norm_progress()
    ok2   = p2.get("ok", 0)   if p2 else 0
    fail2 = p2.get("failed", 0) if p2 else 0

    icon2 = "✅" if norm_rc == 0 else "⚠️"
    upscaled_dir = MEDIA_DIR / session / "upscaled"
    await norm_msg.edit_text(
        f"{icon2} <b>Нормализация и апскейл завершены!</b>\n\n"
        f"✅ Обработано: {ok2}/{total}\n"
        f"❌ Ошибки:     {fail2}\n\n"
        f"📁 <code>{upscaled_dir}</code>",
        reply_markup=InlineKeyboardMarkup([[BACK_BTN]]),
        parse_mode=ParseMode.HTML,
    )


async def cb_vidval_show_bad(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправляет скриншоты плохих видео (первые кадры)."""
    if not await is_allowed(update):
        return
    q = update.callback_query
    await q.answer()

    session = _current_session()
    if not session:
        return

    try:
        rp = BASE_DIR / "data" / "transcripts" / session / "video_validation_report.json"
        report = json.loads(rp.read_text(encoding="utf-8"))
        bad = [v for v in report.get("videos", [])
               if not v.get("valid") and not v.get("replaced")][:10]
    except Exception:
        await q.edit_message_text("❌ Отчёт не найден", reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        return

    if not bad:
        await q.edit_message_text("✅ Плохих видео нет!", reply_markup=InlineKeyboardMarkup([[BACK_BTN]]))
        return

    await q.edit_message_text(
        f"👁 Плохие видео ({len(bad)} шт.) — отправляю кадры...",
        parse_mode=ParseMode.HTML,
    )

    for v in bad:
        video_path = MEDIA_DIR / session / "videos" / v["file"]
        frame_path = BASE_DIR / "temp" / f"bad_frame_{v['id']}.jpg"
        try:
            import subprocess
            subprocess.run([
                "ffmpeg", "-y", "-ss", "1", "-i", str(video_path),
                "-vframes", "1", "-q:v", "2", str(frame_path),
            ], capture_output=True)
            if frame_path.exists():
                with open(frame_path, "rb") as f:
                    await context.bot.send_photo(
                        chat_id=q.message.chat_id,
                        photo=f,
                        caption=f"❌ #{v['id']} — {v['reason']}",
                    )
        except Exception as e:
            await context.bot.send_message(
                chat_id=q.message.chat_id,
                text=f"❌ #{v['id']} — {v['reason']} (скрин не удался: {e})",
            )

    await context.bot.send_message(
        chat_id=q.message.chat_id,
        text="👆 Все плохие видео показаны.",
        reply_markup=InlineKeyboardMarkup([[BACK_BTN]]),
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
    app.add_handler(CallbackQueryHandler(cb_upscale_menu,        pattern=r"^menu:upscale$"))
    app.add_handler(CallbackQueryHandler(cb_upscale_type,        pattern=r"^uptype:"))
    app.add_handler(CallbackQueryHandler(cb_upscale_resolution,  pattern=r"^upres:"))
    app.add_handler(CallbackQueryHandler(cb_pcutter,             pattern=r"^pcutter:"))
    app.add_handler(CallbackQueryHandler(cb_montage,             pattern=r"^menu:montage$"))
    app.add_handler(CallbackQueryHandler(cb_editor,              pattern=r"^menu:editor$"))
    app.add_handler(CallbackQueryHandler(cb_montage_subs,        pattern=r"^montage:"))
    app.add_handler(CallbackQueryHandler(cb_validation,     pattern=r"^menu:validation$"))
    app.add_handler(CallbackQueryHandler(cb_blip,           pattern=r"^menu:blip$"))
    app.add_handler(CallbackQueryHandler(cb_blip_type,      pattern=r"^blip:"))
    app.add_handler(CallbackQueryHandler(cb_blip_regen,     pattern=r"^blip_regen:"))
    app.add_handler(CallbackQueryHandler(cb_cut,            pattern=r"^cut:"))
    app.add_handler(CallbackQueryHandler(cb_platform,       pattern=r"^platform:"))
    app.add_handler(CallbackQueryHandler(cb_mplatform,      pattern=r"^mplatform:"))
    app.add_handler(CallbackQueryHandler(cb_mtype,          pattern=r"^mtype:"))
    app.add_handler(CallbackQueryHandler(cb_gmodel,         pattern=r"^gmodel:"))
    app.add_handler(CallbackQueryHandler(cb_pixelver,       pattern=r"^pixelver:"))
    app.add_handler(CallbackQueryHandler(cb_validate,       pattern=r"^validate:"))
    app.add_handler(CallbackQueryHandler(cb_video_validator,   pattern=r"^menu:video_validator$"))
    app.add_handler(CallbackQueryHandler(cb_vidval_show_bad,   pattern=r"^vidval:show_bad$"))

    # Каналы
    app.add_handler(CallbackQueryHandler(cb_channels_show, pattern=r"^channels:show$"))
    app.add_handler(CallbackQueryHandler(cb_channels_set,  pattern=r"^channels:set:"))

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
