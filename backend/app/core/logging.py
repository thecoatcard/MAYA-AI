import json
import logging
import re
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

from sqlalchemy.engine import make_url

from app.core.config import Settings

request_id_context: ContextVar[str | None] = ContextVar("request_id", default=None)
trace_id_context: ContextVar[str | None] = ContextVar("trace_id", default=None)

URL_CREDENTIALS = re.compile(r"([a-z][a-z0-9+.-]*://)[^/\s@]+@", re.IGNORECASE)
BEARER_TOKEN = re.compile(r"(Bearer\s+)\S+", re.IGNORECASE)


class JsonFormatter(logging.Formatter):
    def __init__(self, secrets: tuple[str, ...]) -> None:
        super().__init__()
        self.secrets = tuple(sorted((value for value in secrets if value), key=len, reverse=True))

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        for secret in self.secrets:
            message = message.replace(secret, "[redacted]")
        message = URL_CREDENTIALS.sub(r"\1[redacted]@", message)
        message = BEARER_TOKEN.sub(r"\1[redacted]", message)
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "message": message,
            "request_id": request_id_context.get(),
            "trace_id": trace_id_context.get(),
        }
        if record.exc_info is not None and record.exc_info[0] is not None:
            payload["exception_type"] = record.exc_info[0].__name__
        return json.dumps(payload)


def configure_logging(settings: Settings) -> None:
    database_url = settings.database_url.get_secret_value()
    redis_url = settings.redis_url.get_secret_value()
    secrets = [database_url, redis_url]
    for url in (database_url, redis_url):
        password = make_url(url).password
        if password:
            secrets.append(password)
    if settings.migration_database_url is not None:
        migration_url = settings.migration_database_url.get_secret_value()
        secrets.extend((migration_url, make_url(migration_url).password or ""))
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(tuple(secrets)))
    logger = logging.getLogger("maya")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
