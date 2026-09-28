"""Run a real loopback server against a temporary, migrated database."""

import json
import socket
import tempfile
import threading
import time
from pathlib import Path

import httpx
import uvicorn
from alembic import command
from alembic.config import Config

from app.core.config import Settings
from app.core.database import build_engine
from app.main import create_app

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    data_dir = (ROOT / "data").resolve()
    data_dir.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="smoke-", dir=data_dir) as directory:
        workspace = Path(directory).resolve()
        if not workspace.is_relative_to(data_dir):
            raise RuntimeError("Smoke workspace must remain within the project data directory")
        settings = Settings(
            _env_file=None,
            database_url=f"sqlite:///{(workspace / 'smoke.db').as_posix()}",
            pexels_api_key="",
            pixabay_api_key="",
        )
        engine = build_engine(settings.database_url)
        try:
            config = Config(str(ROOT / "alembic.ini"))
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            server = uvicorn.Server(
                uvicorn.Config(
                    create_app(settings, engine), host="127.0.0.1", port=port, log_level="warning"
                )
            )
            worker = threading.Thread(target=server.run, daemon=True)
            worker.start()
            try:
                with httpx.Client(
                    base_url=f"http://127.0.0.1:{port}", timeout=2, trust_env=False
                ) as client:
                    deadline = time.monotonic() + 15
                    while not server.started:
                        if not worker.is_alive() or time.monotonic() >= deadline:
                            raise RuntimeError("Server did not start within 15 seconds")
                        time.sleep(0.05)
                    results = {}
                    for path in (
                        "/health",
                        "/candidates",
                        "/candidates/top",
                        "/openapi.json",
                        "/docs",
                    ):
                        response = client.get(path)
                        response.raise_for_status()
                        results[f"GET {path}"] = response.status_code
                    queries = client.post("/discovery/queries", json={"object_name": "tornillo"})
                    queries.raise_for_status()
                    assert "screw factory" in queries.json()
                    results["POST /discovery/queries"] = queries.status_code
                    missing = client.post("/discovery/search", json={"object_name": "screw"})
                    assert missing.status_code == 503
                    results["POST /discovery/search (missing keys)"] = missing.status_code
                    print(json.dumps(results, indent=2))
            finally:
                server.should_exit = True
                worker.join(timeout=10)
                if worker.is_alive():
                    raise RuntimeError("Server did not shut down cleanly")
        finally:
            engine.dispose()


if __name__ == "__main__":
    main()
