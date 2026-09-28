# Local pilot workflow and final quality review

Phase 8B adds an operational validation layer over the existing pipeline. It does not duplicate discovery, research, scripting, narration, rendering or caption generation. A pilot batch stores only candidate membership; its progress is resolved from the current persisted artifacts every time the page or API is read.

## Run a pilot

1. Start the application on `127.0.0.1` and open `/dashboard/pilots`.
2. Create a named batch and optionally enter comma-separated existing candidate IDs. A batch supports up to 20 candidates; the intended real pilot is at least five distinct videos.
3. Open a candidate. The page shows source, rights, candidate approval, visual analysis, research, script, narration, raw render, captions, final video and quality review.
4. Follow the displayed next action. External Gemini, Brave or ElevenLabs actions are explicitly marked and require a confirmation. Page loads never start them.
5. On a final render, watch the video, compare versions and complete all eight scores and the checklist. A rejection requires at least one category and routes revision to the earliest affected stage.
6. Download the final MP4 and SRT through their known database IDs after review.

States are `IN_REVIEW`, `BLOCKED`, `REJECTED` and `READY`. `READY` requires an approved final render plus an approved structured quality review. Rejected categories map to research, script, narration, render plan, caption plan or final review. Existing technical data supplies flags such as estimated alignment, duration mismatch, loops, wide-source center crops, missing visual references and partially supported research.

The batch screen reports state counts, mean quality scores and common rejection categories. Candidate detail reports external-call counts, version counts and elapsed time when the final approval timestamp is available. These counts describe persisted records; they are not provider billing statements.

## Real versus synthetic evidence

`scripts/live_dashboard_smoke.py` creates five synthetic/local candidate records and uses the real locally rendered caption-smoke MP4 and preview. It proves migrations, loopback HTTP, forms, persistence, state resolution and safe downloads. It does not count as five reviewed real-source pilots and never makes a paid API call.

The product recommendation must remain pending until an operator completes structured reviews for at least five real candidates with verified rights. At that point, use the batch averages and rejection frequencies to decide whether to continue, revise the pipeline or stop.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_dashboard.py
.\.venv\Scripts\python.exe scripts/live_dashboard_smoke.py
```

The second command prints `LOCAL PILOT WORKFLOW VERIFIED` only after a five-candidate batch reaches a persisted `READY` result and both MP4/SRT downloads succeed over an actual loopback Uvicorn server.
