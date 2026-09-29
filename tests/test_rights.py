import pytest
from pydantic import ValidationError

from app.core.config import ScoringWeights
from app.core.database import utcnow
from app.core.errors import ConflictError
from app.repositories.candidates import CandidateRepository, apply_score
from app.schemas.domain import (
    CandidateStatus,
    Idea,
    ReviewAction,
    RightsInfo,
    RightsReview,
    SourceLicensePreset,
)
from app.services.review import ReviewService
from app.services.rights.policy import (
    approval_blockers,
    is_cleared_for_commercial_publication,
    license_preset,
)
from app.services.scoring.scorers import HeuristicVideoScorer


def test_public_url_is_not_permission():
    rights = RightsInfo(source="https://example.org/public-video")
    assert rights.rights_status == "UNKNOWN"
    assert rights.commercial_use_allowed is None
    assert approval_blockers(rights)
    assert is_cleared_for_commercial_publication(rights) is False


@pytest.mark.parametrize(
    ("preset", "attribution", "share_alike"),
    [
        (SourceLicensePreset.CC0, False, False),
        (SourceLicensePreset.PUBLIC_DOMAIN, False, False),
        (SourceLicensePreset.CC_BY, True, False),
        (SourceLicensePreset.CC_BY_SA, True, True),
    ],
)
def test_source_license_presets_prefill_without_verifying(preset, attribution, share_alike):
    values = license_preset(preset)
    assert values.commercial_use_allowed is True
    assert values.derivative_works_allowed is True
    assert values.attribution_required is attribution
    assert values.share_alike_required is share_alike
    assert values.rights_status == "UNKNOWN"


def test_custom_license_preset_makes_no_permission_assumptions():
    values = license_preset(SourceLicensePreset.CUSTOM)
    assert values.commercial_use_allowed is None
    assert values.derivative_works_allowed is None
    assert values.rights_status == "UNKNOWN"


@pytest.mark.parametrize("status", ["UNKNOWN", "RESTRICTED", "MANUAL_REVIEW_REQUIRED"])
def test_unverified_status_always_blocks(status, rights_review):
    rights = RightsInfo(
        source="https://example.org",
        **{key: value for key, value in rights_review.items() if key != "reviewer"},
        verified_by="Reviewer",
        verification_date=utcnow(),
    )
    rights.rights_status = status
    assert approval_blockers(rights)


@pytest.mark.parametrize(
    "changes",
    [
        {"evidence_url": None},
        {"license_url": None},
        {"license_name": None},
        {"commercial_use_allowed": False},
        {"modification_allowed": None},
        {"attribution_required": None},
        {"attribution_required": True},
        {"reviewer": " "},
        {"notes": " "},
    ],
)
def test_verification_requires_explicit_evidence(changes, rights_review):
    with pytest.raises(ValidationError):
        RightsReview(**(rights_review | changes))


def test_complete_rights_review_allows_manual_approval(session, video, rights_review):
    repository = CandidateRepository(session)
    candidate, _ = repository.add_if_new(video, "screw", Idea(object_name="screw"))
    apply_score(candidate, HeuristicVideoScorer(ScoringWeights()).score(video))
    candidate.status = CandidateStatus.SCORED
    service = ReviewService(repository, ScoringWeights())
    action = ReviewAction(reviewer="Editor", notes="Approved after checking the source")
    with pytest.raises(ConflictError, match="Rights must be VERIFIED"):
        service.approve(candidate.id, action)
    service.review_rights(candidate.id, RightsReview(**rights_review))
    assert service.approve(candidate.id, action).status == CandidateStatus.APPROVED
    service.approve(candidate.id, action)
    assert [event.action for event in repository.events(candidate.id)].count("APPROVED") == 1
    service.review_rights(
        candidate.id,
        RightsReview(reviewer="Editor", notes="New rights concern", rights_status="RESTRICTED"),
    )
    assert candidate.status == CandidateStatus.SCORED
    assert candidate.rights.verification_date is None
    with pytest.raises(ConflictError):
        service.approve(candidate.id, action)


def test_future_production_states_cannot_be_changed(session, video):
    repository = CandidateRepository(session)
    candidate, _ = repository.add_if_new(video, "screw", Idea(object_name="screw"))
    candidate.status = CandidateStatus.PROCESSING
    service = ReviewService(repository, ScoringWeights())
    with pytest.raises(ConflictError):
        service.reject(candidate.id, ReviewAction(reviewer="Editor", notes="No"))
