from typing import Any
from urllib.parse import unquote, urlparse

from app.providers.base import HTTPVideoProvider, catalog_rights, normalize_video_id
from app.schemas.domain import NormalizedVideo, ProviderName


class PexelsProvider(HTTPVideoProvider):
    name = ProviderName.PEXELS
    endpoint = "https://api.pexels.com/v1/videos/search"
    results_key = "videos"

    def request_options(self, query: str, page: int, per_page: int) -> tuple[dict, dict]:
        return {"query": query, "page": page, "per_page": per_page, "locale": "en-US"}, {
            "Authorization": self.api_key
        }

    def normalize(self, raw: dict[str, Any]) -> NormalizedVideo:
        source = raw["url"]
        video_id = normalize_video_id(raw["id"])
        slug = unquote(urlparse(source).path).rstrip("/").rsplit("/", 1)[-1]
        title = slug.removesuffix(f"-{video_id}").replace("-", " ").strip()
        files = [
            f
            for f in raw.get("video_files", [])
            if f.get("link") and f.get("file_type") == "video/mp4"
        ]
        preview = min(
            files, key=lambda f: (f.get("width") or 0) * (f.get("height") or 0), default={}
        )
        author = raw.get("user") or {}
        return NormalizedVideo(
            provider=self.name,
            provider_video_id=video_id,
            source_url=source,
            preview_url=preview.get("link"),
            thumbnail_url=raw.get("image"),
            title=(title or f"Pexels video {video_id}")[:200],
            description="",
            duration=raw.get("duration"),
            width=raw.get("width") or None,
            height=raw.get("height") or None,
            author=author.get("name"),
            author_url=author.get("url"),
            rights=catalog_rights(self.name, source, author.get("name")),
        )
