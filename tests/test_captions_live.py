import pytest

from app.core.config import Settings
from app.core.errors import RenderConfigurationError
from app.providers.rendering.ffmpeg import resolve_binary
from scripts.live_caption_smoke import main


def local_ffmpeg_available() -> bool:
    settings = Settings()
    try:
        resolve_binary(settings.ffmpeg_binary, "ffmpeg")
        resolve_binary(settings.ffprobe_binary, "ffprobe")
    except RenderConfigurationError:
        return False
    return True


@pytest.mark.skipif(not local_ffmpeg_available(), reason="Local FFmpeg binaries are unavailable")
def test_local_caption_render():
    assert main() == 0
