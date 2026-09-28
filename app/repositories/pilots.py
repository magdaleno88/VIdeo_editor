from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.errors import ConflictError, NotFoundError
from app.models import FinalRenderQualityReview, PilotBatch, PilotRun
from app.schemas.domain import (
    FinalRenderQualityReviewRead,
    PilotBatchRead,
)


class PilotRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_batch(self, batch: PilotBatch) -> PilotBatch:
        try:
            with self.session.begin_nested():
                self.session.add(batch)
                self.session.flush()
        except IntegrityError as exc:
            raise ConflictError(f"Pilot batch slug '{batch.slug}' already exists") from exc
        return self.get_batch(batch.id)

    def get_batch(self, batch_id: int) -> PilotBatch:
        batch = self.session.scalar(
            select(PilotBatch)
            .where(PilotBatch.id == batch_id)
            .options(selectinload(PilotBatch.runs).selectinload(PilotRun.candidate))
        )
        if batch is None:
            raise NotFoundError(f"Pilot batch {batch_id} was not found")
        return batch

    def list_batches(self, limit: int, offset: int) -> tuple[list[PilotBatch], int]:
        from sqlalchemy import func

        total = self.session.scalar(select(func.count()).select_from(PilotBatch)) or 0
        items = list(
            self.session.scalars(
                select(PilotBatch)
                .options(selectinload(PilotBatch.runs))
                .order_by(PilotBatch.created_at.desc(), PilotBatch.id.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        return items, int(total)

    def add_candidate(self, batch_id: int, candidate_id: int) -> PilotRun:
        batch = self.get_batch(batch_id)
        existing = self.session.scalar(
            select(PilotRun).where(
                PilotRun.batch_id == batch_id, PilotRun.candidate_id == candidate_id
            )
        )
        if existing:
            return existing
        run = PilotRun(batch_id=batch.id, candidate_id=candidate_id)
        self.session.add(run)
        self.session.flush()
        return run

    def batches_for_candidate(self, candidate_id: int) -> list[PilotBatch]:
        return list(
            self.session.scalars(
                select(PilotBatch)
                .join(PilotRun, PilotRun.batch_id == PilotBatch.id)
                .where(PilotRun.candidate_id == candidate_id)
                .order_by(PilotBatch.created_at.desc())
            )
        )

    def add_quality_review(self, review: FinalRenderQualityReview) -> FinalRenderQualityReview:
        self.session.add(review)
        self.session.flush()
        return review

    def quality_reviews(self, final_render_id: int) -> list[FinalRenderQualityReview]:
        return list(
            self.session.scalars(
                select(FinalRenderQualityReview)
                .where(FinalRenderQualityReview.final_render_id == final_render_id)
                .order_by(
                    FinalRenderQualityReview.created_at.desc(),
                    FinalRenderQualityReview.id.desc(),
                )
            )
        )


def pilot_batch_read(batch: PilotBatch) -> PilotBatchRead:
    return PilotBatchRead(
        id=batch.id,
        name=batch.name,
        slug=batch.slug,
        description=batch.description,
        created_at=batch.created_at,
        candidate_ids=[run.candidate_id for run in batch.runs],
    )


def quality_review_read(review: FinalRenderQualityReview) -> FinalRenderQualityReviewRead:
    return FinalRenderQualityReviewRead.model_validate(review)
