import logging
import time
from typing import Any

from pydantic import ValidationError

from app.core.errors import AnalysisError, AnalysisTimeoutError, AnalysisValidationError
from app.schemas.domain import VideoVisualAnalysisPayload
from app.services.video.assets import TemporaryVideoAsset

logger = logging.getLogger(__name__)

PROMPT_VERSION = "visual-analysis-v1"
PROMPT = """Analyze only visible evidence in this industrial video for suitability as an
educational short-form video. Do not identify a machine, material, technical process, speed,
temperature, production volume, chemistry, or product unless it is visually evident. Use null
for uncertain detected process/object, lower confidence, and add a warning. Do not perform
factual research and do not predict views or guaranteed virality.

Rate exactly these eight dimensions from 0 to 100 using their literal meanings: movement,
visible transformation, satisfying result, unusual machinery, understandable without audio,
visual hook, educational potential, and loop potential. For every dimension provide confidence,
an explanation, and timestamped evidence when visible. Do not force evidence or transformation.
Specifically inspect approximately the first 1-3 seconds for immediate motion, novelty,
transformation, clarity, and required context. A transformation requires a visibly changed
material or object across the shown process. Identify zero to five strong hook segments and zero
to five possible loop segments. Loop candidates should be repetitive cycles or end visually near
their start. All timestamps must be within the duration actually analyzed. Describe observations,
not hidden causes or unsupported technical facts."""


class GeminiVideoAnalysisProvider:
    name = "gemini"
    prompt_version = PROMPT_VERSION

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_seconds: float,
        client: Any | None = None,
        poll_interval_seconds: float = 2,
    ) -> None:
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        if client is None:
            try:
                from google import genai
                from google.genai import types
            except ImportError:
                raise AnalysisError("Google Gen AI SDK is not installed") from None
            client = genai.Client(
                api_key=api_key,
                http_options=types.HttpOptions(timeout=int(timeout_seconds * 1000)),
            )
        self.client = client

    def close(self) -> None:
        close = getattr(self.client, "close", None)
        if close is not None:
            try:
                close()
            except Exception:
                logger.warning("gemini_client_cleanup_failed")

    def analyze(self, asset: TemporaryVideoAsset) -> VideoVisualAnalysisPayload:
        remote = None
        try:
            from google.genai import types

            logger.info("external_ai_analysis_started", extra={"ai_provider": self.name})
            remote = self.client.files.upload(
                file=asset.path,
                config=types.UploadFileConfig(mime_type=asset.mime_type),
            )
            deadline = time.monotonic() + self.timeout_seconds
            while self._state(remote) == "PROCESSING":
                if time.monotonic() >= deadline:
                    raise AnalysisTimeoutError("Gemini file processing timed out")
                time.sleep(self.poll_interval_seconds)
                remote = self.client.files.get(name=remote.name)
            if self._state(remote) != "ACTIVE":
                raise AnalysisError("Gemini could not process the video asset")
            response = self.client.models.generate_content(
                model=self.model,
                contents=[remote, PROMPT],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=VideoVisualAnalysisPayload,
                    temperature=0,
                ),
            )
            raw = response.parsed if response.parsed is not None else response.text
            return (
                VideoVisualAnalysisPayload.model_validate_json(raw)
                if isinstance(raw, str)
                else VideoVisualAnalysisPayload.model_validate(raw)
            )
        except (AnalysisError, AnalysisTimeoutError):
            raise
        except ValidationError:
            raise AnalysisValidationError(
                "Gemini returned an invalid structured analysis"
            ) from None
        except Exception:
            raise AnalysisError("Gemini analysis request failed") from None
        finally:
            if remote is not None and getattr(remote, "name", None):
                try:
                    self.client.files.delete(name=remote.name)
                except Exception:
                    logger.warning("gemini_remote_file_cleanup_failed")

    @staticmethod
    def _state(remote: Any) -> str:
        state = getattr(remote, "state", None)
        return str(getattr(state, "name", state or "")).upper()
