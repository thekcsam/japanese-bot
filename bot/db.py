"""SQLite storage. Single learner (row id=1)."""

import json
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "bot.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS learner (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    level TEXT NOT NULL DEFAULT 'N2',
    paused INTEGER NOT NULL DEFAULT 0,
    paused_reminded INTEGER NOT NULL DEFAULT 0,
    waiting_for_answer INTEGER NOT NULL DEFAULT 0,
    last_bot_msg_at TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    topic_ja TEXT NOT NULL,
    topic_zh TEXT NOT NULL,
    intro_ja TEXT,
    level TEXT NOT NULL,
    key_expressions_json TEXT,  -- old lessons only; replaced by focus_json
    focus_json TEXT,  -- {"words": [...], "patterns": [...]}
    avg_score REAL,
    status TEXT NOT NULL DEFAULT 'active',  -- active | completed | abandoned
    chat_status TEXT  -- NULL (not started) | active | done
);
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    idx INTEGER NOT NULL,
    ja TEXT NOT NULL,
    reading TEXT,
    reference_zh TEXT NOT NULL,
    difficulty INTEGER,
    target_point TEXT,
    answer TEXT,
    skipped INTEGER NOT NULL DEFAULT 0,
    score INTEGER,
    feedback_json TEXT,
    answered_at TEXT
);
CREATE TABLE IF NOT EXISTS chat_turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    idx INTEGER NOT NULL,
    question_ja TEXT NOT NULL,
    question_zh TEXT,
    target TEXT,
    answer TEXT,
    score INTEGER,
    feedback_json TEXT,
    answered_at TEXT
);
"""

# Small steps: "-" = easier end of a JLPT level, "+" = that level plus a little of the next.
LEVELS = ["N4", "N4+", "N3-", "N3", "N3+", "N2-", "N2", "N2+", "N1-", "N1"]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init() -> None:
    with connect() as c:
        c.executescript(SCHEMA)
        cols = {r["name"] for r in c.execute("PRAGMA table_info(sessions)")}
        for col in ("focus_json", "chat_status"):
            if col not in cols:
                c.execute(f"ALTER TABLE sessions ADD COLUMN {col} TEXT")
        c.execute("INSERT OR IGNORE INTO learner (id) VALUES (1)")


# --- learner state -------------------------------------------------------

def learner() -> sqlite3.Row:
    with connect() as c:
        return c.execute("SELECT * FROM learner WHERE id = 1").fetchone()


def update_learner(**fields) -> None:
    cols = ", ".join(f"{k} = ?" for k in fields)
    with connect() as c:
        c.execute(f"UPDATE learner SET {cols} WHERE id = 1", tuple(fields.values()))


def mark_bot_message(waiting: bool) -> None:
    update_learner(last_bot_msg_at=now_iso(), waiting_for_answer=int(waiting))


# --- sessions ------------------------------------------------------------

def create_session(date: str, lesson: dict, level: str) -> int:
    with connect() as c:
        cur = c.execute(
            "INSERT INTO sessions (date, topic_ja, topic_zh, intro_ja, level, focus_json)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (date, lesson["topic_ja"], lesson["topic_zh"], lesson.get("intro_ja"), level,
             json.dumps({"words": lesson["focus_words"], "patterns": lesson["focus_patterns"]},
                        ensure_ascii=False)),
        )
        sid = cur.lastrowid
        for i, it in enumerate(lesson["items"]):
            c.execute(
                "INSERT INTO items (session_id, idx, ja, reading, reference_zh, target_point)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (sid, i, it["ja"], it.get("reading"), it["reference_zh"], it.get("target_point")),
            )
        return sid


def get_session(sid: int) -> sqlite3.Row:
    with connect() as c:
        return c.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()


def active_session() -> sqlite3.Row | None:
    with connect() as c:
        return c.execute(
            "SELECT * FROM sessions WHERE status = 'active' ORDER BY id DESC LIMIT 1"
        ).fetchone()


def session_for_date(date: str) -> sqlite3.Row | None:
    with connect() as c:
        return c.execute(
            "SELECT * FROM sessions WHERE date = ? ORDER BY id DESC LIMIT 1", (date,)
        ).fetchone()


def close_session(sid: int, status: str) -> float | None:
    """Mark a session completed/abandoned and store its average score."""
    with connect() as c:
        avg = c.execute(
            "SELECT AVG(score) FROM items WHERE session_id = ? AND score IS NOT NULL", (sid,)
        ).fetchone()[0]
        c.execute("UPDATE sessions SET status = ?, avg_score = ? WHERE id = ?", (status, avg, sid))
        return avg


def recent_topics(n: int = 30) -> list[str]:
    with connect() as c:
        rows = c.execute(
            "SELECT topic_ja, topic_zh FROM sessions ORDER BY id DESC LIMIT ?", (n,)
        ).fetchall()
    return [f"{r['topic_ja']} / {r['topic_zh']}" for r in rows]


def recent_focus(n: int = 14) -> list[str]:
    """Words and patterns taught in the last `n` lessons, so they aren't picked again."""
    with connect() as c:
        rows = c.execute(
            "SELECT focus_json FROM sessions WHERE focus_json IS NOT NULL ORDER BY id DESC LIMIT ?", (n,)
        ).fetchall()
    out = []
    for r in rows:
        f = json.loads(r["focus_json"])
        out += [w["ja"] for w in f["words"]] + [p["pattern"] for p in f["patterns"]]
    return out


# --- items ---------------------------------------------------------------

def current_item(sid: int) -> sqlite3.Row | None:
    with connect() as c:
        return c.execute(
            "SELECT * FROM items WHERE session_id = ? AND answer IS NULL AND skipped = 0"
            " ORDER BY idx LIMIT 1",
            (sid,),
        ).fetchone()


def item_count(sid: int) -> int:
    with connect() as c:
        return c.execute("SELECT COUNT(*) FROM items WHERE session_id = ?", (sid,)).fetchone()[0]


def save_answer(item_id: int, answer: str, score: int, feedback: dict) -> None:
    with connect() as c:
        c.execute(
            "UPDATE items SET answer = ?, score = ?, feedback_json = ?, answered_at = ? WHERE id = ?",
            (answer, score, json.dumps(feedback, ensure_ascii=False), now_iso(), item_id),
        )


def skip_item(item_id: int) -> None:
    with connect() as c:
        c.execute(
            "UPDATE items SET skipped = 1, score = 0, answered_at = ? WHERE id = ?",
            (now_iso(), item_id),
        )


# --- conversation practice -------------------------------------------------

def set_chat_status(sid: int, status: str) -> None:
    with connect() as c:
        c.execute("UPDATE sessions SET chat_status = ? WHERE id = ?", (status, sid))


def active_chat() -> sqlite3.Row | None:
    with connect() as c:
        return c.execute(
            "SELECT * FROM sessions WHERE chat_status = 'active' ORDER BY id DESC LIMIT 1"
        ).fetchone()


def add_chat_turn(sid: int, question: dict) -> None:
    with connect() as c:
        idx = c.execute("SELECT COUNT(*) FROM chat_turns WHERE session_id = ?", (sid,)).fetchone()[0]
        c.execute(
            "INSERT INTO chat_turns (session_id, idx, question_ja, question_zh, target) VALUES (?, ?, ?, ?, ?)",
            (sid, idx, question["question_ja"], question["question_zh"], question["target"]),
        )


def chat_turns(sid: int) -> list[sqlite3.Row]:
    with connect() as c:
        return c.execute("SELECT * FROM chat_turns WHERE session_id = ? ORDER BY idx", (sid,)).fetchall()


def current_chat_turn(sid: int) -> sqlite3.Row | None:
    with connect() as c:
        return c.execute(
            "SELECT * FROM chat_turns WHERE session_id = ? AND answer IS NULL ORDER BY idx LIMIT 1", (sid,)
        ).fetchone()


def save_chat_answer(turn_id: int, answer: str, score: int, feedback: dict) -> None:
    with connect() as c:
        c.execute(
            "UPDATE chat_turns SET answer = ?, score = ?, feedback_json = ?, answered_at = ? WHERE id = ?",
            (answer, score, json.dumps(feedback, ensure_ascii=False), now_iso(), turn_id),
        )


# --- stats ---------------------------------------------------------------

def weak_points(days: int = 7, top: int = 5) -> list[dict]:
    """Most frequent error types in the last `days`, each with a recent example note."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with connect() as c:
        rows = c.execute(
            "SELECT feedback_json, answered_at FROM items WHERE feedback_json IS NOT NULL AND answered_at >= ?"
            " UNION ALL"
            " SELECT feedback_json, answered_at FROM chat_turns WHERE feedback_json IS NOT NULL AND answered_at >= ?"
            " ORDER BY answered_at DESC",
            (since, since),
        ).fetchall()
    counts: Counter = Counter()
    examples: dict[str, str] = {}
    for r in rows:
        for err in json.loads(r["feedback_json"]).get("errors", []):
            counts[err["type"]] += 1
            examples.setdefault(err["type"], err.get("note", ""))
    return [{"type": t, "count": n, "example": examples[t]} for t, n in counts.most_common(top)]


def stats(tz) -> dict:
    with connect() as c:
        rows = c.execute(
            "SELECT date, avg_score FROM sessions WHERE status = 'completed' ORDER BY date DESC"
        ).fetchall()
    dates = {r["date"] for r in rows}
    streak, day = 0, datetime.now(tz).date()
    if day.isoformat() not in dates:
        day -= timedelta(days=1)  # today's lesson not done yet doesn't break the streak
    while day.isoformat() in dates:
        streak += 1
        day -= timedelta(days=1)
    week_ago = (datetime.now(tz).date() - timedelta(days=7)).isoformat()
    week = [r["avg_score"] for r in rows if r["date"] > week_ago and r["avg_score"] is not None]
    with connect() as c:
        chat_avg = c.execute(
            "SELECT AVG(t.score) FROM chat_turns t JOIN sessions s ON s.id = t.session_id"
            " WHERE t.score IS NOT NULL AND s.date > ?",
            (week_ago,),
        ).fetchone()[0]
    return {
        "streak": streak,
        "week_avg": sum(week) / len(week) if week else None,
        "chat_week_avg": chat_avg,
        "lessons_done": len(rows),
    }
