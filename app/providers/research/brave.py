import httpx

from app.core.errors import SearchProviderError
from app.schemas.domain import SearchResult


class BraveWebSearchProvider:
    name = "brave"
    endpoint = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_key: str, client: httpx.Client) -> None:
        self.api_key = api_key
        self.client = client

    def search(self, query: str, count: int) -> list[SearchResult]:
        try:
            response = self.client.get(
                self.endpoint,
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": self.api_key,
                },
                params={
                    "q": query,
                    "count": min(count, 20),
                    "country": "US",
                    "search_lang": "en",
                    "safesearch": "strict",
                },
            )
            response.raise_for_status()
            payload = response.json()
            results = payload.get("web", {}).get("results", [])
            if not isinstance(results, list):
                raise ValueError
            normalized = []
            for rank, item in enumerate(results, 1):
                if not isinstance(item, dict) or not item.get("url") or not item.get("title"):
                    continue
                normalized.append(
                    SearchResult(
                        url=item["url"],
                        title=item["title"],
                        snippet=item.get("description", ""),
                        rank=rank,
                    )
                )
            return normalized
        except (httpx.TimeoutException, httpx.RequestError):
            raise SearchProviderError("Web search timed out or failed") from None
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in (401, 403):
                raise SearchProviderError("Web search credentials were rejected") from None
            if status == 429:
                raise SearchProviderError("Web search quota or rate limit was reached") from None
            raise SearchProviderError(f"Web search returned HTTP {status}") from None
        except (ValueError, TypeError):
            raise SearchProviderError("Web search returned an invalid response") from None
