from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.api.dependencies import (
    SessionDep,
    get_ai_scorer,
    get_final_render_service,
    get_narration_service,
    get_render_service,
    get_research_service,
    get_script_service,
)
from app.core.config import Settings
from app.core.errors import ApplicationError, NotFoundError
from app.models import (
    FinalRenderAsset,
    NarrationAsset,
    RenderAsset,
    ResearchDossier,
)
from app.providers.registry import configured_providers
from app.repositories.cache import SearchCacheRepository
from app.repositories.candidates import CandidateRepository
from app.repositories.captions import CaptionRepository
from app.repositories.narrations import NarrationRepository
from app.repositories.pilots import PilotRepository
from app.repositories.renders import RenderRepository
from app.repositories.research import ResearchRepository
from app.repositories.scripts import ScriptRepository
from app.schemas.domain import (
    CaptionPlanRequest,
    DiscoveryRequest,
    FinalRenderQualityReviewRequest,
    FinalRenderReviewRequest,
    NarrationRequest,
    NarrationReviewRequest,
    PilotBatchCreate,
    QualityChecklist,
    QualityRejectionCategory,
    RenderPlanRequest,
    RenderReviewRequest,
    ResearchReview,
    ReviewAction,
    RightsReview,
    RightsStatus,
    ScriptGenerationRequest,
    ScriptReviewRequest,
)
from app.services.captions.runtime import caption_plan_service
from app.services.captions.service import FinalRenderReviewService
from app.services.discovery.queries import TemplateQueryGenerator
from app.services.discovery.service import DiscoveryService
from app.services.narration.service import NarrationReviewService
from app.services.pilots import PilotService, QualityReviewService
from app.services.rendering.runtime import edit_plan_service
from app.services.rendering.service import RenderReviewService
from app.services.research.service import ResearchReviewService
from app.services.review import ReviewService
from app.services.scoring.scorers import HeuristicVideoScorer
from app.services.scripting.service import ScriptReviewService
from app.web.security import form_data, resolve_persisted_file, safe_redirect, verify_csrf
from app.web.service import DashboardService

WEB_ROOT = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(WEB_ROOT / "templates"))
router = APIRouter(include_in_schema=False)


def _context(request: Request, **values):
    return {
        "request": request,
        "csrf_token": request.app.state.dashboard_csrf_token,
        "default_reviewer": request.app.state.settings.dashboard_default_reviewer,
        **values,
    }


def _redirect(target: str, *, notice: str | None = None, error: str | None = None):
    query = urlencode(
        {key: value for key, value in {"notice": notice, "error": error}.items() if value}
    )
    separator = "&" if "?" in target else "?"
    return RedirectResponse(target + (separator + query if query else ""), status_code=303)


@router.get("/", response_class=HTMLResponse, name="dashboard_home")
def home(request: Request, session: SessionDep):
    return templates.TemplateResponse(
        request=request,
        name="home.html",
        context=_context(request, **DashboardService(session).home()),
    )


@router.get("/dashboard/pilots", response_class=HTMLResponse, name="dashboard_pilots")
def pilots(
    request: Request,
    session: SessionDep,
    page: Annotated[int, Query(ge=1)] = 1,
):
    return templates.TemplateResponse(
        request=request,
        name="pilots.html",
        context=_context(request, result=PilotService(session).list_batches(page, 20)),
    )


@router.post("/dashboard/pilots", name="dashboard_create_pilot")
async def create_pilot(request: Request, session: SessionDep):
    values = await form_data(request)
    verify_csrf(request, values)
    try:
        candidate_ids = [
            int(value.strip())
            for value in values.get("candidate_ids", "").split(",")
            if value.strip()
        ]
        batch = PilotService(session).create_batch(
            PilotBatchCreate(
                name=values.get("name", ""),
                slug=values.get("slug", ""),
                description=values.get("description", ""),
                candidate_ids=candidate_ids,
            )
        )
    except (ApplicationError, ValueError) as exc:
        return _redirect("/dashboard/pilots", error=str(exc))
    return _redirect(f"/dashboard/pilots/{batch.id}", notice="Pilot batch created")


@router.get(
    "/dashboard/pilots/{batch_id}",
    response_class=HTMLResponse,
    name="dashboard_pilot_batch",
)
def pilot_batch(request: Request, batch_id: int, session: SessionDep):
    return templates.TemplateResponse(
        request=request,
        name="pilot_batch.html",
        context=_context(request, **PilotService(session).batch_summary(batch_id)),
    )


@router.post("/dashboard/pilots/{batch_id}/candidates", name="dashboard_add_pilot_candidate")
async def add_pilot_candidate(request: Request, batch_id: int, session: SessionDep):
    values = await form_data(request)
    verify_csrf(request, values)
    try:
        PilotService(session).add_candidate(batch_id, int(values.get("candidate_id", "")))
    except (ApplicationError, ValueError) as exc:
        return _redirect(f"/dashboard/pilots/{batch_id}", error=str(exc))
    return _redirect(f"/dashboard/pilots/{batch_id}", notice="Candidate added")


@router.get(
    "/dashboard/pilots/{batch_id}/candidates/{candidate_id}",
    response_class=HTMLResponse,
    name="dashboard_pilot_detail",
)
def pilot_detail(request: Request, batch_id: int, candidate_id: int, session: SessionDep):
    progress = PilotService(session).progress(batch_id, candidate_id)
    return templates.TemplateResponse(
        request=request,
        name="pilot_detail.html",
        context=_context(request, progress=progress),
    )


@router.get("/dashboard/candidates", response_class=HTMLResponse, name="dashboard_candidates")
def candidates(
    request: Request,
    session: SessionDep,
    provider: str | None = None,
    status: str | None = None,
    minimum_score: float | None = None,
    category: str | None = None,
    industrial_process: str | None = None,
    rights_status: str | None = None,
    sort: str = "newest",
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=5, le=100)] = 20,
):
    result = DashboardService(session).candidates(
        provider=provider,
        status=status,
        minimum_score=minimum_score,
        category=category,
        industrial_process=industrial_process,
        rights_status=rights_status,
        sort=sort,
        page=page,
        per_page=per_page,
    )
    filters = {
        "provider": provider or "",
        "status": status or "",
        "minimum_score": minimum_score if minimum_score is not None else "",
        "category": category or "",
        "industrial_process": industrial_process or "",
        "rights_status": rights_status or "",
        "sort": sort,
        "per_page": per_page,
    }
    return templates.TemplateResponse(
        request=request,
        name="candidates.html",
        context=_context(request, result=result, filters=filters),
    )


@router.get(
    "/dashboard/candidates/{candidate_id}",
    response_class=HTMLResponse,
    name="dashboard_candidate_detail",
)
def candidate_detail(request: Request, candidate_id: int, session: SessionDep):
    detail = DashboardService(session).candidate_detail(candidate_id)
    return templates.TemplateResponse(
        request=request,
        name="candidate_detail.html",
        context=_context(request, **detail),
    )


@router.get("/dashboard/review-queue", response_class=HTMLResponse, name="review_queue")
def review_queue(
    request: Request,
    session: SessionDep,
    stage: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=5, le=50)] = 15,
):
    data = DashboardService(session).review_queue(stage, page, per_page)
    return templates.TemplateResponse(
        request=request,
        name="review_queue.html",
        context=_context(request, **data),
    )


@router.get(
    "/dashboard/final-renders/{render_id}",
    response_class=HTMLResponse,
    name="dashboard_final_render",
)
def final_render_detail(request: Request, render_id: int, session: SessionDep):
    detail = DashboardService(session).final_render_detail(render_id)
    return templates.TemplateResponse(
        request=request,
        name="final_render.html",
        context=_context(request, **detail),
    )


@router.get("/dashboard/failures", response_class=HTMLResponse, name="dashboard_failures")
def failures(request: Request, session: SessionDep):
    return templates.TemplateResponse(
        request=request,
        name="failures.html",
        context=_context(request, failures=DashboardService(session).failures()),
    )


@router.get("/media/audio/{asset_id}", name="media_audio")
def media_audio(asset_id: int, session: SessionDep, request: Request):
    asset = session.get(NarrationAsset, asset_id)
    if asset is None:
        raise NotFoundError(f"Narration {asset_id} was not found")
    path = resolve_persisted_file(
        request.app.state.settings.narration_storage_root, asset.storage_path
    )
    media_type = {
        "mp3": "audio/mpeg",
        "mpeg": "audio/mpeg",
        "wav": "audio/wav",
        "ogg": "audio/ogg",
        "m4a": "audio/mp4",
        "aac": "audio/aac",
    }.get(asset.audio_format.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media_type, headers={"Accept-Ranges": "bytes"})


@router.get("/media/render/{asset_id}", name="media_render")
def media_render(asset_id: int, session: SessionDep, request: Request):
    asset = session.get(RenderAsset, asset_id)
    if asset is None:
        raise NotFoundError(f"Render {asset_id} was not found")
    path = resolve_persisted_file(request.app.state.settings.render_storage_root, asset.output_path)
    return FileResponse(path, media_type="video/mp4", headers={"Accept-Ranges": "bytes"})


@router.get("/media/final-render/{asset_id}", name="media_final_render")
def media_final_render(asset_id: int, session: SessionDep, request: Request):
    asset = session.get(FinalRenderAsset, asset_id)
    if asset is None:
        raise NotFoundError(f"Final render {asset_id} was not found")
    path = resolve_persisted_file(request.app.state.settings.render_storage_root, asset.output_path)
    return FileResponse(path, media_type="video/mp4", headers={"Accept-Ranges": "bytes"})


@router.get("/media/preview/{asset_id}", name="media_preview")
def media_preview(asset_id: int, session: SessionDep, request: Request):
    asset = session.get(FinalRenderAsset, asset_id)
    if asset is None:
        raise NotFoundError(f"Final render {asset_id} was not found")
    path = resolve_persisted_file(
        request.app.state.settings.render_storage_root, asset.preview_path
    )
    media_type = "image/jpeg" if path.suffix.lower() in (".jpg", ".jpeg") else "image/png"
    return FileResponse(path, media_type=media_type)


@router.get("/downloads/final-render/{asset_id}.mp4", name="download_final_render")
def download_final_render(asset_id: int, session: SessionDep, request: Request):
    asset = session.get(FinalRenderAsset, asset_id)
    if asset is None:
        raise NotFoundError(f"Final render {asset_id} was not found")
    path = resolve_persisted_file(request.app.state.settings.render_storage_root, asset.output_path)
    return FileResponse(path, media_type="video/mp4", filename=f"final-render-{asset.id}.mp4")


@router.get("/downloads/final-render/{asset_id}.srt", name="download_final_srt")
def download_final_srt(asset_id: int, session: SessionDep, request: Request):
    asset = session.get(FinalRenderAsset, asset_id)
    if asset is None:
        raise NotFoundError(f"Final render {asset_id} was not found")
    path = resolve_persisted_file(request.app.state.settings.caption_storage_root, asset.srt_path)
    return FileResponse(
        path,
        media_type="application/x-subrip",
        filename=f"final-render-{asset.id}.srt",
    )


@router.post(
    "/dashboard/final-renders/{render_id}/quality-review",
    name="dashboard_quality_review",
)
async def submit_quality_review(request: Request, render_id: int, session: SessionDep):
    values = await form_data(request)
    verify_csrf(request, values)
    target = safe_redirect(values.get("return_to"), f"/dashboard/final-renders/{render_id}")
    try:
        checklist = QualityChecklist(
            **{field: values.get(field) == "true" for field in QualityChecklist.model_fields}
        )
        categories = [
            category
            for category in QualityRejectionCategory
            if values.get(f"category_{category.value}") == "true"
        ]
        payload = FinalRenderQualityReviewRequest(
            reviewer=values.get("reviewer", "").strip()
            or request.app.state.settings.dashboard_default_reviewer,
            decision=values.get("decision", ""),
            visual_relevance=int(values.get("visual_relevance", "")),
            pacing=int(values.get("pacing", "")),
            crop_quality=int(values.get("crop_quality", "")),
            narration_quality=int(values.get("narration_quality", "")),
            caption_readability=int(values.get("caption_readability", "")),
            hook_strength=int(values.get("hook_strength", "")),
            audio_sync=int(values.get("audio_sync", "")),
            overall_readiness=int(values.get("overall_readiness", "")),
            checklist=checklist,
            rejection_categories=categories,
            notes=values.get("notes", "").strip(),
        )
        QualityReviewService(
            CandidateRepository(session),
            RenderRepository(session),
            CaptionRepository(session),
            PilotRepository(session),
        ).submit(render_id, payload)
    except (ApplicationError, ValueError) as exc:
        return _redirect(target, error=str(exc))
    return _redirect(target, notice="Structured quality review recorded")


@router.post("/dashboard/review/{kind}/{resource_id}/{decision}", name="dashboard_review")
async def review_resource(
    request: Request,
    kind: str,
    resource_id: int,
    decision: str,
    session: SessionDep,
):
    values = await form_data(request)
    verify_csrf(request, values)
    target = safe_redirect(values.get("return_to"), f"/dashboard/candidates/{resource_id}")
    reviewer = values.get("reviewer", "").strip() or (
        request.app.state.settings.dashboard_default_reviewer
    )
    notes = values.get("notes", "").strip()
    normalized_decision = decision.upper()
    if normalized_decision not in ("APPROVE", "REJECT"):
        raise NotFoundError("Unknown review decision")
    approved = normalized_decision == "APPROVE"
    if not approved and not notes:
        return _redirect(target, error="A rejection reason is required")
    recorded_notes = notes or "Approved in local dashboard"
    try:
        candidates = CandidateRepository(session)
        if kind == "candidate":
            service = ReviewService(candidates, request.app.state.settings.scoring_weights)
            action = ReviewAction(reviewer=reviewer, notes=recorded_notes)
            if approved:
                service.approve(resource_id, action)
            else:
                service.reject(resource_id, action)
        elif kind == "rights":
            candidate = candidates.get(resource_id)
            current = candidate.rights
            status = RightsStatus.VERIFIED if approved else RightsStatus.RESTRICTED
            ReviewService(candidates, request.app.state.settings.scoring_weights).review_rights(
                resource_id,
                RightsReview(
                    reviewer=reviewer,
                    notes=recorded_notes,
                    rights_status=status,
                    license_name=values.get("license_name") or current.license_name,
                    license_url=values.get("license_url") or current.license_url,
                    evidence_url=values.get("evidence_url") or current.evidence_url,
                    commercial_use_allowed=values.get("commercial_use_allowed") == "true",
                    modification_allowed=values.get("modification_allowed") == "true",
                    attribution_required=values.get("attribution_required") == "true",
                    attribution_text=values.get("attribution_text") or current.attribution_text,
                ),
            )
        elif kind == "research":
            dossier = session.get(ResearchDossier, resource_id)
            if dossier is None:
                raise NotFoundError(f"Research dossier {resource_id} was not found")
            ResearchReviewService(candidates, ResearchRepository(session)).review(
                dossier.candidate_id,
                ResearchReview(
                    decision="VERIFIED" if approved else "REJECTED",
                    reviewer=reviewer,
                    notes=recorded_notes,
                ),
            )
        elif kind == "script":
            ScriptReviewService(candidates, ScriptRepository(session)).review(
                resource_id,
                ScriptReviewRequest(
                    decision="APPROVED" if approved else "REJECTED",
                    reviewer=reviewer,
                    notes=recorded_notes,
                ),
            )
        elif kind == "narration":
            NarrationReviewService(
                candidates, ScriptRepository(session), NarrationRepository(session)
            ).review(
                resource_id,
                NarrationReviewRequest(
                    decision="APPROVED" if approved else "REJECTED",
                    reviewer=reviewer,
                    notes=recorded_notes,
                ),
            )
        elif kind == "render":
            RenderReviewService(candidates, RenderRepository(session)).review(
                resource_id,
                RenderReviewRequest(
                    decision="APPROVED" if approved else "REJECTED",
                    reviewer=reviewer,
                    notes=recorded_notes,
                ),
            )
        elif kind == "final-render":
            FinalRenderReviewService(
                candidates, RenderRepository(session), CaptionRepository(session)
            ).review(
                resource_id,
                FinalRenderReviewRequest(
                    decision="APPROVED" if approved else "REJECTED",
                    reviewer=reviewer,
                    notes=recorded_notes,
                ),
            )
        else:
            raise NotFoundError("Unknown review resource")
    except ApplicationError as exc:
        return _redirect(target, error=str(exc))
    return _redirect(target, notice=f"{kind.replace('-', ' ').title()} review recorded")


@router.post("/dashboard/actions/{action}/{resource_id}", name="dashboard_action")
async def run_action(
    request: Request,
    action: str,
    resource_id: int,
    session: SessionDep,
):
    values = await form_data(request)
    verify_csrf(request, values)
    target = safe_redirect(values.get("return_to"), f"/dashboard/candidates/{resource_id}")
    settings: Settings = request.app.state.settings
    try:
        if action == "ai-score":
            with contextmanager(get_ai_scorer)(resource_id, session, settings) as service:
                service.score(resource_id, force=False)
        elif action == "research":
            with contextmanager(get_research_service)(resource_id, session, settings) as service:
                service.research(resource_id, force=False)
        elif action == "script":
            with contextmanager(get_script_service)(resource_id, session, settings) as service:
                service.generate(resource_id, ScriptGenerationRequest())
        elif action == "narration":
            with contextmanager(get_narration_service)(resource_id, session, settings) as service:
                service.generate(resource_id, NarrationRequest())
        elif action == "render-plan":
            edit_plan_service(settings, session).create(
                resource_id,
                RenderPlanRequest(
                    script_id=int(values["script_id"]), narration_id=int(values["narration_id"])
                ),
            )
        elif action == "raw-render":
            with contextmanager(get_render_service)(resource_id, session, settings) as service:
                service.render(resource_id, force=False)
        elif action == "caption-plan":
            caption_plan_service(settings, session).create(resource_id, CaptionPlanRequest())
        elif action == "final-render":
            with contextmanager(get_final_render_service)(session, settings) as service:
                service.render(resource_id, force=False)
        elif action == "preview":
            with contextmanager(get_final_render_service)(session, settings) as service:
                service.preview(resource_id, float(values.get("time_seconds", "1.5")))
        else:
            raise NotFoundError("Unknown pipeline action")
    except (ApplicationError, KeyError, ValueError) as exc:
        return _redirect(target, error=str(exc))
    return _redirect(target, notice=f"{action.replace('-', ' ').title()} completed")


@router.post("/dashboard/discover", name="dashboard_discover")
async def discover(request: Request, session: SessionDep):
    values = await form_data(request)
    verify_csrf(request, values)
    target = "/dashboard/candidates"
    settings: Settings = request.app.state.settings
    try:
        payload = DiscoveryRequest(
            object_name=values.get("object_name", ""),
            process_name=values.get("process_name") or None,
            category=values.get("category") or None,
            max_queries=3,
            per_page=10,
        )
        with configured_providers(settings) as providers:
            service = DiscoveryService(
                CandidateRepository(session),
                SearchCacheRepository(session),
                providers,
                TemplateQueryGenerator(),
                HeuristicVideoScorer(settings.scoring_weights),
            )
            result = service.discover(payload)
    except (ApplicationError, ValueError) as exc:
        return _redirect("/", error=str(exc))
    return _redirect(target, notice=f"Discovery completed: {len(result.candidate_ids)} candidates")
