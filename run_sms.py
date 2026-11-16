"""Local development server for the SMS Reader console (default port 5100)."""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

from sms_reader import create_sms_app  # noqa: E402

app = create_sms_app()

if __name__ == "__main__":
    host = os.getenv("SMS_HOST", os.getenv("HOST", "127.0.0.1"))
    port = int(os.getenv("SMS_PORT", os.getenv("PORT", "5100")))
    app.run(host=host, port=port, debug=False)
