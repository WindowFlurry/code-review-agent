"""Entry point: make printing safe, then hand over to the CLI.

The stream **encoding is deliberately left alone**. Python already asks Windows
for the console code page, and that is the encoding the console actually
renders. Forcing UTF-8 here (as an earlier version did) is only correct when
the .bat has switched the console to codepage 65001; on a standard Chinese
Windows console it turns every Chinese character into 乱码.

Only the error handler is changed, so an emoji or a box-drawing character can
never kill the run.
"""

from __future__ import annotations

import contextlib
import sys


def _harden_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            # Detached or already-wrapped streams raise here; there is nothing
            # useful to do about it, and failing would abort the whole command.
            with contextlib.suppress(ValueError, OSError):
                reconfigure(errors="replace")


def main() -> None:
    _harden_streams()

    from .cli import app

    app()


if __name__ == "__main__":
    main()
