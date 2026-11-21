"""WSGI entrypoint for production servers (waitress, gunicorn).

    gunicorn wsgi:app
    waitress-serve --listen=127.0.0.1:8000 wsgi:app

No banner and no animation here: output belongs to the process manager, and
production logs must stay plain, one event per line.
"""

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv

load_dotenv()

from backend.app import create_app  # noqa: E402

__all__ = ["app", "application"]

_log = logging.getLogger("nexora.wsgi")

try:
    app = create_app()
except Exception as exc:
    # One clear line for the log; the server then prints the traceback.
    _log.critical("NexOra failed to start (pid %s): %s: %s", os.getpid(), type(exc).__name__, exc)
    raise

application = app
_log.info("NexOra WSGI application ready (pid %s)", os.getpid())
