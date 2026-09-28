import hashlib
import os
import uuid
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from app.core.errors import RenderStorageError


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class RenderPaths:
    temporary: Path
    final: Path
    relative: str


class RenderStorage:
    def __init__(self, root: str) -> None:
        self.root = Path(root).resolve()

    def _inside_root(self, path: Path) -> Path:
        resolved = path.resolve()
        if not resolved.is_relative_to(self.root):
            raise RenderStorageError("Render storage path escaped its configured root")
        return resolved

    def prepare(self, candidate_id: int, render_id: int) -> RenderPaths:
        directory = self._inside_root(self.root / f"candidate_{candidate_id}")
        directory.mkdir(parents=True, exist_ok=True)
        final = self._inside_root(directory / f"render_{render_id}.mp4")
        if final.exists():
            raise RenderStorageError("Render output path already exists")
        temporary = self._inside_root(directory / f".{uuid.uuid4().hex}.part.mp4")
        return RenderPaths(temporary, final, final.relative_to(self.root).as_posix())

    def finalize(self, paths: RenderPaths) -> None:
        if not paths.temporary.is_file() or paths.temporary.stat().st_size <= 0:
            raise RenderStorageError("FFmpeg did not produce a non-empty output file")
        try:
            os.replace(paths.temporary, paths.final)
        except OSError:
            raise RenderStorageError("Render output could not be finalized") from None

    def resolve(self, relative: str) -> Path:
        if Path(relative).is_absolute():
            raise RenderStorageError("Stored render path must be relative")
        return self._inside_root(self.root / relative)

    @staticmethod
    def cleanup(paths: RenderPaths) -> None:
        for path in (paths.temporary, paths.final):
            with suppress(OSError):
                path.unlink(missing_ok=True)


class NarrationFileResolver:
    def __init__(self, root: str) -> None:
        self.root = Path(root).resolve()

    def resolve(self, relative: str) -> Path:
        path = Path(relative)
        if path.is_absolute():
            raise RenderStorageError("Narration storage path must be relative")
        resolved = (self.root / path).resolve()
        if not resolved.is_relative_to(self.root):
            raise RenderStorageError("Narration path escaped its configured root")
        if not resolved.is_file() or resolved.stat().st_size <= 0:
            raise RenderStorageError("Approved narration audio is missing")
        return resolved
