from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.models import CandidateEvent, RightsRecord, ScoreEvaluation, VideoCandidate
from app.schemas.domain import (
    CandidateFilters,
    EvaluationRead,
    Idea,
    NormalizedVideo,
    ScoreComparison,
    ScoreDimension,
    ScoreResult,
    VideoVisualAnalysis,
)


class CandidateRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, candidate_id: int) -> VideoCandidate:
        candidate = self.session.get(VideoCandidate, candidate_id)
        if candidate is None:
            raise NotFoundError(f"Candidate {candidate_id} was not found")
        return candidate

    def find_identity(self, provider: str, video_id: str) -> VideoCandidate | None:
        return self.session.scalar(
            select(VideoCandidate).where(
                VideoCandidate.provider == provider, VideoCandidate.provider_video_id == video_id
            )
        )

    def add_if_new(
        self, video: NormalizedVideo, query: str, idea: Idea
    ) -> tuple[VideoCandidate, bool]:
        existing = self.find_identity(video.provider, video.provider_video_id)
        if existing:
            return existing, False
        data = video.model_dump(mode="json", exclude={"rights"})
        candidate = VideoCandidate(
            **data,
            search_query=query,
            category=idea.category,
            industrial_process=idea.process_name,
            object_being_manufactured=idea.object_name,
            rights=RightsRecord(
                **video.rights.model_dump(
                    mode="python", exclude={"source", "license_url", "evidence_url"}
                ),
                source=str(video.rights.source),
                license_url=str(video.rights.license_url) if video.rights.license_url else None,
                evidence_url=str(video.rights.evidence_url) if video.rights.evidence_url else None,
            ),
        )
        try:
            # Savepoint protects the surrounding batch against a concurrent duplicate insert.
            with self.session.begin_nested():
                self.session.add(candidate)
                self.session.flush()
        except IntegrityError:
            existing = self.find_identity(video.provider, video.provider_video_id)
            if existing is None:
                raise
            return existing, False
        return candidate, True

    def list(
        self, filters: CandidateFilters, *, top: bool = False
    ) -> tuple[list[VideoCandidate], int]:
        clauses = []
        for field in ("provider", "status", "category", "industrial_process"):
            value = getattr(filters, field)
            if value is not None:
                clauses.append(getattr(VideoCandidate, field) == value)
        if filters.minimum_score is not None:
            clauses.append(VideoCandidate.total_score >= filters.minimum_score)
        if top:
            clauses.append(VideoCandidate.total_score.is_not(None))
        total = (
            self.session.scalar(select(func.count()).select_from(VideoCandidate).where(*clauses))
            or 0
        )
        order = (
            [VideoCandidate.total_score.desc(), VideoCandidate.id.desc()]
            if top
            else [VideoCandidate.id.desc()]
        )
        items = self.session.scalars(
            select(VideoCandidate)
            .where(*clauses)
            .order_by(*order)
            .limit(filters.limit)
            .offset(filters.offset)
        ).all()
        return list(items), total

    def record_event(
        self,
        candidate: VideoCandidate,
        action: str,
        reviewer: str,
        notes: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.session.add(
            CandidateEvent(
                candidate_id=candidate.id,
                action=action,
                reviewer=reviewer,
                notes=notes,
                details=details or {},
            )
        )

    def events(self, candidate_id: int) -> list[CandidateEvent]:
        self.get(candidate_id)
        return list(
            self.session.scalars(
                select(CandidateEvent)
                .where(CandidateEvent.candidate_id == candidate_id)
                .order_by(CandidateEvent.id)
            )
        )

    def add_evaluation(
        self,
        candidate: VideoCandidate,
        score: ScoreResult,
        *,
        analysis: VideoVisualAnalysis | None = None,
        overall_confidence: float | None = None,
    ) -> ScoreEvaluation:
        evaluation = ScoreEvaluation(
            candidate_id=candidate.id,
            method=score.method,
            scorer_version=score.version,
            ai_provider=analysis.provider if analysis else None,
            ai_model=analysis.model if analysis else None,
            prompt_version=analysis.prompt_version if analysis else None,
            weights=score.weights,
            ratings=score.inputs.model_dump(mode="json"),
            total_score=score.total,
            overall_confidence=overall_confidence,
            analysis=analysis.model_dump(mode="json") if analysis else None,
        )
        self.session.add(evaluation)
        self.session.flush()
        return evaluation

    def evaluations(self, candidate_id: int) -> list[ScoreEvaluation]:
        self.get(candidate_id)
        return list(
            self.session.scalars(
                select(ScoreEvaluation)
                .where(ScoreEvaluation.candidate_id == candidate_id)
                .order_by(ScoreEvaluation.id)
            )
        )

    def reusable_ai_evaluation(
        self,
        candidate_id: int,
        provider: str,
        model: str,
        prompt_version: str,
        scorer_version: str,
    ) -> ScoreEvaluation | None:
        return self.session.scalar(
            select(ScoreEvaluation)
            .where(
                ScoreEvaluation.candidate_id == candidate_id,
                ScoreEvaluation.method == "ai_visual",
                ScoreEvaluation.ai_provider == provider,
                ScoreEvaluation.ai_model == model,
                ScoreEvaluation.prompt_version == prompt_version,
                ScoreEvaluation.scorer_version == scorer_version,
            )
            .order_by(ScoreEvaluation.id.desc())
            .limit(1)
        )

    def latest_by_method(self, candidate_id: int, method: str) -> ScoreEvaluation | None:
        self.get(candidate_id)
        return self.session.scalar(
            select(ScoreEvaluation)
            .where(ScoreEvaluation.candidate_id == candidate_id, ScoreEvaluation.method == method)
            .order_by(ScoreEvaluation.id.desc())
            .limit(1)
        )


def evaluation_read(evaluation: ScoreEvaluation) -> EvaluationRead:
    return EvaluationRead.model_validate(evaluation)


def compare_evaluations(
    candidate_id: int, ai: ScoreEvaluation, manual: ScoreEvaluation
) -> ScoreComparison:
    differences = {
        ScoreDimension(name): round(ai.ratings[name] - manual.ratings[name], 2)
        for name in ai.ratings
    }
    return ScoreComparison(
        candidate_id=candidate_id,
        ai_evaluation_id=ai.id,
        manual_evaluation_id=manual.id,
        ai_score=ai.total_score,
        manual_score=manual.total_score,
        total_difference=round(ai.total_score - manual.total_score, 2),
        differences_by_dimension=differences,
        mean_absolute_error=round(sum(abs(value) for value in differences.values()) / 8, 2),
    )


def apply_score(candidate: VideoCandidate, score: ScoreResult) -> None:
    candidate.score_details = score.model_dump(mode="json")
    candidate.total_score = score.total
    candidate.visual_score = round(
        (
            score.inputs.movement
            + score.inputs.satisfying_result
            + score.inputs.understandable_without_audio
        )
        / 3,
        2,
    )
    candidate.transformation_score = score.inputs.visible_transformation
    candidate.machine_score = score.inputs.unusual_machinery
    candidate.hook_score = score.inputs.visual_hook
    candidate.loop_score = score.inputs.loop_potential
    candidate.educational_score = score.inputs.educational_potential
