import json
from contextlib import contextmanager

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.providers.ai.gemini import GeminiVideoAnalysisProvider
from app.services.video.assets import TemporaryVideoAsset, VideoAssetFetcher
from tests.test_ai_scoring import analysis_payload


@contextmanager
def fake_fetch(self, candidate):
    yield TemporaryVideoAsset(__file__, "video/mp4", 10)


def fake_analyze(self, asset):
    from app.schemas.domain import VideoVisualAnalysisPayload

    return VideoVisualAnalysisPayload.model_validate(analysis_payload())


def test_ai_api_workflow_and_missing_configuration(engine, providers, monkeypatch):
    settings = Settings(
        _env_file=None,
        gemini_api_key="test-secret",
        gemini_video_model="test-model",
        pexels_api_key="",
        pixabay_api_key="",
    )
    app = create_app(settings, engine)
    from app.api.dependencies import get_providers

    app.dependency_overrides[get_providers] = lambda: providers
    monkeypatch.setattr(VideoAssetFetcher, "fetch", fake_fetch)
    monkeypatch.setattr(GeminiVideoAnalysisProvider, "analyze", fake_analyze)
    with TestClient(app) as client:
        discovered = client.post(
            "/discovery/search", json={"object_name": "screw", "max_queries": 1}
        ).json()
        candidate_id = discovered["candidate_ids"][0]
        url = f"/candidates/{candidate_id}"
        scored = client.post(f"{url}/ai-score", json={"force": False})
        assert scored.status_code == 200
        assert scored.json()["evaluation"]["method"] == "ai_visual"
        assert scored.json()["reused"] is False
        assert client.post(f"{url}/ai-score", json={"force": False}).json()["reused"] is True
        assert client.post(f"{url}/ai-score", json={"force": True}).json()["reused"] is False
        evaluations = client.get(f"{url}/evaluations").json()
        assert [item["method"] for item in evaluations] == [
            "metadata_heuristic",
            "ai_visual",
            "ai_visual",
        ]
        assert client.get(f"{url}/score-comparison").status_code == 409
        manual = {
            "reviewer": "API reviewer",
            "notes": "Watched fixture",
            "inputs": dict.fromkeys(analysis_payload()["dimensions"], 70),
        }
        assert client.post(f"{url}/score", json=manual).status_code == 200
        comparison = client.get(f"{url}/score-comparison")
        assert comparison.status_code == 200
        assert comparison.json()["mean_absolute_error"] == 10

    missing = create_app(Settings(_env_file=None), engine)
    with TestClient(missing) as client:
        assert client.post("/candidates/999999/ai-score", json={}).status_code == 404
        response = client.post(f"/candidates/{candidate_id}/ai-score", json={})
        assert response.status_code == 503
        assert "GEMINI_API_KEY" in response.json()["detail"]
        assert "test-secret" not in response.text

    missing_model = create_app(
        Settings(_env_file=None, gemini_api_key="another-secret", gemini_video_model=""),
        engine,
    )
    with TestClient(missing_model) as client:
        response = client.post(f"/candidates/{candidate_id}/ai-score", json={})
        assert response.status_code == 503
        assert "GEMINI_VIDEO_MODEL" in response.json()["detail"]
        assert "another-secret" not in response.text


def test_ai_cli_commands(tmp_path, monkeypatch, capsys, providers):
    from alembic import command
    from alembic.config import Config

    import app.cli
    from app.services.scoring.ai import AIVideoScorer
    from tests.conftest import ROOT
    from tests.test_ai_scoring import FakeAnalysisProvider, FakeFetcher

    database = tmp_path / "cli-ai.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database.as_posix()}")
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")

    @contextmanager
    def mock_discovery(settings):
        yield providers

    @contextmanager
    def mock_ai(settings, repository):
        yield AIVideoScorer(
            repository,
            FakeFetcher(tmp_path / "cli-video.mp4"),
            FakeAnalysisProvider(),
            settings.scoring_weights,
        )

    monkeypatch.setattr(app.cli, "configured_providers", mock_discovery)
    monkeypatch.setattr(app.cli, "configured_ai_scorer", mock_ai)
    assert app.cli.main(["discover", "screw", "--max-queries", "1"]) == 0
    candidate_id = str(json.loads(capsys.readouterr().out)["candidate_ids"][0])
    assert app.cli.main(["candidate", "ai-score", candidate_id]) == 0
    assert json.loads(capsys.readouterr().out)["reused"] is False
    assert app.cli.main(["candidate", "ai-score", candidate_id]) == 0
    assert json.loads(capsys.readouterr().out)["reused"] is True
    assert app.cli.main(["candidate", "evaluations", candidate_id]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 2
    assert app.cli.main(["candidate", "compare-scores", candidate_id]) == 1
    assert "Both AI and manual" in capsys.readouterr().err
