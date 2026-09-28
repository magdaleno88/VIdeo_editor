from collections.abc import Iterator
from typing import Annotated

import httpx
from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import session_scope
from app.core.errors import ConfigurationError
from app.providers.ai import GeminiVideoAnalysisProvider
from app.providers.base import VideoSourceProvider
from app.providers.registry import configured_providers
from app.repositories.cache import SearchCacheRepository
from app.repositories.candidates import CandidateRepository
from app.repositories.captions import CaptionRepository
from app.repositories.narrations import NarrationRepository
from app.repositories.pilots import PilotRepository
from app.repositories.renders import RenderRepository
from app.repositories.research import ResearchRepository
from app.repositories.scripts import ScriptRepository
from app.schemas.domain import ProviderName
from app.services.captions.runtime import caption_plan_service, configured_final_render_service
from app.services.captions.service import (
    CaptionPlanService,
    FinalRenderReviewService,
    FinalRenderService,
)
from app.services.discovery.queries import TemplateQueryGenerator
from app.services.discovery.service import DiscoveryService
from app.services.narration.runtime import configured_narration_service
from app.services.narration.service import NarrationReviewService, NarrationService
from app.services.pilots import PilotService, QualityReviewService
from app.services.rendering.runtime import configured_render_service, edit_plan_service
from app.services.rendering.service import EditPlanService, RenderReviewService, RenderService
from app.services.research.runtime import configured_research_service
from app.services.research.service import ResearchReviewService, TechnicalResearchService
from app.services.review import ReviewService
from app.services.scoring.ai import AIVideoScorer
from app.services.scoring.scorers import HeuristicVideoScorer
from app.services.scripting.runtime import configured_script_service
from app.services.scripting.service import ScriptGenerationService, ScriptReviewService
from app.services.video.assets import VideoAssetFetcher


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_session(request: Request) -> Iterator[Session]:
    with session_scope(request.app.state.session_factory) as session:
        yield session


SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[Session, Depends(get_session, scope="function")]


def get_providers(settings: SettingsDep) -> Iterator[dict[ProviderName, VideoSourceProvider]]:
    with configured_providers(settings) as providers:
        yield providers


def get_discovery(
    session: SessionDep, settings: SettingsDep, providers: Annotated[dict, Depends(get_providers)]
) -> DiscoveryService:
    return DiscoveryService(
        CandidateRepository(session),
        SearchCacheRepository(session),
        providers,
        TemplateQueryGenerator(),
        HeuristicVideoScorer(settings.scoring_weights),
    )


def get_review(session: SessionDep, settings: SettingsDep) -> ReviewService:
    return ReviewService(CandidateRepository(session), settings.scoring_weights)


def get_ai_scorer(
    candidate_id: int, session: SessionDep, settings: SettingsDep
) -> Iterator[AIVideoScorer]:
    repository = CandidateRepository(session)
    repository.get(candidate_id)
    key = settings.gemini_api_key.get_secret_value().strip()
    model = settings.gemini_video_model.strip()
    if not key:
        raise ConfigurationError("GEMINI_API_KEY is required for AI video scoring")
    if not model:
        raise ConfigurationError("GEMINI_VIDEO_MODEL is required for AI video scoring")
    with httpx.Client(timeout=settings.http_timeout_seconds, follow_redirects=False) as client:
        provider = GeminiVideoAnalysisProvider(
            key, model, settings.ai_video_analysis_timeout_seconds
        )
        try:
            yield AIVideoScorer(
                repository,
                VideoAssetFetcher(
                    client,
                    settings.ai_video_max_file_size_mb * 1024 * 1024,
                    settings.ai_video_max_duration_seconds,
                ),
                provider,
                settings.scoring_weights,
            )
        finally:
            provider.close()


def get_research_service(
    candidate_id: int, session: SessionDep, settings: SettingsDep
) -> Iterator[TechnicalResearchService]:
    CandidateRepository(session).get(candidate_id)
    with configured_research_service(settings, session) as service:
        yield service


def get_research_review(session: SessionDep) -> ResearchReviewService:
    return ResearchReviewService(CandidateRepository(session), ResearchRepository(session))


def get_script_service(
    candidate_id: int, session: SessionDep, settings: SettingsDep
) -> Iterator[ScriptGenerationService]:
    CandidateRepository(session).get(candidate_id)
    with configured_script_service(settings, session) as service:
        yield service


def get_script_review(session: SessionDep) -> ScriptReviewService:
    return ScriptReviewService(CandidateRepository(session), ScriptRepository(session))


def get_narration_service(
    script_id: int, session: SessionDep, settings: SettingsDep
) -> Iterator[NarrationService]:
    ScriptRepository(session).get(script_id)
    with configured_narration_service(settings, session) as service:
        yield service


def get_narration_review(session: SessionDep) -> NarrationReviewService:
    return NarrationReviewService(
        CandidateRepository(session), ScriptRepository(session), NarrationRepository(session)
    )


def get_edit_plan(session: SessionDep, settings: SettingsDep) -> EditPlanService:
    return edit_plan_service(settings, session)


def get_render_service(
    plan_id: int, session: SessionDep, settings: SettingsDep
) -> Iterator[RenderService]:
    RenderRepository(session).get_plan(plan_id)
    with configured_render_service(settings, session) as service:
        yield service


def get_render_review(session: SessionDep) -> RenderReviewService:
    return RenderReviewService(CandidateRepository(session), RenderRepository(session))


def get_caption_plan(session: SessionDep, settings: SettingsDep) -> CaptionPlanService:
    return caption_plan_service(settings, session)


def get_final_render_service(
    session: SessionDep, settings: SettingsDep
) -> Iterator[FinalRenderService]:
    with configured_final_render_service(settings, session) as service:
        yield service


def get_final_render_review(session: SessionDep) -> FinalRenderReviewService:
    return FinalRenderReviewService(
        CandidateRepository(session), RenderRepository(session), CaptionRepository(session)
    )


def get_pilot_service(session: SessionDep) -> PilotService:
    return PilotService(session)


def get_quality_review(session: SessionDep) -> QualityReviewService:
    return QualityReviewService(
        CandidateRepository(session),
        RenderRepository(session),
        CaptionRepository(session),
        PilotRepository(session),
    )
