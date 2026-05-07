#!/usr/bin/env python3
"""InterGenOS Host System Requirements Check

Validates that the build host meets all LFS 13.0 minimum requirements
before attempting a build. Replaces the original req_check.sh from
build_003 with proper Python, structured output, and clear diagnostics.

Usage:
    python3 scripts/host-check.py              # Check local system
    python3 scripts/host-check.py --remote     # Check VM via SSH
"""

import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# LFS 13.0 minimum requirements (Section 2.2)
# ---------------------------------------------------------------------------

@dataclass
class Requirement:
    """A single host system requirement."""
    name: str
    min_version: str
    command: str                      # Shell command to get version string
    version_regex: str = ""           # Regex to extract version number
    symlink_check: str = ""           # Path that should be a symlink
    symlink_target: str = ""          # Expected symlink target (substring)
    max_version: str = ""             # Maximum tested version (warning only)
    notes: str = ""
    required: bool = True


REQUIREMENTS = [
    Requirement(
        name="Bash",
        min_version="3.2",
        command="bash --version | head -1",
        version_regex=r"version (\d+\.\d+)",
        symlink_check="/bin/sh",
        symlink_target="bash",
        notes="/bin/sh must be a link to bash",
    ),
    Requirement(
        name="Binutils",
        min_version="2.13.1",
        command="ld --version | head -1",
        version_regex=r"(\d+\.\d+(?:\.\d+)?)",
        max_version="2.46.0",
        notes="Versions > 2.46.0 not tested by LFS",
    ),
    Requirement(
        name="Bison",
        min_version="2.7",
        command="bison --version | head -1",
        version_regex=r"(\d+\.\d+(?:\.\d+)?)",
        symlink_check="/usr/bin/yacc",
        symlink_target="bison",
        notes="/usr/bin/yacc should link to bison",
    ),
    Requirement(
        name="Coreutils",
        min_version="8.1",
        command="chown --version | head -1",
        version_regex=r"(\d+\.\d+)",
    ),
    Requirement(
        name="Diffutils",
        min_version="2.8.1",
        command="diff --version | head -1",
        version_regex=r"(\d+\.\d+(?:\.\d+)?)",
    ),
    Requirement(
        name="Findutils",
        min_version="4.2.31",
        command="find --version | head -1",
        version_regex=r"(\d+\.\d+(?:\.\d+)?)",
    ),
    Requirement(
        name="Gawk",
        min_version="4.0.1",
        command="gawk --version | head -1",
        version_regex=r"GNU Awk (\d+\.\d+\.\d+)",
        symlink_check="/usr/bin/awk",
        symlink_target="gawk",
        notes="/usr/bin/awk should link to gawk",
    ),
    Requirement(
        name="GCC",
        min_version="5.4",
        command="gcc --version | head -1",
        version_regex=r"(\d+\.\d+\.\d+)",
        max_version="15.2.0",
        notes="Versions > 15.2.0 not tested by LFS",
    ),
    Requirement(
        name="G++",
        min_version="5.4",
        command="g++ --version | head -1",
        version_regex=r"(\d+\.\d+\.\d+)",
    ),
    Requirement(
        name="Grep",
        min_version="2.5.1",
        command="grep --version | head -1",
        version_regex=r"(\d+\.\d+(?:\.\d+)?)",
    ),
    Requirement(
        name="Gzip",
        min_version="1.3.12",
        command="gzip --version | head -1",
        version_regex=r"(\d+\.\d+)",
    ),
    Requirement(
        name="Linux Kernel",
        min_version="5.4",
        command="uname -r",
        version_regex=r"(\d+\.\d+)",
        notes="CONFIG_UNIX98_PTYS must be set to y",
    ),
    Requirement(
        name="M4",
        min_version="1.4.10",
        command="m4 --version | head -1",
        version_regex=r"(\d+\.\d+\.\d+)",
    ),
    Requirement(
        name="Make",
        min_version="4.0",
        command="make --version | head -1",
        version_regex=r"(\d+\.\d+(?:\.\d+)?)",
    ),
    Requirement(
        name="Patch",
        min_version="2.5.4",
        command="patch --version | head -1",
        version_regex=r"(\d+\.\d+(?:\.\d+)?)",
    ),
    Requirement(
        name="Perl",
        min_version="5.8.8",
        command='perl -e "print $^V"',
        version_regex=r"v(\d+\.\d+\.\d+)",
    ),
    Requirement(
        name="Python",
        min_version="3.4",
        command="python3 --version",
        version_regex=r"(\d+\.\d+\.\d+)",
    ),
    Requirement(
        name="Sed",
        min_version="4.1.5",
        command="sed --version | head -1",
        version_regex=r"(\d+\.\d+)",
    ),
    Requirement(
        name="Tar",
        min_version="1.22",
        command="tar --version | head -1",
        version_regex=r"(\d+\.\d+)",
    ),
    Requirement(
        name="Texinfo",
        min_version="5.0",
        command="makeinfo --version | head -1",
        version_regex=r"(\d+\.\d+)",
    ),
    Requirement(
        name="Xz",
        min_version="5.0.0",
        command="xz --version | head -1",
        version_regex=r"(\d+\.\d+\.\d+)",
    ),
    # Image-creation prereqs (used by scripts/create-image.sh during phase_image).
    # Without these, a 12+ hour build can complete only to fail at the final
    # image-packaging step. Catching here saves the cycle.
    Requirement(
        name="qemu-img",
        min_version="0",
        command="qemu-img --version | head -1",
        version_regex=r"version (\d+\.\d+(?:\.\d+)?)",
        notes="qemu-utils package — required to format the qcow2 image",
    ),
    Requirement(
        name="qemu-nbd",
        min_version="0",
        command="qemu-nbd --version | head -1",
        version_regex=r"version (\d+\.\d+(?:\.\d+)?)",
        notes="qemu-utils package — required to attach the qcow2 for partitioning",
    ),
    Requirement(
        name="parted",
        min_version="0",
        command="parted --version | head -1",
        version_regex=r"(\d+\.\d+(?:\.\d+)?)",
        notes="parted package — required to write the GPT partition table",
    ),
    Requirement(
        name="mkfs.ext4",
        min_version="0",
        command="mkfs.ext4 -V 2>&1 | head -1",
        version_regex=r"(\d+\.\d+(?:\.\d+)?)",
        notes="e2fsprogs package — required to format the root filesystem",
    ),
]


# ---------------------------------------------------------------------------
# Version comparison
# ---------------------------------------------------------------------------

def parse_version(version_str: str) -> tuple[int, ...]:
    """Parse a version string into a tuple of ints for comparison."""
    parts = re.findall(r"\d+", version_str)
    return tuple(int(p) for p in parts)


def version_ge(actual: str, minimum: str) -> bool:
    """Check if actual version >= minimum version."""
    return parse_version(actual) >= parse_version(minimum)


def version_le(actual: str, maximum: str) -> bool:
    """Check if actual version <= maximum version."""
    return parse_version(actual) <= parse_version(maximum)


# ---------------------------------------------------------------------------
# Check execution
# ---------------------------------------------------------------------------

def run_command(cmd: str, remote: Optional[str] = None) -> tuple[int, str]:
    """Run a command locally or via SSH. Returns (exit_code, output)."""
    if remote:
        full_cmd = f"ssh {remote} '{cmd}'"
    else:
        full_cmd = cmd

    try:
        result = subprocess.run(
            full_cmd, shell=True, capture_output=True, text=True, timeout=15
        )
        output = (result.stdout + result.stderr).strip()
        return result.returncode, output
    except subprocess.TimeoutExpired:
        return 1, "(command timed out)"
    except Exception as e:
        return 1, f"(error: {e})"


def check_symlink(path: str, expected_target: str, remote: Optional[str] = None) -> tuple[bool, str]:
    """Check if a symlink exists and points to the expected target."""
    code, output = run_command(f"readlink -f {path}", remote)
    if code != 0:
        return False, f"{path} not found"
    if expected_target in output:
        return True, f"{path} -> {output}"
    return False, f"{path} -> {output} (expected {expected_target})"


def check_compilation(remote: Optional[str] = None) -> tuple[bool, str]:
    """Test that gcc and g++ can compile and link a simple program."""
    test_code = 'echo "int main(){}" > /tmp/igos-check.c'
    results = []

    # gcc test
    cmd = f'{test_code} && gcc /tmp/igos-check.c -o /tmp/igos-check && echo "gcc OK" && rm -f /tmp/igos-check /tmp/igos-check.c'
    code, output = run_command(cmd, remote)
    if code == 0 and "gcc OK" in output:
        results.append(("gcc compile+link", True, "OK"))
    else:
        results.append(("gcc compile+link", False, output))

    # g++ test
    cmd = f'{test_code} && g++ /tmp/igos-check.c -o /tmp/igos-check && echo "g++ OK" && rm -f /tmp/igos-check /tmp/igos-check.c'
    code, output = run_command(cmd, remote)
    if code == 0 and "g++ OK" in output:
        results.append(("g++ compile+link", True, "OK"))
    else:
        results.append(("g++ compile+link", False, output))

    return results


def check_library_consistency(remote: Optional[str] = None) -> tuple[bool, str]:
    """Check GMP/MPFR/MPC .la file consistency (all present or all absent)."""
    libs = ["libgmp.la", "libmpfr.la", "libmpc.la"]
    found = []

    for lib in libs:
        cmd = f"find /usr/lib* -name '{lib}' 2>/dev/null | head -1"
        code, output = run_command(cmd, remote)
        found.append(bool(output.strip()))

    if all(found):
        return True, "all present (consistent)"
    elif not any(found):
        return True, "all absent (consistent)"
    else:
        present = [l for l, f in zip(libs, found) if f]
        absent = [l for l, f in zip(libs, found) if not f]
        return False, f"INCONSISTENT — present: {present}, absent: {absent}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    remote = None
    if "--remote" in sys.argv:
        idx = sys.argv.index("--remote")
        # Accept "--remote user@host" form; otherwise fall back to env var
        if idx + 1 < len(sys.argv) and not sys.argv[idx + 1].startswith("-"):
            remote = sys.argv[idx + 1]
        else:
            remote = os.environ.get("INTERGENOS_REMOTE")

    target = f"remote ({remote})" if remote else "local system"

    print("=" * 72)
    print(f"  InterGenOS Host System Requirements Check")
    print(f"  LFS 13.0-systemd minimum requirements")
    print(f"  Target: {target}")
    print("=" * 72)
    print()

    passed = 0
    failed = 0
    warnings = 0

    # --- Tool version checks ---
    print("--- Tool Versions ---\n")

    for req in REQUIREMENTS:
        code, output = run_command(req.command, remote)

        if code != 0 and not output:
            status = "FAIL"
            version = "NOT FOUND"
            detail = ""
            failed += 1
        else:
            # Extract version
            match = re.search(req.version_regex, output) if req.version_regex else None
            if match:
                version = match.group(1)
            else:
                version = output[:60]

            # Check minimum
            if match and not version_ge(version, req.min_version):
                status = "FAIL"
                detail = f"(need >= {req.min_version})"
                failed += 1
            elif match and req.max_version and not version_le(version, req.max_version):
                status = "WARN"
                detail = f"(> {req.max_version} — not tested by LFS)"
                warnings += 1
            else:
                status = "OK"
                detail = ""
                passed += 1

        pad = 16 - len(req.name)
        print(f"  [{status:4s}] {req.name}{' ' * pad}{version}  {detail}")

    # --- Symlink checks ---
    print("\n--- Symlink Checks ---\n")

    for req in REQUIREMENTS:
        if not req.symlink_check:
            continue
        ok, detail = check_symlink(req.symlink_check, req.symlink_target, remote)
        status = "OK" if ok else "FAIL"
        if ok:
            passed += 1
        else:
            failed += 1
        print(f"  [{status:4s}] {detail}")

    # --- Compilation tests ---
    print("\n--- Compilation Tests ---\n")

    for name, ok, detail in check_compilation(remote):
        status = "OK" if ok else "FAIL"
        if ok:
            passed += 1
        else:
            failed += 1
        print(f"  [{status:4s}] {name}: {detail}")

    # --- Library consistency ---
    print("\n--- Library Consistency (GMP/MPFR/MPC) ---\n")

    ok, detail = check_library_consistency(remote)
    status = "OK" if ok else "FAIL"
    if ok:
        passed += 1
    else:
        failed += 1
    print(f"  [{status:4s}] {detail}")

    # --- Hardware ---
    print("\n--- Hardware ---\n")

    code, output = run_command("nproc", remote)
    cores = output.strip() if code == 0 else "unknown"
    code, output = run_command("head -1 /proc/meminfo", remote)
    if code == 0 and output.strip():
        match = re.search(r"(\d+)", output)
        if match:
            ram_kb = int(match.group(1))
            ram = f"{ram_kb // 1048576}G" if ram_kb >= 1048576 else f"{ram_kb // 1024}M"
        else:
            ram = "unknown"
    else:
        ram = "unknown"
    code, output = run_command("stat -f --format=%a_%S /", remote)
    if code == 0 and output.strip() and "_" in output:
        parts = output.strip().split("_")
        if len(parts) == 2:
            free_bytes = int(parts[0]) * int(parts[1])
            disk = f"{free_bytes // (1024**3)}G"
        else:
            disk = "unknown"
    else:
        disk = "unknown"

    print(f"  CPU cores:    {cores}")
    print(f"  RAM:          {ram}")
    print(f"  Free disk:    {disk}")

    if cores != "unknown" and int(cores) < 4:
        print(f"  [WARN] LFS recommends at least 4 cores (have {cores})")
        warnings += 1

    # --- Summary ---
    print(f"\n{'=' * 72}")
    print(f"  RESULTS: {passed} passed, {failed} failed, {warnings} warnings")

    if failed > 0:
        print(f"\n  Host system does NOT meet LFS 13.0 requirements.")
        print(f"  Fix the failures above before attempting a build.")
        print(f"{'=' * 72}\n")
        return 1
    elif warnings > 0:
        print(f"\n  Host system meets requirements with warnings.")
        print(f"  Build should succeed but is outside tested configuration.")
        print(f"{'=' * 72}\n")
        return 0
    else:
        print(f"\n  Host system meets all LFS 13.0 requirements.")
        print(f"  Ready to build InterGenOS.")
        print(f"{'=' * 72}\n")
        return 0


if __name__ == "__main__":
    sys.exit(main())
