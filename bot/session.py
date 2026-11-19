"""Daily lesson flow: start → send items one by one → grade → summary.

Every outgoing message goes through `send()`, which records `last_bot_msg_at`
so the idle checker can pause the bot when the learner is away.
"""

import asyncio
import json
import logging
import os
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from telegram import Bot
from telegram.constants import ChatAction, ParseMode

from . import db, llm

log = logging.getLogger(__name__)

# One answer / lesson operation at a time (grading takes a few seconds).
lock = asyncio.Lock()

ERROR_LABELS = {
    "grammar_misread": "语法理解",
    "vocab_misread": "词义理解",
    "false_friend": "中日同形异义",
    "nuance": "语气/细微差别",
    "omission": "漏译",
    "idiom": "惯用语",
}


def tz() -> ZoneInfo:
    return ZoneInfo(os.getenv("TZ", "Asia/Kuala_Lumpur"))


def today() -> str:
    return datetime.now(tz()).date().isoformat()


async def send(bot: Bot, chat_id: int, text: str, waiting: bool = False) -> None:
    await bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)
    db.mark_bot_message(waiting)


async def start_lesson(bot: Bot, chat_id: int) -> None:
    """Abandon any unfinished lesson and generate a fresh one."""
    old = db.active_session()
    if old:
        db.close_session(old["id"], "abandoned")

    level = db.learner()["level"]
    await bot.send_chat_action(chat_id, ChatAction.TYPING)
    await send(bot, chat_id, "📚 正在准备今天的主题，请稍等…")
    try:
        lesson = await llm.generate_lesson(level, db.weak_points(), db.recent_topics(), db.recent_focus())
    except llm.LLMError:
        log.exception("lesson generation failed")
        await send(bot, chat_id, "⚠️ 生成课程失败，请稍后发送 /today 重试。")
        return

    sid = db.create_session(today(), lesson, level)
    await send(
        bot, chat_id,
        f"🗾 <b>今日场景：{escape(lesson['topic_ja'])}</b>（{escape(lesson['topic_zh'])}）· 等级 {level}\n\n"
        f"{escape(lesson['intro_ja'])}\n\n"
        f"请把下面 10 句日语翻译成中文。今天重点练习 3 个单词和 3 个句型，课程结束时会总结。\n"
        f"不会的可以发 /skip 查看答案。",
    )
    await send_current(bot, chat_id, sid)


async def resume_or_start(bot: Bot, chat_id: int) -> None:
    """Resume today's unfinished lesson, otherwise start a new one."""
    s = db.active_session()
    if s and s["date"] == today():
        await send(bot, chat_id, f"▶️ 继续今天的主题：<b>{escape(s['topic_ja'])}</b>")
        await send_current(bot, chat_id, s["id"])
    else:
        await start_lesson(bot, chat_id)


async def send_current(bot: Bot, chat_id: int, sid: int) -> None:
    item = db.current_item(sid)
    if item is None:
        await finish(bot, chat_id, sid)
        return
    total = db.item_count(sid)
    await send(
        bot, chat_id,
        f"<b>{item['idx'] + 1}/{total}</b>\n\n{escape(item['ja'])}",
        waiting=True,
    )


def format_feedback(item, fb: dict) -> str:
    score = fb["score"]
    icon = "✅" if score >= 85 else "🟡" if score >= 60 else "❌"
    lines = [
        f"{icon} <b>{score}/100</b> — {escape(fb['verdict'])}",
        f"📖 读音：{escape(item['reading'] or '')}",
        f"🈯 参考：{escape(item['reference_zh'])}",
    ]
    for m in fb.get("missed", []):
        lines.append(f"• 漏掉：{escape(m)}")
    for e in fb.get("errors", []):
        lines.append(f"❗ [{ERROR_LABELS.get(e['type'], e['type'])}] {escape(e['note'])}")
    if fb.get("explanation"):
        lines.append(f"💡 {escape(fb['explanation'])}")
    return "\n".join(lines)


async def handle_answer(bot: Bot, chat_id: int, text: str) -> None:
    s = db.active_session()
    item = db.current_item(s["id"]) if s else None
    if item is None:
        await send(bot, chat_id, "现在没有进行中的练习。发送 /today 开始今天的课程。")
        return

    db.update_learner(waiting_for_answer=0)  # stop the idle timer while grading
    await bot.send_chat_action(chat_id, ChatAction.TYPING)
    try:
        fb = await llm.grade_translation(s["level"], item, text)
    except llm.LLMError:
        log.exception("grading failed")
        await send(bot, chat_id, "⚠️ 批改失败，请再发一次你的翻译。", waiting=True)
        return

    db.save_answer(item["id"], text, fb["score"], fb)
    await send(bot, chat_id, format_feedback(item, fb))
    await send_current(bot, chat_id, s["id"])


async def skip(bot: Bot, chat_id: int) -> None:
    s = db.active_session()
    item = db.current_item(s["id"]) if s else None
    if item is None:
        await send(bot, chat_id, "现在没有进行中的练习。发送 /today 开始。")
        return
    db.skip_item(item["id"])
    await send(
        bot, chat_id,
        f"⏭ 已跳过（计 0 分）\n📖 读音：{escape(item['reading'] or '')}\n"
        f"🈯 参考：{escape(item['reference_zh'])}\n💡 考点：{escape(item['target_point'] or '')}",
    )
    await send_current(bot, chat_id, s["id"])


def adjust_level(level: str, avg: float | None) -> str:
    if avg is None:
        return level
    i = db.LEVELS.index(level)
    if avg >= 85:
        i = min(i + 1, len(db.LEVELS) - 1)
    elif avg < 60:
        i = max(i - 1, 0)
    return db.LEVELS[i]


async def finish(bot: Bot, chat_id: int, sid: int) -> None:
    avg = db.close_session(sid, "completed")
    s = db.get_session(sid)
    old_level = db.learner()["level"]
    new_level = adjust_level(old_level, avg)
    db.update_learner(level=new_level)

    lines = [
        f"🎉 <b>完成！</b>今日主题：{escape(s['topic_ja'])}",
        f"平均分：<b>{avg:.0f}</b>" if avg is not None else "平均分：—",
    ]
    if new_level != old_level:
        lines.append(f"等级调整：{old_level} → <b>{new_level}</b>（明天生效）")
    weak = db.weak_points()
    if weak:
        lines.append("\n📌 最近的弱点：")
        lines += [f"• {ERROR_LABELS.get(w['type'], w['type'])} ×{w['count']}" for w in weak]
    if s["focus_json"]:
        focus = json.loads(s["focus_json"])
        lines.append("\n📝 今日单词：")
        lines += [f"• {escape(w['ja'])}（{escape(w['reading'])}）— {escape(w['zh'])}" for w in focus["words"]]
        lines.append("\n🧩 今日句型：")
        lines += [f"• <b>{escape(p['pattern'])}</b> — {escape(p['zh'])}\n  例：{escape(p['example'])}"
                  for p in focus["patterns"]]
    lines.append("\n🗣 下一步：语音讨论模式即将推出。")
    await send(bot, chat_id, "\n".join(lines))
