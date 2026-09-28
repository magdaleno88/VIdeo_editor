import logging

from app.core.config import ScoringWeights
from app.core.database import utcnow
from app.core.errors import ConflictError
from app.models import VideoCandidate
from app.repositories.candidates import CandidateRepository, apply_score
from app.schemas.domain import (
    CandidateStatus,
    ManualScoreRequest,
    ReviewAction,
    RightsInfo,
    RightsReview,
    RightsStatus,
)
from app.services.rights.policy import approval_blockers
from app.services.scoring.scorers import ManualVideoScorer

logger = logging.getLogger(__name__)


class ReviewService:
    def __init__(self, repository: CandidateRepository, weights: ScoringWeights) -> None:
        self.repository = repository
        self.scorer = ManualVideoScorer(weights)

    @staticmethod
    def ensure_editable(candidate: VideoCandidate) -> None:
        if candidate.status in (CandidateStatus.PROCESSING, CandidateStatus.READY):
            raise ConflictError(
                "Production candidates cannot be changed through MVP review endpoints"
            )

    def approve(self, candidate_id: int, action: ReviewAction) -> VideoCandidate:
        candidate = self.repository.get(candidate_id)
        self.ensure_editable(candidate)
        blockers = approval_blockers(RightsInfo.model_validate(candidate.rights))
        if candidate.total_score is None:
            blockers.append("A score is required before approval")
        if blockers:
            raise ConflictError("Approval blocked: " + "; ".join(blockers))
        if candidate.status != CandidateStatus.APPROVED:
            self.transition(candidate, CandidateStatus.APPROVED, action)
        return candidate

    def reject(self, candidate_id: int, action: ReviewAction) -> VideoCandidate:
        candidate = self.repository.get(candidate_id)
        self.ensure_editable(candidate)
        if candidate.status != CandidateStatus.REJECTED:
            self.transition(candidate, CandidateStatus.REJECTED, action)
        return candidate

    def transition(
        self, candidate: VideoCandidate, status: CandidateStatus, action: ReviewAction
    ) -> None:
        previous = candidate.status
        candidate.status = status
        self.repository.record_event(
            candidate,
            status.value,
            action.reviewer,
            action.notes,
            {"from": previous.value, "to": status.value},
        )
        self.repository.session.flush()
        logger.info("candidate_reviewed", extra={"candidate_id": candidate.id})

    def review_rights(self, candidate_id: int, review: RightsReview) -> VideoCandidate:
        candidate = self.repository.get(candidate_id)
        self.ensure_editable(candidate)
        previous = RightsInfo.model_validate(candidate.rights).model_dump(mode="json")
        previous_status = candidate.status
        for key, value in review.model_dump(mode="json", exclude={"reviewer"}).items():
            setattr(candidate.rights, key, value)
        verified = review.rights_status == RightsStatus.VERIFIED
        candidate.rights.verification_date = utcnow() if verified else None
        candidate.rights.verified_by = review.reviewer if verified else None
        if candidate.status == CandidateStatus.APPROVED:
            candidate.status = CandidateStatus.SCORED
        # Touch the parent to include rights edits in optimistic concurrency protection.
        candidate.updated_at = utcnow()
        self.repository.record_event(
            candidate,
            "RIGHTS_REVIEWED",
            review.reviewer,
            review.notes,
            {
                "previous": previous,
                "current": RightsInfo.model_validate(candidate.rights).model_dump(mode="json"),
                "previous_status": previous_status.value,
                "current_status": candidate.status.value,
            },
        )
        self.repository.session.flush()
        return candidate

    def score(self, candidate_id: int, review: ManualScoreRequest) -> VideoCandidate:
        candidate = self.repository.get(candidate_id)
        self.ensure_editable(candidate)
        previous = candidate.score_details
        previous_status = candidate.status
        result = self.scorer.score(review.inputs)
        apply_score(candidate, result)
        evaluation = self.repository.add_evaluation(candidate, result)
        if candidate.status != CandidateStatus.REJECTED:
            candidate.status = CandidateStatus.SCORED
        self.repository.record_event(
            candidate,
            "MANUALLY_SCORED",
            review.reviewer,
            review.notes,
            {
                "previous": previous,
                "current": result.model_dump(mode="json"),
                "previous_status": previous_status.value,
                "current_status": candidate.status.value,
                "evaluation_id": evaluation.id,
            },
        )
        self.repository.session.flush()
        return candidate
