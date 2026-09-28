import json
import socket
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.api.dependencies import get_providers
from app.core.config import Settings
from app.core.database import build_engine, session_factory
from app.main import create_app
from app.providers.pexels import PexelsProvider
from app.providers.pixabay import PixabayProvider
from app.schemas.domain import ProviderName

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def no_external_network(monkeypatch):
    original_connect = socket.socket.connect

    def blocked(connection, address):
        # Windows asyncio uses a loopback socket pair internally.
        if isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1"):
            return original_connect(connection, address)
        raise AssertionError("External network access is forbidden in tests")

    monkeypatch.setattr(socket.socket, "connect", blocked)


@pytest.fixture
def payloads():
    return {
        name: json.loads((ROOT / "tests" / "fixtures" / f"{name}.json").read_text())
        for name in ("pexels", "pixabay")
    }


def migrate(engine):
    config = Config(str(ROOT / "alembic.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")


@pytest.fixture
def engine():
    database = build_engine("sqlite:///:memory:")
    migrate(database)
    yield database
    database.dispose()


@pytest.fixture
def session(engine):
    with session_factory(engine)() as session:
        yield session
        session.rollback()


@pytest.fixture
def providers(payloads):
    def handle(request):
        name = "pexels" if request.url.host == "api.pexels.com" else "pixabay"
        return httpx.Response(200, json=payloads[name])

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        yield {
            ProviderName.PEXELS: PexelsProvider(client, "test-key-pexels"),
            ProviderName.PIXABAY: PixabayProvider(client, "test-key-pixabay"),
        }


@pytest.fixture
def video(providers, payloads):
    return providers[ProviderName.PEXELS].normalize(payloads["pexels"]["videos"][0])


@pytest.fixture
def client(engine, providers):
    app = create_app(Settings(_env_file=None, pexels_api_key="", pixabay_api_key=""), engine)
    app.dependency_overrides[get_providers] = lambda: providers
    with TestClient(app) as client:
        yield client


@pytest.fixture
def rights_review():
    return {
        "reviewer": "Test reviewer",
        "notes": "Fixture only; permission checked for this test.",
        "rights_status": "VERIFIED",
        "license_name": "Pexels License",
        "license_url": "https://www.pexels.com/license/",
        "commercial_use_allowed": True,
        "modification_allowed": True,
        "attribution_required": False,
        "evidence_url": "https://example.org/permission-record",
    }
