"""Conversation practice after the translation: the bot asks about the day's topic and
the learner answers in Japanese, using the 3 focus words and 3 patterns, until "stop".

`handle_reply()` takes plain text so voice (transcribed) can feed into it later.
"""

import json
import logging
from html import escape

from telegram import Bot
from telegram.constants import ChatAction

from . import db, llm, session

log = logging.getLogger(__name__)

CHAT_ERROR_LABELS = {
    "particle": "助词",
    "conjugation": "活用变形",
    "grammar": "语法",
    "word_choice": "用词",
    "unnatural": "不自然",
}


def _focus(s) -> dict:
    return json.loads(s["focus_json"])


def _used(turns) -> set[str]:
    used = set()
    for t in turns:
        if t["feedback_json"]:
            used.update(json.loads(t["feedback_json"]).get("used_focus", []))
    return used


def _history(turns) -> list[dict]:
    return [{"q": t["question_ja"], "a": t["answer"]} for t in turns if t["answer"] is not None]


async def send_question(bot: Bot, chat_id: int, q) -> None:
    await session.send(
        bot, chat_id,
        f"💬 {escape(q['question_ja'])}\n"
        f"💡 试着用：<b>{escape(q['target'])}</b>\n"
        f"<tg-spoiler>{escape(q['question_zh'])}</tg-spoiler>",
        waiting=True,
    )


async def start(bot: Bot, chat_id: int, sid: int) -> None:
    s = db.get_session(sid)
    if not s["focus_json"]:
        return  # lessons from before focus words existed
    db.set_chat_status(sid, "active")
    await session.send(
        bot, chat_id,
        "🗣 <b>会话练习</b>\n用日语回答问题，尽量用上今天的单词和句型。\n随时发送 /stop 结束。",
    )
    await bot.send_chat_action(chat_id, ChatAction.TYPING)
    focus = _focus(s)
    try:
        q = await llm.chat_turn(s["level"], s["topic_ja"], focus, [], llm.focus_items(focus), None)
    except llm.LLMError:
        log.exception("chat question failed")
        db.set_chat_status(sid, "done")
        await session.send(bot, chat_id, "⚠️ 生成问题失败，今天的会话练习先跳过。")
        return
    db.add_chat_turn(sid, q)
    await send_question(bot, chat_id, q)


async def resume(bot: Bot, chat_id: int, s) -> None:
    turn = db.current_chat_turn(s["id"])
    await session.send(bot, chat_id, f"▶️ 继续会话练习：<b>{escape(s['topic_ja'])}</b>")
    await send_question(bot, chat_id, turn)


def format_feedback(fb: dict) -> str:
    score = fb["score"]
    icon = "✅" if score >= 85 else "🟡" if score >= 60 else "❌"
    lines = [
        f"{icon} <b>{score}/100</b> — {escape(fb['comment'])}",
        f"✍️ 更自然：{escape(fb['natural_ja'])}",
    ]
    for e in fb.get("errors", []):
        lines.append(f"❗ [{CHAT_ERROR_LABELS.get(e['type'], e['type'])}] {escape(e['note'])}")
    if fb.get("used_focus"):
        lines.append("🎯 用上了：" + "、".join(escape(u) for u in fb["used_focus"]))
    return "\n".join(lines)


async def handle_reply(bot: Bot, chat_id: int, text: str) -> None:
    s = db.active_chat()
    turn = db.current_chat_turn(s["id"])
    turns = db.chat_turns(s["id"])
    focus = _focus(s)
    unused = [i for i in llm.focus_items(focus) if i not in _used(turns)]

    db.update_learner(waiting_for_answer=0)  # stop the idle timer while grading
    await bot.send_chat_action(chat_id, ChatAction.TYPING)
    history = _history(turns) + [{"q": turn["question_ja"], "a": None}]
    try:
        fb = await llm.chat_turn(s["level"], s["topic_ja"], focus, history, unused, text)
    except llm.LLMError:
        log.exception("chat grading failed")
        await session.send(bot, chat_id, "⚠️ 批改失败，请再发一次你的回答。", waiting=True)
        return

    nxt = fb.pop("next")
    db.save_chat_answer(turn["id"], text, fb["score"], fb)
    await session.send(bot, chat_id, format_feedback(fb))
    db.add_chat_turn(s["id"], nxt)
    await send_question(bot, chat_id, nxt)


async def stop(bot: Bot, chat_id: int) -> None:
    s = db.active_chat()
    if s is None:
        await session.send(bot, chat_id, "现在没有进行中的会话练习。")
        return
    db.set_chat_status(s["id"], "done")
    answered = [t for t in db.chat_turns(s["id"]) if t["score"] is not None]
    used = _used(answered)
    items = llm.focus_items(_focus(s))

    lines = ["🏁 <b>会话练习结束</b>"]
    if answered:
        avg = sum(t["score"] for t in answered) / len(answered)
        lines.append(f"回答 {len(answered)} 次 · 平均分 <b>{avg:.0f}</b>")
    lines.append("")
    lines += [f"{'✅' if i in used else '⬜'} {escape(i)}" for i in items]
    missing = [i for i in items if i not in used]
    if missing:
        lines.append(f"\n还没用上：{escape('、'.join(missing))} — 明天可以试试！")
    else:
        lines.append("\n🎉 今天的单词和句型全部用上了！")
    lines.append("明天见 👋")
    await session.send(bot, chat_id, "\n".join(lines))
