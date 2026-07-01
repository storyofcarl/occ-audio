#!/usr/bin/env python3
"""Launcher so `python occ_audio.py <command>` works from the repo root.

Equivalent to `python -m occ_audio <command>`.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from occ_audio.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
