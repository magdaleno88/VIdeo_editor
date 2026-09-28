from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ScoringWeights(BaseModel):
    model_config = ConfigDict(extra="forbid")
    movement: float = Field(20, ge=0, le=100)
    visible_transformation: float = Field(20, ge=0, le=100)
    satisfying_result: float = Field(15, ge=0, le=100)
    unusual_machinery: float = Field(15, ge=0, le=100)
    understandable_without_audio: float = Field(10, ge=0, le=100)
    visual_hook: float = Field(10, ge=0, le=100)
    educational_potential: float = Field(5, ge=0, le=100)
    loop_potential: float = Field(5, ge=0, le=100)

    @model_validator(mode="after")
    def validate_total(self) -> "ScoringWeights":
        if abs(sum(self.model_dump().values()) - 100) > 0.0001:
            raise ValueError("Scoring weights must sum to 100")
        return self


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", env_nested_delimiter="__", extra="ignore"
    )
    database_url: str = "sqlite:///./data/industrial_content_factory.db"
    pexels_api_key: SecretStr = SecretStr("")
    pixabay_api_key: SecretStr = SecretStr("")
    gemini_api_key: SecretStr = SecretStr("")
    gemini_video_model: str = ""
    http_timeout_seconds: float = Field(15, gt=0, le=60)
    ai_video_max_duration_seconds: float = Field(90, gt=0, le=600)
    ai_video_max_file_size_mb: int = Field(40, ge=1, le=100)
    ai_video_analysis_timeout_seconds: float = Field(180, gt=0, le=600)
    brave_search_api_key: SecretStr = SecretStr("")
    research_search_provider: Literal["brave"] = "brave"
    gemini_research_model: str = ""
    research_max_search_queries: int = Field(5, ge=1, le=8)
    research_max_sources: int = Field(8, ge=2, le=20)
    research_max_pages: int = Field(5, ge=2, le=10)
    research_min_sources: int = Field(2, ge=1, le=5)
    research_max_document_bytes: int = Field(500_000, ge=10_000, le=5_000_000)
    research_http_timeout_seconds: float = Field(15, gt=0, le=60)
    research_max_llm_calls: int = Field(2, ge=2, le=6)
    gemini_script_model: str = ""
    script_max_variants: int = Field(3, ge=1, le=3)
    script_max_claims: int = Field(20, ge=1, le=50)
    script_max_context_characters: int = Field(20_000, ge=2_000, le=100_000)
    script_speaking_rate_wpm: float = Field(150, ge=100, le=220)
    script_duration_tolerance: float = Field(0.25, ge=0.1, le=0.5)
    script_allow_partially_supported: bool = False
    script_timeout_seconds: float = Field(60, gt=0, le=300)
    script_max_retries: int = Field(1, ge=0, le=2)
    elevenlabs_api_key: SecretStr = SecretStr("")
    elevenlabs_model_id: str = ""
    elevenlabs_default_voice_id: str = ""
    elevenlabs_default_voice_name: str = "Default Spanish voice"
    narration_storage_root: str = "data/audio"
    narration_max_characters: int = Field(10_000, ge=100, le=40_000)
    narration_timeout_seconds: float = Field(90, gt=0, le=300)
    narration_max_retries: int = Field(1, ge=0, le=2)
    narration_duration_tolerance: float = Field(0.2, ge=0.05, le=0.5)
    narration_max_file_size_mb: int = Field(20, ge=1, le=100)
    ffmpeg_binary: str = ""
    ffprobe_binary: str = ""
    render_storage_root: str = "data/renders"
    render_width: int = Field(1080, ge=360, le=2160)
    render_height: int = Field(1920, ge=640, le=3840)
    render_fps: int = Field(30, ge=24, le=60)
    render_crf: int = Field(20, ge=16, le=30)
    render_audio_bitrate: str = "192k"
    render_timeout_seconds: float = Field(300, gt=0, le=1800)
    render_duration_tolerance: float = Field(0.15, ge=0.02, le=0.5)
    render_min_playback_speed: float = Field(0.8, ge=0.5, le=1)
    render_max_playback_speed: float = Field(1.25, ge=1, le=2)
    render_clip_pre_roll_ms: int = Field(150, ge=0, le=1000)
    render_clip_post_roll_ms: int = Field(150, ge=0, le=1000)
    render_max_file_size_mb: int = Field(150, ge=1, le=1000)
    caption_storage_root: str = "data/captions"
    caption_font_path: str = ""
    caption_default_style: Literal["CLEAN", "BOLD", "MINIMAL"] = "CLEAN"
    caption_min_words: int = Field(2, ge=1, le=10)
    caption_max_words: int = Field(7, ge=2, le=15)
    caption_max_characters: int = Field(42, ge=10, le=100)
    caption_max_lines: int = Field(2, ge=1, le=3)
    caption_linger_ms: int = Field(120, ge=0, le=1000)
    caption_max_characters_per_second: float = Field(20, ge=8, le=40)
    caption_safe_margin_top: float = Field(0.08, ge=0, le=0.4)
    caption_safe_margin_bottom: float = Field(0.18, ge=0, le=0.4)
    caption_safe_margin_left: float = Field(0.08, ge=0, le=0.4)
    caption_safe_margin_right: float = Field(0.16, ge=0, le=0.4)
    caption_word_highlight_enabled: bool = True
    caption_max_emphasis_per_item: int = Field(2, ge=0, le=5)
    graphics_branding_enabled: bool = False
    branding_asset_path: str = ""
    branding_channel_name: str = ""
    branding_opacity: float = Field(0.7, ge=0.1, le=1)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    scoring_weights: ScoringWeights = Field(default_factory=ScoringWeights)

    @model_validator(mode="after")
    def validate_limits(self) -> "Settings":
        if self.research_min_sources > self.research_max_pages:
            raise ValueError("RESEARCH_MIN_SOURCES cannot exceed RESEARCH_MAX_PAGES")
        if self.research_max_pages > self.research_max_sources:
            raise ValueError("RESEARCH_MAX_PAGES cannot exceed RESEARCH_MAX_SOURCES")
        if self.render_min_playback_speed > self.render_max_playback_speed:
            raise ValueError("RENDER_MIN_PLAYBACK_SPEED cannot exceed the maximum")
        if self.caption_min_words > self.caption_max_words:
            raise ValueError("CAPTION_MIN_WORDS cannot exceed CAPTION_MAX_WORDS")
        return self
