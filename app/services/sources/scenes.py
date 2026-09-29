import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.core.errors import RenderProcessError
from app.providers.rendering.ffmpeg import resolve_binary

PTS_PATTERN = re.compile(r"pts_time:([0-9]+(?:\.[0-9]+)?)")


@dataclass(frozen=True)
class DetectedScene:
    start: float
    end: float
    frame_path: Path


class FFmpegSceneDetector:
    def __init__(
        self,
        ffmpeg_binary: str,
        *,
        threshold: float,
        minimum_duration: float,
        merge_threshold: float,
        max_scenes: int,
    ) -> None:
        self.ffmpeg = resolve_binary(ffmpeg_binary, "ffmpeg")
        self.threshold = threshold
        self.minimum_duration = minimum_duration
        self.merge_threshold = merge_threshold
        self.max_scenes = max_scenes

    def detect(self, source: Path, duration: float, frame_directory: Path) -> list[DetectedScene]:
        result = subprocess.run(
            [
                self.ffmpeg,
                "-hide_banner",
                "-i",
                str(source),
                "-vf",
                f"select='gt(scene,{self.threshold})',showinfo",
                "-an",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=max(120, int(duration * 2)),
            check=False,
            shell=False,
        )
        if result.returncode:
            raise RenderProcessError("FFmpeg scene detection failed")
        cuts = sorted({float(value) for value in PTS_PATTERN.findall(result.stderr)})
        boundaries = self._merge([0.0, *cuts, duration], duration)
        frame_directory.mkdir(parents=True, exist_ok=True)
        scenes = []
        for position, (start, end) in enumerate(zip(boundaries, boundaries[1:], strict=False)):
            frame = frame_directory / f"scene-{position + 1:04d}.jpg"
            midpoint = start + (end - start) / 2
            extraction = subprocess.run(
                [
                    self.ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-ss",
                    f"{midpoint:.6f}",
                    "-i",
                    str(source),
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=640:-2",
                    "-y",
                    str(frame),
                ],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
                shell=False,
            )
            if extraction.returncode:
                raise RenderProcessError("Representative frame extraction failed")
            scenes.append(DetectedScene(start, end, frame))
        return scenes

    def _merge(self, boundaries: list[float], duration: float) -> list[float]:
        accepted = [0.0]
        minimum = max(self.minimum_duration, self.merge_threshold)
        for cut in boundaries[1:-1]:
            if cut - accepted[-1] >= minimum and duration - cut >= self.minimum_duration:
                accepted.append(cut)
        accepted.append(duration)
        if len(accepted) - 1 <= self.max_scenes:
            return accepted
        step = (len(accepted) - 2) / (self.max_scenes - 1) if self.max_scenes > 1 else 0
        selected = [accepted[0]]
        for index in range(1, self.max_scenes):
            selected.append(accepted[round(index * step)])
        selected.append(duration)
        return sorted(set(selected))
