from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import NotFoundError
from app.models import CaptionPlan, FinalRenderAsset
from app.schemas.domain import (
    CaptionItemRead,
    CaptionPlanRead,
    FinalRenderAssetRead,
    FinalRenderReviewRead,
    GraphicOverlayRead,
)


class CaptionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    @staticmethod
    def _plan_options():
        return (selectinload(CaptionPlan.items), selectinload(CaptionPlan.overlays))

    @staticmethod
    def _render_options():
        return (selectinload(FinalRenderAsset.reviews),)

    def add_plan(self, plan: CaptionPlan) -> CaptionPlan:
        self.session.add(plan)
        self.session.flush()
        return self.get_plan(plan.id)

    def get_plan(self, plan_id: int) -> CaptionPlan:
        plan = self.session.scalar(
            select(CaptionPlan).where(CaptionPlan.id == plan_id).options(*self._plan_options())
        )
        if plan is None:
            raise NotFoundError(f"Caption plan {plan_id} not found")
        return plan

    def list_plans(self, render_id: int) -> list[CaptionPlan]:
        return list(
            self.session.scalars(
                select(CaptionPlan)
                .where(CaptionPlan.render_asset_id == render_id)
                .options(*self._plan_options())
                .order_by(CaptionPlan.created_at.desc(), CaptionPlan.id.desc())
            )
        )

    def reusable_plan(self, cache_key: str) -> CaptionPlan | None:
        return self.session.scalar(
            select(CaptionPlan)
            .where(CaptionPlan.cache_key == cache_key)
            .options(*self._plan_options())
            .order_by(CaptionPlan.id.desc())
            .limit(1)
        )

    def add_render(self, render: FinalRenderAsset) -> FinalRenderAsset:
        self.session.add(render)
        self.session.flush()
        return self.get_render(render.id)

    def get_render(self, render_id: int) -> FinalRenderAsset:
        render = self.session.scalar(
            select(FinalRenderAsset)
            .where(FinalRenderAsset.id == render_id)
            .options(*self._render_options())
        )
        if render is None:
            raise NotFoundError(f"Final render {render_id} not found")
        return render

    def reusable_render(self, cache_key: str) -> FinalRenderAsset | None:
        return self.session.scalar(
            select(FinalRenderAsset)
            .where(
                FinalRenderAsset.cache_key == cache_key,
                FinalRenderAsset.status.in_(("VALIDATED", "NEEDS_REVIEW", "APPROVED")),
            )
            .options(*self._render_options())
            .order_by(FinalRenderAsset.id.desc())
            .limit(1)
        )


def caption_plan_read(plan: CaptionPlan) -> CaptionPlanRead:
    return CaptionPlanRead(
        id=plan.id,
        render_asset_id=plan.render_asset_id,
        script_id=plan.script_id,
        narration_id=plan.narration_id,
        language=plan.language,
        target_platform=plan.target_platform,
        style_profile=plan.style_profile,
        segmentation_strategy=plan.segmentation_strategy,
        timing_method=plan.timing_method,
        emphasis_mode=plan.emphasis_mode,
        safe_area=plan.safe_area,
        planner_version=plan.planner_version,
        warnings=plan.warnings,
        cache_key=plan.cache_key,
        created_at=plan.created_at,
        items=[CaptionItemRead.model_validate(item) for item in plan.items],
        overlays=[GraphicOverlayRead.model_validate(item) for item in plan.overlays],
    )


def final_render_read(render: FinalRenderAsset) -> FinalRenderAssetRead:
    return FinalRenderAssetRead(
        id=render.id,
        caption_plan_id=render.caption_plan_id,
        raw_render_id=render.raw_render_id,
        output_path=render.output_path,
        ass_path=render.ass_path,
        srt_path=render.srt_path,
        preview_path=render.preview_path,
        width=render.width,
        height=render.height,
        fps=render.fps,
        duration=render.duration,
        video_codec=render.video_codec,
        audio_codec=render.audio_codec,
        file_size_bytes=render.file_size_bytes,
        checksum_sha256=render.checksum_sha256,
        render_version=render.render_version,
        subtitle_renderer_version=render.subtitle_renderer_version,
        ffmpeg_version=render.ffmpeg_version,
        status=render.status,
        warnings=render.warnings,
        cache_key=render.cache_key,
        failure_reason=render.failure_reason,
        process_exit_code=render.process_exit_code,
        created_at=render.created_at,
        reviewed_at=render.reviewed_at,
        reviewed_by=render.reviewed_by,
        review_notes=render.review_notes,
        reviews=[FinalRenderReviewRead.model_validate(item) for item in render.reviews],
    )
