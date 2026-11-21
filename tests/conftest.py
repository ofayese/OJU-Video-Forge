"""Shared pytest configuration for the OJU Video Forge test suite.

Portability note (Windows): helper processes started by pytest plugins and by
the tooling under test emit UTF-8. CPython decodes ``subprocess`` text output
using ``locale.getpreferredencoding(False)``, which on a Windows host is the
ANSI code page (cp1252). Bytes that are undefined there (for example the
spinner glyphs in a progress bar) raise ``UnicodeDecodeError`` inside the
subprocess reader thread, which surfaces later as
``PytestUnhandledThreadExceptionWarning`` and makes an otherwise green run exit
non-zero.

Every ``text=True`` call site in ``app``, ``scripts`` and ``tests`` now passes
``encoding="utf-8"`` explicitly with ``errors="replace"`` so that a genuinely
mis-encoded byte degrades to a visible replacement character instead of losing
data or crashing. The environment keys below keep third-party helpers that we
do not control on the same encoding.
"""

from __future__ import annotations

import os

# Set before any plugin or fixture imports a subprocess helper.
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")
