from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import NotFoundError
from app.models import (
    CaptionPlan,
    FinalRenderAsset,
    FinalRenderQualityReview,
    NarrationAsset,
    RenderAsset,
    ResearchClaim,
    ResearchDossier,
    ScoreEvaluation,
    ScriptBeat,
    ScriptDraft,
    VideoCandidate,
    VideoEditPlan,
)
from app.schemas.domain import (
    CandidateStatus,
    FinalRenderStatus,
    NarrationStatus,
    RenderStatus,
    ResearchStatus,
    RightsStatus,
    ScriptStatus,
)
from app.services.rights.policy import (
    is_cleared_for_commercial_publication,
    publication_clearance_label,
)

REVIEWABLE_RENDER = (RenderStatus.VALIDATED, RenderStatus.NEEDS_REVIEW)
REVIEWABLE_FINAL = (FinalRenderStatus.VALIDATED, FinalRenderStatus.NEEDS_REVIEW)


@dataclass(frozen=True)
class Page:
    items: list[Any]
    total: int
    page: int
    per_page: int

    @property
    def pages(self) -> int:
        return max(1, ceil(self.total / self.per_page))


def status_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


class DashboardService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def home(self) -> dict[str, Any]:
        count = self._count
        counts = {
            "candidates": count(VideoCandidate),
            "rights_review": count(
                VideoCandidate,
                VideoCandidate.rights.has(
                    VideoCandidate.rights.property.mapper.class_.rights_status
                    != RightsStatus.VERIFIED
                ),
            ),
            "scored": count(VideoCandidate, VideoCandidate.total_score.is_not(None)),
            "approved_candidates": count(
                VideoCandidate, VideoCandidate.status == CandidateStatus.APPROVED
            ),
            "research_review": count(
                ResearchDossier, ResearchDossier.status == ResearchStatus.NEEDS_REVIEW
            ),
            "script_review": count(
                ScriptDraft,
                ScriptDraft.status.in_((ScriptStatus.VALIDATED, ScriptStatus.NEEDS_REVISION)),
            ),
            "narration_review": count(
                NarrationAsset,
                NarrationAsset.status.in_(
                    (NarrationStatus.VALIDATED, NarrationStatus.NEEDS_REVIEW)
                ),
            ),
            "raw_review": count(RenderAsset, RenderAsset.status.in_(REVIEWABLE_RENDER)),
            "final_review": count(FinalRenderAsset, FinalRenderAsset.status.in_(REVIEWABLE_FINAL)),
            "approved_final": count(
                FinalRenderAsset, FinalRenderAsset.status == FinalRenderStatus.APPROVED
            ),
        }
        recent = list(
            self.session.scalars(
                select(VideoCandidate).order_by(VideoCandidate.created_at.desc()).limit(6)
            )
        )
        return {"counts": counts, "recent_candidates": recent}

    def _count(self, model: type, *clauses: Any) -> int:
        return int(
            self.session.scalar(select(func.count()).select_from(model).where(*clauses)) or 0
        )

    def candidates(
        self,
        *,
        provider: str | None,
        status: str | None,
        minimum_score: float | None,
        category: str | None,
        industrial_process: str | None,
        rights_status: str | None,
        sort: str,
        page: int,
        per_page: int,
    ) -> Page:
        clauses: list[Any] = []
        if provider:
            clauses.append(VideoCandidate.provider == provider)
        if status:
            clauses.append(VideoCandidate.status == status)
        if minimum_score is not None:
            clauses.append(VideoCandidate.total_score >= minimum_score)
        if category:
            clauses.append(VideoCandidate.category == category)
        if industrial_process:
            clauses.append(VideoCandidate.industrial_process == industrial_process)
        if rights_status:
            rights_model = VideoCandidate.rights.property.mapper.class_
            clauses.append(VideoCandidate.rights.has(rights_model.rights_status == rights_status))
        total = self._count(VideoCandidate, *clauses)
        if sort == "score":
            order = (VideoCandidate.total_score.desc().nullslast(), VideoCandidate.id.desc())
        elif sort == "status":
            order = (VideoCandidate.status, VideoCandidate.id.desc())
        else:
            order = (VideoCandidate.created_at.desc(), VideoCandidate.id.desc())
        items = list(
            self.session.scalars(
                select(VideoCandidate)
                .where(*clauses)
                .order_by(*order)
                .limit(per_page)
                .offset((page - 1) * per_page)
            )
        )
        candidate_ids = [item.id for item in items]
        if candidate_ids:
            stage_sources = (
                ("Final render", FinalRenderAsset, RenderAsset, FinalRenderAsset.raw_render_id),
                ("Raw render", RenderAsset, None, RenderAsset.candidate_id),
                ("TTS", NarrationAsset, ScriptDraft, NarrationAsset.script_id),
                ("Script", ScriptDraft, None, ScriptDraft.candidate_id),
                ("Research", ResearchDossier, None, ResearchDossier.candidate_id),
                ("Visual analysis", ScoreEvaluation, None, ScoreEvaluation.candidate_id),
            )
            stages: dict[int, str] = {}
            for label, model, via, foreign_key in stage_sources:
                if via is RenderAsset:
                    statement = (
                        select(RenderAsset.candidate_id)
                        .join(model, foreign_key == RenderAsset.id)
                        .where(RenderAsset.candidate_id.in_(candidate_ids))
                    )
                elif via is ScriptDraft:
                    statement = (
                        select(ScriptDraft.candidate_id)
                        .join(model, foreign_key == ScriptDraft.id)
                        .where(ScriptDraft.candidate_id.in_(candidate_ids))
                    )
                else:
                    statement = select(foreign_key).where(foreign_key.in_(candidate_ids))
                for candidate_id in self.session.scalars(statement.distinct()):
                    stages.setdefault(candidate_id, label)
            preview_rows = self.session.execute(
                select(RenderAsset.candidate_id, FinalRenderAsset.id)
                .join(FinalRenderAsset, FinalRenderAsset.raw_render_id == RenderAsset.id)
                .where(
                    RenderAsset.candidate_id.in_(candidate_ids),
                    FinalRenderAsset.preview_path.is_not(None),
                )
                .order_by(FinalRenderAsset.created_at.desc(), FinalRenderAsset.id.desc())
            )
            previews: dict[int, int] = {}
            for candidate_id, preview_id in preview_rows:
                previews.setdefault(candidate_id, preview_id)
            for item in items:
                item.dashboard_stage = stages.get(item.id, "Discovery")
                item.dashboard_preview_id = previews.get(item.id)
        return Page(items, total, page, per_page)

    def candidate_detail(self, candidate_id: int) -> dict[str, Any]:
        candidate = self.session.get(VideoCandidate, candidate_id)
        if candidate is None:
            raise NotFoundError(f"Candidate {candidate_id} was not found")
        evaluations = list(
            self.session.scalars(
                select(ScoreEvaluation)
                .where(ScoreEvaluation.candidate_id == candidate_id)
                .order_by(ScoreEvaluation.created_at.desc())
            )
        )
        dossier = self.session.scalar(
            select(ResearchDossier)
            .where(ResearchDossier.candidate_id == candidate_id)
            .options(
                selectinload(ResearchDossier.sources),
                selectinload(ResearchDossier.claims)
                .selectinload(ResearchClaim.evidence)
                .selectinload(ResearchClaim.evidence.property.mapper.class_.source),
                selectinload(ResearchDossier.contradictions),
            )
            .order_by(ResearchDossier.created_at.desc(), ResearchDossier.id.desc())
            .limit(1)
        )
        scripts = list(
            self.session.scalars(
                select(ScriptDraft)
                .where(ScriptDraft.candidate_id == candidate_id)
                .options(
                    selectinload(ScriptDraft.beats).selectinload(ScriptBeat.sentences),
                    selectinload(ScriptDraft.reviews),
                    selectinload(ScriptDraft.narrations).options(
                        selectinload(NarrationAsset.voice_profile),
                        selectinload(NarrationAsset.alignments),
                        selectinload(NarrationAsset.reviews),
                    ),
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
        raw_renders = list(
            self.session.scalars(
                select(RenderAsset)
                .where(RenderAsset.candidate_id == candidate_id)
                .options(selectinload(RenderAsset.reviews))
                .order_by(RenderAsset.created_at.desc(), RenderAsset.id.desc())
            )
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
                            FinalRenderAsset.reviews
                        ),
                    )
                    .order_by(CaptionPlan.created_at.desc(), CaptionPlan.id.desc())
                )
            )
            if raw_ids
            else []
        )
        narrations = [item for script in scripts for item in script.narrations]
        final_renders = [item for plan in caption_plans for item in plan.final_renders]
        latest_ai = next(
            (item for item in evaluations if item.method in ("ai_visual", "source_structural")),
            None,
        )
        latest_script = scripts[0] if scripts else None
        approved_script = next(
            (item for item in scripts if item.status == ScriptStatus.APPROVED), None
        )
        approved_narration = next(
            (item for item in narrations if item.status == NarrationStatus.APPROVED), None
        )
        approved_raw = next(
            (item for item in raw_renders if item.status == RenderStatus.APPROVED), None
        )
        return {
            "candidate": candidate,
            "commercial_rights_cleared": bool(
                candidate and is_cleared_for_commercial_publication(candidate.rights)
            ),
            "rights_label": (
                publication_clearance_label(candidate.rights)
                if candidate
                else "NOT CLEARED FOR PUBLICATION"
            ),
            "evaluations": evaluations,
            "latest_ai": latest_ai,
            "dossier": dossier,
            "scripts": scripts,
            "script": latest_script,
            "narrations": narrations,
            "plans": plans,
            "raw_renders": raw_renders,
            "caption_plans": caption_plans,
            "final_renders": sorted(final_renders, key=lambda item: item.id, reverse=True),
            "pipeline": self._pipeline(
                candidate, latest_ai, dossier, latest_script, narrations, raw_renders, final_renders
            ),
            "actions": {
                "ai": candidate.status
                not in (CandidateStatus.REJECTED, CandidateStatus.PROCESSING),
                "research": latest_ai is not None,
                "script": dossier is not None and dossier.status == ResearchStatus.VERIFIED,
                "narration": approved_script is not None,
                "render_plan": approved_script is not None and approved_narration is not None,
                "caption_plan": approved_raw is not None,
            },
            "approved_script": approved_script,
            "approved_narration": approved_narration,
            "approved_raw": approved_raw,
        }

    @staticmethod
    def _pipeline(candidate, evaluation, dossier, script, narrations, renders, finals):
        return [
            ("Discovery", candidate.status),
            ("Visual analysis", "PENDING" if evaluation is None else "COMPLETED"),
            ("Research", "PENDING" if dossier is None else dossier.status),
            ("Script", "PENDING" if script is None else script.status),
            ("TTS", "PENDING" if not narrations else narrations[0].status),
            ("Raw render", "PENDING" if not renders else renders[0].status),
            ("Final render", "PENDING" if not finals else finals[0].status),
        ]

    def review_queue(self, stage: str | None, page: int, per_page: int) -> dict[str, Any]:
        sections: dict[str, tuple[type, Select[Any], str]] = {
            "rights": (
                VideoCandidate,
                select(VideoCandidate).where(
                    VideoCandidate.rights.has(
                        VideoCandidate.rights.property.mapper.class_.rights_status
                        != RightsStatus.VERIFIED
                    )
                ),
                "Rights",
            ),
            "research": (
                ResearchDossier,
                select(ResearchDossier).where(
                    ResearchDossier.status == ResearchStatus.NEEDS_REVIEW
                ),
                "Research",
            ),
            "scripts": (
                ScriptDraft,
                select(ScriptDraft).where(
                    ScriptDraft.status.in_((ScriptStatus.VALIDATED, ScriptStatus.NEEDS_REVISION))
                ),
                "Scripts",
            ),
            "narrations": (
                NarrationAsset,
                select(NarrationAsset).where(
                    NarrationAsset.status.in_(
                        (NarrationStatus.VALIDATED, NarrationStatus.NEEDS_REVIEW)
                    )
                ),
                "Narrations",
            ),
            "raw-renders": (
                RenderAsset,
                select(RenderAsset).where(RenderAsset.status.in_(REVIEWABLE_RENDER)),
                "Raw renders",
            ),
            "final-renders": (
                FinalRenderAsset,
                select(FinalRenderAsset).where(FinalRenderAsset.status.in_(REVIEWABLE_FINAL)),
                "Final renders",
            ),
        }
        selected = sections if not stage or stage not in sections else {stage: sections[stage]}
        result = []
        for key, (model, statement, label) in selected.items():
            total = self._count_from(statement)
            items = list(
                self.session.scalars(
                    statement.order_by(model.created_at.desc(), model.id.desc())
                    .limit(per_page)
                    .offset((page - 1) * per_page)
                )
            )
            if key == "narrations" and items:
                candidate_by_script = dict(
                    self.session.execute(
                        select(ScriptDraft.id, ScriptDraft.candidate_id).where(
                            ScriptDraft.id.in_([item.script_id for item in items])
                        )
                    )
                )
                for item in items:
                    item.dashboard_candidate_id = candidate_by_script.get(item.script_id)
            result.append({"key": key, "label": label, "items": items, "total": total})
        result.sort(key=lambda item: item["key"] != "final-renders")
        return {"sections": result, "stage": stage, "page": page, "per_page": per_page}

    def _count_from(self, statement: Select[Any]) -> int:
        return int(self.session.scalar(select(func.count()).select_from(statement.subquery())) or 0)

    def final_render_detail(self, render_id: int) -> dict[str, Any]:
        render = self.session.scalar(
            select(FinalRenderAsset)
            .where(FinalRenderAsset.id == render_id)
            .options(
                selectinload(FinalRenderAsset.reviews),
                selectinload(FinalRenderAsset.quality_reviews),
            )
        )
        if render is None:
            raise NotFoundError(f"Final render {render_id} was not found")
        plan = self.session.scalar(
            select(CaptionPlan)
            .where(CaptionPlan.id == render.caption_plan_id)
            .options(selectinload(CaptionPlan.items), selectinload(CaptionPlan.overlays))
        )
        raw = self.session.get(RenderAsset, render.raw_render_id)
        candidate = self.session.get(VideoCandidate, raw.candidate_id) if raw else None
        script = self.session.scalar(
            select(ScriptDraft)
            .where(ScriptDraft.id == plan.script_id)
            .options(selectinload(ScriptDraft.beats).selectinload(ScriptBeat.sentences))
        )
        narration = self.session.scalar(
            select(NarrationAsset)
            .where(NarrationAsset.id == plan.narration_id)
            .options(selectinload(NarrationAsset.voice_profile))
        )
        version_rows = list(
            self.session.execute(
                select(FinalRenderAsset, CaptionPlan.style_profile)
                .join(CaptionPlan, FinalRenderAsset.caption_plan_id == CaptionPlan.id)
                .where(CaptionPlan.render_asset_id == plan.render_asset_id)
                .order_by(FinalRenderAsset.created_at.desc(), FinalRenderAsset.id.desc())
            )
        )
        versions = []
        for asset, style in version_rows:
            asset.dashboard_style = style
            asset.dashboard_quality_review = self.session.scalar(
                select(FinalRenderQualityReview)
                .where(FinalRenderQualityReview.final_render_id == asset.id)
                .order_by(
                    FinalRenderQualityReview.created_at.desc(),
                    FinalRenderQualityReview.id.desc(),
                )
                .limit(1)
            )
            versions.append(asset)
        return {
            "render": render,
            "plan": plan,
            "raw": raw,
            "candidate": candidate,
            "commercial_rights_cleared": bool(
                candidate and is_cleared_for_commercial_publication(candidate.rights)
            ),
            "rights_label": (
                publication_clearance_label(candidate.rights)
                if candidate
                else "NOT CLEARED FOR PUBLICATION"
            ),
            "script": script,
            "narration": narration,
            "versions": versions,
            "playable_versions": [item for item in versions if item.output_path],
            "quality_reviews": sorted(
                render.quality_reviews, key=lambda item: item.id, reverse=True
            ),
        }

    def failures(self, limit: int = 50) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for item in self.session.scalars(
            select(NarrationAsset)
            .where(NarrationAsset.status == NarrationStatus.FAILED)
            .order_by(NarrationAsset.created_at.desc())
            .limit(limit)
        ):
            entries.append(
                {
                    "stage": "TTS",
                    "id": item.id,
                    "created_at": item.created_at,
                    "summary": "Narration generation failed",
                }
            )
        for model, stage in ((RenderAsset, "Raw render"), (FinalRenderAsset, "Final render")):
            for item in self.session.scalars(
                select(model)
                .where(model.status == "FAILED")
                .order_by(model.created_at.desc())
                .limit(limit)
            ):
                entries.append(
                    {
                        "stage": stage,
                        "id": item.id,
                        "created_at": item.created_at,
                        "summary": (item.failure_reason or f"{stage} failed")[:300],
                    }
                )
        return sorted(entries, key=lambda item: item["created_at"], reverse=True)[:limit]
