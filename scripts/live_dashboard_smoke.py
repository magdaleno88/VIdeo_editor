from __future__ import annotations

import json
import socket
import threading
import time
from uuid import uuid4

import httpx
import uvicorn
from alembic import command
from alembic.config import Config

from app.core.config import Settings
from app.core.database import build_engine, session_factory, session_scope
from app.main import create_app
from app.providers.pexels import PexelsProvider
from app.repositories.captions import CaptionRepository
from app.repositories.renders import RenderRepository
from app.schemas.domain import CaptionPlanRequest
from tests.conftest import ROOT
from tests.test_captions import approved_raw, caption_service, final_service


def available_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def main() -> int:
    run_root = ROOT / "data" / "dashboard-smoke" / f"run-{uuid4().hex[:10]}"
    run_root.mkdir(parents=True, exist_ok=False)
    database = run_root / "dashboard.db"
    database_url = f"sqlite:///{database.as_posix()}"
    config = Config(str(ROOT / "alembic.ini"))
    engine = build_engine(database_url)
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")

    payload = json.loads((ROOT / "tests" / "fixtures" / "pexels.json").read_text())
    with httpx.Client() as provider_client:
        video = PexelsProvider(provider_client, "fixture-key").normalize(payload["videos"][0])
    with session_scope(session_factory(engine)) as session:
        candidate, _, narration, raw = approved_raw(session, video, run_root)
        plan = caption_service(session).create(raw.id, CaptionPlanRequest())
        final = final_service(session, run_root).render(plan.id).render
        raw_model = RenderRepository(session).get_render(raw.id)
        final_model = CaptionRepository(session).get_render(final.id)
        raw_model.output_path = "smoke/caption_smoke.mp4"
        final_model.output_path = "smoke/caption_smoke.mp4"
        final_model.preview_path = "smoke/caption_smoke_preview.png"
        ids = {
            "candidate": candidate.id,
            "narration": narration.id,
            "raw": raw.id,
            "final": final.id,
        }

    settings = Settings(
        _env_file=None,
        database_url=database_url,
        narration_storage_root=str(run_root / "audio"),
        render_storage_root=str(ROOT / "data" / "renders"),
        caption_storage_root=str(run_root / "captions"),
        dashboard_default_reviewer="Dashboard smoke",
    )
    application = create_app(settings, engine)
    port = available_port()
    server = uvicorn.Server(
        uvicorn.Config(application, host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("Local dashboard server did not start")
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10) as client:
            checks = [
                client.get("/"),
                client.get(f"/dashboard/candidates/{ids['candidate']}"),
                client.get("/dashboard/review-queue"),
                client.get(f"/dashboard/final-renders/{ids['final']}"),
                client.get(f"/media/audio/{ids['narration']}"),
                client.get(f"/media/render/{ids['raw']}", headers={"Range": "bytes=0-99"}),
                client.get(f"/media/final-render/{ids['final']}", headers={"Range": "bytes=0-99"}),
                client.get(f"/media/preview/{ids['final']}"),
            ]
            if any(response.status_code not in (200, 206) for response in checks):
                raise RuntimeError(
                    "Dashboard HTTP smoke failed: "
                    + ", ".join(str(response.status_code) for response in checks)
                )
            response = client.post(
                f"/dashboard/review/final-render/{ids['final']}/approve",
                data={
                    "csrf_token": application.state.dashboard_csrf_token,
                    "return_to": f"/dashboard/final-renders/{ids['final']}",
                    "reviewer": "Dashboard smoke",
                    "notes": "HTTP form verified",
                },
                follow_redirects=False,
            )
            if response.status_code != 303:
                raise RuntimeError(f"Dashboard review form returned {response.status_code}")
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        engine.dispose()
    print(
        "LOCAL DASHBOARD VERIFIED: home, candidate, queue, final review, audio, raw MP4, "
        "decorated MP4, preview and CSRF-protected approval exercised over HTTP"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
