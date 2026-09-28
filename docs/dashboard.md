# Local operational review dashboard

Phase 8A adds a server-rendered operational interface to the existing FastAPI application. It is a presentation layer over the same repositories and domain services used by the REST API and CLI. It introduces no database tables and does not contain publishing integrations.

## Run locally

From the repository root:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/`. Keep the server bound to loopback. The dashboard is designed for one trusted local operator and must not be exposed directly to a public or shared network.

`DASHBOARD_DEFAULT_REVIEWER` supplies the initial reviewer name in forms. Every accepted review still records a reviewer, notes and timestamp through the existing service. Uvicorn defaults to loopback, and the documented command states the bind address explicitly.

## Architecture

- FastAPI serves REST and HTML from one process.
- Jinja2 templates autoescape database and provider text.
- CSS and a small JavaScript file are served below `/dashboard/static`.
- JavaScript only seeks native video elements and confirms deliberate mutations. It does not evaluate domain state.
- `DashboardService` performs bounded page queries and fixed eager-loading queries for detail screens.
- State-changing forms invoke the current review, generation, planning and rendering services.
- Page loads never run discovery, external models, TTS or FFmpeg.

The main routes are:

| Route | Purpose |
| --- | --- |
| `/` | Operational counts, recent candidates and explicit discovery form |
| `/dashboard/candidates` | Paginated and filtered candidate list |
| `/dashboard/candidates/{id}` | Complete source-to-final pipeline detail |
| `/dashboard/review-queue` | Reviewable rights, research, scripts, narrations and renders |
| `/dashboard/final-renders/{id}` | Portrait visual review, caption timeline, versions and decision forms |
| `/dashboard/failures` | Recent persisted narration/render failures with safe summaries |
| `/dashboard/pilots` | Create and list local pilot batches |
| `/dashboard/pilots/{id}` | Batch state, quality averages and rejection reasons |
| `/dashboard/pilots/{id}/candidates/{candidate_id}` | Full checklist, blocker, next action, calls, versions and warnings |

Existing `/docs`, REST endpoints and CLI commands remain available.

## Candidate and pipeline review

The candidate detail page presents source provenance and rights; eight visual-score dimensions and timestamp evidence; the latest research dossier, claims, evidence and contradictions; script beats and sentence text; narration playback and timing differences; raw renders; caption plans; and final versions.

The pipeline display maps current domain enums instead of persisting dashboard-only states. Buttons appear only when their prerequisite records are present. Backend services recheck every prerequisite, so display gating is advisory rather than an authorization boundary.

External/cost-bearing actions are marked `External API` and require a form submission. Discovery, AI analysis, research, script generation and TTS never run while browsing. Local planning and FFmpeg actions are also explicit. The dashboard does not expose `force=true`; new charged or forced versions remain an advanced API/CLI operation.

## Review workflow

The review queue prioritizes final renders and can be filtered by stage. Review screens call the existing services for:

- candidate and rights decisions;
- research verification/rejection;
- script approval/rejection;
- narration approval/rejection;
- raw-render approval/rejection;
- final-render approval/rejection.

The final-render screen also records the Phase 8B structured review: eight 1–5 scores, eleven checklist items, rejection categories, notes and an automatically recommended revision stage. This record is separate from the basic transition audit while driving the same final-render status.

Approval notes may be omitted; the dashboard records a short local-dashboard note to satisfy the existing audit contract. Rejection always requires a reason. Service-level transition checks remain authoritative and safe errors return to the screen without exposing tracebacks or secrets.

## Media serving

The browser uses native `<audio>` and `<video>` controls. Media routes accept only integer database IDs:

- `/media/audio/{id}`
- `/media/render/{id}`
- `/media/final-render/{id}`
- `/media/preview/{id}`

Each route loads a known persistent record, resolves its stored relative path below the configured audio/render root, rejects absolute paths and traversal, verifies that the file exists and returns a fixed MIME type. There is no generic path-based download route. Starlette `FileResponse` supports HTTP byte ranges in the installed runtime, allowing video seeking.

Pilot exports use `/downloads/final-render/{id}.mp4` and `/downloads/final-render/{id}.srt`. Both resolve a known database record below its configured storage root and set a safe generated filename.

Caption rows, script beats and visual evidence expose timestamp buttons. The small dashboard script seeks an existing player to that final/output or source timestamp. It does not implement an editing timeline.

## CSRF and local security model

The application creates a cryptographically random synchronizer token on startup and embeds it in every dashboard mutation form. Mutations accept only bounded `application/x-www-form-urlencoded` bodies and reject missing or mismatched tokens with HTTP 403. The token is process-local because the dashboard has no login session and targets one local operator.

This protects browser forms in the stated local threat model, but it is not a replacement for authentication. Before any shared or remote deployment, add authenticated users, per-session CSRF tokens, secure cookies, authorization roles, TLS and production secret management. REST mutation endpoints retain their existing trusted-local behavior.

## Pagination and performance

Candidate and review pages enforce bounded page sizes. Candidate detail uses a fixed number of eager-loaded queries for nested collections rather than querying inside template loops. Candidate cards obtain stages and final preview IDs in batched queries. The page does not generate missing previews automatically; the final-render screen offers an explicit generation button.

## Troubleshooting

- A configuration error shown after an explicit action means its provider key/model is missing or invalid. Browsing still works.
- Missing audio/video returns 404 and usually indicates that ignored runtime data moved while its database record remained.
- FFmpeg actions use the configured Phase 6/7 binaries and report sanitized service errors.
- If a form reports an expired token after restarting the app, reload the page.
- If media seeking fails, confirm the file is valid MP4/H.264/AAC and the browser sent a range request.

## Verification

Run the focused tests and real local HTTP smoke:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_dashboard.py
.\.venv\Scripts\python.exe scripts/live_dashboard_smoke.py
```

The smoke starts an actual Uvicorn server on a temporary loopback port, uses a migrated temporary database and five-candidate synthetic pilot, serves the real Phase 7 caption smoke MP4/PNG, exercises the main pages and downloads, and submits a CSRF-protected structured quality approval. It makes no external API calls.
