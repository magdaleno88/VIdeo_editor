import pytest

from app.core.config import Settings
from app.core.errors import RenderConfigurationError
from app.providers.rendering.ffmpeg import resolve_binary
from scripts.live_long_form_smoke import main


def local_ffmpeg_available() -> bool:
    settings = Settings()
    try:
        resolve_binary(settings.ffmpeg_binary, "ffmpeg")
        resolve_binary(settings.ffprobe_binary, "ffprobe")
    except RenderConfigurationError:
        return False
    return True


@pytest.mark.skipif(not local_ffmpeg_available(), reason="Local FFmpeg is unavailable")
def test_long_form_real_local_smoke():
    assert main() == 0
