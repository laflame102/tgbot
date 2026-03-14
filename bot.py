import logging
import os
import re
import tempfile
from pathlib import Path

import yt_dlp
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

# ── Config ────────────────────────────────────────────────────────────────────

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
MAX_SIZE_MB = 49

SUPPORTED_DOMAINS = (
    "tiktok.com",
    "vm.tiktok.com",
    "twitter.com",
    "x.com",
    "instagram.com",
    "instagr.am",
)

URL_RE = re.compile(r"https?://[^\s]+")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ── Helpers ───────────────────────────────────────────────────────────────────

def is_supported(url: str) -> bool:
    return any(domain in url for domain in SUPPORTED_DOMAINS)


def download_video(url: str, out_dir: str) -> str | None:
    ydl_opts = {
        "outtmpl": os.path.join(out_dir, "%(id)s.%(ext)s"),
        "format": "best[ext=mp4]/best",
        "quiet": True,
        "no_warnings": True,
        # cookies потрібні для Instagram — підклади cookies.txt поруч з bot.py
        # "cookiefile": "cookies.txt",
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)
        if not os.path.exists(filename):
            filename = str(Path(filename).with_suffix(".mp4"))
        return filename if os.path.exists(filename) else None


# ── Handlers ──────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привіт! Кидай посилання на TikTok, Twitter/X або Instagram — "
        "я завантажу відео і скину сюди."
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
        status = await msg.reply_text("⏳ Завантажую...")

        try:
            with tempfile.TemporaryDirectory() as tmp:
                filepath = download_video(url, tmp)

                if filepath is None:
                    await status.edit_text("❌ Не вдалося завантажити відео.")
                    continue

                size_mb = os.path.getsize(filepath) / 1024 / 1024
                if size_mb > MAX_SIZE_MB:
                    await status.edit_text(
                        f"❌ Відео {size_mb:.1f} MB — перевищує ліміт {MAX_SIZE_MB} MB."
                    )
                    continue

                await status.edit_text("📤 Відправляю...")
                with open(filepath, "rb") as f:
                    await msg.reply_video(f)
                await status.delete()

        except yt_dlp.utils.DownloadError as e:
            log.warning("DownloadError for %s: %s", url, e)
            await status.edit_text(f"❌ Помилка завантаження:\n{e}")
        except Exception:
            log.exception("Unexpected error for %s", url)
            await status.edit_text("❌ Сталась несподівана помилка.")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не задано!")

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(MessageHandler(filters.TEXT | filters.CAPTION, handle_message))

    log.info("Bot started")
    app.run_polling()