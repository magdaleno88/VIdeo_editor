from collections.abc import Iterator
from contextlib import contextmanager

import httpx
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.providers.rendering import FFmpegVideoRenderer
from app.repositories.candidates import CandidateRepository
from app.repositories.narrations import NarrationRepository
from app.repositories.renders import RenderRepository
from app.repositories.scripts import ScriptRepository
from app.services.rendering.service import EditPlanService, RenderService
from app.services.rendering.storage import NarrationFileResolver, RenderStorage
from app.services.video.assets import VideoAssetFetcher


def edit_plan_service(settings: Settings, session: Session) -> EditPlanService:
    return EditPlanService(
        session,
        CandidateRepository(session),
        ScriptRepository(session),
        NarrationRepository(session),
        RenderRepository(session),
        width=settings.render_width,
        height=settings.render_height,
        fps=settings.render_fps,
        min_speed=settings.render_min_playback_speed,
        max_speed=settings.render_max_playback_speed,
        pre_roll_ms=settings.render_clip_pre_roll_ms,
        post_roll_ms=settings.render_clip_post_roll_ms,
    )


@contextmanager
def configured_render_service(settings: Settings, session: Session) -> Iterator[RenderService]:
    renderer = FFmpegVideoRenderer(
        settings.ffmpeg_binary,
        settings.ffprobe_binary,
        crf=settings.render_crf,
        audio_bitrate=settings.render_audio_bitrate,
        timeout_seconds=settings.render_timeout_seconds,
    )
    with httpx.Client(timeout=settings.http_timeout_seconds, follow_redirects=False) as client:
        yield RenderService(
            CandidateRepository(session),
            ScriptRepository(session),
            NarrationRepository(session),
            RenderRepository(session),
            renderer,
            VideoAssetFetcher(
                client,
                settings.ai_video_max_file_size_mb * 1024 * 1024,
                settings.ai_video_max_duration_seconds,
            ),
            RenderStorage(settings.render_storage_root),
            NarrationFileResolver(settings.narration_storage_root),
            duration_tolerance=settings.render_duration_tolerance,
            max_file_size_bytes=settings.render_max_file_size_mb * 1024 * 1024,
        )
