import re
from typing import Protocol

from app.core.config import ScoringWeights
from app.schemas.domain import NormalizedVideo, ScoreInputs, ScoreResult


class VideoScorer(Protocol):
    def score(self, video: NormalizedVideo) -> ScoreResult: ...


def weighted_score(inputs: ScoreInputs, weights: ScoringWeights) -> float:
    values = inputs.model_dump()
    return round(sum(values[key] * weight / 100 for key, weight in weights.model_dump().items()), 2)


class HeuristicVideoScorer:
    """Text-only priors: no frames, pixels, sound or retention are analyzed."""

    def __init__(self, weights: ScoringWeights) -> None:
        self.weights = weights

    def score(self, video: NormalizedVideo) -> ScoreResult:
        words = set(re.findall(r"[a-z]+", f"{video.title} {video.description}".lower()))
        signals = {
            "movement": ({"rolling", "spinning", "conveyor", "rotating"}, 35),
            "visible_transformation": ({"casting", "forging", "molding", "cutting", "welding"}, 40),
            "unusual_machinery": ({"cnc", "lathe", "press", "robot", "machine"}, 30),
            "educational_potential": (
                {"manufacturing", "factory", "industrial", "production", "assembly", "process"},
                60,
            ),
        }
        values = {key: 0.0 for key in ScoreInputs.model_fields}
        rationale = {
            key: "Unknown; zero is a conservative placeholder, not an observation."
            for key in values
        }
        unknown = set(values)
        for key, (keywords, prior) in signals.items():
            matches = sorted(words & keywords)
            if matches:
                values[key] = prior
                rationale[key] = "Unverified text prior from provider metadata: " + ", ".join(
                    matches
                )
                unknown.remove(key)
        inputs = ScoreInputs(**values)
        return ScoreResult(
            method="metadata_heuristic",
            inputs=inputs,
            weights=self.weights.model_dump(),
            total=weighted_score(inputs, self.weights),
            rationale=rationale,
            unknown_dimensions=sorted(unknown),
        )


def manual_score(inputs: ScoreInputs, weights: ScoringWeights) -> ScoreResult:
    return ScoreResult(
        method="manual",
        inputs=inputs,
        weights=weights.model_dump(),
        total=weighted_score(inputs, weights),
        rationale={key: "Human reviewer supplied this value." for key in inputs.model_dump()},
    )


class ManualVideoScorer:
    def __init__(self, weights: ScoringWeights) -> None:
        self.weights = weights

    def score(self, inputs: ScoreInputs) -> ScoreResult:
        return manual_score(inputs, self.weights)
