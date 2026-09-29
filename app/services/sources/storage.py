import hashlib
import os
import shutil
import subprocess
import uuid
from collections.abc import Iterable
from pathlib import Path

from app.core.errors import AssetTooLargeError, ConfigurationError, UnsupportedMediaError
from app.providers.rendering.ffmpeg import parse_probe, resolve_binary

ALLOWED_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm"}


class SourceStorage:
    def __init__(self, root: str | Path, max_size_bytes: int, ffprobe_binary: str = "") -> None:
        self.root = Path(root).resolve()
        self.max_size_bytes = max_size_bytes
        self.ffprobe = resolve_binary(ffprobe_binary, "ffprobe")
        self.root.mkdir(parents=True, exist_ok=True)
        self.incoming = self.root / ".incoming"
        self.incoming.mkdir(exist_ok=True)

    def stage(self, chunks: Iterable[bytes], original_filename: str) -> tuple[Path, str, int]:
        suffix = Path(original_filename).suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            raise UnsupportedMediaError("Source must be MP4, MOV, MKV, or WebM")
        path = self.incoming / f"{uuid.uuid4().hex}{suffix}.part"
        digest = hashlib.sha256()
        size = 0
        try:
            with path.open("xb") as output:
                for chunk in chunks:
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > self.max_size_bytes:
                        raise AssetTooLargeError("Source exceeds the configured upload limit")
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            if not size:
                raise UnsupportedMediaError("Source file is empty")
            return path, digest.hexdigest(), size
        except Exception:
            path.unlink(missing_ok=True)
            raise

    def stage_file(self, source: Path) -> tuple[Path, str, int]:
        def chunks():
            with source.open("rb") as input_file:
                while chunk := input_file.read(1024 * 1024):
                    yield chunk

        return self.stage(chunks(), source.name)

    def probe(self, path: Path):
        try:
            result = subprocess.run(
                [
                    self.ffprobe,
                    "-v",
                    "error",
                    "-show_streams",
                    "-show_format",
                    "-of",
                    "json",
                    str(path),
                ],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise ConfigurationError("ffprobe could not inspect the source") from None
        if result.returncode:
            raise UnsupportedMediaError("Uploaded content is not a readable video")
        import json

        try:
            metadata = parse_probe(json.loads(result.stdout))
        except (ValueError, TypeError):
            raise UnsupportedMediaError("Uploaded content has invalid media metadata") from None
        if not metadata.has_video or not metadata.width or not metadata.height or not metadata.fps:
            raise UnsupportedMediaError("Source requires a valid video stream and frame rate")
        return metadata

    def finalize(self, staged: Path, source_id: int, original_filename: str) -> str:
        suffix = Path(original_filename).suffix.lower()
        directory = (self.root / str(source_id)).resolve()
        if self.root not in directory.parents:
            raise ConfigurationError("Source storage path escaped its configured root")
        directory.mkdir(parents=False, exist_ok=False)
        destination = directory / f"original{suffix}"
        os.replace(staged, destination)
        return destination.relative_to(self.root).as_posix()

    def resolve(self, relative_path: str) -> Path:
        path = (self.root / relative_path).resolve()
        if self.root not in path.parents or not path.is_file():
            raise UnsupportedMediaError("Stored source path is invalid or unavailable")
        return path

    def discard(self, staged: Path) -> None:
        staged.unlink(missing_ok=True)

    def cleanup_source(self, source_id: int) -> None:
        directory = (self.root / str(source_id)).resolve()
        if self.root in directory.parents:
            shutil.rmtree(directory, ignore_errors=True)
