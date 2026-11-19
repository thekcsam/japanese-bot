# Japanese practice bot (Telegram)

A personal Telegram bot for daily Japanese practice. Every day it picks an everyday
situation, 3 words and 3 sentence patterns to learn, and sends 10 spoken-style Japanese
sentences that use them, all at the same level. You translate each one into
Chinese (Mandarin); Claude grades your comprehension, explains what you missed, and adapts
the next lesson to your weak points.

After the translation, the bot asks you questions about the situation so you use the 3
words and 3 patterns in your own Japanese replies, until you send `/stop`.

**Status:** Phase 1 (translation) and Phase 2a (conversation, text or voice replies) built.
Phase 2b (pronunciation feedback) planned.

## Features (Phase 1)

| Feature | How it works |
|---|---|
| Daily situation lesson | Sent at `DAILY_TIME`, or on demand with `/today`. One everyday situation (restaurant, doctor, chatting with a coworker…), common conversational Japanese. |
| Focus: 3 words + 3 patterns | Each lesson picks 3 words and 3 sentence patterns; every sentence uses at least one, each appears at least twice. Words/patterns from the last 14 lessons aren't repeated. |
| Same level all lesson | All 10 sentences are at the learner's current level; no easy → hard ramp. |
| One sentence at a time | Next sentence is sent only after you answer or `/skip`. |
| Grading | Score 0–100, verdict, missed nuances, typed errors, short explanation, reading and reference translation. Feedback is in Simplified Chinese. |
| Error types | `grammar_misread`, `vocab_misread`, `false_friend` (same kanji, different meaning), `nuance`, `omission`, `idiom`. |
| Adaptive level | Small steps: N4, N4+, N3-, N3, N3+, N2-, N2, N2+, N1-, N1. Lesson average ≥85 → one step up, <60 → one step down, otherwise unchanged. Takes effect from the next lesson. |
| Weak-point targeting | Top 5 error types from the last 7 days are fed into the next lesson's prompt. |
| No repeated topics | Last 30 topics are passed to the generator to avoid. |
| Away → pause | If you don't answer within `IDLE_TIMEOUT_MIN`, the bot silently pauses: no messages, no daily lessons. |
| `revive` | Send `revive` (or `/revive`) to unpause. Resumes today's unfinished lesson at the same sentence, otherwise starts today's lesson. While paused, other messages get one reminder only. |
| Summary | At the end: average, level change, weak points, the 3 words and 3 patterns with examples. |
| Conversation practice | Starts right after the translation summary. Each question is in Japanese, with a 💡 hint (the word/pattern to use) and the Chinese translation behind a spoiler. Questions target unused focus items first, then review the hardest ones. Open-ended until `/stop` (or `stop` / `停止` / `结束`). |
| Conversation feedback | Per reply: score, natural version of what you meant, typed mistakes (`particle`, `conjugation`, `grammar`, `word_choice`, `unnatural`), focus items used. At `/stop`: replies, average, ✅/⬜ for each focus item. |
| Voice replies | In conversation practice you can reply with a Telegram voice message. It's transcribed locally with Whisper (`mlx-whisper`, free, offline), echoed back as 🎤 so you can see what was heard, then graded like text (punctuation and recognition slips aren't counted). Questions stay text. |
| `/chat` | Start conversation practice for today's finished lesson (e.g. one finished before this feature, or again after `/stop`), or resume it. |
| Conversation vs level | Conversation scores don't change your level; its mistakes count toward weak points. `/stats` shows the 7-day conversation average. |
| Private | Only `ALLOWED_USER_ID` can use it; everyone else is ignored. |

### Commands

- `/today` — start or resume today's lesson
- `/skip` — skip the current sentence (scores 0) and show the answer
- `/chat` — start or resume today's conversation practice
- `/stop` — end conversation practice
- `/stats` — level, streak, 7-day average, weak points
- `revive` / `/revive` — unpause after being away
- `/start` — help (before setup: shows your Telegram user ID)

## Setup

1. **Create the bot:** in Telegram, message [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token.
2. **Configure:** `cp .env.example .env` and set `TELEGRAM_BOT_TOKEN`.
3. **Install:** `uv sync` (needs `uv`: `brew install uv`).
4. **Find your user ID:** run the bot (step 6), send `/start` to it, it replies with your ID.
   Put it in `.env` as `ALLOWED_USER_ID` and restart.
5. **Voice replies:** needs `ffmpeg` (`brew install ffmpeg`) and an Apple Silicon Mac (mlx-whisper).
6. **Claude CLI:** the `claude` command must be installed and logged in on this Mac
   (it is used for lesson generation and grading).
7. **Run** (inside `tmux` so it survives closing the terminal; `caffeinate -is` keeps the Mac
   from sleeping while the bot runs, `-s` only works on power):
   ```sh
   tmux new -s jpbot
   caffeinate -is uv run python -m bot.main
   ```
   With the lid closed the Mac sleeps anyway, unless it has power and an external display
   (or `sudo pmset -a disablesleep 1`, undo with `0`).
   Detach with `Ctrl-b d`; reattach with `tmux attach -t jpbot`.

### Configuration (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | — | From @BotFather |
| `ALLOWED_USER_ID` | — | Your numeric Telegram ID |
| `DAILY_TIME` | `08:00` | Daily lesson time (24h) |
| `TZ` | `Asia/Kuala_Lumpur` | Timezone for `DAILY_TIME` and "today" |
| `IDLE_TIMEOUT_MIN` | `120` | Minutes without an answer before pausing |
| `CLAUDE_MODEL_LESSON` | `sonnet` | Model for lesson generation |
| `CLAUDE_MODEL_GRADE` | `sonnet` | Model for grading (`haiku` is faster) |
| `WHISPER_MODEL` | `mlx-community/whisper-large-v3-turbo` | Speech-to-text model for voice replies (~1.6 GB, downloaded on first use) |

### Testing

- `uv run python -m scripts.smoke_test` — calls Claude for real: generates a lesson
  (checks 10 items, 3 words, 3 patterns) and grades a deliberate 手紙 false-friend mistake.

## Architecture

```
Telegram ⇄ python-telegram-bot (long polling, on this Mac)
              │
              ├─ handlers.py   commands, text answers, jobs (daily lesson, idle check every 60s)
              ├─ session.py    lesson flow: start → item → grade → next → summary
              ├─ chat.py       conversation: question → reply → feedback + next question … /stop
              ├─ voice.py      voice reply → mlx-whisper (local) → text → chat.py
              ├─ llm.py ──────► `claude -p --json-schema …` (Claude Code CLI, subscription)
              ├─ prompts.py    learner profile, prompts, JSON schemas
              └─ db.py ───────► data/bot.db (SQLite: learner, sessions, items, chat_turns)
```

- **Lesson generation:** ~20–30 s. **Grading:** a few seconds per answer.
- Claude is called with `--tools ""` and a custom system prompt, from `data/`,
  so it only does text generation and doesn't load any project context.
- `--json-schema` gives validated structured output (`structured_output` field), so no JSON parsing hacks.

## Decisions log

| Date | Decision | Why |
|---|---|---|
| 2026-11-19 | Separate bot from the "control Claude Code from phone" Telegram bot | Different purpose. This one needs its own memory, schedule and (later) voice; the other drives a coding session. They don't depend on each other. |
| 2026-11-19 | LLM via local `claude -p`, not the Claude API | $0 extra: uses the existing Claude subscription. The paid API is billed separately. DeepSeek was considered: cheap but not free, text-only. |
| 2026-11-19 | Translation direction JP → Mandarin | Tests comprehension of nuance and false friends; the learner's Chinese is native, so errors show Japanese misunderstanding, not output weakness. |
| 2026-11-19 | Don't use rare kanji for difficulty | Learner reads kanji well (Chinese speaker); difficulty comes from grammar, nuance, idioms, colloquial speech, keigo. |
| 2026-11-19 | Phased delivery: text translation first, voice later | Get the core loop solid before adding speech. |
| 2026-11-19 | Idle → silent pause, resume with `revive` | Don't spam when the learner is away. Daily lessons are also skipped while paused. |
| 2026-11-19 | Skipped sentences score 0 and count in the average | Keeps level adjustment honest. |
| 2026-11-19 | Starting `/today` with an old unfinished lesson marks it `abandoned` | Each day gets a fresh topic; old partial lessons don't block. |
| 2026-11-19 | SQLite, single learner | One user; zero setup. |
| 2026-11-19 | Runs on the Mac (tmux + caffeinate) | Free. Trade-off: no lessons while the Mac sleeps; the daily job fires only when awake. |
| 2026-11-19 | Everyday situations, common spoken Japanese | First topic (behind-the-scenes of konbini culture) was too far from daily conversation. |
| 2026-11-19 | 3 focus words + 3 focus patterns per lesson, all 10 sentences at one level | Fewer things to learn, seen repeatedly. A difficulty ramp inside a lesson made the last sentences much harder than the learner's level. |
| 2026-11-19 | 10 half-step levels (N4 … N2- N2 N2+ … N1) instead of 4 | One full JLPT level per day was too big a jump. |
| 2026-11-19 | Conversation after translation: open-ended until `/stop`, feedback after every reply, hint + spoiler translation, scores don't affect level | Practise producing the focus items, not just reading them. Level stays tied to one consistent measure (translation). |
| 2026-11-19 | One LLM call per conversation turn (grade reply + next question) | Halves the wait between replies. |
| 2026-11-19 | `handle_reply()` takes text | Voice only needs to transcribe and call it. |
| 2026-11-19 | Questions are text; replies text or voice | Learner's choice. Listening practice (TTS) can come later. |
| 2026-11-19 | Voice transcription with local mlx-whisper (large-v3-turbo) | Free, offline, no account, ~1–2 s per reply on M4. Fine for words/grammar; it smooths over pronunciation, so pronunciation feedback still needs Azure (Phase 2b). |
| 2026-11-19 | Python ≥3.12 via `uv` | System Python is 3.9; uv manages the interpreter and lockfile. |

## Roadmap

### Phase 2b — pronunciation (not started)
- Optional: conversation questions also sent by voice (TTS) for listening practice.
- Pronunciation feedback per word/sound (long vowels, っ, ん, ら行).
- Planned: Azure Speech free tier (F0, ja-JP) for speech-to-text + pronunciation assessment,
  ffmpeg for Telegram OGG → WAV. To decide before building: TTS voice, and log it here.
- Plain Whisper speech-to-text is not enough: it auto-corrects, hiding pronunciation mistakes.

### Ideas
- launchd plist for auto-start on login.
- Spaced review of past mistakes.
