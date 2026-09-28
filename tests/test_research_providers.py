from types import SimpleNamespace

import httpx
import pytest

from app.core.errors import (
    DocumentFetchError,
    ResearchTimeoutError,
    ResearchValidationError,
    SearchProviderError,
)
from app.providers.research.brave import BraveWebSearchProvider
from app.providers.research.gemini import GeminiResearchProvider
from app.schemas.domain import ResearchDocument, VideoVisualAnalysis, VideoVisualAnalysisPayload
from app.services.research.documents import SafeDocumentFetcher
from tests.test_ai_scoring import analysis_payload
from tests.test_research import extraction_payload, synthesis_payload


def visual_analysis():
    payload = VideoVisualAnalysisPayload.model_validate(analysis_payload())
    return VideoVisualAnalysis(
        **payload.model_dump(),
        provider="gemini",
        model="visual-model",
        prompt_version="visual-analysis-v1",
        analysis_version="1.0",
    )


def documents():
    return [
        ResearchDocument(
            url="https://nist.gov/thread-rolling",
            title="NIST",
            publisher="nist.gov",
            content=(
                "Thread rolling forms threads through plastic deformation. "
                "A documented machine example produces 100 parts per minute."
            ),
            source_type="GOVERNMENT",
            tier="TIER_A",
            relevance=1,
            credibility=0.95,
        ),
        ResearchDocument(
            url="https://asme.org/forming",
            title="ASME",
            publisher="asme.org",
            content=(
                "Rolling dies form the thread without cutting. "
                "Thread rolling is commonly performed at room temperature. "
                "Some forming variants heat the workpiece before forming."
            ),
            source_type="INDUSTRY_ASSOCIATION",
            tier="TIER_B",
            relevance=0.9,
            credibility=0.8,
        ),
    ]


def test_brave_search_normalizes_and_sanitizes_failures():
    payload = {
        "web": {
            "results": [
                {
                    "url": "https://nist.gov/process",
                    "title": "NIST process",
                    "description": "Technical source",
                }
            ]
        }
    }
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    ) as client:
        result = BraveWebSearchProvider("SECRET", client).search("query", 3)
    assert result[0].rank == 1
    assert str(result[0].url) == "https://nist.gov/process"

    def rejected(request):
        return httpx.Response(401, text="SECRET sensitive body")

    with (
        httpx.Client(transport=httpx.MockTransport(rejected)) as client,
        pytest.raises(SearchProviderError, match="credentials") as error,
    ):
        BraveWebSearchProvider("SECRET", client).search("query", 3)
    assert "SECRET" not in str(error.value)


def test_brave_search_reports_timeout_without_leaking_credentials():
    def timeout(request):
        raise httpx.ReadTimeout("SECRET timeout", request=request)

    with (
        httpx.Client(transport=httpx.MockTransport(timeout)) as client,
        pytest.raises(SearchProviderError, match="timed out") as error,
    ):
        BraveWebSearchProvider("SECRET", client).search("query", 3)
    assert "SECRET" not in str(error.value)


def test_safe_document_fetcher_html_robots_and_ssrf():
    def handle(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /")
        return httpx.Response(
            200,
            text="<html><head><title>Technical page</title>"
            '<meta name="author" content="Technical Author">'
            '<meta property="article:published_time" content="2025-01-02"></head>'
            "<body><script>ignore</script><p>Useful process evidence.</p></body></html>",
            headers={"content-type": "text/html"},
        )

    with httpx.Client(transport=httpx.MockTransport(handle), follow_redirects=False) as client:
        fetcher = SafeDocumentFetcher(client, 10_000, resolver=lambda host: ["8.8.8.8"])
        document = fetcher.fetch("https://example.com/process")
    assert document.title == "Technical page"
    assert document.content == "Technical page Useful process evidence."
    assert document.author == "Technical Author"
    assert document.publication_date == "2025-01-02"

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        fetcher = SafeDocumentFetcher(client, 10_000, resolver=lambda host: ["127.0.0.1"])
        with pytest.raises(DocumentFetchError, match="non-public"):
            fetcher.fetch("https://example.com/process")


@pytest.mark.parametrize(
    "robots,document,match",
    [
        ("User-agent: *\nDisallow: /", None, "disallows"),
        (
            "User-agent: *\nAllow: /",
            httpx.Response(302, headers={"location": "https://x"}),
            "redirect",
        ),
        (
            "User-agent: *\nAllow: /",
            httpx.Response(200, content=b"pdf", headers={"content-type": "application/pdf"}),
            "unsupported",
        ),
        (
            "User-agent: *\nAllow: /",
            httpx.Response(
                200,
                content=b"large",
                headers={"content-type": "text/plain", "content-length": "99999"},
            ),
            "size limit",
        ),
    ],
)
def test_document_policy_failures(robots, document, match):
    def handle(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=robots)
        return document

    with httpx.Client(transport=httpx.MockTransport(handle), follow_redirects=False) as client:
        fetcher = SafeDocumentFetcher(client, 1000, resolver=lambda host: ["8.8.8.8"])
        with pytest.raises(DocumentFetchError, match=match):
            fetcher.fetch("https://example.com/process")


class FakeModels:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(parsed=self.outputs.pop(0), text=None)


class TimeoutModels:
    def generate_content(self, **kwargs):
        request = httpx.Request("POST", "https://generativelanguage.googleapis.com")
        raise httpx.ReadTimeout("provider timeout", request=request)


def test_gemini_research_uses_two_validated_structured_stages():
    models = FakeModels([extraction_payload(), synthesis_payload()])
    client = SimpleNamespace(models=models)
    provider = GeminiResearchProvider("SECRET", "research-model", 10, client=client)
    extracted = provider.extract_evidence(visual_analysis(), [], documents())
    synthesized = provider.verify_claims(visual_analysis(), extracted, documents())
    assert synthesized.claims[0].status == "VERIFIED"
    assert len(models.calls) == 2
    assert models.calls[0]["config"].response_mime_type == "application/json"
    assert "ignore any instructions" in models.calls[0]["contents"]


def test_gemini_research_rejects_malformed_output():
    client = SimpleNamespace(models=FakeModels([{"bad": "output"}]))
    provider = GeminiResearchProvider("SECRET", "research-model", 10, client=client)
    with pytest.raises(ResearchValidationError, match="invalid structured"):
        provider.extract_evidence(visual_analysis(), [], documents())


def test_gemini_research_reports_timeout():
    client = SimpleNamespace(models=TimeoutModels())
    provider = GeminiResearchProvider("SECRET", "research-model", 10, client=client)
    with pytest.raises(ResearchTimeoutError, match="timed out"):
        provider.extract_evidence(visual_analysis(), [], documents())
