from pathlib import Path

from openai import OpenAI

from app.config import get_settings


def transcribe_voice(file_path: str) -> str:
    settings = get_settings()
    if not settings.openai_api_key:
        return "Voice received, but transcription is disabled (missing OPENAI_API_KEY)."

    client = OpenAI(api_key=settings.openai_api_key)
    with Path(file_path).open("rb") as audio_file:
        transcript = client.audio.transcriptions.create(model="gpt-4o-mini-transcribe", file=audio_file)
    return transcript.text.strip()
