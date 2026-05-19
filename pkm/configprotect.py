"""pkm configprotect — .pkmnew sidecar logic for /etc/* config files.

Handles the Q4 (O-006 + O-021) config-file protection pattern: when an
upgrade ships a new version of a tracked /etc/* file, check if the live
file matches the recorded baseline (database.original_checksum). If
unedited, the new stock deploys normally and the baseline ratchets
forward. If user-edited, the new content writes to <path>.pkmnew next
to the live file, the live file stays untouched, and the baseline does
NOT ratchet (so subsequent upgrades continue to detect the user's edits).

This module owns the orchestration logic. The DB primitives
(get_original_checksum / update_original_checksum / refresh_baseline)
live in pkm.database. The CLI surface (pkm refresh-baseline) lives in
pkm.cli. The upgrade orchestration (which wires all three) is the
caller's responsibility.

Three-step API designed for use from cmd_upgrade:

    plan = prepare_config_protection(staging, file_list, live_root, db)
    # plan["protect"] — paths to EXCLUDE from the tar deploy invocation
    #                   (user-edited; live preserved verbatim)
    # plan["update_baselines"] — dict {path: new_sha} of unedited paths
    #                            whose baseline ratchets after deploy
    # plan["pkmnew_writes"] — list of (staging_src, live_pkmnew_dest)
    #                        tuples for materialize_pkmnew_sidecars

    # ... deploy tar with --exclude for plan["protect"] entries ...

    written = materialize_pkmnew_sidecars(plan["pkmnew_writes"])
    ratchet_baselines(db, plan["update_baselines"])
    summary = summary_lines(written)
"""

import os
import shutil
from pathlib import Path

from .database import _sha256


def prepare_config_protection(staging, file_list, live_root, db):
    """Compare archive /etc/* paths against recorded baselines + live files.

    Args:
        staging: Path or str — extracted-archive staging directory.
        file_list: list of relative paths (no leading slash; dirs end in "/")
            that the archive will deploy. Same shape as installer.py's
            file_list.
        live_root: Path or str — install root (typically "/").
        db: PackageDB instance.

    Returns:
        dict with three fields:
          protect: list[str] — relative paths to EXCLUDE from the deploy
            tar invocation. For each of these the live file must remain
            untouched (user-edited content preserved), and a .pkmnew
            sidecar is written from staging after deploy.
          update_baselines: dict[str, str] — {path: new_sha256} for paths
            that were unedited and will deploy normally. The upgrade
            orchestration calls ratchet_baselines(db, this) after deploy
            so subsequent upgrades treat the new stock as the baseline.
          pkmnew_writes: list[tuple[str, str]] — (staging_src, live_dest)
            tuples passed to materialize_pkmnew_sidecars after deploy.
    """
    staging = Path(staging)
    live_root = Path(live_root)
    protect = []
    update_baselines = {}
    pkmnew_writes = []

    for rel in file_list:
        if rel.endswith("/"):
            continue
        if not rel.startswith("etc/"):
            continue
        staging_path = staging / rel
        live_path = live_root / rel
        if not staging_path.is_file():
            continue  # symlink or special; tar handles natively

        new_sha = _sha256(str(staging_path))

        if not live_path.is_file():
            # First install of this config path. The tar deploy installs
            # the new file; the add_files / config_files INSERT records
            # new_sha as the original_checksum via the normal install path.
            # No .pkmnew protection logic needed.
            continue

        recorded = db.get_original_checksum(rel)
        live_sha = _sha256(str(live_path))

        if recorded is None or live_sha == recorded:
            # User has not edited (or no recorded baseline — treat as
            # unedited for upgrade purposes). Tar deploy proceeds; baseline
            # ratchets forward to new_sha via ratchet_baselines after deploy.
            update_baselines[rel] = new_sha
        else:
            # User edited. Protect live file from tar overwrite; write
            # new stock as .pkmnew sidecar after deploy. Baseline stays
            # at recorded value so subsequent upgrades continue to
            # detect the edit.
            protect.append(rel)
            pkmnew_writes.append(
                (str(staging_path), str(live_path) + ".pkmnew")
            )

    return {
        "protect": protect,
        "update_baselines": update_baselines,
        "pkmnew_writes": pkmnew_writes,
    }


def materialize_pkmnew_sidecars(pkmnew_writes):
    """Copy staging→<live>.pkmnew for each protected path.

    Args:
        pkmnew_writes: list of (staging_src, live_pkmnew_dest) tuples
            from prepare_config_protection.

    Returns:
        list[str] — live-side .pkmnew paths actually written. Used by the
        caller for end-of-upgrade batch summary output.
    """
    written = []
    for src, dest in pkmnew_writes:
        try:
            # Parent directory must exist; tar would have created it but
            # for protected paths the parent may not yet — defensive mkdir.
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(src, dest)
            written.append(dest)
        except (OSError, IOError) as e:
            print(f"  WARNING: failed to write .pkmnew sidecar at {dest}: {e}")
    return written


def ratchet_baselines(db, update_baselines):
    """Update recorded original_checksum for unedited paths after deploy.

    Args:
        db: PackageDB instance.
        update_baselines: dict {rel_path: new_sha256} from
            prepare_config_protection.
    """
    for rel, new_sha in update_baselines.items():
        db.update_original_checksum(rel, new_sha)


def summary_lines(written_pkmnew):
    """Render a multi-line summary for end-of-upgrade output.

    Empty string when no .pkmnew sidecars were written (the common case
    for upgrades that did not touch user-edited config files).
    """
    if not written_pkmnew:
        return ""
    lines = [
        f"  Configuration files with new defaults pending review ({len(written_pkmnew)}):"
    ]
    for p in sorted(written_pkmnew):
        lines.append(f"    {p}")
    lines.append("  To accept the new default:")
    lines.append("    mv <path>.pkmnew <path>")
    lines.append("    pkm refresh-baseline <path>")
    lines.append(
        "  To keep your edits, simply delete the .pkmnew sidecar at your leisure."
    )
    return "\n".join(lines)
