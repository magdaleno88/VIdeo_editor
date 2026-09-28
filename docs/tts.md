# Phase 5: approved-script narration

Phase 5 produces a local audio asset only from a script whose human status is `APPROVED`. It does not edit video, mix audio, create subtitles, publish content or call FFmpeg.

## Input, provider and voice profile

The service orders `ScriptBeat.position` and `ScriptSentence.position`, trims each `spoken_text`, joins the complete narration with line breaks and sends that single text to the provider. Empty speech and text above `NARRATION_MAX_CHARACTERS` fail before any provider call.

The first adapter is `ElevenLabsTTSProvider`, using the official timestamp conversion endpoint with `output_format=mp3_44100_128`. The configured model, voice ID and supported voice settings are stored with the result. A `VoiceProfile` is created or reused by provider and provider voice ID; it records a local label, language, model, settings and enabled state. It does not claim ownership or permission for a third-party voice: that confirmation remains the operator's responsibility before selecting the voice.

Set these local environment values before generating real narration:

```dotenv
ELEVENLABS_API_KEY=
ELEVENLABS_MODEL_ID=
ELEVENLABS_DEFAULT_VOICE_ID=
NARRATION_STORAGE_ROOT=data/audio
```

The key is a `SecretStr`, is supplied as `xi-api-key` only to the provider, and is excluded from stored metadata and sanitized errors. No live request is made by tests or smoke checks.

## Asset validation, storage and timing

The provider response must contain non-empty MP3 audio. The service parses MP3 headers to validate format and calculate duration; WAV parsing exists for offline adapter tests. It rejects empty, malformed, unsupported and oversized audio. Each generated file is written to a unique relative path below `NARRATION_STORAGE_ROOT` using a temporary file, flush and atomic replacement. If persistence fails after writing, the temporary asset is deleted. The database stores the relative path, SHA-256 checksum, byte size, codec, sample rate, channels and duration rather than binary audio.

The duration is compared with the script's estimated narration duration. Within `NARRATION_DURATION_TOLERANCE` the asset is `VALIDATED`; a shorter or longer result becomes `NEEDS_REVIEW`. A provider failure, invalid audio or storage error yields an explicit error and no usable narration asset. A validated narration needs a separate human `APPROVED` or `REJECTED` review.

When ElevenLabs' character timing exactly matches the persisted source text, sentence and beat windows use provider alignment. If it is absent or inconsistent, the service estimates proportional windows and labels them `ESTIMATED_ALIGNMENT` with lower confidence. The response always exposes which method was used.

## Reuse and interfaces

The reuse identity hashes script ID, source text hash, voice ID, normalized settings and the narration implementation version. A matching existing asset is returned without calling the provider unless `force=true`. Different voice settings or a changed source text generate a new version.

Narration is an explicit, potentially billable provider action. Reuse avoids another provider request; `force=true` deliberately bypasses that protection. Operators should choose voices they are authorized to use and monitor the ElevenLabs account's current quota, pricing and terms before enabling production usage.

API routes:

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/scripts/{script_id}/narrations` | Generate or reuse narration from an approved script |
| `GET` | `/scripts/{script_id}/narrations` | List narration history |
| `GET` | `/narrations/{narration_id}` | Read metadata, timing, checksum and reviews |
| `POST` | `/narrations/{narration_id}/approve` | Approve a validated asset |
| `POST` | `/narrations/{narration_id}/reject` | Reject an asset |

Equivalent CLI commands are `candidate narrate`, `candidate narrations`, `narration show`, `narration approve` and `narration reject`. They emit JSON on success and keep errors on stderr.

The official provider behavior is documented in [ElevenLabs' timestamp API reference](https://elevenlabs.io/docs/api-reference/text-to-speech/convert-with-timestamps?explorer=true) and [text-to-speech overview](https://elevenlabs.io/docs/overview/capabilities/text-to-speech).
