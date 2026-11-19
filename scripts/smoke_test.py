"""Offline check of the LLM layer (no Telegram): uv run python -m scripts.smoke_test"""

import asyncio
import json

from bot import llm


async def main() -> None:
    lesson = await llm.generate_lesson("N3", [], ["コンビニで / 在便利店"], [])
    print("topic:", lesson["topic_ja"], "/", lesson["topic_zh"])
    print("words:", [w["ja"] for w in lesson["focus_words"]])
    print("patterns:", [p["pattern"] for p in lesson["focus_patterns"]])
    assert len(lesson["items"]) == 10
    assert len(lesson["focus_words"]) == 3 and len(lesson["focus_patterns"]) == 3
    for it in lesson["items"]:
        print(f"  [{it['target_point']}] {it['ja']}  →  {it['reference_zh']}")

    # Conversation practice: first question, then grade a reply that uses a focus item.
    focus = {"words": lesson["focus_words"], "patterns": lesson["focus_patterns"]}
    items = llm.focus_items(focus)
    q = await llm.chat_turn("N3", lesson["topic_ja"], focus, [], items, None)
    print("question:", q["question_ja"], "| target:", q["target"])
    assert q["target"] in items
    reply = f"はい、{lesson['focus_patterns'][0]['example']}"
    turn = await llm.chat_turn("N3", lesson["topic_ja"], focus, [{"q": q["question_ja"], "a": None}],
                               items, reply)
    print(json.dumps(turn, ensure_ascii=False, indent=2))
    assert "next" in turn and turn["next"]["target"] in items

    # Deliberate false-friend mistake: 手紙 means "letter", not "toilet paper".
    item = {"ja": "母から手紙が届いた。", "reference_zh": "收到了妈妈寄来的信。",
            "target_point": "手紙（信）", "reading": "ははからてがみがとどいた。"}
    fb = await llm.grade_translation("N2", item, "妈妈给我送来了卫生纸。")
    print(json.dumps(fb, ensure_ascii=False, indent=2))
    assert any(e["type"] == "false_friend" for e in fb["errors"]), "expected a false_friend error"
    print("OK")


asyncio.run(main())
