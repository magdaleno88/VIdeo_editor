import base64
import json

import httpx
import pytest

from app.core.errors import NarrationError, NarrationTimeoutError, NarrationValidationError
from app.providers.tts.elevenlabs import ElevenLabsTTSProvider
from app.schemas.domain import VoiceSettings


def provider(handler, *, retries=0):
    return ElevenLabsTTSProvider(
        "secret-value",
        "eleven_multilingual_v2",
        1,
        retries,
        httpx.Client(transport=httpx.MockTransport(handler)),
    )


def valid_response():
    return {
        "audio_base64": base64.b64encode(b"test-audio").decode(),
        "alignment": {
            "characters": list("hola"),
            "character_start_times_seconds": [0, 0.1, 0.2, 0.3],
            "character_end_times_seconds": [0.1, 0.2, 0.3, 0.4],
        },
    }


def test_elevenlabs_uses_timestamps_endpoint_and_supported_settings():
    observed = {}

    def handler(request):
        observed["url"] = str(request.url)
        observed["key"] = request.headers["xi-api-key"]
        observed["body"] = json.loads(request.content)
        return httpx.Response(200, json=valid_response())

    result = provider(handler).synthesize(
        "hola", "voice-1", "es", VoiceSettings(stability=0.4, speed=1.1)
    )
    assert observed["url"].endswith("/voice-1/with-timestamps?output_format=mp3_44100_128")
    assert observed["key"] == "secret-value"
    assert observed["body"] == {
        "text": "hola",
        "model_id": "eleven_multilingual_v2",
        "language_code": "es",
        "apply_text_normalization": "off",
        "voice_settings": {"stability": 0.4, "speed": 1.1},
    }
    assert result.character_alignment.characters == list("hola")


def test_elevenlabs_maps_errors_retries_and_malformed_responses():
    calls = 0

    def transient(request):
        nonlocal calls
        calls += 1
        return httpx.Response(500 if calls == 1 else 200, json=valid_response())

    assert (
        provider(transient, retries=1)
        .synthesize("hola", "voice", "es", VoiceSettings())
        .audio_bytes
    )
    assert calls == 2

    with pytest.raises(NarrationError, match="credentials"):
        provider(lambda request: httpx.Response(401)).synthesize(
            "hola", "voice", "es", VoiceSettings()
        )
    with pytest.raises(NarrationValidationError, match="malformed alignment"):
        provider(
            lambda request: httpx.Response(
                200, json={"audio_base64": "YQ==", "alignment": {"characters": []}}
            )
        ).synthesize("hola", "voice", "es", VoiceSettings())

    def timeout(request):
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(NarrationTimeoutError):
        provider(timeout).synthesize("hola", "voice", "es", VoiceSettings())
