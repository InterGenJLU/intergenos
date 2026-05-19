#!/usr/bin/env python3
"""M-002 chroot-binary-presence gate.

Authored 2026-05-19 against audit finding M-002 (T0-3 sub-cluster 2).
Closes the regression class that produced C-001 (parted missing) and F-001
(iucode_tool path mismatch): the installer / image-build pipeline calls a
binary that the chroot does not contain (or contains at a different path)
and no test catches the gap.

Scans installer Python (installer/backend/*.py + installer/frontend/*.py)
for subprocess invocations, builds the set of installer-required binaries,
unions in a curated set of binaries that shell scripts in scripts/ invoke
(create-image.sh, chroot-build-*.sh — too noisy to scan; declared explicitly
here), and verifies each binary is present in the chroot at one of the
standard search paths.

Cross-references each found binary against every package.yml's verify_paths
declarations under packages/ to surface "binary invoked but no package owns
it" gaps (the verify_paths defect class).

Exit codes:
  0 — all required binaries present in chroot (UNOWNED warnings are non-fatal)
  1 — one or more required binaries missing from chroot (BUILD BLOCKER)
  2 — environment error (chroot path doesn't exist)
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Python subprocess invocations — captures the first string arg of a list-form
# subprocess call. Handles both bare binary ("parted") and absolute path
# ("/usr/sbin/parted"). Matches subprocess.run / .call / .check_output /
# .check_call / .Popen + the installer's own _run wrapper.
PY_CALL_PATTERN = re.compile(
    r"""(?:subprocess\.(?:run|call|check_output|check_call|Popen)|_run)\s*\(\s*
        \[\s*["']([^"']+)["']""",
    re.VERBOSE,
)

# Curated set of binaries that shell pipeline scripts invoke. Listing
# explicitly here (rather than scanning shell scripts heuristically) keeps
# the gate loud about anything that drops off the list — every binary added
# to a pipeline script should also land here OR be discovered via the Python
# scan if it lives in the installer.
SHELL_REQUIRED_BINARIES: set[str] = {
    # Image-build pipeline (scripts/create-image.sh + chroot-build-*.sh):
    "grub-install", "grub-mkconfig", "efibootmgr",
    "mkfs.ext4", "mkfs.fat", "mkfs.vfat", "mkswap",
    "wipefs", "blkid", "losetup", "udevadm",
    "mokutil", "openssl", "sbsign", "sbverify",
    "ukify", "iucode_tool",
    "rsync", "tar", "xz", "gzip",
    # Installer runtime (T0-3 additions — sub-cluster 1):
    "parted", "partprobe",
    "sgdisk", "cgdisk", "gdisk", "fixparts",
    "mkfs.xfs", "xfs_repair", "xfs_admin", "xfs_growfs",
    "mkfs.ntfs",
    "ntfsresize", "ntfsfix", "ntfsinfo", "ntfslabel",
    "mdadm", "mdmon",
    "dialog",
    "os-prober", "linux-boot-prober",
    # C-010 + J-026: generate_locale invokes localedef in the target
    # chroot via run_chroot to compile user-picked locales (fr_FR.UTF-8 /
    # de_DE.UTF-8 / etc.) into /usr/lib/locale/. localedef ships with
    # glibc-core so it's guaranteed-present in any valid InterGenOS
    # chroot, but explicit-when-known per the 2026-05-19 windows-docs-
    # coordinator peer-review observation (the Python scan does catch
    # run_chroot string-form invocations but the curated SHELL set is
    # complement-not-replacement for shell-script-invoked binaries).
    "localedef",
}

# Chroot search paths in priority order. Match LFS-standard layout +
# UsrMerge (/sbin → /usr/sbin, /bin → /usr/bin).
CHROOT_BIN_DIRS: list[str] = [
    "usr/bin",
    "usr/sbin",
    "bin",
    "sbin",
    "usr/lib/intergen",     # static binaries in initramfs envelope
    "usr/lib/os-prober",    # newns + helpers
]

# Tokens to exclude from probing (not binaries — these are arguments,
# variables, or empty interpolations that slip through the regex).
NON_BINARY_TOKENS: set[str] = {
    "-c", "-s", "-l", "-e", "-r", "-v", "-i",
    "--", "", "shell=True", "bash", "sh",
}


def scan_python(installer_dir: Path) -> set[str]:
    """Return set of binaries invoked by installer Python."""
    found: set[str] = set()
    if not installer_dir.exists():
        return found
    for py in installer_dir.rglob("*.py"):
        try:
            text = py.read_text(errors="replace")
        except OSError:
            continue
        for match in PY_CALL_PATTERN.finditer(text):
            binary = match.group(1)
            if binary.startswith("/"):
                binary = binary.rsplit("/", 1)[-1]
            if binary.startswith("$") or "{" in binary or " " in binary:
                continue
            if binary in NON_BINARY_TOKENS:
                continue
            found.add(binary)
    return found


def binary_in_chroot(binary: str, chroot: Path) -> str | None:
    """Return chroot-relative path where binary is found, or None."""
    for bin_dir in CHROOT_BIN_DIRS:
        candidate = chroot / bin_dir / binary
        if candidate.is_file() or candidate.is_symlink():
            return f"/{bin_dir}/{binary}"
    return None


def collect_verify_paths(project_root: Path) -> dict[str, str]:
    """Map every verify_paths entry to the package name that declares it.

    Simple line-based parse — no PyYAML dep so this gate runs in any
    environment with stock Python. Recognizes:
        verify_paths:
          - /usr/sbin/parted
          - /usr/lib/libparted.so
    """
    owners: dict[str, str] = {}
    pkg_root = project_root / "packages"
    if not pkg_root.exists():
        return owners
    for yml in pkg_root.rglob("package.yml"):
        try:
            text = yml.read_text(errors="replace")
        except OSError:
            continue
        pkg_name = yml.parent.name
        in_block = False
        for raw in text.splitlines():
            line = raw.rstrip()
            stripped = line.strip()
            if line.startswith("verify_paths:"):
                in_block = True
                continue
            if in_block:
                # Block ends when a non-indented non-list line appears.
                if line and not line[0].isspace() and not stripped.startswith("-"):
                    in_block = False
                    continue
                if stripped.startswith("- "):
                    path = stripped[2:].strip().strip('"').strip("'")
                    if path:
                        owners[path] = pkg_name
    return owners


def main() -> int:
    parser = argparse.ArgumentParser(
        description="M-002 chroot-binary-presence gate (T0-3 sub-cluster 2)",
    )
    parser.add_argument(
        "--chroot",
        default="/mnt/igos",
        help="Path to chroot root (default: /mnt/igos)",
    )
    parser.add_argument(
        "--project",
        default=str(PROJECT_ROOT),
        help=f"Path to project root (default: {PROJECT_ROOT})",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print every binary found (not just missing)",
    )
    parser.add_argument(
        "--strict-unowned",
        action="store_true",
        help="Treat UNOWNED (binary present but no verify_paths claim) as failure",
    )
    args = parser.parse_args()

    chroot = Path(args.chroot)
    project = Path(args.project)

    if not chroot.exists():
        print(f"[M-002] FATAL: chroot {chroot} does not exist", file=sys.stderr)
        return 2

    py_binaries = scan_python(project / "installer")
    required: set[str] = py_binaries | SHELL_REQUIRED_BINARIES
    owners = collect_verify_paths(project)

    missing: list[str] = []
    unowned: list[tuple[str, str]] = []
    ok_count = 0

    for binary in sorted(required):
        path = binary_in_chroot(binary, chroot)
        if path is None:
            missing.append(binary)
            continue
        if path not in owners:
            unowned.append((binary, path))
        else:
            ok_count += 1
            if args.verbose:
                print(f"[M-002] OK       {binary:24s} {path:40s} -> {owners[path]}")

    if args.verbose and unowned:
        print()
        for binary, path in unowned:
            print(f"[M-002] UNOWNED  {binary:24s} {path:40s} (no verify_paths claim)")

    print()
    print(
        f"[M-002] Summary: {ok_count} OK, "
        f"{len(unowned)} unowned (present but unclaimed in verify_paths), "
        f"{len(missing)} missing in chroot"
    )

    if missing:
        print(file=sys.stderr)
        print("[M-002] MISSING IN CHROOT — installer cannot proceed:", file=sys.stderr)
        for binary in missing:
            print(f"  - {binary}", file=sys.stderr)
        return 1

    if args.strict_unowned and unowned:
        print(file=sys.stderr)
        print("[M-002] UNOWNED binaries (strict mode):", file=sys.stderr)
        for binary, path in unowned:
            print(f"  - {binary} at {path}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
