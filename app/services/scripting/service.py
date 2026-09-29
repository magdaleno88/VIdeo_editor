import hashlib
import json
import re
import uuid

from app.core.database import utcnow
from app.core.errors import ConflictError, ScriptValidationError
from app.models import (
    ScriptBeat,
    ScriptDraft,
    ScriptReview,
    ScriptSentence,
    ScriptSentenceClaim,
    ScriptSentenceVisualRef,
)
from app.providers.scripting.base import ScriptGenerationProvider
from app.repositories.candidates import CandidateRepository
from app.repositories.research import ResearchRepository
from app.repositories.scripts import ScriptRepository, script_read
from app.schemas.domain import (
    ClaimStatus,
    ResearchStatus,
    ScriptDraftRead,
    ScriptFactualityType,
    ScriptGenerationRequest,
    ScriptGenerationResponse,
    ScriptReviewRequest,
    ScriptStatus,
    VideoVisualAnalysis,
)

SCRIPT_VERSION = "1.0"
WORD_PATTERN = re.compile(r"\b[\wÁÉÍÓÚÜÑáéíóúüñ]+\b", re.UNICODE)
NUMBER_PATTERN = re.compile(r"\d")
CAUTION_PATTERN = re.compile(r"\b(puede|podría|probablemente|posiblemente|parece|según)\b", re.I)
CLICKBAIT_PATTERNS = (
    re.compile(r"\b(más peligrosa del mundo|cambiará tu vida|es imposible)\b", re.I),
    re.compile(r"\b(no creerás|te sorprenderá|nadie quiere que sepas)\b", re.I),
)


class ScriptFactPolicy:
    def __init__(self, allow_partially_supported: bool, max_claims: int) -> None:
        self.allow_partially_supported = allow_partially_supported
        self.max_claims = max_claims

    def allowed_claims(self, dossier) -> dict[int, dict]:
        contradicted = {
            claim_id
            for item in dossier.contradictions
            if not item.resolved
            for claim_id in item.claim_ids
        }
        allowed = {}
        ordered = sorted(
            dossier.claims,
            key=lambda claim: (
                claim.status != ClaimStatus.VERIFIED,
                -claim.source_support_confidence,
                claim.id,
            ),
        )
        for claim in ordered:
            if claim.id in contradicted:
                continue
            if claim.status == ClaimStatus.VERIFIED:
                pass
            elif not (
                self.allow_partially_supported and claim.status == ClaimStatus.PARTIALLY_SUPPORTED
            ):
                continue
            if claim.quantitative and not any(
                evidence.relation.value == "SUPPORTS" for evidence in claim.evidence
            ):
                continue
            allowed[claim.id] = {
                "id": claim.id,
                "statement": claim.statement,
                "status": claim.status.value,
                "quantitative": claim.quantitative,
                "caution_required": claim.status == ClaimStatus.PARTIALLY_SUPPORTED,
            }
            if len(allowed) >= self.max_claims:
                break
        return allowed


class ScriptGenerationService:
    def __init__(
        self,
        candidates: CandidateRepository,
        research: ResearchRepository,
        scripts: ScriptRepository,
        provider: ScriptGenerationProvider,
        *,
        max_variants: int,
        max_claims: int,
        max_context_characters: int,
        speaking_rate: float,
        duration_tolerance: float,
        allow_partially_supported: bool,
    ) -> None:
        self.candidates = candidates
        self.research = research
        self.scripts = scripts
        self.provider = provider
        self.max_variants = max_variants
        self.max_context_characters = max_context_characters
        self.speaking_rate = speaking_rate
        self.duration_tolerance = duration_tolerance
        self.policy = ScriptFactPolicy(allow_partially_supported, max_claims)

    def generate(
        self, candidate_id: int, request: ScriptGenerationRequest
    ) -> ScriptGenerationResponse:
        candidate = self.candidates.get(candidate_id)
        if request.variants > self.max_variants:
            raise ConflictError(f"At most {self.max_variants} script variants are allowed")
        dossier = self.research.latest(candidate_id)
        if dossier is None or dossier.status != ResearchStatus.VERIFIED:
            raise ConflictError("A human-verified research dossier is required for scripting")
        evaluation = self.candidates.latest_by_method(candidate_id, "ai_visual")
        if evaluation is None:
            evaluation = self.candidates.latest_by_method(candidate_id, "source_structural")
        if evaluation is None or not evaluation.analysis:
            raise ConflictError("AI visual analysis is required for scripting")
        analysis = VideoVisualAnalysis.model_validate(evaluation.analysis)
        claims = self.policy.allowed_claims(dossier)
        if not claims:
            raise ConflictError("The verified dossier has no claims permitted for scripting")
        visuals = self._visual_timeline(analysis)
        if not any(item["kind"] == "HOOK" for item in visuals.values()):
            raise ConflictError("Visual analysis has no hook segment for a visual-first script")
        cache_key = self._cache_key(candidate_id, dossier.id, evaluation.id, request)
        if not request.force:
            existing = self.scripts.reusable(cache_key, request.variants)
            if len(existing) == request.variants:
                return ScriptGenerationResponse(
                    scripts=[script_read(item) for item in existing], reused=True
                )
        context = self._context(candidate, analysis, claims, visuals, request.editorial_context)
        batch_key = str(uuid.uuid4())
        created = []
        for variant_index in range(1, request.variants + 1):
            payload = self.provider.generate(context, request, variant_index)
            validation = self.provider.validate(context, payload)
            draft = self._build_draft(
                candidate_id,
                dossier.id,
                evaluation.id,
                request,
                payload,
                validation,
                claims,
                visuals,
                cache_key,
                batch_key,
                variant_index,
            )
            created.append(self.scripts.add(draft))
        self.candidates.record_event(
            candidate,
            "SCRIPTS_GENERATED",
            "system",
            "Explicit verified script generation",
            {"script_ids": [item.id for item in created], "variants": request.variants},
        )
        return ScriptGenerationResponse(
            scripts=[script_read(item) for item in created], reused=False
        )

    def _context(self, candidate, analysis, claims, visuals, editorial_context: str) -> dict:
        context = {
            "safe_metadata": {
                "title": candidate.title,
                "object": candidate.object_being_manufactured,
                "duration_seconds": candidate.duration,
                "visual_hook_score": candidate.hook_score,
                "visual_summary": analysis.summary,
                "first_seconds": analysis.first_seconds.model_dump(mode="json"),
            },
            "permitted_claims": list(claims.values()),
            "permitted_visuals": list(visuals.values()),
            "editorial_context": editorial_context,
        }
        serialized = json.dumps(context, ensure_ascii=False)
        if len(serialized) > self.max_context_characters:
            raise ConflictError("Permitted script context exceeds configured size limit")
        return context

    @staticmethod
    def _visual_timeline(analysis: VideoVisualAnalysis) -> dict[str, dict]:
        timeline = {}
        for index, item in enumerate(analysis.evidence):
            timeline[f"evidence:{index}"] = {
                "ref": f"evidence:{index}",
                "description": item.observation,
                "start": item.timestamp_start,
                "end": item.timestamp_end,
                "kind": "EVIDENCE",
            }
        for dimension_name, dimension in analysis.dimensions:
            for index, item in enumerate(dimension.evidence):
                ref = f"dimension:{dimension_name}:{index}"
                timeline[ref] = {
                    "ref": ref,
                    "description": item.observation,
                    "start": item.timestamp_start,
                    "end": item.timestamp_end,
                    "kind": "EVIDENCE",
                }
        for label, kind, items in (
            ("hook", "HOOK", analysis.potential_hook_segments),
            ("notable", "NOTABLE", analysis.notable_moments),
            ("loop", "LOOP", analysis.potential_loop_segments),
        ):
            for index, item in enumerate(items):
                timeline[f"{label}:{index}"] = {
                    "ref": f"{label}:{index}",
                    "description": item.reason,
                    "start": item.start,
                    "end": item.end,
                    "kind": kind,
                }
        return timeline

    def _build_draft(
        self,
        candidate_id,
        dossier_id,
        evaluation_id,
        request,
        payload,
        ai_validation,
        claims,
        visuals,
        cache_key,
        batch_key,
        variant_index,
    ) -> ScriptDraft:
        warnings = list(payload.warnings) + list(ai_validation.notes)
        flattened = [sentence for beat in payload.beats for sentence in beat.sentences]
        if len(ai_validation.sentences) != len(flattened) or {
            item.sentence_index for item in ai_validation.sentences
        } != set(range(len(flattened))):
            raise ScriptValidationError(
                "Script validation does not cover every sentence exactly once"
            )
        if ai_validation.external_facts_detected:
            warnings.append("AI validator detected factual content outside the allowlist.")
        if ai_validation.clickbait_detected:
            warnings.append("AI validator detected unsupported clickbait language.")
        unsupported = {
            item.sentence_index for item in ai_validation.sentences if not item.supported
        }
        local_warnings = self._validate_local(payload, claims, visuals, unsupported, request)
        warnings.extend(local_warnings)
        spoken = [sentence.spoken_text for sentence in flattened]
        word_count = sum(len(WORD_PATTERN.findall(item)) for item in spoken)
        estimated = round(word_count / self.speaking_rate * 60, 2)
        deviation = (
            abs(estimated - request.target_duration_seconds) / request.target_duration_seconds
        )
        if deviation > self.duration_tolerance:
            warnings.append(
                "Estimated narration duration is outside the configured target tolerance."
            )
        status = ScriptStatus.NEEDS_REVISION if warnings else ScriptStatus.VALIDATED
        draft = ScriptDraft(
            candidate_id=candidate_id,
            research_dossier_id=dossier_id,
            visual_evaluation_id=evaluation_id,
            status=status,
            style=request.style,
            target_platform=request.target_platform,
            language=request.language,
            target_duration_seconds=request.target_duration_seconds,
            estimated_duration_seconds=estimated,
            word_count=word_count,
            speaking_rate=self.speaking_rate,
            title=payload.title,
            hook=payload.hook,
            closing=payload.closing,
            full_narration=" ".join(item.display_text for item in flattened),
            warnings=list(dict.fromkeys(warnings)),
            provider=self.provider.name,
            model=self.provider.model,
            prompt_version=self.provider.prompt_version,
            script_version=SCRIPT_VERSION,
            variant_index=variant_index,
            cache_key=cache_key,
            batch_key=batch_key,
        )
        for beat_position, beat_data in enumerate(payload.beats):
            beat = ScriptBeat(
                position=beat_position,
                kind=beat_data.kind,
                recommended_start=beat_data.recommended_start,
                recommended_end=beat_data.recommended_end,
                visual_direction=beat_data.visual_direction,
            )
            draft.beats.append(beat)
            for sentence_position, sentence_data in enumerate(beat_data.sentences):
                sentence = ScriptSentence(
                    position=sentence_position,
                    display_text=sentence_data.display_text,
                    spoken_text=sentence_data.spoken_text,
                    factuality_type=sentence_data.factuality_type,
                )
                beat.sentences.append(sentence)
                sentence.claim_refs.extend(
                    ScriptSentenceClaim(claim_id=claim_id)
                    for claim_id in sentence_data.claim_ids
                    if claim_id in claims
                )
                sentence.visual_refs.extend(
                    ScriptSentenceVisualRef(
                        visual_ref=ref, start=visuals[ref]["start"], end=visuals[ref]["end"]
                    )
                    for ref in sentence_data.visual_refs
                    if ref in visuals
                )
        return draft

    def _validate_local(self, payload, claims, visuals, unsupported, request) -> list[str]:
        warnings = []
        seen_text = set()
        used_visuals = set()
        sentence_index = 0
        for beat_index, beat in enumerate(payload.beats):
            beat_refs = set()
            for sentence in beat.sentences:
                text_key = " ".join(sentence.display_text.casefold().split())
                if text_key in seen_text:
                    warnings.append("Repeated narration sentence detected.")
                seen_text.add(text_key)
                unknown_claims = set(sentence.claim_ids) - set(claims)
                unknown_visuals = set(sentence.visual_refs) - set(visuals)
                if unknown_claims or unknown_visuals:
                    warnings.append("Sentence references evidence outside the factual allowlist.")
                if (
                    sentence.factuality_type == ScriptFactualityType.VERIFIED_FACT
                    and not sentence.claim_ids
                ):
                    warnings.append("Verified factual sentence lacks a claim reference.")
                if (
                    sentence.factuality_type == ScriptFactualityType.OBSERVATION
                    and not sentence.visual_refs
                ):
                    warnings.append("Visual observation lacks a visual reference.")
                if sentence.factuality_type == ScriptFactualityType.INFERENCE and (
                    not sentence.visual_refs or not CAUTION_PATTERN.search(sentence.display_text)
                ):
                    warnings.append("Inference lacks visual support or cautious wording.")
                if NUMBER_PATTERN.search(
                    sentence.display_text + " " + sentence.spoken_text
                ) and not any(
                    claims[item]["quantitative"] for item in sentence.claim_ids if item in claims
                ):
                    warnings.append("Numeric sentence lacks a permitted quantitative claim.")
                if any(
                    claims[item]["caution_required"]
                    and not CAUTION_PATTERN.search(sentence.display_text)
                    for item in sentence.claim_ids
                    if item in claims
                ):
                    warnings.append("Partially supported claim lacks cautious wording.")
                if any(pattern.search(sentence.display_text) for pattern in CLICKBAIT_PATTERNS):
                    warnings.append("Prohibited clickbait pattern detected.")
                if sentence_index in unsupported:
                    warnings.append("AI validator found an unsupported sentence.")
                sentence_index += 1
                beat_refs.update(ref for ref in sentence.visual_refs if ref in visuals)
            if not beat_refs:
                warnings.append("Beat lacks visual alignment.")
            elif not any(
                visuals[ref]["start"] < beat.recommended_end
                and visuals[ref]["end"] > beat.recommended_start
                for ref in beat_refs
            ):
                warnings.append("Beat timestamps do not overlap their referenced visuals.")
            if beat_index == 0 and not any(visuals[ref]["kind"] == "HOOK" for ref in beat_refs):
                warnings.append("Opening beat does not use a permitted hook segment.")
            if used_visuals.intersection(beat_refs):
                warnings.append("Visual segment is repeated across beats.")
            used_visuals.update(beat_refs)
        if not payload.hook.strip():
            warnings.append("Hook is empty.")
        if request.language != "es":
            warnings.append("Only Spanish scripts are currently supported.")
        return list(dict.fromkeys(warnings))

    def _cache_key(self, candidate_id, dossier_id, evaluation_id, request) -> str:
        value = {
            "candidate": candidate_id,
            "dossier": dossier_id,
            "evaluation": evaluation_id,
            "style": request.style.value,
            "platform": request.target_platform.value,
            "language": request.language,
            "duration": request.target_duration_seconds,
            "variants": request.variants,
            "editorial_context": request.editorial_context,
            "provider": self.provider.name,
            "model": self.provider.model,
            "prompt": self.provider.prompt_version,
            "version": SCRIPT_VERSION,
        }
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class ScriptReviewService:
    def __init__(self, candidates: CandidateRepository, scripts: ScriptRepository) -> None:
        self.candidates = candidates
        self.scripts = scripts

    def list(self, candidate_id: int) -> list[ScriptDraftRead]:
        self.candidates.get(candidate_id)
        return [script_read(item) for item in self.scripts.list(candidate_id)]

    def get(self, script_id: int) -> ScriptDraftRead:
        return script_read(self.scripts.get(script_id))

    def review(self, script_id: int, review: ScriptReviewRequest) -> ScriptDraftRead:
        script = self.scripts.get(script_id)
        candidate = self.candidates.get(script.candidate_id)
        target = ScriptStatus(review.decision)
        if target == ScriptStatus.APPROVED and script.status != ScriptStatus.VALIDATED:
            raise ConflictError("Only a validated script can be approved")
        script.status = target
        script.reviewed_at = utcnow()
        script.reviewed_by = review.reviewer
        script.review_notes = review.notes
        script.reviews.append(
            ScriptReview(
                decision=review.decision,
                reviewer=review.reviewer,
                notes=review.notes,
            )
        )
        self.candidates.record_event(
            candidate,
            f"SCRIPT_{review.decision}",
            review.reviewer,
            review.notes,
            {"script_id": script.id},
        )
        self.scripts.session.flush()
        return script_read(self.scripts.get(script.id))
