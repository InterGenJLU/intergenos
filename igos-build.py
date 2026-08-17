#!/usr/bin/env python3
"""Wrapper script to run igos-build from any directory.

Usage:
    python3 /mnt/intergenos/igos-build.py [args...]

    Or from /mnt/intergenos:
    python3 -m igos-build [args...]
"""

import os
import sys

# Real-time log output: force line-buffered stdout. The per-tier phase invokes
# us as `python3 igos-build.py ... 2>&1 | tee -a <tier>-build.log`, so our stdout
# is a PIPE, not a TTY — and CPython block-buffers (~8KB) a non-interactive
# stdout by default. That makes a working build look frozen in the log and, if a
# build is killed/OOMs mid-phase, loses the buffered narration of where it got
# to. There is no good reason to block-buffer a build log; stream it. (2026-06-02)
sys.stdout.reconfigure(line_buffering=True)

# Ensure we can find the igos-build package
project_root = os.path.dirname(os.path.abspath(__file__))
os.chdir(project_root)
sys.path.insert(0, project_root)

# Python doesn't allow hyphens in package names with -m,
# so we import the package by manipulating the path
import importlib
pkg = importlib.import_module("igos-build.__main__")
pkg.main()
