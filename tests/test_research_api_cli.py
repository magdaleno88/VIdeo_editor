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
from tests.conftest import ROOT
from tests.test_research import make_service, seed_research_candidate


def test_research_api_workflow_configuration_and_not_found(engine, video, monkeypatch):
    with session_scope(session_factory(engine)) as session:
        candidate_id = seed_research_candidate(session, video).id

    unconfigured = create_app(Settings(_env_file=None), engine)
    with TestClient(unconfigured) as client:
        missing = client.post(f"/candidates/{candidate_id}/research", json={})
        assert missing.status_code == 503
        assert "BRAVE_SEARCH_API_KEY" in missing.json()["detail"]
        assert client.post("/candidates/99999/research", json={}).status_code == 404

    @contextmanager
    def fake_runtime(settings, session):
        yield make_service(session)[0]

    monkeypatch.setattr(app.api.dependencies, "configured_research_service", fake_runtime)
    configured = create_app(Settings(_env_file=None), engine)
    with TestClient(configured) as client:
        url = f"/candidates/{candidate_id}/research"
        created = client.post(url, json={})
        assert created.status_code == 200
        assert created.json()["reused"] is False
        assert client.post(url, json={}).json()["reused"] is True
        assert len(client.get(url).json()) == 1
        assert len(client.get(f"{url}/claims").json()) == 4
        assert len(client.get(f"{url}/sources").json()) == 2
        verified = client.post(
            f"{url}/verify",
            json={"decision": "VERIFIED", "reviewer": "API human", "notes": "Checked"},
        )
        assert verified.status_code == 200
        assert verified.json()["status"] == "VERIFIED"


def test_research_cli_commands(tmp_path, video, monkeypatch, capsys):
    database = tmp_path / "research-cli.db"
    url = f"sqlite:///{database.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(ROOT / "alembic.ini"))
    command.upgrade(config, "head")
    engine = build_engine(url)
    with session_scope(session_factory(engine)) as session:
        candidate_id = seed_research_candidate(session, video).id
    engine.dispose()

    @contextmanager
    def fake_runtime(settings, session):
        yield make_service(session)[0]

    monkeypatch.setattr(app.cli, "configured_research_service", fake_runtime)
    candidate_id = str(candidate_id)
    assert app.cli.main(["candidate", "research", candidate_id]) == 0
    assert json.loads(capsys.readouterr().out)["reused"] is False
    assert app.cli.main(["candidate", "research", candidate_id]) == 0
    assert json.loads(capsys.readouterr().out)["reused"] is True
    assert app.cli.main(["candidate", "research-show", candidate_id]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "NEEDS_REVIEW"
    assert app.cli.main(["candidate", "claims", candidate_id]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 4
    assert app.cli.main(["candidate", "sources", candidate_id]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 2
    assert (
        app.cli.main(
            [
                "candidate",
                "research-verify",
                candidate_id,
                "--decision",
                "VERIFIED",
                "--reviewer",
                "CLI human",
                "--notes",
                "Sources checked",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["status"] == "VERIFIED"
