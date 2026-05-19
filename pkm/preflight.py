"""pkm preflight — Q6 free-disk-space preflight check.

Per the operator-greenlit Q6 design: before any download or extract,
estimate total bytes required and compare against available bytes on
the target filesystem. Refuse the operation when available < required
with a 10% safety margin, to prevent partial extraction onto a full
filesystem from corrupting unrelated files via failed atomic-rename
operations.

The extraction-size multiplier accounts for the fact that compressed
archive bytes expand on disk: a 1 GiB .tar.gz typically extracts to
between 1.5x and 4x its compressed size depending on content. The 1.5x
default is conservative-enough to avoid mid-extract failures on
typical-content archives while not over-rejecting on tight-disk
scenarios. A 100 MiB floor protects very-small upgrades from being
permitted on near-full disks where header overhead alone could fail.

This module provides the helpers; the actual call site sits in the
download/upgrade orchestration which calls into Q6's retry/backoff/
resume + mirror failover logic (windows-host coordinator's lane).
"""

import os
import shutil
from pathlib import Path


# Multiplier applied to compressed archive total to estimate extracted size.
# 1.5x is the conservative working figure for typical-content archives
# (executables, configs, docs, libraries). Highly-compressible content like
# uncompressed shell scripts can expand more; binary packages with already-
# compressed payloads (firmware blobs, PNGs) expand less. The 1.5x estimate
# combined with the 1.1x safety margin in check_free_space gives ~65% headroom
# over compressed size, which covers the common case without over-rejection.
EXTRACTION_MULTIPLIER = 1.5

# Minimum headroom floor in bytes (100 MiB). Even very small upgrades need
# enough scratch space for staging dirs, partial writes, and atomic renames.
# Refusing an operation when disk available falls below this threshold
# prevents extraction races that could corrupt unrelated files.
MIN_HEADROOM_BYTES = 100 * 1024 * 1024

# Safety margin multiplier on the required-bytes estimate before comparing
# against available. Required * 1.1 is the gate.
SAFETY_MARGIN_MULTIPLIER = 1.1


def estimate_required_space(archive_sizes_bytes):
    """Estimate bytes needed on the target filesystem for the operation.

    Args:
        archive_sizes_bytes: iterable of compressed archive sizes (bytes).
            Each entry is the on-disk size of one .igos.tar.gz file the
            operation will download + extract.

    Returns:
        int — estimated required bytes, accounting for the extraction
        multiplier and the minimum headroom floor.
    """
    total = sum(archive_sizes_bytes)
    estimated = int(total * EXTRACTION_MULTIPLIER)
    return max(estimated, MIN_HEADROOM_BYTES)


def check_free_space(required_bytes, target_path):
    """Check whether target_path's filesystem has enough free space.

    Args:
        required_bytes: estimate from estimate_required_space.
        target_path: Path or str — a path on the target filesystem. The
            check uses shutil.disk_usage which inspects the filesystem
            containing the path (so the path must exist).

    Returns:
        dict with four fields:
          ok: bool — True if available >= required * SAFETY_MARGIN_MULTIPLIER
          available_bytes: int — bytes available on the target filesystem
          required_bytes: int — input required_bytes
          required_with_margin: int — required_bytes * SAFETY_MARGIN_MULTIPLIER
    """
    target = Path(target_path)
    # Walk up to the nearest existing ancestor so shutil.disk_usage has
    # something to inspect. /var/cache/pkm/ may not exist on first install
    # of a fresh system, but its parent /var/cache/ does.
    probe = target
    while probe != probe.parent and not probe.exists():
        probe = probe.parent
    usage = shutil.disk_usage(str(probe))
    required_with_margin = int(required_bytes * SAFETY_MARGIN_MULTIPLIER)
    return {
        "ok": usage.free >= required_with_margin,
        "available_bytes": usage.free,
        "required_bytes": required_bytes,
        "required_with_margin": required_with_margin,
    }


def format_preflight_failure(check_result, target_path):
    """Render a user-facing message when check_free_space returns ok=False.

    Args:
        check_result: dict returned by check_free_space.
        target_path: the path that was checked.

    Returns:
        str — multi-line error message suitable for surfacing to the user
        and to the operation's failure return.
    """
    avail_mb = check_result["available_bytes"] // (1024 * 1024)
    req_mb = check_result["required_with_margin"] // (1024 * 1024)
    return (
        f"Insufficient disk space at {target_path}: "
        f"{avail_mb} MiB available, {req_mb} MiB required (with 10% margin). "
        f"Refusing the operation to prevent partial extraction onto a near-full "
        f"filesystem (which could corrupt unrelated files via failed atomic-rename "
        f"operations). Free up space and retry."
    )
