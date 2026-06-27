# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""``python -m hermes_penfield`` entry point.

Mirrors the ``hermes-penfield`` console script declared in
``[project.scripts]`` so the documented commands work both ways.
"""

from hermes_penfield.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
