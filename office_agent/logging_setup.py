"""File logging with secret redaction. Document contents are never logged by this program."""
import logging
import re
from logging.handlers import RotatingFileHandler

from .config import log_dir

_SECRET = re.compile(r"\b(?:sa|se)_[A-Za-z0-9_-]{8,}|(?i:bearer)\s+\S+")


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _SECRET.sub("[redacted]", record.getMessage())
        record.args = ()
        if record.exc_info:  # tracebacks can embed request details
            record.exc_text = _SECRET.sub("[redacted]", logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        return True


def setup_logging() -> None:
    handler = RotatingFileHandler(log_dir() / "agent.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactFilter())
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
