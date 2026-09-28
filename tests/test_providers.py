import httpx
import pytest

from app.core.errors import ProviderError
from app.providers.base import normalize_video_id
from app.providers.pexels import PexelsProvider
from app.providers.pixabay import PixabayProvider
from app.schemas.domain import Orientation, ProviderName, RightsStatus


def test_pexels_normalization(video):
    assert video.provider_video_id == "101"
    assert video.title == "industrial machine cutting metal"
    assert str(video.preview_url) == "https://example.org/fixture-preview.mp4"
    assert video.orientation == Orientation.LANDSCAPE
    assert video.author == "Fixture Creator"
    assert video.rights.rights_status == RightsStatus.MANUAL_REVIEW_REQUIRED
    assert video.rights.verification_date is None
    assert video.rights.source == video.source_url


def test_pixabay_normalization(providers, payloads):
    video = providers[ProviderName.PIXABAY].normalize(payloads["pixabay"]["hits"][0])
    assert video.orientation == Orientation.PORTRAIT
    assert video.width == 1080
    assert str(video.preview_url) == "https://example.org/fixture-tiny.mp4"
    assert str(video.thumbnail_url) == "https://example.org/fixture-tiny.jpg"
    assert "Fixture%20Creator-3" in str(video.author_url)


@pytest.mark.parametrize(
    "provider_class,name,key,expected_path",
    [
        (PexelsProvider, "pexels", "query", "/v1/videos/search"),
        (PixabayProvider, "pixabay", "q", "/api/videos/"),
    ],
)
def test_search_request_contract(provider_class, name, key, expected_path, payloads):
    def handle(request):
        assert request.url.path == expected_path
        assert request.url.params[key] == "factory press"
        assert request.url.params["page"] == "2"
        assert request.url.params["per_page"] == "5"
        if name == "pexels":
            assert request.headers["Authorization"] == "fixture-key"
        else:
            assert request.url.params["key"] == "fixture-key"
        return httpx.Response(200, json=payloads[name])

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        page = provider_class(client, "fixture-key").search("factory press", page=2, per_page=5)
        assert len(page.items) == 1


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_http_error_never_exposes_secrets(status):
    with (
        httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(status, text="SECRET"))
        ) as client,
        pytest.raises(ProviderError) as error,
    ):
        PixabayProvider(client, "SECRET").search("press", page=1, per_page=3)
    assert "SECRET" not in str(error.value)
    assert str(status) in str(error.value)


@pytest.mark.parametrize("payload", [{}, {"hits": {}}, ["wrong"]])
def test_bad_envelope_is_failure(payload):
    with (
        httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
        ) as client,
        pytest.raises(ProviderError, match="schema"),
    ):
        PixabayProvider(client, "test").search("press", page=1, per_page=3)


def test_malformed_item_does_not_discard_good_records(payloads):
    payload = payloads["pexels"]
    payload["videos"].extend([{}, {"id": 0, "url": "javascript:alert(1)"}])
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
    ) as client:
        page = PexelsProvider(client, "test").search("press", page=1, per_page=3)
        assert len(page.items) == 1
        assert page.skipped == 2


def test_missing_optional_metadata(providers):
    video = providers[ProviderName.PEXELS].normalize(
        {"id": 5, "url": "https://www.pexels.com/video/5/"}
    )
    assert video.preview_url is None
    assert video.orientation == Orientation.UNKNOWN
    assert video.author is None


def test_timeout_and_bad_json():
    def timeout(request):
        raise httpx.ReadTimeout("https://example.org?key=SECRET")

    for handler in (timeout, lambda r: httpx.Response(200, text="not-json")):
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ProviderError) as error:
                PixabayProvider(client, "SECRET").search("press", page=1, per_page=3)
            assert "SECRET" not in str(error.value)


@pytest.mark.parametrize("value", [None, True, 0, -1, "not-an-id"])
def test_invalid_provider_ids_are_rejected(value):
    with pytest.raises(ValueError):
        normalize_video_id(value)
