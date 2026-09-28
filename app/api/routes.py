from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.dependencies import (
    SessionDep,
    get_ai_scorer,
    get_caption_plan,
    get_discovery,
    get_edit_plan,
    get_final_render_review,
    get_final_render_service,
    get_narration_review,
    get_narration_service,
    get_render_review,
    get_render_service,
    get_research_review,
    get_research_service,
    get_review,
    get_script_review,
    get_script_service,
)
from app.core.errors import ConfigurationError, ConflictError
from app.core.schema import check_schema
from app.repositories.candidates import CandidateRepository, compare_evaluations
from app.schemas.domain import (
    AIScoreRequest,
    AIScoreResponse,
    CandidateFilters,
    CandidatePage,
    CandidateRead,
    CaptionPlanRead,
    CaptionPlanRequest,
    DiscoveryRequest,
    DiscoveryResult,
    EvaluationRead,
    EventRead,
    FinalRenderAssetRead,
    FinalRenderRequest,
    FinalRenderResponse,
    FinalRenderReviewRequest,
    Idea,
    ManualScoreRequest,
    NarrationAssetRead,
    NarrationRequest,
    NarrationResponse,
    NarrationReviewRequest,
    PreviewRequest,
    RenderAssetRead,
    RenderPlanRequest,
    RenderRequest,
    RenderResponse,
    RenderReviewRequest,
    ResearchClaimRead,
    ResearchDossierRead,
    ResearchRequest,
    ResearchReview,
    ResearchRunResponse,
    ResearchSourceRead,
    ReviewAction,
    RightsReview,
    ScoreComparison,
    ScriptDraftRead,
    ScriptGenerationRequest,
    ScriptGenerationResponse,
    ScriptReviewRequest,
    VideoEditPlanRead,
)
from app.services.captions.service import (
    CaptionPlanService,
    FinalRenderReviewService,
    FinalRenderService,
)
from app.services.discovery.queries import TemplateQueryGenerator
from app.services.discovery.service import DiscoveryService
from app.services.narration.service import NarrationReviewService, NarrationService
from app.services.rendering.service import EditPlanService, RenderReviewService, RenderService
from app.services.research.service import ResearchReviewService, TechnicalResearchService
from app.services.review import ReviewService
from app.services.scoring.ai import AIVideoScorer
from app.services.scripting.service import ScriptGenerationService, ScriptReviewService

router = APIRouter()
ReviewDep = Annotated[ReviewService, Depends(get_review)]
AIScorerDep = Annotated[AIVideoScorer, Depends(get_ai_scorer, scope="function")]
ResearchDep = Annotated[TechnicalResearchService, Depends(get_research_service, scope="function")]
ResearchReviewDep = Annotated[ResearchReviewService, Depends(get_research_review)]
ScriptDep = Annotated[ScriptGenerationService, Depends(get_script_service, scope="function")]
ScriptReviewDep = Annotated[ScriptReviewService, Depends(get_script_review)]
NarrationDep = Annotated[NarrationService, Depends(get_narration_service, scope="function")]
NarrationReviewDep = Annotated[NarrationReviewService, Depends(get_narration_review)]
EditPlanDep = Annotated[EditPlanService, Depends(get_edit_plan)]
RenderDep = Annotated[RenderService, Depends(get_render_service, scope="function")]
RenderReviewDep = Annotated[RenderReviewService, Depends(get_render_review)]
CaptionPlanDep = Annotated[CaptionPlanService, Depends(get_caption_plan)]
FinalRenderDep = Annotated[FinalRenderService, Depends(get_final_render_service, scope="function")]
FinalRenderReviewDep = Annotated[FinalRenderReviewService, Depends(get_final_render_review)]


@router.get("/health", tags=["operations"])
def health(request: Request):
    try:
        check_schema(request.app.state.engine)
        with request.app.state.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except (SQLAlchemyError, ConfigurationError):
        return JSONResponse(
            status_code=503, content={"status": "unhealthy", "database": "unavailable"}
        )
    return {"status": "ok", "database": "ok", "version": "0.1.0"}


@router.post("/discovery/queries", response_model=list[str], tags=["discovery"])
def queries(idea: Idea) -> list[str]:
    return TemplateQueryGenerator().generate(idea)


@router.post("/discovery/search", response_model=DiscoveryResult, tags=["discovery"])
def discover(
    body: DiscoveryRequest, service: Annotated[DiscoveryService, Depends(get_discovery)]
) -> DiscoveryResult:
    return service.discover(body)


def candidate_page(session: SessionDep, filters: CandidateFilters, *, top: bool) -> CandidatePage:
    items, total = CandidateRepository(session).list(filters, top=top)
    return CandidatePage(
        items=[CandidateRead.model_validate(item) for item in items],
        total=total,
        limit=filters.limit,
        offset=filters.offset,
    )


@router.get("/candidates", response_model=CandidatePage, tags=["candidates"])
def candidates(session: SessionDep, filters: Annotated[CandidateFilters, Query()]) -> CandidatePage:
    return candidate_page(session, filters, top=False)


# Static route must precede /candidates/{candidate_id}.
@router.get("/candidates/top", response_model=CandidatePage, tags=["candidates"])
def top_candidates(
    session: SessionDep, filters: Annotated[CandidateFilters, Query()]
) -> CandidatePage:
    return candidate_page(session, filters, top=True)


@router.get("/candidates/{candidate_id}", response_model=CandidateRead, tags=["candidates"])
def candidate(candidate_id: int, session: SessionDep) -> CandidateRead:
    return CandidateRead.model_validate(CandidateRepository(session).get(candidate_id))


@router.get("/candidates/{candidate_id}/history", response_model=list[EventRead], tags=["review"])
def history(candidate_id: int, session: SessionDep) -> list[EventRead]:
    return [
        EventRead.model_validate(event)
        for event in CandidateRepository(session).events(candidate_id)
    ]


@router.post("/candidates/{candidate_id}/approve", response_model=CandidateRead, tags=["review"])
def approve(candidate_id: int, body: ReviewAction, service: ReviewDep) -> CandidateRead:
    return CandidateRead.model_validate(service.approve(candidate_id, body))


@router.post("/candidates/{candidate_id}/reject", response_model=CandidateRead, tags=["review"])
def reject(candidate_id: int, body: ReviewAction, service: ReviewDep) -> CandidateRead:
    return CandidateRead.model_validate(service.reject(candidate_id, body))


@router.put("/candidates/{candidate_id}/rights", response_model=CandidateRead, tags=["review"])
def rights(candidate_id: int, body: RightsReview, service: ReviewDep) -> CandidateRead:
    return CandidateRead.model_validate(service.review_rights(candidate_id, body))


@router.post("/candidates/{candidate_id}/score", response_model=CandidateRead, tags=["review"])
def score(candidate_id: int, body: ManualScoreRequest, service: ReviewDep) -> CandidateRead:
    return CandidateRead.model_validate(service.score(candidate_id, body))


@router.post(
    "/candidates/{candidate_id}/ai-score", response_model=AIScoreResponse, tags=["scoring"]
)
def ai_score(candidate_id: int, body: AIScoreRequest, service: AIScorerDep) -> AIScoreResponse:
    return service.score(candidate_id, force=body.force)


@router.get(
    "/candidates/{candidate_id}/evaluations",
    response_model=list[EvaluationRead],
    tags=["scoring"],
)
def evaluations(candidate_id: int, session: SessionDep) -> list[EvaluationRead]:
    return [
        EvaluationRead.model_validate(item)
        for item in CandidateRepository(session).evaluations(candidate_id)
    ]


@router.get(
    "/candidates/{candidate_id}/score-comparison",
    response_model=ScoreComparison,
    tags=["scoring"],
)
def score_comparison(candidate_id: int, session: SessionDep) -> ScoreComparison:
    repository = CandidateRepository(session)
    ai = repository.latest_by_method(candidate_id, "ai_visual")
    manual = repository.latest_by_method(candidate_id, "manual")
    if ai is None or manual is None:
        raise ConflictError("Both AI and manual evaluations are required for comparison")
    return compare_evaluations(candidate_id, ai, manual)


@router.post(
    "/candidates/{candidate_id}/research",
    response_model=ResearchRunResponse,
    tags=["research"],
)
def research(candidate_id: int, body: ResearchRequest, service: ResearchDep) -> ResearchRunResponse:
    return service.research(candidate_id, force=body.force)


@router.get(
    "/candidates/{candidate_id}/research",
    response_model=list[ResearchDossierRead],
    tags=["research"],
)
def research_history(candidate_id: int, service: ResearchReviewDep) -> list[ResearchDossierRead]:
    return service.dossiers(candidate_id)


@router.get(
    "/candidates/{candidate_id}/research/claims",
    response_model=list[ResearchClaimRead],
    tags=["research"],
)
def research_claims(candidate_id: int, service: ResearchReviewDep) -> list[ResearchClaimRead]:
    return service.latest(candidate_id).claims


@router.get(
    "/candidates/{candidate_id}/research/sources",
    response_model=list[ResearchSourceRead],
    tags=["research"],
)
def research_sources(candidate_id: int, service: ResearchReviewDep) -> list[ResearchSourceRead]:
    return service.latest(candidate_id).sources


@router.post(
    "/candidates/{candidate_id}/research/verify",
    response_model=ResearchDossierRead,
    tags=["research"],
)
def verify_research(
    candidate_id: int, body: ResearchReview, service: ResearchReviewDep
) -> ResearchDossierRead:
    return service.review(candidate_id, body)


@router.post(
    "/candidates/{candidate_id}/scripts",
    response_model=ScriptGenerationResponse,
    tags=["scripts"],
)
def generate_scripts(
    candidate_id: int, body: ScriptGenerationRequest, service: ScriptDep
) -> ScriptGenerationResponse:
    return service.generate(candidate_id, body)


@router.get(
    "/candidates/{candidate_id}/scripts",
    response_model=list[ScriptDraftRead],
    tags=["scripts"],
)
def candidate_scripts(candidate_id: int, service: ScriptReviewDep) -> list[ScriptDraftRead]:
    return service.list(candidate_id)


@router.get("/scripts/{script_id}", response_model=ScriptDraftRead, tags=["scripts"])
def script(script_id: int, service: ScriptReviewDep) -> ScriptDraftRead:
    return service.get(script_id)


@router.post("/scripts/{script_id}/approve", response_model=ScriptDraftRead, tags=["scripts"])
def approve_script(script_id: int, body: ReviewAction, service: ScriptReviewDep) -> ScriptDraftRead:
    return service.review(
        script_id,
        ScriptReviewRequest(decision="APPROVED", reviewer=body.reviewer, notes=body.notes),
    )


@router.post("/scripts/{script_id}/reject", response_model=ScriptDraftRead, tags=["scripts"])
def reject_script(script_id: int, body: ReviewAction, service: ScriptReviewDep) -> ScriptDraftRead:
    return service.review(
        script_id,
        ScriptReviewRequest(decision="REJECTED", reviewer=body.reviewer, notes=body.notes),
    )


@router.post(
    "/scripts/{script_id}/narrations",
    response_model=NarrationResponse,
    tags=["narrations"],
)
def generate_narration(
    script_id: int, body: NarrationRequest, service: NarrationDep
) -> NarrationResponse:
    return service.generate(script_id, body)


@router.get(
    "/scripts/{script_id}/narrations",
    response_model=list[NarrationAssetRead],
    tags=["narrations"],
)
def script_narrations(script_id: int, service: NarrationReviewDep) -> list[NarrationAssetRead]:
    return service.list(script_id)


@router.get("/narrations/{narration_id}", response_model=NarrationAssetRead, tags=["narrations"])
def narration(narration_id: int, service: NarrationReviewDep) -> NarrationAssetRead:
    return service.get(narration_id)


@router.post(
    "/narrations/{narration_id}/approve", response_model=NarrationAssetRead, tags=["narrations"]
)
def approve_narration(
    narration_id: int, body: ReviewAction, service: NarrationReviewDep
) -> NarrationAssetRead:
    return service.review(
        narration_id,
        NarrationReviewRequest(decision="APPROVED", reviewer=body.reviewer, notes=body.notes),
    )


@router.post(
    "/narrations/{narration_id}/reject", response_model=NarrationAssetRead, tags=["narrations"]
)
def reject_narration(
    narration_id: int, body: ReviewAction, service: NarrationReviewDep
) -> NarrationAssetRead:
    return service.review(
        narration_id,
        NarrationReviewRequest(decision="REJECTED", reviewer=body.reviewer, notes=body.notes),
    )


@router.post(
    "/candidates/{candidate_id}/render-plans",
    response_model=VideoEditPlanRead,
    tags=["rendering"],
)
def create_render_plan(
    candidate_id: int, body: RenderPlanRequest, service: EditPlanDep
) -> VideoEditPlanRead:
    return service.create(candidate_id, body)


@router.get(
    "/candidates/{candidate_id}/render-plans",
    response_model=list[VideoEditPlanRead],
    tags=["rendering"],
)
def candidate_render_plans(candidate_id: int, service: EditPlanDep) -> list[VideoEditPlanRead]:
    return service.list(candidate_id)


@router.get("/render-plans/{plan_id}", response_model=VideoEditPlanRead, tags=["rendering"])
def render_plan(plan_id: int, service: RenderReviewDep) -> VideoEditPlanRead:
    return service.get_plan(plan_id)


@router.post("/render-plans/{plan_id}/render", response_model=RenderResponse, tags=["rendering"])
def execute_render(plan_id: int, body: RenderRequest, service: RenderDep) -> RenderResponse:
    return service.render(plan_id, force=body.force)


@router.get("/renders/{render_id}", response_model=RenderAssetRead, tags=["rendering"])
def render_asset(render_id: int, service: RenderReviewDep) -> RenderAssetRead:
    return service.get(render_id)


@router.post("/renders/{render_id}/approve", response_model=RenderAssetRead, tags=["rendering"])
def approve_render(render_id: int, body: ReviewAction, service: RenderReviewDep) -> RenderAssetRead:
    return service.review(
        render_id,
        RenderReviewRequest(decision="APPROVED", reviewer=body.reviewer, notes=body.notes),
    )


@router.post("/renders/{render_id}/reject", response_model=RenderAssetRead, tags=["rendering"])
def reject_render(render_id: int, body: ReviewAction, service: RenderReviewDep) -> RenderAssetRead:
    return service.review(
        render_id,
        RenderReviewRequest(decision="REJECTED", reviewer=body.reviewer, notes=body.notes),
    )


@router.post(
    "/renders/{render_id}/caption-plans", response_model=CaptionPlanRead, tags=["captions"]
)
def create_caption_plan(
    render_id: int, body: CaptionPlanRequest, service: CaptionPlanDep
) -> CaptionPlanRead:
    return service.create(render_id, body)


@router.get(
    "/renders/{render_id}/caption-plans",
    response_model=list[CaptionPlanRead],
    tags=["captions"],
)
def render_caption_plans(render_id: int, service: CaptionPlanDep) -> list[CaptionPlanRead]:
    return service.list(render_id)


@router.get("/caption-plans/{plan_id}", response_model=CaptionPlanRead, tags=["captions"])
def caption_plan(plan_id: int, service: CaptionPlanDep) -> CaptionPlanRead:
    return service.get(plan_id)


@router.post(
    "/caption-plans/{plan_id}/render", response_model=FinalRenderResponse, tags=["captions"]
)
def execute_final_render(
    plan_id: int, body: FinalRenderRequest, service: FinalRenderDep
) -> FinalRenderResponse:
    return service.render(plan_id, force=body.force)


@router.get("/final-renders/{render_id}", response_model=FinalRenderAssetRead, tags=["captions"])
def final_render(render_id: int, service: FinalRenderReviewDep) -> FinalRenderAssetRead:
    return service.get(render_id)


@router.post(
    "/final-renders/{render_id}/preview",
    response_model=FinalRenderAssetRead,
    tags=["captions"],
)
def preview_final_render(
    render_id: int, body: PreviewRequest, service: FinalRenderDep
) -> FinalRenderAssetRead:
    return service.preview(render_id, body.time_seconds)


@router.post(
    "/final-renders/{render_id}/approve",
    response_model=FinalRenderAssetRead,
    tags=["captions"],
)
def approve_final_render(
    render_id: int, body: ReviewAction, service: FinalRenderReviewDep
) -> FinalRenderAssetRead:
    return service.review(
        render_id,
        FinalRenderReviewRequest(decision="APPROVED", reviewer=body.reviewer, notes=body.notes),
    )


@router.post(
    "/final-renders/{render_id}/reject",
    response_model=FinalRenderAssetRead,
    tags=["captions"],
)
def reject_final_render(
    render_id: int, body: ReviewAction, service: FinalRenderReviewDep
) -> FinalRenderAssetRead:
    return service.review(
        render_id,
        FinalRenderReviewRequest(decision="REJECTED", reviewer=body.reviewer, notes=body.notes),
    )
