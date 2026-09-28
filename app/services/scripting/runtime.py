from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ConfigurationError
from app.providers.scripting import GeminiScriptGenerationProvider
from app.repositories.candidates import CandidateRepository
from app.repositories.research import ResearchRepository
from app.repositories.scripts import ScriptRepository
from app.services.scripting.service import ScriptGenerationService


@contextmanager
def configured_script_service(
    settings: Settings, session: Session
) -> Iterator[ScriptGenerationService]:
    key = settings.gemini_api_key.get_secret_value().strip()
    model = settings.gemini_script_model.strip()
    if not key:
        raise ConfigurationError("GEMINI_API_KEY is required for script generation")
    if not model:
        raise ConfigurationError("GEMINI_SCRIPT_MODEL is required for script generation")
    provider = GeminiScriptGenerationProvider(
        key,
        model,
        settings.script_timeout_seconds,
        settings.script_max_retries,
    )
    try:
        yield ScriptGenerationService(
            CandidateRepository(session),
            ResearchRepository(session),
            ScriptRepository(session),
            provider,
            max_variants=settings.script_max_variants,
            max_claims=settings.script_max_claims,
            max_context_characters=settings.script_max_context_characters,
            speaking_rate=settings.script_speaking_rate_wpm,
            duration_tolerance=settings.script_duration_tolerance,
            allow_partially_supported=settings.script_allow_partially_supported,
        )
    finally:
        provider.close()
