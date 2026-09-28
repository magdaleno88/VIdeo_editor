# AI video analysis

Phase 2 adds explicit visual scoring for an already discovered candidate. It does not run during discovery, approve candidates, verify rights, edit media or predict view counts.

## Configure Gemini

Set these values in the local `.env` file:

```dotenv
GEMINI_API_KEY=your-local-secret
GEMINI_VIDEO_MODEL=your-video-capable-model
AI_VIDEO_MAX_DURATION_SECONDS=90
AI_VIDEO_MAX_FILE_SIZE_MB=40
AI_VIDEO_ANALYSIS_TIMEOUT_SECONDS=180
```

The model name has no default because available models and account access change over time. Choose a current model that supports video input and structured JSON output. Missing key or model configuration returns a clear `503` only when AI scoring is requested. Secrets, source URLs and model response bodies are excluded from application errors and audit events.

The implementation uses the official `google-genai` Python SDK. It uploads the temporary candidate asset, waits for Gemini file processing, requests a Pydantic response schema, validates the result again locally, and deletes the remote file in cleanup.

## Run an analysis

API:

```http
POST /candidates/15/ai-score
Content-Type: application/json

{"force": false}
```

CLI:

```powershell
.\.venv\Scripts\python.exe -m app candidate ai-score 15
.\.venv\Scripts\python.exe -m app candidate evaluations 15
.\.venv\Scripts\python.exe -m app candidate compare-scores 15
```

A successful matching evaluation is reused without a download or Gemini call. Use `--force` in the CLI or `{"force": true}` in the API only when a deliberate fresh analysis is needed. Batch scoring is not implemented, which prevents an accidental unbounded paid run.

## Result semantics

Every one of the eight dimensions has a rating, confidence, explanation and evidence list. Confidence expresses how strongly the visible content supports that assessment; it is not a probability of virality or a measure of model accuracy. A low-confidence dimension may have no timestamped evidence. Warnings preserve visible uncertainty about processes, objects, machinery or materials.

`first_seconds` records a separate assessment of the available opening one to three seconds. Hook and loop candidates contain timestamps and reasons, with at most five of each. They are inputs for a later editing phase and do not cause edits in Phase 2.

The application computes the weighted total locally using the same configurable weights as heuristic and manual scoring. The persisted history includes the exact weight snapshot, model, provider, prompt/scorer versions and structured evidence needed to compare later evaluations.

## Asset and cost controls

`VideoAssetFetcher` accepts only the preview URL stored for a known Pexels or Pixabay candidate. It requires HTTPS and an exact provider CDN host, rejects credentials, nonstandard ports and redirects, and validates duration before making a request. It enforces size both from `Content-Length` and while streaming, accepts only configured video MIME types, checks MP4/QuickTime/WebM signatures, uses an OS-generated temporary filename and removes it after every outcome.

One uncached request represents one external Gemini analysis. Logs announce the provider call without logging keys or media URLs. The operation is synchronous and bounded by HTTP, file-processing, duration and size settings.

## Expected failures

Failures remain distinct from a successful low score:

- missing Gemini key or model: configuration error (`503`);
- candidate missing: not found (`404`);
- missing or expired preview: asset unavailable (`502`);
- excessive duration or bytes: payload too large (`413`);
- unsupported MIME or signature mismatch: unsupported media (`415`);
- Gemini/provider failure or rejected file: analysis error (`502`);
- Gemini processing timeout: gateway timeout (`504`);
- malformed structured response: validation/analysis error (`502`).

None of these cases persists a valid AI evaluation. The local temporary file and any created Gemini file are cleaned up, and the action can be retried.

## Verification boundary

Offline tests mock the video transport and Gemini client, including structured success, cache reuse, forced analysis, cleanup and every error family above. Live Gemini behavior, model availability, quotas, latency and billing require a configured account and are not claimed by the offline verification.
