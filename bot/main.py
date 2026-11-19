"""Entry point: `uv run python -m bot.main`."""

import logging
import os
from datetime import time

from dotenv import load_dotenv
from telegram.ext import Application, CommandHandler, MessageHandler, filters

load_dotenv()

from . import db, handlers, session  # noqa: E402  (needs env loaded first)

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)


def main() -> None:
    db.init()
    app = Application.builder().token(os.environ["TELEGRAM_BOT_TOKEN"]).build()

    app.add_handler(CommandHandler("start", handlers.cmd_start))

    uid = handlers.allowed_id()
    if uid is None:
        logging.warning("ALLOWED_USER_ID not set: only /start (shows your ID) will work")
    else:
        me = filters.User(user_id=uid)
        app.add_handler(CommandHandler("today", handlers.cmd_today, filters=me))
        app.add_handler(CommandHandler("skip", handlers.cmd_skip, filters=me))
        app.add_handler(CommandHandler("stats", handlers.cmd_stats, filters=me))
        app.add_handler(CommandHandler("revive", handlers.cmd_revive, filters=me))
        app.add_handler(CommandHandler("chat", handlers.cmd_chat, filters=me))
        app.add_handler(CommandHandler("stop", handlers.cmd_stop, filters=me))
        app.add_handler(MessageHandler(me & filters.VOICE, handlers.on_voice))
        app.add_handler(MessageHandler(me & filters.Regex(r"(?i)^\s*(stop|停止|结束)\s*$"), handlers.cmd_stop))
        app.add_handler(MessageHandler(me & filters.TEXT & ~filters.COMMAND, handlers.on_text))

        h, m = map(int, os.getenv("DAILY_TIME", "08:00").split(":"))
        app.job_queue.run_daily(handlers.job_daily, time(h, m, tzinfo=session.tz()))
        app.job_queue.run_repeating(handlers.job_idle_check, interval=60, first=10)

    app.run_polling()


if __name__ == "__main__":
    main()
