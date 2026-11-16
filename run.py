"""Local development server. Use waitress or gunicorn in production."""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

from backend.app import create_app  # noqa: E402

app = create_app()

if __name__ == "__main__":
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "5000"))
    app.run(host=host, port=port, debug=False)
