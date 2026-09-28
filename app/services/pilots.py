from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from statistics import mean
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import ConflictError
from app.models import (
    CaptionPlan,
    FinalRenderAsset,
    FinalRenderQualityReview,
    NarrationAsset,
    PilotBatch,
    ResearchDossier,
    ScoreEvaluation,
    ScriptDraft,
    VideoCandidate,
    VideoEditPlan,
)
from app.repositories.candidates import CandidateRepository
from app.repositories.captions import CaptionRepository
from app.repositories.pilots import (
    PilotRepository,
    pilot_batch_read,
    quality_review_read,
)
from app.repositories.renders import RenderRepository
from app.schemas.domain import (
    AlignmentMethod,
    CandidateStatus,
    ClaimStatus,
    CompositionStrategy,
    FinalRenderQualityReviewRequest,
    FinalRenderReviewRequest,
    FinalRenderStatus,
    NarrationStatus,
    PilotBatchCreate,
    PilotProgressRead,
    PilotRunState,
    PilotStageRead,
    QualityRejectionCategory,
    RenderStatus,
    ResearchStatus,
    RevisionStage,
    RightsStatus,
    ScriptStatus,
)
from app.services.captions.service import FinalRenderReviewService

REVISION_ROUTES: dict[QualityRejectionCategory, RevisionStage] = {
    QualityRejectionCategory.FACTUAL_PROBLEM: RevisionStage.RESEARCH,
    QualityRejectionCategory.WEAK_HOOK: RevisionStage.SCRIPT,
    QualityRejectionCategory.TTS_PROBLEM: RevisionStage.NARRATION,
    QualityRejectionCategory.AUDIO_SYNC: RevisionStage.NARRATION,
    QualityRejectionCategory.BAD_CROP: RevisionStage.RENDER_PLAN,
    QualityRejectionCategory.BAD_CLIP_SELECTION: RevisionStage.RENDER_PLAN,
    QualityRejectionCategory.PACING_TOO_SLOW: RevisionStage.RENDER_PLAN,
    QualityRejectionCategory.PACING_TOO_FAST: RevisionStage.RENDER_PLAN,
    QualityRejectionCategory.VISUAL_NARRATION_MISMATCH: RevisionStage.RENDER_PLAN,
    QualityRejectionCategory.CAPTION_PROBLEM: RevisionStage.CAPTION_PLAN,
    QualityRejectionCategory.OTHER: RevisionStage.FINAL_REVIEW,
}
REVISION_PRIORITY = {stage: index for index, stage in enumerate(RevisionStage)}


def recommend_revision_stage(
    categories: list[QualityRejectionCategory],
) -> RevisionStage | None:
    stages = [REVISION_ROUTES[item] for item in categories]
    return min(stages, key=REVISION_PRIORITY.get) if stages else None


@dataclass
class PilotStage:
    key: str
    label: str
    status: str
    blocked_reason: str | None = None


@dataclass
class PilotProgress:
    batch_id: int
    candidate: VideoCandidate
    state: PilotRunState
    next_action: str
    next_action_key: str | None
    action_resource_id: int | None
    blocked_reason: str | None
    stages: list[PilotStage]
    external_calls: dict[str, int]
    version_counts: dict[str, int]
    elapsed_seconds: float | None
    technical_warnings: list[str]
    high_risk_flags: list[str]
    artifacts: dict[str, Any]


class PilotService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = PilotRepository(session)
        self.candidates = CandidateRepository(session)

    def create_batch(self, request: PilotBatchCreate):
        for candidate_id in request.candidate_ids:
            self.candidates.get(candidate_id)
        batch = self.repository.create_batch(
            PilotBatch(
                name=request.name,
                slug=request.slug,
                description=request.description,
            )
        )
        for candidate_id in request.candidate_ids:
            self.repository.add_candidate(batch.id, candidate_id)
        self.session.expire(batch, ["runs"])
        return pilot_batch_read(self.repository.get_batch(batch.id))

    def add_candidate(self, batch_id: int, candidate_id: int) -> PilotProgress:
        self.candidates.get(candidate_id)
        self.repository.add_candidate(batch_id, candidate_id)
        return self.progress(batch_id, candidate_id)

    def list_batches(self, page: int, per_page: int):
        items, total = self.repository.list_batches(per_page, (page - 1) * per_page)
        return {
            "items": [pilot_batch_read(item) for item in items],
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": max(1, (total + per_page - 1) // per_page),
        }

    def batch_summary(self, batch_id: int) -> dict[str, Any]:
        batch = self.repository.get_batch(batch_id)
        progresses = [self.progress(batch.id, run.candidate_id) for run in batch.runs]
        states = Counter(item.state.value for item in progresses)
        latest_reviews = [
            item.artifacts.get("quality_review")
            for item in progresses
            if item.artifacts.get("quality_review") is not None
        ]
        dimensions = (
            "visual_relevance",
            "pacing",
            "crop_quality",
            "narration_quality",
            "caption_readability",
            "hook_strength",
            "audio_sync",
            "overall_readiness",
        )
        averages = {
            name: round(mean(getattr(review, name) for review in latest_reviews), 2)
            for name in dimensions
            if latest_reviews
        }
        reasons = Counter(
            category
            for review in latest_reviews
            for category in (review.rejection_categories or [])
        )
        return {
            "batch": batch,
            "progresses": progresses,
            "states": states,
            "average_scores": averages,
            "common_rejection_reasons": reasons.most_common(),
        }

    def progress(self, batch_id: int, candidate_id: int) -> PilotProgress:
        batch = self.repository.get_batch(batch_id)
        if candidate_id not in {run.candidate_id for run in batch.runs}:
            raise ConflictError("Candidate is not part of this pilot batch")
        candidate = self.candidates.get(candidate_id)
        evaluations = list(
            self.session.scalars(
                select(ScoreEvaluation)
                .where(ScoreEvaluation.candidate_id == candidate_id)
                .order_by(ScoreEvaluation.created_at.desc(), ScoreEvaluation.id.desc())
            )
        )
        dossiers = list(
            self.session.scalars(
                select(ResearchDossier)
                .where(ResearchDossier.candidate_id == candidate_id)
                .options(selectinload(ResearchDossier.claims))
                .order_by(ResearchDossier.created_at.desc(), ResearchDossier.id.desc())
            )
        )
        scripts = list(
            self.session.scalars(
                select(ScriptDraft)
                .where(ScriptDraft.candidate_id == candidate_id)
                .options(
                    selectinload(ScriptDraft.narrations).selectinload(NarrationAsset.alignments)
                )
                .order_by(ScriptDraft.created_at.desc(), ScriptDraft.id.desc())
            )
        )
        plans = list(
            self.session.scalars(
                select(VideoEditPlan)
                .where(VideoEditPlan.candidate_id == candidate_id)
                .options(selectinload(VideoEditPlan.segments), selectinload(VideoEditPlan.renders))
                .order_by(VideoEditPlan.created_at.desc(), VideoEditPlan.id.desc())
            )
        )
        raw_renders = sorted(
            [render for plan in plans for render in plan.renders],
            key=lambda item: item.id,
            reverse=True,
        )
        raw_ids = [item.id for item in raw_renders]
        caption_plans = (
            list(
                self.session.scalars(
                    select(CaptionPlan)
                    .where(CaptionPlan.render_asset_id.in_(raw_ids))
                    .options(
                        selectinload(CaptionPlan.items),
                        selectinload(CaptionPlan.overlays),
                        selectinload(CaptionPlan.final_renders).selectinload(
                            FinalRenderAsset.quality_reviews
                        ),
                    )
                    .order_by(CaptionPlan.created_at.desc(), CaptionPlan.id.desc())
                )
            )
            if raw_ids
            else []
        )
        finals = sorted(
            [render for plan in caption_plans for render in plan.final_renders],
            key=lambda item: item.id,
            reverse=True,
        )
        ai = next((item for item in evaluations if item.method == "ai_visual"), None)
        dossier = dossiers[0] if dossiers else None
        script = scripts[0] if scripts else None
        approved_script = next(
            (item for item in scripts if item.status == ScriptStatus.APPROVED), None
        )
        narrations = sorted(
            [item for item in scripts for item in item.narrations],
            key=lambda item: item.id,
            reverse=True,
        )
        relevant_narrations = (
            [item for item in approved_script.narrations] if approved_script else narrations
        )
        narration = relevant_narrations[0] if relevant_narrations else None
        approved_narration = next(
            (item for item in relevant_narrations if item.status == NarrationStatus.APPROVED), None
        )
        matching_plans = [
            item
            for item in plans
            if approved_script
            and approved_narration
            and item.script_id == approved_script.id
            and item.narration_id == approved_narration.id
        ]
        plan = matching_plans[0] if matching_plans else None
        plan_renders = sorted(
            list(plan.renders) if plan else [], key=lambda item: item.id, reverse=True
        )
        raw = plan_renders[0] if plan_renders else None
        approved_raw = next(
            (item for item in plan_renders if item.status == RenderStatus.APPROVED), None
        )
        relevant_caption_plans = [
            item
            for item in caption_plans
            if approved_raw and item.render_asset_id == approved_raw.id
        ]
        caption_plan = relevant_caption_plans[0] if relevant_caption_plans else None
        relevant_finals = sorted(
            [render for item in relevant_caption_plans for render in item.final_renders],
            key=lambda item: item.id,
            reverse=True,
        )
        final = relevant_finals[0] if relevant_finals else None
        quality = (
            sorted(final.quality_reviews, key=lambda item: item.id, reverse=True)[0]
            if final and final.quality_reviews
            else None
        )

        rights_ok = (
            candidate.rights.rights_status == RightsStatus.VERIFIED
            and candidate.rights.commercial_use_allowed is True
            and candidate.rights.modification_allowed is True
        )
        stages, next_action, next_key, resource_id, blocker = self._resolve_stages(
            candidate=candidate,
            rights_ok=rights_ok,
            ai=ai,
            dossier=dossier,
            script=script,
            approved_script=approved_script,
            narration=narration,
            approved_narration=approved_narration,
            plan=plan,
            raw=raw,
            approved_raw=approved_raw,
            caption_plan=caption_plan,
            final=final,
            quality=quality,
        )
        stage_labels = (
            ("source", "Source"),
            ("rights", "Rights"),
            ("candidate", "Candidate approval"),
            ("visual", "Visual analysis"),
            ("research", "Research"),
            ("script", "Script"),
            ("narration", "Narration"),
            ("raw", "Raw render"),
            ("captions", "Captions"),
            ("final", "Final video"),
            ("quality", "Quality review"),
        )
        existing = {stage.key for stage in stages}
        for key, label in stage_labels:
            if key not in existing:
                stages.append(PilotStage(key, label, "BLOCKED", "Complete prior stages first."))
        state = self._state(candidate, rights_ok, dossier, script, narration, raw, final, quality)
        warnings, flags = self._warnings(
            candidate, dossier, narration, plan, raw, caption_plan, final
        )
        elapsed = None
        if final and final.status == FinalRenderStatus.APPROVED and final.reviewed_at:
            elapsed = max(0.0, (final.reviewed_at - candidate.created_at).total_seconds())
        external_calls = {
            "ai_analysis": sum(item.method == "ai_visual" for item in evaluations),
            "research": len(dossiers),
            "script_generation": len({item.batch_key for item in scripts}),
            "tts_generation": len(narrations),
        }
        version_counts = {
            "scripts": len(scripts),
            "narrations": len(narrations),
            "render_plans": len(plans),
            "raw_renders": len(raw_renders),
            "caption_plans": len(caption_plans),
            "final_renders": len(finals),
        }
        return PilotProgress(
            batch_id=batch_id,
            candidate=candidate,
            state=state,
            next_action=next_action,
            next_action_key=next_key,
            action_resource_id=resource_id,
            blocked_reason=blocker,
            stages=stages,
            external_calls=external_calls,
            version_counts=version_counts,
            elapsed_seconds=elapsed,
            technical_warnings=warnings,
            high_risk_flags=flags,
            artifacts={
                "evaluations": evaluations,
                "dossiers": dossiers,
                "scripts": scripts,
                "narrations": narrations,
                "plans": plans,
                "raw_renders": raw_renders,
                "caption_plans": caption_plans,
                "final_renders": finals,
                "quality_review": quality,
                "approved_script": approved_script,
                "approved_narration": approved_narration,
                "approved_raw": approved_raw,
            },
        )

    @staticmethod
    def as_read(progress: PilotProgress) -> PilotProgressRead:
        return PilotProgressRead(
            batch_id=progress.batch_id,
            candidate_id=progress.candidate.id,
            state=progress.state,
            next_action=progress.next_action,
            next_action_key=progress.next_action_key,
            blocked_reason=progress.blocked_reason,
            stages=[PilotStageRead(**stage.__dict__) for stage in progress.stages],
            external_calls=progress.external_calls,
            version_counts=progress.version_counts,
            elapsed_seconds=progress.elapsed_seconds,
            technical_warnings=progress.technical_warnings,
            high_risk_flags=progress.high_risk_flags,
        )

    @staticmethod
    def _resolve_stages(**values):
        candidate = values["candidate"]
        rights_ok = values["rights_ok"]
        ai = values["ai"]
        dossier = values["dossier"]
        script = values["script"]
        approved_script = values["approved_script"]
        narration = values["narration"]
        approved_narration = values["approved_narration"]
        plan = values["plan"]
        raw = values["raw"]
        approved_raw = values["approved_raw"]
        caption_plan = values["caption_plan"]
        final = values["final"]
        quality = values["quality"]
        stages = [PilotStage("source", "Source", "COMPLETED")]

        def blocked(key, label, reason):
            stages.append(PilotStage(key, label, "BLOCKED", reason))

        if not rights_ok:
            stages.append(
                PilotStage(
                    "rights",
                    "Rights",
                    "BLOCKED",
                    "Commercial use and modification rights have not been verified.",
                )
            )
            for key, label in (
                ("visual", "Visual analysis"),
                ("research", "Research"),
                ("script", "Script"),
                ("narration", "Narration"),
                ("raw", "Raw render"),
                ("captions", "Captions"),
                ("final", "Final video"),
                ("quality", "Quality review"),
            ):
                blocked(key, label, "Verified rights are required first.")
            return stages, "Verify rights", "rights-review", candidate.id, stages[1].blocked_reason
        stages.append(PilotStage("rights", "Rights", "VERIFIED"))
        if candidate.status != CandidateStatus.APPROVED:
            blocked("candidate", "Candidate approval", "Candidate has not been human approved.")
            return (
                stages,
                "Approve candidate",
                "candidate-approve",
                candidate.id,
                stages[-1].blocked_reason,
            )
        stages.append(PilotStage("candidate", "Candidate approval", "APPROVED"))
        if ai is None:
            if dossier is None:
                stages.append(PilotStage("visual", "Visual analysis", "PENDING"))
                return stages, "Run AI visual analysis", "ai-score", candidate.id, None
            stages.append(PilotStage("visual", "Visual analysis", "SKIPPED"))
        else:
            stages.append(PilotStage("visual", "Visual analysis", "COMPLETED"))
        if dossier is None:
            stages.append(PilotStage("research", "Research", "PENDING"))
            return stages, "Run research", "research", candidate.id, None
        stages.append(PilotStage("research", "Research", dossier.status.value))
        if dossier.status == ResearchStatus.NEEDS_REVIEW:
            return stages, "Review research", "research-review", dossier.id, None
        if dossier.status != ResearchStatus.VERIFIED:
            reason = "The latest research dossier was rejected."
            stages[-1].blocked_reason = reason
            return stages, "Revise research", "research", candidate.id, reason
        if script is None:
            stages.append(PilotStage("script", "Script", "PENDING"))
            return stages, "Generate script", "script", candidate.id, None
        stages.append(PilotStage("script", "Script", script.status.value))
        if approved_script is None:
            if script.status in (ScriptStatus.VALIDATED, ScriptStatus.NEEDS_REVISION):
                return stages, "Review script", "script-review", script.id, None
            reason = "No approved script version is available."
            stages[-1].blocked_reason = reason
            return stages, "Generate revised script", "script", candidate.id, reason
        if narration is None:
            stages.append(PilotStage("narration", "Narration", "PENDING"))
            return stages, "Generate narration", "narration", approved_script.id, None
        stages.append(PilotStage("narration", "Narration", narration.status.value))
        if approved_narration is None:
            if narration.status in (NarrationStatus.VALIDATED, NarrationStatus.NEEDS_REVIEW):
                return stages, "Review narration", "narration-review", narration.id, None
            reason = "No approved narration version is available."
            stages[-1].blocked_reason = reason
            return stages, "Generate revised narration", "narration", approved_script.id, reason
        if plan is None:
            stages.append(PilotStage("raw", "Raw render", "PENDING"))
            return stages, "Create render plan", "render-plan", candidate.id, None
        if raw is None:
            stages.append(PilotStage("raw", "Raw render", "PENDING"))
            return stages, "Render video", "raw-render", plan.id, None
        stages.append(PilotStage("raw", "Raw render", raw.status.value))
        if approved_raw is None:
            if raw.status in (RenderStatus.VALIDATED, RenderStatus.NEEDS_REVIEW):
                return stages, "Review raw render", "render-review", raw.id, None
            reason = "No approved raw render version is available."
            stages[-1].blocked_reason = reason
            return stages, "Revise render plan", "render-plan", candidate.id, reason
        if caption_plan is None:
            stages.append(PilotStage("captions", "Captions", "PENDING"))
            return stages, "Create caption plan", "caption-plan", approved_raw.id, None
        stages.append(PilotStage("captions", "Captions", "COMPLETED"))
        if final is None:
            stages.append(PilotStage("final", "Final video", "PENDING"))
            return stages, "Render final video", "final-render", caption_plan.id, None
        stages.append(PilotStage("final", "Final video", final.status.value))
        if final.status in (FinalRenderStatus.VALIDATED, FinalRenderStatus.NEEDS_REVIEW):
            stages.append(PilotStage("quality", "Quality review", "PENDING"))
            return stages, "Complete final quality review", "quality-review", final.id, None
        if (
            quality
            and quality.decision == "APPROVED"
            and final.status == FinalRenderStatus.APPROVED
        ):
            stages.append(PilotStage("quality", "Quality review", "APPROVED"))
            return stages, "Pilot complete", None, None, None
        if final.status == FinalRenderStatus.REJECTED:
            stage = (
                quality.recommended_revision_stage.value
                if quality and quality.recommended_revision_stage
                else "FINAL_REVIEW"
            )
            reason = f"Final video was rejected. Revise from {stage}."
            stages.append(PilotStage("quality", "Quality review", "REJECTED", reason))
            return stages, f"Revise from {stage}", "revision", final.id, reason
        stages.append(PilotStage("quality", "Quality review", "PENDING"))
        return stages, "Complete final quality review", "quality-review", final.id, None

    @staticmethod
    def _state(candidate, rights_ok, dossier, script, narration, raw, final, quality):
        if (
            final
            and final.status == FinalRenderStatus.APPROVED
            and quality
            and quality.decision == "APPROVED"
        ):
            return PilotRunState.READY
        if candidate.status == CandidateStatus.REJECTED or (
            final and final.status == FinalRenderStatus.REJECTED
        ):
            return PilotRunState.REJECTED
        if not rights_ok or any(
            item is not None and item.status.value in ("REJECTED", "FAILED")
            for item in (dossier, script, narration, raw)
        ):
            return PilotRunState.BLOCKED
        return PilotRunState.IN_REVIEW

    @staticmethod
    def _warnings(candidate, dossier, narration, plan, raw, caption_plan, final):
        warnings: list[str] = []
        flags: list[str] = []
        for item in (plan, raw, caption_plan, final):
            warnings.extend(getattr(item, "warnings", []) if item else [])
        if narration:
            warnings.append(
                f"Narration duration difference: {narration.duration_difference_seconds:+.2f}s"
            )
            if narration.alignment_method == AlignmentMethod.ESTIMATED_ALIGNMENT:
                flags.append("ESTIMATED_ALIGNMENT")
            if narration.duration_status.value in ("TOO_LONG", "TOO_SHORT"):
                flags.append(narration.duration_status.value)
        if plan:
            if any(segment.loop_count > 1 for segment in plan.segments):
                flags.append("LOOP_USED")
            if (
                plan.composition == CompositionStrategy.CENTER_CROP
                and candidate.width
                and candidate.height
                and candidate.width > candidate.height
            ):
                flags.append("CENTER_CROP_ON_WIDE_SOURCE")
            if any(not segment.visual_refs for segment in plan.segments):
                flags.append("MISSING_ROI")
        if dossier and (
            dossier.status != ResearchStatus.VERIFIED
            or any(claim.status == ClaimStatus.PARTIALLY_SUPPORTED for claim in dossier.claims)
        ):
            flags.append("PARTIAL_RESEARCH")
        return sorted(set(warnings)), sorted(set(flags))


class QualityReviewService:
    def __init__(
        self,
        candidates: CandidateRepository,
        renders: RenderRepository,
        captions: CaptionRepository,
        pilots: PilotRepository,
    ) -> None:
        self.candidates = candidates
        self.renders = renders
        self.captions = captions
        self.pilots = pilots

    def list(self, final_render_id: int):
        self.captions.get_render(final_render_id)
        return [quality_review_read(item) for item in self.pilots.quality_reviews(final_render_id)]

    def submit(self, final_render_id: int, request: FinalRenderQualityReviewRequest):
        render = self.captions.get_render(final_render_id)
        target = FinalRenderStatus(request.decision)
        if target == FinalRenderStatus.APPROVED and render.status not in (
            FinalRenderStatus.VALIDATED,
            FinalRenderStatus.NEEDS_REVIEW,
            FinalRenderStatus.APPROVED,
        ):
            raise ConflictError("Only a completed final render can pass quality review")
        stage = recommend_revision_stage(request.rejection_categories)
        review = self.pilots.add_quality_review(
            FinalRenderQualityReview(
                final_render_id=final_render_id,
                reviewer=request.reviewer,
                decision=request.decision,
                visual_relevance=request.visual_relevance,
                pacing=request.pacing,
                crop_quality=request.crop_quality,
                narration_quality=request.narration_quality,
                caption_readability=request.caption_readability,
                hook_strength=request.hook_strength,
                audio_sync=request.audio_sync,
                overall_readiness=request.overall_readiness,
                checklist=request.checklist.model_dump(mode="json"),
                rejection_categories=[item.value for item in request.rejection_categories],
                recommended_revision_stage=stage,
                notes=request.notes,
            )
        )
        if render.status != target:
            FinalRenderReviewService(self.candidates, self.renders, self.captions).review(
                final_render_id,
                FinalRenderReviewRequest(
                    decision=request.decision,
                    reviewer=request.reviewer,
                    notes=request.notes or "Structured final quality review",
                ),
            )
        else:
            raw = self.renders.get_render(render.raw_render_id)
            candidate = self.candidates.get(raw.candidate_id)
            self.candidates.record_event(
                candidate,
                "FINAL_QUALITY_REVIEWED",
                request.reviewer,
                request.notes or "Structured final quality review",
                {"final_render_id": final_render_id, "quality_review_id": review.id},
            )
        self.pilots.session.flush()
        return quality_review_read(review)
