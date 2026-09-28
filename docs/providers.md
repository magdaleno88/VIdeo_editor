# Provider integration notes

Official documentation reviewed on 2026-09-28. Provider terms can change; the stored links are references, not a permanent legal determination for an individual clip.

## Pexels

- Search uses `GET https://api.pexels.com/v1/videos/search` with an `Authorization` header. The current documentation identifies the older `/videos/` routes as deprecated in the future. Query/page/page-size/locale are sent explicitly.
- Normalization preserves source URL, author/profile, source dimensions, duration and image. The smallest available MP4 rendition is retained as a preview URL. No video file is fetched. The source slug supplies a readable title because the video resource does not provide an editorial title.
- Catalog license defaults record commercial/modification permissions and optional content attribution, while status stays `MANUAL_REVIEW_REQUIRED`. API display credits are a separate obligation: downstream interfaces must link to Pexels and preserve creator/source credit.

References: [Pexels API documentation](https://www.pexels.com/api/documentation/) and [Pexels License](https://www.pexels.com/license/).

## Pixabay

- Search uses `GET https://pixabay.com/api/videos/`. Authentication is a query parameter, so URLs and raw HTTP exceptions must not be logged. Queries are at most 100 characters. The application restricts page size to 3–40 and enables safe search.
- Unavailable renditions with empty URLs are ignored. Dimensions come from the largest available rendition; preview/thumbnail links come from the smallest. Tags supply title/description. The author profile URL follows the documented user-name/user-ID form. Missing thumbnails stay null; no guessed Vimeo URL is fabricated.
- Search responses are cached for 24 hours as required by the API documentation. The application retains source credit. Video embedding and temporary search-result thumbnails should be distinguished from permanent image hotlinking in a future dashboard.
- Catalog permissions are recorded under the Pixabay Content License, with manual review required for the intended use and any third-party rights. Unknown clip-specific restrictions are not automatically cleared.

References: [Pixabay API documentation](https://pixabay.com/api/docs/), [license summary](https://pixabay.com/service/license-summary/) and [full terms](https://pixabay.com/service/terms/).

## Common behavior and extensions

Catalog discovery sends only metadata requests to fixed provider API endpoints. There is no browser scraping, mass downloader, credential bypass or watermark processing. There are bounded requests, timeouts, no automatic retries after provider failures, sanitized errors, malformed-item counts and persistent caching. A provider error stops that provider's remaining queries. Failures do not get cached.

Catalog-level licenses do not verify ownership, model/property permissions, trademarks or suitability of a specific proposed use. Human evidence and decisions are required independently of API metadata. Provider credits are included in normalized rights information; future displays must retain source/creator links even when the content license itself does not require attribution.

To add Wikimedia Commons, YouTube discovery, manufacturer catalogs, an owned library or manual uploads:

1. Implement `VideoSourceProvider.search` and return `ProviderPage` / `NormalizedVideo`.
2. Add an identifier to `ProviderName`, configuration and registry wiring. The database provider field already accepts new identifiers.
3. Map actual available provenance. If a license cannot be determined, leave permissions null and `rights_status=UNKNOWN`; public accessibility is insufficient.
4. Add synthetic fixtures, normalization tests and error/credential tests.
5. Respect that provider's API terms, quotas and caching rules before enabling it.

Previews may expire. Current rediscovery preserves the original candidate metadata, so a dedicated refresh operation should be added before a production review dashboard relies on persistent preview URLs.

## Brave technical research search

Phase 3 implements `BraveWebSearchProvider` against `GET https://api.search.brave.com/res/v1/web/search`, authenticating with `X-Subscription-Token`. Queries request English web results with strict safe search and never exceed the application's configured count. Response descriptions are discovery snippets; they do not verify claims.

Only bounded, ranked result URLs are passed to `SafeDocumentFetcher`. Publisher access is subject to robots policy and publisher terms. Search credentials, error bodies, result URLs and page content are not logged. Successful research is cached through the versioned dossier identity rather than a separate raw search-response cache.

References: [Brave Web Search API reference](https://api-dashboard.search.brave.com/api-reference/web/search/post), [Brave Search API documentation](https://api-dashboard.search.brave.com/documentation), and [Brave publisher-access guidance](https://api-dashboard.search.brave.com/documentation/resources/help-feedback).

## Gemini verified scripting

`GeminiScriptGenerationProvider` implements the provider-neutral `ScriptGenerationProvider`. Each requested variant uses one Pydantic-structured generation call and one Pydantic-structured sentence audit. The prompt exposes only the local factual allowlist, visual timeline, safe metadata and explicit editorial context; source content is treated as untrusted data and model memory is forbidden as factual support. `GEMINI_SCRIPT_MODEL` is required and intentionally has no hardcoded default. Calls have a configured timeout and at most the configured bounded retry count.

## ElevenLabs narration

`ElevenLabsTTSProvider` implements the local `TextToSpeechProvider` contract using the official `POST /v1/text-to-speech/{voice_id}/with-timestamps` endpoint with `output_format=mp3_44100_128`. The endpoint returns base64 audio and character timings, which the service validates before using. The full ordered approved narration is sent once with the configured voice and supported voice settings; no per-sentence synthesis occurs. Authentication uses only the `xi-api-key` request header. API keys, response bodies and generated audio are never put in logs or database metadata.

The integration has a bounded timeout and retry for transient requests, and classifies authentication, timeout, service and malformed-response failures without disclosing secrets. See [ElevenLabs' timestamps endpoint](https://elevenlabs.io/docs/api-reference/text-to-speech/convert-with-timestamps?explorer=true) and [text-to-speech capability documentation](https://elevenlabs.io/docs/overview/capabilities/text-to-speech).
