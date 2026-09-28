from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ConfigurationError
from app.providers.tts import ElevenLabsTTSProvider
from app.repositories.candidates import CandidateRepository
from app.repositories.narrations import NarrationRepository
from app.repositories.scripts import ScriptRepository
from app.services.narration.audio import AudioStorage
from app.services.narration.service import NarrationService


@contextmanager
def configured_narration_service(
    settings: Settings, session: Session
) -> Iterator[NarrationService]:
    key = settings.elevenlabs_api_key.get_secret_value().strip()
    model = settings.elevenlabs_model_id.strip()
    if not key:
        raise ConfigurationError("ELEVENLABS_API_KEY is required for narration")
    if not model:
        raise ConfigurationError("ELEVENLABS_MODEL_ID is required for narration")
    provider = ElevenLabsTTSProvider(
        key, model, settings.narration_timeout_seconds, settings.narration_max_retries
    )
    try:
        yield NarrationService(
            CandidateRepository(session),
            ScriptRepository(session),
            NarrationRepository(session),
            provider,
            AudioStorage(settings.narration_storage_root),
            default_voice_id=settings.elevenlabs_default_voice_id.strip(),
            default_voice_name=settings.elevenlabs_default_voice_name,
            max_characters=settings.narration_max_characters,
            max_file_size_bytes=settings.narration_max_file_size_mb * 1024 * 1024,
            duration_tolerance=settings.narration_duration_tolerance,
        )
    finally:
        provider.close()
