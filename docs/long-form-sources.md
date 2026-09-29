# Long-form sources

`LongFormSource` is the primary production input. It represents a persistent, authorized video that may be ten minutes, an hour or longer. The Phase 2 90-second preview limit still protects stock-candidate AI scoring and does not limit long-form storage.

## Ingest and storage

Browser uploads stream to disk and enforce `SOURCE_UPLOAD_MAX_SIZE_MB` without holding the complete file in memory. Initial formats are MP4, MOV, MKV and WebM, but an extension is only a first filter: ffprobe must find a positive duration, a real video stream, dimensions and a frame rate. Display rotation is applied to stored width and height.

Files are written atomically below `data/sources/<source_id>/original.<ext>`. The database stores only a relative path, original filename, byte size, SHA-256, duration, normalized dimensions, FPS, codecs, audio presence, container and rotation. Internal filenames never use the submitted filename. Resolution checks prevent stored paths from escaping the configured root. Exact duplicate checksums reuse the existing source and discard the incoming bytes.

Filesystem import is disabled unless `LOCAL_SOURCE_IMPORT_ROOT` is configured. A requested path must resolve inside that root. Arbitrary URL download, DRM bypass, ripping, watermark removal and restricted-source extraction are outside this system.

## Rights and provenance

Every source starts with `UNKNOWN` rights. Production concept approval requires `VERIFIED`, commercial use, derivative works, a license name, evidence reference and an explicit attribution decision. One source review covers all derived concepts. Approval creates a regular pipeline candidate with an inherited `RightsRecord` that points back to the source evidence; the concept retains its source and candidate identifiers.

```text
FinalRender → RenderPlan → Candidate → ShortFormConcept
→ LongFormSource → SourceRights → evidence_reference
```

## Local preprocessing

`FFmpegSceneDetector` uses the FFmpeg scene score, merges cuts below the configured minimum and merge thresholds, caps the number of scenes, and extracts one 640-pixel representative JPEG from the midpoint of each retained scene. The original remains unchanged. Scene and frame paths are persistent; external calls are not made.

Timestamped `SourceTranscript` and `SourceTranscriptSegment` records are provider-neutral. The current request contract supports a configured provider or mocked segments. A no-audio source records `SKIPPED_NO_AUDIO` and continues through visual analysis.

## Hierarchical analysis and cost controls

The local analyzer first consumes scene boundaries, representative frames and any transcript context. It persists a coarse source summary, stage windows and ranked moments using the existing movement, transformation, machinery, educational, hook and loop dimensions where applicable. `ECONOMY`, `BALANCED` and `QUALITY` profiles expose an operational budget before an external action: selected frames, deep moments, selected minutes and expected external-call count.

Gemini long-form analysis is never automatic. The explicit external action creates a blocked analysis record when the provider is unavailable and leaves the source valid. A future provider should send the compact coarse representation first, rank windows second, and upload only selected proxies or reusable session assets for deeper analysis. HTTP status, provider stage, sanitized message, model and file metadata must retain the existing Gemini diagnostics, including HTTP 402 `RESOURCE_EXHAUSTED`.

## API and CLI

REST endpoints cover upload/list/detail, rights, scene detection, transcript structure, analysis, stages, moments and concept generation. `POST /sources/upload` accepts a streamed body with `x-filename` and optional `x-title` headers. The dashboard implements this wire format with `fetch`.

CLI commands include `source import`, `source list`, `source show`, `source rights-review`, `source detect-scenes`, `source transcribe`, `source analyze`, `source moments` and `source concepts`. Local import still requires the configured confined root.
