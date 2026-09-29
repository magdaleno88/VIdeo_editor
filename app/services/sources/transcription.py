from typing import Protocol

from app.schemas.domain import TranscriptSegmentInput


class TranscriptionProvider(Protocol):
    name: str
    model: str

    def transcribe(self, audio_path) -> list[TranscriptSegmentInput]: ...
