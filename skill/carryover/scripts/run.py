#!/usr/bin/env python3
"""Entry point for the carryover skill.

Works both from the repository (skill/carryover/scripts -> ../../../src) and
from a built skill, where the package is copied next to this file.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
for candidate in (HERE, os.path.join(HERE, "..", "..", "..", "src")):
    if os.path.isfile(os.path.join(candidate, "carryover", "cli.py")):
        sys.path.insert(0, os.path.abspath(candidate))
        break

from carryover.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
