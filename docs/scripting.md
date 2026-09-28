# Verified short-form scripting

Phase 4 turns a human-verified research dossier and its matching visual analysis into a versioned Spanish script for Facebook Reels. It does not search the web, read source pages, use model memory as factual evidence, produce audio or edit video.

## Factual allowlist

`ScriptFactPolicy` builds the only technical facts visible to the script model. It admits `VERIFIED` claims by default. `PARTIALLY_SUPPORTED` claims are excluded unless `SCRIPT_ALLOW_PARTIALLY_SUPPORTED=true`; when admitted, sentences must use cautious wording. `UNVERIFIED`, `CONTRADICTED`, claims involved in unresolved contradictions, and quantitative claims without supporting evidence are always excluded.

The other permitted inputs are direct Phase 2 observations, stable candidate metadata and explicit editorial context. Source excerpts are not copied into the scripting prompt. The prompt treats every supplied value as untrusted data and instructs Gemini to ignore embedded commands.

## Pipeline

1. Load the candidate, latest AI visual evaluation and latest research dossier.
2. Require the dossier to have a human `VERIFIED` decision.
3. Build the claim allowlist and a visual timeline with stable references such as `hook:0`, `notable:0`, `evidence:0` and `loop:0`.
4. Ask the configured provider for one Pydantic-validated script variant.
5. Ask the provider for a second structured sentence-level factual audit.
6. Apply local rules for references, numbers, cautious language, clickbait, repetition, visual overlap and duration.
7. Persist an immutable versioned draft and its relational provenance.
8. Require a separate human approval or rejection.

Generation is explicit. Discovery, scoring and research never invoke it.

## Structure and traceability

A `ScriptDraft` contains style, platform, target and estimated duration, speaking rate, title, hook, closing, complete narration, provider versions and warnings. It owns ordered `ScriptBeat` rows; beats own ordered `ScriptSentence` rows. Each sentence has display and spoken text plus a factuality type: `OBSERVATION`, `VERIFIED_FACT`, `INFERENCE` or `EDITORIAL`.

`ScriptSentenceClaim` links factual sentences to existing `ResearchClaim` rows. `ScriptSentenceVisualRef` stores the stable visual reference and timestamp snapshot from the selected visual evaluation. This permits the audit path:

```text
script sentence
  -> ResearchClaim ID
     -> ResearchClaimEvidence
        -> ResearchSource

script sentence
  -> visual ref + visual_evaluation_id
     -> Phase 2 timestamped observation or segment
```

No source document is duplicated into the script tables.

## Styles, duration and visual alignment

The initial styles are `EDUCATIONAL`, `CURIOSITY` and `PROCESS_EXPLAINER`. The initial platform is `FACEBOOK_REELS`; platform is persisted so later platforms can supply their own editorial limits without adding publishing behavior.

Targets are 30, 45 or 60 seconds. Duration is estimated as `word_count / speaking_rate * 60`; it is an estimate, not a promise. The default rate is 150 words per minute. Every beat carries recommended source-video start and end times and must overlap at least one referenced visual. The first beat must be `HOOK` and use a Phase 2 hook segment.

## Validation and statuses

Local validation checks:

- unknown or prohibited claim and visual references;
- factual sentences without the required provenance type;
- unsupported numbers;
- partially supported facts without cautious wording;
- missing or non-overlapping visual alignment;
- repeated narration or repeated visual segments;
- configured duration tolerance;
- a small set of clear false-clickbait patterns;
- complete sentence coverage by the structured AI audit.

Any warning produces `NEEDS_REVISION`. A clean draft is `VALIDATED`. Only `VALIDATED` can receive a human `APPROVED` decision. Rejection produces `REJECTED`. Every decision records reviewer, notes and time in `script_reviews` and the candidate event log.

## Configuration and cost controls

```dotenv
GEMINI_SCRIPT_MODEL=your-text-model
SCRIPT_MAX_VARIANTS=3
SCRIPT_MAX_CLAIMS=20
SCRIPT_MAX_CONTEXT_CHARACTERS=20000
SCRIPT_SPEAKING_RATE_WPM=150
SCRIPT_DURATION_TOLERANCE=0.25
SCRIPT_ALLOW_PARTIALLY_SUPPORTED=false
SCRIPT_TIMEOUT_SECONDS=60
SCRIPT_MAX_RETRIES=1
```

`GEMINI_API_KEY` is reused. One variant uses two model calls. The default is one variant and the hard maximum is three. Context, claims, retries and variants are bounded. An identical successful batch is reused by candidate, dossier, visual evaluation, style, platform, language, duration, variants, editorial context, provider, model, prompt and script versions. `force=true` creates new rows and never overwrites history.

## API and CLI

API:

- `POST /candidates/{id}/scripts`
- `GET /candidates/{id}/scripts`
- `GET /scripts/{script_id}`
- `POST /scripts/{script_id}/approve`
- `POST /scripts/{script_id}/reject`

CLI:

```powershell
python -m app candidate script 15 --duration 45 --style educational
python -m app candidate script 15 --duration 30 --style curiosity --variants 2 --force
python -m app candidate scripts 15
python -m app script show 7
python -m app script approve 7 --reviewer "Name" --notes "Ready"
python -m app script reject 7 --reviewer "Name" --notes "Hook needs revision"
```

## Offline example

```text
Candidate: screw manufacturing clip
  -> Visual evidence: hook:0, 0–2 s, immediate machine motion
  -> Verified claim 14: thread rolling forms threads by plastic deformation
  -> Beat 1 / HOOK: direct visual observation, ref hook:0
  -> Beat 2 / TECHNICAL_INSIGHT: explanatory narration, ref claim 14 + notable:0
  -> Beat 3 / PAYOFF: visible repeated cycle, ref loop:0
  -> VALIDATED draft
  -> human APPROVED decision
```

Tests construct this flow from fixtures without contacting Gemini.

## Limits

Sentence entailment is checked by the configured model plus deterministic reference rules; it is not a formal proof that a paraphrase is semantically identical to a claim. Spanish-only output and Facebook Reels are the initial contracts. Reviewer identities are self-reported, the service is synchronous, and there is no collaborative edit model. Live Gemini quality, quotas and billing are not verified by the offline suite.
