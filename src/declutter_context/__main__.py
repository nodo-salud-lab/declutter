"""Entry point for ``python -m declutter_context``."""

from __future__ import annotations

import sys

from declutter_context.cli import main

if __name__ == "__main__":
    sys.exit(main())
