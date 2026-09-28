from typing import Any
from urllib.parse import quote

from app.providers.base import HTTPVideoProvider, catalog_rights, normalize_video_id
from app.schemas.domain import NormalizedVideo, ProviderName


class PixabayProvider(HTTPVideoProvider):
    name = ProviderName.PIXABAY
    endpoint = "https://pixabay.com/api/videos/"
    results_key = "hits"

    def request_options(self, query: str, page: int, per_page: int) -> tuple[dict, dict]:
        return {
            "key": self.api_key,
            "q": query,
            "lang": "en",
            "page": page,
            "per_page": per_page,
            "safesearch": "true",
        }, {}

    def normalize(self, raw: dict[str, Any]) -> NormalizedVideo:
        source = raw["pageURL"]
        video_id = normalize_video_id(raw["id"])
        files = [f for f in raw.get("videos", {}).values() if f.get("url")]

        def resolution(file: dict[str, Any]) -> int:
            return (file.get("width") or 0) * (file.get("height") or 0)

        preview = min(files, key=resolution, default={})
        largest = max(files, key=resolution, default={})
        author = raw.get("user") or None
        user_id = raw.get("user_id")
        author_url = (
            f"https://pixabay.com/users/{quote(author, safe='')}-{user_id}/"
            if author and user_id
            else None
        )
        tags = raw.get("tags") or ""
        return NormalizedVideo(
            provider=self.name,
            provider_video_id=video_id,
            source_url=source,
            preview_url=preview.get("url"),
            thumbnail_url=preview.get("thumbnail"),
            title=(tags or f"Pixabay video {video_id}")[:200],
            description=tags,
            duration=raw.get("duration"),
            width=largest.get("width") or None,
            height=largest.get("height") or None,
            author=author,
            author_url=author_url,
            rights=catalog_rights(self.name, source, author),
        )
