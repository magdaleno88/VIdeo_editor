"""Local dashboard smoke for the long-form source workflow."""

import subprocess
import tempfile
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.database import build_engine
from app.main import create_app
from app.providers.rendering.ffmpeg import resolve_binary

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    configured = Settings()
    ffmpeg = resolve_binary(configured.ffmpeg_binary, "ffmpeg")
    ffprobe = resolve_binary(configured.ffprobe_binary, "ffprobe")
    with tempfile.TemporaryDirectory(prefix="icf-source-dashboard-") as temporary:
        root = Path(temporary)
        source = root / "dashboard-source.mp4"
        result = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=640x360:rate=30:duration=3",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(source),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            shell=False,
        )
        if result.returncode:
            raise RuntimeError("Dashboard fixture generation failed")
        settings = Settings(
            _env_file=None,
            database_url=f"sqlite:///{(root / 'dashboard.db').as_posix()}",
            source_storage_root=str(root / "sources"),
            ffmpeg_binary=ffmpeg,
            ffprobe_binary=ffprobe,
        )
        engine = build_engine(settings.database_url)
        config = Config(str(ROOT / "alembic.ini"))
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        app = create_app(settings, engine)
        try:
            with TestClient(app) as client:
                listing = client.get("/dashboard/sources")
                upload = client.post(
                    "/sources/upload",
                    headers={"x-filename": source.name, "x-title": "Dashboard source"},
                    content=source.read_bytes(),
                )
                upload.raise_for_status()
                source_id = upload.json()["id"]
                detail = client.get(f"/dashboard/sources/{source_id}")
                media = client.get(f"/dashboard/sources/{source_id}/media")
                if (
                    listing.status_code != 200
                    or detail.status_code != 200
                    or "Source player" not in detail.text
                    or "Timeline" not in detail.text
                    or media.status_code != 200
                    or not media.content
                ):
                    raise RuntimeError("Dashboard source workflow failed")
                print(
                    "LOCAL LONG-FORM DASHBOARD VERIFIED: "
                    f"source={source_id}, list/detail/player/timeline available"
                )
        finally:
            engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
