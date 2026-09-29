import logging
import re
import time
from typing import Any

from pydantic import ValidationError

from app.core.errors import AnalysisError, AnalysisTimeoutError, AnalysisValidationError
from app.schemas.domain import VideoVisualAnalysisPayload
from app.services.video.assets import TemporaryVideoAsset

logger = logging.getLogger(__name__)

INPUT_METHOD = "FILES_API"
SUPPORTED_VIDEO_MIME = {"video/mp4", "video/webm", "video/quicktime"}

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
        self._api_key = api_key
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
        stage = "VIDEO_VALIDATION"
        try:
            from google.genai import types

            self._validate_asset(asset)
            context = self._context(asset, stage)
            logger.info("external_ai_analysis_started", extra=context)
            stage = "VIDEO_UPLOAD"
            remote = self.client.files.upload(
                file=asset.path,
                config=types.UploadFileConfig(mime_type=asset.mime_type),
            )
            deadline = time.monotonic() + self.timeout_seconds
            while self._state(remote) == "PROCESSING":
                if time.monotonic() >= deadline:
                    error = AnalysisTimeoutError(
                        "Gemini file processing timed out at VIDEO_UPLOAD",
                        **self._error_context(asset, stage),
                    )
                    self._log_failure(error)
                    raise error
                time.sleep(self.poll_interval_seconds)
                remote = self.client.files.get(name=remote.name)
            if self._state(remote) != "ACTIVE":
                status = getattr(remote, "error", None)
                error = self._make_error(
                    asset,
                    stage,
                    code=getattr(status, "code", None),
                    status="FILE_PROCESSING_FAILED",
                    message=getattr(status, "message", None)
                    or "Gemini could not process the video asset",
                )
                self._log_failure(error)
                raise error
            stage = "GEMINI_REQUEST"
            response = self.client.models.generate_content(
                model=self.model,
                contents=[remote, PROMPT],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=VideoVisualAnalysisPayload,
                    temperature=0,
                ),
            )
            logger.info("gemini_request_succeeded", extra=self._context(asset, stage))
            stage = "RESPONSE_PARSE"
            raw = response.parsed if response.parsed is not None else response.text
            stage = "STRUCTURED_OUTPUT_VALIDATION"
            return (
                VideoVisualAnalysisPayload.model_validate_json(raw)
                if isinstance(raw, str)
                else VideoVisualAnalysisPayload.model_validate(raw)
            )
        except (AnalysisError, AnalysisTimeoutError):
            raise
        except ValidationError as exc:
            details = "; ".join(
                f"{'.'.join(map(str, item['loc']))}: {item['msg']}"
                for item in exc.errors(include_input=False)[:5]
            )
            error = AnalysisValidationError(
                f"Gemini structured output failed validation: {details}",
                provider_message=details,
                **self._error_context(asset, "STRUCTURED_OUTPUT_VALIDATION"),
            )
            self._log_failure(error)
            raise error from exc
        except Exception as exc:
            if self._is_timeout(exc):
                message = self._sanitize(getattr(exc, "message", None) or str(exc))
                error = AnalysisTimeoutError(
                    f"Gemini timed out at {stage}: {message}",
                    provider_status="TIMEOUT",
                    provider_message=message,
                    **self._error_context(asset, stage),
                )
                self._log_failure(error)
                raise error from exc
            error = self._make_error(
                asset,
                stage,
                code=self._http_status(exc),
                status=getattr(exc, "status", None),
                message=getattr(exc, "message", None) or str(exc),
            )
            self._log_failure(error)
            raise error from exc
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

    def _validate_asset(self, asset: TemporaryVideoAsset) -> None:
        message = None
        if asset.mime_type not in SUPPORTED_VIDEO_MIME:
            message = f"Unsupported Gemini video MIME type: {asset.mime_type}"
        elif not asset.path.is_file():
            message = "Prepared video file does not exist"
        else:
            try:
                size = asset.path.stat().st_size
                with asset.path.open("rb") as source:
                    source.read(1)
            except OSError:
                message = "Prepared video file is not readable"
            else:
                if size != asset.size_bytes:
                    message = "Prepared video file size changed before upload"
        if message:
            error = AnalysisValidationError(
                message,
                provider_message=message,
                **self._error_context(asset, "VIDEO_VALIDATION"),
            )
            self._log_failure(error)
            raise error

    def _make_error(
        self,
        asset: TemporaryVideoAsset,
        stage: str,
        *,
        code: int | None,
        status: str | None,
        message: str | None,
    ) -> AnalysisError:
        safe_message = self._sanitize(message or "No provider message")
        category = self._category(code, stage)
        parts = [f"Gemini failed at {stage}"]
        if code is not None:
            parts.append(f"HTTP {code}")
        if status:
            parts.append(self._sanitize(str(status)))
        parts.append(category)
        public = "; ".join(parts) + f": {safe_message}"
        error_type = AnalysisTimeoutError if category == "timeout" else AnalysisError
        return error_type(
            public,
            http_status=code,
            provider_status=self._sanitize(str(status)) if status else None,
            provider_message=safe_message,
            **self._error_context(asset, stage),
        )

    def _error_context(self, asset: TemporaryVideoAsset, stage: str) -> dict[str, Any]:
        return {
            "stage": stage,
            "model": self.model,
            "mime_type": asset.mime_type,
            "size_bytes": asset.size_bytes,
            "duration_seconds": asset.duration_seconds,
            "input_method": INPUT_METHOD,
        }

    def _context(self, asset: TemporaryVideoAsset, stage: str) -> dict[str, Any]:
        return {
            "request_stage": stage,
            "configured_model": self.model,
            "video_mime": asset.mime_type,
            "video_size_bytes": asset.size_bytes,
            "video_duration_seconds": asset.duration_seconds,
            "input_method": INPUT_METHOD,
        }

    def _log_failure(self, error: AnalysisError) -> None:
        logger.warning(
            "gemini_video_analysis_failed",
            extra={
                **self._context_from_error(error),
                "http_status": error.http_status,
                "gemini_status": error.provider_status,
                "gemini_message": error.provider_message,
            },
        )

    @staticmethod
    def _context_from_error(error: AnalysisError) -> dict[str, Any]:
        return {
            "request_stage": error.stage,
            "configured_model": error.model,
            "video_mime": error.mime_type,
            "video_size_bytes": error.size_bytes,
            "video_duration_seconds": error.duration_seconds,
            "input_method": error.input_method,
        }

    @staticmethod
    def _http_status(exc: Exception) -> int | None:
        code = getattr(exc, "code", None)
        if isinstance(code, int):
            return code
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)
        return status_code if isinstance(status_code, int) else None

    @staticmethod
    def _is_timeout(exc: Exception) -> bool:
        return isinstance(exc, TimeoutError) or "timeout" in type(exc).__name__.lower()

    @staticmethod
    def _category(code: int | None, stage: str) -> str:
        if code == 400:
            return "invalid request or unsupported payload"
        if code == 402:
            return "billing or prepaid credits unavailable"
        if code in (401, 403):
            return "authentication or permission denied"
        if code == 404:
            return "model or endpoint not found"
        if code == 413:
            return "payload too large"
        if code == 429:
            return "quota or rate limit"
        if code is not None and code >= 500:
            return "Gemini service failure"
        if stage == "VIDEO_UPLOAD":
            return "video upload or processing failure"
        return "provider request failure"

    def _sanitize(self, value: str) -> str:
        sanitized = value.replace(self._api_key, "[REDACTED]") if self._api_key else value
        sanitized = re.sub(
            r"(?i)(key|api[_-]?key|x-goog-api-key)(?:=|:|%3D)\s*[^\s&,]+",
            r"\1=[REDACTED]",
            sanitized,
        )
        sanitized = re.sub(r"(?i)bearer\s+[A-Za-z0-9._~-]+", "Bearer [REDACTED]", sanitized)
        return " ".join(sanitized.split())[:500]
