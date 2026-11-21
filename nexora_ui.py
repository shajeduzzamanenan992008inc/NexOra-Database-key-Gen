"""NexOra terminal design system (standard library only).

Shared by ``run.py`` and ``run_sms.py``.

Principles
----------
* Honest output. Animation is cosmetic: a spinner only runs while a real check
  is running, every timing shown is measured, and no status line is scripted.
* One orchestrated moment: the logo reveal (about 0.4 s). It plays while the
  platform modules import, so it overlaps real startup work; when imports are
  faster than the reveal it adds the difference. NEXORA_ANIMATE=0 removes it.
* Graceful degradation: truecolor -> 256 -> 16 colours -> plain text,
  Unicode -> ASCII, animation -> static. Pipes, CI logs and NO_COLOR get
  clean text without escape codes.
* Palette follows the NexOra brand: electric cyan, neon blue, violet, silver.

Environment variables
---------------------
NO_COLOR / NEXORA_NO_COLOR   disable colours
FORCE_COLOR                  keep colours when stdout is not a terminal
NEXORA_ANIMATE=0             disable animation (also off when CI is set)
NEXORA_ASCII=1               force plain ASCII glyphs
NEXORA_UNICODE=1             force the modern Unicode glyph set
NEXORA_LOG_QUERY=1           show query strings in the live request log
                             (hidden by default: they can carry secrets)
NEXORA_ART=0                 hide the mascot art in the banner
NEXORA_ART_FILE=path         use another art file ($1..$9 colour-band format)
"""

from __future__ import annotations

import atexit
import logging
import os
import re
import shutil
import socket
import sys
import threading
import time
import traceback
import unicodedata
import webbrowser
from functools import lru_cache
from typing import Dict, List, Optional, Sequence, Tuple, Union

__all__ = [
    "Console",
    "Task",
    "RequestLog",
    "env_flag",
    "fmt_duration",
    "fit",
    "format_host",
    "format_url",
    "import_hint",
    "install_request_log",
    "is_loopback",
    "load_art",
    "parse_art",
    "parse_port",
    "port_open",
    "probe_host",
    "serve",
    "visible_width",
]

RGB = Tuple[int, int, int]
Color = Union[str, RGB]
Row = Tuple[str, ...]  # (label, value) or (label, value, colour name)

RESET = "\033[0m"

# --------------------------------------------------------------------------
# Palette
# --------------------------------------------------------------------------

PALETTE: Dict[str, RGB] = {
    "cyan": (0, 229, 255),      # electric cyan
    "blue": (58, 134, 255),     # neon blue
    "violet": (150, 92, 255),   # violet accent
    "silver": (206, 214, 228),  # primary text
    "mute": (146, 158, 180),    # secondary text
    "dim": (88, 102, 128),      # borders and rules
    "ok": (0, 230, 118),
    "warn": (255, 196, 0),
    "fail": (255, 82, 82),
    "white": (255, 255, 255),
}
_SGR16 = {
    "cyan": 96, "blue": 94, "violet": 95, "silver": 97, "mute": 37,
    "dim": 90, "ok": 92, "warn": 93, "fail": 91, "white": 97,
}
_GRADIENT: Sequence[Tuple[float, RGB]] = (
    (0.0, PALETTE["cyan"]),
    (0.55, PALETTE["blue"]),
    (1.0, PALETTE["violet"]),
)


def _mix(a: RGB, b: RGB, t: float) -> RGB:
    return (
        int(round(a[0] + (b[0] - a[0]) * t)),
        int(round(a[1] + (b[1] - a[1]) * t)),
        int(round(a[2] + (b[2] - a[2]) * t)),
    )


def _gradient(t: float) -> RGB:
    t = min(max(t, 0.0), 1.0)
    for (p0, c0), (p1, c1) in zip(_GRADIENT, _GRADIENT[1:]):
        if t <= p1:
            return _mix(c0, c1, (t - p0) / (p1 - p0))
    return _GRADIENT[-1][1]


def _rgb_to_256(rgb: RGB) -> int:
    r, g, b = rgb
    if abs(r - g) < 10 and abs(g - b) < 10:  # grey ramp
        if r < 8:
            return 16
        if r > 248:
            return 231
        return 232 + int(round((r - 8) / 247 * 24))
    return 16 + 36 * int(round(r / 255 * 5)) + 6 * int(round(g / 255 * 5)) + int(round(b / 255 * 5))


# --------------------------------------------------------------------------
# Logo (figlet "ANSI Shadow" letters, assembled so every row has equal width)
# --------------------------------------------------------------------------

_LETTERS: Dict[str, List[str]] = {
    "N": ["███╗   ██╗", "████╗  ██║", "██╔██╗ ██║", "██║╚██╗██║", "██║ ╚████║", "╚═╝  ╚═══╝"],
    "E": ["███████╗", "██╔════╝", "█████╗  ", "██╔══╝  ", "███████╗", "╚══════╝"],
    "X": ["██╗  ██╗", "╚██╗██╔╝", " ╚███╔╝ ", " ██╔██╗ ", "██╔╝ ██╗", "╚═╝  ╚═╝"],
    "O": [" ██████╗ ", "██╔═══██╗", "██║   ██║", "██║   ██║", "╚██████╔╝", " ╚═════╝ "],
    "R": ["██████╗ ", "██╔══██╗", "██████╔╝", "██╔══██╗", "██║  ██║", "╚═╝  ╚═╝"],
    "A": [" █████╗ ", "██╔══██╗", "███████║", "██╔══██║", "██║  ██║", "╚═╝  ╚═╝"],
}
LOGO: List[str] = ["".join(_LETTERS[ch][row] for ch in "NEXORA") for row in range(6)]
LOGO_WIDTH: int = max(len(row) for row in LOGO)


# --------------------------------------------------------------------------
# Mascot art (fastfetch-style text art: each line starts with $1..$9, a colour band)
# --------------------------------------------------------------------------

_ART_LINE = re.compile(r"^\$([1-9])(.*)$")


def parse_art(text: str) -> List[Tuple[int, str]]:
    """Parse ``$1``..``$9`` prefixed art into ``[(band, cells), ...]`` of equal width.

    Braille blanks become plain spaces so alignment never depends on the font.
    Art without any markers gets an even top-to-bottom gradient.
    """
    rows: List[Tuple[Optional[int], str]] = []
    for raw in text.splitlines():
        match = _ART_LINE.match(raw)
        band, cells = (int(match.group(1)), match.group(2)) if match else (None, raw)
        rows.append((band, cells.replace("\u2800", " ").rstrip("\r")))
    while rows and not rows[0][1].strip():
        rows.pop(0)
    while rows and not rows[-1][1].strip():
        rows.pop()
    if not rows:
        return []
    last = max(len(rows) - 1, 1)
    banded: List[Tuple[int, str]] = []
    current = 1
    for index, (band, cells) in enumerate(rows):
        if band is not None:
            current = band
        elif not any(b is not None for b, _ in rows):
            current = 1 + int(round(8 * index / last))
        banded.append((current, cells))
    width = max(visible_width(cells) for _, cells in banded)
    return [(band, cells + " " * (width - visible_width(cells))) for band, cells in banded]


@lru_cache(maxsize=1)
def load_art() -> Optional[Tuple[Tuple[int, str], ...]]:
    """Mascot art: NEXORA_ART_FILE, then ascii.txt next to this module, then the built-in copy."""
    here = os.path.dirname(os.path.abspath(__file__))
    sources: List[str] = []
    for path in (os.environ.get("NEXORA_ART_FILE"), os.path.join(here, "ascii.txt")):
        if not path:
            continue
        try:
            with open(path, encoding="utf-8-sig") as handle:
                sources.append(handle.read())
        except (OSError, UnicodeDecodeError):
            continue
    try:
        from nexora_art import ART_TEXT

        sources.append(ART_TEXT)
    except ImportError:
        pass
    for text in sources:
        rows = parse_art(text)
        if rows and len(rows) <= 60 and visible_width(rows[0][1]) <= 80:
            return tuple(rows)
    return None


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

_FALSE = {"0", "false", "no", "off", ""}
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")


def env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in _FALSE


def fmt_duration(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _cell_width(ch: str) -> int:
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def visible_width(text: str) -> int:
    """Terminal columns used by ``text`` (escape codes ignored)."""
    return sum(_cell_width(ch) for ch in _ANSI_RE.sub("", text))


def fit(text: str, width: int, ellipsis: str = "…") -> str:
    """Truncate plain ``text`` to ``width`` columns."""
    if width <= 0:
        return ""
    if visible_width(text) <= width:
        return text
    limit = width - visible_width(ellipsis)
    out: List[str] = []
    used = 0
    for ch in text:
        cw = _cell_width(ch)
        if used + cw > limit:
            break
        out.append(ch)
        used += cw
    return "".join(out) + ellipsis


_WILDCARD = {"", "0.0.0.0", "::", "[::]"}
_LOOPBACK = {"localhost", "127.0.0.1", "::1", "[::1]"}


def probe_host(host: str) -> str:
    """Address to connect to when checking a server bound to ``host``."""
    if host in _WILDCARD:
        return "127.0.0.1"
    return host.strip("[]")


def format_host(host: str) -> str:
    """Host as it must appear in a URL or ``host:port`` pair (IPv6 in brackets)."""
    host = host.strip("[]")
    return f"[{host}]" if ":" in host else host


def format_url(host: str, port: int, path: str = "/") -> str:
    return f"http://{format_host(probe_host(host))}:{port}{path}"


def is_loopback(host: str) -> bool:
    return host in _LOOPBACK or host.startswith("127.")


def parse_port(raw: object) -> int:
    try:
        port = int(str(raw).strip())
    except (TypeError, ValueError):
        raise ValueError(f"port must be an integer between 1 and 65535 (got {raw!r})") from None
    if not 1 <= port <= 65535:
        raise ValueError(f"port must be between 1 and 65535 (got {port})")
    return port


def port_open(host: str, port: int, timeout: float = 0.35) -> bool:
    try:
        with socket.create_connection((probe_host(host), port), timeout=timeout):
            return True
    except OSError:
        return False


def import_hint(exc: ImportError, root: str) -> str:
    """Plain-language fix for an ImportError raised while loading the app."""
    name = getattr(exc, "name", None)
    if isinstance(exc, ModuleNotFoundError) and name:
        top = name.split(".")[0]
        if top in {"backend", "sms_reader"}:
            return f"run the launcher from the project folder ({root}) so '{top}' can be imported"
        if os.path.isfile(os.path.join(root, "requirements.txt")):
            return "pip install -r requirements.txt"
        return f"install the missing module '{top}' in this Python environment"
    return "check that the installed package versions match the project requirements"


# --------------------------------------------------------------------------
# Terminal capability detection
# --------------------------------------------------------------------------

_MODERN_PROGRAMS = {"vscode", "iTerm.app", "WezTerm", "Hyper", "ghostty", "Tabby", "rio"}


def _isatty(stream: object) -> bool:
    try:
        return bool(stream.isatty())  # type: ignore[attr-defined]
    except Exception:
        return False


def _enable_windows_vt() -> bool:
    """Turn on ANSI processing for the Windows console (no-op elsewhere)."""
    if os.name != "nt":
        return True
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetStdHandle.restype = wintypes.HANDLE
        kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel32.GetConsoleMode.restype = wintypes.BOOL
        kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.SetConsoleMode.restype = wintypes.BOOL
        kernel32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]

        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = wintypes.DWORD()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        enable_vt = 0x0004  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        if mode.value & enable_vt:
            return True
        return bool(kernel32.SetConsoleMode(handle, mode.value | enable_vt))
    except Exception:
        return False


def _vt_supported() -> bool:
    if os.environ.get("TERM", "").lower() == "dumb":
        return False
    return _enable_windows_vt()


def _detect_color(tty: bool, vt: bool) -> int:
    """0 = none, 1 = 16 colours, 2 = 256 colours, 3 = truecolor."""
    env = os.environ
    if env.get("NO_COLOR") or env.get("NEXORA_NO_COLOR"):
        return 0
    force = (env.get("FORCE_COLOR") or env.get("CLICOLOR_FORCE") or "").strip().lower()
    if force in {"0", "false", "no", "off"}:
        return 0
    forced = bool(force)
    if not (vt or forced):
        return 0

    colorterm = env.get("COLORTERM", "").lower()
    term = env.get("TERM", "").lower()
    if colorterm in {"truecolor", "24bit"} or term.endswith("-direct"):
        level = 3
    elif env.get("WT_SESSION") or env.get("KITTY_WINDOW_ID") or env.get("ConEmuANSI") == "ON":
        level = 3
    elif env.get("TERM_PROGRAM") in _MODERN_PROGRAMS:
        level = 3
    elif os.name == "nt":
        level = 3
    elif "256color" in term:
        level = 2
    else:
        level = 1
    return max(level, 2) if forced else level


def _can_encode(text: str, encoding: str) -> bool:
    try:
        text.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return False
    return True


def _detect_unicode(stream: object) -> Tuple[bool, bool]:
    """Return (unicode_ok, fancy_ok).

    ``unicode_ok``: logo blocks and box drawing can be printed.
    ``fancy_ok``:   braille spinner, check marks and rounded corners can be printed.
    """
    env = os.environ
    if env_flag("NEXORA_ASCII", False):
        return False, False
    encoding = getattr(stream, "encoding", None) or "ascii"
    legacy_probe = "█╗║╔╝╚═" + "".join(_glyphs(True, False).values())
    if not _can_encode(legacy_probe, encoding):
        return False, False
    fancy_probe = "".join(_glyphs(True, True).values())
    encodable = _can_encode(fancy_probe, encoding)
    if env_flag("NEXORA_UNICODE", False):
        return True, encodable
    if env.get("TERM", "").lower() == "linux":  # Linux virtual console: limited font
        return True, False
    if os.name == "nt":
        # Legacy conhost fonts often lack braille and dingbats; modern hosts are fine.
        modern = bool(env.get("WT_SESSION") or env.get("TERM_PROGRAM") or env.get("ConEmuANSI") == "ON")
        return True, modern and encodable
    return True, encodable


def _glyphs(unicode_ok: bool, fancy: bool) -> Dict[str, str]:
    if fancy:
        return {
            "ok": "✔", "warn": "▲", "fail": "✖", "info": "●", "dot": "●",
            "diamond": "◆", "h": "─", "heavy": "━", "v": "│",
            "tl": "╭", "tr": "╮", "bl": "╰", "br": "╯",
            "sep": "·", "ell": "…", "spin": "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏",
        }
    badges = {"ok": "[ OK ]", "warn": "[WARN]", "fail": "[FAIL]", "info": "[INFO]"}
    if unicode_ok:
        return dict(
            badges, dot="■", diamond="■", h="─", heavy="─", v="│",
            tl="┌", tr="┐", bl="└", br="┘", sep="·", ell="...", spin="|/-\\",
        )
    return dict(
        badges, dot="*", diamond="*", h="-", heavy="-", v="|",
        tl="+", tr="+", bl="+", br="+", sep="-", ell="...", spin="|/-\\",
    )


# --------------------------------------------------------------------------
# Console
# --------------------------------------------------------------------------

class Console:
    """Capability-aware terminal output.

    ``quiet=True`` silences the boot interface (used by the Werkzeug reloader
    child process) but still lets failures and the live request log through.
    """

    LABEL_WIDTH = 15

    def __init__(
        self,
        *,
        quiet: bool = False,
        animate: Optional[bool] = None,
        stream: Optional[object] = None,
    ) -> None:
        self.stream = stream if stream is not None else sys.stdout
        self.quiet = quiet
        self._lock = threading.RLock()
        self._cursor_hidden = False
        self._stopped = False
        self._banner_thread: Optional[threading.Thread] = None
        try:  # never crash on an exotic console encoding
            self.stream.reconfigure(errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass

        self.tty = _isatty(self.stream)
        self.vt = self.tty and _vt_supported()
        self.color = _detect_color(self.tty, self.vt)
        self.unicode, self.fancy = _detect_unicode(self.stream)
        motion = env_flag("NEXORA_ANIMATE", True) and not env_flag("CI", False)
        if animate is False:
            motion = False
        self.animate = bool(self.vt and motion)
        self.g = _glyphs(self.unicode, self.fancy)
        atexit.register(self.stop)

    # -- low level ---------------------------------------------------------

    def term_width(self) -> int:
        return shutil.get_terminal_size((80, 24)).columns

    def content_width(self) -> int:
        """Width shared by the logo, section rules and the status panel."""
        return max(36, min(LOGO_WIDTH + 2, self.term_width() - 4))

    def emit(self, text: str) -> None:
        """Write ``text`` even in quiet mode."""
        with self._lock:
            try:
                self.stream.write(text)  # type: ignore[attr-defined]
                self.stream.flush()  # type: ignore[attr-defined]
            except UnicodeEncodeError:
                encoding = getattr(self.stream, "encoding", None) or "ascii"
                try:
                    self.stream.write(text.encode(encoding, "replace").decode(encoding, "replace"))  # type: ignore[attr-defined]
                    self.stream.flush()  # type: ignore[attr-defined]
                except Exception:
                    pass
            except (OSError, ValueError):  # closed pipe or stream
                pass

    def write(self, text: str) -> None:
        if not self.quiet:
            self.emit(text)

    def line(self, text: str = "") -> None:
        self.write(text + "\n")

    def rewrite(self, text: str) -> None:
        """Replace the current terminal line (animation only)."""
        self.write("\r\033[2K" + text)

    # -- style -------------------------------------------------------------

    def _fg(self, color: Color) -> str:
        rgb = PALETTE[color] if isinstance(color, str) else color
        if self.color >= 3:
            return f"38;2;{rgb[0]};{rgb[1]};{rgb[2]}"
        if self.color == 2:
            return f"38;5;{_rgb_to_256(rgb)}"
        if self.color == 1:
            return str(_SGR16.get(color, 39)) if isinstance(color, str) else "96"
        return ""

    def style(
        self,
        text: str,
        color: Optional[Color] = None,
        *,
        bold: bool = False,
        dim: bool = False,
        underline: bool = False,
    ) -> str:
        if not text or not self.color:
            return text
        codes: List[str] = []
        if bold:
            codes.append("1")
        if dim:
            codes.append("2")
        if underline:
            codes.append("4")
        if color is not None:
            code = self._fg(color)
            if code:
                codes.append(code)
        if not codes:
            return text
        return f"\033[{';'.join(codes)}m{text}{RESET}"

    def gradient(self, text: str) -> str:
        if self.color < 2:
            return self.style(text, "cyan")
        last = max(len(text) - 1, 1)
        parts = [
            ch if ch == " " else f"\033[{self._fg(_gradient(i / last))}m{ch}"
            for i, ch in enumerate(text)
        ]
        return "".join(parts) + RESET

    def join(self, *parts: str) -> str:
        return f" {self.g['sep']} ".join(p for p in parts if p)

    # -- cursor and window -------------------------------------------------

    def hide_cursor(self) -> None:
        if self.vt and not self.quiet and not self._stopped:
            self.emit("\033[?25l")
            self._cursor_hidden = True

    def show_cursor(self) -> None:
        if self._cursor_hidden:
            self.emit("\033[?25h")
            self._cursor_hidden = False

    def stop(self) -> None:
        """Stop any running animation and give the cursor back (safe to call twice)."""
        self._stopped = True
        thread = self._banner_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        try:
            self.show_cursor()
        except Exception:
            pass

    def set_title(self, title: str) -> None:
        if self.vt and not self.quiet:
            self.emit(f"\033]0;{title}\007")

    # -- banner ------------------------------------------------------------

    def _logo_fits(self) -> bool:
        return self.unicode and self.term_width() >= LOGO_WIDTH + 4

    def banner(
        self,
        title: str,
        tagline: Sequence[str],
        *,
        background: bool = False,
    ) -> Optional[threading.Thread]:
        """Print the NexOra banner.

        With ``background=True`` the animated reveal runs in a thread so the
        caller can do real work meanwhile; pass the returned handle to
        :meth:`wait` before printing anything else.
        """
        if self.quiet:
            return None
        if background and self.animate and self.color >= 2 and self._logo_fits():
            thread = threading.Thread(
                target=self._banner, args=(title, tagline), daemon=True, name="nexora-banner"
            )
            thread.start()
            self._banner_thread = thread
            return thread
        self._banner(title, tagline)
        return None

    @staticmethod
    def wait(thread: Optional[threading.Thread]) -> None:
        if thread is not None:
            thread.join()

    def _banner(self, title: str, tagline: Sequence[str]) -> None:
        try:
            tag = self.join(*tagline)
            if self.tty:  # never let the banner wrap on narrow terminals
                room = self.term_width() - 4
                title = fit(title, room, self.g["ell"])
                tag = fit(tag, room, self.g["ell"])
            self.line()
            layout, why_hidden = self._art_plan(title, tag)
            if layout is not None:
                self._banner_art(layout, title, tag)
            elif self._logo_fits():
                self._logo()
                self.line()
                self.line("  " + self.style(title, "white", bold=True))
                self.line("  " + self.style(tag, "mute"))
                self.line("  " + self.gradient(self.g["heavy"] * self.content_width()))
            elif self.unicode:
                self.line("  " + self.gradient("NEXORA") + "  " + self.style(title, "white", bold=True))
                self.line("  " + self.style(tag, "mute"))
                self.line("  " + self.gradient(self.g["heavy"] * 24))
            else:
                head = "N E X O R A  /  " + title.upper()
                width = max(visible_width(head), visible_width(tag)) + 2
                for row in self.box([head, tag], inner=width, color="cyan"):
                    self.line("  " + row)
            if layout is None and why_hidden:
                room = self.term_width() - 4 if self.tty else 10_000
                self.line("  " + self.style(fit("wolf hidden: " + why_hidden, room, self.g["ell"]), "dim"))
        finally:
            self.show_cursor()

    # -- mascot banner -----------------------------------------------------

    def _art_plan(
        self, title: str, tag: str
    ) -> Tuple[Optional[Tuple[Tuple[Tuple[int, str], ...], int]], str]:
        """(layout, reason). ``layout`` is None when the mascot is hidden; ``reason`` is a one-line explanation for cases the user can fix (empty when hiding is expected)."""
        if not env_flag("NEXORA_ART", True) or not self.tty or env_flag("NEXORA_ASCII", False):
            return None, ""
        if not self.fancy:
            return None, "this console may not draw Braille - set NEXORA_UNICODE=1 to try, or use Windows Terminal"
        art = load_art()
        if art is None:
            return None, "art not found - keep ascii.txt or nexora_art.py next to nexora_ui.py"
        right = max(LOGO_WIDTH, visible_width(title), visible_width(tag))
        need_cols = 2 + visible_width(art[0][1]) + 3 + right + 3
        need_rows = max(len(art), len(LOGO) + 4) + 2
        size = shutil.get_terminal_size((80, 24))
        if size.columns < need_cols or size.lines < need_rows:
            return None, (
                f"terminal is {size.columns}x{size.lines}, the wolf needs {need_cols}x{need_rows} "
                "- enlarge the window"
            )
        return (art, right), ""

    def _art_row(self, art: Sequence[Tuple[int, str]], row: int, glow: float) -> str:
        band, cells = art[row]
        if not self.color:
            return cells
        if self.color == 1:
            name = "cyan" if band <= 3 else "blue" if band <= 6 else "violet"
            return f"\033[{self._fg(name)}m{cells}{RESET}"
        rgb = _gradient((band - 1) / 8)
        if glow:
            rgb = _mix(rgb, PALETTE["white"], glow)
        return f"\033[{self._fg(rgb)}m{cells}{RESET}"

    def _logo_row(self, index: int, glow: float) -> str:
        text = LOGO[index]
        if not self.color:
            return text
        if self.color == 1:
            return self.style(text, "cyan")
        parts: List[str] = []
        for col, ch in enumerate(text):
            if ch == " ":
                parts.append(" ")
                continue
            rgb = _gradient(col / (LOGO_WIDTH - 1))
            if glow:
                rgb = _mix(rgb, PALETTE["white"], glow)
            parts.append(f"\033[{self._fg(rgb)}m{ch}")
        return "".join(parts) + RESET

    def _banner_art(
        self,
        layout: Tuple[Tuple[Tuple[int, str], ...], int],
        title: str,
        tag: str,
    ) -> None:
        """Mascot on the left, wordmark and title on the right (fastfetch style).

        The reveal prints row by row with a bright leading edge. It only ever
        repaints at most the last three rows, so it works in any terminal height.
        """
        art, right = layout
        art_w = visible_width(art[0][1])
        block: List[Tuple[str, int]] = [("logo", i) for i in range(len(LOGO))]
        block += [("blank", 0), ("title", 0), ("tag", 0), ("rule", 0)]
        height = max(len(art), len(block))  # art or wordmark may be the taller side
        art_top = (height - len(art)) // 2
        block_top = (height - len(block)) // 2

        def side(kind: str, index: int, glow: float) -> str:
            if kind == "logo":
                return self._logo_row(index, glow)
            if kind == "title":
                return self.style(title, "white", bold=True)
            if kind == "tag":
                return self.style(tag, "mute")
            if kind == "rule":
                return self.gradient(self.g["heavy"] * right)
            return ""

        def compose(row: int, glow: float) -> str:
            a = row - art_top
            text = "  " + (self._art_row(art, a, glow) if 0 <= a < len(art) else " " * art_w)
            k = row - block_top
            if 0 <= k < len(block):
                text += "   " + side(block[k][0], block[k][1], glow)
            return text.rstrip() if not self.color else text

        if not (self.animate and self.color >= 2):
            for row in range(height):
                self.line(compose(row, 0.0))
            return

        glows = (0.85, 0.45, 0.18, 0.0)  # leading row, two fading rows, then the final colour
        self.hide_cursor()
        for row in range(height):
            if self._stopped:
                return
            back = min(row, len(glows) - 1)
            out = f"\033[{back}A" if back else ""
            for j in range(row - back, row + 1):
                out += compose(j, glows[row - j]) + "\n"
            self.write(out)
            time.sleep(0.014)
        settle = min(height, len(glows))
        out = f"\033[{settle}A"
        for j in range(height - settle, height):
            out += compose(j, 0.0) + "\n"
        self.write(out)

    def _logo_frame(self, step: Optional[int], steps: int) -> str:
        width = LOGO_WIDTH
        front = None if step is None else (width + 12) * step / max(steps - 1, 1)
        out: List[str] = []
        for r, row in enumerate(LOGO):
            parts: List[str] = ["  "]
            for c, ch in enumerate(row):
                if ch == " ":
                    parts.append(" ")
                    continue
                glow = 0.0
                if front is not None:
                    behind = front - c - r * 1.5
                    if behind < 0:
                        parts.append(" ")
                        continue
                    glow = max(0.0, 1.0 - behind / 7.0)
                t = c / (width - 1)
                if self.color >= 2:
                    rgb = _gradient(t)
                    if glow:
                        rgb = _mix(rgb, PALETTE["white"], 0.85 * glow)
                    parts.append(f"\033[{self._fg(rgb)}m{ch}")
                elif self.color == 1:
                    name = "cyan" if t < 0.34 else "blue" if t < 0.67 else "violet"
                    parts.append(f"\033[{self._fg(name)}m{ch}")
                else:
                    parts.append(ch)
            if self.color:
                parts.append(RESET)
            out.append("".join(parts))
        return "\n".join(out) + "\n"

    def _logo(self) -> None:
        height = len(LOGO)
        if not (self.animate and self.color >= 2):
            self.write(self._logo_frame(None, 0))
            return
        steps = 18
        self.hide_cursor()
        for step in range(steps):
            if self._stopped:
                return
            prefix = "" if step == 0 else f"\033[{height}A"
            self.write(prefix + self._logo_frame(step, steps))
            time.sleep(0.02)
        self.write(f"\033[{height}A" + self._logo_frame(None, steps))

    # -- structure ---------------------------------------------------------

    def section(self, title: str) -> None:
        if self.quiet:
            return
        fill = max(4, self.content_width() - visible_width(title) - 3)
        marker = self.style(self.g["diamond"], "cyan")
        head = self.style(title, "silver", bold=True)
        self.line()
        self.line(f"  {marker} {head} {self.style(self.g['h'] * fill, 'dim')}")

    def _badge(self, kind: str) -> Tuple[str, int]:
        color = {"ok": "ok", "warn": "warn", "fail": "fail", "info": "cyan"}[kind]
        raw = self.g[kind]
        return self.style(raw, color, bold=True), visible_width(raw)

    def _detail_indent(self) -> int:
        return 2 + visible_width(self.g["ok"]) + 1 + self.LABEL_WIDTH + 1

    def status_text(self, kind: str, label: str, detail: str = "") -> str:
        badge, badge_w = self._badge(kind)
        if self.tty:
            detail = fit(detail, self.term_width() - self._detail_indent() - 1, self.g["ell"])
        color = {"ok": "mute", "info": "mute", "warn": "warn", "fail": "fail"}[kind]
        label_txt = self.style(label.ljust(self.LABEL_WIDTH), "silver")
        return f"  {badge} {label_txt} {self.style(detail, color)}"

    def status(self, kind: str, label: str, detail: str = "") -> None:
        self.line(self.status_text(kind, label, detail))

    def hint(self, text: str) -> None:
        """How to fix the problem shown above. Printed even in quiet mode."""
        self.emit(" " * self._detail_indent() + self.style("fix: " + text, "silver") + "\n")

    def fatal(self, label: str, message: str, hint: Optional[str] = None) -> None:
        self.emit(self.status_text("fail", label, message) + "\n")
        if hint:
            self.hint(hint)

    def show_traceback(self) -> None:
        indent = " " * self._detail_indent()
        for row in traceback.format_exc().rstrip().splitlines():
            self.emit(indent + self.style(row, "dim") + "\n")

    def task(self, label: str, detail: str = "") -> "Task":
        return Task(self, label, detail)

    def goodbye(self, seconds: float) -> None:
        self.line()
        self.status("info", "Shutdown", self.join(f"stopped after {fmt_duration(seconds)}", "goodbye"))

    # -- boxes -------------------------------------------------------------

    def box(
        self,
        lines: Sequence[str],
        *,
        inner: int,
        title: str = "",
        color: Color = "dim",
        pad: int = 2,
    ) -> List[str]:
        g = self.g
        space = " " * pad

        def edge(text: str) -> str:
            return self.style(text, color)

        if title:
            inner = max(inner, visible_width(title) + 2)
        total = inner + 2 * pad  # columns between the two vertical edges
        if title:
            fill = max(1, total - visible_width(title) - 3)
            top = edge(g["tl"] + g["h"]) + " " + title + " " + edge(g["h"] * fill + g["tr"])
        else:
            top = edge(g["tl"] + g["h"] * total + g["tr"])
        rows = [top]
        for text in lines:
            fill_pad = " " * max(0, inner - visible_width(text))
            rows.append(edge(g["v"]) + space + text + fill_pad + space + edge(g["v"]))
        rows.append(edge(g["bl"] + g["h"] * total + g["br"]))
        return rows

    def panel(
        self,
        title: str,
        rows: Sequence[Row],
        *,
        kind: str = "ok",
        footer: str = "",
    ) -> None:
        if self.quiet or not rows:
            return
        avail = max(24, self.term_width() - 8) if self.tty else 10_000
        label_w = max(len(row[0]) for row in rows)
        lines: List[str] = [""]
        for row in rows:
            label, value = row[0], row[1]
            color = row[2] if len(row) > 2 else "silver"
            value = fit(value, avail - label_w - 2, self.g["ell"])
            lines.append(
                self.style(label.ljust(label_w), "mute")
                + "  "
                + self.style(value, color, underline=(color == "cyan"))
            )
        if footer:
            lines += ["", self.style(fit(footer, avail, self.g["ell"]), "mute")]
        lines.append("")
        border = {"ok": "ok", "warn": "warn", "fail": "fail"}.get(kind, "dim")
        head = self.style(self.g["dot"], border) + " " + self.style(title, "silver", bold=True)
        inner = max(visible_width(text) for text in lines + [head])
        inner = min(max(inner, self.content_width() - 6), avail)
        with self._lock:
            for out in self.box(lines, inner=inner, title=head, color=border):
                self.line("  " + out)


# --------------------------------------------------------------------------
# Task: a real check with an optional spinner
# --------------------------------------------------------------------------

class Task:
    """Context manager for one real check.

    The spinner is shown only while the check runs. Resolve it with
    :meth:`ok`, :meth:`warn`, :meth:`fail` or :meth:`info`; leaving the block
    without resolving it marks the task done (or failed if it raised).
    """

    def __init__(self, ui: Console, label: str, detail: str = "") -> None:
        self.ui = ui
        self.label = label
        self.detail = detail
        self.elapsed_ms = 0.0
        self._kind: Optional[str] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._started = 0.0

    @property
    def kind(self) -> Optional[str]:
        """Final state: ``ok``, ``warn``, ``fail``, ``info`` (None while running)."""
        return self._kind

    def __enter__(self) -> "Task":
        self._started = time.perf_counter()
        if self.ui.animate and not self.ui.quiet:
            self.ui.hide_cursor()
            self._thread = threading.Thread(target=self._spin, daemon=True, name="nexora-spinner")
            self._thread.start()
        return self

    def _spin(self) -> None:
        ui = self.ui
        frames = ui.g["spin"]
        label = self.ui.style(self.label.ljust(Console.LABEL_WIDTH), "silver")
        i = 0
        while True:
            ch = frames[i % len(frames)]
            glyph = ch if ui.fancy else f"[ {ch}  ]"
            detail = fit(self.detail, ui.term_width() - ui._detail_indent() - 1, ui.g["ell"])
            ui.rewrite(f"  {ui.style(glyph, 'cyan', bold=True)} {label} {ui.style(detail, 'mute')}")
            i += 1
            if self._stop.wait(0.08):
                return

    def _stop_spinner(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None

    def _finish(self, kind: str, detail: str, timing: bool = False) -> None:
        if self._kind is not None:
            return
        self._stop_spinner()
        self._kind = kind
        self.elapsed_ms = (time.perf_counter() - self._started) * 1000
        if timing:
            detail = self.ui.join(detail, f"{self.elapsed_ms:.0f} ms")
        if self.ui.quiet and kind != "fail":
            return
        text = self.ui.status_text(kind, self.label, detail)
        prefix = "\r\033[2K" if self.ui.animate else ""
        self.ui.emit(prefix + text + "\n")

    def ok(self, detail: str = "", *, timing: bool = False) -> None:
        self._finish("ok", detail, timing)

    def warn(self, detail: str = "", *, timing: bool = False) -> None:
        self._finish("warn", detail, timing)

    def fail(self, detail: str = "", *, timing: bool = False) -> None:
        self._finish("fail", detail, timing)

    def info(self, detail: str = "", *, timing: bool = False) -> None:
        self._finish("info", detail, timing)

    def __exit__(self, exc_type, exc, tb) -> bool:
        if self._kind is None:
            if exc_type is None:
                self._finish("ok", "done")
            elif issubclass(exc_type, KeyboardInterrupt):
                self._stop_spinner()
                if self.ui.animate:
                    self.ui.write("\r\033[2K")
            else:
                self._finish("fail", f"{exc_type.__name__}: {exc}")
        return False


# --------------------------------------------------------------------------
# Live request log (Werkzeug access log, rendered one line per request)
# --------------------------------------------------------------------------

_ACCESS_RE = re.compile(
    r'^(?P<ip>\S+) - - \[(?P<ts>[^\]]*)\] "(?P<req>.*)" (?P<code>\d{3}|-) (?P<size>\S+)\s*$'
)
_REQUEST_RE = re.compile(r"^(?P<method>[A-Z]+) (?P<target>\S+)(?: HTTP/\d(?:\.\d)?)?$")
_CHANGE_RE = re.compile(r"Detected change in '(?P<file>.+?)', reloading")
_DROP_TOKENS = (
    "Running on",
    "development server",
    "Press CTRL+C",
    "Serving Flask app",
    "Debug mode:",
)


class RequestLog(logging.Handler):
    """Render Werkzeug log records as a compact, colour-coded live stream."""

    _METHOD_COLORS = {
        "GET": "cyan", "HEAD": "cyan", "OPTIONS": "mute",
        "POST": "violet", "PUT": "warn", "PATCH": "warn", "DELETE": "fail",
    }

    def __init__(self, ui: Console, *, show_query: bool = False) -> None:
        super().__init__(level=logging.DEBUG)
        self.ui = ui
        self.show_query = show_query
        self._last_reload = ("", 0.0)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            text = _ANSI_RE.sub("", record.getMessage())
            if record.exc_info:
                text += "\n" + "".join(traceback.format_exception(*record.exc_info))
            for raw in text.splitlines():
                rendered = self._render(raw.rstrip(), record.levelno)
                if rendered is not None:
                    self.ui.emit(rendered + "\n")
        except Exception:  # logging must never break request handling
            self.handleError(record)

    def _render(self, raw: str, level: int) -> Optional[str]:
        ui = self.ui
        if not raw.strip():
            return None
        match = _ACCESS_RE.match(raw)
        if match:
            return self._access(match)
        if any(token in raw for token in _DROP_TOKENS):
            return None  # the launcher prints its own, accurate summary
        change = _CHANGE_RE.search(raw)
        if change:
            try:
                changed = os.path.relpath(change.group("file"))
            except ValueError:
                changed = change.group("file")
            now = time.monotonic()
            if self._last_reload[0] == changed and now - self._last_reload[1] < 1.0:
                return None  # file watchers often fire twice for one save
            self._last_reload = (changed, now)
            return f"  {ui.style('reload', 'violet', bold=True)}  {ui.style(changed + ' changed', 'mute')}"
        text = raw.rstrip()
        if text.lstrip().startswith("* "):
            text = text.lstrip()[2:]
        color = "fail" if level >= logging.ERROR else "warn" if level >= logging.WARNING else "mute"
        return "  " + ui.style(text, color)

    def _access(self, match: "re.Match[str]") -> Optional[str]:
        ui = self.ui
        request = _REQUEST_RE.match(match.group("req"))
        if request is None:
            return None
        method = request.group("method")
        path, _, query = request.group("target").partition("?")
        code = match.group("code")
        status_color = {"1": "ok", "2": "ok", "3": "cyan", "4": "warn", "5": "fail"}.get(code[:1], "mute")

        hidden = ui.style(" ?" + ui.g["ell"], "dim") if query and not self.show_query else ""
        if query and self.show_query:
            path = path + "?" + query
        room = max(12, ui.term_width() - 28) if ui.tty else 10_000
        parts = [
            "  ",
            ui.style(time.strftime("%H:%M:%S"), "dim"),
            "  ",
            ui.style(method.ljust(6), self._METHOD_COLORS.get(method, "mute"), bold=True),
            " ",
            ui.style(code, status_color, bold=True),
            "  ",
            ui.style(fit(path, room, ui.g["ell"]), "silver"),
            hidden,
        ]
        client = match.group("ip")
        if not is_loopback(client):
            parts.append("  " + ui.style(client, "dim"))
        return "".join(parts)


def install_request_log(ui: Console) -> Optional[RequestLog]:
    """Route Werkzeug's access log through the NexOra renderer (terminals only)."""
    if not ui.tty:
        return None
    handler = RequestLog(ui, show_query=env_flag("NEXORA_LOG_QUERY", False))
    logger = logging.getLogger("werkzeug")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return handler


def _silence_flask_banner() -> None:
    """Flask prints its own ' * Serving Flask app' lines; the launcher replaces them."""
    try:
        import flask.cli as flask_cli

        flask_cli.show_server_banner = lambda *args, **kwargs: None  # type: ignore[assignment]
    except Exception:
        pass


# --------------------------------------------------------------------------
# Development server runner
# --------------------------------------------------------------------------

def _announce_ready(
    ui: Console,
    host: str,
    port: int,
    rows: Sequence[Row],
    url: str,
    open_browser: bool,
    panel_kind: str = "ok",
    footer: str = "",
    timeout: float = 25.0,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if port_open(host, port):
            break
        time.sleep(0.12)
    else:
        ui.status("warn", "Server", f"did not accept connections on {format_host(probe_host(host))}:{port}")
        return

    ui.panel("Online", rows, kind=panel_kind, footer=footer)
    ui.section("Live requests")
    if open_browser:
        try:
            webbrowser.open(url, new=0, autoraise=True)
        except Exception:
            ui.status("warn", "Browser", "could not launch the default browser")


def serve(
    ui: Console,
    app: object,
    *,
    host: str,
    port: int,
    debug: bool,
    reloader: bool,
    url: str,
    rows: Sequence[Row],
    quiet_child: bool = False,
    open_browser: bool = False,
    window_title: str = "NexOra",
    warnings: int = 0,
) -> int:
    """Run ``app`` with the Flask development server and the NexOra interface.

    Returns a process exit code. ``quiet_child`` is True inside the Werkzeug
    reloader child process, which only serves requests. ``warnings`` is the
    number of warnings raised during the preflight: it turns the panel border
    amber so they are not missed once the log starts scrolling.
    """
    started = time.monotonic()
    if not quiet_child:
        ui.section("Launch")
        if port_open(host, port):
            ui.fatal(
                "Server",
                f"port {port} is already in use",
                hint="stop the other process, or choose another port with --port / PORT",
            )
            return 1
        if not is_loopback(host):
            ui.status(
                "warn",
                "Network",
                f"listening on {format_host(host)} - reachable from other machines; development server only",
            )
        ui.status("info", "Server", f"starting on {url}")
        ui.set_title(window_title)
        footer = "Press Ctrl+C to stop"
        if warnings:
            footer = ui.join(f"{warnings} warning(s) above", footer)
        threading.Thread(
            target=_announce_ready,
            args=(ui, host, port, rows, url, open_browser, "warn" if warnings else "ok", footer),
            daemon=True,
            name="nexora-ready",
        ).start()

    install_request_log(ui)
    _silence_flask_banner()
    ui.show_cursor()

    failed = False
    try:
        app.run(host=host, port=port, debug=debug, use_reloader=reloader, threaded=True)  # type: ignore[attr-defined]
    except KeyboardInterrupt:
        pass
    except OSError as exc:
        failed = True
        ui.fatal(
            "Server",
            f"cannot bind {format_host(host)}:{port} - {exc.strerror or exc}",
            hint="check that the address exists on this machine and the port is free",
        )
        return 1
    finally:
        if not failed and not quiet_child:
            ui.goodbye(time.monotonic() - started)
    return 0