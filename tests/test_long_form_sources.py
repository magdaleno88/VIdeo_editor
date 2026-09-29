from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.core.config import ScoringWeights
from app.core.errors import UnsupportedMediaError
from app.models import LongFormSource, SourceRights, VideoCandidate
from app.providers.rendering.ffmpeg import parse_probe
from app.repositories.sources import SourceRepository
from app.schemas.domain import (
    ConceptClipPatch,
    ConceptGenerationRequest,
    ReviewAction,
    ShortFormClipInput,
    SourceAnalysisRequest,
    SourceLicensePreset,
    SourceRightsReview,
    TranscriptSegmentInput,
)
from app.services.rights.policy import is_cleared_for_commercial_publication
from app.services.sources.scenes import DetectedScene, FFmpegSceneDetector
from app.services.sources.service import LongFormSourceService
from app.services.sources.storage import SourceStorage


def test_verified_source_rights_require_production_permissions_and_evidence():
    with pytest.raises(ValidationError, match="license and evidence"):
        SourceRightsReview(rights_status="VERIFIED", reviewer="Operator")
    with pytest.raises(ValidationError, match="commercial and derivative"):
        SourceRightsReview(
            rights_status="VERIFIED",
            reviewer="Operator",
            license_name="Owned media",
            evidence_reference="local release form",
            commercial_use_allowed=True,
            derivative_works_allowed=False,
            attribution_required=False,
        )
    review = SourceRightsReview(
        rights_status="VERIFIED",
        reviewer="Operator",
        license_name="Owned media",
        evidence_reference="local release form",
        commercial_use_allowed=True,
        derivative_works_allowed=True,
        attribution_required=False,
    )
    assert review.derivative_works_allowed is True


def test_timestamp_contracts_reject_inverted_transcript_and_clip_windows():
    with pytest.raises(ValidationError, match="Transcript segment end"):
        TranscriptSegmentInput(start_seconds=3, end_seconds=2, text="invalid")
    with pytest.raises(ValidationError, match="Clip end"):
        ShortFormClipInput(
            source_start=5,
            source_end=5,
            output_order=0,
            target_output_duration=3,
        )


def test_clip_patch_preserves_story_order_independent_of_source_time():
    patch = ConceptClipPatch(
        clips=[
            ShortFormClipInput(
                source_start=40,
                source_end=44,
                output_order=0,
                target_output_duration=4,
                role="HOOK",
            ),
            ShortFormClipInput(
                source_start=2,
                source_end=8,
                output_order=1,
                target_output_duration=6,
                role="CONTEXT",
            ),
        ]
    )
    assert patch.clips[0].source_start > patch.clips[1].source_start


def test_scene_merging_avoids_micro_scenes_and_caps_output():
    detector = object.__new__(FFmpegSceneDetector)
    detector.minimum_duration = 2
    detector.merge_threshold = 1
    detector.max_scenes = 3
    assert detector._merge([0, 0.5, 2.1, 4.2, 6.3, 8.4, 10], 10) == [0, 4.2, 6.3, 10]


def test_ffprobe_rotation_normalizes_display_dimensions():
    metadata = parse_probe(
        {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 1920,
                    "height": 1080,
                    "avg_frame_rate": "30/1",
                    "side_data_list": [{"rotation": -90}],
                }
            ],
            "format": {"duration": "10", "format_name": "mov,mp4"},
        }
    )
    assert (metadata.width, metadata.height, metadata.rotation) == (1080, 1920, -90)


def test_source_storage_resolution_is_confined(tmp_path):
    storage = object.__new__(SourceStorage)
    storage.root = tmp_path.resolve()
    valid = tmp_path / "1" / "original.mp4"
    valid.parent.mkdir()
    valid.write_bytes(b"video")
    assert storage.resolve("1/original.mp4") == valid.resolve()
    with pytest.raises(UnsupportedMediaError, match="invalid or unavailable"):
        storage.resolve("../outside.mp4")


def test_source_storage_uses_only_supported_initial_extensions():
    storage = object.__new__(SourceStorage)
    storage.root = Path(".").resolve()
    storage.incoming = storage.root
    storage.max_size_bytes = 100
    with pytest.raises(UnsupportedMediaError, match="MP4, MOV, MKV, or WebM"):
        storage.stage([b"not media"], "payload.exe")


def test_unreviewed_source_runs_local_analysis_concepts_and_preserves_provenance(
    session, tmp_path, monkeypatch
):
    source = LongFormSource(
        title="Unreviewed factory source",
        source_type="LOCAL_UPLOAD",
        original_filename="factory.mp4",
        relative_path="1/original.mp4",
        checksum_sha256="a" * 64,
        size_bytes=100,
        duration_seconds=30,
        width=1920,
        height=1080,
        fps=30,
        video_codec="h264",
        audio_codec="aac",
        has_audio=True,
        container="mov,mp4",
        rotation=0,
        status="INGESTED",
        rights=SourceRights(rights_status="UNKNOWN", notes="Original provenance note"),
    )
    session.add(source)
    session.flush()
    root = tmp_path / "sources"
    root.mkdir()
    frame = root / str(source.id) / "frames" / "scene-0001.jpg"

    class FakeDetector:
        def __init__(self, *args, **kwargs):
            pass

        def detect(self, source_path, duration, frame_directory):
            return [DetectedScene(0, duration, frame)]

    monkeypatch.setattr("app.services.sources.service.FFmpegSceneDetector", FakeDetector)
    service = object.__new__(LongFormSourceService)
    service.session = session
    service.settings = SimpleNamespace(
        ffmpeg_binary="",
        scene_threshold=0.3,
        minimum_scene_duration_seconds=2,
        scene_merge_threshold_seconds=1,
        scene_analysis_max_scenes=120,
        coarse_frame_budget=80,
        deep_analysis_max_moments=12,
        deep_analysis_window_seconds=20,
        max_concepts_per_source=5,
        long_video_max_analysis_minutes=120,
        gemini_video_model="",
        scoring_weights=ScoringWeights(),
    )
    service.sources = SourceRepository(session)
    service.storage = SimpleNamespace(root=root, resolve=lambda value: root / value)

    internal = service.continue_as_internal(
        source.id, ReviewAction(reviewer="Operator", notes="Internal test")
    )
    assert internal.rights.rights_status == "UNKNOWN"
    assert internal.rights.is_cleared_for_commercial_publication is False
    assert service.detect_scenes(source.id).scenes
    analyzed = service.analyze(source.id, SourceAnalysisRequest())
    assert analyzed.analyses[-1].moments
    concepts = service.generate_concepts(source.id, ConceptGenerationRequest(count=1))
    assert len(concepts) == 1
    approved = service.approve_concept(
        concepts[0].id, ReviewAction(reviewer="Editor", notes="Good internal concept")
    )
    candidate = service.session.get(VideoCandidate, approved.candidate_id)
    assert candidate is not None
    assert candidate.rights.rights_status == "UNKNOWN"
    assert candidate.rights.notes.startswith("Inherited from LongFormSource")
    assert is_cleared_for_commercial_publication(candidate.rights) is False

    reviewed = service.review_rights(
        source.id,
        SourceRightsReview(
            rights_status="VERIFIED",
            license_preset=SourceLicensePreset.CC_BY,
            license_name="CC BY 4.0",
            license_url="https://creativecommons.org/licenses/by/4.0/",
            creator="Factory owner",
            commercial_use_allowed=True,
            derivative_works_allowed=True,
            attribution_required=True,
            share_alike_required=False,
            attribution_text="Factory owner, CC BY 4.0",
            evidence_reference="https://example.org/source-page",
            reviewer="Rights editor",
            notes="File license checked",
        ),
    )
    assert reviewed.rights.is_cleared_for_commercial_publication is True
    assert candidate.rights.rights_status == "VERIFIED"
    assert candidate.rights.creator == "Factory owner"
    assert candidate.rights.license_url.endswith("/by/4.0/")
    assert "https://example.org/source-page" in candidate.rights.notes
