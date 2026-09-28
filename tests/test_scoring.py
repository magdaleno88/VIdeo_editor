import pytest
from pydantic import ValidationError

from app.core.config import ScoringWeights, Settings
from app.schemas.domain import ScoreInputs
from app.services.scoring.scorers import HeuristicVideoScorer, manual_score, weighted_score


@pytest.mark.parametrize("value", [0, 25, 100])
def test_weighted_scale(value):
    inputs = ScoreInputs(**dict.fromkeys(ScoreInputs.model_fields, value))
    assert weighted_score(inputs, ScoringWeights()) == value


def test_weights_configuration(monkeypatch):
    monkeypatch.setenv("SCORING_WEIGHTS__MOVEMENT", "30")
    monkeypatch.setenv("SCORING_WEIGHTS__VISIBLE_TRANSFORMATION", "10")
    weights = Settings(_env_file=None).scoring_weights
    values = dict.fromkeys(ScoreInputs.model_fields, 0)
    values["movement"] = 100
    assert weighted_score(ScoreInputs(**values), weights) == 30


@pytest.mark.parametrize("value", [-1, 101, float("nan"), float("inf")])
def test_invalid_scores(value):
    with pytest.raises(ValidationError):
        ScoreInputs(**dict.fromkeys(ScoreInputs.model_fields, value))


def test_invalid_weights():
    with pytest.raises(ValidationError):
        ScoringWeights(movement=50)


def test_heuristic_is_explicit_and_deterministic(video):
    scorer = HeuristicVideoScorer(ScoringWeights())
    score = scorer.score(video)
    assert score == scorer.score(video)
    assert score.method == "metadata_heuristic"
    assert score.analyzed_video is False
    assert score.inputs.visible_transformation == 40
    assert "visual_hook" in score.unknown_dimensions
    assert "Unverified text prior" in score.rationale["visible_transformation"]
    assert score.total == 15.5


def test_no_signals_and_manual_score(video):
    video = video.model_copy(update={"title": "Landscape", "description": ""})
    assert HeuristicVideoScorer(ScoringWeights()).score(video).total == 0
    result = manual_score(
        ScoreInputs(**dict.fromkeys(ScoreInputs.model_fields, 70)), ScoringWeights()
    )
    assert result.total == 70
    assert result.method == "manual"
    assert result.unknown_dimensions == []
