from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import ResearchClaim, ResearchClaimEvidence, ResearchDossier
from app.schemas.domain import (
    ClaimEvidenceRead,
    ResearchClaimRead,
    ResearchContradictionRead,
    ResearchDossierRead,
    ResearchSourceRead,
    ResearchStatus,
)


class ResearchRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    @staticmethod
    def _options():
        return (
            selectinload(ResearchDossier.sources),
            selectinload(ResearchDossier.claims)
            .selectinload(ResearchClaim.evidence)
            .selectinload(ResearchClaimEvidence.source),
            selectinload(ResearchDossier.contradictions),
        )

    def get(self, dossier_id: int) -> ResearchDossier | None:
        return self.session.scalar(
            select(ResearchDossier)
            .where(ResearchDossier.id == dossier_id)
            .options(*self._options())
        )

    def list(self, candidate_id: int) -> list[ResearchDossier]:
        return list(
            self.session.scalars(
                select(ResearchDossier)
                .where(ResearchDossier.candidate_id == candidate_id)
                .options(*self._options())
                .order_by(ResearchDossier.id)
            )
        )

    def latest(self, candidate_id: int) -> ResearchDossier | None:
        return self.session.scalar(
            select(ResearchDossier)
            .where(ResearchDossier.candidate_id == candidate_id)
            .options(*self._options())
            .order_by(ResearchDossier.id.desc())
            .limit(1)
        )

    def reusable(
        self,
        candidate_id: int,
        search_provider: str,
        research_provider: str,
        model: str,
        prompt_version: str,
        research_version: str,
    ) -> ResearchDossier | None:
        return self.session.scalar(
            select(ResearchDossier)
            .where(
                ResearchDossier.candidate_id == candidate_id,
                ResearchDossier.search_provider == search_provider,
                ResearchDossier.research_provider == research_provider,
                ResearchDossier.model == model,
                ResearchDossier.prompt_version == prompt_version,
                ResearchDossier.research_version == research_version,
                ResearchDossier.status.in_((ResearchStatus.NEEDS_REVIEW, ResearchStatus.VERIFIED)),
            )
            .options(*self._options())
            .order_by(ResearchDossier.id.desc())
            .limit(1)
        )

    def add(self, dossier: ResearchDossier) -> ResearchDossier:
        self.session.add(dossier)
        self.session.flush()
        return dossier


def dossier_read(dossier: ResearchDossier) -> ResearchDossierRead:
    return ResearchDossierRead(
        id=dossier.id,
        candidate_id=dossier.candidate_id,
        status=dossier.status,
        detected_object=dossier.detected_object,
        detected_process=dossier.detected_process,
        visual_observations=dossier.visual_observations,
        alternate_process_hypotheses=dossier.alternate_process_hypotheses,
        research_questions=dossier.research_questions,
        search_queries=dossier.search_queries,
        research_summary=dossier.research_summary,
        verified_process_name=dossier.verified_process_name,
        process_confidence=dossier.process_confidence,
        material=dossier.material,
        machine_types=dossier.machine_types,
        process_steps=dossier.process_steps,
        technical_explanations=dossier.technical_explanations,
        interesting_facts=dossier.interesting_facts,
        safety_notes=dossier.safety_notes,
        unknowns=dossier.unknowns,
        search_provider=dossier.search_provider,
        research_provider=dossier.research_provider,
        model=dossier.model,
        prompt_version=dossier.prompt_version,
        research_version=dossier.research_version,
        created_at=dossier.created_at,
        reviewed_at=dossier.reviewed_at,
        reviewed_by=dossier.reviewed_by,
        review_notes=dossier.review_notes,
        sources=[ResearchSourceRead.model_validate(item) for item in dossier.sources],
        claims=[
            ResearchClaimRead(
                id=claim.id,
                statement=claim.statement,
                category=claim.category,
                status=claim.status,
                knowledge_type=claim.knowledge_type,
                source_support_confidence=claim.source_support_confidence,
                video_applicability_confidence=claim.video_applicability_confidence,
                quantitative=claim.quantitative,
                notes=claim.notes,
                evidence=[
                    ClaimEvidenceRead(
                        id=item.id,
                        source_id=item.source_id,
                        source_url=item.source.url,
                        relation=item.relation,
                        excerpt=item.excerpt,
                        location=item.location,
                        source_says_confidence=item.source_says_confidence,
                    )
                    for item in claim.evidence
                ],
            )
            for claim in dossier.claims
        ],
        contradictions=[
            ResearchContradictionRead.model_validate(item) for item in dossier.contradictions
        ],
    )
