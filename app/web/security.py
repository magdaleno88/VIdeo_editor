from __future__ import annotations

import secrets
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import Request

from app.core.errors import ApplicationError, NotFoundError


class CSRFError(ApplicationError):
    status_code = 403


async def form_data(request: Request) -> dict[str, str]:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/x-www-form-urlencoded":
        raise CSRFError("Dashboard forms require URL-encoded data")
    body = await request.body()
    if len(body) > 64 * 1024:
        raise CSRFError("Dashboard form is too large")
    values = parse_qs(body.decode("utf-8"), keep_blank_values=True, max_num_fields=100)
    return {key: items[-1] for key, items in values.items()}


def verify_csrf(request: Request, values: dict[str, str]) -> None:
    supplied = values.get("csrf_token", "")
    expected = request.app.state.dashboard_csrf_token
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise CSRFError("Invalid or expired dashboard form token")


def safe_redirect(value: str | None, fallback: str = "/") -> str:
    if value and value.startswith("/") and not value.startswith("//"):
        return value
    return fallback


def resolve_persisted_file(root_value: str, stored_path: str | None) -> Path:
    if not stored_path:
        raise NotFoundError("Media asset is not available")
    relative = Path(stored_path)
    if relative.is_absolute():
        raise NotFoundError("Media asset path is invalid")
    root = Path(root_value).expanduser().resolve()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise NotFoundError("Media asset path is invalid") from exc
    if not candidate.is_file():
        raise NotFoundError("Media asset file was not found")
    return candidate
