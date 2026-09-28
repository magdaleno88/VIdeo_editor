from pathlib import Path

from app.core.errors import RenderProcessError, RenderValidationError
from app.providers.rendering import FFmpegVideoRenderer


def escape_filter_path(path: Path) -> str:
    value = path.resolve().as_posix()
    return value.replace("\\", r"\\").replace(":", r"\:").replace("'", r"\'")


class CaptionFFmpegRenderer:
    version = "caption-ffmpeg-1.0"

    def __init__(self, executor: FFmpegVideoRenderer) -> None:
        self.executor = executor
        self.ffmpeg = executor.ffmpeg
        self.ffprobe = executor.ffprobe
        self.ffmpeg_version = executor.version

    def build_command(
        self,
        source: Path,
        subtitles: Path,
        output: Path,
        *,
        font_path: Path | None = None,
        branding_path: Path | None = None,
        branding_opacity: float = 0.7,
    ) -> list[str]:
        subtitle_filter = f"subtitles=filename='{escape_filter_path(subtitles)}'"
        if font_path:
            subtitle_filter += f":fontsdir='{escape_filter_path(font_path.parent)}'"
        command = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source)]
        if branding_path:
            command.extend(["-loop", "1", "-i", str(branding_path)])
            command.extend(
                [
                    "-filter_complex",
                    f"[0:v]{subtitle_filter}[captioned];"
                    f"[1:v]format=rgba,colorchannelmixer=aa={branding_opacity:.3f}[logo0];"
                    "[logo0][captioned]scale2ref=w=main_w*0.14:h=ow/mdar[logo][base];"
                    "[base][logo]overlay=main_w-overlay_w-main_w*0.08:main_h*0.08[outv]",
                    "-map",
                    "[outv]",
                    "-map",
                    "0:a:0",
                ]
            )
        else:
            command.extend(["-vf", subtitle_filter, "-map", "0:v:0", "-map", "0:a:0"])
        command.extend(
            [
                "-c:v",
                "libx264",
                "-crf",
                "20",
                "-preset",
                "medium",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                str(output),
            ]
        )
        return command

    def render(self, source: Path, subtitles: Path, output: Path, **kwargs) -> int:
        result = self.executor._run(self.build_command(source, subtitles, output, **kwargs))
        if result.returncode:
            tail = "\n".join(result.stderr.splitlines()[-20:])[-4000:]
            raise RenderProcessError(
                f"FFmpeg caption rendering failed: {tail or 'no diagnostic output'}",
                exit_code=result.returncode,
            )
        return result.returncode

    def preview(self, source: Path, output: Path, time_seconds: float) -> None:
        result = self.executor._run(
            [
                self.ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                f"{time_seconds:.3f}",
                "-i",
                str(source),
                "-frames:v",
                "1",
                str(output),
            ]
        )
        if result.returncode or not output.is_file() or output.stat().st_size == 0:
            raise RenderValidationError("FFmpeg could not create the preview frame")

    def probe(self, path: Path):
        return self.executor.probe(path)
