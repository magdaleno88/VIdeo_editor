from abc import ABC, abstractmethod
from typing import Any, Protocol

import httpx
from pydantic import ValidationError

from app.core.errors import ProviderError
from app.schemas.domain import Contract, NormalizedVideo, ProviderName, RightsInfo, RightsStatus


class ProviderPage(Contract):
    items: list[NormalizedVideo]
    skipped: int = 0


class VideoSourceProvider(Protocol):
    name: ProviderName

    def search(self, query: str, *, page: int, per_page: int) -> ProviderPage: ...


class HTTPVideoProvider(ABC):
    name: ProviderName
    endpoint: str
    results_key: str

    def __init__(self, client: httpx.Client, api_key: str) -> None:
        self.client = client
        self.api_key = api_key

    def search(self, query: str, *, page: int, per_page: int) -> ProviderPage:
        params, headers = self.request_options(query, page, per_page)
        try:
            response = self.client.get(self.endpoint, params=params, headers=headers)
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            hints = {
                401: "Check the API key",
                403: "Access denied; check the API key and quota",
                429: "Rate limited; retry later",
            }
            # Deliberately exclude URL, response body and raw exception (may contain a key).
            raise ProviderError(
                f"{self.name}: HTTP {status}. {hints.get(status, 'Provider request failed')}"
            ) from None
        except httpx.RequestError:
            raise ProviderError(f"{self.name}: network error or timeout") from None
        except ValueError:
            raise ProviderError(f"{self.name}: invalid JSON response") from None
        if not isinstance(payload, dict) or not isinstance(payload.get(self.results_key), list):
            raise ProviderError(f"{self.name}: unexpected response schema")
        items = []
        skipped = 0
        for raw in payload[self.results_key]:
            try:
                items.append(self.normalize(raw))
            except (KeyError, TypeError, ValueError, AttributeError, ValidationError):
                skipped += 1
        return ProviderPage(items=items, skipped=skipped)

    @abstractmethod
    def request_options(self, query: str, page: int, per_page: int) -> tuple[dict, dict]: ...

    @abstractmethod
    def normalize(self, raw: dict[str, Any]) -> NormalizedVideo: ...


def normalize_video_id(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("Provider video ID must be a positive integer")
    return str(value)


def catalog_rights(provider: ProviderName, source: str, author: str | None) -> RightsInfo:
    license_name, license_url = {
        ProviderName.PEXELS: ("Pexels License", "https://www.pexels.com/license/"),
        ProviderName.PIXABAY: ("Pixabay Content License", "https://pixabay.com/service/terms/"),
    }[provider]
    return RightsInfo(
        source=source,
        creator=author,
        license_name=license_name,
        license_url=license_url,
        commercial_use_allowed=True,
        modification_allowed=True,
        attribution_required=False,
        attribution_text=f"Video by {author or 'unknown creator'} on {provider.value}: {source}",
        rights_status=RightsStatus.MANUAL_REVIEW_REQUIRED,
        notes=(
            "Catalog-level license information only; not verified for this clip or intended use. "
            "Review third-party rights, people, brands, property and API attribution requirements. "
            "Keep provider and creator credits with displayed search results."
        ),
    )
