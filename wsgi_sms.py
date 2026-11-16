"""WSGI entrypoint for the isolated SMS Reader console."""

from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()

from sms_reader import create_sms_app  # noqa: E402

app = create_sms_app()
application = app
