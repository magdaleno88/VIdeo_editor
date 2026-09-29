from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

import app.cli
from app.core.config import ScoringWeights, Settings
from app.core.errors import RenderProcessError
from app.main import create_app
from app.models import LongFormSource, SourceRights, SourceScene
from app.repositories.sources import SourceRepository
from app.schemas.domain import AnalysisProfile, SourceAnalysisRequest
from app.services.sources.scenes import DetectedScene
from app.services.sources.service import LongFormSourceService
from app.services.sources.storage import SourceStorage


class FakeSceneDetector:
    calls = 0
    configurations: list[dict] = []
    fail = False

    def __init__(self, ffmpeg_binary, **configuration):
        self.configuration = configuration
        self.__class__.configurations.append(configuration)

    def detect(self, source, duration, frame_directory):
        self.__class__.calls += 1
        frame_directory.mkdir(parents=True)
        first = frame_directory / "scene-0001.jpg"
        first.write_bytes(b"first-frame")
        if self.__class__.fail:
            raise RenderProcessError("Synthetic scene detection failure")
        split = 4.0 if self.configuration["threshold"] >= 0.4 else 5.0
        second = frame_directory / "scene-0002.jpg"
        second.write_bytes(b"second-frame")
        return [DetectedScene(0, split, first), DetectedScene(split, duration, second)]


@pytest.fixture(autouse=True)
def reset_fake_detector():
    FakeSceneDetector.calls = 0
    FakeSceneDetector.configurations = []
    FakeSceneDetector.fail = False


def source_fixture(session, tmp_path):
    root = tmp_path / "sources"
    original = root / "1" / "original.mp4"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"video")
    source = LongFormSource(
        title="Scene replacement fixture",
        source_type="LOCAL_UPLOAD",
        original_filename="fixture.mp4",
        relative_path="1/original.mp4",
        checksum_sha256="c" * 64,
        size_bytes=5,
        duration_seconds=10,
        width=1920,
        height=1080,
        fps=30,
        video_codec="h264",
        audio_codec="aac",
        has_audio=True,
        container="mov,mp4",
        rotation=0,
        status="INGESTED",
        rights=SourceRights(rights_status="UNKNOWN", notes=""),
    )
    session.add(source)
    session.flush()
    storage = object.__new__(SourceStorage)
    storage.root = root.resolve()
    storage.incoming = storage.root
    storage.max_size_bytes = 1_000_000
    settings = SimpleNamespace(
        ffmpeg_binary="fake",
        scene_threshold=0.3,
        minimum_scene_duration_seconds=2.0,
        scene_merge_threshold_seconds=1.0,
        scene_analysis_max_scenes=120,
        coarse_frame_budget=80,
        deep_analysis_max_moments=12,
        deep_analysis_window_seconds=20,
        max_concepts_per_source=5,
        long_video_max_analysis_minutes=120,
        gemini_video_model="",
        scoring_weights=ScoringWeights(),
    )
    service = object.__new__(LongFormSourceService)
    service.session = session
    service.settings = settings
    service.sources = SourceRepository(session)
    service.storage = storage
    service.scene_detection_outcome = None
    return source, service, storage, settings


def scene_state(session, source_id):
    rows = list(
        session.scalars(
            select(SourceScene)
            .where(SourceScene.source_id == source_id)
            .order_by(SourceScene.position)
        )
    )
    return [(item.id, item.position, item.representative_frame_path) for item in rows]


def test_scene_detection_reuses_same_config_and_atomically_replaces_changed_config(
    session, tmp_path, monkeypatch
):
    monkeypatch.setattr("app.services.sources.service.FFmpegSceneDetector", FakeSceneDetector)
    source, service, storage, settings = source_fixture(session, tmp_path)

    first = service.detect_scenes(source.id)
    assert service.scene_detection_outcome == "SCENES_DETECTED"
    assert FakeSceneDetector.calls == 1
    assert [item.position for item in first.scenes] == [0, 1]
    first_state = scene_state(session, source.id)
    first_paths = [storage.root / item[2] for item in first_state]
    session.commit()

    repeated = service.detect_scenes(source.id)
    assert service.scene_detection_outcome == "SCENES_REUSED"
    assert FakeSceneDetector.calls == 1
    repeated_state = [
        (item.id, item.position, item.representative_frame_path) for item in repeated.scenes
    ]
    assert repeated_state == first_state

    forced = service.detect_scenes(source.id, force=True)
    assert service.scene_detection_outcome == "SCENES_REDETECTED"
    assert FakeSceneDetector.calls == 2
    forced_paths = [storage.root / item.representative_frame_path for item in forced.scenes]
    session.commit()
    assert all(not path.exists() for path in first_paths)
    assert all(path.is_file() for path in forced_paths)

    settings.scene_threshold = 0.4
    changed = service.detect_scenes(source.id)
    assert service.scene_detection_outcome == "SCENES_REDETECTED"
    assert FakeSceneDetector.calls == 3
    assert changed.scenes[0].end_seconds == 4
    session.commit()

    settings.minimum_scene_duration_seconds = 2.5
    service.detect_scenes(source.id)
    assert service.scene_detection_outcome == "SCENES_REDETECTED"
    assert FakeSceneDetector.calls == 4
    session.commit()
    duplicates = session.execute(
        text(
            "SELECT source_id, position, COUNT(*) FROM source_scenes "
            "GROUP BY source_id, position HAVING COUNT(*) > 1"
        )
    ).all()
    assert duplicates == []
    referenced = {
        (storage.root / item.representative_frame_path).resolve()
        for item in service.sources.get(source.id).scenes
    }
    stored = {item.resolve() for item in (storage.root / "1" / "frames").rglob("*.jpg")}
    assert stored == referenced


def test_failed_redetection_and_transaction_rollback_preserve_previous_scenes(
    session, tmp_path, monkeypatch
):
    monkeypatch.setattr("app.services.sources.service.FFmpegSceneDetector", FakeSceneDetector)
    source, service, storage, _ = source_fixture(session, tmp_path)
    service.detect_scenes(source.id)
    session.commit()
    original = scene_state(session, source.id)
    original_paths = [storage.root / item[2] for item in original]

    FakeSceneDetector.fail = True
    with pytest.raises(RenderProcessError, match="Synthetic"):
        service.detect_scenes(source.id, force=True)
    assert scene_state(session, source.id) == original
    assert all(path.is_file() for path in original_paths)
    assert not list((storage.root / "1").glob(".scene-detection-*"))

    FakeSceneDetector.fail = False
    replacement = service.detect_scenes(source.id, force=True)
    replacement_paths = [
        storage.root / item.representative_frame_path for item in replacement.scenes
    ]
    assert replacement_paths != original_paths
    session.rollback()
    assert scene_state(session, source.id) == original
    assert all(path.is_file() for path in original_paths)
    assert all(not path.exists() for path in replacement_paths)


def test_analysis_profiles_preserve_scene_rows(session, tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.sources.service.FFmpegSceneDetector", FakeSceneDetector)
    source, service, _, _ = source_fixture(session, tmp_path)
    service.detect_scenes(source.id)
    session.commit()
    original = scene_state(session, source.id)
    for profile in (
        AnalysisProfile.BALANCED,
        AnalysisProfile.ECONOMY,
        AnalysisProfile.QUALITY,
    ):
        service.analyze(source.id, SourceAnalysisRequest(profile=profile))
        assert scene_state(session, source.id) == original
    assert FakeSceneDetector.calls == 1


def test_dashboard_and_api_scene_actions_are_explicit_and_idempotent(
    engine, session, tmp_path, monkeypatch
):
    source, _, storage, _ = source_fixture(session, tmp_path)
    source_id = source.id
    session.commit()
    session.close()
    monkeypatch.setattr("app.services.sources.service.FFmpegSceneDetector", FakeSceneDetector)
    monkeypatch.setattr("app.services.sources.service.SourceStorage", lambda *args: storage)
    app = create_app(Settings(_env_file=None), engine)
    with TestClient(app) as client:
        csrf = app.state.dashboard_csrf_token
        first = client.post(f"/sources/{source_id}/scene-detection")
        assert first.status_code == 200
        assert FakeSceneDetector.calls == 1
        repeated = client.post(f"/sources/{source_id}/scene-detection")
        assert repeated.status_code == 200
        assert FakeSceneDetector.calls == 1
        forced = client.post(f"/sources/{source_id}/scene-detection?force=true")
        assert forced.status_code == 200
        assert FakeSceneDetector.calls == 2
        page = client.get(f"/dashboard/sources/{source_id}")
        assert "Scenes detected: 2" in page.text
        assert "Re-detect Scenes" in page.text
        redetected = client.post(
            f"/dashboard/sources/{source_id}/actions/redetect-scenes",
            data={"csrf_token": csrf},
            follow_redirects=False,
        )
        assert redetected.status_code == 303
        assert "Scenes+re-detected" in redetected.headers["location"]
        assert FakeSceneDetector.calls == 3
        with app.state.session_factory() as read_session:
            before_profiles = [item[0] for item in scene_state(read_session, source_id)]
        for profile in ("ECONOMY", "QUALITY"):
            analyzed = client.post(
                f"/dashboard/sources/{source_id}/actions/analyze",
                data={"csrf_token": csrf, "profile": profile},
                follow_redirects=False,
            )
            assert analyzed.status_code == 303
        assert FakeSceneDetector.calls == 3
        with app.state.session_factory() as read_session:
            assert [item[0] for item in scene_state(read_session, source_id)] == before_profiles

        FakeSceneDetector.fail = True
        failed_dashboard = client.post(
            f"/dashboard/sources/{source_id}/actions/redetect-scenes",
            data={"csrf_token": csrf},
            follow_redirects=False,
        )
        assert failed_dashboard.status_code == 303
        assert "error=Synthetic+scene+detection+failure" in failed_dashboard.headers["location"]
        failed_api = client.post(f"/sources/{source_id}/scene-detection?force=true")
        assert failed_api.status_code == 502
        assert failed_api.json() == {"detail": "Synthetic scene detection failure"}
        with app.state.session_factory() as read_session:
            assert [item[0] for item in scene_state(read_session, source_id)] == before_profiles


def test_cli_detect_scenes_passes_explicit_force(engine, monkeypatch, capsys):
    observed = []

    class FakeSourceService:
        def __init__(self, session, settings):
            pass

        def detect_scenes(self, source_id, *, force=False):
            observed.append((source_id, force))
            return []

    monkeypatch.setattr(app.cli, "build_engine", lambda url: engine)
    monkeypatch.setattr(engine, "dispose", lambda: None)
    monkeypatch.setattr(app.cli, "LongFormSourceService", FakeSourceService)
    assert app.cli.main(["source", "detect-scenes", "1"]) == 0
    assert app.cli.main(["source", "detect-scenes", "1", "--force"]) == 0
    assert observed == [(1, False), (1, True)]
    capsys.readouterr()
