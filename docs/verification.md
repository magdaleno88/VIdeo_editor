# Local verification report

Verified on 2026-09-28 in Windows PowerShell, from the project root.

## Environment

- Python **3.12.14** in the workspace `.venv`.
- Windows 10 Pro, version 25H2 (build 26200.9445), running PowerShell 7.6.5.
- The system Python launcher reported no registered Python installations. The available Codex runtime at `C:/Users/USER/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe` was used only to create the virtual environment.
- Runtime/development dependencies were installed into that virtual environment. Exact installed package versions are recorded in `requirements.lock`; project constraints are in `pyproject.toml`.
- SQLite development database migrated to revision `0007` at `data/industrial_content_factory.db`.

## Results

| Check | Command | Result |
| --- | --- | --- |
| Offline unit/integration suite | `.\.venv\Scripts\python.exe -m pytest -q --cov=app --cov-report=term-missing` | **181 passed**, 1 upstream deprecation warning; **92% statement coverage** for `app` (5,363 statements, 404 not covered) |
| Phase 3 focused suite | `.\.venv\Scripts\python.exe -m pytest -q tests/test_research.py tests/test_research_providers.py tests/test_research_api_cli.py` | **21 passed**, 1 upstream deprecation warning |
| Phase 4 focused suite | `.\.venv\Scripts\python.exe -m pytest -q tests/test_scripting.py tests/test_scripting_provider.py tests/test_scripting_api_cli.py` | **9 passed**, 1 upstream deprecation warning |
| Phase 5 focused suite | `.\.venv\Scripts\python.exe -m pytest -q tests/test_narration.py tests/test_elevenlabs_tts.py` | **7 passed**, 1 upstream deprecation warning |
| Phase 6 focused suite | `.\.venv\Scripts\python.exe -m pytest -q tests/test_rendering.py tests/test_rendering_live.py` | **8 passed**; mocked pipeline and actual local binary smoke passed |
| Phase 7 focused suite | `.\.venv\Scripts\python.exe -m pytest -q tests/test_captions.py tests/test_captions_live.py` | **12 passed**; offline caption/graphics pipeline and actual local ASS render passed |
| Lint | `.\.venv\Scripts\python.exe -m ruff check .` | Passed |
| Formatting | `.\.venv\Scripts\python.exe -m ruff format --check .` | Passed, 119 Python files |
| Migration/model consistency | `.\.venv\Scripts\python.exe -m alembic check` | No new upgrade operations detected |
| Dependency consistency | `.\.venv\Scripts\python.exe -m pip check` | No broken requirements |
| Real HTTP startup/shutdown | `.\.venv\Scripts\python.exe scripts/smoke.py` | Passed; temporary database and local server cleaned up |
| Local FFmpeg synthetic render | `.\.venv\Scripts\python.exe scripts/live_render_smoke.py` | **LOCAL FFMPEG VERIFIED:** actual FFmpeg render and ffprobe validation produced `360x640`, `3.00s`, `30.00fps`, `h264/aac`, container `mov,mp4,m4a,3gp,3g2,mj2` |
| Local caption render | `.\.venv\Scripts\python.exe scripts/live_caption_smoke.py` | **LOCAL CAPTION RENDER VERIFIED:** actual libass/FFmpeg render produced `360x640`, `3.00s`, `30.00fps`, H.264/AAC plus a PNG preview |
| PostgreSQL offline migration generation | `python -m alembic upgrade head --sql` with a PostgreSQL URL | **Not verified:** existing revision `0002` performs a result-dependent data backfill that Alembic offline mode cannot execute |

The suite includes complete discovery-to-approval workflows through both API and CLI with synthetic Pexels/Pixabay responses. Phase 2 coverage uses mocked video transport and Gemini clients to verify structured success, all eight ratings, weighted totals, evidence/version persistence, manual comparison, reuse, force, reweighting without another AI call, missing configuration, model and schema failures, expired/oversized/invalid assets, SSRF controls and cleanup after success or failure.

Phase 3 tests use mocked Brave search, document transport and Gemini output. They cover research questions and query types, source ranking, multiple-source claims, exact excerpts, claim/source graph persistence, unsupported and contradicted claims, quantitative-source policy, version reuse and force, human review, malformed structured output, provider failures and timeouts, safe document limits, API, CLI and candidate-not-found behavior. Migration upgrade/downgrade/re-upgrade, Phase 1 score backfill and schema drift checks use isolated SQLite databases. Non-loopback network calls are blocked during tests.

Phase 4 tests use a fake script provider and mocked Gemini SDK output. They cover the reviewed-dossier gate, factual allowlist, verified/partial/unverified/contradicted policy, quantitative validation, visual references, hook use, beats, word and duration calculation, sentence auditing, cache reuse, force, variants, structured-output failures, timeout, approval/rejection history, API, CLI and migration tables.

Phase 5 tests use generated WAV fixtures and a fake TTS provider, plus `httpx.MockTransport` for the ElevenLabs adapter. They cover the approved-script gate, one ordered complete source text, voice settings, cache reuse and force, checksum and relative atomic storage, parsed duration status, provider and estimated alignment, malformed audio cleanup, narration approval/rejection, API/CLI flows, timestamp request mapping, retries, authentication failure, timeout and malformed provider output.

Phase 6 tests create the complete approved Phase 1–5 prerequisite graph, then cover rights rechecks, deterministic hook/notable/loop selection, beat and sentence mappings, narration-led continuity, bounded speed, FIT/CENTER_CROP/BLURRED_BACKGROUND filters, source-audio muting, command construction, ffprobe parsing, timeout, invalid streams/dimensions, storage traversal, atomic output/checksum, failure cleanup, cache/force, review, API, CLI and migration tables. Source acquisition and FFmpeg execution are replaced by controlled test doubles; non-loopback networking remains blocked.

Phase 7 tests cover the approved-raw gate, script/narration linkage, final-timeline timing, provider and estimated alignment, `display_text`/`spoken_text` separation, punctuation segmentation, technical terms, number/unit grouping, word/character/line limits, reading speed, pause linger, three style profiles, relative safe areas, hook overlays, verified-claim overlays, rejected partial/unverified/contradicted facts, word-emphasis fallback, Unicode, ASS/SRT escaping, filter-injection resistance, lazy font and branding-path validation, atomic storage, checksums, reuse/force, preview, human review, API, CLI and migration tables.

## Local FFmpeg verification

- Distribution: the `win64 GPL` build from the official [BtbN FFmpeg-Builds GitHub release](https://github.com/BtbN/FFmpeg-Builds/releases), installed locally beneath `.tools/ffmpeg`; no global `PATH` or system application was changed.
- FFmpeg: `N-126947-g45f3fecca9-20260928`, at `.tools/ffmpeg/ffmpeg-master-latest-win64-gpl/bin/ffmpeg.exe`.
- ffprobe: `N-126947-g45f3fecca9-20260928`, at `.tools/ffmpeg/ffmpeg-master-latest-win64-gpl/bin/ffprobe.exe`.
- Archive SHA-256: `7BE4E989F0B038D3CC777D97469E2C1B0DB07B6EACB64B04D6DB7509C1FC3FE7`.
- The build exposes `libx264` and AAC encoders. The project resolves the pair through local `.env` values for `FFMPEG_BINARY` and `FFPROBE_BINARY`; `.env.example` remains generic and `.env` is ignored by Git.
- `scripts/live_render_smoke.py` generated a synthetic 360x640 MP4 with video and audio, then validated its container, positive duration, video/audio streams and codecs through ffprobe. Its output path is a temporary `icf-render-smoke-*\\output.mp4` directory and is cleaned by design once validation completes; the smoke uses 360x640 deliberately, while production plan validation enforces its configured dimensions (1080x1920 by default).

## Local caption-render verification

- `scripts/live_caption_smoke.py` generated a three-second 360x640 H.264/AAC source entirely with local FFmpeg, wrote a UTF-8 ASS document containing Spanish punctuation and `250 °C`, and burned it with the real `subtitles`/libass filter.
- ffprobe confirmed a valid MP4-family container, video and audio streams, 360x640 dimensions, 30 fps, positive 3.00-second duration and H.264/AAC codecs.
- The inspectable generated artifacts are `data/renders/smoke/caption_smoke.mp4` and `data/renders/smoke/caption_smoke_preview.png`. Both are runtime data ignored by Git.

The real HTTP smoke check returned:

| Endpoint | Expected and observed HTTP status |
| --- | ---: |
| `GET /health` | 200 |
| `GET /candidates` | 200 |
| `GET /candidates/top` | 200 |
| `GET /openapi.json` | 200 |
| `GET /docs` | 200 |
| `POST /discovery/queries` | 200 |
| `POST /discovery/search` without keys | 503, configuration error as intended |

The module CLI was also executed directly for query generation, an empty top-candidate listing, and the missing-credentials discovery error (exit code 1).

## Verification status

- **OFFLINE TESTS VERIFIED:** full suite, Phase 3–7 focused suites, lint, formatting, schema consistency, dependency consistency and local HTTP smoke test all pass.
- **LIVE SEARCH NOT VERIFIED:** no authenticated Brave request, quota or billing behavior was exercised. Request shape, result normalization and failure handling are verified offline.
- **LIVE LLM NOT VERIFIED:** no paid Gemini research request was made. Both structured stages and their validation are verified offline with a fake SDK client.
- **LIVE SCRIPT MODEL NOT VERIFIED:** no paid Gemini script generation or audit request was made. Both structured calls are verified offline with a fake SDK client.
- **LIVE TTS NOT VERIFIED:** no ElevenLabs key, paid synthesis, quota or billing behavior was exercised. Request mapping and audio/timing handling are verified offline with mocked transport and synthetic audio.
- **LOCAL FFMPEG VERIFIED:** the local BtbN build supplied real FFmpeg and ffprobe processes. The synthetic smoke generated and ffprobe-validated a non-empty MP4 with video and audio (`360x640`, 3.00 seconds, 30 fps, H.264/AAC, MP4-family container).
- **LOCAL CAPTION RENDER VERIFIED:** real FFmpeg/libass burned the generated ASS captions and hook into a separate MP4; ffprobe validation and preview generation succeeded.

## Known limits of this verification

- **No live authenticated Pexels/Pixabay searches:** credentials were not supplied. Adapter request shapes and normalization were checked against official documentation and offline fixtures.
- **No live authenticated Brave search:** credentials were not supplied. Search behavior is verified at the provider boundary with mocked HTTP transport.
- **No live PostgreSQL server:** revisions `0003` through `0006` use portable SQLAlchemy types, but the complete offline chain cannot be rendered because the already-applied `0002` migration reads backfill rows. A PostgreSQL upgrade still needs testing against a disposable server.
- **Live Gemini for Phases 2–4 was not verified:** no real API key, paid call, remote video upload, quota or billing behavior was exercised. Provider behavior is offline-verified with the official SDK installed.
- **Live ElevenLabs narration was not verified:** no real API key, paid call, voice-license assertion, quota or billing behavior was exercised. The service stores only safe provider metadata and never logs the API key.
- The FFmpeg verification uses generated local media only. It does not verify provider download, live narration, source-cache behavior, publishing or production deployment.
- The real caption smoke uses a compact 360x640 synthetic clip for speed. Production 1080x1920 rules, cache/versioning, factual overlays and branding are covered offline; optional branding with a real logo and custom font still needs project-specific visual review.
- Installed Starlette 1.7.0 emits a deprecation warning when its test client uses `httpx`: it recommends a future move to `httpx2` for tests. All tests pass with the locked version. The warning is visible, not suppressed, and does not affect the provider HTTP clients or local server verification.
- Coverage is statement coverage for application code, not a guarantee of production readiness. The service remains intended for trusted local use.
