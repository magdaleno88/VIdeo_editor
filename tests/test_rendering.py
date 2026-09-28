import json
import subprocess
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import ValidationError

import app.api.dependencies
import app.cli
from app.core.config import ScoringWeights, Settings
from app.core.database import build_engine, session_factory, session_scope
from app.core.errors import (
    ConflictError,
    RenderConfigurationError,
    RenderProcessError,
    RenderStorageError,
    RenderTimeoutError,
    RenderValidationError,
)
from app.main import create_app
from app.providers.rendering.base import MediaMetadata, RenderExecution
from app.providers.rendering.ffmpeg import (
    FFmpegCommandBuilder,
    FFmpegVideoRenderer,
    parse_probe,
)
from app.repositories.candidates import CandidateRepository
from app.repositories.narrations import NarrationRepository
from app.repositories.renders import RenderRepository
from app.repositories.scripts import ScriptRepository
from app.schemas.domain import (
    CompositionStrategy,
    NarrationRequest,
    NarrationReviewRequest,
    RenderPlanRequest,
    RenderReviewRequest,
    RenderStatus,
    ReviewAction,
    RightsReview,
)
from app.services.narration.service import NarrationReviewService
from app.services.rendering.service import EditPlanService, RenderReviewService, RenderService
from app.services.rendering.storage import NarrationFileResolver, RenderStorage
from app.services.review import ReviewService
from tests.conftest import ROOT
from tests.test_narration import approved_script, narration_service
from tests.test_research import seed_research_candidate


def seed_approved_assets(session, video, tmp_path):
    candidate = seed_research_candidate(session, video)
    script = approved_script(session, candidate)
    narration = (
        narration_service(session, tmp_path, duration=script.estimated_duration_seconds)[0]
        .generate(script.id, NarrationRequest())
        .narration
    )
    narration = NarrationReviewService(
        CandidateRepository(session), ScriptRepository(session), NarrationRepository(session)
    ).review(
        narration.id,
        NarrationReviewRequest(decision="APPROVED", reviewer="Audio", notes="Checked"),
    )
    review = ReviewService(CandidateRepository(session), ScoringWeights())
    review.review_rights(
        candidate.id,
        RightsReview(
            rights_status="VERIFIED",
            license_name="Test license",
            license_url="https://example.com/license",
            commercial_use_allowed=True,
            modification_allowed=True,
            attribution_required=False,
            evidence_url="https://example.com/evidence",
            reviewer="Rights editor",
            notes="Verified fixture",
        ),
    )
    review.approve(candidate.id, ReviewAction(reviewer="Editor", notes="Ready"))
    return candidate, script, narration


@pytest.fixture
def approved_assets(session, video, tmp_path):
    return seed_approved_assets(session, video, tmp_path)


def planner(session, *, min_speed=0.8, max_speed=1.25):
    return EditPlanService(
        session,
        CandidateRepository(session),
        ScriptRepository(session),
        NarrationRepository(session),
        RenderRepository(session),
        width=1080,
        height=1920,
        fps=30,
        min_speed=min_speed,
        max_speed=max_speed,
        pre_roll_ms=150,
        post_roll_ms=150,
    )


class FakeFetcher:
    def __init__(self, path: Path):
        self.path = path
        self.calls = 0

    @contextmanager
    def fetch(self, candidate):
        self.calls += 1
        yield SimpleNamespace(path=self.path, mime_type="video/mp4", size_bytes=10)


class FakeRenderer:
    name = "fake"
    version = "fake-1"

    def __init__(self, *, fail=False, bad_output=False):
        self.fail = fail
        self.bad_output = bad_output
        self.calls = 0

    def probe(self, path):
        if path.suffix == ".wav":
            return MediaMetadata(20, None, None, None, None, "pcm_s16le", False, True, 0, "wav")
        if ".part" in path.name:
            return MediaMetadata(
                20,
                720 if self.bad_output else 1080,
                1920,
                30,
                "h264",
                "aac",
                True,
                True,
                0,
                "mov,mp4",
            )
        return MediaMetadata(18, 1280, 720, 30, "h264", "aac", True, True, 0, "mov,mp4")

    def build_command(self, source, narration, output, plan):
        return ["fake", str(source), str(narration), str(output)]

    def render(self, source, narration, output, plan):
        self.calls += 1
        if self.fail:
            raise RenderProcessError("synthetic failure", exit_code=7)
        output.write_bytes(b"synthetic-mp4")
        return RenderExecution(0, "")


def render_service(session, tmp_path, renderer):
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    return RenderService(
        CandidateRepository(session),
        ScriptRepository(session),
        NarrationRepository(session),
        RenderRepository(session),
        renderer,
        FakeFetcher(source),
        RenderStorage(str(tmp_path / "renders")),
        NarrationFileResolver(str(tmp_path / "audio")),
        duration_tolerance=0.2,
        max_file_size_bytes=1_000_000,
    )


def test_plan_requires_approved_candidate_script_narration(session, video):
    candidate = seed_research_candidate(session, video)
    with pytest.raises(ConflictError, match="approved candidate"):
        planner(session).create(candidate.id, RenderPlanRequest(script_id=1, narration_id=1))


def test_revoked_rights_block_a_new_plan(session, approved_assets):
    candidate, script, narration = approved_assets
    candidate.rights.rights_status = "RESTRICTED"
    with pytest.raises(ConflictError, match="rights"):
        planner(session).create(
            candidate.id,
            RenderPlanRequest(script_id=script.id, narration_id=narration.id),
        )


def test_edit_plan_uses_hook_alignment_visual_refs_speed_and_cache(session, approved_assets):
    candidate, script, narration = approved_assets
    service = planner(session)
    request = RenderPlanRequest(
        script_id=script.id,
        narration_id=narration.id,
        composition=CompositionStrategy.FIT,
    )
    first = service.create(candidate.id, request)
    reused = service.create(candidate.id, request)
    forced = service.create(candidate.id, request.model_copy(update={"force": True}))
    assert reused.id == first.id and forced.id != first.id
    assert first.segments[0].visual_refs == ["hook:0"]
    assert first.segments[0].output_start == 0
    assert first.segments[-1].output_end == pytest.approx(narration.duration_seconds)
    assert all(0.8 <= item.playback_speed <= 1.25 for item in first.segments)
    assert all(
        left.output_end == pytest.approx(right.output_start)
        for left, right in zip(first.segments, first.segments[1:], strict=False)
    )
    assert any("notable:0" in item.visual_refs for item in first.segments)
    assert any(item.loop_count > 1 for item in first.segments if item.visual_refs == ["loop:0"])
    assert first.composition == CompositionStrategy.FIT


def test_render_storage_validation_cache_force_failure_and_review(
    session, approved_assets, tmp_path
):
    candidate, script, narration = approved_assets
    plan = planner(session).create(
        candidate.id,
        RenderPlanRequest(
            script_id=script.id,
            narration_id=narration.id,
            composition=CompositionStrategy.FIT,
        ),
    )
    renderer = FakeRenderer()
    service = render_service(session, tmp_path, renderer)
    first = service.render(plan.id)
    assert first.render.status in (RenderStatus.VALIDATED, RenderStatus.NEEDS_REVIEW)
    assert first.render.checksum_sha256
    assert (tmp_path / "renders" / first.render.output_path).is_file()
    reused = service.render(plan.id)
    forced = service.render(plan.id, force=True)
    assert reused.reused is True and reused.render.id == first.render.id
    assert forced.reused is False and renderer.calls == 2

    approved = RenderReviewService(CandidateRepository(session), RenderRepository(session)).review(
        first.render.id,
        RenderReviewRequest(decision="APPROVED", reviewer="Video editor", notes="Checked"),
    )
    assert approved.status == RenderStatus.APPROVED

    failing = render_service(session, tmp_path, FakeRenderer(fail=True))
    failed = failing.render(plan.id, force=True).render
    assert failed.status == RenderStatus.FAILED
    assert failed.process_exit_code == 7
    assert not list((tmp_path / "renders").rglob(f"render_{failed.id}.mp4"))


def test_ffprobe_parser_command_builder_and_security(tmp_path):
    metadata = parse_probe(
        {
            "format": {"duration": "3.5", "format_name": "mov,mp4"},
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 1080,
                    "height": 1920,
                    "avg_frame_rate": "30/1",
                    "tags": {"rotate": "90"},
                },
                {"codec_type": "audio", "codec_name": "aac"},
            ],
        }
    )
    assert metadata.duration == 3.5 and metadata.rotation == 90 and metadata.has_audio
    with pytest.raises(RenderValidationError, match="duration"):
        parse_probe({"format": {"duration": "0"}, "streams": []})

    segment = SimpleNamespace(
        source_start=1,
        source_end=2,
        playback_speed=1,
        composition=CompositionStrategy.CENTER_CROP,
        loop_count=1,
        hold_seconds=0,
    )
    plan = SimpleNamespace(segments=[segment], width=1080, height=1920, fps=30, target_duration=1)
    command = FFmpegCommandBuilder("ffmpeg", crf=20, audio_bitrate="192k").build(
        tmp_path / "source.mp4", tmp_path / "narration.wav", tmp_path / "output.mp4", plan
    )
    assert command[0] == "ffmpeg" and "shell=True" not in command
    filters = command[command.index("-filter_complex") + 1]
    assert "trim=start=1.000000:end=2.000000" in filters
    assert "scale=1080:1920" in filters and "crop=1080:1920" in filters
    assert command[command.index("-map") + 1] == "[outv]"
    assert "0:a" not in command
    for strategy, expected in (
        (CompositionStrategy.FIT, "pad=1080:1920"),
        (CompositionStrategy.BLURRED_BACKGROUND, "boxblur=20:2"),
    ):
        segment.composition = strategy
        filters = FFmpegCommandBuilder("ffmpeg", crf=20, audio_bitrate="192k").build(
            tmp_path / "source.mp4",
            tmp_path / "narration.wav",
            tmp_path / "output.mp4",
            plan,
        )
        assert expected in filters[filters.index("-filter_complex") + 1]
    with pytest.raises(ValidationError):
        RenderPlanRequest(script_id=1, narration_id=1, composition="scale=1;delete=all")
    with pytest.raises(RenderConfigurationError, match="BITRATE"):
        FFmpegCommandBuilder("ffmpeg", crf=20, audio_bitrate="unsafe")
    with pytest.raises(RenderStorageError, match="relative"):
        NarrationFileResolver(str(tmp_path)).resolve(str((tmp_path / "absolute.wav").resolve()))


def test_render_validation_and_timeout(monkeypatch):
    plan = SimpleNamespace(width=1080, height=1920, fps=30)
    with pytest.raises(RenderValidationError, match="video and audio"):
        RenderService._validate_output(
            MediaMetadata(1, 1080, 1920, 30, "h264", None, True, False, 0, "mp4"), plan
        )
    with pytest.raises(RenderValidationError, match="dimensions"):
        RenderService._validate_output(
            MediaMetadata(1, 720, 1920, 30, "h264", "aac", True, True, 0, "mp4"), plan
        )

    renderer = object.__new__(FFmpegVideoRenderer)
    renderer.timeout_seconds = 1

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("ffmpeg", 1)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(RenderTimeoutError):
        renderer._run(["ffmpeg", "-version"])


def test_render_plan_api_and_cli(engine, session, video, tmp_path, monkeypatch, capsys):
    candidate, script, narration = seed_approved_assets(session, video, tmp_path)
    session.commit()

    @contextmanager
    def fake_api_runtime(settings, api_session):
        yield render_service(api_session, tmp_path, FakeRenderer())

    monkeypatch.setattr(app.api.dependencies, "configured_render_service", fake_api_runtime)
    api = create_app(Settings(_env_file=None), engine)
    with TestClient(api) as client:
        created = client.post(
            f"/candidates/{candidate.id}/render-plans",
            json={
                "script_id": script.id,
                "narration_id": narration.id,
                "composition": "FIT",
            },
        )
        assert created.status_code == 200
        plan_id = created.json()["id"]
        assert client.get(f"/render-plans/{plan_id}").status_code == 200
        assert len(client.get(f"/candidates/{candidate.id}/render-plans").json()) == 1
        rendered = client.post(f"/render-plans/{plan_id}/render", json={})
        assert rendered.status_code == 200
        assert rendered.json()["render"]["status"] in ("VALIDATED", "NEEDS_REVIEW")
        assert "/render-plans/{plan_id}/render" in client.get("/openapi.json").json()["paths"]

    database = tmp_path / "render-cli.db"
    url = f"sqlite:///{database.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    cli_engine = build_engine(url)
    with session_scope(session_factory(cli_engine)) as cli_session:
        cli_candidate, cli_script, cli_narration = seed_approved_assets(
            cli_session, video, tmp_path / "cli"
        )
        identifiers = (cli_candidate.id, cli_script.id, cli_narration.id)
    cli_engine.dispose()
    assert (
        app.cli.main(
            [
                "candidate",
                "render-plan",
                str(identifiers[0]),
                "--script",
                str(identifiers[1]),
                "--narration",
                str(identifiers[2]),
                "--composition",
                "fit",
            ]
        )
        == 0
    )
    plan_id = json.loads(capsys.readouterr().out)["id"]
    assert app.cli.main(["render-plan", "show", str(plan_id)]) == 0
    assert json.loads(capsys.readouterr().out)["id"] == plan_id

    @contextmanager
    def fake_cli_runtime(settings, cli_session):
        yield render_service(cli_session, tmp_path / "cli", FakeRenderer())

    monkeypatch.setattr(app.cli, "configured_render_service", fake_cli_runtime)
    assert app.cli.main(["render-plan", "render", str(plan_id)]) == 0
    render_id = json.loads(capsys.readouterr().out)["render"]["id"]
    assert app.cli.main(["render", "show", str(render_id)]) == 0
    assert json.loads(capsys.readouterr().out)["id"] == render_id
    assert (
        app.cli.main(
            [
                "render",
                "reject",
                str(render_id),
                "--reviewer",
                "CLI editor",
                "--notes",
                "Crop needs work",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["status"] == "REJECTED"
