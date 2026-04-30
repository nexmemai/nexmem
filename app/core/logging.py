import logging
import json
import sys
from datetime import datetime, timezone


class JSONFormatter(logging.Formatter):
    def format(self, record):
        data = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": getattr(record, "request_id", None),
            "user_id": getattr(record, "user_id", None),
        }
        return json.dumps(data)


def configure_logging():
    """Configure root logger with JSON formatting once at startup."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())

    root = logging.getLogger()  # root logger - captures everything
    root.setLevel(logging.INFO)
    root.handlers = []  # remove any pre-existing handlers
    root.addHandler(handler)
