import hashlib
import json
import re
from pathlib import Path

from sqlalchemy import select

from app.core.database import utcnow
from app.core.errors import (
    ConflictError,
    RenderConfigurationError,
    RenderProcessError,
    RenderValidationError,
)
from app.models import (
    CaptionItem,
    CaptionPlan,
    FinalRenderAsset,
    FinalRenderReview,
    GraphicOverlay,
    ResearchClaim,
    VideoCandidate,
)
from app.providers.captions import ASSSubtitleRenderer, CaptionFFmpegRenderer, SRTSubtitleRenderer
from app.providers.captions.ass import emphasis_spans
from app.repositories.candidates import CandidateRepository
from app.repositories.captions import CaptionRepository, caption_plan_read, final_render_read
from app.repositories.narrations import NarrationRepository
from app.repositories.renders import RenderRepository
from app.repositories.scripts import ScriptRepository
from app.schemas.domain import (
    AlignmentMethod,
    CaptionPlanRequest,
    CaptionPosition,
    CaptionStyleProfile,
    CaptionTimingMethod,
    ClaimStatus,
    EmphasisMode,
    FinalRenderResponse,
    FinalRenderReviewRequest,
    FinalRenderStatus,
    GraphicOverlayType,
    NarrationStatus,
    RenderStatus,
)
from app.services.captions.planning import segment_caption
from app.services.captions.storage import CaptionStorage
from app.services.rendering.storage import sha256_file
from app.services.rights.policy import is_cleared_for_commercial_publication

PLANNER_VERSION = "caption-planner-1.0"
FINAL_RENDER_VERSION = "final-render-1.0"

STYLE_CONFIGURATION = {
    CaptionStyleProfile.CLEAN: {
        "font_weight": 700,
        "outline": 4,
        "shadow": 2,
        "background_box": True,
        "alignment": "center",
        "capitalization": "preserve",
    },
    CaptionStyleProfile.BOLD: {
        "font_weight": 700,
        "outline": 5,
        "shadow": 2,
        "background_box": True,
        "alignment": "center",
        "capitalization": "preserve",
    },
    CaptionStyleProfile.MINIMAL: {
        "font_weight": 400,
        "outline": 3,
        "shadow": 1,
        "background_box": False,
        "alignment": "center",
        "capitalization": "preserve",
    },
}


def _hash(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _word(value: str) -> str:
    return re.sub(r"[^\w°%]", "", value, flags=re.UNICODE).casefold()


def _provider_chunks(chunks: list[str], word_timings: list[dict] | None):
    timings = word_timings or []
    normalized = [_word(str(item.get("text", ""))) for item in timings]
    cursor = 0
    result: list[tuple[float, float, list[dict]]] = []
    for chunk in chunks:
        words = [_word(value) for value in chunk.split() if _word(value)]
        if not words or normalized[cursor : cursor + len(words)] != words:
            return None
        selected = timings[cursor : cursor + len(words)]
        result.append(
            (
                float(selected[0]["start_seconds"]),
                float(selected[-1]["end_seconds"]),
                selected,
            )
        )
        cursor += len(words)
    return result if cursor == len(normalized) else None


def configured_file(value: str, suffixes: set[str], label: str) -> Path | None:
    if not value.strip():
        return None
    raw = Path(value.strip())
    if ".." in raw.parts:
        raise RenderConfigurationError(f"{label} path cannot contain parent traversal")
    path = raw.expanduser().resolve()
    if path.suffix.lower() not in suffixes or not path.is_file():
        raise RenderConfigurationError(f"{label} file is missing or unsupported")
    return path


class CaptionPlanService:
    def __init__(
        self,
        renders: RenderRepository,
        scripts: ScriptRepository,
        narrations: NarrationRepository,
        captions: CaptionRepository,
        *,
        default_style: CaptionStyleProfile,
        min_words: int,
        max_words: int,
        max_characters: int,
        max_lines: int,
        linger_ms: int,
        max_characters_per_second: float,
        safe_area: dict[str, float],
        max_emphasis: int,
        word_highlight_enabled: bool,
        branding_enabled: bool,
        branding_channel_name: str,
    ) -> None:
        self.renders = renders
        self.scripts = scripts
        self.narrations = narrations
        self.captions = captions
        self.default_style = CaptionStyleProfile(default_style)
        self.min_words = min_words
        self.max_words = max_words
        self.max_characters = max_characters
        self.max_lines = max_lines
        self.linger_seconds = linger_ms / 1000
        self.max_cps = max_characters_per_second
        self.safe_area = safe_area
        self.max_emphasis = max_emphasis
        self.word_highlight_enabled = word_highlight_enabled
        self.branding_enabled = branding_enabled
        self.branding_channel_name = branding_channel_name.strip()

    def create(self, render_id: int, request: CaptionPlanRequest):
        raw = self.renders.get_render(render_id)
        if raw.status != RenderStatus.APPROVED:
            raise ConflictError("Caption planning requires an approved raw render")
        if raw.duration is None or raw.duration <= 0 or not raw.checksum_sha256:
            raise ConflictError("Approved raw render metadata is incomplete")
        script = self.scripts.get(raw.script_id)
        narration = self.narrations.get(raw.narration_id)
        if narration.script_id != script.id or narration.status != NarrationStatus.APPROVED:
            raise ConflictError("Caption planning requires the matching approved narration")
        requested_emphasis = request.emphasis_mode
        style_profile = request.style_profile or self.default_style
        has_provider_alignment = bool(narration.alignments) and all(
            row.method == AlignmentMethod.PROVIDER_ALIGNMENT for row in narration.alignments
        )
        emphasis = requested_emphasis
        warnings: list[str] = []
        if emphasis == EmphasisMode.WORD and (
            not has_provider_alignment or not self.word_highlight_enabled
        ):
            emphasis = EmphasisMode.PHRASE
            warnings.append(
                "WORD emphasis fell back to PHRASE because precise timing is unavailable"
            )
        timing_method = (
            CaptionTimingMethod.PROVIDER_ALIGNMENT
            if has_provider_alignment
            else CaptionTimingMethod.ESTIMATED_ALIGNMENT
        )
        cache_key = _hash(
            {
                "raw": raw.checksum_sha256,
                "script": script.id,
                "narration": narration.checksum_sha256,
                "style": style_profile,
                "position": request.position,
                "emphasis": emphasis,
                "hook": request.include_hook,
                "claims": sorted(request.factual_claim_ids),
                "settings": [
                    self.min_words,
                    self.max_words,
                    self.max_characters,
                    self.max_lines,
                    self.linger_seconds,
                    self.max_cps,
                    self.safe_area,
                    self.max_emphasis,
                ],
                "version": PLANNER_VERSION,
            }
        )
        if not request.force:
            reused = self.captions.reusable_plan(cache_key)
            if reused:
                return caption_plan_read(reused)

        alignments = {row.sentence_id: row for row in narration.alignments}
        ordered_sentences = [
            (beat, sentence) for beat in script.beats for sentence in beat.sentences
        ]
        if not ordered_sentences:
            raise ConflictError("Approved script has no sentences")
        plan = CaptionPlan(
            render_asset_id=raw.id,
            script_id=script.id,
            narration_id=narration.id,
            language=script.language,
            target_platform=script.target_platform,
            style_profile=style_profile,
            segmentation_strategy="PUNCTUATION_BOUNDED",
            timing_method=timing_method,
            emphasis_mode=emphasis,
            safe_area=self.safe_area,
            planner_version=PLANNER_VERSION,
            warnings=warnings,
            cache_key=cache_key,
        )
        position = 0
        for sentence_index, (beat, sentence) in enumerate(ordered_sentences):
            alignment = alignments.get(sentence.id)
            if alignment is None:
                raise ConflictError(f"Narration alignment is missing sentence {sentence.id}")
            start = max(0.0, alignment.start_seconds)
            end = min(raw.duration, alignment.end_seconds)
            if end <= start:
                raise ConflictError(
                    "Narration alignment contains an invalid final timeline interval"
                )
            chunks = segment_caption(
                sentence.display_text,
                self.min_words,
                self.max_words,
                self.max_characters,
            )
            weights = [max(1, len(chunk.split())) for chunk in chunks]
            total_weight = sum(weights)
            cursor = start
            provider_chunks = (
                _provider_chunks(chunks, alignment.word_timings)
                if emphasis == EmphasisMode.WORD
                and alignment.method == AlignmentMethod.PROVIDER_ALIGNMENT
                else None
            )
            for chunk_index, (chunk, weight) in enumerate(zip(chunks, weights, strict=True)):
                if provider_chunks:
                    cursor, chunk_end, exact_words = provider_chunks[chunk_index]
                else:
                    exact_words = []
                    chunk_end = (
                        end
                        if chunk_index == len(chunks) - 1
                        else cursor + (end - start) * weight / total_weight
                    )
                duration = max(0.05, chunk_end - cursor)
                cps = len(chunk) / duration
                wpm = len(chunk.split()) / duration * 60
                item_warnings: list[str] = []
                if cps > self.max_cps:
                    item_warnings.append(
                        f"Reading speed {cps:.1f} characters/s exceeds {self.max_cps:.1f}"
                    )
                    warnings.append(f"Caption item {position} exceeds configured reading speed")
                if emphasis == EmphasisMode.WORD and provider_chunks:
                    ranked = sorted(
                        exact_words,
                        key=lambda value: -len(_word(str(value.get("text", "")))),
                    )[: self.max_emphasis]
                    item_emphasis = [
                        {
                            "text": str(value["text"]).strip(".,;:!?"),
                            "kind": "WORD",
                            "start_seconds": float(value["start_seconds"]),
                            "end_seconds": float(value["end_seconds"]),
                        }
                        for value in ranked
                        if len(_word(str(value.get("text", "")))) >= 4
                    ]
                elif emphasis == EmphasisMode.WORD:
                    item_emphasis = emphasis_spans(chunk, self.max_emphasis, EmphasisMode.PHRASE)
                    item_warnings.append(
                        "WORD emphasis fell back to PHRASE because display/spoken words differ"
                    )
                    warnings.append(
                        f"Caption item {position} lacks exact display-word provider timing"
                    )
                else:
                    item_emphasis = emphasis_spans(chunk, self.max_emphasis, emphasis)
                plan.items.append(
                    CaptionItem(
                        position=position,
                        start_seconds=round(cursor, 3),
                        end_seconds=round(chunk_end, 3),
                        display_text=chunk,
                        spoken_text=sentence.spoken_text,
                        sentence_id=sentence.id,
                        beat_id=beat.id,
                        timing_method=(
                            CaptionTimingMethod.PROVIDER_ALIGNMENT
                            if alignment.method == AlignmentMethod.PROVIDER_ALIGNMENT
                            else CaptionTimingMethod.ESTIMATED_ALIGNMENT
                        ),
                        position_name=request.position,
                        style={
                            **STYLE_CONFIGURATION[style_profile],
                            "max_characters": self.max_characters,
                            "max_lines": self.max_lines,
                        },
                        emphasis_spans=item_emphasis,
                        characters_per_second=round(cps, 3),
                        words_per_minute=round(wpm, 3),
                        warnings=item_warnings,
                    )
                )
                position += 1
                cursor = chunk_end
            if sentence_index + 1 < len(ordered_sentences):
                next_alignment = alignments.get(ordered_sentences[sentence_index + 1][1].id)
                if next_alignment and next_alignment.start_seconds > end:
                    plan.items[-1].end_seconds = min(
                        next_alignment.start_seconds, end + self.linger_seconds
                    )

        if request.include_hook and script.hook.strip():
            first_beat_id = script.beats[0].id
            plan.overlays.append(
                GraphicOverlay(
                    overlay_type=GraphicOverlayType.HOOK_TEXT,
                    text=script.hook.strip()[:120],
                    start_seconds=0,
                    end_seconds=min(3.0, raw.duration),
                    position_name=CaptionPosition.UPPER,
                    style_profile=style_profile,
                    claim_ids=[],
                    beat_ids=[first_beat_id],
                    z_index=10,
                )
            )
        if request.factual_claim_ids:
            claims = list(
                self.captions.session.scalars(
                    select(ResearchClaim).where(ResearchClaim.id.in_(request.factual_claim_ids))
                )
            )
            if len(claims) != len(set(request.factual_claim_ids)):
                raise ConflictError("Every factual overlay must reference an existing claim")
            for index, claim in enumerate(claims):
                if claim.dossier_id != script.research_dossier_id:
                    raise ConflictError(
                        "Factual overlay claim does not belong to the script dossier"
                    )
                if claim.status != ClaimStatus.VERIFIED:
                    raise ConflictError("Factual overlays require VERIFIED research claims")
                plan.overlays.append(
                    GraphicOverlay(
                        overlay_type=GraphicOverlayType.INFO_LABEL,
                        text=claim.statement[:120],
                        start_seconds=min(raw.duration - 0.1, 3.0 + index * 2.5),
                        end_seconds=min(raw.duration, 5.0 + index * 2.5),
                        position_name=CaptionPosition.UPPER,
                        style_profile=CaptionStyleProfile.MINIMAL,
                        claim_ids=[claim.id],
                        beat_ids=[],
                        z_index=20 + index,
                    )
                )
        if self.branding_enabled and self.branding_channel_name:
            plan.overlays.append(
                GraphicOverlay(
                    overlay_type=GraphicOverlayType.BRANDING,
                    text=self.branding_channel_name[:80],
                    start_seconds=0,
                    end_seconds=raw.duration,
                    position_name=CaptionPosition.UPPER,
                    style_profile=CaptionStyleProfile.MINIMAL,
                    claim_ids=[],
                    beat_ids=[],
                    z_index=30,
                )
            )
        plan.warnings = sorted(set(warnings))
        return caption_plan_read(self.captions.add_plan(plan))

    def get(self, plan_id: int):
        return caption_plan_read(self.captions.get_plan(plan_id))

    def list(self, render_id: int):
        self.renders.get_render(render_id)
        return [caption_plan_read(item) for item in self.captions.list_plans(render_id)]


class FinalRenderService:
    def __init__(
        self,
        renders: RenderRepository,
        captions: CaptionRepository,
        renderer: CaptionFFmpegRenderer,
        ass_renderer: ASSSubtitleRenderer,
        srt_renderer: SRTSubtitleRenderer,
        storage: CaptionStorage,
        *,
        font_path: str,
        branding_enabled: bool,
        branding_path: str,
        branding_opacity: float,
        duration_tolerance: float,
        max_file_size_bytes: int,
    ) -> None:
        self.renders = renders
        self.captions = captions
        self.renderer = renderer
        self.ass_renderer = ass_renderer
        self.srt_renderer = srt_renderer
        self.storage = storage
        self.font_path_value = font_path
        self.branding_enabled = branding_enabled
        self.branding_path_value = branding_path
        self.branding_opacity = branding_opacity
        self.duration_tolerance = duration_tolerance
        self.max_file_size_bytes = max_file_size_bytes

    def render(self, plan_id: int, *, force: bool = False) -> FinalRenderResponse:
        plan = self.captions.get_plan(plan_id)
        raw = self.renders.get_render(plan.render_asset_id)
        if raw.status != RenderStatus.APPROVED or not raw.output_path or not raw.checksum_sha256:
            raise ConflictError("Final rendering requires the approved raw render")
        font = configured_file(self.font_path_value, {".ttf", ".otf", ".ttc"}, "Caption font")
        branding = None
        if self.branding_enabled and self.branding_path_value.strip():
            branding = configured_file(
                self.branding_path_value, {".png", ".jpg", ".jpeg", ".webp"}, "Branding asset"
            )
        font_identity = (
            [str(font), font.stat().st_size, font.stat().st_mtime_ns] if font else ["fallback"]
        )
        cache_key = _hash(
            {
                "raw": raw.checksum_sha256,
                "plan": plan.cache_key,
                "font": font_identity,
                "branding": str(branding) if branding else "",
                "subtitle": self.ass_renderer.version,
                "renderer": self.renderer.version,
                "ffmpeg": self.renderer.ffmpeg_version,
            }
        )
        if not force:
            reused = self.captions.reusable_render(cache_key)
            if reused:
                return FinalRenderResponse(render=final_render_read(reused), reused=True)
        warnings = list(plan.warnings)
        candidate = self.renders.session.get(VideoCandidate, raw.candidate_id)
        if candidate and not is_cleared_for_commercial_publication(candidate.rights):
            warnings.append("TEST ASSET — RIGHTS NOT VERIFIED FOR PUBLICATION")
        asset = FinalRenderAsset(
            caption_plan_id=plan.id,
            raw_render_id=raw.id,
            output_path=None,
            ass_path=None,
            srt_path=None,
            preview_path=None,
            width=None,
            height=None,
            fps=None,
            duration=None,
            video_codec=None,
            audio_codec=None,
            file_size_bytes=None,
            checksum_sha256=None,
            render_version=FINAL_RENDER_VERSION,
            subtitle_renderer_version=self.ass_renderer.version,
            ffmpeg_version=self.renderer.ffmpeg_version,
            status=FinalRenderStatus.RENDERING,
            warnings=warnings,
            cache_key=cache_key,
            failure_reason="",
            process_exit_code=None,
            reviewed_at=None,
            reviewed_by=None,
            review_notes="",
        )
        asset = self.captions.add_render(asset)
        paths = self.storage.prepare(raw.candidate_id, plan.id, asset.id)
        try:
            source = self.storage.resolve_render(raw.output_path)
            paths.ass_temporary.write_text(
                self.ass_renderer.render(plan, raw.width or 1080, raw.height or 1920, font),
                encoding="utf-8-sig",
            )
            paths.srt_temporary.write_text(self.srt_renderer.render(plan), encoding="utf-8")
            self.renderer.render(
                source,
                paths.ass_temporary,
                paths.video_temporary,
                font_path=font,
                branding_path=branding,
                branding_opacity=self.branding_opacity,
            )
            metadata = self.renderer.probe(paths.video_temporary)
            self._validate(plan, raw, metadata, paths.video_temporary)
            self.storage.finalize(paths)
            asset.output_path = paths.video_relative
            asset.ass_path = paths.ass_relative
            asset.srt_path = paths.srt_relative
            asset.width = metadata.width
            asset.height = metadata.height
            asset.fps = metadata.fps
            asset.duration = metadata.duration
            asset.video_codec = metadata.video_codec
            asset.audio_codec = metadata.audio_codec
            asset.file_size_bytes = paths.video_final.stat().st_size
            asset.checksum_sha256 = sha256_file(paths.video_final)
            asset.status = (
                FinalRenderStatus.NEEDS_REVIEW if asset.warnings else FinalRenderStatus.VALIDATED
            )
        except (RenderProcessError, OSError) as exc:
            self.storage.cleanup(paths)
            asset.status = FinalRenderStatus.FAILED
            asset.failure_reason = str(exc)
            asset.process_exit_code = getattr(exc, "exit_code", None)
        self.captions.session.flush()
        return FinalRenderResponse(render=final_render_read(asset), reused=False)

    def _validate(self, plan, raw, metadata, path: Path) -> None:
        if not path.is_file() or path.stat().st_size <= 0:
            raise RenderValidationError("Decorated render is empty")
        if path.stat().st_size > self.max_file_size_bytes:
            raise RenderValidationError("Decorated render exceeds configured size")
        if not metadata.has_video or not metadata.has_audio:
            raise RenderValidationError("Decorated render requires video and audio")
        if metadata.width != raw.width or metadata.height != raw.height:
            raise RenderValidationError("Decorated render dimensions changed")
        if metadata.fps is None or raw.fps is None or abs(metadata.fps - raw.fps) > 0.1:
            raise RenderValidationError("Decorated render frame rate changed")
        if metadata.video_codec != "h264" or metadata.audio_codec != "aac":
            raise RenderValidationError("Decorated render must use H.264 and AAC")
        if raw.duration is None or abs(metadata.duration - raw.duration) > self.duration_tolerance:
            raise RenderValidationError("Decorated render duration changed")
        if any(
            item.start_seconds < 0 or item.end_seconds > metadata.duration + 0.01
            for item in plan.items
        ):
            raise RenderValidationError("Caption timestamp falls outside final output duration")

    def preview(self, render_id: int, time_seconds: float):
        asset = self.captions.get_render(render_id)
        if asset.status not in (
            FinalRenderStatus.VALIDATED,
            FinalRenderStatus.NEEDS_REVIEW,
            FinalRenderStatus.APPROVED,
            FinalRenderStatus.REJECTED,
        ):
            raise ConflictError("Preview requires a completed final render")
        if asset.duration is None or time_seconds > asset.duration:
            raise ConflictError("Preview time is outside final render duration")
        raw = self.renders.get_render(asset.raw_render_id)
        source = self.storage.resolve_render(asset.output_path or "")
        output, relative = self.storage.prepare_preview(raw.candidate_id, asset.id)
        self.renderer.preview(source, output, time_seconds)
        asset.preview_path = relative
        self.captions.session.flush()
        return final_render_read(asset)


class FinalRenderReviewService:
    def __init__(
        self,
        candidates: CandidateRepository,
        renders: RenderRepository,
        captions: CaptionRepository,
    ) -> None:
        self.candidates = candidates
        self.renders = renders
        self.captions = captions

    def get(self, render_id: int):
        return final_render_read(self.captions.get_render(render_id))

    def review(self, render_id: int, request: FinalRenderReviewRequest):
        render = self.captions.get_render(render_id)
        target = FinalRenderStatus(request.decision)
        if target == FinalRenderStatus.APPROVED and render.status not in (
            FinalRenderStatus.VALIDATED,
            FinalRenderStatus.NEEDS_REVIEW,
        ):
            raise ConflictError("Only a completed final render can be approved")
        render.status = target
        render.reviewed_at = utcnow()
        render.reviewed_by = request.reviewer
        render.review_notes = request.notes
        render.reviews.append(
            FinalRenderReview(
                decision=request.decision, reviewer=request.reviewer, notes=request.notes
            )
        )
        raw = self.renders.get_render(render.raw_render_id)
        candidate = self.candidates.get(raw.candidate_id)
        self.candidates.record_event(
            candidate,
            f"FINAL_RENDER_{request.decision}",
            request.reviewer,
            request.notes,
            {"final_render_id": render.id, "caption_plan_id": render.caption_plan_id},
        )
        self.captions.session.flush()
        return final_render_read(render)
