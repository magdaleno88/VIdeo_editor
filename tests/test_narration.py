import hashlib
import io
import json
import wave
from contextlib import contextmanager

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

import app.api.dependencies
import app.cli
from app.core.config import Settings
from app.core.database import build_engine, session_factory, session_scope
from app.core.errors import ConflictError, NarrationValidationError
from app.main import create_app
from app.providers.tts.base import CharacterAlignment, SynthesizedAudio
from app.repositories.candidates import CandidateRepository
from app.repositories.narrations import NarrationRepository
from app.repositories.scripts import ScriptRepository
from app.schemas.domain import (
    AlignmentMethod,
    NarrationRequest,
    NarrationReviewRequest,
    NarrationStatus,
    ScriptGenerationRequest,
    ScriptReviewRequest,
    VoiceSettings,
)
from app.services.narration.audio import AudioStorage
from app.services.narration.service import NarrationReviewService, NarrationService
from app.services.scripting.service import ScriptReviewService
from tests.conftest import ROOT
from tests.test_research import seed_research_candidate
from tests.test_scripting import scripting_service, verified_dossier


@pytest.fixture
def research_candidate(session, video):
    return seed_research_candidate(session, video)


def wav_audio(duration: float) -> bytes:
    frames = max(1, round(duration * 8_000))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(8_000)
        output.writeframes(b"\0\0" * frames)
    return buffer.getvalue()


class FakeTTSProvider:
    name = "elevenlabs"
    model = "test-tts-model"

    def __init__(self, duration: float, *, alignment: bool = True, invalid: bool = False):
        self.duration = duration
        self.alignment = alignment
        self.invalid = invalid
        self.calls: list[tuple[str, str, str, VoiceSettings]] = []

    def synthesize(self, text, voice_id, language, settings):
        self.calls.append((text, voice_id, language, settings))
        if self.invalid:
            return SynthesizedAudio(b"invalid", "WAV", None, None, None, None, {})
        character_alignment = None
        if self.alignment:
            step = self.duration / len(text)
            character_alignment = CharacterAlignment(
                list(text),
                [position * step for position in range(len(text))],
                [(position + 1) * step for position in range(len(text))],
            )
        return SynthesizedAudio(
            wav_audio(self.duration),
            "WAV",
            "PCM_S16LE",
            8_000,
            1,
            character_alignment,
            {"test_provider": True},
        )

    def close(self):
        pass


def approved_script(session, research_candidate):
    verified_dossier(session, research_candidate)
    draft = (
        scripting_service(session)[0]
        .generate(research_candidate.id, ScriptGenerationRequest(target_duration_seconds=30))
        .scripts[0]
    )
    return ScriptReviewService(CandidateRepository(session), ScriptRepository(session)).review(
        draft.id, ScriptReviewRequest(decision="APPROVED", reviewer="Editor", notes="Ready")
    )


def narration_service(session, tmp_path, *, duration, alignment=True, invalid=False):
    provider = FakeTTSProvider(duration, alignment=alignment, invalid=invalid)
    return (
        NarrationService(
            CandidateRepository(session),
            ScriptRepository(session),
            NarrationRepository(session),
            provider,
            AudioStorage(str(tmp_path / "audio")),
            default_voice_id="voice-default",
            default_voice_name="Spanish test voice",
            max_characters=10_000,
            max_file_size_bytes=20 * 1024 * 1024,
            duration_tolerance=0.2,
        ),
        provider,
    )


def test_narration_requires_an_approved_script(session, research_candidate, tmp_path):
    verified_dossier(session, research_candidate)
    draft = (
        scripting_service(session)[0]
        .generate(research_candidate.id, ScriptGenerationRequest(target_duration_seconds=30))
        .scripts[0]
    )
    service, provider = narration_service(
        session, tmp_path, duration=draft.estimated_duration_seconds
    )
    with pytest.raises(ConflictError, match="human-approved"):
        service.generate(draft.id, NarrationRequest())
    assert provider.calls == []


def test_narration_persists_audio_alignment_cache_and_review(session, research_candidate, tmp_path):
    script = approved_script(session, research_candidate)
    service, provider = narration_service(
        session, tmp_path, duration=script.estimated_duration_seconds
    )
    first = service.generate(script.id, NarrationRequest())
    asset = first.narration
    assert first.reused is False
    assert provider.calls[0][0] == "\n".join(
        sentence.spoken_text for beat in script.beats for sentence in beat.sentences
    )
    assert asset.source_text_hash == hashlib.sha256(provider.calls[0][0].encode()).hexdigest()
    assert asset.status == NarrationStatus.VALIDATED
    assert asset.alignment_method == AlignmentMethod.PROVIDER_ALIGNMENT
    assert asset.duration_status == "WITHIN_TARGET"
    assert len(asset.alignments) == sum(len(beat.sentences) for beat in script.beats)
    assert (tmp_path / "audio" / asset.storage_path).is_file()
    assert (
        asset.checksum_sha256
        == hashlib.sha256((tmp_path / "audio" / asset.storage_path).read_bytes()).hexdigest()
    )

    cached = service.generate(script.id, NarrationRequest())
    forced = service.generate(script.id, NarrationRequest(force=True))
    changed_voice = service.generate(script.id, NarrationRequest(voice_id="another-voice"))
    changed_settings = service.generate(
        script.id, NarrationRequest(settings=VoiceSettings(stability=0.4))
    )
    assert cached.reused is True and cached.narration.id == asset.id
    assert forced.reused is False
    assert changed_voice.reused is False and changed_settings.reused is False
    assert len(provider.calls) == 4

    approved = NarrationReviewService(
        CandidateRepository(session), ScriptRepository(session), NarrationRepository(session)
    ).review(
        asset.id,
        NarrationReviewRequest(decision="APPROVED", reviewer="Audio editor", notes="Checked"),
    )
    assert approved.status == NarrationStatus.APPROVED
    assert approved.reviews[-1].decision == "APPROVED"


def test_fallback_alignment_duration_review_and_failed_file_cleanup(
    session, research_candidate, tmp_path, monkeypatch
):
    script = approved_script(session, research_candidate)
    service, _ = narration_service(session, tmp_path, duration=1, alignment=False)
    narration = service.generate(script.id, NarrationRequest()).narration
    assert narration.status == NarrationStatus.NEEDS_REVIEW
    assert narration.alignment_method == AlignmentMethod.ESTIMATED_ALIGNMENT
    with pytest.raises(ConflictError, match="validated narration"):
        NarrationReviewService(
            CandidateRepository(session), ScriptRepository(session), NarrationRepository(session)
        ).review(
            narration.id,
            NarrationReviewRequest(decision="APPROVED", reviewer="Audio editor", notes="Too short"),
        )

    invalid_service, _ = narration_service(
        session, tmp_path / "invalid", duration=script.estimated_duration_seconds, invalid=True
    )
    with pytest.raises(NarrationValidationError, match="valid WAV"):
        invalid_service.generate(script.id, NarrationRequest(voice_id="bad-voice"))
    assert not (tmp_path / "invalid" / "audio").exists()

    failing_service, _ = narration_service(
        session, tmp_path / "persistence-failure", duration=script.estimated_duration_seconds
    )
    monkeypatch.setattr(
        failing_service.narrations,
        "add",
        lambda narration: (_ for _ in ()).throw(RuntimeError("database write failed")),
    )
    with pytest.raises(RuntimeError, match="database write failed"):
        failing_service.generate(script.id, NarrationRequest(voice_id="cleanup-voice"))
    assert list((tmp_path / "persistence-failure" / "audio").rglob("*.wav")) == []


def test_narration_api_uses_configured_runtime(
    engine, session, research_candidate, tmp_path, monkeypatch
):
    script = approved_script(session, research_candidate)
    session.commit()

    @contextmanager
    def fake_runtime(settings, api_session):
        yield narration_service(api_session, tmp_path, duration=script.estimated_duration_seconds)[
            0
        ]

    monkeypatch.setattr(app.api.dependencies, "configured_narration_service", fake_runtime)
    api_app = create_app(Settings(_env_file=None), engine)
    with TestClient(api_app) as client:
        script_id = script.id
        created = client.post(f"/scripts/{script_id}/narrations", json={})
        assert created.status_code == 200
        narration_id = created.json()["narration"]["id"]
        assert client.get(f"/scripts/{script_id}/narrations").status_code == 200
        assert client.get(f"/narrations/{narration_id}").status_code == 200
        approved = client.post(
            f"/narrations/{narration_id}/approve",
            json={"reviewer": "API editor", "notes": "Approved"},
        )
        assert approved.status_code == 200


def test_narration_cli_generate_list_show_and_reject(tmp_path, video, monkeypatch, capsys):
    database = tmp_path / "narration-cli.db"
    url = f"sqlite:///{database.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    engine = build_engine(url)
    with session_scope(session_factory(engine)) as session:
        candidate = seed_research_candidate(session, video)
        script = approved_script(session, candidate)
        script_id = str(script.id)
        duration = script.estimated_duration_seconds
    engine.dispose()

    @contextmanager
    def fake_runtime(settings, cli_session):
        yield narration_service(cli_session, tmp_path, duration=duration)[0]

    monkeypatch.setattr(app.cli, "configured_narration_service", fake_runtime)
    assert app.cli.main(["candidate", "narrate", script_id, "--voice", "voice-cli"]) == 0
    created = json.loads(capsys.readouterr().out)
    narration_id = str(created["narration"]["id"])
    assert app.cli.main(["candidate", "narrations", script_id]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 1
    assert app.cli.main(["narration", "show", narration_id]) == 0
    assert json.loads(capsys.readouterr().out)["id"] == int(narration_id)
    assert (
        app.cli.main(
            [
                "narration",
                "reject",
                narration_id,
                "--reviewer",
                "CLI editor",
                "--notes",
                "Use another voice",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["status"] == "REJECTED"
