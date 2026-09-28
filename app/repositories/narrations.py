from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import NotFoundError
from app.models import NarrationAsset, VoiceProfile
from app.schemas.domain import (
    AlignmentMethod,
    NarrationAlignmentRead,
    NarrationAssetRead,
    NarrationBeatTiming,
    NarrationReviewRead,
    VoiceProfileRead,
)


class NarrationRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    @staticmethod
    def _options():
        return (
            selectinload(NarrationAsset.voice_profile),
            selectinload(NarrationAsset.alignments),
            selectinload(NarrationAsset.reviews),
        )

    def get(self, narration_id: int) -> NarrationAsset:
        item = self.session.scalar(
            select(NarrationAsset)
            .where(NarrationAsset.id == narration_id)
            .options(*self._options())
        )
        if item is None:
            raise NotFoundError(f"Narration {narration_id} not found")
        return item

    def list(self, script_id: int) -> list[NarrationAsset]:
        return list(
            self.session.scalars(
                select(NarrationAsset)
                .where(NarrationAsset.script_id == script_id)
                .options(*self._options())
                .order_by(NarrationAsset.created_at.desc(), NarrationAsset.id.desc())
            )
        )

    def reusable(self, cache_key: str) -> NarrationAsset | None:
        return self.session.scalar(
            select(NarrationAsset)
            .where(NarrationAsset.cache_key == cache_key)
            .options(*self._options())
            .order_by(NarrationAsset.id.desc())
            .limit(1)
        )

    def get_or_create_voice(
        self,
        provider: str,
        voice_id: str,
        name: str,
        language: str,
        model: str,
        settings: dict,
    ) -> VoiceProfile:
        voice = self.session.scalar(
            select(VoiceProfile).where(
                VoiceProfile.provider == provider, VoiceProfile.provider_voice_id == voice_id
            )
        )
        if voice is None:
            voice = VoiceProfile(
                provider=provider,
                provider_voice_id=voice_id,
                name=name,
                language=language,
                model=model,
                settings=settings,
                enabled=True,
            )
            self.session.add(voice)
            self.session.flush()
        return voice

    def add(self, narration: NarrationAsset) -> NarrationAsset:
        self.session.add(narration)
        self.session.flush()
        return self.get(narration.id)


def narration_read(item: NarrationAsset) -> NarrationAssetRead:
    alignments = [NarrationAlignmentRead.model_validate(row) for row in item.alignments]
    grouped: dict[int, list[NarrationAlignmentRead]] = {}
    for row in alignments:
        grouped.setdefault(row.beat_id, []).append(row)
    beat_timings = [
        NarrationBeatTiming(
            beat_id=beat_id,
            start_seconds=min(row.start_seconds for row in rows),
            end_seconds=max(row.end_seconds for row in rows),
            method=(
                rows[0].method
                if all(row.method == rows[0].method for row in rows)
                else AlignmentMethod.ESTIMATED_ALIGNMENT
            ),
        )
        for beat_id, rows in sorted(grouped.items())
    ]
    return NarrationAssetRead(
        id=item.id,
        script_id=item.script_id,
        voice_profile=VoiceProfileRead.model_validate(item.voice_profile),
        provider=item.provider,
        model=item.model,
        voice_settings=item.voice_settings,
        source_text_hash=item.source_text_hash,
        source_character_count=item.source_character_count,
        audio_format=item.audio_format,
        codec=item.codec,
        sample_rate=item.sample_rate,
        channels=item.channels,
        duration_seconds=item.duration_seconds,
        estimated_duration_seconds=item.estimated_duration_seconds,
        duration_difference_seconds=item.duration_difference_seconds,
        duration_difference_percent=item.duration_difference_percent,
        duration_status=item.duration_status,
        file_size_bytes=item.file_size_bytes,
        checksum_sha256=item.checksum_sha256,
        storage_path=item.storage_path,
        generation_version=item.generation_version,
        provider_metadata=item.provider_metadata,
        status=item.status,
        alignment_method=item.alignment_method,
        created_at=item.created_at,
        reviewed_at=item.reviewed_at,
        reviewed_by=item.reviewed_by,
        review_notes=item.review_notes,
        alignments=alignments,
        beat_timings=beat_timings,
        reviews=[NarrationReviewRead.model_validate(row) for row in item.reviews],
    )
