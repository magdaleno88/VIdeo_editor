# Scoring specification

The project keeps three distinct methods: `HeuristicVideoScorer` for metadata priors, `ManualVideoScorer`/`manual_score` for human ratings, and `AIVideoScorer` for real visual analysis. All produce the same authoritative eight ratings and configured weighted total. Their provenance is never collapsed into one method label.

## Dimensions and weights

| Input | Weight | Meaning when assessed by a human |
| --- | ---: | --- |
| `movement` | 20 | Visible action and progression |
| `visible_transformation` | 20 | A discernible before/after change |
| `satisfying_result` | 15 | A clear and satisfying process outcome |
| `unusual_machinery` | 15 | Distinctive equipment with visual interest |
| `understandable_without_audio` | 10 | The process is legible without narration |
| `visual_hook` | 10 | A compelling opening moment |
| `educational_potential` | 5 | A process that supports a useful explanation |
| `loop_potential` | 5 | An ending that can lead naturally into the beginning |

`total = round(sum(input[dimension] * weight[dimension]) / 100, 2)`. Each rating is finite and between 0 and 100. Weights are nonnegative, at most 100 each and sum to 100. Override nested environment settings, for example `SCORING_WEIGHTS__MOVEMENT=30` and `SCORING_WEIGHTS__VISIBLE_TRANSFORMATION=10` together. A changed configuration affects new evaluations only.

## Automatic metadata priors

Keyword matching uses word boundaries in normalized provider title/description text. Pexels has no editorial title field in the video response, so its title comes from the source URL slug. Pixabay supplies tags. Neither the search query nor user-assigned labels are scored as evidence.

| Dimension | Provider text keywords | Assigned prior if any match |
| --- | --- | ---: |
| Movement | rolling, spinning, conveyor, rotating | 35 |
| Transformation | casting, forging, molding, cutting, welding | 40 |
| Machinery | cnc, lathe, press, robot, machine | 30 |
| Education | manufacturing, factory, industrial, production, assembly, process | 60 |

Other dimensions and unmatched dimensions receive zero as a conservative unknown placeholder. Zero does not mean the clip was observed to lack the feature. Every result includes per-dimension rationale and an `unknown_dimensions` list. Matched priors remain unverified text estimates. Default weights cap this heuristic at 22.5, and it can miss relevant videos without descriptive metadata. It is neither calibrated for retention nor a reliable comparison against complete manual ratings.

## AI visual scoring

The AI provider must return a strict `VideoVisualAnalysisPayload`. Each dimension contains a 0–100 rating, 0–1 confidence, explanation and zero or more timestamped observations. Empty evidence is valid when a feature is not visibly supported; the prompt instructs the model to lower confidence and warn rather than invent evidence. Timestamps, frame timestamps and segment bounds are validated against `duration_analyzed`.

The analysis also records a summary, optional visible process/object labels, notable moments, up to five hook segments, up to five loop segments, warnings and a separate inspection of the first one to three seconds. That opening inspection covers immediate motion, novelty, visible transformation, clarity and required context. Hook and loop segments are suggestions for later editing; Phase 2 does not alter the video.

`AIVideoScorer` calculates the total locally from the validated ratings and current configured weights. It never accepts a model-supplied total. `overall_confidence` is the arithmetic mean of the eight dimension confidences and is descriptive uncertainty, not statistical accuracy. The score estimates visual suitability and retention potential for an educational short; it does not predict views.

## Storage and manual review

`video_candidates.score_details` and convenience columns hold the current score for compatible listing/ranking. Every heuristic, manual and AI result is also appended to `score_evaluations`, with method, scorer version, weights, ratings, total and timestamp. AI evaluations add provider, model, prompt version, overall confidence and the complete validated visual analysis. Existing rows are not overwritten. Manual review is distinguished by method and the reviewer/notes in the audit event.

Candidate convenience fields:

| Field | Mapping |
| --- | --- |
| `visual_score` | Mean of movement, satisfying result and understandable-without-audio |
| `transformation_score` | Visible transformation |
| `machine_score` | Unusual machinery |
| `hook_score` | Visual hook |
| `loop_score` | Loop potential |
| `educational_score` | Educational potential |

These summaries do not replace the weighted eight-input total. A manual request must provide every dimension plus a reviewer and notes. Any new current score revokes an existing approval. Rejection remains in place until an explicit new approval decision. AI output neither verifies rights nor approves a candidate.

Successful AI analysis is reused only when candidate, AI provider, model, prompt version and scorer version match. `force=true` creates a fresh evaluation and makes a new external call. A failed request or invalid structured response creates no evaluation and remains retryable. If only configured weights change, stored visual analysis can be reweighted into a new evaluation without another model call.

`score-comparison` uses the latest AI and latest manual rows. It reports signed AI-minus-manual differences for every dimension and total, plus mean absolute error across the eight dimensions. This is a descriptive comparison and is deliberately not called accuracy.

## Calibration still pending

Phase 2 provides comparison data but does not claim that the AI ratings are calibrated. A future evaluation set should use independently labeled clips and defined agreement metrics. Later performance metrics may inform weight calibration with held-out evaluation and exposure context rather than treating raw view counts as ground truth.
