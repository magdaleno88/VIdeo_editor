from contextlib import contextmanager
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import ScoringWeights
from app.core.errors import AnalysisError, AnalysisValidationError, ConflictError
from app.models import ScoreEvaluation
from app.repositories.candidates import CandidateRepository
from app.schemas.domain import (
    CandidateStatus,
    Idea,
    ManualScoreRequest,
    NormalizedVideo,
    ScoreInputs,
    VideoVisualAnalysisPayload,
)
from app.services.review import ReviewService
from app.services.scoring.ai import AIVideoScorer
from app.services.video.assets import TemporaryVideoAsset


def analysis_payload(rating: float = 80, duration: float = 18) -> dict:
    dimensions = {}
    for name in ScoreInputs.model_fields:
        dimensions[name] = {
            "rating": rating,
            "confidence": 0.9,
            "explanation": f"Visible evidence supports {name}.",
            "evidence": [
                {
                    "timestamp_start": 1.0,
                    "timestamp_end": 2.0,
                    "frame_timestamp": 1.5,
                    "observation": f"Observed {name}.",
                    "related_dimension": name,
                    "confidence": 0.9,
                }
            ],
        }
    return {
        "summary": "A machine visibly changes a workpiece.",
        "detected_process": None,
        "detected_object": None,
        "duration_analyzed": duration,
        "dimensions": dimensions,
        "evidence": [],
        "notable_moments": [{"start": 1, "end": 2, "reason": "Visible action", "confidence": 0.9}],
        "potential_hook_segments": [
            {"start": 0, "end": 2, "reason": "Immediate motion", "confidence": 0.8}
        ],
        "potential_loop_segments": [
            {"start": 3, "end": 5, "reason": "Repeated cycle", "confidence": 0.7}
        ],
        "first_seconds": {
            "duration_observed": 3,
            "immediate_motion": "Motion starts immediately.",
            "visual_novelty": "An uncommon mechanism is visible.",
            "visible_transformation": "The first seconds begin a change.",
            "visual_clarity": "The work area is clear.",
            "context_required": "Some process context is required.",
            "confidence": 0.85,
        },
        "warnings": ["Material cannot be identified visually."],
    }


class FakeFetcher:
    def __init__(self, path: Path):
        self.path = path
        self.calls = 0
        self.cleaned = False

    @contextmanager
    def fetch(self, candidate):
        self.calls += 1
        self.path.write_bytes(b"0000ftypfixture")
        try:
            yield TemporaryVideoAsset(self.path, "video/mp4", self.path.stat().st_size)
        finally:
            self.path.unlink(missing_ok=True)
            self.cleaned = True


class FakeAnalysisProvider:
    name = "gemini"
    model = "configured-test-model"
    prompt_version = "visual-analysis-v1"

    def __init__(self, payload=None, error=None):
        self.payload = VideoVisualAnalysisPayload.model_validate(payload or analysis_payload())
        self.error = error
        self.calls = 0

    def analyze(self, asset):
        self.calls += 1
        if self.error:
            raise self.error
        return self.payload


@pytest.fixture
def ai_candidate(session, video):
    video = NormalizedVideo.model_validate(
        video.model_dump(exclude={"orientation"})
        | {"preview_url": "https://videos.pexels.com/video.mp4"}
    )
    candidate, _ = CandidateRepository(session).add_if_new(
        video, "screw", Idea(object_name="screw")
    )
    session.flush()
    return candidate


def make_scorer(session, candidate, tmp_path, provider=None):
    fetcher = FakeFetcher(tmp_path / "temporary.mp4")
    provider = provider or FakeAnalysisProvider()
    scorer = AIVideoScorer(CandidateRepository(session), fetcher, provider, ScoringWeights())
    return scorer, fetcher, provider


def test_successful_ai_analysis_persists_evidence_and_versions(session, ai_candidate, tmp_path):
    scorer, fetcher, provider = make_scorer(session, ai_candidate, tmp_path)
    response = scorer.score(ai_candidate.id)
    assert response.reused is False
    assert response.evaluation.total_score == 80
    assert response.evaluation.overall_confidence == 0.9
    assert response.evaluation.analysis.dimensions.visual_hook.evidence[0].frame_timestamp == 1.5
    assert response.evaluation.ai_model == "configured-test-model"
    assert response.evaluation.prompt_version == "visual-analysis-v1"
    assert response.evaluation.scorer_version == "2.0"
    assert ai_candidate.total_score == 80
    assert ai_candidate.status == CandidateStatus.SCORED
    assert fetcher.cleaned and not fetcher.path.exists()
    assert provider.calls == 1
    stored = session.get(ScoreEvaluation, response.evaluation.id)
    assert stored.analysis["warnings"]


def test_ai_reuse_and_force_reanalysis(session, ai_candidate, tmp_path):
    scorer, fetcher, provider = make_scorer(session, ai_candidate, tmp_path)
    first = scorer.score(ai_candidate.id)
    reused = scorer.score(ai_candidate.id)
    forced = scorer.score(ai_candidate.id, force=True)
    assert reused.reused is True
    assert reused.evaluation.id == first.evaluation.id
    assert forced.evaluation.id != first.evaluation.id
    assert provider.calls == 2
    assert fetcher.calls == 2
    assert len(CandidateRepository(session).evaluations(ai_candidate.id)) == 2


def test_changed_weights_reuse_visual_analysis_and_append_reweighted_evaluation(
    session, ai_candidate, tmp_path
):
    payload = analysis_payload(rating=80)
    payload["dimensions"]["movement"]["rating"] = 100
    payload["dimensions"]["visible_transformation"]["rating"] = 0
    provider = FakeAnalysisProvider(payload)
    scorer, fetcher, _ = make_scorer(session, ai_candidate, tmp_path, provider)
    first = scorer.score(ai_candidate.id)
    reweighted = AIVideoScorer(
        CandidateRepository(session),
        fetcher,
        provider,
        ScoringWeights(movement=30, visible_transformation=10),
    ).score(ai_candidate.id)
    assert first.evaluation.total_score == 68
    assert reweighted.reused is True
    assert reweighted.evaluation.total_score == 78
    assert reweighted.evaluation.id != first.evaluation.id
    assert reweighted.evaluation.weights["movement"] == 30
    assert provider.calls == 1
    assert fetcher.calls == 1


def test_failed_analysis_is_not_persisted_and_cleanup_runs(session, ai_candidate, tmp_path):
    provider = FakeAnalysisProvider(error=AnalysisError("model failed"))
    scorer, fetcher, _ = make_scorer(session, ai_candidate, tmp_path, provider)
    with pytest.raises(AnalysisError, match="model failed"):
        scorer.score(ai_candidate.id)
    assert CandidateRepository(session).evaluations(ai_candidate.id) == []
    assert fetcher.cleaned and not fetcher.path.exists()
    assert ai_candidate.total_score is None


def test_ai_duration_validation_does_not_save_score(session, ai_candidate, tmp_path):
    provider = FakeAnalysisProvider(analysis_payload(duration=25))
    scorer, _, _ = make_scorer(session, ai_candidate, tmp_path, provider)
    with pytest.raises(AnalysisValidationError, match="duration"):
        scorer.score(ai_candidate.id)
    assert CandidateRepository(session).evaluations(ai_candidate.id) == []


def test_manual_ai_comparison_uses_latest_evaluations(session, ai_candidate, tmp_path):
    repository = CandidateRepository(session)
    manual = ReviewService(repository, ScoringWeights())
    manual.score(
        ai_candidate.id,
        ManualScoreRequest(
            reviewer="Reviewer",
            notes="Watched entire fixture",
            inputs=ScoreInputs(**dict.fromkeys(ScoreInputs.model_fields, 70)),
        ),
    )
    scorer, _, _ = make_scorer(session, ai_candidate, tmp_path)
    scorer.score(ai_candidate.id)
    comparison = scorer.compare_with_manual(ai_candidate.id)
    assert comparison.ai_score == 80
    assert comparison.manual_score == 70
    assert comparison.total_difference == 10
    assert comparison.mean_absolute_error == 10
    assert set(comparison.differences_by_dimension) == set(ScoreInputs.model_fields)


def test_comparison_requires_both_methods(session, ai_candidate, tmp_path):
    scorer, _, _ = make_scorer(session, ai_candidate, tmp_path)
    scorer.score(ai_candidate.id)
    with pytest.raises(ConflictError, match="Both AI and manual"):
        scorer.compare_with_manual(ai_candidate.id)


def test_ai_score_never_changes_rights_and_invalidates_approval(session, ai_candidate, tmp_path):
    ai_candidate.status = CandidateStatus.APPROVED
    previous_rights = ai_candidate.rights.rights_status
    scorer, _, _ = make_scorer(session, ai_candidate, tmp_path)
    scorer.score(ai_candidate.id)
    assert ai_candidate.status == CandidateStatus.SCORED
    assert ai_candidate.rights.rights_status == previous_rights


def test_structured_schema_rejects_missing_dimensions_and_bad_evidence():
    payload = analysis_payload()
    del payload["dimensions"]["movement"]
    with pytest.raises(ValidationError):
        VideoVisualAnalysisPayload.model_validate(payload)
    payload = analysis_payload()
    payload["dimensions"]["movement"]["evidence"][0]["related_dimension"] = "visual_hook"
    with pytest.raises(ValidationError, match="mismatch"):
        VideoVisualAnalysisPayload.model_validate(payload)
    payload = analysis_payload()
    payload["potential_hook_segments"][0]["end"] = 99
    with pytest.raises(ValidationError, match="duration"):
        VideoVisualAnalysisPayload.model_validate(payload)


def test_schema_allows_no_evidence_when_a_dimension_is_not_visually_supported():
    payload = analysis_payload()
    payload["dimensions"]["visible_transformation"]["evidence"] = []
    payload["dimensions"]["visible_transformation"]["confidence"] = 0.1
    parsed = VideoVisualAnalysisPayload.model_validate(payload)
    assert parsed.dimensions.visible_transformation.evidence == []


def test_production_candidate_cannot_be_ai_scored(session, ai_candidate, tmp_path):
    ai_candidate.status = CandidateStatus.PROCESSING
    scorer, _, provider = make_scorer(session, ai_candidate, tmp_path)
    with pytest.raises(ConflictError):
        scorer.score(ai_candidate.id)
    assert provider.calls == 0
