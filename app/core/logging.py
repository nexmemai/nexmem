import logging
import sys
import structlog

from app.core.log_redactor import redact_sensitive


def configure_logging():
    """Configure structlog for JSON structured logging.

    Pipeline order matters:
      contextvars merge → log level → logger name → timestamp →
      stack info → exception formatting → REDACTION → JSON render.

    The redaction step (P2-C8) sits immediately before serialisation
    so any contextvars / extras / exception messages are scrubbed of
    known-sensitive keys before they hit stdout.
    """
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            redact_sensitive,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Configure standard logging to route through structlog
    handler = logging.StreamHandler(sys.stdout)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers = []
    root.addHandler(handler)
