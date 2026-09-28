from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.models import VideoCandidate
from app.repositories.candidates import CandidateRepository
from app.repositories.captions import CaptionRepository
from app.repositories.narrations import NarrationRepository
from app.schemas.domain import CaptionPlanRequest, FinalRenderStatus, Idea
from tests.test_captions import (
    approved_raw,
    caption_service,
    final_service,
)


@pytest.fixture
def dashboard_data(engine, session, video, tmp_path):
    candidate, script, narration, raw = approved_raw(session, video, tmp_path)
    plan = caption_service(session).create(raw.id, CaptionPlanRequest())
    final = final_service(session, tmp_path).render(plan.id).render
    final = final_service(session, tmp_path).preview(final.id, 1.5)
    ids = {
        "candidate": candidate.id,
        "script": script.id,
        "narration": narration.id,
        "raw": raw.id,
        "plan": plan.id,
        "final": final.id,
    }
    session.commit()
    settings = Settings(
        _env_file=None,
        narration_storage_root=str(tmp_path / "audio"),
        render_storage_root=str(tmp_path / "renders"),
        caption_storage_root=str(tmp_path / "captions"),
        dashboard_default_reviewer="Dashboard tester",
    )
    app = create_app(settings, engine)
    with TestClient(app) as dashboard:
        ids["csrf"] = app.state.dashboard_csrf_token
        yield dashboard, ids, app


def test_dashboard_home_candidate_list_and_detail(dashboard_data):
    client, ids, _ = dashboard_data
    home = client.get("/")
    assert home.status_code == 200
    assert "Production dashboard" in home.text
    assert "Final renders awaiting review" in home.text
    listing = client.get("/dashboard/candidates?sort=score&page=1&per_page=5")
    assert listing.status_code == 200
    assert "Candidates" in listing.text and "Page 1 of" in listing.text
    detail = client.get(f"/dashboard/candidates/{ids['candidate']}")
    assert detail.status_code == 200
    for heading in (
        "Visual analysis",
        "Research",
        "Script",
        "Narration",
        "Raw render",
        "Final render",
    ):
        assert heading in detail.text
    assert "External API" in detail.text


def test_review_queue_final_detail_status_and_timestamp_navigation(dashboard_data):
    client, ids, _ = dashboard_data
    queue = client.get("/dashboard/review-queue?stage=final-renders")
    assert queue.status_code == 200
    assert f"#{ids['final']}" in queue.text
    detail = client.get(f"/dashboard/final-renders/{ids['final']}")
    assert detail.status_code == 200
    assert "Final render review" in detail.text
    assert "data-seek=" in detail.text
    assert "NEEDS_REVIEW" in detail.text or "VALIDATED" in detail.text
    assert "Preview" in detail.text and "Versions" in detail.text


@pytest.mark.parametrize(
    ("path_key", "expected_type"),
    (
        ("narration", "audio/"),
        ("raw", "video/mp4"),
        ("final", "video/mp4"),
        ("preview", "image/png"),
    ),
)
def test_safe_media_routes_and_content_types(dashboard_data, path_key, expected_type):
    client, ids, _ = dashboard_data
    routes = {
        "narration": f"/media/audio/{ids['narration']}",
        "raw": f"/media/render/{ids['raw']}",
        "final": f"/media/final-render/{ids['final']}",
        "preview": f"/media/preview/{ids['final']}",
    }
    response = client.get(routes[path_key])
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(expected_type)
    assert response.content


def test_media_range_unknown_ids_and_traversal_are_safe(dashboard_data):
    client, ids, app = dashboard_data
    ranged = client.get(f"/media/final-render/{ids['final']}", headers={"Range": "bytes=0-3"})
    assert ranged.status_code == 206
    assert client.get("/media/final-render/999999").status_code == 404
    assert client.get("/media/final-render/../../.env").status_code in (404, 422)
    assert client.get("/media/final-render/%2e%2e%2f%2e%2e%2f.env").status_code in (
        404,
        422,
    )
    with app.state.session_factory.begin() as session:
        narration = NarrationRepository(session).get(ids["narration"])
        original = narration.storage_path
        narration.storage_path = "../../.env"
    assert client.get(f"/media/audio/{ids['narration']}").status_code == 404
    with app.state.session_factory.begin() as session:
        narration = NarrationRepository(session).get(ids["narration"])
        narration.storage_path = str(Path.cwd() / ".env")
    assert client.get(f"/media/audio/{ids['narration']}").status_code == 404
    with app.state.session_factory.begin() as session:
        NarrationRepository(session).get(ids["narration"]).storage_path = original


def test_final_render_approval_rejection_and_csrf(dashboard_data):
    client, ids, app = dashboard_data
    route = f"/dashboard/review/final-render/{ids['final']}/approve"
    assert client.post(route, data={"reviewer": "X", "notes": "Y"}).status_code == 403
    approved = client.post(
        route,
        data={
            "csrf_token": ids["csrf"],
            "return_to": f"/dashboard/final-renders/{ids['final']}",
            "reviewer": "Dashboard tester",
            "notes": "Visual check passed",
        },
        follow_redirects=False,
    )
    assert approved.status_code == 303
    with app.state.session_factory() as session:
        assert (
            CaptionRepository(session).get_render(ids["final"]).status == FinalRenderStatus.APPROVED
        )
    rejected = client.post(
        f"/dashboard/review/final-render/{ids['final']}/reject",
        data={
            "csrf_token": ids["csrf"],
            "return_to": f"/dashboard/final-renders/{ids['final']}",
            "reviewer": "Dashboard tester",
            "notes": "Caption covers machinery",
        },
        follow_redirects=False,
    )
    assert rejected.status_code == 303
    with app.state.session_factory() as session:
        assert (
            CaptionRepository(session).get_render(ids["final"]).status == FinalRenderStatus.REJECTED
        )


def test_rejection_requires_reason_and_errors_are_rendered(dashboard_data):
    client, ids, _ = dashboard_data
    response = client.post(
        f"/dashboard/review/render/{ids['raw']}/reject",
        data={
            "csrf_token": ids["csrf"],
            "return_to": f"/dashboard/candidates/{ids['candidate']}",
            "reviewer": "Dashboard tester",
            "notes": "",
        },
    )
    assert response.status_code == 200
    assert "A rejection reason is required" in response.text


def test_pages_escape_database_text_and_do_not_run_paid_actions(dashboard_data, monkeypatch):
    client, ids, app = dashboard_data
    with app.state.session_factory.begin() as session:
        candidate = session.get(VideoCandidate, ids["candidate"])
        candidate.title = "<script>alert('x')</script>"

    def forbidden(*args, **kwargs):
        raise AssertionError("Paid action ran during a GET request")

    monkeypatch.setattr("app.services.scoring.ai.AIVideoScorer.score", forbidden)
    response = client.get(f"/dashboard/candidates/{ids['candidate']}")
    assert response.status_code == 200
    assert "&lt;script&gt;" in response.text
    assert "<script>alert" not in response.text


def test_pipeline_action_buttons_are_prerequisite_gated(dashboard_data, video):
    client, _, app = dashboard_data
    with app.state.session_factory.begin() as session:
        fresh = video.model_copy(update={"provider_video_id": "dashboard-gating"})
        candidate, _ = CandidateRepository(session).add_if_new(
            fresh,
            "dashboard gating",
            Idea(object_name="test object"),
        )
        candidate_id = candidate.id
    response = client.get(f"/dashboard/candidates/{candidate_id}")
    assert response.status_code == 200
    assert "Run AI score" in response.text
    assert "Run research" not in response.text
    assert "Generate script" not in response.text
    assert "Generate narration" not in response.text
    assert "Create render plan" not in response.text


def test_preview_is_not_generated_on_page_load(dashboard_data):
    client, ids, app = dashboard_data
    with app.state.session_factory.begin() as session:
        render = CaptionRepository(session).get_render(ids["final"])
        render.preview_path = None
    response = client.get(f"/dashboard/final-renders/{ids['final']}")
    assert response.status_code == 200
    assert "Generate at 1.5 s" in response.text
    with app.state.session_factory() as session:
        assert CaptionRepository(session).get_render(ids["final"]).preview_path is None
