from types import SimpleNamespace

import pytest

from app.core.errors import AnalysisError, AnalysisTimeoutError, AnalysisValidationError
from app.providers.ai.gemini import PROMPT, GeminiVideoAnalysisProvider
from app.services.video.assets import TemporaryVideoAsset
from tests.test_ai_scoring import analysis_payload


class FakeFiles:
    def __init__(self, state="ACTIVE"):
        self.state = state
        self.deleted = []

    def upload(self, **kwargs):
        return SimpleNamespace(name="files/test", state=SimpleNamespace(name=self.state))

    def get(self, name):
        return SimpleNamespace(name=name, state=SimpleNamespace(name=self.state))

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
    return TemporaryVideoAsset(path, "video/mp4", 5)


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
    with pytest.raises(AnalysisValidationError, match="invalid structured"):
        service.analyze(asset(tmp_path))
    assert client.files.deleted == ["files/test"]


def test_gemini_model_error_is_sanitized_and_cleanup_runs(tmp_path):
    service, client = provider(models=FakeModels(error=RuntimeError("SECRET https://sensitive")))
    with pytest.raises(AnalysisError, match="request failed") as error:
        service.analyze(asset(tmp_path))
    assert "SECRET" not in str(error.value)
    assert client.files.deleted == ["files/test"]


def test_gemini_processing_failure_and_timeout(tmp_path):
    service, client = provider(files=FakeFiles("FAILED"))
    with pytest.raises(AnalysisError, match="could not process"):
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
