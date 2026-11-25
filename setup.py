"""Packaging for NexOra: Database Key Generator + SMS Reader console.

    pip install -e .                  development install + latest sync
    pip install -e ".[waitress]"      plus the waitress production server
    python setup.py                   also works directly (upgrades & runs develop)

Installs two commands:
    nexora        -> run.py      (Database Key Generator, development server)
    nexora-sms    -> run_sms.py  (SMS Reader console, development server)

Dependencies are read from requirements.txt when it exists, so there is a
single list to maintain.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

from setuptools import find_packages, setup

ROOT = Path(__file__).resolve().parent


def _read(name: str) -> str:
    path = ROOT / name
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _bump_patch_version(ver: str) -> str:
    """Increment patch version (e.g. 0.1.0 -> 0.1.1) when no VERSION file is present."""
    parts = ver.split(".")
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        return f"{parts[0]}.{parts[1]}.{int(parts[2]) + 1}"
    return ver


def _version() -> str:
    """VERSION file first, then backend/__init__.py ``__version__``, else auto-bounced 0.1.x."""
    explicit = _read("VERSION").strip()
    if explicit:
        return explicit
    found = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', _read("backend/__init__.py"), re.M)
    base = found.group(1) if found else "0.1.0"
    if os.environ.get("NEXORA_AUTO_BUMP", "1").strip().lower() not in {"0", "false", "no", "off"}:
        return _bump_patch_version(base)
    return base


def _requirements() -> list[str]:
    wanted: list[str] = []
    for raw in _read("requirements.txt").splitlines():
        line = raw.split(" #", 1)[0].strip()
        if line and not line.startswith(("#", "-")) and "://" not in line:
            wanted.append(line)
    return wanted or ["Flask", "python-dotenv"]


def _root_data_files() -> list[tuple[str, list[str]]]:
    files: list[str] = []
    for candidate in ("ascii.txt", "VERSION", "README.md"):
        if (ROOT / candidate).is_file():
            files.append(candidate)
    return [("share/nexora", files)] if files else []


def _sync_latest_repo() -> str:
    """Pull latest changes from git remote if available and requested."""
    if os.environ.get("NEXORA_UPGRADE", "1").strip().lower() in {"0", "false", "no", "off"}:
        return "upgrade skipped"
    git_dir = ROOT / ".git"
    if not git_dir.exists():
        return "local workspace (no .git)"
    try:
        res = subprocess.run(
            ["git", "pull", "--ff-only"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=15,
        )
        if res.returncode == 0:
            out = (res.stdout or "").strip().splitlines()
            return out[-1] if out else "up to date"
        return "local changes kept"
    except Exception:
        return "offline / local mode"


def _animate_setup_intro() -> None:
    """Play the NexOra gradient banner, colored status text & real upgrade task animation."""
    if os.environ.get("NEXORA_SETUP_ANIMATE", "1").strip().lower() in {"0", "false", "no", "off"}:
        return
    try:
        from nexora_ui import Console
    except ImportError:
        return

    ui = Console(animate=True)
    if ui.vt and ui.color < 2 and not os.environ.get("NO_COLOR") and not os.environ.get("NEXORA_NO_COLOR"):
        ui.color = 3
    ui.animate = bool(ui.vt and ui.color >= 2)

    ui.banner(
        "Latest Version & CLI Upgrade",
        ["nexora", "v" + _version(), "auto-sync"],
        background=False,
    )

    with ui.task("Upgrading", "checking latest version and workspace") as check:
        sync_msg = _sync_latest_repo()
        pkgs = find_packages(
            where=str(ROOT),
            include=["backend", "backend.*", "sms_reader", "sms_reader.*"],
        )
        reqs = _requirements()
        time.sleep(0.25)
        check.ok(f"v{_version()} · {sync_msg} · {len(pkgs)} pkgs", timing=True)

    ui.status(
        "info",
        "Commands",
        ui.style("nexora", "cyan", bold=True)
        + ui.style(" · ", "dim")
        + ui.style("nexora-sms", "violet", bold=True)
        + ui.style(f" ({len(reqs)} deps)", "mute"),
    )
    ui.section("Install")


def _run_setup() -> None:
    setup(
        name="nexora",
        version=_version(),
        description="NexOra - Database Key Generator and SMS Reader console.",
        license="LicenseRef-NexOra-PANC-1.1",
        license_files=["LICENSE"],
        long_description=_read("README.md"),
        long_description_content_type="text/markdown",
        python_requires=">=3.9",
        packages=find_packages(
            where=str(ROOT),
            include=["backend", "backend.*", "sms_reader", "sms_reader.*"],
        ),
        py_modules=["nexora_ui", "nexora_art", "run", "run_sms"],
        package_data={
            "backend": ["templates/**/*", "static/**/*"],
            "sms_reader": ["templates/**/*", "static/**/*"],
        },
        data_files=_root_data_files(),
        include_package_data=True,
        install_requires=_requirements(),
        extras_require={
            "waitress": ["waitress"],
            "gunicorn": ["gunicorn"],
        },
        entry_points={
            "console_scripts": [
                "nexora=run:main",
                "nexora-sms=run_sms:main",
            ]
        },
        classifiers=[
            "Programming Language :: Python :: 3",
            "Framework :: Flask",
            "Operating System :: OS Independent",
            # Blocks an accidental upload to PyPI; remove it if you publish on purpose.
            "Private :: Do Not Upload",
        ],
        zip_safe=False,
    )


if __name__ == "__main__":
    if len(sys.argv) == 1:
        sys.argv.extend(["develop", "--no-deps"])
    _animate_setup_intro()
    _run_setup()