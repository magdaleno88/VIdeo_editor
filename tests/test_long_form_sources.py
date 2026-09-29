from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.errors import UnsupportedMediaError
from app.providers.rendering.ffmpeg import parse_probe
from app.schemas.domain import (
    ConceptClipPatch,
    ShortFormClipInput,
    SourceRightsReview,
    TranscriptSegmentInput,
)
from app.services.sources.scenes import FFmpegSceneDetector
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
