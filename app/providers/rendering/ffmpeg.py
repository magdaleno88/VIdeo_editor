import json
import math
import shutil
import subprocess
from pathlib import Path

from app.core.errors import (
    RenderConfigurationError,
    RenderProcessError,
    RenderTimeoutError,
    RenderValidationError,
)
from app.models import VideoEditPlan
from app.providers.rendering.base import MediaMetadata, RenderExecution
from app.schemas.domain import CompositionStrategy


def resolve_binary(configured: str, default: str) -> str:
    value = configured.strip()
    if "\x00" in value:
        raise RenderConfigurationError(f"Invalid {default} binary configuration")
    resolved = shutil.which(value or default)
    if resolved is None:
        raise RenderConfigurationError(
            f"{default} was not found; install FFmpeg or configure its binary path"
        )
    return resolved


def parse_probe(payload: dict) -> MediaMetadata:
    streams = payload.get("streams")
    format_data = payload.get("format")
    if not isinstance(streams, list) or not isinstance(format_data, dict):
        raise RenderValidationError("ffprobe returned malformed metadata")
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    try:
        duration = float(format_data.get("duration") or (video or {}).get("duration"))
    except (TypeError, ValueError):
        raise RenderValidationError("Media duration is missing or invalid") from None
    if not math.isfinite(duration) or duration <= 0:
        raise RenderValidationError("Media duration must be positive")
    fps = None
    if video:
        value = video.get("avg_frame_rate") or video.get("r_frame_rate")
        try:
            numerator, denominator = str(value).split("/", 1)
            fps = float(numerator) / float(denominator)
        except (TypeError, ValueError, ZeroDivisionError):
            fps = None
    tags = (video or {}).get("tags") or {}
    side_data = (video or {}).get("side_data_list") or []
    rotation = int(tags.get("rotate", 0) or 0)
    for item in side_data:
        if "rotation" in item:
            rotation = int(item["rotation"])
    return MediaMetadata(
        duration=duration,
        width=int(video["width"]) if video and video.get("width") else None,
        height=int(video["height"]) if video and video.get("height") else None,
        fps=fps,
        video_codec=video.get("codec_name") if video else None,
        audio_codec=audio.get("codec_name") if audio else None,
        has_video=video is not None,
        has_audio=audio is not None,
        rotation=rotation,
        format_name=str(format_data.get("format_name", "")),
    )


class FFmpegCommandBuilder:
    def __init__(self, binary: str, *, crf: int, audio_bitrate: str) -> None:
        self.binary = binary
        self.crf = crf
        if not audio_bitrate.endswith("k") or not audio_bitrate[:-1].isdigit():
            raise RenderConfigurationError("RENDER_AUDIO_BITRATE must look like 192k")
        self.audio_bitrate = audio_bitrate

    @staticmethod
    def _compose(label: str, output: str, strategy, width: int, height: int) -> list[str]:
        if strategy == CompositionStrategy.CENTER_CROP:
            return [
                f"[{label}]scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height}[{output}]"
            ]
        if strategy == CompositionStrategy.FIT:
            return [
                f"[{label}]scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black[{output}]"
            ]
        if strategy == CompositionStrategy.BLURRED_BACKGROUND:
            return [
                f"[{label}]split=2[{output}bg][{output}fg]",
                f"[{output}bg]scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},boxblur=20:2[{output}b]",
                f"[{output}fg]scale={width}:{height}:force_original_aspect_ratio=decrease"
                f"[{output}f]",
                f"[{output}b][{output}f]overlay=(W-w)/2:(H-h)/2[{output}]",
            ]
        raise RenderConfigurationError("Unsupported composition strategy")

    def build(self, source: Path, narration: Path, output: Path, plan: VideoEditPlan) -> list[str]:
        filters: list[str] = []
        labels: list[str] = []
        sequence = 0
        for segment in plan.segments:
            for repeat in range(segment.loop_count):
                raw = f"s{sequence}raw"
                composed = f"s{sequence}composed"
                final = f"s{sequence}"
                trim = (
                    f"[0:v]trim=start={segment.source_start:.6f}:end={segment.source_end:.6f},"
                    f"setpts=(PTS-STARTPTS)/{segment.playback_speed:.6f}[{raw}]"
                )
                filters.append(trim)
                filters.extend(
                    self._compose(raw, composed, segment.composition, plan.width, plan.height)
                )
                suffix = f"fps={plan.fps:.6f},format=yuv420p"
                if repeat == segment.loop_count - 1 and segment.hold_seconds > 0:
                    suffix += f",tpad=stop_mode=clone:stop_duration={segment.hold_seconds:.6f}"
                filters.append(f"[{composed}]{suffix}[{final}]")
                labels.append(f"[{final}]")
                sequence += 1
        if not labels:
            raise RenderConfigurationError("Edit plan contains no segments")
        filters.append(f"{''.join(labels)}concat=n={len(labels)}:v=1:a=0[outv]")
        return [
            self.binary,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-i",
            str(narration),
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[outv]",
            "-map",
            "1:a:0",
            "-t",
            f"{plan.target_duration:.6f}",
            "-c:v",
            "libx264",
            "-crf",
            str(self.crf),
            "-preset",
            "medium",
            "-c:a",
            "aac",
            "-b:a",
            self.audio_bitrate,
            "-movflags",
            "+faststart",
            str(output),
        ]


class FFmpegVideoRenderer:
    name = "ffmpeg"

    def __init__(
        self,
        ffmpeg_binary: str,
        ffprobe_binary: str,
        *,
        crf: int,
        audio_bitrate: str,
        timeout_seconds: float,
    ) -> None:
        self.ffmpeg = resolve_binary(ffmpeg_binary, "ffmpeg")
        self.ffprobe = resolve_binary(ffprobe_binary, "ffprobe")
        self.timeout_seconds = timeout_seconds
        self.builder = FFmpegCommandBuilder(self.ffmpeg, crf=crf, audio_bitrate=audio_bitrate)
        self.version = self._version()

    def _run(self, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                arguments,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            raise RenderTimeoutError("FFmpeg process exceeded the configured timeout") from None
        except OSError:
            raise RenderProcessError("FFmpeg process could not be started") from None

    def _version(self) -> str:
        result = self._run([self.ffmpeg, "-version"])
        if result.returncode != 0:
            raise RenderConfigurationError("FFmpeg binary failed its version check")
        return result.stdout.splitlines()[0][:200]

    def probe(self, path: Path) -> MediaMetadata:
        result = self._run(
            [
                self.ffprobe,
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(path),
            ]
        )
        if result.returncode != 0:
            raise RenderValidationError("ffprobe could not read the media file")
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            raise RenderValidationError("ffprobe returned invalid JSON") from None
        return parse_probe(payload)

    def build_command(
        self, source: Path, narration: Path, output: Path, plan: VideoEditPlan
    ) -> list[str]:
        return self.builder.build(source, narration, output, plan)

    def render(
        self, source: Path, narration: Path, output: Path, plan: VideoEditPlan
    ) -> RenderExecution:
        result = self._run(self.build_command(source, narration, output, plan))
        tail = "\n".join(result.stderr.splitlines()[-20:])[-4000:]
        if result.returncode != 0:
            raise RenderProcessError(
                f"FFmpeg rendering failed: {tail or 'no diagnostic output'}",
                exit_code=result.returncode,
            )
        return RenderExecution(result.returncode, tail)
