"""Entry point: fix the console encoding, then hand over to the CLI.

This has to happen before anything else imports or prints. A Windows console
defaults to GBK (cp936), and the first box-drawing character from ``rich``, or
the first emoji from a model's output, raises ``UnicodeEncodeError`` and kills
the run. Redirecting is not enough -- ``PYTHONIOENCODING`` is not always set by
the time a package is imported, so the streams are reconfigured directly.
"""

from __future__ import annotations

import contextlib
import sys


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            # Detached or already-wrapped streams raise here; there is nothing
            # useful to do about it, and failing would abort the whole command.
            with contextlib.suppress(ValueError, OSError):
                reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    _force_utf8()

    from .cli import app

    app()


if __name__ == "__main__":
    main()
