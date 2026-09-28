import hashlib
import json
import re
import uuid

from app.core.database import utcnow
from app.core.errors import ConflictError, NarrationValidationError
from app.models import NarrationAlignment, NarrationAsset, NarrationReview
from app.providers.tts.base import CharacterAlignment, TextToSpeechProvider
from app.repositories.candidates import CandidateRepository
from app.repositories.narrations import NarrationRepository, narration_read
from app.repositories.scripts import ScriptRepository
from app.schemas.domain import (
    AlignmentMethod,
    DurationStatus,
    NarrationRequest,
    NarrationResponse,
    NarrationReviewRequest,
    NarrationStatus,
    ScriptStatus,
)
from app.services.narration.audio import AudioStorage, inspect_audio, sha256

NARRATION_VERSION = "1.0"


class NarrationService:
    def __init__(
        self,
        candidates: CandidateRepository,
        scripts: ScriptRepository,
        narrations: NarrationRepository,
        provider: TextToSpeechProvider,
        storage: AudioStorage,
        *,
        default_voice_id: str,
        default_voice_name: str,
        max_characters: int,
        max_file_size_bytes: int,
        duration_tolerance: float,
    ) -> None:
        self.candidates = candidates
        self.scripts = scripts
        self.narrations = narrations
        self.provider = provider
        self.storage = storage
        self.default_voice_id = default_voice_id
        self.default_voice_name = default_voice_name
        self.max_characters = max_characters
        self.max_file_size_bytes = max_file_size_bytes
        self.duration_tolerance = duration_tolerance

    def generate(self, script_id: int, request: NarrationRequest) -> NarrationResponse:
        script = self.scripts.get(script_id)
        if script.status != ScriptStatus.APPROVED:
            raise ConflictError("Only a human-approved script can generate narration")
        if request.provider != self.provider.name:
            raise ConflictError("Requested narration provider is unavailable")
        text, sentence_spans = self._spoken_text(script)
        if len(text) > self.max_characters:
            raise NarrationValidationError("Narration text exceeds the configured character limit")
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        voice_id = request.voice_id or self.default_voice_id
        if not voice_id:
            raise ConflictError("A voice ID is required for narration")
        settings = request.settings.supported_values()
        voice = self.narrations.get_or_create_voice(
            self.provider.name,
            voice_id,
            request.voice_name or self.default_voice_name,
            script.language,
            self.provider.model,
            settings,
        )
        if not voice.enabled:
            raise ConflictError("Selected voice profile is disabled")
        cache_key = self._cache_key(script.id, text_hash, voice_id, settings)
        if not request.force:
            existing = self.narrations.reusable(cache_key)
            if existing is not None:
                return NarrationResponse(narration=narration_read(existing), reused=True)
        generated = self.provider.synthesize(text, voice_id, script.language, request.settings)
        if len(generated.audio_bytes) > self.max_file_size_bytes:
            raise NarrationValidationError("Narration audio exceeds the configured file size limit")
        metadata = inspect_audio(generated.audio_bytes, generated.audio_format)
        path = self.storage.write(
            script.candidate_id,
            script.id,
            str(uuid.uuid4()),
            generated.audio_bytes,
            metadata.audio_format,
        )
        try:
            difference = metadata.duration_seconds - script.estimated_duration_seconds
            percentage = abs(difference) / script.estimated_duration_seconds
            duration_status = (
                DurationStatus.WITHIN_TARGET
                if percentage <= self.duration_tolerance
                else DurationStatus.TOO_SHORT
                if difference < 0
                else DurationStatus.TOO_LONG
            )
            alignments, alignment_method = self._align(
                script,
                text,
                sentence_spans,
                metadata.duration_seconds,
                generated.character_alignment,
            )
            narration = NarrationAsset(
                script_id=script.id,
                voice_profile_id=voice.id,
                provider=self.provider.name,
                model=self.provider.model,
                voice_settings=settings,
                source_text_hash=text_hash,
                source_character_count=len(text),
                audio_format=metadata.audio_format,
                codec=metadata.codec,
                sample_rate=metadata.sample_rate,
                channels=metadata.channels,
                duration_seconds=metadata.duration_seconds,
                estimated_duration_seconds=script.estimated_duration_seconds,
                duration_difference_seconds=difference,
                duration_difference_percent=percentage,
                duration_status=duration_status,
                file_size_bytes=len(generated.audio_bytes),
                checksum_sha256=sha256(generated.audio_bytes),
                storage_path=path,
                generation_version=NARRATION_VERSION,
                provider_metadata=generated.provider_metadata,
                status=(
                    NarrationStatus.VALIDATED
                    if duration_status == DurationStatus.WITHIN_TARGET
                    else NarrationStatus.NEEDS_REVIEW
                ),
                alignment_method=alignment_method,
                cache_key=cache_key,
            )
            narration.alignments.extend(alignments)
            saved = self.narrations.add(narration)
        except Exception:
            self.storage.delete(path)
            raise
        candidate = self.candidates.get(script.candidate_id)
        self.candidates.record_event(
            candidate,
            "NARRATION_GENERATED",
            "system",
            "Explicit narration generation from approved script",
            {"narration_id": saved.id, "script_id": script.id},
        )
        return NarrationResponse(narration=narration_read(saved), reused=False)

    @staticmethod
    def _spoken_text(script) -> tuple[str, list[tuple[object, int, int]]]:
        parts = []
        spans = []
        cursor = 0
        for beat in script.beats:
            for sentence in beat.sentences:
                value = sentence.spoken_text.strip()
                if not value:
                    raise NarrationValidationError("Approved script contains missing spoken text")
                if parts:
                    cursor += 1
                start = cursor
                parts.append(value)
                cursor += len(value)
                spans.append((sentence, start, cursor))
        text = "\n".join(parts)
        if not text.strip():
            raise NarrationValidationError("Approved script has no spoken narration")
        return text, spans

    @staticmethod
    def _cache_key(script_id: int, text_hash: str, voice_id: str, settings: dict) -> str:
        serialized_settings = json.dumps(settings, sort_keys=True, separators=(",", ":"))
        value = f"{script_id}|{text_hash}|{voice_id}|{serialized_settings}|{NARRATION_VERSION}"
        return hashlib.sha256(value.encode()).hexdigest()

    def _align(self, script, text, spans, duration, alignment: CharacterAlignment | None):
        if alignment and self._is_usable_alignment(text, alignment, duration):
            rows = []
            for sentence, start, end in spans:
                word_timings = [
                    {
                        "text": match.group(),
                        "start_seconds": alignment.starts[start + match.start()],
                        "end_seconds": alignment.ends[start + match.end() - 1],
                    }
                    for match in re.finditer(r"\S+", text[start:end])
                ]
                rows.append(
                    NarrationAlignment(
                        sentence_id=sentence.id,
                        beat_id=sentence.beat_id,
                        start_seconds=alignment.starts[start],
                        end_seconds=alignment.ends[end - 1],
                        method=AlignmentMethod.PROVIDER_ALIGNMENT,
                        confidence=1.0,
                        word_timings=word_timings,
                    )
                )
            return rows, AlignmentMethod.PROVIDER_ALIGNMENT
        weights = [max(end - start, 1) for _, start, end in spans]
        total = sum(weights)
        cursor = 0.0
        rows = []
        for (sentence, _, _), weight in zip(spans, weights, strict=True):
            end = cursor + duration * weight / total
            rows.append(
                NarrationAlignment(
                    sentence_id=sentence.id,
                    beat_id=sentence.beat_id,
                    start_seconds=cursor,
                    end_seconds=end,
                    method=AlignmentMethod.ESTIMATED_ALIGNMENT,
                    confidence=0.4,
                    word_timings=[],
                )
            )
            cursor = end
        return rows, AlignmentMethod.ESTIMATED_ALIGNMENT

    @staticmethod
    def _is_usable_alignment(text: str, alignment: CharacterAlignment, duration: float) -> bool:
        return (
            len(alignment.characters) == len(text)
            and "".join(alignment.characters) == text
            and len(alignment.starts) == len(text)
            and len(alignment.ends) == len(text)
            and all(
                0 <= start <= end <= duration + 0.01
                for start, end in zip(alignment.starts, alignment.ends, strict=True)
            )
        )


class NarrationReviewService:
    def __init__(
        self,
        candidates: CandidateRepository,
        scripts: ScriptRepository,
        narrations: NarrationRepository,
    ) -> None:
        self.candidates = candidates
        self.scripts = scripts
        self.narrations = narrations

    def list(self, script_id: int):
        self.scripts.get(script_id)
        return [narration_read(item) for item in self.narrations.list(script_id)]

    def get(self, narration_id: int):
        return narration_read(self.narrations.get(narration_id))

    def review(self, narration_id: int, review: NarrationReviewRequest):
        narration = self.narrations.get(narration_id)
        target = NarrationStatus(review.decision)
        if target == NarrationStatus.APPROVED and narration.status != NarrationStatus.VALIDATED:
            raise ConflictError("Only a validated narration can be approved")
        narration.status = target
        narration.reviewed_at = utcnow()
        narration.reviewed_by = review.reviewer
        narration.review_notes = review.notes
        narration.reviews.append(
            NarrationReview(decision=review.decision, reviewer=review.reviewer, notes=review.notes)
        )
        script = self.scripts.get(narration.script_id)
        candidate = self.candidates.get(script.candidate_id)
        self.candidates.record_event(
            candidate,
            f"NARRATION_{review.decision}",
            review.reviewer,
            review.notes,
            {"narration_id": narration.id, "script_id": narration.script_id},
        )
        self.narrations.session.flush()
        return narration_read(self.narrations.get(narration.id))
