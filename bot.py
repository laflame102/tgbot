import asyncio
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
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputMediaVideo,
)
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

# PRIVAT_HISTORY_API = "https://api.privatbank.ua/p24api/exchange_rates?json&date={date}"
# CURRENCY_LABELS = {"USD": "Долар", "EUR": "Євро", "GBP": "Фунт стерлінгів"}
# CURRENCY_COLORS = {"USD": "#2ecc71", "EUR": "#5b9bd5", "GBP": "#e74c3c"}

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
)

TWITTER_STATUS_RE = re.compile(r"(?:twitter\.com|x\.com)/[^/]+/status/(\d+)")

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

# ── PrivatBank rates (вимкнено) ───────────────────────────────────────────────

# PERIOD_LABELS = {7: "7 днів", 30: "Місяць", 90: "3 місяці", 365: "Рік"}
# _FETCH_SEM = asyncio.Semaphore(15)
#
#
# def rates_keyboard(active_days: int) -> InlineKeyboardMarkup:
#     buttons = [
#         InlineKeyboardButton(
#             f"✓ {label}" if days == active_days else label,
#             callback_data=f"rates_period:{days}",
#         )
#         for days, label in PERIOD_LABELS.items()
#     ]
#     return InlineKeyboardMarkup([buttons])
#
#
# async def fetch_rates_history(days: int) -> dict:
#     history = {ccy: [] for ccy in CURRENCY_LABELS}
#
#     async def fetch_day(client: httpx.AsyncClient, i: int):
#         day = datetime.now() - timedelta(days=i)
#         date_str = day.strftime("%d.%m.%Y")
#         async with _FETCH_SEM:
#             try:
#                 resp = await client.get(PRIVAT_HISTORY_API.format(date=date_str))
#                 resp.raise_for_status()
#                 return day.date(), resp.json()
#             except Exception as e:
#                 log.warning("Помилка курсів за %s: %s", date_str, e)
#                 return day.date(), None
#
#     async with httpx.AsyncClient(timeout=20) as client:
#         results = await asyncio.gather(*[fetch_day(client, i) for i in range(days - 1, -1, -1)])
#
#     for date, data in sorted(results, key=lambda x: x[0]):
#         if not data:
#             continue
#         for rate in data.get("exchangeRate", []):
#             ccy = rate.get("currency")
#             if ccy not in CURRENCY_LABELS:
#                 continue
#             buy = rate.get("purchaseRate") or rate.get("purchaseRateNB")
#             sale = rate.get("saleRate") or rate.get("saleRateNB")
#             if buy and sale:
#                 history[ccy].append((date, float(buy), float(sale)))
#
#     return history
#
#
# def build_rates_chart(history: dict, days: int) -> io.BytesIO:
#     fig, ax = plt.subplots(figsize=(10, 5))
#     fig.patch.set_facecolor("#1a1a2e")
#     ax.set_facecolor("#16213e")
#
#     marker = "o" if days <= 30 else None
#     ms = 4 if days <= 30 else None
#
#     for ccy, points in history.items():
#         if not points:
#             continue
#         dates = [p[0] for p in points]
#         sales = [p[2] for p in points]
#         color = CURRENCY_COLORS[ccy]
#         ax.plot(dates, sales, marker=marker, markersize=ms, label=ccy, color=color, linewidth=2)
#         ax.annotate(f"{sales[0]:.2f}", (dates[0], sales[0]), textcoords="offset points",
#                     xytext=(-5, 6), color=color, fontsize=8)
#         ax.annotate(f"{sales[-1]:.2f}", (dates[-1], sales[-1]), textcoords="offset points",
#                     xytext=(5, 6), color=color, fontsize=8)
#
#     ax.xaxis.set_major_locator(mdates.AutoDateLocator())
#     ax.xaxis.set_major_formatter(mdates.AutoDateFormatter(ax.xaxis.get_major_locator()))
#     ax.tick_params(colors="white", axis="both")
#     ax.tick_params(axis="x", rotation=20)
#     for spine in ax.spines.values():
#         spine.set_edgecolor("#444")
#     ax.grid(True, alpha=0.15, color="white")
#     ax.legend(facecolor="#1a1a2e", labelcolor="white", edgecolor="#444", fontsize=11)
#     ax.set_title(f"ПриватБанк — курс продажу за {PERIOD_LABELS[days].lower()} (UAH)",
#                  color="white", fontsize=13, pad=12)
#     ax.set_ylabel("UAH", color="white")
#     plt.tight_layout()
#
#     buf = io.BytesIO()
#     plt.savefig(buf, format="png", dpi=150, facecolor=fig.get_facecolor())
#     buf.seek(0)
#     plt.close(fig)
#     return buf
#
#
# def build_rates_text(history: dict, days: int) -> str:
#     period_label = PERIOD_LABELS[days].lower()
#     lines = ["💱 *Курс ПриватБанку (готівка):*\n"]
#     for ccy, label in CURRENCY_LABELS.items():
#         points = history.get(ccy, [])
#         if not points:
#             continue
#         buy_today, sale_today = points[-1][1], points[-1][2]
#         diff = sale_today - points[0][2]
#         arrow = "📈" if diff > 0.005 else ("📉" if diff < -0.005 else "➡️")
#         diff_str = f"+{diff:.2f}" if diff >= 0 else f"{diff:.2f}"
#         lines.append(
#             f"*{ccy}* — {label}\n"
#             f"  купівля: `{buy_today:.2f}` / продаж: `{sale_today:.2f}`\n"
#             f"  {arrow} за {period_label}: `{diff_str} грн`"
#         )
#     if len(lines) == 1:
#         return "❌ Не вдалося отримати дані."
#     return "\n\n".join(lines)
#
#
# async def _send_rates(days: int) -> tuple[io.BytesIO, str]:
#     history = await fetch_rates_history(days)
#     return build_rates_chart(history, days), build_rates_text(history, days)
#
#
# async def cmd_rates(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     status = await update.message.reply_text("⏳ Збираю дані...")
#     chart, text = await _send_rates(7)
#     await status.delete()
#     await update.message.reply_photo(
#         photo=chart, caption=text, parse_mode="Markdown", reply_markup=rates_keyboard(7)
#     )
#
#
# async def handle_rates_period(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     query = update.callback_query
#     await query.answer()
#     days = int(query.data.split(":")[1])
#     await query.edit_message_caption(caption="⏳ Оновлюю...", parse_mode="Markdown")
#     chart, text = await _send_rates(days)
#     await query.edit_message_media(
#         media=InputMediaPhoto(media=chart, caption=text, parse_mode="Markdown"),
#         reply_markup=rates_keyboard(days),
#     )
#
#
# async def send_rates_job(context: ContextTypes.DEFAULT_TYPE):
#     if not RATES_CHAT_ID:
#         return
#     chart, text = await _send_rates(7)
#     await context.bot.send_photo(
#         chat_id=RATES_CHAT_ID, photo=chart, caption=text,
#         parse_mode="Markdown", reply_markup=rates_keyboard(7)
#     )


# ── Binance P2P (вимкнено) ────────────────────────────────────────────────────

# BINANCE_P2P_URL = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search"
# BINANCE_HEADERS = {
#     "Content-Type": "application/json",
#     "User-Agent": "Mozilla/5.0",
# }
#
#
# async def _fetch_p2p_prices(trade_type: str) -> list[float]:
#     payload = {
#         "fiat": "UAH",
#         "page": 1,
#         "rows": 5,
#         "tradeType": trade_type,
#         "asset": "USDT",
#         "countries": [],
#         "payTypes": [],
#         "publisherType": None,
#         "classifies": ["mass", "profession"],
#     }
#     async with httpx.AsyncClient(timeout=10) as client:
#         resp = await client.post(BINANCE_P2P_URL, json=payload, headers=BINANCE_HEADERS)
#         resp.raise_for_status()
#         data = resp.json()
#     return [float(ad["adv"]["price"]) for ad in data.get("data", [])]
#
#
# async def cmd_crypto(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     status = await update.message.reply_text("⏳ Отримую курс USDT/UAH...")
#     try:
#         buy_prices, sell_prices = await asyncio.gather(
#             _fetch_p2p_prices("BUY"),
#             _fetch_p2p_prices("SELL"),
#         )
#         if not buy_prices or not sell_prices:
#             await status.edit_text("❌ Не вдалося отримати дані з Binance P2P.")
#             return
#         avg_buy = sum(buy_prices) / len(buy_prices)
#         avg_sell = sum(sell_prices) / len(sell_prices)
#         text = (
#             "🟡 *Binance P2P — USDT/UAH*\n\n"
#             f"Купити USDT: `{avg_buy:.2f} UAH`\n"
#             f"Продати USDT: `{avg_sell:.2f} UAH`\n\n"
#             f"_Середнє по топ-5 оголошеннях_"
#         )
#         await status.edit_text(text, parse_mode="Markdown")
#     except Exception as e:
#         log.warning("Binance P2P error: %s", e)
#         await status.edit_text("❌ Помилка при зверненні до Binance P2P.")


# ── Helpers ───────────────────────────────────────────────────────────────────


def is_supported(url: str) -> bool:
    return any(domain in url for domain in SUPPORTED_DOMAINS)


def is_twitter(url: str) -> bool:
    return "twitter.com" in url or "x.com" in url


async def fetch_twitter_media(url: str) -> dict | None:
    match = TWITTER_STATUS_RE.search(url)
    if not match:
        return None
    tweet_id = match.group(1)
    endpoints = (
        f"https://api.fxtwitter.com/status/{tweet_id}",
        f"https://api.vxtwitter.com/status/{tweet_id}",
    )
    for api in endpoints:
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                resp = await client.get(api)
                if resp.status_code != 200:
                    continue
                data = resp.json()
        except Exception as e:
            log.warning("twitter api error %s: %s", api, e)
            continue

        # fxtwitter format
        if "tweet" in data:
            tweet = data.get("tweet") or {}
            media = tweet.get("media") or {}
            photos = [p.get("url") for p in (media.get("photos") or []) if p.get("url")]
            videos = [v.get("url") for v in (media.get("videos") or []) if v.get("url")]
        else:
            # vxtwitter format
            photos = [u for u in (data.get("mediaURLs") or []) if u and "video" not in u]
            videos = [
                m.get("url")
                for m in (data.get("media_extended") or [])
                if m.get("type") in ("video", "gif") and m.get("url")
            ]
        if photos or videos:
            return {"photos": photos, "videos": videos}
    return None


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


def _resolve_filename(ydl: yt_dlp.YoutubeDL, entry: dict) -> str | None:
    filename = ydl.prepare_filename(entry)
    for ext in ("mp4", "mkv", "webm", "avi"):
        candidate = str(Path(filename).with_suffix(f".{ext}"))
        if os.path.exists(candidate):
            return candidate
    return filename if os.path.exists(filename) else None


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
            entry = info["entries"][0] if info.get("entries") else info
            return _resolve_filename(ydl, entry)
    except yt_dlp.utils.DownloadError:
        if quality != "best":
            log.warning("Якість %s недоступна, завантажую найкращий доступний варіант", quality)
            ydl_opts["format"] = "best[ext=mp4]/best"
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                entry = info["entries"][0] if info.get("entries") else info
                return _resolve_filename(ydl, entry)
        raise


def download_all_videos(url: str, out_dir: str) -> list[str]:
    ydl_opts = {
        "outtmpl": os.path.join(out_dir, "%(id)s.%(ext)s"),
        "format": "best[ext=mp4]/best",
        "quiet": True,
        "no_warnings": True,
        "merge_output_format": "mp4",
    }
    if _COOKIES_FILE:
        ydl_opts["cookiefile"] = _COOKIES_FILE

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        entries = info.get("entries") if info.get("_type") == "playlist" else [info]
        results = []
        for entry in (entries or []):
            if entry is None:
                continue
            path = _resolve_filename(ydl, entry)
            if path:
                results.append(path)
        return results


async def _send_file(msg, filepath: str, audio_mode: bool):
    with open(filepath, "rb") as f:
        if audio_mode:
            await msg.reply_audio(f, write_timeout=120, read_timeout=120)
        else:
            await msg.reply_video(f, supports_streaming=True, write_timeout=120, read_timeout=120)


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
            except Exception as e:
                log.warning("Error for %s: %s: %s", url, type(e).__name__, e)
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
            # Twitter з картинками — тягнемо галерею через fxtwitter
            if is_twitter(url):
                twitter_media = await fetch_twitter_media(url)
                if twitter_media and twitter_media["photos"] and not twitter_media["videos"]:
                    status = await msg.reply_text("⏳ Завантажую картинки...")
                    try:
                        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                            photos_bytes = []
                            for photo_url in twitter_media["photos"]:
                                r = await client.get(photo_url)
                                r.raise_for_status()
                                photos_bytes.append(r.content)
                        await status.edit_text("📤 Відправляю...")
                        if len(photos_bytes) == 1:
                            await msg.reply_photo(
                                photos_bytes[0], write_timeout=120, read_timeout=120
                            )
                        else:
                            media = [InputMediaPhoto(d) for d in photos_bytes]
                            await msg.reply_media_group(
                                media=media, write_timeout=120, read_timeout=120
                            )
                        await status.delete()
                    except Exception as e:
                        log.warning("Twitter gallery error for %s: %s", url, e)
                        await status.edit_text("❌ Не вдалося завантажити картинки.")
                    continue

            # Інші платформи (TikTok, Twitter-відео, Instagram) — завантажуємо всі відео
            status = await msg.reply_text("⏳ Завантажую...")
            try:
                with tempfile.TemporaryDirectory() as tmp:
                    filepaths = await asyncio.get_event_loop().run_in_executor(
                        None, download_all_videos, url, tmp
                    )
                    if not filepaths:
                        await status.edit_text("❌ Не вдалося завантажити файл.")
                        continue
                    await status.edit_text("📤 Відправляю...")
                    valid = [p for p in filepaths if os.path.getsize(p) / 1024 / 1024 <= MAX_SIZE_MB]
                    if not valid:
                        await status.edit_text(f"❌ Файли перевищують ліміт {MAX_SIZE_MB} MB.")
                        continue
                    if len(valid) == 1:
                        await _send_file(msg, valid[0], audio_mode=False)
                    else:
                        handles = [open(p, "rb") for p in valid]
                        try:
                            media = [InputMediaVideo(f) for f in handles]
                            await msg.reply_media_group(media=media, write_timeout=120, read_timeout=120)
                        finally:
                            for f in handles:
                                f.close()
                    await status.delete()
            except yt_dlp.utils.DownloadError as e:
                log.warning("DownloadError for %s: %s", url, e)
                await status.edit_text(f"❌ Помилка завантаження:\n{e}")
            except Exception as e:
                log.warning("Error for %s: %s: %s", url, type(e).__name__, e)
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
    app.add_handler(MessageHandler(filters.TEXT | filters.CAPTION, handle_message))
    app.add_handler(CallbackQueryHandler(handle_quality_callback, pattern=r"^dl:"))

    log.info("Bot started (max file size: %d MB)", MAX_SIZE_MB)
    app.run_polling()
