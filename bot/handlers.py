"""Telegram command/message handlers and scheduled jobs."""

import logging
import os
from datetime import datetime, timedelta, timezone

from telegram import Update
from telegram.ext import ContextTypes

from . import db, session
from .session import ERROR_LABELS

log = logging.getLogger(__name__)

HELP = (
    "🇯🇵 日语翻译练习\n\n"
    "每天我会选一个主题，发 10 句日语给你翻译成中文。\n\n"
    "/today — 开始或继续今天的课程\n"
    "/skip — 跳过当前句子并查看答案\n"
    "/stats — 学习统计\n"
    "/revive — 你离开后我会自动暂停，发送 /revive 恢复"
)


def allowed_id() -> int | None:
    v = os.getenv("ALLOWED_USER_ID", "").strip()
    return int(v) if v else None


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    if allowed_id() is None:
        await update.message.reply_text(
            f"Your Telegram user ID is {uid}.\nPut it in .env as ALLOWED_USER_ID and restart the bot."
        )
    elif uid == allowed_id():
        await update.message.reply_text(HELP)
    # anyone else: silently ignored


async def _paused_gate(update: Update) -> bool:
    """Return True if the bot is paused (and remind the user once)."""
    learner = db.learner()
    if not learner["paused"]:
        return False
    if not learner["paused_reminded"]:
        await update.message.reply_text("⏸ 已暂停 — 发送 revive 继续。")
        db.update_learner(paused_reminded=1)
    return True


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _paused_gate(update):
        return
    async with session.lock:
        await session.resume_or_start(context.bot, update.effective_chat.id)


async def cmd_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _paused_gate(update):
        return
    async with session.lock:
        await session.skip(context.bot, update.effective_chat.id)


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    st = db.stats(session.tz())
    learner = db.learner()
    lines = [
        "📊 学习统计",
        f"等级：{learner['level']}",
        f"连续天数：{st['streak']}",
        f"完成课程：{st['lessons_done']}",
        f"7 日平均分：{st['week_avg']:.0f}" if st["week_avg"] is not None else "7 日平均分：—",
    ]
    weak = db.weak_points()
    if weak:
        lines.append("弱点：" + "、".join(f"{ERROR_LABELS.get(w['type'], w['type'])}×{w['count']}" for w in weak))
    if learner["paused"]:
        lines.append("状态：⏸ 已暂停（发送 revive 恢复）")
    await update.message.reply_text("\n".join(lines))


async def cmd_revive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db.update_learner(paused=0, paused_reminded=0)
    await update.message.reply_text("👋 欢迎回来！")
    async with session.lock:
        await session.resume_or_start(context.bot, update.effective_chat.id)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _paused_gate(update):
        return
    async with session.lock:
        await session.handle_answer(context.bot, update.effective_chat.id, update.message.text.strip())


# --- jobs ----------------------------------------------------------------

async def job_daily(context: ContextTypes.DEFAULT_TYPE) -> None:
    if db.learner()["paused"]:
        log.info("daily lesson skipped: paused")
        return
    if db.session_for_date(session.today()):
        log.info("daily lesson skipped: already have one today")
        return
    async with session.lock:
        await session.start_lesson(context.bot, allowed_id())


async def job_idle_check(context: ContextTypes.DEFAULT_TYPE) -> None:
    learner = db.learner()
    if learner["paused"] or not learner["waiting_for_answer"] or not learner["last_bot_msg_at"]:
        return
    timeout = timedelta(minutes=int(os.getenv("IDLE_TIMEOUT_MIN", "120")))
    if datetime.now(timezone.utc) - datetime.fromisoformat(learner["last_bot_msg_at"]) > timeout:
        db.update_learner(paused=1, paused_reminded=0, waiting_for_answer=0)
        log.info("no answer for %s, pausing", timeout)
