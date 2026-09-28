from __future__ import annotations

import hashlib
import json
import math

from sqlalchemy.orm import Session

from app.core.database import utcnow
from app.core.errors import ConflictError, RenderProcessError, RenderValidationError
from app.models import EditSegment, RenderAsset, RenderReview, ScoreEvaluation, VideoEditPlan
from app.providers.rendering.base import VideoRenderer
from app.repositories.candidates import CandidateRepository
from app.repositories.narrations import NarrationRepository
from app.repositories.renders import RenderRepository, plan_read, render_read
from app.repositories.scripts import ScriptRepository
from app.schemas.domain import (
    CandidateStatus,
    CompositionStrategy,
    NarrationStatus,
    RenderPlanRequest,
    RenderResponse,
    RenderReviewRequest,
    RenderStatus,
    RightsInfo,
    ScriptStatus,
    SourceAudioPolicy,
    TransitionType,
    VideoVisualAnalysis,
)
from app.services.rendering.storage import (
    NarrationFileResolver,
    RenderStorage,
    sha256_file,
)
from app.services.rights.policy import approval_blockers

PLANNER_VERSION = "1.0"
RENDER_VERSION = "1.0"


class EditPlanService:
    def __init__(
        self,
        session: Session,
        candidates: CandidateRepository,
        scripts: ScriptRepository,
        narrations: NarrationRepository,
        renders: RenderRepository,
        *,
        width: int,
        height: int,
        fps: float,
        min_speed: float,
        max_speed: float,
        pre_roll_ms: int,
        post_roll_ms: int,
    ) -> None:
        self.session = session
        self.candidates = candidates
        self.scripts = scripts
        self.narrations = narrations
        self.renders = renders
        self.width = width
        self.height = height
        self.fps = fps
        self.min_speed = min_speed
        self.max_speed = max_speed
        self.pre_roll = pre_roll_ms / 1000
        self.post_roll = post_roll_ms / 1000

    def create(self, candidate_id: int, request: RenderPlanRequest):
        candidate, script, narration = self._prerequisites(
            candidate_id, request.script_id, request.narration_id
        )
        evaluation = self.session.get(ScoreEvaluation, script.visual_evaluation_id)
        if evaluation is None or not evaluation.analysis:
            raise ConflictError("The approved script has no visual analysis")
        analysis = VideoVisualAnalysis.model_validate(evaluation.analysis)
        settings = {
            "composition": request.composition.value,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "min_speed": self.min_speed,
            "max_speed": self.max_speed,
            "pre_roll": self.pre_roll,
            "post_roll": self.post_roll,
        }
        cache_key = self._hash(
            candidate.id,
            candidate.provider_video_id,
            candidate.preview_url,
            script.id,
            narration.id,
            narration.checksum_sha256,
            settings,
        )
        if not request.force:
            reusable = self.renders.reusable_plan(cache_key)
            if reusable is not None:
                return plan_read(reusable)
        segments, warnings = self._segments(candidate, script, narration, analysis, request)
        if request.composition == CompositionStrategy.CENTER_CROP:
            warnings.append(
                "Spatial subject tracking is unavailable; center crop requires human framing review"
            )
        plan = VideoEditPlan(
            candidate_id=candidate.id,
            script_id=script.id,
            narration_id=narration.id,
            target_platform=script.target_platform,
            aspect_ratio="9:16",
            width=self.width,
            height=self.height,
            fps=self.fps,
            target_duration=narration.duration_seconds,
            actual_narration_duration=narration.duration_seconds,
            composition=request.composition,
            source_audio_policy=SourceAudioPolicy.MUTED,
            audio_configuration={"narration_id": narration.id, "source_audio": "MUTED"},
            render_configuration=settings,
            planner_version=PLANNER_VERSION,
            warnings=warnings,
            cache_key=cache_key,
        )
        plan.segments.extend(segments)
        saved = self.renders.add_plan(plan)
        self.candidates.record_event(
            candidate,
            "RENDER_PLAN_CREATED",
            "system",
            "Deterministic timeline created from approved assets",
            {"edit_plan_id": saved.id, "script_id": script.id, "narration_id": narration.id},
        )
        return plan_read(saved)

    def list(self, candidate_id: int):
        self.candidates.get(candidate_id)
        return [plan_read(item) for item in self.renders.list_plans(candidate_id)]

    def get(self, plan_id: int):
        return plan_read(self.renders.get_plan(plan_id))

    def _prerequisites(self, candidate_id: int, script_id: int, narration_id: int):
        candidate = self.candidates.get(candidate_id)
        if candidate.status != CandidateStatus.APPROVED:
            raise ConflictError("Rendering requires an approved candidate")
        blockers = approval_blockers(RightsInfo.model_validate(candidate.rights))
        if blockers:
            raise ConflictError("Rendering rights are no longer valid: " + "; ".join(blockers))
        script = self.scripts.get(script_id)
        if script.candidate_id != candidate.id or script.status != ScriptStatus.APPROVED:
            raise ConflictError("Rendering requires a matching approved script")
        narration = self.narrations.get(narration_id)
        if narration.script_id != script.id or narration.status != NarrationStatus.APPROVED:
            raise ConflictError("Rendering requires a matching approved narration")
        return candidate, script, narration

    def _segments(self, candidate, script, narration, analysis, request):
        source_duration = min(
            candidate.duration or analysis.duration_analyzed, analysis.duration_analyzed
        )
        if source_duration <= 0:
            raise RenderValidationError("Source duration is unavailable")
        by_beat: dict[int, list] = {}
        for alignment in narration.alignments:
            by_beat.setdefault(alignment.beat_id, []).append(alignment)
        visual_timeline = self._visual_timeline(analysis)
        segments = []
        warnings: list[str] = []
        cursor = 0.0
        for position, beat in enumerate(script.beats):
            alignments = by_beat.get(beat.id, [])
            if not alignments:
                raise RenderValidationError(f"Beat {beat.id} has no narration alignment")
            required = max(item.end_seconds for item in alignments) - min(
                item.start_seconds for item in alignments
            )
            if required <= 0:
                raise RenderValidationError(f"Beat {beat.id} has invalid narration timing")
            sentence_ids = [sentence.id for sentence in beat.sentences]
            refs = list(
                dict.fromkeys(
                    ref.visual_ref for sentence in beat.sentences for ref in sentence.visual_refs
                )
            )
            selected = self._select_visual(position, refs, visual_timeline)
            if selected is None:
                selected = {
                    "ref": "fallback:beat",
                    "start": beat.recommended_start,
                    "end": beat.recommended_end,
                    "kind": "FALLBACK",
                    "description": beat.visual_direction,
                }
                warnings.append(f"Beat {beat.id} has no valid visual reference; beat timing used")
            start, end, speed, loop_count, hold = self._fit(selected, required, source_duration)
            if hold > 0:
                warnings.append(
                    f"Beat {beat.id} requires a {hold:.2f}s final-frame hold; review pacing"
                )
            if loop_count > 1:
                warnings.append(
                    f"Beat {beat.id} uses loop candidate {selected['ref']} {loop_count} times"
                )
            output_end = cursor + required
            segments.append(
                EditSegment(
                    position=position,
                    source_start=start,
                    source_end=end,
                    output_start=cursor,
                    output_end=output_end,
                    playback_speed=speed,
                    composition=request.composition,
                    beat_id=beat.id,
                    sentence_ids=sentence_ids,
                    visual_refs=[selected["ref"]],
                    rationale=selected["description"],
                    transition=TransitionType.CUT,
                    loop_count=loop_count,
                    hold_seconds=hold,
                )
            )
            cursor = output_end
        difference = narration.duration_seconds - cursor
        if abs(difference) > 0.05:
            segments[-1].output_end += difference
            segments[-1].hold_seconds += max(difference, 0)
            warnings.append("Final segment was adjusted to match the approved narration duration")
        return segments, warnings

    @staticmethod
    def _select_visual(position: int, refs: list[str], timeline: dict[str, dict]):
        values = [timeline[ref] for ref in refs if ref in timeline]
        if position == 0:
            hook = next((item for item in values if item["kind"] == "HOOK"), None)
            if hook:
                return hook
            fallback_hook = next(
                (item for item in timeline.values() if item["kind"] == "HOOK"), None
            )
            if fallback_hook:
                return fallback_hook
        priority = {"NOTABLE": 0, "EVIDENCE": 1, "LOOP": 2, "HOOK": 3}
        return min(values, key=lambda item: priority.get(item["kind"], 9)) if values else None

    def _fit(self, selected: dict, required: float, source_duration: float):
        start = max(0.0, float(selected["start"]) - self.pre_roll)
        end = min(source_duration, float(selected["end"]) + self.post_roll)
        if end <= start:
            raise RenderValidationError("Selected visual interval is outside the source")
        is_loop = selected["kind"] == "LOOP"
        available = end - start
        if not is_loop:
            desired = min(required, source_duration)
            center = (start + end) / 2
            start = max(0.0, min(center - desired / 2, source_duration - desired))
            end = start + desired
            available = end - start
        if available / required > self.max_speed:
            end = start + required * self.max_speed
            available = end - start
        loop_count = 1
        if is_loop and available / required < self.min_speed:
            loop_count = max(1, math.ceil(required * self.min_speed / available))
        speed = available * loop_count / required
        hold = 0.0
        if speed < self.min_speed:
            speed = self.min_speed
            hold = required - available / speed
        speed = min(speed, self.max_speed)
        return start, end, speed, loop_count, max(0.0, hold)

    @staticmethod
    def _visual_timeline(analysis: VideoVisualAnalysis) -> dict[str, dict]:
        timeline: dict[str, dict] = {}
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

    @staticmethod
    def _hash(*values) -> str:
        encoded = json.dumps(values, sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()


class RenderService:
    def __init__(
        self,
        candidates: CandidateRepository,
        scripts: ScriptRepository,
        narrations: NarrationRepository,
        renders: RenderRepository,
        renderer: VideoRenderer,
        fetcher,
        storage: RenderStorage,
        narration_files: NarrationFileResolver,
        *,
        duration_tolerance: float,
        max_file_size_bytes: int,
    ) -> None:
        self.candidates = candidates
        self.scripts = scripts
        self.narrations = narrations
        self.renders = renders
        self.renderer = renderer
        self.fetcher = fetcher
        self.storage = storage
        self.narration_files = narration_files
        self.duration_tolerance = duration_tolerance
        self.max_file_size_bytes = max_file_size_bytes

    def render(self, plan_id: int, *, force: bool = False) -> RenderResponse:
        plan = self.renders.get_plan(plan_id)
        candidate, script, narration = self._prerequisites(plan)
        cache_key = EditPlanService._hash(
            plan.cache_key,
            narration.checksum_sha256,
            candidate.provider_video_id,
            candidate.preview_url,
            RENDER_VERSION,
            self.renderer.version,
        )
        if not force:
            reusable = self.renders.reusable_render(cache_key)
            if reusable is not None:
                return RenderResponse(render=render_read(reusable), reused=True)
        asset = self.renders.add_render(
            RenderAsset(
                candidate_id=candidate.id,
                script_id=script.id,
                narration_id=narration.id,
                edit_plan_id=plan.id,
                planned_duration=plan.target_duration,
                render_version=RENDER_VERSION,
                status=RenderStatus.RENDERING,
                warnings=list(plan.warnings),
                cache_key=cache_key,
            )
        )
        paths = self.storage.prepare(candidate.id, asset.id)
        try:
            narration_path = self.narration_files.resolve(narration.storage_path)
            with self.fetcher.fetch(candidate) as source:
                source_metadata = self.renderer.probe(source.path)
                self._validate_source(source_metadata, plan)
                narration_metadata = self.renderer.probe(narration_path)
                if not narration_metadata.has_audio:
                    raise RenderValidationError("Approved narration file has no audio stream")
                self.renderer.render(source.path, narration_path, paths.temporary, plan)
            metadata = self.renderer.probe(paths.temporary)
            self._validate_output(metadata, plan)
            size = paths.temporary.stat().st_size
            if size > self.max_file_size_bytes:
                raise RenderValidationError("Rendered MP4 exceeds the configured size limit")
            checksum = sha256_file(paths.temporary)
            self.storage.finalize(paths)
            difference = metadata.duration - plan.target_duration
            warnings = list(plan.warnings)
            status = RenderStatus.VALIDATED
            if abs(difference) / plan.target_duration > self.duration_tolerance:
                warnings.append("Rendered duration is outside the configured tolerance")
                status = RenderStatus.NEEDS_REVIEW
            elif warnings:
                status = RenderStatus.NEEDS_REVIEW
            asset.output_path = paths.relative
            asset.width = metadata.width
            asset.height = metadata.height
            asset.fps = metadata.fps
            asset.duration = metadata.duration
            asset.duration_difference = difference
            asset.video_codec = metadata.video_codec
            asset.audio_codec = metadata.audio_codec
            asset.file_size_bytes = size
            asset.checksum_sha256 = checksum
            asset.ffmpeg_version = self.renderer.version
            asset.status = status
            asset.warnings = warnings
            self.renders.session.flush()
            self.candidates.record_event(
                candidate,
                "RENDER_COMPLETED",
                "system",
                "Versioned vertical draft rendered from approved assets",
                {"render_id": asset.id, "edit_plan_id": plan.id, "status": status.value},
            )
        except Exception as exc:
            self.storage.cleanup(paths)
            asset.status = RenderStatus.FAILED
            asset.failure_reason = self._failure_message(exc)
            asset.process_exit_code = getattr(exc, "exit_code", None)
            self.renders.session.flush()
            return RenderResponse(render=render_read(asset), reused=False)
        return RenderResponse(render=render_read(asset), reused=False)

    def _prerequisites(self, plan):
        candidate = self.candidates.get(plan.candidate_id)
        blockers = approval_blockers(RightsInfo.model_validate(candidate.rights))
        if candidate.status != CandidateStatus.APPROVED or blockers:
            raise ConflictError(
                "Candidate approval or verified rendering rights are no longer valid"
            )
        script = self.scripts.get(plan.script_id)
        narration = self.narrations.get(plan.narration_id)
        if script.status != ScriptStatus.APPROVED:
            raise ConflictError("The edit plan script is no longer approved")
        if narration.status != NarrationStatus.APPROVED:
            raise ConflictError("The edit plan narration is no longer approved")
        return candidate, script, narration

    @staticmethod
    def _validate_source(metadata, plan) -> None:
        if not metadata.has_video or not metadata.width or not metadata.height:
            raise RenderValidationError("Source asset has no valid video stream")
        if any(segment.source_end > metadata.duration + 0.05 for segment in plan.segments):
            raise RenderValidationError("Edit plan segment exceeds the acquired source duration")

    @staticmethod
    def _validate_output(metadata, plan) -> None:
        if not metadata.has_video or not metadata.has_audio:
            raise RenderValidationError("Rendered output must contain video and audio")
        if (metadata.width, metadata.height) != (plan.width, plan.height):
            raise RenderValidationError("Rendered output dimensions do not match the edit plan")
        if metadata.fps is None or abs(metadata.fps - plan.fps) > 1:
            raise RenderValidationError("Rendered output frame rate does not match the edit plan")
        if metadata.video_codec != "h264" or metadata.audio_codec != "aac":
            raise RenderValidationError("Rendered output codecs must be H.264 and AAC")
        if not any(name in metadata.format_name for name in ("mp4", "mov")):
            raise RenderValidationError("Rendered output is not a valid MP4 container")

    @staticmethod
    def _failure_message(exc: Exception) -> str:
        if isinstance(exc, RenderProcessError):
            return str(exc)[:4000]
        return (
            f"Render failed during media acquisition, validation, or storage ({type(exc).__name__})"
        )


class RenderReviewService:
    def __init__(self, candidates: CandidateRepository, renders: RenderRepository) -> None:
        self.candidates = candidates
        self.renders = renders

    def get_plan(self, plan_id: int):
        return plan_read(self.renders.get_plan(plan_id))

    def get(self, render_id: int):
        return render_read(self.renders.get_render(render_id))

    def review(self, render_id: int, request: RenderReviewRequest):
        render = self.renders.get_render(render_id)
        target = RenderStatus(request.decision)
        if target == RenderStatus.APPROVED and render.status not in (
            RenderStatus.VALIDATED,
            RenderStatus.NEEDS_REVIEW,
        ):
            raise ConflictError("Only a completed render can be approved")
        render.status = target
        render.reviewed_at = utcnow()
        render.reviewed_by = request.reviewer
        render.review_notes = request.notes
        render.reviews.append(
            RenderReview(decision=request.decision, reviewer=request.reviewer, notes=request.notes)
        )
        candidate = self.candidates.get(render.candidate_id)
        self.candidates.record_event(
            candidate,
            f"RENDER_{request.decision}",
            request.reviewer,
            request.notes,
            {"render_id": render.id, "edit_plan_id": render.edit_plan_id},
        )
        self.renders.session.flush()
        return render_read(render)
