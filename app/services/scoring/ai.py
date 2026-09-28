import logging

from app.core.config import ScoringWeights
from app.core.errors import AnalysisValidationError, ConflictError
from app.models import ScoreEvaluation
from app.providers.ai.base import VideoAnalysisProvider
from app.repositories.candidates import CandidateRepository, apply_score, compare_evaluations
from app.schemas.domain import (
    AIScoreResponse,
    CandidateStatus,
    EvaluationRead,
    ScoreInputs,
    ScoreResult,
    VideoVisualAnalysis,
    VideoVisualAnalysisPayload,
)
from app.services.scoring.scorers import weighted_score
from app.services.video.assets import VideoAssetFetcher

logger = logging.getLogger(__name__)
SCORER_VERSION = "2.0"
ANALYSIS_VERSION = "1.0"


class AIVideoScorer:
    def __init__(
        self,
        repository: CandidateRepository,
        fetcher: VideoAssetFetcher,
        provider: VideoAnalysisProvider,
        weights: ScoringWeights,
    ) -> None:
        self.repository = repository
        self.fetcher = fetcher
        self.provider = provider
        self.weights = weights

    def score(self, candidate_id: int, *, force: bool = False) -> AIScoreResponse:
        candidate = self.repository.get(candidate_id)
        if candidate.status in (CandidateStatus.PROCESSING, CandidateStatus.READY):
            raise ConflictError("Production candidates cannot be rescored")
        if not force:
            existing = self.repository.reusable_ai_evaluation(
                candidate_id,
                self.provider.name,
                self.provider.model,
                self.provider.prompt_version,
                SCORER_VERSION,
            )
            if existing:
                score = self._score_from_analysis(existing)
                if (
                    existing.scorer_version != SCORER_VERSION
                    or existing.weights != self.weights.model_dump()
                ):
                    analysis = VideoVisualAnalysis.model_validate(existing.analysis)
                    existing = self.repository.add_evaluation(
                        candidate,
                        score,
                        analysis=analysis,
                        overall_confidence=self._confidence(analysis),
                    )
                self._make_current(candidate, score)
                self.repository.record_event(
                    candidate,
                    "AI_SCORE_REUSED",
                    "system",
                    "Reused an existing AI visual evaluation; no external call was made.",
                    {"evaluation_id": existing.id},
                )
                return AIScoreResponse(
                    evaluation=EvaluationRead.model_validate(existing), reused=True
                )
        try:
            with self.fetcher.fetch(candidate) as asset:
                payload = self.provider.analyze(asset)
        except Exception as exc:
            logger.warning(
                "ai_video_analysis_failed",
                extra={"candidate_id": candidate.id, "error_type": type(exc).__name__},
            )
            raise
        self._validate_duration(candidate.duration, payload)
        analysis = VideoVisualAnalysis(
            **payload.model_dump(),
            provider=self.provider.name,
            model=self.provider.model,
            analysis_version=ANALYSIS_VERSION,
            prompt_version=self.provider.prompt_version,
        )
        inputs = ScoreInputs(**{name: dimension.rating for name, dimension in analysis.dimensions})
        score = ScoreResult(
            method="ai_visual",
            version=SCORER_VERSION,
            inputs=inputs,
            weights=self.weights.model_dump(),
            total=weighted_score(inputs, self.weights),
            rationale={name: dimension.explanation for name, dimension in analysis.dimensions},
            analyzed_video=True,
        )
        confidence = self._confidence(analysis)
        evaluation = self.repository.add_evaluation(
            candidate, score, analysis=analysis, overall_confidence=confidence
        )
        self._make_current(candidate, score)
        self.repository.record_event(
            candidate,
            "AI_VISUAL_SCORED",
            "system",
            "AI analyzed temporary video content; rights were not assessed.",
            {
                "evaluation_id": evaluation.id,
                "provider": self.provider.name,
                "model": self.provider.model,
                "prompt_version": self.provider.prompt_version,
                "scorer_version": SCORER_VERSION,
            },
        )
        return AIScoreResponse(evaluation=EvaluationRead.model_validate(evaluation), reused=False)

    def compare_with_manual(self, candidate_id: int):
        ai = self.repository.latest_by_method(candidate_id, "ai_visual")
        manual = self.repository.latest_by_method(candidate_id, "manual")
        if ai is None or manual is None:
            raise ConflictError("Both AI and manual evaluations are required for comparison")
        return compare_evaluations(candidate_id, ai, manual)

    @staticmethod
    def _validate_duration(
        candidate_duration: float | None, analysis: VideoVisualAnalysisPayload
    ) -> None:
        if candidate_duration is not None and analysis.duration_analyzed > candidate_duration + 1:
            raise AnalysisValidationError("AI analysis duration exceeds the candidate duration")

    def _score_from_analysis(self, evaluation: ScoreEvaluation) -> ScoreResult:
        analysis = VideoVisualAnalysis.model_validate(evaluation.analysis)
        inputs = ScoreInputs(**{name: dimension.rating for name, dimension in analysis.dimensions})
        return ScoreResult(
            method="ai_visual",
            version=SCORER_VERSION,
            inputs=inputs,
            weights=self.weights.model_dump(),
            total=weighted_score(inputs, self.weights),
            rationale={name: dimension.explanation for name, dimension in analysis.dimensions},
            analyzed_video=True,
        )

    @staticmethod
    def _confidence(analysis: VideoVisualAnalysis) -> float:
        return round(sum(item.confidence for _, item in analysis.dimensions) / 8, 4)

    @staticmethod
    def _make_current(candidate, score: ScoreResult) -> None:
        apply_score(candidate, score)
        if candidate.status != CandidateStatus.REJECTED:
            candidate.status = CandidateStatus.SCORED
