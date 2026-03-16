import base64
import logging
import os
import re
import tempfile
import uuid
from pathlib import Path

import httpx
import yt_dlp
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ── Config ────────────────────────────────────────────────────────────────────

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
LOCAL_BOT_API = os.getenv("LOCAL_BOT_API_SERVER", "")
MAX_SIZE_MB = 2000 if LOCAL_BOT_API else 49
RATES_CHAT_ID = os.getenv("RATES_CHAT_ID", "")

PRIVAT_API = "https://api.privatbank.ua/p24api/pubinfo?json&exchange&coursid=5"
CURRENCY_LABELS = {"USD": "Долар", "EUR": "Євро", "GBP": "Фунт стерлінгів"}

# Cookies для YouTube (base64-encoded cookies.txt, задається як env змінна)
_COOKIES_FILE: str | None = None


def _init_cookies() -> None:
    global _COOKIES_FILE
    encoded = os.getenv("YOUTUBE_COOKIES", "")
    if not encoded:
        return
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".txt", mode="wb")
    tmp.write(base64.b64decode(encoded))
    tmp.close()
    _COOKIES_FILE = tmp.name
    log.info("YouTube cookies завантажено з env")


SUPPORTED_DOMAINS = (
    "tiktok.com",
    "vm.tiktok.com",
    "twitter.com",
    "x.com",
    "instagram.com",
    "instagr.am",
    "youtube.com",
    "youtu.be",
    "music.youtube.com",
)

YOUTUBE_MUSIC_DOMAINS = ("music.youtube.com",)
YOUTUBE_VIDEO_DOMAINS = ("youtube.com", "youtu.be")

QUALITY_FORMATS = {
    "360":  "best[height<=360][ext=mp4]/best[height<=360]",
    "480":  "best[height<=480][ext=mp4]/best[height<=480]",
    "720":  "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best[height<=720]",
    "1080": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best[height<=1080]",
    "best": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
}

URL_RE = re.compile(r"https?://[^\s]+")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ── PrivatBank rates ──────────────────────────────────────────────────────────


async def fetch_privat_rates() -> str:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(PRIVAT_API)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        log.warning("Не вдалося отримати курси: %s", e)
        return "❌ Не вдалося отримати курси валют."

    lines = ["💱 *Курс ПриватБанку (готівка):*\n"]
    for item in data:
        ccy = item.get("ccy", "")
        if ccy in CURRENCY_LABELS:
            buy = item.get("buy", "—")
            sale = item.get("sale", "—")
            lines.append(f"*{ccy}* — {CURRENCY_LABELS[ccy]}\n  купівля: `{buy}` / продаж: `{sale}`")

    if len(lines) == 1:
        return "❌ Не знайдено потрібних валют у відповіді API."
    return "\n".join(lines)


async def cmd_rates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status = await update.message.reply_text("⏳ Отримую курси...")
    text = await fetch_privat_rates()
    await status.edit_text(text, parse_mode="Markdown")


async def send_rates_job(context: ContextTypes.DEFAULT_TYPE):
    if not RATES_CHAT_ID:
        return
    text = await fetch_privat_rates()
    await context.bot.send_message(chat_id=RATES_CHAT_ID, text=text, parse_mode="Markdown")


# ── Helpers ───────────────────────────────────────────────────────────────────


def is_supported(url: str) -> bool:
    return any(domain in url for domain in SUPPORTED_DOMAINS)


def is_youtube_music(url: str) -> bool:
    return any(domain in url for domain in YOUTUBE_MUSIC_DOMAINS)


def is_youtube_video(url: str) -> bool:
    return any(domain in url for domain in YOUTUBE_VIDEO_DOMAINS) and not is_youtube_music(url)


def download_audio(url: str, out_dir: str) -> str | None:
    ydl_opts = {
        "outtmpl": os.path.join(out_dir, "%(title)s.%(ext)s"),
        "format": "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio",
        "quiet": True,
        "no_warnings": True,
        "extractor_args": {"youtube": {"player_client": ["android"]}},
    }
    if _COOKIES_FILE:
        ydl_opts["cookiefile"] = _COOKIES_FILE

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)
        if os.path.exists(filename):
            return filename
        for ext in ("m4a", "webm", "opus", "ogg"):
            candidate = str(Path(filename).with_suffix(f".{ext}"))
            if os.path.exists(candidate):
                return candidate
        return None


def download_video(url: str, out_dir: str, quality: str = "best") -> str | None:
    fmt = QUALITY_FORMATS.get(quality, QUALITY_FORMATS["best"])

    ydl_opts = {
        "outtmpl": os.path.join(out_dir, "%(id)s.%(ext)s"),
        "format": fmt,
        "quiet": True,
        "no_warnings": True,
        "merge_output_format": "mp4",
        "extractor_args": {"youtube": {"player_client": ["android"]}},
    }
    if _COOKIES_FILE:
        ydl_opts["cookiefile"] = _COOKIES_FILE

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            # yt-dlp може змінити розширення після merge
            for ext in ("mp4", "mkv", "webm", "avi"):
                candidate = str(Path(filename).with_suffix(f".{ext}"))
                if os.path.exists(candidate):
                    return candidate
            if os.path.exists(filename):
                return filename
            return None
    except yt_dlp.utils.DownloadError:
        # Fallback: якщо якість недоступна — беремо найкращий мuxed потік
        if quality != "best":
            log.warning("Якість %s недоступна, завантажую найкращий доступний варіант", quality)
            ydl_opts["format"] = "best[ext=mp4]/best"
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                filename = ydl.prepare_filename(info)
                for ext in ("mp4", "mkv", "webm"):
                    candidate = str(Path(filename).with_suffix(f".{ext}"))
                    if os.path.exists(candidate):
                        return candidate
                return filename if os.path.exists(filename) else None
        raise


async def _send_file(msg, filepath: str, audio_mode: bool):
    with open(filepath, "rb") as f:
        if audio_mode:
            await msg.reply_audio(f)
        else:
            await msg.reply_video(f, supports_streaming=True)


# ── Handlers ──────────────────────────────────────────────────────────────────


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привіт! Кидай посилання — я завантажу відео або аудіо.\n\n"
        "Підтримується:\n"
        "• TikTok, Twitter/X, Instagram — відео\n"
        "• YouTube (youtube.com, youtu.be) — відео з вибором якості\n"
        "• YouTube Music (music.youtube.com) — аудіо\n\n"
        f"Ліміт файлу: {MAX_SIZE_MB} MB"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return

    text = msg.text or msg.caption or ""
    urls = [u for u in URL_RE.findall(text) if is_supported(u)]

    if not urls:
        return

    for url in urls:
        if is_youtube_music(url):
            # Аудіо — завантажуємо одразу
            status = await msg.reply_text("⏳ Завантажую аудіо...")
            try:
                with tempfile.TemporaryDirectory() as tmp:
                    filepath = download_audio(url, tmp)
                    if filepath is None:
                        await status.edit_text("❌ Не вдалося завантажити файл.")
                        continue
                    size_mb = os.path.getsize(filepath) / 1024 / 1024
                    if size_mb > MAX_SIZE_MB:
                        await status.edit_text(
                            f"❌ Файл {size_mb:.1f} MB — перевищує ліміт {MAX_SIZE_MB} MB."
                        )
                        continue
                    await status.edit_text("📤 Відправляю...")
                    await _send_file(msg, filepath, audio_mode=True)
                    await status.delete()
            except yt_dlp.utils.DownloadError as e:
                log.warning("DownloadError for %s: %s", url, e)
                await status.edit_text(f"❌ Помилка завантаження:\n{e}")
            except Exception:
                log.exception("Unexpected error for %s", url)
                await status.edit_text("❌ Сталась несподівана помилка.")

        elif is_youtube_video(url):
            # YouTube відео — показуємо вибір якості
            dl_id = str(uuid.uuid4())[:8]
            context.bot_data[dl_id] = {
                "url": url,
                "chat_id": msg.chat_id,
                "message_id": msg.message_id,
            }

            keyboard = [
                [
                    InlineKeyboardButton("360p", callback_data=f"dl:{dl_id}:360"),
                    InlineKeyboardButton("480p", callback_data=f"dl:{dl_id}:480"),
                    InlineKeyboardButton("720p", callback_data=f"dl:{dl_id}:720"),
                    InlineKeyboardButton("1080p", callback_data=f"dl:{dl_id}:1080"),
                    InlineKeyboardButton("Найкраща", callback_data=f"dl:{dl_id}:best"),
                ]
            ]
            await msg.reply_text(
                "🎬 Обери якість відео:",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )

        else:
            # Інші платформи — завантажуємо одразу
            status = await msg.reply_text("⏳ Завантажую...")
            try:
                with tempfile.TemporaryDirectory() as tmp:
                    filepath = download_video(url, tmp)
                    if filepath is None:
                        await status.edit_text("❌ Не вдалося завантажити файл.")
                        continue
                    size_mb = os.path.getsize(filepath) / 1024 / 1024
                    if size_mb > MAX_SIZE_MB:
                        await status.edit_text(
                            f"❌ Файл {size_mb:.1f} MB — перевищує ліміт {MAX_SIZE_MB} MB."
                        )
                        continue
                    await status.edit_text("📤 Відправляю...")
                    await _send_file(msg, filepath, audio_mode=False)
                    await status.delete()
            except yt_dlp.utils.DownloadError as e:
                log.warning("DownloadError for %s: %s", url, e)
                await status.edit_text(f"❌ Помилка завантаження:\n{e}")
            except Exception:
                log.exception("Unexpected error for %s", url)
                await status.edit_text("❌ Сталась несподівана помилка.")


async def handle_quality_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":", 2)
    if len(parts) != 3 or parts[0] != "dl":
        return

    _, dl_id, quality = parts
    data = context.bot_data.pop(dl_id, None)
    if data is None:
        await query.edit_message_text("❌ Сесія завантаження застаріла. Надішли посилання знову.")
        return

    url = data["url"]
    quality_label = quality if quality != "best" else "найкраща"
    await query.edit_message_text(f"⏳ Завантажую відео ({quality_label})...")

    try:
        with tempfile.TemporaryDirectory() as tmp:
            filepath = download_video(url, tmp, quality=quality)
            if filepath is None:
                await query.edit_message_text("❌ Не вдалося завантажити файл.")
                return

            size_mb = os.path.getsize(filepath) / 1024 / 1024
            if size_mb > MAX_SIZE_MB:
                await query.edit_message_text(
                    f"❌ Файл {size_mb:.1f} MB — перевищує ліміт {MAX_SIZE_MB} MB."
                )
                return

            await query.edit_message_text("📤 Відправляю...")
            chat_id = data["chat_id"]
            with open(filepath, "rb") as f:
                await context.bot.send_video(
                    chat_id=chat_id,
                    video=f,
                    supports_streaming=True,
                )
            await query.delete_message()

    except yt_dlp.utils.DownloadError as e:
        log.warning("DownloadError for %s: %s", url, e)
        await query.edit_message_text(f"❌ Помилка завантаження:\n{e}")
    except Exception:
        log.exception("Unexpected error for %s", url)
        await query.edit_message_text("❌ Сталась несподівана помилка.")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не задано!")

    _init_cookies()

    builder = ApplicationBuilder().token(BOT_TOKEN)
    if LOCAL_BOT_API:
        builder = builder.base_url(f"{LOCAL_BOT_API}/bot").local_mode(True)
        log.info("Використовується локальний Bot API сервер: %s", LOCAL_BOT_API)

    app = builder.build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("rates", cmd_rates))
    app.add_handler(MessageHandler(filters.TEXT | filters.CAPTION, handle_message))
    app.add_handler(CallbackQueryHandler(handle_quality_callback, pattern=r"^dl:"))

    if RATES_CHAT_ID:
        app.job_queue.run_repeating(send_rates_job, interval=8 * 3600, first=10)
        log.info("Авторозсилка курсів кожні 8 год у чат %s", RATES_CHAT_ID)

    log.info("Bot started (max file size: %d MB)", MAX_SIZE_MB)
    app.run_polling()
