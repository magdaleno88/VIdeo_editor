import json
from contextlib import contextmanager

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

import app.api.dependencies
import app.cli
from app.core.config import Settings
from app.core.database import build_engine, session_factory, session_scope
from app.main import create_app
from app.repositories.candidates import CandidateRepository
from app.repositories.research import ResearchRepository
from app.schemas.domain import ResearchReview
from app.services.research.service import ResearchReviewService
from tests.conftest import ROOT
from tests.test_research import make_service, seed_research_candidate
from tests.test_scripting import FakeScriptProvider, scripting_service


def seed_verified(session, video):
    candidate = seed_research_candidate(session, video)
    make_service(session)[0].research(candidate.id)
    ResearchReviewService(CandidateRepository(session), ResearchRepository(session)).review(
        candidate.id,
        ResearchReview(decision="VERIFIED", reviewer="Human", notes="Checked"),
    )
    return candidate


def test_script_api_configuration_not_found_generation_and_review(engine, video, monkeypatch):
    with session_scope(session_factory(engine)) as session:
        candidate_id = seed_verified(session, video).id

    unconfigured = create_app(Settings(_env_file=None), engine)
    with TestClient(unconfigured) as client:
        response = client.post(
            f"/candidates/{candidate_id}/scripts", json={"target_duration_seconds": 30}
        )
        assert response.status_code == 503
        assert "GEMINI_API_KEY" in response.json()["detail"]
        assert client.post("/candidates/99999/scripts", json={}).status_code == 404

    @contextmanager
    def fake_runtime(settings, session):
        yield scripting_service(session, FakeScriptProvider())[0]

    monkeypatch.setattr(app.api.dependencies, "configured_script_service", fake_runtime)
    configured = create_app(Settings(_env_file=None), engine)
    with TestClient(configured) as client:
        generated = client.post(
            f"/candidates/{candidate_id}/scripts", json={"target_duration_seconds": 30}
        )
        assert generated.status_code == 200
        assert generated.json()["reused"] is False
        script_id = generated.json()["scripts"][0]["id"]
        assert client.get(f"/scripts/{script_id}").status_code == 200
        assert len(client.get(f"/candidates/{candidate_id}/scripts").json()) == 1
        approved = client.post(
            f"/scripts/{script_id}/approve",
            json={"reviewer": "API human", "notes": "Ready"},
        )
        assert approved.status_code == 200
        assert approved.json()["status"] == "APPROVED"


def test_script_cli_generation_listing_show_and_rejection(tmp_path, video, monkeypatch, capsys):
    database = tmp_path / "script-cli.db"
    url = f"sqlite:///{database.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    engine = build_engine(url)
    with session_scope(session_factory(engine)) as session:
        candidate_id = seed_verified(session, video).id
    engine.dispose()

    @contextmanager
    def fake_runtime(settings, session):
        yield scripting_service(session, FakeScriptProvider())[0]

    monkeypatch.setattr(app.cli, "configured_script_service", fake_runtime)
    candidate = str(candidate_id)
    assert (
        app.cli.main(
            ["candidate", "script", candidate, "--duration", "30", "--style", "educational"]
        )
        == 0
    )
    generated = json.loads(capsys.readouterr().out)
    script_id = str(generated["scripts"][0]["id"])
    assert app.cli.main(["candidate", "scripts", candidate]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 1
    assert app.cli.main(["script", "show", script_id]) == 0
    assert json.loads(capsys.readouterr().out)["id"] == int(script_id)
    assert (
        app.cli.main(
            [
                "script",
                "reject",
                script_id,
                "--reviewer",
                "CLI human",
                "--notes",
                "Hook needs work",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["status"] == "REJECTED"
