"""Speech-to-text for voice replies, with local Whisper (mlx-whisper, Apple Silicon).

Free and offline. The model downloads once on first use (~1.6 GB for large-v3-turbo).
Whisper tidies up what it hears, so this is fine for checking words and grammar but not
for pronunciation feedback (Phase 2b plans Azure pronunciation assessment for that).
"""

import asyncio
import os
import tempfile

import mlx_whisper
from telegram import Voice


def _transcribe(path: str) -> str:
    result = mlx_whisper.transcribe(
        path,
        path_or_hf_repo=os.getenv("WHISPER_MODEL", "mlx-community/whisper-large-v3-turbo"),
        language="ja",
    )
    return result["text"].strip()


async def transcribe(voice: Voice) -> str:
    """Download a Telegram voice message (OGG/Opus) and return the Japanese text."""
    f = await voice.get_file()
    with tempfile.NamedTemporaryFile(suffix=".ogg") as tmp:
        await f.download_to_drive(tmp.name)
        return await asyncio.to_thread(_transcribe, tmp.name)  # ffmpeg decodes the OGG
