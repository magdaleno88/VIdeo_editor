import json
import logging
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        event = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in (
            "provider",
            "ai_provider",
            "candidate_id",
            "candidates_created",
            "duplicates",
            "request_id",
            "error_type",
            "request_stage",
            "http_status",
            "gemini_status",
            "gemini_message",
            "configured_model",
            "video_mime",
            "video_size_bytes",
            "video_duration_seconds",
            "video_extension",
            "input_method",
        ):
            if hasattr(record, key):
                event[key] = getattr(record, key)
        return json.dumps(event, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("app")
    logger.handlers = [handler]
    logger.setLevel(level)
    logger.propagate = False
    # Pixabay authenticates in query parameters. Never log HTTP request URLs.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).disabled = True
