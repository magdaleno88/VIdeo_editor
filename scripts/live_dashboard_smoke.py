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
from app.repositories.candidates import CandidateRepository
from app.repositories.captions import CaptionRepository
from app.repositories.renders import RenderRepository
from app.schemas.domain import CaptionPlanRequest, Idea, PilotBatchCreate
from app.services.pilots import PilotService
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
        candidate_ids = [candidate.id]
        candidates = CandidateRepository(session)
        for index in range(4):
            extra, _ = candidates.add_if_new(
                video.model_copy(update={"provider_video_id": f"dashboard-smoke-{index}"}),
                "local synthetic pilot",
                Idea(object_name="industrial pilot"),
            )
            candidate_ids.append(extra.id)
        batch = PilotService(session).create_batch(
            PilotBatchCreate(
                name="Local five-video pilot",
                slug="local-five-video-pilot",
                description="Synthetic candidates; no claim of real-source review.",
                candidate_ids=candidate_ids,
            )
        )
        ids = {
            "candidate": candidate.id,
            "narration": narration.id,
            "raw": raw.id,
            "final": final.id,
            "batch": batch.id,
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
                client.get(f"/dashboard/pilots/{ids['batch']}"),
                client.get(f"/dashboard/pilots/{ids['batch']}/candidates/{ids['candidate']}"),
                client.get(f"/media/audio/{ids['narration']}"),
                client.get(f"/media/render/{ids['raw']}", headers={"Range": "bytes=0-99"}),
                client.get(f"/media/final-render/{ids['final']}", headers={"Range": "bytes=0-99"}),
                client.get(f"/media/preview/{ids['final']}"),
                client.get(f"/downloads/final-render/{ids['final']}.mp4"),
                client.get(f"/downloads/final-render/{ids['final']}.srt"),
            ]
            if any(response.status_code not in (200, 206) for response in checks):
                raise RuntimeError(
                    "Dashboard HTTP smoke failed: "
                    + ", ".join(str(response.status_code) for response in checks)
                )
            response = client.post(
                f"/dashboard/final-renders/{ids['final']}/quality-review",
                data={
                    "csrf_token": application.state.dashboard_csrf_token,
                    "return_to": f"/dashboard/final-renders/{ids['final']}",
                    "reviewer": "Dashboard smoke",
                    "decision": "APPROVED",
                    "notes": "Synthetic HTTP quality workflow verified",
                    **{
                        key: "5"
                        for key in (
                            "visual_relevance",
                            "pacing",
                            "crop_quality",
                            "narration_quality",
                            "caption_readability",
                            "hook_strength",
                            "audio_sync",
                            "overall_readiness",
                        )
                    },
                    **{
                        key: "true"
                        for key in (
                            "first_two_seconds_interesting",
                            "visuals_match_narration",
                            "important_parts_visible",
                            "narration_pacing_natural",
                            "captions_readable",
                            "captions_preserve_visuals",
                            "hook_makes_sense",
                            "facts_consistent",
                            "cuts_and_loops_natural",
                            "audio_synchronized",
                            "ready_to_publish",
                        )
                    },
                },
                follow_redirects=False,
            )
            if response.status_code != 303:
                raise RuntimeError(f"Dashboard quality form returned {response.status_code}")
            progress = client.get(f"/pilot-batches/{ids['batch']}/candidates/{ids['candidate']}")
            if progress.status_code != 200 or progress.json()["state"] != "READY":
                raise RuntimeError("Pilot did not reach READY after quality approval")
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        engine.dispose()
    print(
        "LOCAL DASHBOARD VERIFIED: home, candidate, queue, final review, audio, raw MP4, "
        "decorated MP4, preview and CSRF-protected approval exercised over HTTP"
    )
    print(
        "LOCAL PILOT WORKFLOW VERIFIED: five-candidate synthetic batch, full progress, "
        "structured quality approval and known-ID MP4/SRT downloads exercised over HTTP"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
