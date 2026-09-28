from collections.abc import Iterator
from contextlib import contextmanager

import httpx

from app.core.config import Settings
from app.core.errors import ConfigurationError
from app.providers.base import VideoSourceProvider
from app.providers.pexels import PexelsProvider
from app.providers.pixabay import PixabayProvider
from app.schemas.domain import ProviderName


@contextmanager
def configured_providers(settings: Settings) -> Iterator[dict[ProviderName, VideoSourceProvider]]:
    with httpx.Client(timeout=settings.http_timeout_seconds, follow_redirects=False) as client:
        providers: dict[ProviderName, VideoSourceProvider] = {}
        for name, adapter, key in (
            (ProviderName.PEXELS, PexelsProvider, settings.pexels_api_key),
            (ProviderName.PIXABAY, PixabayProvider, settings.pixabay_api_key),
        ):
            value = key.get_secret_value().strip()
            if value:
                providers[name] = adapter(client, value)
        yield providers


def select_providers(
    available: dict[ProviderName, VideoSourceProvider],
    selected: list[ProviderName] | None,
) -> list[VideoSourceProvider]:
    names = list(dict.fromkeys(selected or available))
    if not names:
        raise ConfigurationError(
            "Set PEXELS_API_KEY and/or PIXABAY_API_KEY in .env before discovery"
        )
    missing = [name.value.upper() + "_API_KEY" for name in names if name not in available]
    if missing:
        raise ConfigurationError("Missing provider credentials: " + ", ".join(missing))
    return [available[name] for name in names]
