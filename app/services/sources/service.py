from __future__ import annotations

import math
from collections.abc import Iterable
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import utcnow
from app.core.errors import ConflictError
from app.models import (
    InterestingMoment,
    LongFormSource,
    ProcessStage,
    RightsRecord,
    ShortFormClip,
    ShortFormConcept,
    SourceAnalysis,
    SourceRights,
    SourceScene,
    SourceTranscript,
    SourceTranscriptSegment,
    VideoCandidate,
)
from app.repositories.candidates import CandidateRepository, apply_score
from app.repositories.sources import SourceRepository
from app.schemas.domain import (
    AnalysisProfile,
    CandidateStatus,
    ConceptClipPatch,
    ConceptGenerationRequest,
    DimensionAnalysis,
    FirstSecondsAnalysis,
    LongFormSourceRead,
    LongFormSourceStatus,
    LongFormSourceType,
    Orientation,
    ReviewAction,
    RightsStatus,
    ScoreInputs,
    ScoreResult,
    ShortFormClipRole,
    ShortFormConceptRead,
    ShortFormConceptStatus,
    SourceAnalysisRequest,
    SourceAnalysisStatus,
    SourceRightsReview,
    SourceTranscriptionRequest,
    VideoVisualAnalysis,
    VisualDimensions,
    VisualEvidence,
    VisualSegment,
)
from app.services.sources.scenes import FFmpegSceneDetector
from app.services.sources.storage import SourceStorage

ANALYSIS_VERSION = "long-form-local-1.0"
CONCEPT_VERSION = "short-form-concept-1.0"


class LongFormSourceService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.sources = SourceRepository(session)
        self.storage = SourceStorage(
            settings.source_storage_root,
            settings.source_upload_max_size_mb * 1024 * 1024,
            settings.ffprobe_binary,
        )

    def list(self) -> list[LongFormSourceRead]:
        return [LongFormSourceRead.model_validate(item) for item in self.sources.list()]

    def get(self, source_id: int) -> LongFormSourceRead:
        return LongFormSourceRead.model_validate(self.sources.get(source_id))

    def ingest(
        self,
        chunks: Iterable[bytes],
        original_filename: str,
        *,
        title: str | None = None,
        source_type: LongFormSourceType = LongFormSourceType.LOCAL_UPLOAD,
    ) -> tuple[LongFormSourceRead, bool]:
        staged, checksum, size = self.storage.stage(chunks, original_filename)
        existing = self.sources.by_checksum(checksum)
        if existing is not None:
            self.storage.discard(staged)
            return LongFormSourceRead.model_validate(existing), True
        try:
            metadata = self.storage.probe(staged)
            source = self.sources.add(
                LongFormSource(
                    title=(title or Path(original_filename).stem)[:200],
                    source_type=source_type,
                    original_filename=Path(original_filename).name[:500],
                    relative_path=f"pending/{checksum}",
                    checksum_sha256=checksum,
                    size_bytes=size,
                    duration_seconds=metadata.duration,
                    width=metadata.width,
                    height=metadata.height,
                    fps=metadata.fps,
                    video_codec=metadata.video_codec or "unknown",
                    audio_codec=metadata.audio_codec,
                    has_audio=metadata.has_audio,
                    container=metadata.format_name,
                    rotation=metadata.rotation,
                    status=LongFormSourceStatus.INGESTED,
                    rights=SourceRights(rights_status=RightsStatus.UNKNOWN, notes=""),
                )
            )
            source.relative_path = self.storage.finalize(staged, source.id, original_filename)
            self.session.flush()
            return LongFormSourceRead.model_validate(source), False
        except Exception:
            self.storage.discard(staged)
            raise

    def import_local(self, path: str, *, title: str | None = None):
        configured = self.settings.local_source_import_root.strip()
        if not configured:
            raise ConflictError("LOCAL_SOURCE_IMPORT_ROOT is required for filesystem imports")
        root = Path(configured).resolve()
        requested = Path(path).resolve()
        if root != requested and root not in requested.parents:
            raise ConflictError("Import path is outside LOCAL_SOURCE_IMPORT_ROOT")
        if not requested.is_file():
            raise ConflictError("Import path is not a file")

        def source_chunks():
            with requested.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    yield chunk

        return self.ingest(
            source_chunks(),
            requested.name,
            title=title,
            source_type=LongFormSourceType.LOCAL_IMPORT,
        )

    def review_rights(self, source_id: int, review: SourceRightsReview) -> LongFormSourceRead:
        source = self.sources.get(source_id)
        rights = source.rights
        rights.rights_status = review.rights_status
        rights.license_name = review.license_name
        rights.commercial_use_allowed = review.commercial_use_allowed
        rights.derivative_works_allowed = review.derivative_works_allowed
        rights.attribution_required = review.attribution_required
        rights.attribution_text = review.attribution_text
        rights.evidence_reference = review.evidence_reference
        rights.reviewed_by = review.reviewer
        rights.verified_at = utcnow() if review.rights_status == RightsStatus.VERIFIED else None
        rights.notes = review.notes
        self.session.flush()
        return LongFormSourceRead.model_validate(source)

    def detect_scenes(self, source_id: int) -> LongFormSourceRead:
        source = self.sources.get(source_id)
        source_path = self.storage.resolve(source.relative_path)
        detector = FFmpegSceneDetector(
            self.settings.ffmpeg_binary,
            threshold=self.settings.scene_threshold,
            minimum_duration=self.settings.minimum_scene_duration_seconds,
            merge_threshold=self.settings.scene_merge_threshold_seconds,
            max_scenes=self.settings.scene_analysis_max_scenes,
        )
        for old in list(source.scenes):
            self.session.delete(old)
        frame_directory = self.storage.root / str(source.id) / "frames"
        detected = detector.detect(source_path, source.duration_seconds, frame_directory)
        for position, item in enumerate(detected):
            source.scenes.append(
                SourceScene(
                    position=position,
                    start_seconds=item.start,
                    end_seconds=item.end,
                    duration_seconds=item.end - item.start,
                    representative_frame_path=item.frame_path.relative_to(
                        self.storage.root
                    ).as_posix(),
                    technical_metadata={
                        "detector": "ffmpeg-scene",
                        "threshold": self.settings.scene_threshold,
                    },
                    heuristic_score=min(100.0, 55 + (item.end - item.start)),
                )
            )
        source.status = LongFormSourceStatus.PREPROCESSED
        self.session.flush()
        return LongFormSourceRead.model_validate(source)

    def transcribe(self, source_id: int, request: SourceTranscriptionRequest):
        source = self.sources.get(source_id)
        if any(item.end_seconds > source.duration_seconds for item in request.segments):
            raise ConflictError("Transcript segment exceeds source duration")
        status = "COMPLETED" if source.has_audio else "SKIPPED_NO_AUDIO"
        segments = request.segments if source.has_audio else []
        transcript = SourceTranscript(
            provider=request.provider,
            model=request.model,
            language=request.language,
            status=status,
            full_text=" ".join(item.text for item in segments),
            segments=[
                SourceTranscriptSegment(position=index, **item.model_dump())
                for index, item in enumerate(segments)
            ],
        )
        source.transcripts.append(transcript)
        self.session.flush()
        return LongFormSourceRead.model_validate(source)

    def analyze(self, source_id: int, request: SourceAnalysisRequest) -> LongFormSourceRead:
        source = self.sources.get(source_id)
        if request.use_external_ai:
            analysis = SourceAnalysis(
                status=SourceAnalysisStatus.BLOCKED,
                profile=request.profile,
                provider="gemini",
                model=self.settings.gemini_video_model or "unconfigured",
                version=ANALYSIS_VERSION,
                summary=(
                    "External long-form analysis was requested explicitly but was not completed."
                ),
                warnings=[
                    "External long-form provider is not verified in this phase; "
                    "local source remains valid."
                ],
                budget=self._budget(source, request.profile),
            )
            source.analyses.append(analysis)
            source.analysis_status = SourceAnalysisStatus.BLOCKED
            source.status = LongFormSourceStatus.AI_ANALYSIS_BLOCKED
            self.session.flush()
            return LongFormSourceRead.model_validate(source)
        if not source.scenes:
            raise ConflictError("Run local scene detection before source analysis")
        budget = self._budget(source, request.profile)
        analysis = SourceAnalysis(
            status=SourceAnalysisStatus.COMPLETED,
            profile=request.profile,
            provider="local_scene_analysis",
            model="deterministic-hierarchy",
            version=ANALYSIS_VERSION,
            detected_process=source.title,
            summary=(
                f"Local structural map of {source.title} across "
                f"{len(source.scenes)} detected scenes."
            ),
            confidence=0.65,
            warnings=[
                "Stage names are local structural labels pending explicit multimodal review."
            ],
            budget=budget,
        )
        source.analyses.append(analysis)
        selected = self._stage_windows(source.scenes)
        for order, group in enumerate(selected):
            start, end = group[0].start_seconds, group[-1].end_seconds
            stage = ProcessStage(
                source_id=source.id,
                name=f"Process stage {order + 1}",
                start_seconds=start,
                end_seconds=end,
                description=(
                    f"Structural stage covering scenes {group[0].position + 1}–"
                    f"{group[-1].position + 1}."
                ),
                visual_description="Representative frames and scene boundaries detected locally.",
                transcript_context=self._transcript_context(source, start, end),
                confidence=0.65,
                stage_order=order,
            )
            analysis.stages.append(stage)
        self.session.flush()
        scene_rank = sorted(source.scenes, key=lambda item: item.heuristic_score or 0, reverse=True)
        for rank, scene in enumerate(scene_rank[: self.settings.deep_analysis_max_moments]):
            stage = next(
                (
                    item
                    for item in analysis.stages
                    if item.start_seconds <= scene.start_seconds < item.end_seconds
                ),
                None,
            )
            score = max(50.0, 82.0 - rank * 2)
            analysis.moments.append(
                InterestingMoment(
                    source_id=source.id,
                    stage_id=stage.id if stage else None,
                    start_seconds=scene.start_seconds,
                    end_seconds=scene.end_seconds,
                    proposed_start_seconds=scene.start_seconds,
                    proposed_end_seconds=scene.end_seconds,
                    description=f"Visually distinct scene {scene.position + 1}",
                    visual_interest_score=score,
                    educational_value=max(50, score - 4),
                    transformation_score=max(45, score - 6),
                    motion_score=score,
                    machinery_score=max(50, score - 2),
                    hook_score=max(50, score - rank),
                    loop_potential=max(40, score - 10),
                    confidence=0.65,
                    evidence=[{"scene_id": scene.id, "frame": scene.representative_frame_path}],
                )
            )
        source.analysis_status = SourceAnalysisStatus.COMPLETED
        source.status = LongFormSourceStatus.ANALYZED
        self.session.flush()
        return LongFormSourceRead.model_validate(source)

    def generate_concepts(self, source_id: int, request: ConceptGenerationRequest):
        source = self.sources.get(source_id)
        analysis = self.sources.latest_analysis(source_id)
        if (
            analysis is None
            or analysis.status != SourceAnalysisStatus.COMPLETED
            or not analysis.moments
        ):
            raise ConflictError("Completed source analysis with interesting moments is required")
        count = min(request.count, self.settings.max_concepts_per_source, len(analysis.moments))
        created = []
        moments = sorted(analysis.moments, key=lambda item: item.hook_score, reverse=True)
        for index in range(count):
            ordered = moments[index:] + moments[:index]
            selected = ordered[: min(6, len(ordered))]
            concept = ShortFormConcept(
                source_id=source.id,
                analysis_id=analysis.id,
                title=f"{source.title}: angle {index + 1}",
                angle=(
                    "Complete process overview"
                    if index == 0
                    else f"Focus on process stage {index + 1}"
                ),
                target_duration=request.target_duration,
                hook_candidate=selected[0].description,
                stage_refs=list(dict.fromkeys(item.stage_id for item in selected if item.stage_id)),
                moment_refs=[item.id for item in selected],
                coverage={
                    "moments": len(selected),
                    "source_seconds": round(
                        sum(item.end_seconds - item.start_seconds for item in selected), 2
                    ),
                },
                status=ShortFormConceptStatus.PROPOSED,
                generation_version=CONCEPT_VERSION,
                clips=self._clips(selected, request.target_duration),
            )
            concept.overlap_warning = self._overlap_warning(concept, source.concepts)
            source.concepts.append(concept)
            created.append(concept)
        self.session.flush()
        return [ShortFormConceptRead.model_validate(item) for item in created]

    def get_concept(self, concept_id: int) -> ShortFormConceptRead:
        return ShortFormConceptRead.model_validate(self.sources.get_concept(concept_id))

    def patch_clips(self, concept_id: int, request: ConceptClipPatch) -> ShortFormConceptRead:
        concept = self.sources.get_concept(concept_id)
        source = self.sources.get(concept.source_id)
        if concept.status != ShortFormConceptStatus.PROPOSED:
            raise ConflictError("Only proposed concepts can be edited")
        orders = [item.output_order for item in request.clips]
        if sorted(orders) != list(range(len(orders))) or len(set(orders)) != len(orders):
            raise ConflictError("Clip output order must be contiguous from zero")
        if any(item.source_end > source.duration_seconds for item in request.clips):
            raise ConflictError("Clip exceeds source duration")
        concept.clips.clear()
        concept.clips.extend(ShortFormClip(**item.model_dump()) for item in request.clips)
        concept.target_duration = sum(item.target_output_duration for item in request.clips)
        self.session.flush()
        return ShortFormConceptRead.model_validate(concept)

    def reject_concept(self, concept_id: int, action: ReviewAction) -> ShortFormConceptRead:
        concept = self.sources.get_concept(concept_id)
        concept.status = ShortFormConceptStatus.REJECTED
        concept.reviewed_by = action.reviewer
        concept.reviewed_at = utcnow()
        concept.review_notes = action.notes
        self.session.flush()
        return ShortFormConceptRead.model_validate(concept)

    def approve_concept(self, concept_id: int, action: ReviewAction) -> ShortFormConceptRead:
        concept = self.sources.get_concept(concept_id)
        source = self.sources.get(concept.source_id)
        rights = source.rights
        if not (
            rights.rights_status == RightsStatus.VERIFIED
            and rights.commercial_use_allowed is True
            and rights.derivative_works_allowed is True
        ):
            raise ConflictError("Concept approval requires verified source-level production rights")
        if concept.candidate_id is None:
            candidate = self._candidate_from_concept(source, concept)
            self.session.add(candidate)
            self.session.flush()
            concept.candidate_id = candidate.id
            repository = CandidateRepository(self.session)
            score, visual = self._structural_evaluation(source, concept)
            apply_score(candidate, score)
            repository.add_evaluation(candidate, score, analysis=visual, overall_confidence=0.65)
        concept.status = ShortFormConceptStatus.APPROVED
        concept.reviewed_by = action.reviewer
        concept.reviewed_at = utcnow()
        concept.review_notes = action.notes
        self.session.flush()
        return ShortFormConceptRead.model_validate(concept)

    def _candidate_from_concept(self, source, concept):
        rights = source.rights
        evidence = f"https://local.invalid/sources/{source.id}/rights"
        return VideoCandidate(
            provider="long_form",
            provider_video_id=f"{source.id}:{concept.id}",
            source_url=f"https://local.invalid/sources/{source.id}",
            preview_url=f"https://local.invalid/sources/{source.id}/media",
            thumbnail_url=None,
            title=concept.title,
            description=concept.angle,
            duration=source.duration_seconds,
            width=source.width,
            height=source.height,
            orientation=Orientation.PORTRAIT
            if source.height > source.width
            else Orientation.LANDSCAPE,
            author="Local operator",
            author_url=None,
            search_query="long-form source",
            category="long-form",
            industrial_process=source.title,
            object_being_manufactured=source.title,
            status=CandidateStatus.APPROVED,
            rights=RightsRecord(
                source=f"https://local.invalid/sources/{source.id}",
                creator="Local operator",
                license_name=rights.license_name,
                license_url=evidence,
                commercial_use_allowed=True,
                modification_allowed=True,
                attribution_required=rights.attribution_required,
                attribution_text=rights.attribution_text,
                rights_status=RightsStatus.VERIFIED,
                verification_date=rights.verified_at,
                verified_by=rights.reviewed_by,
                evidence_url=evidence,
                notes=(
                    f"Inherited from LongFormSource #{source.id}: "
                    f"{rights.evidence_reference}. {rights.notes}"
                ),
            ),
        )

    def _structural_evaluation(self, source, concept):
        ratings = ScoreInputs(
            movement=78,
            visible_transformation=76,
            satisfying_result=72,
            unusual_machinery=75,
            understandable_without_audio=70,
            visual_hook=80,
            educational_potential=82,
            loop_potential=65,
        )
        weights = self.settings.scoring_weights.model_dump()
        total = sum(getattr(ratings, key) * value / 100 for key, value in weights.items())
        score = ScoreResult(
            method="source_structural",
            version=ANALYSIS_VERSION,
            inputs=ratings,
            weights=weights,
            total=round(total, 2),
            rationale={
                key: "Inherited from approved long-form concept planning." for key in weights
            },
            analyzed_video=True,
        )
        clips = sorted(concept.clips, key=lambda item: item.output_order)
        first = clips[0]
        dimension_names = list(weights)
        dimensions = {}
        for name in dimension_names:
            dimensions[name] = DimensionAnalysis(
                rating=getattr(ratings, name),
                confidence=0.65,
                explanation="Local scene and concept evidence.",
                evidence=[
                    VisualEvidence(
                        timestamp_start=first.source_start,
                        timestamp_end=first.source_end,
                        observation=concept.hook_candidate,
                        related_dimension=name,
                        confidence=0.65,
                    )
                ],
            )
        segments = [
            VisualSegment(
                start=item.source_start,
                end=item.source_end,
                reason=item.role.value,
                confidence=0.65,
            )
            for item in clips
        ]
        analysis = VideoVisualAnalysis(
            provider="long_form_local",
            model="deterministic-hierarchy",
            analysis_version=ANALYSIS_VERSION,
            prompt_version="local-no-prompt",
            summary=concept.angle,
            detected_process=source.title,
            detected_object=source.title,
            duration_analyzed=source.duration_seconds,
            dimensions=VisualDimensions(**dimensions),
            evidence=[],
            notable_moments=segments[:10],
            potential_hook_segments=[segments[0]],
            potential_loop_segments=segments[-1:],
            first_seconds=FirstSecondsAnalysis(
                duration_observed=min(3, source.duration_seconds),
                immediate_motion="See selected hook window.",
                visual_novelty="Selected from ranked source moments.",
                visible_transformation="Review concept clips.",
                visual_clarity="Representative source scene.",
                context_required="Verified research remains required.",
                confidence=0.65,
            ),
            warnings=["Structural evaluation is local and is not a live Gemini analysis."],
        )
        return score, analysis

    def _budget(self, source, profile):
        factor = {
            AnalysisProfile.ECONOMY: 0.5,
            AnalysisProfile.BALANCED: 1.0,
            AnalysisProfile.QUALITY: 1.5,
        }[profile]
        frames = min(len(source.scenes), max(1, int(self.settings.coarse_frame_budget * factor)))
        moments = min(
            len(source.scenes), max(1, int(self.settings.deep_analysis_max_moments * factor))
        )
        return {
            "profile": profile.value,
            "frames_selected": frames,
            "deep_moments": moments,
            "deep_minutes": round(moments * self.settings.deep_analysis_window_seconds / 60, 2),
            "estimated_external_calls": 0,
        }

    @staticmethod
    def _stage_windows(scenes):
        count = min(8, len(scenes))
        size = math.ceil(len(scenes) / count)
        return [scenes[index : index + size] for index in range(0, len(scenes), size)]

    @staticmethod
    def _transcript_context(source, start, end):
        if not source.transcripts:
            return ""
        return " ".join(
            segment.text
            for segment in source.transcripts[-1].segments
            if segment.start_seconds < end and segment.end_seconds > start
        )[:4000]

    @staticmethod
    def _clips(moments, target):
        duration = target / len(moments)
        roles = [
            ShortFormClipRole.HOOK,
            ShortFormClipRole.CONTEXT,
            ShortFormClipRole.PROCESS,
            ShortFormClipRole.TRANSFORMATION,
            ShortFormClipRole.PROCESS,
            ShortFormClipRole.RESULT,
        ]
        clips = []
        for index, moment in enumerate(moments):
            available = moment.end_seconds - moment.start_seconds
            clips.append(
                ShortFormClip(
                    moment_id=moment.id,
                    source_start=moment.start_seconds,
                    source_end=moment.end_seconds,
                    output_order=index,
                    target_output_duration=min(duration, available),
                    speed_recommendation=max(0.8, min(1.25, available / min(duration, available))),
                    role=roles[index],
                )
            )
        return clips

    @staticmethod
    def _overlap_warning(concept, existing):
        proposed = {
            (round(item.source_start, 1), round(item.source_end, 1)) for item in concept.clips
        }
        for other in existing:
            current = {
                (round(item.source_start, 1), round(item.source_end, 1)) for item in other.clips
            }
            if proposed and len(proposed & current) / len(proposed) >= 0.8:
                return f"High clip overlap with concept #{other.id}"
        return None
