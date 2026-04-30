import logging
import sys
import structlog

def configure_logging():
    """Configure structlog for JSON structured logging."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
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
