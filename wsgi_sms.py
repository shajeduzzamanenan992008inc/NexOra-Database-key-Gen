"""WSGI entrypoint for the isolated SMS Reader console.

    gunicorn wsgi_sms:app
    waitress-serve --listen=127.0.0.1:8100 wsgi_sms:app

No banner and no animation here: output belongs to the process manager, and
production logs must stay plain, one event per line.
"""

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv

load_dotenv()

from sms_reader import create_sms_app  # noqa: E402

__all__ = ["app", "application"]

_log = logging.getLogger("nexora.wsgi.sms")

try:
    app = create_sms_app()
except Exception as exc:
    # One clear line for the log; the server then prints the traceback.
    _log.critical("SMS Reader console failed to start (pid %s): %s: %s", os.getpid(), type(exc).__name__, exc)
    raise

application = app
_log.info("SMS Reader console WSGI application ready (pid %s)", os.getpid())
