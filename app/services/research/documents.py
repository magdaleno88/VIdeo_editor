import ipaddress
import socket
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from app.core.errors import DocumentFetchError

USER_AGENT = "IndustrialContentFactoryResearch/0.3"
ALLOWED_DOCUMENT_TYPES = {"text/html", "application/xhtml+xml", "text/plain"}


@dataclass(frozen=True)
class FetchedDocument:
    content: str
    title: str | None = None
    author: str | None = None
    publication_date: str | None = None


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self.ignored_depth = 0
        self.in_title = False
        self.author: str | None = None
        self.publication_date: str | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self.ignored_depth += 1
        if tag == "title":
            self.in_title = True
        if tag == "meta":
            values = {key.lower(): value for key, value in attrs if key and value}
            key = (values.get("name") or values.get("property") or "").lower()
            content = " ".join(values.get("content", "").split())
            if key in {"author", "article:author"} and content:
                self.author = content[:300]
            if key in {"date", "datepublished", "article:published_time"} and content:
                self.publication_date = content[:80]

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self.ignored_depth:
            self.ignored_depth -= 1
        if tag == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.ignored_depth:
            return
        clean = " ".join(data.split())
        if clean:
            self.parts.append(clean)
            if self.in_title:
                self.title_parts.append(clean)

    def result(self, limit: int = 20_000) -> FetchedDocument:
        text = " ".join(self.parts)[:limit].strip()
        title = " ".join(self.title_parts)[:500].strip() or None
        return FetchedDocument(text, title, self.author, self.publication_date)


def system_resolver(host: str) -> list[str]:
    return sorted({item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})


class SafeDocumentFetcher:
    def __init__(
        self,
        client: httpx.Client,
        max_bytes: int,
        resolver: Callable[[str], list[str]] = system_resolver,
    ) -> None:
        self.client = client
        self.max_bytes = max_bytes
        self.resolver = resolver
        self.robots_cache: dict[str, RobotFileParser] = {}

    def fetch(self, url: str) -> FetchedDocument:
        parsed = self._validate_url(url)
        self._check_robots(parsed, url)
        body, content_type = self._download(url, self.max_bytes)
        if content_type not in ALLOWED_DOCUMENT_TYPES:
            raise DocumentFetchError("Research source has an unsupported content type")
        if content_type == "text/plain":
            text = " ".join(body.decode("utf-8", errors="replace").split())[:20_000]
            if not text:
                raise DocumentFetchError("Research source contained no usable text")
            return FetchedDocument(text)
        parser = TextExtractor()
        try:
            parser.feed(body.decode("utf-8", errors="replace"))
        except Exception:
            raise DocumentFetchError("Research source could not be parsed") from None
        document = parser.result()
        if not document.content:
            raise DocumentFetchError("Research source contained no usable text")
        return document

    def _validate_url(self, url: str):
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError:
            raise DocumentFetchError("Research source URL failed the safety policy") from None
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or port not in (None, 443)
        ):
            raise DocumentFetchError("Research source URL failed the HTTPS safety policy")
        try:
            addresses = self.resolver(parsed.hostname)
        except OSError:
            raise DocumentFetchError("Research source hostname could not be resolved") from None
        if not addresses:
            raise DocumentFetchError("Research source hostname could not be resolved")
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if not ip.is_global:
                raise DocumentFetchError("Research source resolved to a non-public address")
        return parsed

    def _check_robots(self, parsed, url: str) -> None:
        origin = f"https://{parsed.hostname}"
        policy = self.robots_cache.get(origin)
        if policy is None:
            robots_url = f"{origin}/robots.txt"
            body, content_type = self._download(
                robots_url, min(self.max_bytes, 65_536), allow_missing=True
            )
            policy = RobotFileParser()
            policy.set_url(robots_url)
            if body and content_type in {"text/plain", "text/html"}:
                policy.parse(body.decode("utf-8", errors="replace").splitlines())
            else:
                policy.parse([])
            self.robots_cache[origin] = policy
        if not policy.can_fetch(USER_AGENT, url):
            raise DocumentFetchError("Research source disallows this fetch in robots.txt")

    def _download(self, url: str, limit: int, *, allow_missing: bool = False) -> tuple[bytes, str]:
        try:
            with self.client.stream("GET", url, headers={"User-Agent": USER_AGENT}) as response:
                if allow_missing and response.status_code in (404, 410):
                    return b"", "text/plain"
                if response.is_redirect:
                    raise DocumentFetchError("Research source redirects are not followed")
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError:
                    raise DocumentFetchError(
                        f"Research source returned HTTP {response.status_code}"
                    ) from None
                length = response.headers.get("content-length")
                if length and int(length) > limit:
                    raise DocumentFetchError("Research source exceeds the configured size limit")
                content_type = (
                    response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                )
                chunks = []
                total = 0
                for chunk in response.iter_bytes(64 * 1024):
                    total += len(chunk)
                    if total > limit:
                        raise DocumentFetchError(
                            "Research source exceeds the configured size limit"
                        )
                    chunks.append(chunk)
                return b"".join(chunks), content_type
        except (httpx.TimeoutException, httpx.RequestError):
            raise DocumentFetchError("Research source fetch timed out or failed") from None
        except ValueError:
            raise DocumentFetchError("Research source returned an invalid content length") from None
