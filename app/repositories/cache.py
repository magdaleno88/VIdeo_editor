import hashlib
import json
from datetime import timedelta

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.core.database import utcnow
from app.models import SearchCache
from app.providers.base import ProviderPage
from app.schemas.domain import NormalizedVideo


class SearchCacheRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    @staticmethod
    def key(provider: str, query: str, page: int, per_page: int) -> str:
        raw = json.dumps(["normalized-v1", provider, query, page, per_page], ensure_ascii=False)
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, key: str) -> ProviderPage | None:
        entry = self.session.get(SearchCache, key)
        if entry is None or entry.expires_at <= utcnow():
            return None
        return ProviderPage(
            items=[NormalizedVideo.model_validate(item) for item in entry.payload["items"]],
            skipped=entry.payload["skipped"],
        )

    def put(self, key: str, page: ProviderPage) -> None:
        payload = {
            "items": [item.model_dump(mode="json", exclude={"orientation"}) for item in page.items],
            "skipped": page.skipped,
        }
        values = {"key": key, "payload": payload, "expires_at": utcnow() + timedelta(hours=24)}
        dialect = self.session.get_bind().dialect.name
        insert = sqlite_insert if dialect == "sqlite" else pg_insert
        statement = insert(SearchCache).values(**values)
        self.session.execute(
            statement.on_conflict_do_update(
                index_elements=[SearchCache.key],
                set_={k: v for k, v in values.items() if k != "key"},
            )
        )
        self.session.expire_all()
