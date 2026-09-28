"""Real local FFmpeg smoke test for ASS captions and a preview frame."""

import os
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

from app.core.config import Settings
from app.providers.captions import ASSSubtitleRenderer, CaptionFFmpegRenderer
from app.providers.rendering import FFmpegVideoRenderer
from app.providers.rendering.ffmpeg import resolve_binary
from app.schemas.domain import CaptionPosition, CaptionStyleProfile


def run(arguments: list[str]) -> None:
    result = subprocess.run(arguments, capture_output=True, text=True, timeout=30, check=False)
    if result.returncode:
        raise RuntimeError("Synthetic caption media generation failed: " + result.stderr[-1000:])


def main() -> int:
    settings = Settings()
    try:
        ffmpeg = resolve_binary(settings.ffmpeg_binary, "ffmpeg")
        ffprobe = resolve_binary(settings.ffprobe_binary, "ffprobe")
    except Exception:
        print("LOCAL CAPTION RENDER NOT VERIFIED: ffmpeg and ffprobe were not found")
        return 0
    destination = Path(settings.render_storage_root).resolve() / "smoke"
    destination.mkdir(parents=True, exist_ok=True)
    output = destination / "caption_smoke.mp4"
    preview = destination / "caption_smoke_preview.png"
    part = destination / ".caption_smoke.part.mp4"
    output.unlink(missing_ok=True)
    preview.unlink(missing_ok=True)
    part.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="icf-caption-smoke-") as directory:
        root = Path(directory)
        source = root / "raw.mp4"
        subtitle = root / "captions.ass"
        run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=360x640:rate=30:duration=3",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=3",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-shortest",
                str(source),
            ]
        )
        item = SimpleNamespace(
            display_text="¿Cómo cambia el acero a 250 °C?",
            start_seconds=0.2,
            end_seconds=2.8,
            position_name=CaptionPosition.LOWER,
            style={"max_characters": 36, "max_lines": 2},
            emphasis_spans=[{"text": "250 °C", "kind": "PHRASE"}],
        )
        overlay = SimpleNamespace(
            text="De acero a pieza",
            start_seconds=0,
            end_seconds=1.8,
            position_name=CaptionPosition.UPPER,
            z_index=10,
        )
        plan = SimpleNamespace(
            style_profile=CaptionStyleProfile.CLEAN,
            safe_area={"top": 0.08, "bottom": 0.18, "left": 0.08, "right": 0.16},
            items=[item],
            overlays=[overlay],
        )
        subtitle.write_text(ASSSubtitleRenderer().render(plan, 360, 640), encoding="utf-8-sig")
        executor = FFmpegVideoRenderer(
            ffmpeg, ffprobe, crf=24, audio_bitrate="128k", timeout_seconds=30
        )
        renderer = CaptionFFmpegRenderer(executor)
        renderer.render(source, subtitle, part)
        metadata = renderer.probe(part)
        if (
            not metadata.has_video
            or not metadata.has_audio
            or metadata.width != 360
            or metadata.height != 640
            or metadata.fps is None
            or abs(metadata.fps - 30) > 0.1
            or metadata.video_codec != "h264"
            or metadata.audio_codec != "aac"
            or metadata.duration <= 0
            or "mp4" not in metadata.format_name.split(",")
        ):
            raise RuntimeError("Decorated smoke render failed ffprobe validation")
        os.replace(part, output)
        renderer.preview(output, preview, 1.5)
    print(
        "LOCAL CAPTION RENDER VERIFIED: "
        f"{metadata.width}x{metadata.height}, {metadata.duration:.2f}s, "
        f"{metadata.fps:.2f}fps, {metadata.video_codec}/{metadata.audio_codec}; "
        f"output={output}; preview={preview}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
