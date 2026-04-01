#!/usr/bin/env python3
"""Wrapper script to run igos-build from any directory.

Usage:
    python3 /mnt/intergenos/igos-build.py [args...]

    Or from /mnt/intergenos:
    python3 -m igos-build [args...]
"""

import os
import sys

# Ensure we can find the igos-build package
project_root = os.path.dirname(os.path.abspath(__file__))
os.chdir(project_root)
sys.path.insert(0, project_root)

# Python doesn't allow hyphens in package names with -m,
# so we import the package by manipulating the path
import importlib
pkg = importlib.import_module("igos-build.__main__")
pkg.main()
