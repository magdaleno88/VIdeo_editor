import httpx
import pytest

from app.core.errors import AssetTooLargeError, AssetUnavailableError, UnsupportedMediaError
from app.repositories.candidates import CandidateRepository
from app.schemas.domain import Idea, NormalizedVideo
from app.services.video.assets import VideoAssetFetcher

MP4 = b"\x00\x00\x00\x18ftypmp42" + b"video-data"


def candidate(session, video, **updates):
    video = NormalizedVideo.model_validate(
        video.model_dump(exclude={"orientation"})
        | {"preview_url": "https://videos.pexels.com/video.mp4", **updates}
    )
    item, _ = CandidateRepository(session).add_if_new(video, "screw", Idea(object_name="screw"))
    session.flush()
    return item


def fetcher(response, tmp_path, *, max_size=1000):
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: response), follow_redirects=False
    )
    return client, VideoAssetFetcher(client, max_size, 30, tmp_path)


def test_asset_success_and_cleanup(session, video, tmp_path):
    item = candidate(session, video)
    client, service = fetcher(
        httpx.Response(200, content=MP4, headers={"content-type": "video/mp4"}), tmp_path
    )
    with client, service.fetch(item) as asset:
        path = asset.path
        assert path.exists()
        assert path.parent == tmp_path
        assert asset.size_bytes == len(MP4)
    assert not path.exists()


@pytest.mark.parametrize("status", [401, 403, 404, 410])
def test_expired_asset(session, video, tmp_path, status):
    item = candidate(session, video)
    client, service = fetcher(httpx.Response(status), tmp_path)
    with (
        client,
        pytest.raises(AssetUnavailableError, match="expired"),
        service.fetch(item),
    ):
        pass
    assert list(tmp_path.iterdir()) == []


def test_oversized_content_length_and_stream(session, video, tmp_path):
    item = candidate(session, video)
    for response in (
        httpx.Response(
            200, content=MP4, headers={"content-type": "video/mp4", "content-length": "999"}
        ),
        httpx.Response(200, content=MP4, headers={"content-type": "video/mp4"}),
    ):
        client, service = fetcher(response, tmp_path, max_size=8)
        with client, pytest.raises(AssetTooLargeError), service.fetch(item):
            pass
        assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "content_type,body",
    [
        ("text/html", b"<html>login</html>"),
        ("video/mp4", b"not-video"),
        ("video/webm", MP4),
    ],
)
def test_invalid_mime_or_signature(session, video, tmp_path, content_type, body):
    item = candidate(session, video)
    client, service = fetcher(
        httpx.Response(200, content=body, headers={"content-type": content_type}), tmp_path
    )
    with client, pytest.raises(UnsupportedMediaError), service.fetch(item):
        pass
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "url",
    [
        "http://videos.pexels.com/video.mp4",
        "https://127.0.0.1/video.mp4",
        "https://user:secret@videos.pexels.com/video.mp4",
        "https://videos.pexels.com:444/video.mp4",
        "https://cdn.pixabay.com/video.mp4",
    ],
)
def test_ssrf_and_provider_host_policy(session, video, tmp_path, url):
    item = candidate(session, video, preview_url=url)
    client, service = fetcher(
        httpx.Response(200, content=MP4, headers={"content-type": "video/mp4"}), tmp_path
    )
    with client, pytest.raises(AssetUnavailableError, match="policy|host"), service.fetch(item):
        pass


def test_duration_and_missing_preview_are_blocked_before_http(session, video, tmp_path):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, content=MP4, headers={"content-type": "video/mp4"})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        service = VideoAssetFetcher(client, 1000, 10, tmp_path)
        with (
            pytest.raises(AssetTooLargeError, match="duration"),
            service.fetch(candidate(session, video, duration=11)),
        ):
            pass
        missing = candidate(session, video, provider_video_id="missing", preview_url=None)
        with (
            pytest.raises(AssetUnavailableError, match="no temporary"),
            service.fetch(missing),
        ):
            pass
    assert calls == []


def test_timeout_cleanup(session, video, tmp_path):
    item = candidate(session, video)

    def timeout(request):
        raise httpx.ReadTimeout("sensitive-url")

    with httpx.Client(transport=httpx.MockTransport(timeout)) as client:
        service = VideoAssetFetcher(client, 1000, 30, tmp_path)
        with pytest.raises(AssetUnavailableError, match="timed out"), service.fetch(item):
            pass
    assert list(tmp_path.iterdir()) == []


def test_consumer_error_is_preserved_and_asset_is_cleaned(session, video, tmp_path):
    item = candidate(session, video)
    client, service = fetcher(
        httpx.Response(200, content=MP4, headers={"content-type": "video/mp4"}), tmp_path
    )
    with (
        client,
        pytest.raises(RuntimeError, match="analysis failed"),
        service.fetch(item) as asset,
    ):
        path = asset.path
        raise RuntimeError("analysis failed")
    assert not path.exists()
