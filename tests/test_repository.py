from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.core.database import build_engine, session_factory, session_scope, utcnow
from app.models import SearchCache, VideoCandidate
from app.providers.base import ProviderPage
from app.repositories.cache import SearchCacheRepository
from app.repositories.candidates import CandidateRepository
from app.schemas.domain import CandidateFilters, CandidateRead, Idea
from tests.conftest import migrate


def test_round_trip_and_duplicate_identity(session, video):
    repository = CandidateRepository(session)
    candidate, created = repository.add_if_new(video, "screw factory", Idea(object_name="screw"))
    assert created
    first_id = candidate.id
    session.commit()
    session.expire_all()
    duplicate, created = repository.add_if_new(video, "different query", Idea(object_name="nail"))
    assert not created
    assert duplicate.id == first_id
    assert duplicate.search_query == "screw factory"
    assert duplicate.object_being_manufactured == "screw"
    assert duplicate.created_at.tzinfo is not None
    assert CandidateRead.model_validate(duplicate).rights_status == "MANUAL_REVIEW_REQUIRED"


def test_database_unique_constraint_survives_precheck_race(session, video, monkeypatch):
    repository = CandidateRepository(session)
    original, _ = repository.add_if_new(video, "one", Idea(object_name="screw"))
    session.commit()
    actual_lookup = repository.find_identity
    calls = 0

    def simulated_race(provider, video_id):
        nonlocal calls
        calls += 1
        return None if calls == 1 else actual_lookup(provider, video_id)

    monkeypatch.setattr(repository, "find_identity", simulated_race)
    duplicate, created = repository.add_if_new(video, "two", Idea(object_name="screw"))
    assert duplicate.id == original.id
    assert not created
    assert session.scalar(select(func.count()).select_from(VideoCandidate)) == 1


def test_failed_batch_rolls_back_savepoint_insert(engine, video):
    factory = session_factory(engine)
    with pytest.raises(RuntimeError), session_scope(factory) as session:
        CandidateRepository(session).add_if_new(video, "screw", Idea(object_name="screw"))
        raise RuntimeError("Simulated failure after insertion")
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(VideoCandidate)) == 0


def test_filters_pagination_and_top_sort(session, video):
    repository = CandidateRepository(session)
    for number, score in enumerate([10, 70, 30]):
        item = video.model_copy(update={"provider_video_id": str(number)})
        candidate, _ = repository.add_if_new(
            item, "screw", Idea(object_name="screw", category="metal", process_name="forging")
        )
        candidate.total_score = score
    session.flush()
    filters = CandidateFilters(
        provider="pexels",
        category="metal",
        industrial_process="forging",
        minimum_score=20,
        limit=1,
        offset=1,
    )
    items, total = repository.list(filters, top=True)
    assert total == 2
    assert len(items) == 1
    assert items[0].total_score == 30
    assert repository.list(CandidateFilters(provider="pixabay"))[1] == 0


def test_cache_round_trip_and_expiration(session, video):
    cache = SearchCacheRepository(session)
    key = cache.key("pexels", "screw", 1, 10)
    assert cache.get(key) is None
    cache.put(key, ProviderPage(items=[video], skipped=2))
    session.commit()
    restored = cache.get(key)
    assert restored.items[0] == video
    assert restored.skipped == 2
    assert key != cache.key("pexels", "screw", 2, 10)
    entry = session.get(SearchCache, key)
    entry.expires_at = utcnow() - timedelta(seconds=1)
    session.flush()
    assert cache.get(key) is None
    cache.put(key, ProviderPage(items=[]))
    assert cache.get(key).items == []


def test_optimistic_concurrency_rejects_stale_review(tmp_path, video):
    engine = build_engine(f"sqlite:///{(tmp_path / 'concurrent.db').as_posix()}")
    migrate(engine)
    factory = session_factory(engine)
    with session_scope(factory) as session:
        candidate, _ = CandidateRepository(session).add_if_new(
            video, "screw", Idea(object_name="screw")
        )
        candidate_id = candidate.id
    with factory() as first, factory() as second:
        a = CandidateRepository(first).get(candidate_id)
        first.commit()
        b = CandidateRepository(second).get(candidate_id)
        second.commit()
        a.status = "REJECTED"
        first.commit()
        b.status = "SCORED"
        with pytest.raises(StaleDataError):
            second.commit()
        second.rollback()
    engine.dispose()


def test_score_db_constraint(session, video):
    candidate, _ = CandidateRepository(session).add_if_new(
        video, "screw", Idea(object_name="screw")
    )
    candidate.total_score = 150
    with pytest.raises(IntegrityError):
        session.flush()
