"""Telegram command/message handlers and scheduled jobs."""

import logging
import os
from datetime import datetime, timedelta, timezone
from html import escape

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from . import chat, db, session, voice
from .chat import CHAT_ERROR_LABELS
from .session import ERROR_LABELS

log = logging.getLogger(__name__)

HELP = (
    "🇯🇵 日语翻译练习\n\n"
    "每天我会选一个日常场景，发 10 句日语给你翻译成中文，然后用日语聊这个话题。\n\n"
    "/today — 开始或继续今天的课程\n"
    "/skip — 跳过当前句子并查看答案\n"
    "/chat — 开始或继续今天的会话练习（可以用文字或语音回答）\n"
    "/stop — 结束会话练习\n"
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
        f"7 日会话平均分：{st['chat_week_avg']:.0f}" if st["chat_week_avg"] is not None else "7 日会话平均分：—",
    ]
    labels = ERROR_LABELS | CHAT_ERROR_LABELS
    weak = db.weak_points()
    if weak:
        lines.append("弱点：" + "、".join(f"{labels.get(w['type'], w['type'])}×{w['count']}" for w in weak))
    if learner["paused"]:
        lines.append("状态：⏸ 已暂停（发送 revive 恢复）")
    await update.message.reply_text("\n".join(lines))


async def cmd_revive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db.update_learner(paused=0, paused_reminded=0)
    await update.message.reply_text("👋 欢迎回来！")
    async with session.lock:
        await session.resume_or_start(context.bot, update.effective_chat.id)


async def cmd_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _paused_gate(update):
        return
    async with session.lock:
        await chat.start_or_resume(context.bot, update.effective_chat.id)


async def on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _paused_gate(update):
        return
    chat_id = update.effective_chat.id
    async with session.lock:
        if not db.active_chat():
            await session.send(context.bot, chat_id, "语音只能用在会话练习里。翻译请用文字回答；发送 /chat 开始会话练习。")
            return
        await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
        try:
            text = await voice.transcribe(update.message.voice)
        except Exception:
            log.exception("transcription failed")
            await session.send(context.bot, chat_id, "⚠️ 语音识别失败，请再录一次或用文字回答。", waiting=True)
            return
        if not text:
            await session.send(context.bot, chat_id, "🎤 没听清，请再录一次。", waiting=True)
            return
        await session.send(context.bot, chat_id, f"🎤 我听到：{escape(text)}")
        await chat.handle_reply(context.bot, chat_id, text, voice=True)


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _paused_gate(update):
        return
    async with session.lock:
        await chat.stop(context.bot, update.effective_chat.id)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _paused_gate(update):
        return
    text = update.message.text.strip()
    async with session.lock:
        if db.active_chat():
            await chat.handle_reply(context.bot, update.effective_chat.id, text)
        else:
            await session.handle_answer(context.bot, update.effective_chat.id, text)


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
