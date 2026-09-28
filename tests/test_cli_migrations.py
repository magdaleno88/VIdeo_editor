import json
from contextlib import contextmanager

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from app.cli import main
from app.core.database import build_engine
from app.core.errors import ConfigurationError
from app.core.schema import check_schema
from app.schemas.domain import ScoreInputs
from tests.conftest import ROOT


def test_cli_queries(capsys):
    assert main(["queries", "tornillo"]) == 0
    assert "screw factory" in json.loads(capsys.readouterr().out)


def test_cli_database_and_missing_candidate(tmp_path, monkeypatch, capsys):
    path = tmp_path / "cli.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path.as_posix()}")
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    assert main(["candidates", "--top", "20"]) == 0
    assert json.loads(capsys.readouterr().out)["items"] == []
    assert main(["candidate", "approve", "999", "--reviewer", "Test", "--notes", "Review"]) == 1
    assert "not found" in capsys.readouterr().err


def test_migration_upgrade_no_drift_downgrade_and_reupgrade(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'migration.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(ROOT / "alembic.ini"))
    command.upgrade(config, "head")
    command.check(config)
    engine = build_engine(url)
    check_schema(engine)
    assert "rights_records" in inspect(engine).get_table_names()
    assert "research_dossiers" in inspect(engine).get_table_names()
    assert "research_claim_evidence" in inspect(engine).get_table_names()
    assert "script_drafts" in inspect(engine).get_table_names()
    assert "script_beats" in inspect(engine).get_table_names()
    assert "script_sentences" in inspect(engine).get_table_names()
    assert "script_sentence_claims" in inspect(engine).get_table_names()
    assert "script_sentence_visual_refs" in inspect(engine).get_table_names()
    assert "script_reviews" in inspect(engine).get_table_names()
    assert "voice_profiles" in inspect(engine).get_table_names()
    assert "narration_assets" in inspect(engine).get_table_names()
    assert "video_edit_plans" in inspect(engine).get_table_names()
    assert "edit_segments" in inspect(engine).get_table_names()
    assert "render_assets" in inspect(engine).get_table_names()
    assert "render_reviews" in inspect(engine).get_table_names()
    engine.dispose()
    command.downgrade(config, "base")
    engine = build_engine(url)
    assert "video_candidates" not in inspect(engine).get_table_names()
    engine.dispose()
    command.upgrade(config, "head")


def test_migration_backfills_existing_current_score(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'backfill.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(ROOT / "alembic.ini"))
    command.upgrade(config, "0001")
    weights = dict.fromkeys(ScoreInputs.model_fields, 12.5)
    ratings = dict.fromkeys(ScoreInputs.model_fields, 64)
    score_details = {
        "method": "manual",
        "version": "1.0",
        "inputs": ratings,
        "weights": weights,
        "total": 64,
        "rationale": {},
        "unknown_dimensions": [],
        "analyzed_video": False,
    }
    engine = build_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """INSERT INTO video_candidates (
                provider, provider_video_id, source_url, preview_url, thumbnail_url,
                title, description, duration, width, height, orientation, author, author_url,
                search_query, category, industrial_process, object_being_manufactured,
                total_score, score_details, status, created_at, updated_at, version
                ) VALUES (
                'pexels', 'legacy-1', 'https://example.com/video', NULL, NULL,
                'Legacy candidate', '', 10, 100, 100, 'square', NULL, NULL,
                'legacy', NULL, NULL, 'part', 64, :details, 'SCORED',
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 1
                )"""
            ),
            {"details": json.dumps(score_details)},
        )
    engine.dispose()
    command.upgrade(config, "head")
    engine = build_engine(url)
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT method, scorer_version, ratings, weights, total_score "
                "FROM score_evaluations"
            )
        ).one()
    engine.dispose()
    assert row.method == "manual"
    assert row.scorer_version == "1.0"
    assert json.loads(row.ratings) == ratings
    assert json.loads(row.weights) == weights
    assert row.total_score == 64


def test_schema_guard_requires_migrations():
    engine = build_engine("sqlite:///:memory:")
    with pytest.raises(ConfigurationError, match="alembic upgrade"):
        check_schema(engine)
    engine.dispose()


def test_cli_discovery_review_and_history(tmp_path, monkeypatch, capsys, providers, rights_review):
    import app.cli

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'workflow.db').as_posix()}")
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")

    @contextmanager
    def mocked_providers(settings):
        yield providers

    monkeypatch.setattr(app.cli, "configured_providers", mocked_providers)
    assert main(["discover", "tornillo", "--max-queries", "1"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["created"] == 2
    candidate_id = str(result["candidate_ids"][0])
    assert main(["candidate", "show", candidate_id]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "SCORED"
    action = ["--reviewer", "CLI Reviewer", "--notes", "Inspected fixture only"]
    assert main(["candidate", "approve", candidate_id, *action]) == 1
    assert "VERIFIED" in capsys.readouterr().err
    score_file = tmp_path / "score.json"
    score_file.write_text(
        json.dumps(
            {
                "reviewer": "CLI Reviewer",
                "notes": "Manual assessment",
                "inputs": dict.fromkeys(ScoreInputs.model_fields, 75),
            }
        )
    )
    assert main(["candidate", "score", candidate_id, "--file", str(score_file)]) == 0
    assert json.loads(capsys.readouterr().out)["total_score"] == 75
    rights_file = tmp_path / "rights.json"
    rights_file.write_text(json.dumps(rights_review))
    assert main(["candidate", "rights", candidate_id, "--file", str(rights_file)]) == 0
    assert json.loads(capsys.readouterr().out)["rights_status"] == "VERIFIED"
    assert main(["candidate", "approve", candidate_id, *action]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "APPROVED"
    assert main(["candidate", "reject", candidate_id, *action]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "REJECTED"
    assert main(["candidate", "history", candidate_id]) == 0
    history = json.loads(capsys.readouterr().out)
    assert history[-1]["action"] == "REJECTED"
