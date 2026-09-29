from types import SimpleNamespace

import pytest
from google.genai.errors import APIError

from app.core.errors import AnalysisError, AnalysisTimeoutError, AnalysisValidationError
from app.providers.ai.gemini import PROMPT, GeminiVideoAnalysisProvider
from app.services.video.assets import TemporaryVideoAsset
from tests.test_ai_scoring import analysis_payload


class FakeFiles:
    def __init__(self, state="ACTIVE", error=None, processing_error=None):
        self.state = state
        self.error = error
        self.processing_error = processing_error
        self.deleted = []

    def upload(self, **kwargs):
        if self.error:
            raise self.error
        return SimpleNamespace(
            name="files/test",
            state=SimpleNamespace(name=self.state),
            error=self.processing_error,
        )

    def get(self, name):
        return SimpleNamespace(
            name=name,
            state=SimpleNamespace(name=self.state),
            error=self.processing_error,
        )

    def delete(self, name):
        self.deleted.append(name)


class FakeModels:
    def __init__(self, parsed=None, error=None):
        self.parsed = parsed
        self.error = error
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(parsed=self.parsed, text=None)


def provider(files=None, models=None, **kwargs):
    client = SimpleNamespace(
        files=files or FakeFiles(), models=models or FakeModels(analysis_payload())
    )
    return GeminiVideoAnalysisProvider(
        "SECRET", "configured-model", 10, client=client, poll_interval_seconds=0, **kwargs
    ), client


def asset(tmp_path):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"video")
    return TemporaryVideoAsset(path, "video/mp4", 5, 12)


def test_gemini_structured_success_and_remote_cleanup(tmp_path):
    service, client = provider()
    result = service.analyze(asset(tmp_path))
    assert result.dimensions.visible_transformation.rating == 80
    assert client.files.deleted == ["files/test"]
    call = client.models.calls[0]
    assert call["model"] == "configured-model"
    assert PROMPT in call["contents"]
    assert call["config"].response_mime_type == "application/json"


def test_gemini_malformed_output_is_distinct_and_cleanup_runs(tmp_path):
    service, client = provider(models=FakeModels({"summary": "incomplete"}))
    with pytest.raises(AnalysisValidationError, match="structured output failed"):
        service.analyze(asset(tmp_path))
    assert client.files.deleted == ["files/test"]


def test_gemini_model_error_is_sanitized_and_cleanup_runs(tmp_path):
    service, client = provider(models=FakeModels(error=RuntimeError("SECRET https://sensitive")))
    with pytest.raises(AnalysisError, match="GEMINI_REQUEST") as error:
        service.analyze(asset(tmp_path))
    assert "SECRET" not in str(error.value)
    assert client.files.deleted == ["files/test"]


def test_gemini_processing_failure_and_timeout(tmp_path):
    service, client = provider(files=FakeFiles("FAILED"))
    with pytest.raises(AnalysisError, match="VIDEO_UPLOAD"):
        service.analyze(asset(tmp_path))
    assert client.files.deleted == ["files/test"]

    processing = FakeFiles("PROCESSING")
    client = SimpleNamespace(files=processing, models=FakeModels(analysis_payload()))
    service = GeminiVideoAnalysisProvider(
        "SECRET", "configured-model", -1, client=client, poll_interval_seconds=0
    )
    with pytest.raises(AnalysisTimeoutError, match="timed out"):
        service.analyze(asset(tmp_path))
    assert processing.deleted == ["files/test"]


@pytest.mark.parametrize(
    ("code", "status", "category"),
    (
        (400, "INVALID_ARGUMENT", "invalid request"),
        (402, "RESOURCE_EXHAUSTED", "billing or prepaid credits"),
        (401, "UNAUTHENTICATED", "authentication"),
        (403, "PERMISSION_DENIED", "permission"),
        (404, "NOT_FOUND", "model or endpoint"),
        (413, "PAYLOAD_TOO_LARGE", "payload too large"),
        (429, "RESOURCE_EXHAUSTED", "quota or rate limit"),
        (500, "INTERNAL", "service failure"),
    ),
)
def test_gemini_api_errors_preserve_safe_diagnostics(tmp_path, caplog, code, status, category):
    secret = "SECRET-LIVE-KEY"
    failure = APIError(
        code,
        {"error": {"code": code, "status": status, "message": f"safe reason {secret}"}},
    )
    service, _ = provider(models=FakeModels(error=failure))
    service._api_key = secret
    with pytest.raises(AnalysisError, match=category) as caught:
        service.analyze(asset(tmp_path))
    error = caught.value
    assert error.stage == "GEMINI_REQUEST"
    assert error.http_status == code
    assert error.provider_status == status
    assert error.provider_message == "safe reason [REDACTED]"
    assert error.input_method == "FILES_API"
    assert secret not in str(error)
    assert secret not in caplog.text


def test_gemini_upload_failure_timeout_invalid_mime_and_processing_error(tmp_path):
    upload_failure = APIError(
        403,
        {"error": {"code": 403, "status": "PERMISSION_DENIED", "message": "upload denied"}},
    )
    service, _ = provider(files=FakeFiles(error=upload_failure))
    with pytest.raises(AnalysisError, match="VIDEO_UPLOAD") as caught:
        service.analyze(asset(tmp_path))
    assert caught.value.http_status == 403

    service, _ = provider(models=FakeModels(error=TimeoutError("request timed out")))
    with pytest.raises(AnalysisTimeoutError, match="timed out"):
        service.analyze(asset(tmp_path))

    invalid = asset(tmp_path)
    invalid = TemporaryVideoAsset(invalid.path, "text/html", invalid.size_bytes, 12)
    service, client = provider()
    with pytest.raises(AnalysisValidationError, match="Unsupported.*MIME") as caught:
        service.analyze(invalid)
    assert caught.value.stage == "VIDEO_VALIDATION"
    assert client.files.deleted == []

    processing_error = SimpleNamespace(code=13, message="codec processing failed")
    service, _ = provider(files=FakeFiles("FAILED", processing_error=processing_error))
    with pytest.raises(AnalysisError, match="codec processing failed") as caught:
        service.analyze(asset(tmp_path))
    assert caught.value.provider_status == "FILE_PROCESSING_FAILED"


def test_prompt_explicitly_forbids_unsupported_facts_and_requires_opening_analysis():
    lower = PROMPT.lower()
    for term in (
        "temperature",
        "production volume",
        "chemistry",
        "first 1-3 seconds",
        "do not force",
        "do not predict views",
    ):
        assert term in lower
