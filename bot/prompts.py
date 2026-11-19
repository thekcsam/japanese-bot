"""Prompt templates and JSON schemas for `claude -p`."""

import json

LEARNER_PROFILE = """\
Learner profile:
- Native Mandarin Chinese speaker (uses Simplified Chinese).
- Japanese level around JLPT {level} ("-" = the easier end of that level, "+" = that level
  plus a few patterns from the next level up). Reading and kanji are strong because of Chinese;
  the weak areas are grammar nuance, natural spoken expressions, and words whose kanji
  look the same in Chinese but mean something different (false friends, e.g. 手紙, 勉強, 大丈夫, 新聞).
- Goal: understand everyday spoken Japanese. Prefer common, high-frequency words and
  sentence patterns that come up in daily conversation over formal, written or rare ones.
- Do NOT make sentences harder by using rare kanji. Make them harder through grammar,
  nuance, colloquial speech and implied meaning.
"""

SYSTEM = (
    "You are a patient, precise Japanese tutor for a Chinese-speaking learner. "
    "Explanations for the learner are written in Simplified Chinese. "
    "Japanese must be natural, modern and correct."
)

ERROR_TYPES = ["grammar_misread", "vocab_misread", "false_friend", "nuance", "omission", "idiom"]

_WORD = {
    "type": "object",
    "properties": {
        "ja": {"type": "string"},
        "reading": {"type": "string"},
        "zh": {"type": "string"},
    },
    "required": ["ja", "reading", "zh"],
}

_PATTERN = {
    "type": "object",
    "properties": {
        "pattern": {"type": "string", "description": "e.g. 〜てもいいですか"},
        "zh": {"type": "string", "description": "meaning and when to use it, in Chinese"},
        "example": {"type": "string", "description": "one short everyday example sentence in Japanese"},
    },
    "required": ["pattern", "zh", "example"],
}

LESSON_SCHEMA = {
    "type": "object",
    "properties": {
        "topic_ja": {"type": "string"},
        "topic_zh": {"type": "string"},
        "intro_ja": {"type": "string", "description": "1-2 sentence intro to the situation, in Japanese"},
        "focus_words": {"type": "array", "minItems": 3, "maxItems": 3, "items": _WORD},
        "focus_patterns": {"type": "array", "minItems": 3, "maxItems": 3, "items": _PATTERN},
        "items": {
            "type": "array",
            "minItems": 10,
            "maxItems": 10,
            "items": {
                "type": "object",
                "properties": {
                    "ja": {"type": "string"},
                    "reading": {"type": "string", "description": "full hiragana reading"},
                    "reference_zh": {"type": "string"},
                    "target_point": {"type": "string", "description": "which focus word/pattern it uses, in Chinese"},
                },
                "required": ["ja", "reading", "reference_zh", "target_point"],
            },
        },
    },
    "required": ["topic_ja", "topic_zh", "intro_ja", "focus_words", "focus_patterns", "items"],
}

GRADE_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer", "minimum": 0, "maximum": 100},
        "verdict": {"type": "string", "description": "one short line in Chinese: was the meaning understood?"},
        "missed": {"type": "array", "items": {"type": "string"}, "description": "nuances missed, in Chinese"},
        "errors": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ERROR_TYPES},
                    "note": {"type": "string", "description": "in Chinese, quote the Japanese involved"},
                },
                "required": ["type", "note"],
            },
        },
        "explanation": {"type": "string", "description": "brief note on the key grammar/vocab, in Chinese"},
    },
    "required": ["score", "verdict", "missed", "errors", "explanation"],
}


def lesson_prompt(level: str, weak_points: list[dict], recent_topics: list[str],
                  recent_focus: list[str]) -> str:
    return f"""{LEARNER_PROFILE.format(level=level)}
Create today's translation lesson.

1. Choose ONE everyday situation the learner could really be in (e.g. ordering at a
   restaurant, chatting with a coworker about the weekend, making plans with a friend,
   at the doctor, returning something to a shop, talking with a neighbour). It must be
   different from these recent topics:
{json.dumps(recent_topics, ensure_ascii=False)}

2. Pick exactly 3 words and 3 sentence patterns you most want the learner to learn for
   this situation: common, useful in daily conversation, and at level {level}. Don't
   repeat these recently taught ones:
{json.dumps(recent_focus, ensure_ascii=False)}

3. Write 10 Japanese sentences the learner will translate into Chinese.
   - Natural spoken lines someone would actually say in this situation, together
     forming a short conversation or scene.
   - ALL 10 at the same difficulty ({level}); do not get harder towards the end.
   - Every sentence uses at least one focus word or pattern, and each focus word and
     pattern appears in at least 2 sentences, so the learner sees them repeatedly.
   - Give some attention to these recent weak points (type, count, example):
{json.dumps(weak_points, ensure_ascii=False)}
"""


def grade_prompt(level: str, ja: str, reference_zh: str, target_point: str, answer: str) -> str:
    return f"""{LEARNER_PROFILE.format(level=level)}
Grade the learner's Chinese translation of a Japanese sentence. The goal is to check
Japanese comprehension, not Chinese style: any wording that captures the full meaning
and nuance deserves a high score.

Japanese: {ja}
Point being tested: {target_point}
Reference translation: {reference_zh}
Learner's translation: {answer}

Scoring: 90-100 meaning and nuance fully correct; 70-89 meaning correct, some nuance lost;
40-69 partly misunderstood; 0-39 mostly wrong or empty.
List each misunderstanding in `errors` with the most fitting type. Empty list if none.
"""
