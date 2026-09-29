"""Real local long-form ingestion, scene detection, concept and render smoke."""

import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

from alembic import command
from alembic.config import Config

from app.core.config import Settings
from app.core.database import build_engine, session_factory, session_scope
from app.providers.rendering import FFmpegVideoRenderer
from app.providers.rendering.ffmpeg import resolve_binary
from app.schemas.domain import (
    AnalysisProfile,
    CompositionStrategy,
    ConceptGenerationRequest,
    ReviewAction,
    RightsStatus,
    SourceAnalysisRequest,
    SourceRightsReview,
    SourceTranscriptionRequest,
    TranscriptSegmentInput,
)
from app.services.sources.service import LongFormSourceService

ROOT = Path(__file__).resolve().parents[1]


def run(arguments: list[str], timeout: int = 180) -> None:
    result = subprocess.run(
        arguments, capture_output=True, text=True, timeout=timeout, check=False, shell=False
    )
    if result.returncode:
        raise RuntimeError(result.stderr[-2000:] or "FFmpeg command failed")


def generate_source(ffmpeg: str, output: Path) -> None:
    colors = ("red", "blue", "green", "yellow", "magenta", "cyan")
    arguments = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    for color in colors:
        arguments += ["-f", "lavfi", "-i", f"color=c={color}:s=640x360:r=30:d=10"]
    arguments += ["-f", "lavfi", "-i", "sine=frequency=330:duration=60"]
    inputs = "".join(f"[{index}:v]" for index in range(len(colors)))
    arguments += [
        "-filter_complex",
        f"{inputs}concat=n={len(colors)}:v=1:a=0[v]",
        "-map",
        "[v]",
        "-map",
        f"{len(colors)}:a",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        str(output),
    ]
    run(arguments)


def migrate(engine) -> None:
    config = Config(str(ROOT / "alembic.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")


def main() -> int:
    configured = Settings()
    ffmpeg = resolve_binary(configured.ffmpeg_binary, "ffmpeg")
    ffprobe = resolve_binary(configured.ffprobe_binary, "ffprobe")
    with tempfile.TemporaryDirectory(prefix="icf-long-form-smoke-") as temporary:
        root = Path(temporary)
        source_path = root / "industrial-process.mp4"
        narration_path = root / "narration.wav"
        output_path = root / "multi-window.mp4"
        generate_source(ffmpeg, source_path)
        run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=9",
                str(narration_path),
            ]
        )
        settings = Settings(
            _env_file=None,
            database_url=f"sqlite:///{(root / 'smoke.db').as_posix()}",
            source_storage_root=str(root / "sources"),
            ffmpeg_binary=ffmpeg,
            ffprobe_binary=ffprobe,
            minimum_scene_duration_seconds=1,
            scene_merge_threshold_seconds=0.5,
        )
        engine = build_engine(settings.database_url)
        migrate(engine)
        try:
            scene_verification = None
            with session_scope(session_factory(engine)) as session:
                service = LongFormSourceService(session, settings)

                def chunks():
                    with source_path.open("rb") as handle:
                        while chunk := handle.read(64 * 1024):
                            yield chunk

                source, duplicate = service.ingest(chunks(), source_path.name)
                if duplicate or source.duration_seconds < 59 or source.duration_seconds > 61:
                    raise RuntimeError("Long-form ingestion metadata validation failed")
                _, duplicate = service.ingest(chunks(), source_path.name)
                if not duplicate or len(service.list()) != 1:
                    raise RuntimeError("Checksum duplicate reuse failed")
                print(
                    "LOCAL LONG-FORM INGEST VERIFIED: "
                    f"source={source.id}, {source.duration_seconds:.2f}s, "
                    f"{source.width}x{source.height}, sha256={source.checksum_sha256[:12]}..."
                )
                service.review_rights(
                    source.id,
                    SourceRightsReview(
                        rights_status=RightsStatus.VERIFIED,
                        license_name="Synthetic smoke fixture",
                        commercial_use_allowed=True,
                        derivative_works_allowed=True,
                        attribution_required=False,
                        evidence_reference="Generated locally by live_long_form_smoke.py",
                        reviewer="Local verification",
                    ),
                )
                detected = service.detect_scenes(source.id)
                if len(detected.scenes) < 6 or any(
                    not (root / "sources" / item.representative_frame_path).is_file()
                    for item in detected.scenes
                ):
                    raise RuntimeError("Scene detection or representative frames failed")
                first_paths = {
                    (root / "sources" / item.representative_frame_path).resolve()
                    for item in detected.scenes
                }
                redetected = service.detect_scenes(source.id, force=True)
                second_paths = {
                    (root / "sources" / item.representative_frame_path).resolve()
                    for item in redetected.scenes
                }
                positions = [item.position for item in redetected.scenes]
                if (
                    len(redetected.scenes) != len(detected.scenes)
                    or positions != list(range(len(redetected.scenes)))
                    or first_paths & second_paths
                ):
                    raise RuntimeError("Atomic scene re-detection validation failed")
                scene_verification = (
                    source.id,
                    len(detected.scenes),
                    len(redetected.scenes),
                    first_paths,
                    second_paths,
                )
                service.transcribe(
                    source.id,
                    SourceTranscriptionRequest(
                        provider="mock",
                        model="smoke",
                        language="es",
                        segments=[
                            TranscriptSegmentInput(
                                start_seconds=0,
                                end_seconds=5,
                                text="Proceso industrial sintético.",
                                confidence=1,
                                language="es",
                            )
                        ],
                    ),
                )
                service.analyze(source.id, SourceAnalysisRequest(profile=AnalysisProfile.BALANCED))
                concepts = service.generate_concepts(
                    source.id, ConceptGenerationRequest(count=3, target_duration=30)
                )
                if len(concepts) != 3:
                    raise RuntimeError("Multiple concept generation failed")
                first = service.approve_concept(
                    concepts[0].id,
                    ReviewAction(reviewer="Local verification", notes="Smoke approval"),
                )
                second = service.approve_concept(
                    concepts[1].id,
                    ReviewAction(reviewer="Local verification", notes="Independent derivative"),
                )
                if (
                    not first.candidate_id
                    or not second.candidate_id
                    or first.candidate_id == second.candidate_id
                ):
                    raise RuntimeError("Independent derived candidate creation failed")
                source_file = service.storage.resolve(service.sources.get(source.id).relative_path)
                windows = ((1, 4), (21, 24), (51, 54))
                segments = [
                    SimpleNamespace(
                        source_start=start,
                        source_end=end,
                        playback_speed=1,
                        composition=CompositionStrategy.FIT,
                        loop_count=1,
                        hold_seconds=0,
                    )
                    for start, end in windows
                ]
                plan = SimpleNamespace(
                    segments=segments, width=360, height=640, fps=30, target_duration=9
                )
                renderer = FFmpegVideoRenderer(
                    ffmpeg, ffprobe, crf=24, audio_bitrate="128k", timeout_seconds=180
                )
                renderer.render(source_file, narration_path, output_path, plan)
                metadata = renderer.probe(output_path)
                if (
                    metadata.width != 360
                    or metadata.height != 640
                    or not metadata.has_audio
                    or abs(metadata.duration - 9) > 0.2
                ):
                    raise RuntimeError("Long-form multi-window output failed validation")
                print(
                    "LOCAL LONG-FORM MULTI-CLIP RENDER VERIFIED: "
                    f"windows={windows}, output={metadata.width}x{metadata.height}, "
                    f"duration={metadata.duration:.2f}s"
                )
            source_id, first_count, second_count, first_paths, second_paths = scene_verification
            stored_frames = {
                item.resolve()
                for item in (root / "sources" / str(source_id) / "frames").rglob("*.jpg")
            }
            if any(path.exists() for path in first_paths) or stored_frames != second_paths:
                raise RuntimeError("Committed scene frame cleanup validation failed")
            print(
                "LOCAL SCENE REDETECTION VERIFIED: "
                f"first={first_count}, second={second_count}, "
                f"frames={len(stored_frames)}, duplicates=0, orphans=0"
            )
        finally:
            engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
