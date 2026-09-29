# Short-form extraction

A `ShortFormConcept` is a proposed Reel story, not a final script. It stores an angle, hook, target duration, selected stages and moments, coverage, generation version, overlap warning and an ordered list of source windows. One long-form source can own many concepts and each approved concept receives an independent downstream candidate and independent asset versions.

## Clip plan

Each `ShortFormClip` keeps `source_start`, `source_end`, `output_order`, target output duration, speed recommendation, role and optional moment provenance. Roles are hook, context, process, transformation and result. Output order may differ from source chronology, allowing a result-first hook followed by the process.

The dashboard concept screen previews every interval and lets the operator change start, end and order. Validation enforces a contiguous unique order and `0 <= start < end <= source duration`. Requests contain timestamps only; they cannot choose a filesystem path.

Concept generation is capped by `MAX_CONCEPTS_PER_SOURCE` and the API limit of five per request. A simple clip-set overlap ratio flags proposals that reuse at least 80 percent of another concept's windows. This is deliberately transparent and avoids an embedding dependency.

## Existing pipeline integration

Approving a concept requires verified source rights and creates a normal `VideoCandidate` with provider `long_form`. A `source_structural` evaluation translates the approved windows into the established visual evidence schema. Research and scripting accept this evaluation when no Gemini `ai_visual` evaluation exists.

Research receives the selected process and visual context, but documentary narration or subtitles never become verified technical facts by themselves. The existing research dossier and human verification gate still control factual claims. Scripts continue to link sentences and beats to real visual references.

The existing Phase 6 planner already separates source timestamps from output timestamps and builds one `EditSegment` per beat. The local source fetcher resolves the persistent source instead of downloading a stock preview. FFmpeg trims multiple non-contiguous windows and concatenates them in output order. `CENTER_CROP`, `FIT`, `BLURRED_BACKGROUND` remain available; `ROI_AWARE` currently uses the center-crop implementation and signals the future tracking boundary. Center crop retains its human framing warning.

TTS remains the primary audio. `SOURCE_AMBIENT_AUDIO_ENABLED=false` mutes source audio by default. When explicitly enabled, the render graph trims the corresponding source-audio windows, reduces them to 12 percent and mixes them below narration. Original dialogue is not treated as authoritative and final captions still come from the approved script.

## Human workflow

1. Open **Sources** and upload a licensed video.
2. Verify source-level rights.
3. Run local scene detection and inspect representative windows.
4. Add a transcript when a provider is configured; no-audio sources continue.
5. Run local structural analysis and inspect stages, moments and budget.
6. Run external AI only when explicitly desired and configured.
7. Generate three to five concepts.
8. Preview, trim and reorder clips; approve one concept.
9. Open its derived candidate and use Research → Script → TTS → Render → Captions → Review.
10. Return to the same source and approve another concept without reimporting it.
