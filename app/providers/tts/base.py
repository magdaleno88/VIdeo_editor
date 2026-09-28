from dataclasses import dataclass
from typing import Protocol

from app.schemas.domain import VoiceSettings


@dataclass(frozen=True)
class CharacterAlignment:
    characters: list[str]
    starts: list[float]
    ends: list[float]


@dataclass(frozen=True)
class SynthesizedAudio:
    audio_bytes: bytes
    audio_format: str
    codec: str | None
    sample_rate: int | None
    channels: int | None
    character_alignment: CharacterAlignment | None
    provider_metadata: dict[str, str | int | float | bool]


class TextToSpeechProvider(Protocol):
    name: str
    model: str

    def synthesize(
        self, text: str, voice_id: str, language: str, settings: VoiceSettings
    ) -> SynthesizedAudio: ...

    def close(self) -> None: ...
