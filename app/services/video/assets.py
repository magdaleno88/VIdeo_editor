import tempfile
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from app.core.errors import AssetTooLargeError, AssetUnavailableError, UnsupportedMediaError
from app.models import VideoCandidate

ALLOWED_SOURCES = {
    "pexels": {"videos.pexels.com", "player.vimeo.com", "vod-progressive.akamaized.net"},
    "pixabay": {"cdn.pixabay.com"},
}
ALLOWED_MIME = {"video/mp4": ".mp4", "video/webm": ".webm", "video/quicktime": ".mov"}


@dataclass(frozen=True)
class TemporaryVideoAsset:
    path: Path
    mime_type: str
    size_bytes: int


class VideoAssetFetcher:
    def __init__(
        self,
        client: httpx.Client,
        max_size_bytes: int,
        max_duration_seconds: float,
        temp_directory: Path | None = None,
    ) -> None:
        self.client = client
        self.max_size_bytes = max_size_bytes
        self.max_duration_seconds = max_duration_seconds
        self.temp_directory = temp_directory

    @contextmanager
    def fetch(self, candidate: VideoCandidate) -> Iterator[TemporaryVideoAsset]:
        if not candidate.preview_url:
            raise AssetUnavailableError("Candidate has no temporary preview URL")
        if candidate.duration is None:
            raise AssetUnavailableError("Candidate duration is unknown; AI analysis is blocked")
        if candidate.duration > self.max_duration_seconds:
            raise AssetTooLargeError(
                f"Candidate duration exceeds the {self.max_duration_seconds:g}-second limit"
            )
        self._validate_url(candidate.provider, candidate.preview_url)
        path: Path | None = None
        try:
            try:
                path, mime, total, header = self._download(candidate.preview_url)
            except (httpx.TimeoutException, httpx.RequestError):
                raise AssetUnavailableError("Preview download timed out or failed") from None
            except ValueError:
                raise AssetUnavailableError("Preview returned an invalid content length") from None
            self._validate_signature(mime, header)
            yield TemporaryVideoAsset(path=path, mime_type=mime, size_bytes=total)
        finally:
            if path is not None:
                with suppress(OSError):
                    path.unlink(missing_ok=True)

    def _download(self, url: str) -> tuple[Path, str, int, bytes]:
        path: Path | None = None
        try:
            with self.client.stream("GET", url) as response:
                if response.status_code in (401, 403, 404, 410):
                    raise AssetUnavailableError("Candidate preview is unavailable or expired")
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError:
                    raise AssetUnavailableError(
                        f"Preview provider returned HTTP {response.status_code}"
                    ) from None
                mime = response.headers.get("content-type", "").split(";", 1)[0].lower().strip()
                if mime not in ALLOWED_MIME:
                    raise UnsupportedMediaError(
                        "Preview response is not a supported video MIME type"
                    )
                length = response.headers.get("content-length")
                if length and int(length) > self.max_size_bytes:
                    raise AssetTooLargeError("Preview exceeds the configured file-size limit")
                with tempfile.NamedTemporaryFile(
                    prefix="icf-video-",
                    suffix=ALLOWED_MIME[mime],
                    dir=self.temp_directory,
                    delete=False,
                ) as temporary:
                    path = Path(temporary.name)
                    total = 0
                    header = b""
                    for chunk in response.iter_bytes(64 * 1024):
                        total += len(chunk)
                        if total > self.max_size_bytes:
                            raise AssetTooLargeError(
                                "Preview exceeds the configured file-size limit"
                            )
                        if len(header) < 16:
                            header += chunk[: 16 - len(header)]
                        temporary.write(chunk)
                return path, mime, total, header
        except Exception:
            if path is not None:
                with suppress(OSError):
                    path.unlink(missing_ok=True)
            raise

    @staticmethod
    def _validate_url(provider: str, url: str) -> None:
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError:
            raise AssetUnavailableError("Preview URL failed the HTTPS safety policy") from None
        allowed = ALLOWED_SOURCES.get(provider, set())
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or port not in (None, 443)
        ):
            raise AssetUnavailableError("Preview URL failed the HTTPS safety policy")
        hostname = (parsed.hostname or "").lower()
        if hostname not in allowed:
            raise AssetUnavailableError("Preview host is not allowed for this candidate provider")

    @staticmethod
    def _validate_signature(mime: str, header: bytes) -> None:
        valid = {
            "video/mp4": len(header) >= 8 and header[4:8] == b"ftyp",
            "video/quicktime": len(header) >= 8 and header[4:8] == b"ftyp",
            "video/webm": header.startswith(b"\x1aE\xdf\xa3"),
        }[mime]
        if not valid:
            raise UnsupportedMediaError(
                "Preview content does not match its declared video MIME type"
            )
