from contextlib import contextmanager

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.providers.captions import ASSSubtitleRenderer, CaptionFFmpegRenderer, SRTSubtitleRenderer
from app.providers.rendering import FFmpegVideoRenderer
from app.repositories.captions import CaptionRepository
from app.repositories.narrations import NarrationRepository
from app.repositories.renders import RenderRepository
from app.repositories.scripts import ScriptRepository
from app.services.captions.service import CaptionPlanService, FinalRenderService
from app.services.captions.storage import CaptionStorage


def caption_plan_service(settings: Settings, session: Session) -> CaptionPlanService:
    return CaptionPlanService(
        RenderRepository(session),
        ScriptRepository(session),
        NarrationRepository(session),
        CaptionRepository(session),
        default_style=settings.caption_default_style,
        min_words=settings.caption_min_words,
        max_words=settings.caption_max_words,
        max_characters=settings.caption_max_characters,
        max_lines=settings.caption_max_lines,
        linger_ms=settings.caption_linger_ms,
        max_characters_per_second=settings.caption_max_characters_per_second,
        safe_area={
            "top": settings.caption_safe_margin_top,
            "bottom": settings.caption_safe_margin_bottom,
            "left": settings.caption_safe_margin_left,
            "right": settings.caption_safe_margin_right,
        },
        max_emphasis=settings.caption_max_emphasis_per_item,
        word_highlight_enabled=settings.caption_word_highlight_enabled,
        branding_enabled=settings.graphics_branding_enabled,
        branding_channel_name=settings.branding_channel_name,
    )


@contextmanager
def configured_final_render_service(settings: Settings, session: Session):
    executor = FFmpegVideoRenderer(
        settings.ffmpeg_binary,
        settings.ffprobe_binary,
        crf=settings.render_crf,
        audio_bitrate=settings.render_audio_bitrate,
        timeout_seconds=settings.render_timeout_seconds,
    )
    yield FinalRenderService(
        RenderRepository(session),
        CaptionRepository(session),
        CaptionFFmpegRenderer(executor),
        ASSSubtitleRenderer(),
        SRTSubtitleRenderer(),
        CaptionStorage(settings.caption_storage_root, settings.render_storage_root),
        font_path=settings.caption_font_path,
        branding_enabled=settings.graphics_branding_enabled,
        branding_path=settings.branding_asset_path,
        branding_opacity=settings.branding_opacity,
        duration_tolerance=settings.render_duration_tolerance,
        max_file_size_bytes=settings.render_max_file_size_mb * 1024 * 1024,
    )
