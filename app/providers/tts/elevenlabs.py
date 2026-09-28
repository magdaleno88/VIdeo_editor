import base64
import time

import httpx

from app.core.errors import NarrationError, NarrationTimeoutError, NarrationValidationError
from app.providers.tts.base import CharacterAlignment, SynthesizedAudio
from app.schemas.domain import VoiceSettings


class ElevenLabsTTSProvider:
    name = "elevenlabs"
    endpoint = "https://api.elevenlabs.io/v1/text-to-speech"
    output_format = "mp3_44100_128"

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_seconds: float,
        max_retries: int,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.client = client or httpx.Client(timeout=timeout_seconds, follow_redirects=False)
        self._owns_client = client is None

    def synthesize(
        self, text: str, voice_id: str, language: str, settings: VoiceSettings
    ) -> SynthesizedAudio:
        payload = {
            "text": text,
            "model_id": self.model,
            "language_code": language,
            "apply_text_normalization": "off",
        }
        values = settings.supported_values()
        if values:
            payload["voice_settings"] = values
        for attempt in range(self.max_retries + 1):
            try:
                response = self.client.post(
                    f"{self.endpoint}/{voice_id}/with-timestamps",
                    params={"output_format": self.output_format},
                    headers={"xi-api-key": self.api_key, "Content-Type": "application/json"},
                    json=payload,
                )
                if response.status_code in (401, 403):
                    raise NarrationError("ElevenLabs credentials were rejected")
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt < self.max_retries:
                        time.sleep(0.25 * (attempt + 1))
                        continue
                    raise NarrationError("ElevenLabs quota, rate limit, or service error")
                response.raise_for_status()
                return self._parse_response(response.json())
            except httpx.TimeoutException:
                if attempt < self.max_retries:
                    time.sleep(0.25 * (attempt + 1))
                    continue
                raise NarrationTimeoutError("ElevenLabs request timed out") from None
            except httpx.RequestError:
                if attempt < self.max_retries:
                    time.sleep(0.25 * (attempt + 1))
                    continue
                raise NarrationError("ElevenLabs request failed") from None
            except httpx.HTTPStatusError as exc:
                raise NarrationError(
                    f"ElevenLabs returned HTTP {exc.response.status_code}"
                ) from None
        raise NarrationError("ElevenLabs request failed")

    def _parse_response(self, payload: dict) -> SynthesizedAudio:
        try:
            audio = base64.b64decode(payload["audio_base64"], validate=True)
        except (KeyError, TypeError, ValueError) as exc:
            raise NarrationValidationError("ElevenLabs returned malformed audio") from exc
        alignment = payload.get("alignment")
        parsed_alignment = None
        if alignment is not None:
            try:
                characters = list(alignment["characters"])
                starts = [float(item) for item in alignment["character_start_times_seconds"]]
                ends = [float(item) for item in alignment["character_end_times_seconds"]]
                if not (len(characters) == len(starts) == len(ends)):
                    raise ValueError
                parsed_alignment = CharacterAlignment(characters, starts, ends)
            except (KeyError, TypeError, ValueError):
                raise NarrationValidationError("ElevenLabs returned malformed alignment") from None
        return SynthesizedAudio(
            audio_bytes=audio,
            audio_format="MP3",
            codec="MP3",
            sample_rate=44_100,
            channels=1,
            character_alignment=parsed_alignment,
            provider_metadata={
                "output_format": self.output_format,
                "alignment": alignment is not None,
            },
        )

    def close(self) -> None:
        if self._owns_client:
            self.client.close()
