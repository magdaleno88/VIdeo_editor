"""Optional real-FFmpeg smoke test using only generated local media."""

import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

from app.core.config import Settings
from app.core.errors import RenderConfigurationError
from app.providers.rendering import FFmpegVideoRenderer
from app.providers.rendering.ffmpeg import resolve_binary
from app.schemas.domain import CompositionStrategy


def run(arguments: list[str]) -> None:
    result = subprocess.run(arguments, capture_output=True, text=True, timeout=30, check=False)
    if result.returncode:
        raise RuntimeError("Synthetic media generation failed")


def main() -> int:
    settings = Settings()
    try:
        ffmpeg = resolve_binary(settings.ffmpeg_binary, "ffmpeg")
        ffprobe = resolve_binary(settings.ffprobe_binary, "ffprobe")
    except RenderConfigurationError:
        print("LOCAL FFMPEG NOT VERIFIED: ffmpeg and ffprobe were not found")
        return 0
    with tempfile.TemporaryDirectory(prefix="icf-render-smoke-") as directory:
        root = Path(directory)
        source = root / "source.mp4"
        narration = root / "narration.wav"
        output = root / "output.mp4"
        run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=640x360:rate=30:duration=3",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(source),
            ]
        )
        run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=3",
                str(narration),
            ]
        )
        renderer = FFmpegVideoRenderer(
            ffmpeg, ffprobe, crf=24, audio_bitrate="128k", timeout_seconds=30
        )
        segment = SimpleNamespace(
            source_start=0,
            source_end=3,
            playback_speed=1,
            composition=CompositionStrategy.FIT,
            loop_count=1,
            hold_seconds=0,
        )
        plan = SimpleNamespace(segments=[segment], width=360, height=640, fps=30, target_duration=3)
        renderer.render(source, narration, output, plan)
        metadata = renderer.probe(output)
        if (
            not output.is_file()
            or output.stat().st_size == 0
            or not metadata.has_video
            or not metadata.has_audio
            or metadata.duration <= 0
            or metadata.width != plan.width
            or metadata.height != plan.height
            or metadata.fps is None
            or abs(metadata.fps - plan.fps) > 0.1
            or metadata.video_codec != "h264"
            or metadata.audio_codec != "aac"
            or "mp4" not in metadata.format_name.split(",")
        ):
            raise RuntimeError("Synthetic render did not pass ffprobe validation")
        print(
            "LOCAL FFMPEG VERIFIED: "
            f"{metadata.width}x{metadata.height}, {metadata.duration:.2f}s, "
            f"{metadata.fps:.2f}fps, {metadata.video_codec}/{metadata.audio_codec}, "
            f"{metadata.format_name}; output={output}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
