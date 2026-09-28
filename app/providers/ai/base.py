from typing import Protocol

from app.schemas.domain import VideoVisualAnalysisPayload
from app.services.video.assets import TemporaryVideoAsset


class VideoAnalysisProvider(Protocol):
    name: str
    model: str
    prompt_version: str

    def analyze(self, asset: TemporaryVideoAsset) -> VideoVisualAnalysisPayload: ...
