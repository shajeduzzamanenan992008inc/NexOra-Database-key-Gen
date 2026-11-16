"""WSGI entrypoint for production servers (waitress, gunicorn)."""

from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()

from backend.app import create_app  # noqa: E402

app = create_app()
application = app
