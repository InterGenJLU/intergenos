"""Forge — InterGenOS System Installer — Entry point.

Usage:
    forge --archives /var/lib/igos/archives [--packages /path/to/packages]
    forge --help
"""

import argparse
import sys
from pathlib import Path

from .frontend.tui import run_installer


def main():
    parser = argparse.ArgumentParser(
        prog="forge",
        description="Forge — InterGenOS System Installer"
    )
    parser.add_argument("--archives", required=True,
                        help="Path to .igos.tar.gz package archives")
    parser.add_argument("--packages",
                        help="Path to packages/ directory (for post-install hooks)")
    parser.add_argument("--version", action="version",
                        version="Forge 0.1.0 (InterGenOS Installer)")

    args = parser.parse_args()

    archive_dir = Path(args.archives)
    if not archive_dir.exists():
        print(f"ERROR: Archive directory not found: {archive_dir}")
        sys.exit(1)

    packages_dir = Path(args.packages) if args.packages else None

    # Must run as root
    import os
    if os.geteuid() != 0:
        print("ERROR: Forge must be run as root.")
        print("  sudo forge --archives /path/to/archives")
        sys.exit(1)

    run_installer(str(archive_dir), str(packages_dir) if packages_dir else None)


if __name__ == "__main__":
    main()
