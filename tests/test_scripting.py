from copy import deepcopy

import pytest

from app.core.errors import ConflictError
from app.repositories.candidates import CandidateRepository
from app.repositories.research import ResearchRepository
from app.repositories.scripts import ScriptRepository
from app.schemas.domain import (
    ClaimStatus,
    ResearchReview,
    ScriptGenerationPayload,
    ScriptGenerationRequest,
    ScriptReviewRequest,
    ScriptStatus,
    ScriptValidationPayload,
)
from app.services.research.service import ResearchReviewService
from app.services.scripting.service import (
    ScriptFactPolicy,
    ScriptGenerationService,
    ScriptReviewService,
)
from tests.test_research import make_service, seed_research_candidate


@pytest.fixture
def research_candidate(session, video):
    return seed_research_candidate(session, video)


def script_payload(claim_id: int, *, unsupported_number: bool = False):
    number = " Produce 900 piezas por minuto." if unsupported_number else ""
    return {
        "title": "Cómo toma forma una rosca",
        "hook": "Mira cómo el metal cambia de forma desde el primer segundo.",
        "beats": [
            {
                "kind": "HOOK",
                "recommended_start": 0,
                "recommended_end": 2,
                "visual_direction": "Abrir con el movimiento inmediato.",
                "sentences": [
                    {
                        "display_text": (
                            "Mira cómo el metal cambia de forma desde el primer segundo."
                        ),
                        "spoken_text": (
                            "Mira cómo el metal cambia de forma desde el primer segundo."
                        ),
                        "factuality_type": "OBSERVATION",
                        "claim_ids": [],
                        "visual_refs": ["hook:0"],
                    }
                ],
            },
            {
                "kind": "TECHNICAL_INSIGHT",
                "recommended_start": 1,
                "recommended_end": 2,
                "visual_direction": "Mostrar la zona donde actúa la herramienta.",
                "sentences": [
                    {
                        "display_text": (
                            "El laminado forma la rosca mediante deformación plástica, mientras la "
                            "herramienta presiona el material sin retirar viruta." + number
                        ),
                        "spoken_text": (
                            "El laminado forma la rosca mediante deformación plástica, mientras la "
                            "herramienta presiona el material sin retirar viruta." + number
                        ),
                        "factuality_type": "VERIFIED_FACT",
                        "claim_ids": [claim_id],
                        "visual_refs": ["notable:0"],
                    }
                ],
            },
            {
                "kind": "PAYOFF",
                "recommended_start": 3,
                "recommended_end": 5,
                "visual_direction": "Cerrar con la repetición del ciclo.",
                "sentences": [
                    {
                        "display_text": (
                            "La secuencia vuelve a comenzar y permite ver con claridad "
                            "el mismo ciclo industrial mientras la pieza avanza hasta "
                            "quedar lista para la siguiente etapa."
                        ),
                        "spoken_text": (
                            "La secuencia vuelve a comenzar y permite ver con claridad "
                            "el mismo ciclo industrial mientras la pieza avanza hasta "
                            "quedar lista para la siguiente etapa."
                        ),
                        "factuality_type": "OBSERVATION",
                        "claim_ids": [],
                        "visual_refs": ["loop:0"],
                    }
                ],
            },
        ],
        "closing": "Una transformación breve, repetible y visible.",
        "warnings": [],
    }


class FakeScriptProvider:
    name = "gemini"
    model = "test-script-model"
    prompt_version = "verified-short-script-v1"

    def __init__(self, *, validation=None, unsupported_number=False, error=None):
        self.validation = validation
        self.unsupported_number = unsupported_number
        self.error = error
        self.generate_calls = 0
        self.validate_calls = 0
        self.contexts = []

    def generate(self, context, request, variant_index):
        self.generate_calls += 1
        self.contexts.append(deepcopy(context))
        if self.error:
            raise self.error
        claim_id = context["permitted_claims"][0]["id"]
        return ScriptGenerationPayload.model_validate(
            script_payload(claim_id, unsupported_number=self.unsupported_number)
        )

    def validate(self, context, script):
        self.validate_calls += 1
        if self.error:
            raise self.error
        return ScriptValidationPayload.model_validate(
            self.validation
            or {
                "sentences": [
                    {"sentence_index": 0, "supported": True},
                    {"sentence_index": 1, "supported": True},
                    {"sentence_index": 2, "supported": True},
                ]
            }
        )

    def close(self):
        pass


def verified_dossier(session, research_candidate):
    research_service = make_service(session)[0]
    dossier = research_service.research(research_candidate.id).dossier
    ResearchReviewService(CandidateRepository(session), ResearchRepository(session)).review(
        research_candidate.id,
        ResearchReview(decision="VERIFIED", reviewer="Research human", notes="Checked"),
    )
    return ResearchRepository(session).get(dossier.id)


def scripting_service(session, provider=None, *, allow_partial=False, max_variants=3):
    provider = provider or FakeScriptProvider()
    return (
        ScriptGenerationService(
            CandidateRepository(session),
            ResearchRepository(session),
            ScriptRepository(session),
            provider,
            max_variants=max_variants,
            max_claims=20,
            max_context_characters=20_000,
            speaking_rate=150,
            duration_tolerance=0.5,
            allow_partially_supported=allow_partial,
        ),
        provider,
    )


def test_generation_requires_reviewed_dossier(session, research_candidate):
    make_service(session)[0].research(research_candidate.id)
    service, _ = scripting_service(session)
    with pytest.raises(ConflictError, match="human-verified"):
        service.generate(research_candidate.id, ScriptGenerationRequest())


def test_generation_persists_beats_traceability_duration_and_cache(session, research_candidate):
    dossier = verified_dossier(session, research_candidate)
    service, provider = scripting_service(session)
    request = ScriptGenerationRequest(target_duration_seconds=30)
    first = service.generate(research_candidate.id, request)
    reused = service.generate(research_candidate.id, request)
    forced = service.generate(
        research_candidate.id,
        ScriptGenerationRequest(target_duration_seconds=30, force=True),
    )
    draft = first.scripts[0]
    assert first.reused is False and reused.reused is True
    assert forced.scripts[0].id != draft.id
    assert provider.generate_calls == provider.validate_calls == 2
    assert draft.research_dossier_id == dossier.id
    assert draft.word_count > 0 and draft.estimated_duration_seconds > 0
    assert len(draft.beats) == 3
    assert draft.beats[0].kind == "HOOK"
    assert draft.beats[1].sentences[0].claim_ids
    assert draft.beats[0].sentences[0].visual_refs == ["hook:0"]
    assert provider.contexts[0]["permitted_visuals"]


def test_fact_policy_excludes_unverified_contradicted_and_partial_by_default(
    session, research_candidate
):
    dossier = verified_dossier(session, research_candidate)
    allowed = ScriptFactPolicy(False, 20).allowed_claims(dossier)
    assert allowed
    assert all(
        dossier_claim.status == ClaimStatus.VERIFIED
        for dossier_claim in dossier.claims
        if dossier_claim.id in allowed
    )
    partial = next(item for item in dossier.claims if item.status == ClaimStatus.VERIFIED)
    partial.status = ClaimStatus.PARTIALLY_SUPPORTED
    assert partial.id not in ScriptFactPolicy(False, 20).allowed_claims(dossier)
    assert ScriptFactPolicy(True, 20).allowed_claims(dossier)[partial.id]["caution_required"]


def test_unsupported_sentence_and_number_require_revision(session, research_candidate):
    verified_dossier(session, research_candidate)
    provider = FakeScriptProvider(
        unsupported_number=True,
        validation={
            "sentences": [
                {"sentence_index": 0, "supported": True},
                {"sentence_index": 1, "supported": False, "notes": "External number"},
                {"sentence_index": 2, "supported": True},
            ],
            "external_facts_detected": True,
        },
    )
    service, _ = scripting_service(session, provider)
    draft = service.generate(
        research_candidate.id, ScriptGenerationRequest(target_duration_seconds=30)
    ).scripts[0]
    assert draft.status == ScriptStatus.NEEDS_REVISION
    assert any("quantitative" in item or "unsupported" in item for item in draft.warnings)
    with pytest.raises(ConflictError, match="validated script"):
        ScriptReviewService(CandidateRepository(session), ScriptRepository(session)).review(
            draft.id,
            ScriptReviewRequest(decision="APPROVED", reviewer="Human", notes="Not ready"),
        )


def test_variant_limits_versioning_and_human_review(session, research_candidate):
    verified_dossier(session, research_candidate)
    service, _ = scripting_service(session, max_variants=2)
    with pytest.raises(ConflictError, match="At most 2"):
        service.generate(research_candidate.id, ScriptGenerationRequest(variants=3))
    drafts = service.generate(
        research_candidate.id,
        ScriptGenerationRequest(variants=2, target_duration_seconds=30),
    ).scripts
    assert [item.variant_index for item in drafts] == [1, 2]
    review = ScriptReviewService(CandidateRepository(session), ScriptRepository(session))
    approved = review.review(
        drafts[0].id,
        ScriptReviewRequest(decision="APPROVED", reviewer="Human", notes="Ready"),
    )
    rejected = review.review(
        drafts[1].id,
        ScriptReviewRequest(decision="REJECTED", reviewer="Human", notes="Weak hook"),
    )
    assert approved.status == ScriptStatus.APPROVED
    assert approved.reviews[-1].decision == "APPROVED"
    assert rejected.status == ScriptStatus.REJECTED
