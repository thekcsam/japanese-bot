"""Calls Claude through the local Claude Code CLI (`claude -p`).

This uses the Claude subscription the CLI is logged in with, so no API key or
per-token billing is needed.
"""

import asyncio
import json
import logging
import os

from . import prompts
from .db import DB_PATH

log = logging.getLogger(__name__)

TIMEOUT_S = 240


class LLMError(RuntimeError):
    pass


async def _ask(prompt: str, schema: dict, model: str) -> dict:
    cmd = [
        "claude", "-p",
        "--output-format", "json",
        "--model", model,
        "--tools", "",
        "--system-prompt", prompts.SYSTEM,
        "--json-schema", json.dumps(schema),
    ]
    # Run outside the project tree so no CLAUDE.md or project settings get loaded.
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=DB_PATH.parent,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(prompt.encode()), TIMEOUT_S)
    except asyncio.TimeoutError:
        proc.kill()
        raise LLMError("claude -p timed out")
    if proc.returncode != 0:
        raise LLMError(f"claude -p exited {proc.returncode}: {err.decode()[-500:]}")
    data = json.loads(out)
    if data.get("is_error") or "structured_output" not in data:
        raise LLMError(f"claude -p returned no structured output: {str(data.get('result'))[:500]}")
    return data["structured_output"]


async def ask(prompt: str, schema: dict, model: str, retries: int = 1) -> dict:
    for attempt in range(retries + 1):
        try:
            return await _ask(prompt, schema, model)
        except (LLMError, json.JSONDecodeError) as e:
            log.warning("LLM call failed (attempt %d): %s", attempt + 1, e)
            if attempt == retries:
                raise LLMError(str(e)) from e
    raise AssertionError("unreachable")


async def generate_lesson(level: str, weak_points: list[dict], recent_topics: list[str],
                          recent_focus: list[str]) -> dict:
    return await ask(
        prompts.lesson_prompt(level, weak_points, recent_topics, recent_focus),
        prompts.LESSON_SCHEMA,
        os.getenv("CLAUDE_MODEL_LESSON", "sonnet"),
    )


async def grade_translation(level: str, item, answer: str) -> dict:
    return await ask(
        prompts.grade_prompt(level, item["ja"], item["reference_zh"], item["target_point"], answer),
        prompts.GRADE_SCHEMA,
        os.getenv("CLAUDE_MODEL_GRADE", "sonnet"),
    )


def focus_items(focus: dict) -> list[str]:
    return [w["ja"] for w in focus["words"]] + [p["pattern"] for p in focus["patterns"]]


async def chat_turn(level: str, topic: str, focus: dict, history: list[dict], unused: list[str],
                    answer: str | None) -> dict:
    """First question (answer=None), or grade `answer` and return feedback plus `next` question."""
    items = focus_items(focus)
    schema = prompts.chat_question_schema(items) if answer is None else prompts.chat_turn_schema(items)
    return await ask(
        prompts.chat_prompt(level, topic, focus, history, unused, answer),
        schema,
        os.getenv("CLAUDE_MODEL_GRADE", "sonnet"),
    )
