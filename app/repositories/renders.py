from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import NotFoundError
from app.models import RenderAsset, VideoEditPlan
from app.schemas.domain import (
    EditSegmentRead,
    RenderAssetRead,
    RenderReviewRead,
    VideoEditPlanRead,
)


class RenderRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    @staticmethod
    def _plan_options():
        return (selectinload(VideoEditPlan.segments),)

    @staticmethod
    def _render_options():
        return (selectinload(RenderAsset.reviews),)

    def add_plan(self, plan: VideoEditPlan) -> VideoEditPlan:
        self.session.add(plan)
        self.session.flush()
        return self.get_plan(plan.id)

    def get_plan(self, plan_id: int) -> VideoEditPlan:
        plan = self.session.scalar(
            select(VideoEditPlan).where(VideoEditPlan.id == plan_id).options(*self._plan_options())
        )
        if plan is None:
            raise NotFoundError(f"Render plan {plan_id} not found")
        return plan

    def list_plans(self, candidate_id: int) -> list[VideoEditPlan]:
        return list(
            self.session.scalars(
                select(VideoEditPlan)
                .where(VideoEditPlan.candidate_id == candidate_id)
                .options(*self._plan_options())
                .order_by(VideoEditPlan.created_at.desc(), VideoEditPlan.id.desc())
            )
        )

    def reusable_plan(self, cache_key: str) -> VideoEditPlan | None:
        return self.session.scalar(
            select(VideoEditPlan)
            .where(VideoEditPlan.cache_key == cache_key)
            .options(*self._plan_options())
            .order_by(VideoEditPlan.id.desc())
            .limit(1)
        )

    def add_render(self, render: RenderAsset) -> RenderAsset:
        self.session.add(render)
        self.session.flush()
        return self.get_render(render.id)

    def get_render(self, render_id: int) -> RenderAsset:
        render = self.session.scalar(
            select(RenderAsset).where(RenderAsset.id == render_id).options(*self._render_options())
        )
        if render is None:
            raise NotFoundError(f"Render {render_id} not found")
        return render

    def reusable_render(self, cache_key: str) -> RenderAsset | None:
        return self.session.scalar(
            select(RenderAsset)
            .where(
                RenderAsset.cache_key == cache_key,
                RenderAsset.status.in_(("VALIDATED", "NEEDS_REVIEW", "APPROVED")),
            )
            .options(*self._render_options())
            .order_by(RenderAsset.id.desc())
            .limit(1)
        )


def plan_read(plan: VideoEditPlan) -> VideoEditPlanRead:
    return VideoEditPlanRead(
        id=plan.id,
        candidate_id=plan.candidate_id,
        script_id=plan.script_id,
        narration_id=plan.narration_id,
        target_platform=plan.target_platform,
        aspect_ratio=plan.aspect_ratio,
        width=plan.width,
        height=plan.height,
        fps=plan.fps,
        target_duration=plan.target_duration,
        actual_narration_duration=plan.actual_narration_duration,
        composition=plan.composition,
        source_audio_policy=plan.source_audio_policy,
        audio_configuration=plan.audio_configuration,
        render_configuration=plan.render_configuration,
        planner_version=plan.planner_version,
        warnings=plan.warnings,
        cache_key=plan.cache_key,
        created_at=plan.created_at,
        segments=[EditSegmentRead.model_validate(item) for item in plan.segments],
    )


def render_read(render: RenderAsset) -> RenderAssetRead:
    return RenderAssetRead(
        id=render.id,
        candidate_id=render.candidate_id,
        script_id=render.script_id,
        narration_id=render.narration_id,
        edit_plan_id=render.edit_plan_id,
        output_path=render.output_path,
        width=render.width,
        height=render.height,
        fps=render.fps,
        duration=render.duration,
        planned_duration=render.planned_duration,
        duration_difference=render.duration_difference,
        video_codec=render.video_codec,
        audio_codec=render.audio_codec,
        file_size_bytes=render.file_size_bytes,
        checksum_sha256=render.checksum_sha256,
        render_version=render.render_version,
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
        reviews=[RenderReviewRead.model_validate(item) for item in render.reviews],
    )
