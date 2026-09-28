# Phase 6: deterministic vertical-video assembly

Phase 6 converts an approved candidate, approved script and approved narration into a versioned vertical MP4 draft. Planning and rendering are separate operations: a reviewer can inspect every source interval, output interval, beat mapping, sentence mapping, visual reference, speed, loop and warning before FFmpeg runs.

It does not publish videos, add music, create branded graphics or implement the Phase 7 subtitle engine.

## Prerequisites and rights gate

A plan requires all of the following at creation time:

- candidate status `APPROVED`;
- currently complete `VERIFIED` rights with commercial use and modification permission;
- an `APPROVED` script belonging to that candidate;
- an `APPROVED` narration belonging to that script;
- the script's persisted Phase 2 visual evaluation and narration alignment.

Rendering checks the approvals and rights again. A later rights change therefore blocks an old plan instead of relying on stale approval.

## Edit planning

`VideoEditPlan` stores the candidate, script, narration, Facebook Reels target, 9:16 output geometry, FPS, narration-led duration, composition and audio policy, render settings, warnings, planner version and cache identity. Ordered `EditSegment` rows store source/output intervals, speed, composition, beat ID, sentence IDs, visual refs, rationale, transition, loop count and any final-frame hold.

For each script beat, the deterministic planner:

1. derives required output duration from the approved sentence alignment;
2. reads the script's exact visual references and their Phase 2 timestamp snapshots;
3. prioritizes hook evidence for the first beat, then notable evidence, other evidence and loop candidates;
4. adds bounded pre/post roll without exceeding the analyzed source duration;
5. fits the interval within the configured playback-speed range;
6. repeats only a declared Phase 2 loop candidate when a short cyclic interval must fill a longer narration span;
7. uses a recorded final-frame hold and warning when ordinary footage cannot fill the span safely;
8. produces a continuous output timeline ending at the approved narration duration.

Missing visual references fall back to the beat's reviewed recommended interval and produce a warning. The planner does not invent an ROI or claim smart subject tracking. Default center crop therefore records a framing warning for human review. `FIT` is the safest option for wide industrial machinery; `BLURRED_BACKGROUND` preserves the full foreground over a blurred 9:16 background.

## FFmpeg and ffprobe

Set optional explicit paths or leave them empty for system discovery:

```dotenv
FFMPEG_BINARY=
FFPROBE_BINARY=
RENDER_STORAGE_ROOT=data/renders
RENDER_WIDTH=1080
RENDER_HEIGHT=1920
RENDER_FPS=30
RENDER_CRF=20
RENDER_AUDIO_BITRATE=192k
RENDER_TIMEOUT_SECONDS=300
RENDER_DURATION_TOLERANCE=0.15
RENDER_MIN_PLAYBACK_SPEED=0.80
RENDER_MAX_PLAYBACK_SPEED=1.25
RENDER_CLIP_PRE_ROLL_MS=150
RENDER_CLIP_POST_ROLL_MS=150
RENDER_MAX_FILE_SIZE_MB=150
```

FFmpeg is resolved only when rendering is requested, so missing binaries do not break health, planning, review or other API routes. `FFmpegCommandBuilder` accepts typed plans and local paths, generates fixed filter structures and passes an argument array to `subprocess.run` with `shell=False`. API/CLI requests cannot provide filter strings, codecs, output filenames or arbitrary FFmpeg arguments.

The source input contributes video only. Input 1 is the exact approved narration and is the only mapped audio. Output uses H.264, AAC, `yuv420p`, the configured FPS and `+faststart`. Source audio is `MUTED` in the Phase 6 contract. Scaling preserves aspect ratio; low-resolution sources may still be enlarged to fill the configured canvas, but Phase 6 applies no synthetic enhancement or repeated intermediate transcodes.

ffprobe supplies structured JSON for source and output duration, dimensions, FPS, codecs, streams, rotation and container. Validation requires a non-empty MP4 with video and audio, planned dimensions, approximate FPS, H.264/AAC, positive duration and configured size. Duration outside tolerance or any planning warning produces `NEEDS_REVIEW`; the completed draft may still be accepted explicitly by a human. Empty files, missing streams, bad dimensions/codecs, segment overflow, timeouts and process failures produce `FAILED`.

## Source acquisition, storage and reuse

Rendering reuses `VideoAssetFetcher`: only the stored preview URL of a known Pexels/Pixabay candidate is accepted; HTTPS host, port, redirect, MIME, signature, duration and byte limits remain enforced. The source is temporary and deleted after the operation. Phase 6 does not introduce permanent source-video caching because catalog rights and provider policies vary.

Final files use a generated `.part.mp4` path below `RENDER_STORAGE_ROOT`, are probed before finalization and are atomically moved to `candidate_<id>/render_<id>.mp4`. Only relative paths are persisted. Absolute paths and root escapes are rejected. Failed temporary/final files are cleaned up. Each successful `RenderAsset` stores size, SHA-256, dimensions, FPS, duration difference, codecs, renderer/FFmpeg versions, warnings and review history.

Plan reuse includes candidate/source identity, script, narration/checksum, planning settings and planner version. Render reuse additionally includes the plan identity, renderer version and approved narration checksum. `force=true` creates a new historical version; existing files are never overwritten.

## API and CLI

API:

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/candidates/{id}/render-plans` | Create/reuse a dry-run plan |
| `GET` | `/candidates/{id}/render-plans` | List plan history |
| `GET` | `/render-plans/{id}` | Inspect the complete plan |
| `POST` | `/render-plans/{id}/render` | Render/reuse an MP4 |
| `GET` | `/renders/{id}` | Inspect validation and review status |
| `POST` | `/renders/{id}/approve` | Approve a completed render |
| `POST` | `/renders/{id}/reject` | Reject a render |

Example plan body:

```json
{
  "script_id": 7,
  "narration_id": 12,
  "composition": "FIT",
  "force": false
}
```

CLI:

```powershell
python -m app candidate render-plan 15 --script 7 --narration 12 --composition fit
python -m app candidate render-plans 15
python -m app render-plan show 4
python -m app render-plan render 4
python -m app render show 3
python -m app render approve 3 --reviewer "Name" --notes "Framing checked"
python -m app render reject 3 --reviewer "Name" --notes "Important machine cropped"
```

`render-plan show` is the dry-run/debug interface and never downloads media or invokes FFmpeg.

## Offline worked example

```text
Approved candidate: industrial thread-forming clip, rights rechecked
  -> Phase 2 hook: hook:0, source 0.0–2.0 s
  -> Approved script beat 1: sentence refs hook:0
  -> Approved narration alignment: beat 1 at output 0.0–4.5 s
  -> Edit segment: source hook interval with bounded extension/speed
  -> Later beats: notable:0 and loop:0 mapped to their narration windows
  -> VideoEditPlan: continuous 9:16 timeline, source audio MUTED
  -> FFmpeg concept: trim/setpts + FIT/CROP/BLUR + concat + exact narration map
  -> ffprobe: MP4, 1080×1920, 30 fps, H.264/AAC, duration checked
  -> RenderAsset: relative path + SHA-256 + warnings + human review
```

## Local smoke test and troubleshooting

Run the optional synthetic test intentionally:

```powershell
.\.venv\Scripts\python.exe scripts/live_render_smoke.py
```

It uses generated test video and tone only; no provider or AI/TTS credentials are needed. It reports `LOCAL FFMPEG VERIFIED` with probed metadata, or `LOCAL FFMPEG NOT VERIFIED` when binaries are absent. Install an FFmpeg build that includes `libx264` and AAC support, put both executables on `PATH`, or configure explicit paths.

Common failures:

- missing binaries: install/configure FFmpeg and ffprobe; unrelated routes remain available;
- expired provider preview: rediscover or refresh the candidate rather than supplying an arbitrary URL;
- missing narration file: restore the approved asset beneath `NARRATION_STORAGE_ROOT`;
- crop warning: inspect the dry-run plan and use `FIT` or `BLURRED_BACKGROUND` when center crop removes machinery;
- timeout or process failure: inspect the bounded diagnostic tail in `failure_reason`, then retry explicitly;
- `FAILED` validation: check streams, dimensions, codecs, duration and available disk space.

The current planner does not perform subject tracking, optical scene analysis, black-frame/freeze detection, crossfades, subtitles, music or background job scheduling. Those boundaries are explicit rather than simulated.
