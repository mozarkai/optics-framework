import logging
import uvicorn

from optics_framework.common.logging_config import internal_logger

# Local console format for optics serve only (keeps global logging_config unchanged)
CONSOLE_FORMAT = "%(levelname)s | %(asctime)s | pid=%(process)d | %(name)s:%(funcName)s:%(lineno)d | %(message)s"

APP_STRING = "optics_framework.common.expose_api:app"


def _apply_optics_logging_to_uvicorn() -> None:
    """Copy the project's console handlers to uvicorn/gunicorn and root loggers.

    This ensures uvicorn access/error logs follow the same rich formatter used
    by the Optics Framework internal logger.
    """
    try:
        handlers = (
            internal_logger.handlers[:]
            if internal_logger and internal_logger.handlers
            else []
        )
        if not handlers:
            return

        # Attach handlers to commonly-used server loggers
        server_loggers = (
            "uvicorn.access",
            "uvicorn.error",
            "uvicorn",
        )
        for name in server_loggers:
            lg = logging.getLogger(name)
            # Replace existing handlers so uvicorn's default formatting isn't used
            lg.handlers = list(handlers)
            # Use the first handler's level as the logger level if available
            try:
                lg.setLevel(handlers[0].level)
            except (AttributeError, IndexError, ValueError, TypeError) as e:
                internal_logger.debug("Failed to set logger level for %s: %s", name, e)
                lg.setLevel(logging.DEBUG)
            lg.propagate = False

        # Also ensure the root logger uses the same handlers so other libraries
        # emit consistently formatted logs.
        root = logging.getLogger()
        root.handlers = list(handlers)
        root.setLevel(logging.DEBUG)
    except (AttributeError, RuntimeError, OSError, TypeError, ValueError) as e:
        # If anything goes wrong, fall back to default logging behaviour and log debug
        try:
            internal_logger.debug("Error applying optics logging to uvicorn: %s", e)
        except Exception:
            print("Error applying optics logging to uvicorn, and internal_logger is unavailable.")


def run_uvicorn_server(host: str = "127.0.0.1", port: int = 8000, workers: int = 1):
    """
    Run the Optics Framework API server using uvicorn.

    Args:
        host (str, optional): Host address. Defaults to "127.0.0.1".
        port (int, optional): Port number. Defaults to 8000.
        workers (int, optional): Number of worker processes. Defaults to 1.
    """
    # Apply Optics logging handlers to uvicorn so access/error logs match
    _apply_optics_logging_to_uvicorn()

    # Provide a uvicorn logging config that uses the project's console format
    # This helps ensure the master and worker startup logs use the same formatting.
    log_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "optics": {
                "format": CONSOLE_FORMAT,
                "datefmt": "%H:%M:%S",
            }
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "optics",
                "stream": "ext://sys.stdout",
            }
        },
        "loggers": {
            "uvicorn": {"handlers": ["console"], "level": "DEBUG"},
            "uvicorn.error": {"handlers": ["console"], "level": "ERROR"},
            "uvicorn.access": {"handlers": ["console"], "level": "INFO"},
        },
        "root": {"handlers": ["console"], "level": "DEBUG"},
    }

    uvicorn.run(
        APP_STRING,
        host=host,
        port=port,
        log_level="debug",
        access_log=True,
        workers=workers,
        log_config=log_config,
    )
