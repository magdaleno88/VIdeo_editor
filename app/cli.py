import argparse
import json
import sys
from contextlib import contextmanager
from pathlib import Path

import httpx
from pydantic import BaseModel, ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm.exc import StaleDataError

from app.core.config import Settings
from app.core.database import build_engine, session_factory, session_scope
from app.core.errors import ApplicationError, ConfigurationError, ConflictError
from app.core.logging import configure_logging
from app.core.schema import check_schema
from app.providers.ai import GeminiVideoAnalysisProvider
from app.providers.registry import configured_providers
from app.repositories.cache import SearchCacheRepository
from app.repositories.candidates import CandidateRepository, compare_evaluations
from app.repositories.captions import CaptionRepository
from app.repositories.narrations import NarrationRepository
from app.repositories.renders import RenderRepository
from app.repositories.research import ResearchRepository
from app.repositories.scripts import ScriptRepository
from app.schemas.domain import (
    AnalysisProfile,
    CandidateFilters,
    CandidatePage,
    CandidateRead,
    CandidateStatus,
    CaptionPlanRequest,
    CompositionStrategy,
    ConceptGenerationRequest,
    DiscoveryRequest,
    EvaluationRead,
    EventRead,
    FinalRenderReviewRequest,
    Idea,
    ManualScoreRequest,
    NarrationRequest,
    NarrationReviewRequest,
    ProviderName,
    RenderPlanRequest,
    RenderReviewRequest,
    ResearchReview,
    ReviewAction,
    RightsReview,
    RightsStatus,
    ScriptGenerationRequest,
    ScriptReviewRequest,
    ScriptStyle,
    SourceAnalysisRequest,
    SourceRightsReview,
    VoiceSettings,
)
from app.services.captions.runtime import caption_plan_service, configured_final_render_service
from app.services.captions.service import FinalRenderReviewService
from app.services.discovery.queries import TemplateQueryGenerator
from app.services.discovery.service import DiscoveryService
from app.services.narration.runtime import configured_narration_service
from app.services.narration.service import NarrationReviewService
from app.services.rendering.runtime import configured_render_service, edit_plan_service
from app.services.rendering.service import RenderReviewService
from app.services.research.runtime import configured_research_service
from app.services.research.service import ResearchReviewService
from app.services.review import ReviewService
from app.services.scoring.ai import AIVideoScorer
from app.services.scoring.scorers import HeuristicVideoScorer
from app.services.scripting.runtime import configured_script_service
from app.services.scripting.service import ScriptReviewService
from app.services.sources.service import LongFormSourceService
from app.services.video.assets import VideoAssetFetcher


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Industrial Content Factory (local MVP)")
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("queries", "discover"):
        command = commands.add_parser(name)
        command.add_argument("object_name")
        command.add_argument("--process", dest="process_name")
        command.add_argument("--category")
        if name == "discover":
            command.add_argument("--provider", choices=list(ProviderName), action="append")
            command.add_argument("--max-queries", type=int, default=3)
            command.add_argument("--page", type=int, default=1)
            command.add_argument("--per-page", type=int, default=10)
    listing = commands.add_parser("candidates")
    listing.add_argument("--top", type=int, metavar="N")
    listing.add_argument("--limit", type=int, default=20)
    listing.add_argument("--offset", type=int, default=0)
    listing.add_argument("--provider", choices=list(ProviderName))
    listing.add_argument("--status", choices=list(CandidateStatus))
    listing.add_argument("--minimum-score", type=float)
    listing.add_argument("--category")
    listing.add_argument("--industrial-process")
    candidate = commands.add_parser("candidate").add_subparsers(dest="action", required=True)
    for name in (
        "show",
        "history",
        "evaluations",
        "compare-scores",
        "ai-score",
        "research",
        "research-show",
        "claims",
        "sources",
        "research-verify",
        "script",
        "scripts",
        "narrate",
        "narrations",
        "render-plan",
        "render-plans",
        "approve",
        "reject",
        "rights",
        "score",
    ):
        action = candidate.add_parser(name)
        action.add_argument("id", type=int)
        if name in ("approve", "reject"):
            action.add_argument("--reviewer", required=True)
            action.add_argument("--notes", required=True)
        if name in ("rights", "score"):
            action.add_argument("--file", type=Path, required=True)
        if name == "ai-score":
            action.add_argument("--force", action="store_true")
        if name == "research":
            action.add_argument("--force", action="store_true")
        if name == "research-verify":
            action.add_argument("--decision", choices=("VERIFIED", "REJECTED"), required=True)
            action.add_argument("--reviewer", required=True)
            action.add_argument("--notes", required=True)
        if name == "script":
            action.add_argument("--duration", type=int, choices=(30, 45, 60), default=45)
            action.add_argument(
                "--style",
                choices=[item.value.lower() for item in ScriptStyle],
                default="educational",
            )
            action.add_argument("--variants", type=int, choices=(1, 2, 3), default=1)
            action.add_argument("--force", action="store_true")
            action.add_argument("--editorial-context", default="")
        if name == "narrate":
            action.add_argument("--voice")
            action.add_argument("--voice-name")
            action.add_argument("--stability", type=float)
            action.add_argument("--similarity-boost", type=float)
            action.add_argument("--style-strength", type=float)
            action.add_argument("--speed", type=float)
            action.add_argument("--speaker-boost", action="store_true", default=None)
            action.add_argument("--force", action="store_true")
        if name == "render-plan":
            action.add_argument("--script", type=int, required=True)
            action.add_argument("--narration", type=int, required=True)
            action.add_argument(
                "--composition",
                choices=[item.value.lower().replace("_", "-") for item in CompositionStrategy],
                default="center-crop",
            )
            action.add_argument("--force", action="store_true")
    narration = commands.add_parser("narration").add_subparsers(dest="action", required=True)
    for name in ("show", "approve", "reject"):
        action = narration.add_parser(name)
        action.add_argument("id", type=int)
        if name in ("approve", "reject"):
            action.add_argument("--reviewer", required=True)
            action.add_argument("--notes", required=True)
    script = commands.add_parser("script").add_subparsers(dest="action", required=True)
    for name in ("show", "approve", "reject"):
        action = script.add_parser(name)
        action.add_argument("id", type=int)
        if name in ("approve", "reject"):
            action.add_argument("--reviewer", required=True)
            action.add_argument("--notes", required=True)
    render_plan = commands.add_parser("render-plan").add_subparsers(dest="action", required=True)
    for name in ("show", "render"):
        action = render_plan.add_parser(name)
        action.add_argument("id", type=int)
        if name == "render":
            action.add_argument("--force", action="store_true")
    render = commands.add_parser("render").add_subparsers(dest="action", required=True)
    for name in ("show", "caption-plan", "approve", "reject"):
        action = render.add_parser(name)
        action.add_argument("id", type=int)
        if name == "caption-plan":
            action.add_argument("--style", choices=("clean", "bold", "minimal"))
            action.add_argument("--position", choices=("upper", "center", "lower"), default="lower")
            action.add_argument("--emphasis", choices=("none", "phrase", "word"), default="phrase")
            action.add_argument("--no-hook", action="store_true")
            action.add_argument("--claim", type=int, action="append", default=[])
            action.add_argument("--force", action="store_true")
        if name in ("approve", "reject"):
            action.add_argument("--reviewer", required=True)
            action.add_argument("--notes", required=True)
    caption_plan = commands.add_parser("caption-plan").add_subparsers(dest="action", required=True)
    for name in ("show", "render"):
        action = caption_plan.add_parser(name)
        action.add_argument("id", type=int)
        if name == "render":
            action.add_argument("--force", action="store_true")
    final_render = commands.add_parser("final-render").add_subparsers(dest="action", required=True)
    for name in ("show", "preview", "approve", "reject"):
        action = final_render.add_parser(name)
        action.add_argument("id", type=int)
        if name == "preview":
            action.add_argument("--time", type=float, default=1.5)
        if name in ("approve", "reject"):
            action.add_argument("--reviewer", required=True)
            action.add_argument("--notes", required=True)
    source = commands.add_parser("source").add_subparsers(dest="action", required=True)
    source_import = source.add_parser("import")
    source_import.add_argument("path")
    source_import.add_argument("--title")
    source.add_parser("list")
    for name in ("show", "detect-scenes", "transcribe", "analyze", "moments", "concepts"):
        action = source.add_parser(name)
        action.add_argument("id", type=int)
        if name == "detect-scenes":
            action.add_argument("--force", action="store_true")
        if name == "analyze":
            action.add_argument(
                "--profile", choices=[item.value for item in AnalysisProfile], default="BALANCED"
            )
            action.add_argument("--external-ai", action="store_true")
        if name == "concepts":
            action.add_argument("--count", type=int, choices=range(1, 6), default=3)
    source_rights = source.add_parser("rights-review")
    source_rights.add_argument("id", type=int)
    source_rights.add_argument("--license", required=True)
    source_rights.add_argument("--evidence", required=True)
    source_rights.add_argument("--reviewer", required=True)
    source_rights.add_argument("--notes", default="")
    concept = commands.add_parser("concept").add_subparsers(dest="action", required=True)
    for name in ("show", "approve", "reject"):
        action = concept.add_parser(name)
        action.add_argument("id", type=int)
        if name in ("approve", "reject"):
            action.add_argument("--reviewer", required=True)
            action.add_argument("--notes", required=True)
    return root


def print_json(value: BaseModel | list) -> None:
    if isinstance(value, BaseModel):
        print(value.model_dump_json(indent=2))
    else:
        print(json.dumps(value, ensure_ascii=False, indent=2))


@contextmanager
def configured_ai_scorer(settings: Settings, repository: CandidateRepository):
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


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        settings = Settings()
        configure_logging(settings.log_level)
        if args.command == "queries":
            idea = Idea(
                object_name=args.object_name, process_name=args.process_name, category=args.category
            )
            print_json(TemplateQueryGenerator().generate(idea))
            return 0
        engine = build_engine(settings.database_url)
        try:
            check_schema(engine)
            with session_scope(session_factory(engine)) as session:
                repository = CandidateRepository(session)
                service = ReviewService(repository, settings.scoring_weights)
                if args.command == "source":
                    source_service = LongFormSourceService(session, settings)
                    if args.action == "import":
                        result, _ = source_service.import_local(args.path, title=args.title)
                    elif args.action == "list":
                        result = [item.model_dump(mode="json") for item in source_service.list()]
                    elif args.action == "show":
                        result = source_service.get(args.id)
                    elif args.action == "rights-review":
                        result = source_service.review_rights(
                            args.id,
                            SourceRightsReview(
                                rights_status=RightsStatus.VERIFIED,
                                license_name=args.license,
                                commercial_use_allowed=True,
                                derivative_works_allowed=True,
                                attribution_required=False,
                                evidence_reference=args.evidence,
                                reviewer=args.reviewer,
                                notes=args.notes,
                            ),
                        )
                    elif args.action == "detect-scenes":
                        result = source_service.detect_scenes(args.id, force=args.force)
                    elif args.action == "transcribe":
                        from app.schemas.domain import SourceTranscriptionRequest

                        result = source_service.transcribe(args.id, SourceTranscriptionRequest())
                    elif args.action == "analyze":
                        result = source_service.analyze(
                            args.id,
                            SourceAnalysisRequest(
                                profile=args.profile, use_external_ai=args.external_ai
                            ),
                        )
                    elif args.action == "moments":
                        analysis = source_service.sources.latest_analysis(args.id)
                        result = (
                            []
                            if analysis is None
                            else [
                                {
                                    "id": item.id,
                                    "start": item.start_seconds,
                                    "end": item.end_seconds,
                                    "description": item.description,
                                }
                                for item in analysis.moments
                            ]
                        )
                    else:
                        result = source_service.generate_concepts(
                            args.id, ConceptGenerationRequest(count=args.count)
                        )
                elif args.command == "concept":
                    source_service = LongFormSourceService(session, settings)
                    if args.action == "show":
                        result = source_service.get_concept(args.id)
                    else:
                        action = ReviewAction(reviewer=args.reviewer, notes=args.notes)
                        result = (
                            source_service.approve_concept(args.id, action)
                            if args.action == "approve"
                            else source_service.reject_concept(args.id, action)
                        )
                elif args.command == "discover":
                    request = DiscoveryRequest(
                        object_name=args.object_name,
                        process_name=args.process_name,
                        category=args.category,
                        providers=args.provider,
                        max_queries=args.max_queries,
                        page=args.page,
                        per_page=args.per_page,
                    )
                    with configured_providers(settings) as providers:
                        result = DiscoveryService(
                            repository,
                            SearchCacheRepository(session),
                            providers,
                            TemplateQueryGenerator(),
                            HeuristicVideoScorer(settings.scoring_weights),
                        ).discover(request)
                elif args.command == "candidates":
                    filters = CandidateFilters(
                        provider=args.provider,
                        status=args.status,
                        minimum_score=args.minimum_score,
                        category=args.category,
                        industrial_process=args.industrial_process,
                        limit=args.top if args.top is not None else args.limit,
                        offset=args.offset,
                    )
                    items, total = repository.list(filters, top=args.top is not None)
                    result = CandidatePage(
                        items=[CandidateRead.model_validate(item) for item in items],
                        total=total,
                        limit=filters.limit,
                        offset=filters.offset,
                    )
                elif args.command == "script":
                    script_review = ScriptReviewService(repository, ScriptRepository(session))
                    if args.action == "show":
                        result = script_review.get(args.id)
                    else:
                        result = script_review.review(
                            args.id,
                            ScriptReviewRequest(
                                decision="APPROVED" if args.action == "approve" else "REJECTED",
                                reviewer=args.reviewer,
                                notes=args.notes,
                            ),
                        )
                elif args.command == "narration":
                    narration_review = NarrationReviewService(
                        repository, ScriptRepository(session), NarrationRepository(session)
                    )
                    if args.action == "show":
                        result = narration_review.get(args.id)
                    else:
                        result = narration_review.review(
                            args.id,
                            NarrationReviewRequest(
                                decision="APPROVED" if args.action == "approve" else "REJECTED",
                                reviewer=args.reviewer,
                                notes=args.notes,
                            ),
                        )
                elif args.command == "render-plan":
                    if args.action == "show":
                        result = RenderReviewService(
                            repository, RenderRepository(session)
                        ).get_plan(args.id)
                    else:
                        with configured_render_service(settings, session) as render_service:
                            result = render_service.render(args.id, force=args.force)
                elif args.command == "render":
                    if args.action == "caption-plan":
                        result = caption_plan_service(settings, session).create(
                            args.id,
                            CaptionPlanRequest(
                                style_profile=args.style.upper() if args.style else None,
                                position=args.position.upper(),
                                emphasis_mode=args.emphasis.upper(),
                                include_hook=not args.no_hook,
                                factual_claim_ids=args.claim,
                                force=args.force,
                            ),
                        )
                    else:
                        render_review = RenderReviewService(repository, RenderRepository(session))
                        if args.action == "show":
                            result = render_review.get(args.id)
                        else:
                            result = render_review.review(
                                args.id,
                                RenderReviewRequest(
                                    decision=(
                                        "APPROVED" if args.action == "approve" else "REJECTED"
                                    ),
                                    reviewer=args.reviewer,
                                    notes=args.notes,
                                ),
                            )
                elif args.command == "caption-plan":
                    if args.action == "show":
                        result = caption_plan_service(settings, session).get(args.id)
                    else:
                        with configured_final_render_service(settings, session) as final_service:
                            result = final_service.render(args.id, force=args.force)
                elif args.command == "final-render":
                    final_review = FinalRenderReviewService(
                        repository, RenderRepository(session), CaptionRepository(session)
                    )
                    if args.action == "show":
                        result = final_review.get(args.id)
                    elif args.action == "preview":
                        with configured_final_render_service(settings, session) as final_service:
                            result = final_service.preview(args.id, args.time)
                    else:
                        result = final_review.review(
                            args.id,
                            FinalRenderReviewRequest(
                                decision=("APPROVED" if args.action == "approve" else "REJECTED"),
                                reviewer=args.reviewer,
                                notes=args.notes,
                            ),
                        )
                elif args.action == "history":
                    result = [
                        EventRead.model_validate(event).model_dump(mode="json")
                        for event in repository.events(args.id)
                    ]
                elif args.action == "evaluations":
                    result = [
                        EvaluationRead.model_validate(item).model_dump(mode="json")
                        for item in repository.evaluations(args.id)
                    ]
                elif args.action == "compare-scores":
                    ai = repository.latest_by_method(args.id, "ai_visual")
                    manual = repository.latest_by_method(args.id, "manual")
                    if ai is None or manual is None:
                        raise ConflictError(
                            "Both AI and manual evaluations are required for comparison"
                        )
                    result = compare_evaluations(args.id, ai, manual)
                elif args.action == "ai-score":
                    repository.get(args.id)
                    with configured_ai_scorer(settings, repository) as scorer:
                        result = scorer.score(args.id, force=args.force)
                elif args.action == "research":
                    repository.get(args.id)
                    with configured_research_service(settings, session) as research_service:
                        result = research_service.research(args.id, force=args.force)
                elif args.action == "script":
                    repository.get(args.id)
                    with configured_script_service(settings, session) as script_service:
                        result = script_service.generate(
                            args.id,
                            ScriptGenerationRequest(
                                style=args.style.upper(),
                                target_duration_seconds=args.duration,
                                variants=args.variants,
                                force=args.force,
                                editorial_context=args.editorial_context,
                            ),
                        )
                elif args.action == "scripts":
                    result = [
                        item.model_dump(mode="json")
                        for item in ScriptReviewService(repository, ScriptRepository(session)).list(
                            args.id
                        )
                    ]
                elif args.action == "narrate":
                    with configured_narration_service(settings, session) as narration_service:
                        result = narration_service.generate(
                            args.id,
                            NarrationRequest(
                                voice_id=args.voice,
                                voice_name=args.voice_name,
                                settings=VoiceSettings(
                                    stability=args.stability,
                                    similarity_boost=args.similarity_boost,
                                    style=args.style_strength,
                                    speed=args.speed,
                                    use_speaker_boost=args.speaker_boost,
                                ),
                                force=args.force,
                            ),
                        )
                elif args.action == "narrations":
                    result = [
                        item.model_dump(mode="json")
                        for item in NarrationReviewService(
                            repository, ScriptRepository(session), NarrationRepository(session)
                        ).list(args.id)
                    ]
                elif args.action == "render-plan":
                    result = edit_plan_service(settings, session).create(
                        args.id,
                        RenderPlanRequest(
                            script_id=args.script,
                            narration_id=args.narration,
                            composition=args.composition.upper().replace("-", "_"),
                            force=args.force,
                        ),
                    )
                elif args.action == "render-plans":
                    result = [
                        item.model_dump(mode="json")
                        for item in edit_plan_service(settings, session).list(args.id)
                    ]
                elif args.action in ("research-show", "claims", "sources", "research-verify"):
                    research_review = ResearchReviewService(repository, ResearchRepository(session))
                    if args.action == "research-show":
                        result = research_review.latest(args.id)
                    elif args.action == "claims":
                        result = [
                            item.model_dump(mode="json")
                            for item in research_review.latest(args.id).claims
                        ]
                    elif args.action == "sources":
                        result = [
                            item.model_dump(mode="json")
                            for item in research_review.latest(args.id).sources
                        ]
                    else:
                        result = research_review.review(
                            args.id,
                            ResearchReview(
                                decision=args.decision,
                                reviewer=args.reviewer,
                                notes=args.notes,
                            ),
                        )
                else:
                    if args.action == "show":
                        candidate = repository.get(args.id)
                    elif args.action in ("approve", "reject"):
                        action = ReviewAction(reviewer=args.reviewer, notes=args.notes)
                        candidate = getattr(service, args.action)(args.id, action)
                    else:
                        content = args.file.read_text(encoding="utf-8-sig")
                        if args.action == "rights":
                            candidate = service.review_rights(
                                args.id, RightsReview.model_validate_json(content)
                            )
                        else:
                            candidate = service.score(
                                args.id, ManualScoreRequest.model_validate_json(content)
                            )
                    result = CandidateRead.model_validate(candidate)
            # Output only after successful commit.
            print_json(result)
        finally:
            engine.dispose()
    except (ApplicationError, ValidationError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except StaleDataError:
        print("Error: Candidate changed concurrently; reload and retry", file=sys.stderr)
        return 1
    except SQLAlchemyError:
        print(
            "Error: Database operation failed. Check DATABASE_URL and run migrations.",
            file=sys.stderr,
        )
        return 1
    return 0
