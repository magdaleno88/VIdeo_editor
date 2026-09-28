# Roadmap

Phases 1–7 are implemented. Later phases are architectural plans, not executable or simulated integrations.

| Phase | Module | Completion criteria |
| --- | --- | --- |
| 1 | Discovery, database, rights, initial scoring and approval | Bounded provider queries, normalized provenance, deduplication, persisted scores, explicit rights review, human approval/rejection, API/CLI and offline tests. Implemented. |
| 2 | AI visual analysis and automatic scoring | Explicit bounded preview access, strict timestamped observations, uncertainty, model/prompt/scorer provenance, reusable successful evaluations and comparison with manual labels. **Implemented.** |
| 3 | Technical research and factual verification | Bounded web research, source quality, exact evidence excerpts, claim/source mapping, contradictions, quantitative policy, versioned dossiers and explicit human review. **Implemented.** |
| 4 | Script generation | Versioned educational scripts constrained to reviewed verified claims, with hooks, scene beats, sentence-level provenance, timing and human review. **Implemented.** |
| 5 | Text-to-speech | Provider-neutral voice generation from approved scripts, persisted voice profiles/settings, atomic audio storage, checksum and format checks, provider or estimated timing, duration QA, cache identity and explicit narration review. **Implemented.** |
| 6 | FFmpeg automatic editing | Rights-gated deterministic edit plans, narration-led clip selection, bounded speed/loops, vertical composition, safe FFmpeg execution, ffprobe validation, versioned MP4 assets and human review. **Implemented.** |
| 7 | Subtitles and graphics | Script-derived timed captions, ASS/SRT, verified factual overlays, reusable styles, mobile safe areas, optional branding, preview, versioned decorated MP4 and human review. **Implemented.** |
| 8 | Human approval dashboard | Spanish-friendly candidate and artifact review, source credits, authenticated roles, before/after evidence and explicit QA decisions. **Next module.** |
| 9 | Publishing integrations | QA-gated exports; Facebook Reels first, then Instagram/TikTok/YouTube Shorts; platform authorization, idempotency, schedules and publication receipts. |
| 10 | Analytics and performance feedback | Versioned metrics, retention/completion context, controlled experiments and audited feedback into scoring. |

Before introducing background jobs, add durable job states, idempotency and an outbox. n8n can orchestrate the internal API after these contracts exist. Jobs must recheck rights and approval at execution time. Media storage, backups, real PostgreSQL tests, authentication and quota coordination become prerequisites for shared operation; they are not implied by local MVP readiness.
