# Phase 7: captions, graphics and final renders

Phase 7 decorates a human-approved Phase 6 `RenderAsset` without changing or overwriting it. The complete boundary is:

```text
approved raw RenderAsset
  -> versioned CaptionPlan
  -> CaptionItem + GraphicOverlay timeline
  -> UTF-8 ASS and SRT assets
  -> FFmpeg decorated MP4
  -> ffprobe validation
  -> versioned FinalRenderAsset
  -> human approval or rejection
```

No speech-to-text or language model is used. Caption text comes from the approved script's `ScriptSentence.display_text`; `spoken_text` remains as its traceable narration reference. Timing comes from the matching approved `NarrationAlignment` and always uses the final output timeline, never the source-video timeline.

## Planning and timing

Planning requires an approved raw render with complete checksum and media metadata, plus its matching approved script and narration. Provider alignment is retained as `PROVIDER_ALIGNMENT`; migration 0007 also persists provider-derived word intervals from the exact character alignment. Proportional fallback remains explicitly `ESTIMATED_ALIGNMENT`. Word emphasis uses those intervals only when display and spoken words can be matched; otherwise it falls back to phrase emphasis with a warning rather than inventing timestamps.

The deterministic segmenter respects punctuation, a configurable 2–7-word window, character limits and common joined technical terms. Number/unit tokens such as `250 °C` remain together. Each persisted item records sentence and beat IDs, display/spoken text, start/end, timing method, position, style snapshot, emphasis spans, characters per second, words per minute and warnings. Excessive reading speed marks the plan for review; it never changes audio. A short configured linger is bounded by the next narration interval, so old text disappears during longer pauses.

## Styles and safe areas

Three reusable profiles are available: `CLEAN`, `BOLD` and `MINIMAL`. They control weight, outline, shadow, background treatment and size while preserving casing. Captions support `UPPER`, `CENTER` and `LOWER`; `LOWER` is the default.

Facebook Reels safe areas are stored as resolution-independent fractions. Defaults reserve 8% at the top and left, 18% at the bottom and 16% at the right. ASS rendering converts those values to pixels for the actual output resolution. Captions default to two lines.

## Graphics and provenance

`HOOK_TEXT` is derived verbatim from the approved script hook and is limited to the opening three seconds. `INFO_LABEL` accepts only explicitly requested `VERIFIED` `ResearchClaim` rows from the same dossier. The overlay persists claim IDs, preserving the path to source evidence. Partially supported, unverified and contradicted claims are rejected.

Optional `BRANDING` text and a configured logo asset are disabled by default. Logo files must be existing PNG/JPEG/WebP paths and are normalized to a small fraction of frame width with configured opacity and safe margins. Branding never removes, blurs or deliberately covers source watermarks.

## ASS, SRT and security

`ASSSubtitleRenderer` owns style declarations, wrapping, positioning and visual emphasis. `SRTSubtitleRenderer` produces a plain accessibility/debug asset from the same plan. Both are UTF-8 and cover Spanish punctuation, accents, `ñ` and unit symbols.

User-controlled text is escaped as data. ASS backslashes and override braces are neutralized; SRT newlines and cue arrows cannot create new cues. FFmpeg receives an argument list with `shell=False` through the existing bounded process executor. API and CLI users cannot submit filters, codec arguments or output paths.

An optional font is configured through `CAPTION_FONT_PATH`. It is checked only when a decorated render is requested, must be an existing TTF/OTF/TTC file, and cannot contain parent traversal. With no configured font, ASS uses the documented `Arial` family fallback available on the verified Windows host.

## Storage, cache and review

ASS/SRT files are finalized atomically beneath `CAPTION_STORAGE_ROOT`. Decorated MP4s and preview PNGs are stored under the existing ignored render root in `candidate_<id>/decorated`. Database paths stay relative. Output files are non-empty, checksummed, never overwritten and cleaned after failure.

Cache identity includes the raw-render checksum, caption plan, style, font identity, graphics, subtitle-renderer version and FFmpeg-renderer version. `force=true` creates a distinct asset version. ffprobe verifies MP4 video/audio streams, dimensions, FPS, duration and H.264/AAC codecs. A final render can then be approved or rejected with reviewer, notes and timestamp.

## API and CLI

API endpoints:

| Method | Path | Purpose |
| --- | --- | --- |
| `POST/GET` | `/renders/{id}/caption-plans` | Create/reuse or list plans |
| `GET` | `/caption-plans/{id}` | Inspect captions, graphics and warnings |
| `POST` | `/caption-plans/{id}/render` | Create/reuse a final render |
| `GET` | `/final-renders/{id}` | Read final metadata and review history |
| `POST` | `/final-renders/{id}/preview` | Generate a review frame at a bounded time |
| `POST` | `/final-renders/{id}/approve` | Approve a completed final render |
| `POST` | `/final-renders/{id}/reject` | Reject a final render |

CLI examples:

```powershell
python -m app render caption-plan 12 --style bold --emphasis phrase
python -m app caption-plan show 4
python -m app caption-plan render 4
python -m app final-render show 5
python -m app final-render preview 5 --time 1.5
python -m app final-render approve 5 --reviewer "Name" --notes "Safe areas checked"
python -m app final-render reject 5 --reviewer "Name" --notes "Caption covers machinery"
```

## Offline worked example

```text
Approved sentence
  display_text: "El acero llega a 250 °C"
  spoken_text: "El acero llega a doscientos cincuenta grados Celsius"
  narration alignment: 4.2–6.8 s, PROVIDER_ALIGNMENT
    -> caption segments remain on final timeline
    -> "250 °C" stays one unit
    -> CLEAN style + LOWER safe position
    -> ASS burned into a copy of the raw MP4
    -> SRT retained for accessibility/debugging
    -> ffprobe + checksum
    -> FinalRenderAsset awaiting human review
```

The current planner does not perform object detection, ROI-aware placement, transcription, animated diagrams, music selection or publishing. `ROI_AWARE` and `SUBJECT_AWARE` remain future positioning strategies.
