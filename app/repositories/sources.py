from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.models import LongFormSource, ShortFormConcept, SourceAnalysis


class SourceRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, source: LongFormSource) -> LongFormSource:
        self.session.add(source)
        self.session.flush()
        return source

    def get(self, source_id: int) -> LongFormSource:
        source = self.session.get(LongFormSource, source_id)
        if source is None:
            raise NotFoundError(f"Source {source_id} was not found")
        return source

    def list(self) -> list[LongFormSource]:
        return list(self.session.scalars(select(LongFormSource).order_by(LongFormSource.id.desc())))

    def by_checksum(self, checksum: str) -> LongFormSource | None:
        return self.session.scalar(
            select(LongFormSource).where(LongFormSource.checksum_sha256 == checksum)
        )

    def latest_analysis(self, source_id: int) -> SourceAnalysis | None:
        self.get(source_id)
        return self.session.scalar(
            select(SourceAnalysis)
            .where(SourceAnalysis.source_id == source_id)
            .order_by(SourceAnalysis.id.desc())
            .limit(1)
        )

    def get_concept(self, concept_id: int) -> ShortFormConcept:
        concept = self.session.get(ShortFormConcept, concept_id)
        if concept is None:
            raise NotFoundError(f"Concept {concept_id} was not found")
        return concept

    def concepts(self, source_id: int) -> list[ShortFormConcept]:
        self.get(source_id)
        return list(
            self.session.scalars(
                select(ShortFormConcept)
                .where(ShortFormConcept.source_id == source_id)
                .order_by(ShortFormConcept.id)
            )
        )
