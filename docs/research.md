# Technical research and factual verification

Phase 3 converts a Phase 2 visual analysis into a versioned, human-reviewable technical dossier. It does not generate a Reel script. A future script generator must treat reviewed `VERIFIED_FACT` claims as its factual allowlist.

## Epistemic boundary

The data model keeps four meanings separate:

| Type | Meaning | Example |
| --- | --- | --- |
| `OBSERVATION` | Visible pixels recorded by Phase 2 | “A cylindrical workpiece rotates between two tools.” |
| `INFERENCE` | Plausible interpretation requiring caution | “This may be thread rolling.” |
| `VERIFIED_FACT` | Claim supported by retained external evidence | “Thread rolling forms threads through plastic deformation.” |
| `UNKNOWN` | Insufficient external evidence | “The alloy used in this video is unknown.” |

A general fact can be well supported while its applicability to the video remains uncertain. Every research claim therefore stores `source_support_confidence` separately from `video_applicability_confidence`.

## Explicit pipeline

1. Load the latest successful AI visual evaluation.
2. Generate inspectable general-process and claim-verification questions/queries.
3. Run at most the configured number of Brave searches.
4. Deduplicate and rank sources with local Tier A–D rules.
5. Attempt only the configured number of pages through the safe document fetcher.
6. Ask Gemini to extract short evidence excerpts and candidate claims.
7. Verify every excerpt occurs in the fetched text.
8. Make a second Gemini call to classify every claim and identify contradictions.
9. Apply local source-quality and quantitative-claim policy.
10. Persist the dossier, sources, claims, evidence links and contradictions as one transaction.
11. Require an explicit human `VERIFIED` or `REJECTED` decision.

The LLM receives untrusted documents with instructions to ignore embedded commands. It cannot create valid evidence by citing an unknown URL or a quotation absent from the retrieved document. Factual synthesis items contain claim keys, so steps, explanations and interesting facts remain traceable to claim/source records.

## Claim and source policy

Claims use these statuses:

- `VERIFIED`: explicit supporting evidence passed local source policy;
- `PARTIALLY_SUPPORTED`: some support exists, but scope or source quality is insufficient;
- `UNVERIFIED`: evidence is absent or unusable; this does not mean false;
- `CONTRADICTED`: retained evidence conflicts with the statement.

Source tiers are conservative:

- Tier A: recognized standards endpoints, government and academic domains;
- Tier B: recognized engineering/standards associations;
- Tier C: other technical or educational web material;
- Tier D: forums and social platforms.

Tier D cannot verify a claim. A quantitative statement containing figures, units, rates, pressures, temperatures or durations needs Tier A/B support to remain `VERIFIED`. Otherwise it is downgraded. This automated tier is an initial policy signal, not a substitute for human source assessment.

Each claim/source edge stores a short excerpt, location, `SUPPORTS`/`CONTRADICTS` relation and confidence that the source says the quoted information. Complete pages are not persisted.

Contradictions retain involved claim IDs, source IDs, possible explanations and an unresolved flag. The system does not silently reconcile heated/cold variants, different machines or conflicting ranges.

## Providers and configuration

Brave Web Search is the first `WebSearchProvider`; Gemini is the first `ResearchAIProvider`. The research core depends only on these contracts. Configure locally:

```dotenv
BRAVE_SEARCH_API_KEY=your-local-secret
RESEARCH_SEARCH_PROVIDER=brave
GEMINI_API_KEY=your-local-secret
GEMINI_RESEARCH_MODEL=your-text-model
RESEARCH_MAX_SEARCH_QUERIES=5
RESEARCH_MAX_SOURCES=8
RESEARCH_MAX_PAGES=5
RESEARCH_MIN_SOURCES=2
RESEARCH_MAX_DOCUMENT_BYTES=500000
RESEARCH_HTTP_TIMEOUT_SECONDS=15
RESEARCH_MAX_LLM_CALLS=2
```

No model name or secret is hardcoded. Search/API error bodies, fetched URLs, document content and prompts are excluded from logs and audit events.

## Web safety and publisher controls

The fetcher only accepts HTTPS without embedded credentials or nonstandard ports. It resolves the hostname and rejects non-public addresses, disables redirects, consults `robots.txt`, sends an identifiable user agent, permits HTML/XHTML/plain text, enforces declared and streamed size limits, ignores scripts/styles and retains at most 20,000 normalized characters for analysis. Publisher terms still apply; the operator must ensure permitted access and use. PDF extraction and broad crawling are intentionally absent.

## API and CLI

```http
POST /candidates/15/research
Content-Type: application/json

{"force": false}
```

```powershell
.\.venv\Scripts\python.exe -m app candidate research 15
.\.venv\Scripts\python.exe -m app candidate research-show 15
.\.venv\Scripts\python.exe -m app candidate claims 15
.\.venv\Scripts\python.exe -m app candidate sources 15
.\.venv\Scripts\python.exe -m app candidate research-verify 15 --decision VERIFIED --reviewer "Name" --notes "Claims checked"
```

Read operations and human review do not require provider credentials. Research itself is explicit and never runs during discovery or visual scoring.

## Cost, reuse and failures

One fresh run performs up to five searches, attempts up to five source pages and makes exactly two structured LLM calls. A successful dossier is reused when candidate, search provider, research provider, model, prompt version and research version match. `force=true` creates historical research again. Rejected or failed work is not reused.

No search results, too few usable documents, blocked/oversized/unsupported pages, provider failure, timeout, fabricated excerpts, malformed structured output and insufficient evidence remain errors. None is converted into a false claim or an empty “successful” dossier.

## Verification boundary

Offline tests replace Brave, document retrieval and Gemini. They exercise planning, ranking, robots/SSRF/size/content rules, exact-excerpt validation, multiple-source evidence, unsupported/contradicted/quantitative claims, relationships, reuse, force, human review, API, CLI and migrations. Live Brave and Gemini behavior, quotas, billing and changing web content require separately supplied credentials and are not claimed by the standard suite.
