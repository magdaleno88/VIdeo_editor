from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.models import VideoEditPlan


@dataclass(frozen=True)
class MediaMetadata:
    duration: float
    width: int | None
    height: int | None
    fps: float | None
    video_codec: str | None
    audio_codec: str | None
    has_video: bool
    has_audio: bool
    rotation: int
    format_name: str


@dataclass(frozen=True)
class RenderExecution:
    exit_code: int
    stderr_tail: str


class VideoRenderer(Protocol):
    name: str
    version: str

    def probe(self, path: Path) -> MediaMetadata: ...

    def build_command(
        self, source: Path, narration: Path, output: Path, plan: VideoEditPlan
    ) -> list[str]: ...

    def render(
        self, source: Path, narration: Path, output: Path, plan: VideoEditPlan
    ) -> RenderExecution: ...
