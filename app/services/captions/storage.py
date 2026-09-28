import os
import uuid
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from app.core.errors import RenderStorageError


@dataclass(frozen=True)
class CaptionPaths:
    ass_temporary: Path
    ass_final: Path
    ass_relative: str
    srt_temporary: Path
    srt_final: Path
    srt_relative: str
    video_temporary: Path
    video_final: Path
    video_relative: str


class CaptionStorage:
    def __init__(self, caption_root: str, render_root: str) -> None:
        self.caption_root = Path(caption_root).resolve()
        self.render_root = Path(render_root).resolve()

    @staticmethod
    def _inside(root: Path, path: Path) -> Path:
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            raise RenderStorageError("Caption storage path escaped its configured root")
        return resolved

    def prepare(self, candidate_id: int, plan_id: int, render_id: int) -> CaptionPaths:
        caption_dir = self._inside(
            self.caption_root, self.caption_root / f"candidate_{candidate_id}"
        )
        video_dir = self._inside(
            self.render_root, self.render_root / f"candidate_{candidate_id}" / "decorated"
        )
        caption_dir.mkdir(parents=True, exist_ok=True)
        video_dir.mkdir(parents=True, exist_ok=True)
        ass = self._inside(
            caption_dir, caption_dir / f"caption_plan_{plan_id}_render_{render_id}.ass"
        )
        srt = self._inside(
            caption_dir, caption_dir / f"caption_plan_{plan_id}_render_{render_id}.srt"
        )
        video = self._inside(video_dir, video_dir / f"final_render_{render_id}.mp4")
        if any(path.exists() for path in (ass, srt, video)):
            raise RenderStorageError("Caption or final-render output path already exists")
        nonce = uuid.uuid4().hex
        return CaptionPaths(
            ass.with_name(f".{nonce}.part.ass"),
            ass,
            ass.relative_to(self.caption_root).as_posix(),
            srt.with_name(f".{nonce}.part.srt"),
            srt,
            srt.relative_to(self.caption_root).as_posix(),
            video.with_name(f".{nonce}.part.mp4"),
            video,
            video.relative_to(self.render_root).as_posix(),
        )

    @staticmethod
    def finalize(paths: CaptionPaths) -> None:
        for temporary, final in (
            (paths.ass_temporary, paths.ass_final),
            (paths.srt_temporary, paths.srt_final),
            (paths.video_temporary, paths.video_final),
        ):
            if not temporary.is_file() or temporary.stat().st_size == 0:
                raise RenderStorageError("Caption rendering did not produce every expected asset")
            os.replace(temporary, final)

    def prepare_preview(self, candidate_id: int, render_id: int) -> tuple[Path, str]:
        directory = self._inside(
            self.render_root, self.render_root / f"candidate_{candidate_id}" / "decorated"
        )
        directory.mkdir(parents=True, exist_ok=True)
        path = self._inside(directory, directory / f"final_render_{render_id}_preview.png")
        if path.exists():
            raise RenderStorageError("Preview output already exists")
        return path, path.relative_to(self.render_root).as_posix()

    def resolve_render(self, relative: str) -> Path:
        if Path(relative).is_absolute():
            raise RenderStorageError("Stored render path must be relative")
        path = self._inside(self.render_root, self.render_root / relative)
        if not path.is_file() or path.stat().st_size == 0:
            raise RenderStorageError("Render asset is missing")
        return path

    @staticmethod
    def cleanup(paths: CaptionPaths) -> None:
        for path in (
            paths.ass_temporary,
            paths.ass_final,
            paths.srt_temporary,
            paths.srt_final,
            paths.video_temporary,
            paths.video_final,
        ):
            with suppress(OSError):
                path.unlink(missing_ok=True)
