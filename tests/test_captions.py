import json
from contextlib import contextmanager

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select

import app.api.dependencies
import app.cli
from app.core.config import Settings
from app.core.database import build_engine, session_factory, session_scope
from app.core.errors import ConflictError, RenderConfigurationError
from app.main import create_app
from app.models import ResearchClaim
from app.providers.captions import ASSSubtitleRenderer, CaptionFFmpegRenderer, SRTSubtitleRenderer
from app.providers.captions.ass import escape_ass_text, escape_srt_text
from app.providers.rendering.base import MediaMetadata
from app.repositories.candidates import CandidateRepository
from app.repositories.captions import CaptionRepository
from app.repositories.narrations import NarrationRepository
from app.repositories.renders import RenderRepository
from app.repositories.scripts import ScriptRepository
from app.schemas.domain import (
    AlignmentMethod,
    CaptionPlanRequest,
    CaptionStyleProfile,
    ClaimStatus,
    EmphasisMode,
    FinalRenderReviewRequest,
    FinalRenderStatus,
    RenderPlanRequest,
    RenderReviewRequest,
)
from app.services.captions.planning import segment_caption
from app.services.captions.service import (
    CaptionPlanService,
    FinalRenderReviewService,
    FinalRenderService,
    configured_file,
)
from app.services.captions.storage import CaptionStorage
from app.services.rendering.service import RenderReviewService
from tests.conftest import ROOT
from tests.test_rendering import FakeRenderer, planner, render_service, seed_approved_assets


class FakeCaptionRenderer:
    version = "caption-fake-1"
    ffmpeg_version = "ffmpeg-fake-1"

    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.calls = 0

    def render(self, source, subtitles, output, **kwargs):
        self.calls += 1
        if self.fail:
            from app.core.errors import RenderProcessError

            raise RenderProcessError("caption failure", exit_code=9)
        assert source.is_file() and subtitles.is_file()
        output.write_bytes(b"decorated-mp4")
        return 0

    def probe(self, path):
        return MediaMetadata(20, 1080, 1920, 30, "h264", "aac", True, True, 0, "mov,mp4")

    def preview(self, source, output, time_seconds):
        assert source.is_file() and time_seconds >= 0
        output.write_bytes(b"png")


def approved_raw(session, video, tmp_path):
    candidate, script, narration = seed_approved_assets(session, video, tmp_path)
    plan = planner(session).create(
        candidate.id, RenderPlanRequest(script_id=script.id, narration_id=narration.id)
    )
    rendered = render_service(session, tmp_path, FakeRenderer()).render(plan.id).render
    raw = RenderReviewService(CandidateRepository(session), RenderRepository(session)).review(
        rendered.id,
        RenderReviewRequest(decision="APPROVED", reviewer="Editor", notes="Raw checked"),
    )
    return candidate, script, narration, raw


def caption_service(session, **overrides):
    values = {
        "min_words": 2,
        "max_words": 7,
        "max_characters": 42,
        "max_lines": 2,
        "linger_ms": 120,
        "max_characters_per_second": 20,
        "safe_area": {"top": 0.08, "bottom": 0.18, "left": 0.08, "right": 0.16},
        "max_emphasis": 2,
        "word_highlight_enabled": True,
        "branding_enabled": False,
        "branding_channel_name": "",
    }
    values.update(overrides)
    return CaptionPlanService(
        RenderRepository(session),
        ScriptRepository(session),
        NarrationRepository(session),
        CaptionRepository(session),
        default_style="CLEAN",
        **values,
    )


def final_service(session, tmp_path, renderer=None, **overrides):
    values = {
        "font_path": "",
        "branding_enabled": False,
        "branding_path": "",
        "branding_opacity": 0.7,
        "duration_tolerance": 0.2,
        "max_file_size_bytes": 1_000_000,
    }
    values.update(overrides)
    return FinalRenderService(
        RenderRepository(session),
        CaptionRepository(session),
        renderer or FakeCaptionRenderer(),
        ASSSubtitleRenderer(),
        SRTSubtitleRenderer(),
        CaptionStorage(str(tmp_path / "captions"), str(tmp_path / "renders")),
        **values,
    )


def test_caption_plan_requires_approved_raw_render(session, video, tmp_path):
    candidate, script, narration = seed_approved_assets(session, video, tmp_path)
    plan = planner(session).create(
        candidate.id, RenderPlanRequest(script_id=script.id, narration_id=narration.id)
    )
    raw = render_service(session, tmp_path, FakeRenderer()).render(plan.id).render
    with pytest.raises(ConflictError, match="approved raw"):
        caption_service(session).create(raw.id, CaptionPlanRequest())


def test_caption_planning_uses_display_text_final_timeline_and_cache(session, video, tmp_path):
    _, script, narration, raw = approved_raw(session, video, tmp_path)
    script_model = ScriptRepository(session).get(script.id)
    script_model.beats[0].sentences[
        0
    ].display_text = "Presión 250 °C durante thread rolling, sin cortar."
    script_model.beats[0].sentences[
        0
    ].spoken_text = "Presión de doscientos cincuenta grados Celsius durante el laminado."
    narration_model = NarrationRepository(session).get(narration.id)
    for alignment in narration_model.alignments:
        alignment.method = AlignmentMethod.ESTIMATED_ALIGNMENT
    service = caption_service(session)
    request = CaptionPlanRequest(style_profile="BOLD", emphasis_mode="WORD")
    first = service.create(raw.id, request)
    reused = service.create(raw.id, request)
    forced = service.create(raw.id, request.model_copy(update={"force": True}))
    assert reused.id == first.id and forced.id != first.id
    assert first.style_profile == CaptionStyleProfile.BOLD
    assert all(0 <= item.start_seconds < item.end_seconds <= raw.duration for item in first.items)
    assert "250 °C" in " ".join(item.display_text for item in first.items)
    assert any("doscientos cincuenta" in item.spoken_text for item in first.items)
    assert all(len(item.display_text.split()) <= 7 for item in first.items)
    assert all(len(item.display_text) <= 42 for item in first.items)
    assert first.emphasis_mode == EmphasisMode.PHRASE
    assert any("fell back" in warning for warning in first.warnings)


def test_provider_alignment_enables_word_emphasis_and_pause_linger(session, video, tmp_path):
    _, _, narration, raw = approved_raw(session, video, tmp_path)
    narration_model = NarrationRepository(session).get(narration.id)
    for alignment in narration_model.alignments:
        alignment.method = AlignmentMethod.PROVIDER_ALIGNMENT
    if len(narration_model.alignments) > 1:
        narration_model.alignments[1].start_seconds = narration_model.alignments[0].end_seconds + 1
    result = caption_service(session, linger_ms=100).create(
        raw.id, CaptionPlanRequest(emphasis_mode="WORD")
    )
    assert result.emphasis_mode == EmphasisMode.WORD
    assert result.timing_method.value == "PROVIDER_ALIGNMENT"
    assert result.items[0].end_seconds <= narration_model.alignments[0].end_seconds + 0.1
    assert any("start_seconds" in span for item in result.items for span in item.emphasis_spans)


def test_segmentation_technical_terms_units_speed_styles_safe_area_and_hook(
    session, video, tmp_path
):
    assert any("250 °C" in item for item in segment_caption("Acero a 250 °C cambia", 2, 4, 20))
    assert (
        "thread rolling"
        in " ".join(segment_caption("Modern thread rolling forms steel", 2, 4, 24)).lower()
    )
    _, _, _, raw = approved_raw(session, video, tmp_path)
    result = caption_service(session, max_characters_per_second=0.5).create(
        raw.id, CaptionPlanRequest(style_profile="MINIMAL")
    )
    assert result.safe_area["right"] > result.safe_area["left"]
    assert result.items[0].style["max_lines"] == 2
    assert any(item.warnings for item in result.items)
    hook = next(item for item in result.overlays if item.overlay_type.value == "HOOK_TEXT")
    assert hook.start_seconds == 0 and hook.end_seconds <= 3


def test_factual_overlay_requires_verified_claim(session, video, tmp_path):
    _, script, _, raw = approved_raw(session, video, tmp_path)
    claims = list(
        session.scalars(
            select(ResearchClaim).where(ResearchClaim.dossier_id == script.research_dossier_id)
        )
    )
    verified = next(claim for claim in claims if claim.status == ClaimStatus.VERIFIED)
    result = caption_service(session).create(
        raw.id, CaptionPlanRequest(factual_claim_ids=[verified.id])
    )
    overlay = next(item for item in result.overlays if item.overlay_type.value == "INFO_LABEL")
    assert overlay.claim_ids == [verified.id]
    verified.status = ClaimStatus.PARTIALLY_SUPPORTED
    with pytest.raises(ConflictError, match="VERIFIED"):
        caption_service(session).create(
            raw.id, CaptionPlanRequest(factual_claim_ids=[verified.id], force=True)
        )
    verified.status = ClaimStatus.CONTRADICTED
    with pytest.raises(ConflictError, match="VERIFIED"):
        caption_service(session).create(
            raw.id, CaptionPlanRequest(factual_claim_ids=[verified.id], force=True)
        )
    verified.status = ClaimStatus.UNVERIFIED
    with pytest.raises(ConflictError, match="VERIFIED"):
        caption_service(session).create(
            raw.id, CaptionPlanRequest(factual_claim_ids=[verified.id], force=True)
        )


def test_ass_srt_unicode_and_injection_are_escaped(session, video, tmp_path):
    _, script, _, raw = approved_raw(session, video, tmp_path)
    script_model = ScriptRepository(session).get(script.id)
    script_model.beats[0].sentences[0].display_text = "¿Máquina; presión 250 °C? {\\pos(1,1)}\n¡Sí!"
    plan_read = caption_service(session).create(raw.id, CaptionPlanRequest())
    plan = CaptionRepository(session).get_plan(plan_read.id)
    ass = ASSSubtitleRenderer().render(plan, 1080, 1920)
    srt = SRTSubtitleRenderer().render(plan)
    assert "¿" in ass and "250 °C" in ass and r"\{" in ass
    assert "{\\pos(1,1)}" not in ass
    assert "¿" in srt and "-->" in srt
    assert "\n999\n00:00" not in srt
    assert escape_ass_text("{x}\\y") == r"\{x\}\\y"
    assert escape_srt_text("a --> b\nc") == "a — b c"


def test_font_and_branding_paths_are_lazy_and_safe(tmp_path):
    assert configured_file("", {".ttf"}, "Font") is None
    with pytest.raises(RenderConfigurationError, match="traversal"):
        configured_file("../font.ttf", {".ttf"}, "Font")
    with pytest.raises(RenderConfigurationError, match="traversal"):
        configured_file("../logo.png", {".png"}, "Branding asset")
    with pytest.raises(RenderConfigurationError, match="missing"):
        configured_file(str(tmp_path / "missing.ttf"), {".ttf"}, "Font")


def test_ffmpeg_caption_command_is_argument_safe(tmp_path):
    executor = type(
        "Executor",
        (),
        {"ffmpeg": "ffmpeg", "ffprobe": "ffprobe", "version": "v"},
    )()
    renderer = CaptionFFmpegRenderer(executor)
    command = renderer.build_command(
        tmp_path / "input;safe.mp4",
        tmp_path / "caption's [safe].ass",
        tmp_path / "output.mp4",
    )
    assert command[0] == "ffmpeg" and "shell=True" not in command
    assert "subtitles=filename=" in command[command.index("-vf") + 1]
    assert str(tmp_path / "input;safe.mp4") in command


def test_final_render_storage_cache_preview_and_review(session, video, tmp_path):
    candidate, _, _, raw = approved_raw(session, video, tmp_path)
    plan = caption_service(session).create(raw.id, CaptionPlanRequest())
    renderer = FakeCaptionRenderer()
    service = final_service(session, tmp_path, renderer)
    first = service.render(plan.id)
    reused = service.render(plan.id)
    forced = service.render(plan.id, force=True)
    assert first.render.status in (FinalRenderStatus.VALIDATED, FinalRenderStatus.NEEDS_REVIEW)
    assert first.render.checksum_sha256 and first.render.ass_path and first.render.srt_path
    assert reused.reused is True and forced.render.id != first.render.id
    previewed = service.preview(first.render.id, 1.5)
    assert previewed.preview_path and (tmp_path / "renders" / previewed.preview_path).is_file()
    reviewed = FinalRenderReviewService(
        CandidateRepository(session), RenderRepository(session), CaptionRepository(session)
    ).review(
        first.render.id,
        FinalRenderReviewRequest(decision="APPROVED", reviewer="Editor", notes="Checked"),
    )
    assert reviewed.status == FinalRenderStatus.APPROVED
    assert CandidateRepository(session).get(candidate.id)


def test_caption_api_and_migration(engine, session, video, tmp_path, monkeypatch):
    _, _, _, raw = approved_raw(session, video, tmp_path)
    session.commit()

    @contextmanager
    def fake_runtime(settings, api_session):
        yield final_service(api_session, tmp_path)

    monkeypatch.setattr(app.api.dependencies, "configured_final_render_service", fake_runtime)
    api = create_app(Settings(_env_file=None), engine)
    with TestClient(api) as client:
        created = client.post(f"/renders/{raw.id}/caption-plans", json={})
        assert created.status_code == 200
        plan_id = created.json()["id"]
        assert client.get(f"/caption-plans/{plan_id}").status_code == 200
        rendered = client.post(f"/caption-plans/{plan_id}/render", json={})
        assert rendered.status_code == 200
        final_id = rendered.json()["render"]["id"]
        assert client.get(f"/final-renders/{final_id}").status_code == 200
        assert (
            client.post(
                f"/final-renders/{final_id}/reject",
                json={"reviewer": "API", "notes": "Move caption"},
            ).status_code
            == 200
        )
    tables = set(inspect(engine).get_table_names())
    assert {
        "caption_plans",
        "caption_items",
        "graphic_overlays",
        "final_render_assets",
        "final_render_reviews",
    } <= tables


def test_caption_cli(session, video, tmp_path, monkeypatch, capsys):
    database = tmp_path / "caption-cli.db"
    url = f"sqlite:///{database.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    cli_engine = build_engine(url)
    with session_scope(session_factory(cli_engine)) as cli_session:
        _, _, _, raw = approved_raw(cli_session, video, tmp_path / "cli")
        raw_id = raw.id
    cli_engine.dispose()
    assert app.cli.main(["render", "caption-plan", str(raw_id), "--style", "bold"]) == 0
    plan_id = json.loads(capsys.readouterr().out)["id"]
    assert app.cli.main(["caption-plan", "show", str(plan_id)]) == 0
    assert json.loads(capsys.readouterr().out)["id"] == plan_id

    @contextmanager
    def fake_runtime(settings, cli_session):
        yield final_service(cli_session, tmp_path / "cli")

    monkeypatch.setattr(app.cli, "configured_final_render_service", fake_runtime)
    assert app.cli.main(["caption-plan", "render", str(plan_id)]) == 0
    final_id = json.loads(capsys.readouterr().out)["render"]["id"]
    assert app.cli.main(["final-render", "show", str(final_id)]) == 0
    assert json.loads(capsys.readouterr().out)["id"] == final_id
