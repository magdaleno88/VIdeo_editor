# Industrial Content Factory

A local, semi-automated system for discovering industrial-process videos and building human-reviewed educational short-form content. Phases 1–8A implement **discovery, rights review, scoring, AI visual analysis, claim-centric research, traceable scripts, approved narration, deterministic vertical-video assembly, captions, graphics and a local operational dashboard**. It does not publish or remove watermarks from videos.

## Features

- Generate bounded English queries from an object/process idea using deterministic templates and an extensible Spanish/English vocabulary.
- Search Pexels and Pixabay through official APIs and normalize source, creator, previews, dimensions and catalog license information.
- Store candidates in SQLite with Alembic migrations and database-enforced `(provider, provider_video_id)` uniqueness.
- Cache normalized searches for 24 hours and report partial failures without exposing credentials.
- Assign a clearly labeled text-metadata heuristic score or accept eight manual 0–100 ratings with configurable weights.
- Explicitly analyze a known candidate with Gemini video understanding, validate timestamped evidence, and persist a versioned AI evaluation.
- Reuse successful evaluations for the same candidate/provider/model/prompt/scorer identity, with an explicit force option.
- Compare the latest AI and manual ratings by total difference, per-dimension difference and mean absolute error.
- Research a visually analyzed candidate through bounded Brave searches, safe document retrieval and two validated Gemini stages.
- Preserve versioned dossiers, source quality, exact evidence excerpts, claim/source relations, contradictions and separate source/video confidence.
- Generate Spanish, beat-based Facebook Reels scripts only from reviewed claims and Phase 2 visual evidence, with sentence-level provenance and human approval.
- Generate a single checked narration asset from an approved script through the configured ElevenLabs voice, with stored checksum, timing, cache identity and separate human review.
- Build an inspectable edit plan from approved visual references and narration timing, then render and validate a versioned vertical H.264/AAC MP4 with FFmpeg.
- Build captions from approved script sentences and narration alignment, generate UTF-8 ASS/SRT, add safe-area hook/verified-fact graphics, render a separate decorated MP4 and review it independently.
- Operate the complete pipeline from a local server-rendered dashboard with review queues, safe audio/video playback, timestamp seeking, explicit cost-bearing actions and CSRF-protected decisions.
- Review rights with evidence, approve/reject candidates, and keep an audit history. Unverified rights block approval.
- Use the same services through FastAPI, interactive OpenAPI docs and a JSON-output CLI.

## Architecture

```mermaid
flowchart LR
    API[FastAPI / CLI] --> Discovery[DiscoveryService]
    Discovery --> Queries[QueryGenerator]
    Discovery --> Providers[Pexels / Pixabay adapters]
    Discovery --> Cache[24-hour search cache]
    Discovery --> Scoring[VideoScorer]
    Discovery --> Repository[CandidateRepository]
    API --> Review[ReviewService]
    API --> Research[TechnicalResearchService]
    Research --> Search[Brave search + safe documents]
    Research --> Claims[Gemini extraction + verification]
    Research --> Repository
    API --> Scripting[Verified ScriptGenerationService]
    Scripting --> Allowlist[Claims + visual timeline]
    Scripting --> ScriptAI[Gemini generation + audit]
    Scripting --> Repository
    API --> Narration[Approved NarrationService]
    Narration --> TTS[ElevenLabs TTS adapter]
    Narration --> Audio[Atomic local audio storage]
    Narration --> Repository
    API --> Planning[Deterministic EditPlanService]
    Planning --> Evidence[Beats + visual refs + alignment]
    API --> Rendering[VideoRenderer]
    Rendering --> FFmpeg[FFmpeg + ffprobe]
    Rendering --> Output[Atomic MP4 storage]
    Rendering --> Repository
    API --> Captions[CaptionPlanService]
    Captions --> Subtitles[ASS + SRT]
    Captions --> Final[Decorated MP4 + preview]
    Final --> Repository
    Dashboard[Local Jinja2 dashboard] --> API
    Dashboard --> Media[ID-based safe media routes]
    Review --> Rights[Rights policy]
    Review --> Repository
    Repository --> DB[(SQLite / PostgreSQL-ready schema)]
```

See [full pipeline architecture](docs/architecture.md), [provider integration notes](docs/providers.md), [scoring semantics](docs/scoring.md), [technical research](docs/research.md), [verified scripting](docs/scripting.md), [narration](docs/tts.md), [rendering](docs/rendering.md), [captions and graphics](docs/captions.md), [dashboard operation](docs/dashboard.md), and [the ten-phase roadmap](docs/roadmap.md).

## Installation

Requires **Python 3.12+**. Run commands from the repository root. FFmpeg and ffprobe are required only when executing a render; planning, discovery and review remain available without them. Docker, Node.js and a queue are not required.

PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]" -c requirements.lock
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

If Python is available as `python`, use `python -m venv .venv`. **In this delivered workspace, `.venv` and the migrated development database already exist.** See [verification details](docs/verification.md) for the actual runtime used.

macOS/Linux:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]' -c requirements.lock
cp .env.example .env
.venv/bin/python -m alembic upgrade head
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Unix instructions describe the intended setup; this delivery was verified on Windows. `requirements.lock` pins the installed runtime and test dependencies. The optional PostgreSQL driver is not included in that lock.

Open the [local dashboard](http://127.0.0.1:8000/), [interactive API docs](http://127.0.0.1:8000/docs) or [health](http://127.0.0.1:8000/health). Keep Uvicorn bound to `127.0.0.1`; Phase 8A is a trusted single-user interface without remote authentication. Migrations create the database schema. Application startup never silently changes tables and reports missing/outdated migrations.

## Configuration

Edit `.env` locally. Never commit real credentials.

| Variable | Default / purpose |
| --- | --- |
| `DATABASE_URL` | `sqlite:///./data/industrial_content_factory.db` |
| `PEXELS_API_KEY` | Empty; required when selecting Pexels |
| `PIXABAY_API_KEY` | Empty; required when selecting Pixabay |
| `GEMINI_API_KEY` | Empty; shared by explicitly requested Gemini video, research and script actions |
| `GEMINI_VIDEO_MODEL` | Empty; required for AI scoring; no model is hardcoded |
| `HTTP_TIMEOUT_SECONDS` | `15`, per-request timeout |
| `AI_VIDEO_MAX_DURATION_SECONDS` | `90`; rejects longer candidates before download |
| `AI_VIDEO_MAX_FILE_SIZE_MB` | `40`; enforced from headers and streamed bytes |
| `AI_VIDEO_ANALYSIS_TIMEOUT_SECONDS` | `180`; upper bound for Gemini file processing |
| `BRAVE_SEARCH_API_KEY` | Empty; required only for explicit technical research |
| `RESEARCH_SEARCH_PROVIDER` | `brave`; provider identity stored with each dossier |
| `GEMINI_RESEARCH_MODEL` | Empty; required for research and deliberately not hardcoded |
| `RESEARCH_MAX_SEARCH_QUERIES` | `5`; hard bounded to 1–8 |
| `RESEARCH_MAX_SOURCES` | `8`; search candidates retained per bounded run |
| `RESEARCH_MAX_PAGES` | `5`; maximum source pages attempted |
| `RESEARCH_MIN_SOURCES` | `2`; usable documents required before AI extraction |
| `RESEARCH_MAX_DOCUMENT_BYTES` | `500000`; streamed limit per robots/page response |
| `RESEARCH_HTTP_TIMEOUT_SECONDS` | `15`; search, document and research-AI timeout |
| `RESEARCH_MAX_LLM_CALLS` | `2`; current pipeline uses extraction plus verification |
| `GEMINI_SCRIPT_MODEL` | Empty; required for explicit script generation |
| `SCRIPT_MAX_VARIANTS` | `3`; default request generates one draft |
| `SCRIPT_MAX_CLAIMS` | `20`; maximum claims exposed to one generation |
| `SCRIPT_MAX_CONTEXT_CHARACTERS` | `20000`; bounded model context |
| `SCRIPT_SPEAKING_RATE_WPM` | `150`; duration-estimation rate |
| `SCRIPT_DURATION_TOLERANCE` | `0.25`; deviation before `NEEDS_REVISION` |
| `SCRIPT_ALLOW_PARTIALLY_SUPPORTED` | `false`; opt-in cautious use policy |
| `SCRIPT_TIMEOUT_SECONDS` | `60`; per script-model request |
| `SCRIPT_MAX_RETRIES` | `1`; bounded provider retry count |
| `ELEVENLABS_API_KEY` | Empty; required only for explicit narration generation |
| `ELEVENLABS_MODEL_ID` | Empty; required for narration; model identity is persisted |
| `ELEVENLABS_DEFAULT_VOICE_ID` | Empty; used when a narration request has no `voice_id` |
| `ELEVENLABS_DEFAULT_VOICE_NAME` | `Default Spanish voice`; local label for the default voice profile |
| `NARRATION_STORAGE_ROOT` | `data/audio`; ignored local root for generated assets |
| `NARRATION_MAX_CHARACTERS` | `10000`; bounded complete spoken text sent in one request |
| `NARRATION_TIMEOUT_SECONDS` / `NARRATION_MAX_RETRIES` | `90` / `1`; bounded provider request handling |
| `NARRATION_DURATION_TOLERANCE` | `0.20`; relative variance accepted before manual review is required |
| `NARRATION_MAX_FILE_SIZE_MB` | `20`; generated audio byte limit |
| `FFMPEG_BINARY` / `FFPROBE_BINARY` | Empty; use system discovery, or set explicit binary paths |
| `RENDER_STORAGE_ROOT` | `data/renders`; ignored root for final MP4 assets |
| `RENDER_WIDTH` / `RENDER_HEIGHT` / `RENDER_FPS` | `1080` / `1920` / `30` |
| `RENDER_CRF` / `RENDER_AUDIO_BITRATE` | `20` / `192k`; H.264 quality and AAC bitrate |
| `RENDER_TIMEOUT_SECONDS` | `300`; hard process timeout |
| `RENDER_DURATION_TOLERANCE` | `0.15`; relative rendered-duration tolerance |
| `RENDER_MIN_PLAYBACK_SPEED` / `RENDER_MAX_PLAYBACK_SPEED` | `0.80` / `1.25`; conservative planning bounds |
| `RENDER_CLIP_PRE_ROLL_MS` / `RENDER_CLIP_POST_ROLL_MS` | `150` / `150`; bounded visual context |
| `RENDER_MAX_FILE_SIZE_MB` | `150`; final MP4 limit |
| `CAPTION_STORAGE_ROOT` | `data/captions`; ignored ASS/SRT storage |
| `CAPTION_FONT_PATH` | Empty; optional local TTF/OTF/TTC used only for decorated renders |
| `CAPTION_DEFAULT_STYLE` | `CLEAN`; reusable `CLEAN`, `BOLD` or `MINIMAL` profile |
| `CAPTION_MIN_WORDS` / `CAPTION_MAX_WORDS` | `2` / `7`; deterministic segmentation window |
| `CAPTION_MAX_CHARACTERS` / `CAPTION_MAX_LINES` | `42` / `2`; mobile wrapping bounds |
| `CAPTION_LINGER_MS` | `120`; bounded post-speech visibility before a pause |
| `CAPTION_MAX_CHARACTERS_PER_SECOND` | `20`; warning threshold for reading speed |
| `CAPTION_SAFE_MARGIN_*` | Relative Facebook Reels margins that scale with output resolution |
| `CAPTION_WORD_HIGHLIGHT_ENABLED` | `true`; still requires provider alignment |
| `CAPTION_MAX_EMPHASIS_PER_ITEM` | `2`; limits highlight density |
| `GRAPHICS_BRANDING_ENABLED` | `false`; optional branding is opt-in |
| `BRANDING_ASSET_PATH` / `BRANDING_CHANNEL_NAME` | Empty; optional checked local logo and text |
| `BRANDING_OPACITY` | `0.70`; normalized logo opacity |
| `DASHBOARD_DEFAULT_REVIEWER` | `Local operator`; initial reviewer name in dashboard forms |
| `LOG_LEVEL` | `INFO`; application events use JSON logging on stderr |
| `SCORING_WEIGHTS__<DIMENSION>` | Override a score weight; the total must remain 100 |

If `providers` is omitted, discovery searches every configured provider. No keys produces `503` with configuration instructions; queries, listing, review and health still work. Explicit selection of a provider without a key fails before any searches. HTTP request URLs, authentication headers and provider error bodies are excluded from application logs.

### PostgreSQL preparation

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[postgres]"
# Set DATABASE_URL in .env to postgresql+psycopg://user:password@localhost:5432/icf
.\.venv\Scripts\python.exe -m alembic upgrade head
```

The schema uses portable SQLAlchemy types, non-native enums, UTC timestamps and dialect-specific cache upserts. Live PostgreSQL integration is not verified. SQLite is intended for local, low-concurrency use. Docker is deferred until it provides a needed service or deployment environment.

## CLI

Replace candidate ID `15` with an ID returned by discovery:

```powershell
.\.venv\Scripts\python.exe -m app queries "tornillo"
.\.venv\Scripts\python.exe -m app discover "tornillo" --provider pexels --max-queries 3
.\.venv\Scripts\python.exe -m app discover "plastic part" --process "moldeo por inyeccion" --category plastico
.\.venv\Scripts\python.exe -m app candidates --top 20
.\.venv\Scripts\python.exe -m app candidates --status SCORED --provider pixabay --minimum-score 10 --limit 10 --offset 0
.\.venv\Scripts\python.exe -m app candidate show 15
.\.venv\Scripts\python.exe -m app candidate history 15
.\.venv\Scripts\python.exe -m app candidate ai-score 15
.\.venv\Scripts\python.exe -m app candidate ai-score 15 --force
.\.venv\Scripts\python.exe -m app candidate evaluations 15
.\.venv\Scripts\python.exe -m app candidate compare-scores 15
.\.venv\Scripts\python.exe -m app candidate research 15
.\.venv\Scripts\python.exe -m app candidate research 15 --force
.\.venv\Scripts\python.exe -m app candidate research-show 15
.\.venv\Scripts\python.exe -m app candidate claims 15
.\.venv\Scripts\python.exe -m app candidate sources 15
.\.venv\Scripts\python.exe -m app candidate research-verify 15 --decision VERIFIED --reviewer "Your name" --notes "Claims and sources reviewed"
.\.venv\Scripts\python.exe -m app candidate script 15 --duration 45 --style educational
.\.venv\Scripts\python.exe -m app candidate script 15 --duration 30 --style curiosity --variants 2 --force
.\.venv\Scripts\python.exe -m app candidate scripts 15
.\.venv\Scripts\python.exe -m app script show 7
.\.venv\Scripts\python.exe -m app script approve 7 --reviewer "Your name" --notes "Ready for narration"
.\.venv\Scripts\python.exe -m app script reject 7 --reviewer "Your name" --notes "Hook needs revision"
.\.venv\Scripts\python.exe -m app candidate narrate 7 --voice "ELEVENLABS_VOICE_ID"
.\.venv\Scripts\python.exe -m app candidate narrate 7 --voice "ELEVENLABS_VOICE_ID" --stability 0.45 --similarity-boost 0.7 --speed 1.0 --force
.\.venv\Scripts\python.exe -m app candidate narrations 7
.\.venv\Scripts\python.exe -m app narration show 12
.\.venv\Scripts\python.exe -m app narration approve 12 --reviewer "Your name" --notes "Audio checked"
.\.venv\Scripts\python.exe -m app narration reject 12 --reviewer "Your name" --notes "Timing needs revision"
.\.venv\Scripts\python.exe -m app candidate render-plan 15 --script 7 --narration 12 --composition fit
.\.venv\Scripts\python.exe -m app candidate render-plans 15
.\.venv\Scripts\python.exe -m app render-plan show 4
.\.venv\Scripts\python.exe -m app render-plan render 4
.\.venv\Scripts\python.exe -m app render-plan render 4 --force
.\.venv\Scripts\python.exe -m app render show 3
.\.venv\Scripts\python.exe -m app render approve 3 --reviewer "Your name" --notes "Framing checked"
.\.venv\Scripts\python.exe -m app render reject 3 --reviewer "Your name" --notes "Important machine cropped"
.\.venv\Scripts\python.exe -m app render caption-plan 3 --style clean --emphasis phrase
.\.venv\Scripts\python.exe -m app caption-plan show 4
.\.venv\Scripts\python.exe -m app caption-plan render 4
.\.venv\Scripts\python.exe -m app final-render show 5
.\.venv\Scripts\python.exe -m app final-render preview 5 --time 1.5
.\.venv\Scripts\python.exe -m app final-render approve 5 --reviewer "Your name" --notes "Captions checked"
.\.venv\Scripts\python.exe -m app final-render reject 5 --reviewer "Your name" --notes "Caption covers machinery"
.\.venv\Scripts\python.exe -m app candidate score 15 --file examples/manual-score.json
.\.venv\Scripts\python.exe -m app candidate rights 15 --file examples/rights-review.json
.\.venv\Scripts\python.exe -m app candidate approve 15 --reviewer "Your name" --notes "Source and educational use reviewed"
.\.venv\Scripts\python.exe -m app candidate reject 15 --reviewer "Your name" --notes "Process is not sufficiently visible"
```

`icf` is also installed as an entry point. JSON goes to stdout and errors/logs to stderr. Application failures exit with code 1; syntax errors exit with code 2. **Replace the sample ratings before using the score file.** The rights example starts as `UNKNOWN` intentionally.

The query generator translates a small industrial vocabulary, including screws, nails, bearings, glass bottles, chains, gears and manufacturing processes. Arbitrary Spanish phrases are not translated automatically. Unknown terms are preserved; use English or extend `TRANSLATIONS` / `PROCESS_HINTS`. Optional process/category queries are prioritized within the search budget. `QueryGenerator` is the interface for a future LLM implementation.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Database/schema readiness |
| `POST` | `/discovery/queries` | Generate queries without provider requests |
| `POST` | `/discovery/search` | Search, normalize, deduplicate, store and score |
| `GET` | `/candidates` | Filter and paginate newest-first |
| `GET` | `/candidates/top` | Filter and paginate by descending score |
| `GET` | `/candidates/{id}` | Candidate details, score evidence and rights |
| `PUT` | `/candidates/{id}/rights` | Replace the rights assessment |
| `POST` | `/candidates/{id}/score` | Record all eight manual ratings |
| `POST` | `/candidates/{id}/ai-score` | Explicitly analyze video; body is `{"force": false}` |
| `GET` | `/candidates/{id}/evaluations` | Ordered, versioned heuristic/manual/AI evaluations |
| `GET` | `/candidates/{id}/score-comparison` | Compare the latest AI and manual evaluations |
| `POST` | `/candidates/{id}/research` | Explicit bounded research; body is `{"force": false}` |
| `GET` | `/candidates/{id}/research` | Versioned dossier history |
| `GET` | `/candidates/{id}/research/claims` | Latest dossier claims and evidence links |
| `GET` | `/candidates/{id}/research/sources` | Latest dossier sources and quality tiers |
| `POST` | `/candidates/{id}/research/verify` | Human `VERIFIED` or `REJECTED` decision |
| `POST` | `/candidates/{id}/scripts` | Explicit verified script generation |
| `GET` | `/candidates/{id}/scripts` | Versioned script history |
| `GET` | `/scripts/{script_id}` | Script, beats, sentences and provenance |
| `POST` | `/scripts/{script_id}/approve` | Human script approval |
| `POST` | `/scripts/{script_id}/reject` | Human script rejection |
| `POST` | `/scripts/{script_id}/narrations` | Generate one narration from an approved script |
| `GET` | `/scripts/{script_id}/narrations` | Versioned narration history for a script |
| `GET` | `/narrations/{narration_id}` | Audio metadata, checksum, alignment and review history |
| `POST` | `/narrations/{narration_id}/approve` | Human narration approval after validation |
| `POST` | `/narrations/{narration_id}/reject` | Human narration rejection |
| `POST` | `/candidates/{id}/render-plans` | Create or reuse an inspectable edit plan |
| `GET` | `/candidates/{id}/render-plans` | List versioned plans |
| `GET` | `/render-plans/{plan_id}` | Inspect source/output timing and rationale |
| `POST` | `/render-plans/{plan_id}/render` | Explicit FFmpeg render; body supports `force` |
| `GET` | `/renders/{render_id}` | Read render validation, checksum and status |
| `POST` | `/renders/{render_id}/approve` | Human render approval |
| `POST` | `/renders/{render_id}/reject` | Human render rejection |
| `POST` | `/renders/{render_id}/caption-plans` | Create/reuse a caption and graphics plan |
| `GET` | `/renders/{render_id}/caption-plans` | List caption-plan history |
| `GET` | `/caption-plans/{plan_id}` | Inspect caption timing, styles and overlays |
| `POST` | `/caption-plans/{plan_id}/render` | Render ASS captions/graphics into a separate MP4 |
| `GET` | `/final-renders/{render_id}` | Read decorated render metadata and review history |
| `POST` | `/final-renders/{render_id}/preview` | Generate a preview frame |
| `POST` | `/final-renders/{render_id}/approve` | Human final-render approval |
| `POST` | `/final-renders/{render_id}/reject` | Human final-render rejection |
| `POST` | `/candidates/{id}/approve` | Human approval gated by rights |
| `POST` | `/candidates/{id}/reject` | Human rejection |
| `GET` | `/candidates/{id}/history` | Ordered audit events |

List filters: `provider`, `status`, `minimum_score`, `category`, `industrial_process`, `limit` (1–100), `offset` (0+). Text labels use exact matching. `top` includes all statuses unless filtered, excludes unscored candidates and breaks ties by descending ID. Use `status=SCORED` for a review queue or `status=APPROVED` for an approved shortlist.

Discovery body:

```json
{
  "object_name": "tornillo",
  "process_name": "cold heading",
  "category": "metal",
  "providers": ["pexels", "pixabay"],
  "max_queries": 3,
  "page": 1,
  "per_page": 10
}
```

`max_queries` is 1–8, `page` is 1–10, and `per_page` is 3–40. One page is fetched per query; no automatic crawling occurs. Default discovery makes at most six API calls, with an absolute maximum of sixteen. These synchronous requests can take several minutes if providers are slow.

Responses contain `queries`, unique `candidate_ids`, `created`, `duplicates`, `cache_hits`, `successful_searches`, `errors` and `warnings`. Repeated appearances count as duplicates even inside one request. A failure stops further requests to that provider while allowing the other provider to succeed. Partial success is `200` with errors; all attempted searches failing returns `502`. Empty search results are successful and cached. Repeated discovery preserves existing scores, decisions, original query and editorial labels.

## Rights and human review

Pexels/Pixabay catalog license information is distinct from verification for a specific clip and proposed use. Imports start as `MANUAL_REVIEW_REQUIRED`, without a verification timestamp. Unknown licenses remain `UNKNOWN`; a public URL never proves permission.

After checking the creator, source, intended use and restrictions, submit a complete `RightsReview` with:

- `rights_status: "VERIFIED"`, license name and license URL;
- explicit commercial-use and modification permissions;
- an attribution decision and attribution text when required;
- `evidence_url`, reviewer and explanatory notes.

The server records verification time. Evidence URLs are reviewer-supplied references, not automatically verified permission documents. `PUT /rights` replaces the assessment: omitted optional fields reset instead of merging. Source/creator provenance stays attached, and audit events preserve previous/current assessments.

Approval requires a score and complete verified rights. It does not start production. Changing rights or a score invalidates an existing approval. Rescoring a rejected candidate leaves it rejected; a later explicit approval is possible after blockers are resolved. `PROCESSING` and `READY` are reserved for later phases and cannot be entered or edited through the MVP review API.

This is a **local trusted-user service without authentication**. Reviewer names are self-reported. Keep it on loopback; add authentication/authorization before shared or remote use.

## Scoring

The eight configurable weights are: movement 20, visible transformation 20, satisfying result 15, unusual machinery 15, understandable without audio 10, visual hook 10, educational potential 5, loop potential 5. Total score is `sum(value * weight) / 100`.

The heuristic scorer only reads provider title/slug/tags and remains a conservative discovery prior. Manual scoring supplies all eight values after a person watches the clip. AI scoring analyzes temporary video bytes and returns strict ratings, confidence, explanations, timestamped observations, opening-seconds notes, and possible hook/loop segments. These are content-suitability signals, not a prediction of views.

Every evaluation records method, scorer version, weight snapshot, ratings and total. AI rows also retain provider, model, prompt version, confidence and the validated visual analysis. Old rows are never overwritten. See [the scoring specification](docs/scoring.md) and [AI analysis guide](docs/ai-analysis.md).

## Technical research

Research is an explicit action after AI visual analysis. Visual observations remain observations; possible process names remain inferences until external evidence supports a claim. A claim can be `VERIFIED`, `PARTIALLY_SUPPORTED`, `UNVERIFIED` or `CONTRADICTED`. Only `VERIFIED` claims become `VERIFIED_FACT`; no model output can obtain that status without a persisted source excerpt.

Sources are ranked conservatively from Tier A through Tier D. Tier D cannot verify a claim, and quantitative claims need Tier A/B support to remain verified. The service keeps separate confidence for what a source says and whether that fact applies to this video. Human verification marks the dossier reviewed; it does not approve the candidate for production. See [technical research](docs/research.md).

## Verified scripting

Script generation requires the latest research dossier to be human-verified. A local allowlist excludes unverified, contradicted and unresolved claims; partially supported claims are opt-in and require cautious language. Gemini receives only permitted claims, stable candidate metadata, explicit editorial context and a Phase 2 visual timeline. It returns a structured beat/sentence draft and performs a second structured factual audit. Local validation checks references, numbers, hook footage, beat timestamps, repetition, clickbait and estimated duration.

Clean drafts become `VALIDATED`; any warning produces `NEEDS_REVISION`. Only a human can change `VALIDATED` to `APPROVED`. Each sentence links relationally to research claim IDs and/or stable visual references. See [verified scripting](docs/scripting.md).

## Narration

Narration accepts only a human-approved script and joins its ordered sentence `spoken_text` values with line breaks. The complete narration is sent in one ElevenLabs request; it is never synthesized sentence by sentence. The provider uses ElevenLabs' timestamp-capable text-to-speech endpoint and an MP3 output setting, as documented in [their API reference](https://elevenlabs.io/docs/api-reference/text-to-speech/convert-with-timestamps?explorer=true). Credentials stay in `ELEVENLABS_API_KEY` and are not persisted or emitted in errors.

Each successful asset records the exact source-text hash, voice/profile/model/settings, audio format, parsed duration, duration difference, checksum, safe provider metadata and a relative storage path under `NARRATION_STORAGE_ROOT`. Provider character timing is converted into sentence and beat timing when it exactly corresponds to the stored text; otherwise the service stores a clearly labeled proportional estimate. A duration outside the configured tolerance becomes `NEEDS_REVIEW`; provider, validation and storage failures return an error and do not create a usable asset. Identical approved input, voice, settings and narration version reuse the previous asset unless `force` is supplied. A validated narration still requires explicit human approval. See [narration details](docs/tts.md).

## Automatic video assembly

Planning requires an approved candidate with currently verified rights, an approved matching script and an approved matching narration. The planner uses sentence visual references, hook/notable/loop evidence and narration alignment to produce a contiguous, explainable timeline before any source download or FFmpeg process. Playback speed is bounded; approved loop candidates may repeat, while an unavoidable shortage becomes a recorded final-frame hold warning.

Rendering reacquires the known provider preview through the Phase 2 safety policy, uses the exact approved narration, mutes source audio, converts without stretching to `CENTER_CROP`, `FIT` or optional `BLURRED_BACKGROUND`, and validates the result with structured ffprobe JSON. The default output is 1080×1920 H.264/AAC MP4. Files are finalized atomically under `RENDER_STORAGE_ROOT`, checksummed and never silently overwritten. See [rendering details](docs/rendering.md).

## Captions and final graphics

Caption planning requires a human-approved raw render. It uses approved `display_text`, preserves the matching `spoken_text` reference, and maps narration alignment directly onto the final output timeline. Punctuation-aware segmentation protects number/unit pairs and common technical terms, while reading-speed warnings, two-line wrapping and relative Facebook Reels safe areas support manual QA.

The plan produces escaped UTF-8 ASS and SRT assets. Hook text comes from the approved script; factual labels require a `VERIFIED` research claim from that script's dossier. Optional branding is disabled by default. FFmpeg creates a separate immutable decorated MP4, ffprobe validates it, and a human approves or rejects the `FinalRenderAsset`. See [captions and graphics](docs/captions.md).

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest -q --cov=app --cov-report=term-missing
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m alembic check
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe scripts/smoke.py
.\.venv\Scripts\python.exe scripts/live_render_smoke.py
.\.venv\Scripts\python.exe scripts/live_caption_smoke.py
```

Tests mock provider responses using `httpx.MockTransport` and block non-loopback socket connections. Coverage includes queries, normalization, provider failures, persistence, duplicate races, rollback, scoring, rights validation, state transitions, API/CLI behavior, cache expiration and migrations. The smoke script runs a real loopback server against a temporary migrated database, checks basic routes, and stops it. Neither tests nor smoke checks use API keys or contact the catalogs.

## Structure

```text
app/
  api/                 Routes and dependency wiring
  core/                Configuration, database, schema checks, errors and logs
  models/              Candidate, rights, audit event and cache tables
  schemas/             Pydantic contracts and enums
  repositories/        Persistence, deduplication, versioning and cache
  providers/           Catalog, AI, TTS and FFmpeg adapters
  services/
    discovery/         Query generation and search workflow
    scoring/           Heuristic, manual and AI scoring orchestration
    research/          Planning, safe retrieval, source policy and claim verification
    scripting/         Factual allowlist, visual planning, generation, validation and review
    narration/         Audio validation, atomic storage, timing and narration review
    rendering/         Edit planning, safe execution, validation, storage and review
    captions/          Caption planning, ASS/SRT, final rendering, preview and review
    video/             Restricted temporary candidate-asset acquisition
    rights/            Shared eligibility policy
    review.py          Human review and audit events
  cli.py               CLI using the same services
  main.py              Application factory
migrations/            Alembic schema revisions
tests/                 Offline tests and synthetic provider fixtures
examples/              Editable review JSON
scripts/               Local server smoke check
data/                  Local databases (ignored)
docs/                  Architecture, providers, scoring, roadmap and verification
```

## Limitations and next module

Authenticated catalog searches, Brave research searches and live Gemini calls require keys and were not verified in this delivery. The document fetcher currently accepts HTTPS HTML/XHTML/plain text; PDF extraction is deliberately unsupported. Script entailment combines a model audit with deterministic provenance rules rather than formal semantic proof. Source tiers use conservative domain rules and require human review. Preview URLs and web pages can expire. SQLite and self-reported reviewer identities are not a multi-user production solution.

The next logical module is **Phase 8: the human approval dashboard**, presenting the existing evidence and artifact review contracts without adding publishing. It is described in [the roadmap](docs/roadmap.md).
