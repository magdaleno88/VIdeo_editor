from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import NotFoundError
from app.models import ScriptBeat, ScriptDraft, ScriptSentence
from app.schemas.domain import (
    ScriptBeatRead,
    ScriptDraftRead,
    ScriptReviewRead,
    ScriptSentenceRead,
)


def _options():
    sentences = selectinload(ScriptDraft.beats).selectinload(ScriptBeat.sentences)
    return (
        sentences.selectinload(ScriptSentence.claim_refs),
        sentences.selectinload(ScriptSentence.visual_refs),
        selectinload(ScriptDraft.reviews),
    )


class ScriptRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, script: ScriptDraft) -> ScriptDraft:
        self.session.add(script)
        self.session.flush()
        return self.get(script.id)

    def get(self, script_id: int) -> ScriptDraft:
        script = self.session.scalar(
            select(ScriptDraft).options(*_options()).where(ScriptDraft.id == script_id)
        )
        if script is None:
            raise NotFoundError(f"Script {script_id} not found")
        return script

    def list(self, candidate_id: int) -> list[ScriptDraft]:
        return list(
            self.session.scalars(
                select(ScriptDraft)
                .options(*_options())
                .where(ScriptDraft.candidate_id == candidate_id)
                .order_by(ScriptDraft.created_at.desc(), ScriptDraft.id.desc())
            )
        )

    def reusable(self, cache_key: str, variants: int) -> list[ScriptDraft]:
        latest = self.session.scalar(
            select(ScriptDraft)
            .where(ScriptDraft.cache_key == cache_key)
            .order_by(ScriptDraft.created_at.desc(), ScriptDraft.id.desc())
        )
        if latest is None:
            return []
        return list(
            self.session.scalars(
                select(ScriptDraft)
                .options(*_options())
                .where(ScriptDraft.batch_key == latest.batch_key)
                .order_by(ScriptDraft.variant_index)
                .limit(variants)
            )
        )


def script_read(script: ScriptDraft) -> ScriptDraftRead:
    return ScriptDraftRead(
        id=script.id,
        candidate_id=script.candidate_id,
        research_dossier_id=script.research_dossier_id,
        visual_evaluation_id=script.visual_evaluation_id,
        status=script.status,
        style=script.style,
        target_platform=script.target_platform,
        language=script.language,
        target_duration_seconds=script.target_duration_seconds,
        estimated_duration_seconds=script.estimated_duration_seconds,
        word_count=script.word_count,
        speaking_rate=script.speaking_rate,
        title=script.title,
        hook=script.hook,
        closing=script.closing,
        full_narration=script.full_narration,
        warnings=script.warnings,
        provider=script.provider,
        model=script.model,
        prompt_version=script.prompt_version,
        script_version=script.script_version,
        variant_index=script.variant_index,
        created_at=script.created_at,
        reviewed_at=script.reviewed_at,
        reviewed_by=script.reviewed_by,
        review_notes=script.review_notes,
        beats=[
            ScriptBeatRead(
                id=beat.id,
                position=beat.position,
                kind=beat.kind,
                recommended_start=beat.recommended_start,
                recommended_end=beat.recommended_end,
                visual_direction=beat.visual_direction,
                sentences=[
                    ScriptSentenceRead(
                        id=sentence.id,
                        position=sentence.position,
                        display_text=sentence.display_text,
                        spoken_text=sentence.spoken_text,
                        factuality_type=sentence.factuality_type,
                        claim_ids=[item.claim_id for item in sentence.claim_refs],
                        visual_refs=[item.visual_ref for item in sentence.visual_refs],
                    )
                    for sentence in beat.sentences
                ],
            )
            for beat in script.beats
        ],
        reviews=[ScriptReviewRead.model_validate(item) for item in script.reviews],
    )
