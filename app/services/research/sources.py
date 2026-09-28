from urllib.parse import urlsplit

from app.schemas.domain import ResearchDocument, SearchResult, SourceTier, SourceType
from app.services.research.documents import FetchedDocument


class SourceRanker:
    tier_a_hosts = {"doi.org", "iso.org", "nist.gov", "osha.gov"}
    tier_b_hosts = {"asme.org", "astm.org", "sme.org", "worldsteel.org"}
    tier_d_hosts = {
        "facebook.com",
        "instagram.com",
        "quora.com",
        "reddit.com",
        "tiktok.com",
        "x.com",
        "youtube.com",
    }

    def classify(self, result: SearchResult) -> tuple[SourceTier, SourceType, float]:
        host = (urlsplit(str(result.url)).hostname or "").lower()
        if host in self.tier_d_hosts or any(
            host.endswith(f".{item}") for item in self.tier_d_hosts
        ):
            return SourceTier.TIER_D, SourceType.FORUM_SOCIAL, 0.2
        if (
            host in self.tier_a_hosts
            or host.endswith(".gov")
            or host.endswith(".edu")
            or host.endswith(".ac.uk")
        ):
            source_type = SourceType.GOVERNMENT if host.endswith(".gov") else SourceType.ACADEMIC
            if host in {"doi.org", "iso.org"}:
                source_type = SourceType.STANDARD
            return SourceTier.TIER_A, source_type, 0.95
        if host in self.tier_b_hosts or (
            host.endswith(".org")
            and any(
                word in result.title.lower() for word in ("association", "institute", "society")
            )
        ):
            return SourceTier.TIER_B, SourceType.INDUSTRY_ASSOCIATION, 0.8
        return SourceTier.TIER_C, SourceType.TECHNICAL_MEDIA, 0.6

    def rank(
        self, results: list[SearchResult]
    ) -> list[tuple[SearchResult, SourceTier, SourceType, float, float]]:
        unique: dict[str, SearchResult] = {}
        for result in results:
            unique.setdefault(str(result.url), result)
        ranked = []
        for result in unique.values():
            tier, source_type, credibility = self.classify(result)
            relevance = max(0.2, round(1 - (result.rank - 1) * 0.08, 2))
            ranked.append((result, tier, source_type, relevance, credibility))
        order = {
            SourceTier.TIER_A: 0,
            SourceTier.TIER_B: 1,
            SourceTier.TIER_C: 2,
            SourceTier.TIER_D: 3,
        }
        return sorted(ranked, key=lambda item: (order[item[1]], -item[3], str(item[0].url)))

    @staticmethod
    def document(
        result: SearchResult,
        fetched: FetchedDocument,
        tier: SourceTier,
        source_type: SourceType,
        relevance: float,
        credibility: float,
    ) -> ResearchDocument:
        host = (urlsplit(str(result.url)).hostname or "unknown").lower()
        return ResearchDocument(
            url=result.url,
            title=fetched.title or result.title,
            publisher=host,
            author=fetched.author,
            publication_date=fetched.publication_date,
            content=fetched.content,
            search_snippet=result.snippet,
            source_type=source_type,
            tier=tier,
            relevance=relevance,
            credibility=credibility,
        )
