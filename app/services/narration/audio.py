import hashlib
import os
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path

from app.core.errors import AudioStorageError, NarrationValidationError


@dataclass(frozen=True)
class AudioMetadata:
    audio_format: str
    codec: str | None
    sample_rate: int | None
    channels: int | None
    duration_seconds: float


def inspect_audio(data: bytes, declared_format: str) -> AudioMetadata:
    if not data:
        raise NarrationValidationError("Narration audio is empty")
    if declared_format.upper() == "WAV":
        return _inspect_wav(data)
    if declared_format.upper() == "MP3":
        return _inspect_mp3(data)
    raise NarrationValidationError("Narration audio format is unsupported")


def _inspect_wav(data: bytes) -> AudioMetadata:
    import io

    try:
        with wave.open(io.BytesIO(data), "rb") as wav:
            rate = wav.getframerate()
            channels = wav.getnchannels()
            frames = wav.getnframes()
            if rate <= 0 or channels <= 0 or frames <= 0:
                raise ValueError
            return AudioMetadata("WAV", "PCM_S16LE", rate, channels, frames / rate)
    except (wave.Error, EOFError, ValueError):
        raise NarrationValidationError("Narration audio is not a valid WAV file") from None


def _inspect_mp3(data: bytes) -> AudioMetadata:
    offset = 0
    if data[:3] == b"ID3" and len(data) >= 10:
        size = sum((byte & 0x7F) << (7 * (3 - index)) for index, byte in enumerate(data[6:10]))
        offset = 10 + size
    duration = 0.0
    sample_rate = None
    frames = 0
    while offset + 4 <= len(data):
        header = int.from_bytes(data[offset : offset + 4], "big")
        if (header >> 21) & 0x7FF != 0x7FF:
            break
        version_bits, layer_bits = (header >> 19) & 0x3, (header >> 17) & 0x3
        bitrate_index, rate_index, padding = (
            (header >> 12) & 0xF,
            (header >> 10) & 0x3,
            (header >> 9) & 0x1,
        )
        if version_bits == 1 or layer_bits != 1 or bitrate_index in (0, 15) or rate_index == 3:
            break
        base_rates = (44_100, 48_000, 32_000)
        rate = (
            base_rates[rate_index]
            if version_bits == 3
            else base_rates[rate_index] // (2 if version_bits == 2 else 4)
        )
        bitrates = (
            (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)
            if version_bits == 3
            else (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160)
        )
        bitrate = bitrates[bitrate_index] * 1000
        samples = 1152 if version_bits == 3 else 576
        frame_length = ((144000 if version_bits == 3 else 72000) * bitrate // rate) + padding
        if frame_length <= 4 or offset + frame_length > len(data):
            break
        duration += samples / rate
        sample_rate = rate
        frames += 1
        offset += frame_length
    if not frames or sample_rate is None or duration <= 0:
        raise NarrationValidationError("Narration audio is not a valid MP3 file")
    return AudioMetadata("MP3", "MP3", sample_rate, 1, duration)


class AudioStorage:
    def __init__(self, root: str) -> None:
        self.root = Path(root).resolve()

    def write(
        self, candidate_id: int, script_id: int, token: str, data: bytes, audio_format: str
    ) -> str:
        extension = {"MP3": ".mp3", "WAV": ".wav"}.get(audio_format.upper())
        if extension is None:
            raise AudioStorageError("Unsupported narration storage format")
        directory = (self.root / f"candidate_{candidate_id}" / f"script_{script_id}").resolve()
        if not directory.is_relative_to(self.root):
            raise AudioStorageError("Narration path is outside the configured storage root")
        directory.mkdir(parents=True, exist_ok=True)
        destination = (directory / f"narration_{token}{extension}").resolve()
        if not destination.is_relative_to(self.root) or destination.exists():
            raise AudioStorageError("Narration storage path is invalid or already exists")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=directory, prefix=".narration-", delete=False
            ) as handle:
                temporary = Path(handle.name)
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        except OSError as exc:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            raise AudioStorageError("Narration audio could not be stored") from exc
        return destination.relative_to(self.root).as_posix()

    def delete(self, relative_path: str) -> None:
        target = (self.root / relative_path).resolve()
        if not target.is_relative_to(self.root):
            raise AudioStorageError("Narration cleanup path is outside the configured storage root")
        try:
            target.unlink(missing_ok=True)
        except OSError as exc:
            raise AudioStorageError("Narration audio cleanup failed") from exc


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
