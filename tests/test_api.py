import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_providers
from app.core.config import Settings
from app.main import create_app
from app.providers.pexels import PexelsProvider
from app.providers.pixabay import PixabayProvider
from app.schemas.domain import ProviderName, ScoreInputs

ACTION = {"reviewer": "Test editor", "notes": "Reviewed this candidate"}


def discover(client, **kwargs):
    response = client.post(
        "/discovery/search", json={"object_name": "tornillo", "max_queries": 1, **kwargs}
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_health_openapi_and_query_generation(client):
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/docs").status_code == 200
    response = client.post("/discovery/queries", json={"object_name": "tornillo"})
    assert "thread rolling machine" in response.json()


def test_source_rights_presets_never_auto_verify(client):
    response = client.get("/source-rights/license-presets")
    assert response.status_code == 200
    presets = {item["preset"]: item for item in response.json()}
    assert presets["CC_BY"]["attribution_required"] is True
    assert presets["CC_BY_SA"]["share_alike_required"] is True
    assert presets["CC0"]["commercial_use_allowed"] is True
    assert presets["PUBLIC_DOMAIN"]["derivative_works_allowed"] is True
    assert all(item["rights_status"] == "UNKNOWN" for item in presets.values())


def test_end_to_end_discover_cache_filter_review(client, rights_review):
    first = discover(client, category="metal", process_name="cold heading")
    assert first["created"] == 2
    assert first["duplicates"] == 0
    again = discover(client, category="metal", process_name="cold heading")
    assert again["created"] == 0
    assert again["duplicates"] == 2
    assert again["cache_hits"] == 2
    listing = client.get(
        "/candidates",
        params={
            "provider": "pexels",
            "status": "SCORED",
            "minimum_score": 1,
            "category": "metal",
            "industrial_process": "cold heading",
        },
    ).json()
    assert listing["total"] == 1
    candidate_id = listing["items"][0]["id"]
    url = f"/candidates/{candidate_id}"
    item = client.get(url).json()
    assert item["rights"]["verification_date"] is None
    assert item["score_details"]["analyzed_video"] is False
    assert client.post(f"{url}/approve", json=ACTION).status_code == 409
    assert client.put(f"{url}/rights", json=rights_review).status_code == 200
    response = client.post(f"{url}/approve", json=ACTION)
    assert response.status_code == 200
    assert response.json()["status"] == "APPROVED"
    assert client.get(url).json()["status"] == "APPROVED"
    history = client.get(f"{url}/history").json()
    assert [event["action"] for event in history] == [
        "DISCOVERED_AND_SCORED",
        "RIGHTS_REVIEWED",
        "APPROVED",
    ]
    assert history[-1]["reviewer"] == "Test editor"
    assert client.post(f"{url}/reject", json=ACTION).json()["status"] == "REJECTED"


def test_manual_score_changes_ranking_and_requires_new_approval(client, rights_review):
    ids = discover(client)["candidate_ids"]
    url = f"/candidates/{ids[0]}"
    client.put(f"{url}/rights", json=rights_review)
    client.post(f"{url}/approve", json=ACTION)
    inputs = dict.fromkeys(ScoreInputs.model_fields, 80)
    response = client.post(f"{url}/score", json=ACTION | {"inputs": inputs})
    assert response.status_code == 200
    assert response.json()["total_score"] == 80
    assert response.json()["status"] == "SCORED"
    top = client.get("/candidates/top", params={"limit": 1, "minimum_score": 70}).json()
    assert top["total"] == 1
    assert top["items"][0]["id"] == ids[0]
    assert client.get("/candidates", params={"limit": 1, "offset": 1}).json()["total"] == 2


@pytest.mark.parametrize("path", ["/candidates/999", "/candidates/999/history"])
def test_missing_candidate(client, path):
    assert client.get(path).status_code == 404


@pytest.mark.parametrize(
    "query", ["limit=0", "offset=-1", "minimum_score=101", "provider=invalid", "status=invalid"]
)
def test_invalid_filters(client, query):
    assert client.get(f"/candidates?{query}").status_code == 422


def test_invalid_reviews_and_unknown_rights(client):
    candidate_id = discover(client)["candidate_ids"][0]
    url = f"/candidates/{candidate_id}"
    assert client.post(f"{url}/approve", json={}).status_code == 422
    assert (
        client.put(f"{url}/rights", json=ACTION | {"rights_status": "VERIFIED"}).status_code == 422
    )
    assert (
        client.put(f"{url}/rights", json=ACTION | {"rights_status": "UNKNOWN"}).status_code == 200
    )
    assert client.post(f"{url}/approve", json=ACTION).status_code == 409
    assert client.post("/discovery/search", json={"object_name": " "}).status_code == 422


def test_missing_keys_are_clear_and_do_not_block_health(engine):
    app = create_app(Settings(_env_file=None, pexels_api_key="", pixabay_api_key=""), engine)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        response = client.post("/discovery/search", json={"object_name": "screw"})
        assert response.status_code == 503
        assert "PEXELS_API_KEY" in response.json()["detail"]


def test_explicit_missing_provider_fails_before_search(client, providers):
    client.app.dependency_overrides[get_providers] = lambda: {
        ProviderName.PEXELS: providers[ProviderName.PEXELS]
    }
    response = client.post(
        "/discovery/search", json={"object_name": "screw", "providers": ["pexels", "pixabay"]}
    )
    assert response.status_code == 503
    assert "PIXABAY_API_KEY" in response.json()["detail"]
    assert client.get("/candidates").json()["total"] == 0


def test_partial_provider_failure_is_reported_and_stops_retry(client, providers):
    requests = []

    def unavailable(request):
        requests.append(request)
        return httpx.Response(429, text="SECRET")

    with httpx.Client(transport=httpx.MockTransport(unavailable)) as http:
        client.app.dependency_overrides[get_providers] = lambda: {
            ProviderName.PEXELS: providers[ProviderName.PEXELS],
            ProviderName.PIXABAY: PixabayProvider(http, "SECRET"),
        }
        result = discover(client, max_queries=3)
    assert len(requests) == 1
    assert result["created"] == 1
    assert result["duplicates"] == 2
    assert result["errors"][0]["provider"] == "pixabay"
    assert "SECRET" not in str(result)


def test_total_failure_has_502(client):
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))) as http:
        client.app.dependency_overrides[get_providers] = lambda: {
            ProviderName.PEXELS: PexelsProvider(http, "test")
        }
        response = client.post("/discovery/search", json={"object_name": "screw"})
    assert response.status_code == 502
    assert client.get("/candidates").json()["total"] == 0


def test_empty_search_is_success_and_cached(client):
    calls = []

    def empty(request):
        calls.append(request)
        return httpx.Response(200, json={"videos": []})

    with httpx.Client(transport=httpx.MockTransport(empty)) as http:
        client.app.dependency_overrides[get_providers] = lambda: {
            ProviderName.PEXELS: PexelsProvider(http, "test")
        }
        assert discover(client)["created"] == 0
        assert discover(client)["cache_hits"] == 1
    assert len(calls) == 1


def test_rediscovery_does_not_overwrite_rejection_or_manual_score(client):
    result = discover(client)
    url = f"/candidates/{result['candidate_ids'][0]}"
    client.post(
        f"{url}/score", json=ACTION | {"inputs": dict.fromkeys(ScoreInputs.model_fields, 90)}
    )
    client.post(f"{url}/reject", json=ACTION)
    discover(client)
    assert client.get(url).json()["status"] == "REJECTED"
    assert client.get(url).json()["total_score"] == 90
