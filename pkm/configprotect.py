# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
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
        recorded = db.get_original_checksum(rel)

        if not live_path.is_file():
            # Live file is absent. Two sub-cases:
            #   (a) Truly first install — no recorded baseline yet. The
            #       tar deploy installs new content; add_files records
            #       new_sha via the INSERT branch of the ON CONFLICT.
            #       Nothing to plan here.
            #   (b) Stale-baseline state — recorded baseline exists but
            #       live was removed by a previous remove operation
            #       (cmd_upgrade's current remove-then-install pattern
            #       on unedited configs; resolves once install-new-first
            #       ordering lands per Q1). The ON CONFLICT clause
            #       preserves the stale baseline, so without explicit
            #       ratchet here the post-deploy live (new stock) would
            #       diverge from the recorded baseline (old stock) and
            #       the next upgrade would wrongly classify as
            #       user-edited. Plan the ratchet so post-deploy state
            #       is internally consistent regardless of the caller's
            #       upgrade-orchestration shape.
            if recorded is not None:
                update_baselines[rel] = new_sha
            continue

        live_sha = _sha256(str(live_path))

        if recorded is None:
            # No recorded baseline (legacy install / never tracked). The
            # live bytes cannot be attributed: with nothing recorded, the
            # only unedited state we can PROVE is live == incoming stock.
            if live_sha == new_sha:
                # Live already matches the incoming stock — deploy-over is
                # byte-identical; ratchet so the file is tracked from here.
                update_baselines[rel] = new_sha
            else:
                # Live differs from the incoming stock — the content may be
                # the user's. Never overwrite what we cannot attribute:
                # protect live, deliver stock as .pkmnew. Baseline stays
                # unrecorded (conservative, matching the user-edited arm's
                # keep-detecting stance); `pkm refresh-baseline` is the
                # user's explicit adopt path.
                protect.append(rel)
                pkmnew_writes.append(
                    (str(staging_path), str(live_path) + ".pkmnew")
                )
        elif live_sha == recorded:
            # User has not edited. Tar deploy proceeds; baseline
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


def ratchet_baselines(db, update_baselines, commit=True):
    """Update recorded original_checksum for unedited paths after deploy.

    Args:
        db: PackageDB instance.
        update_baselines: dict {rel_path: new_sha256} from
            prepare_config_protection.
        commit: passed through to db.update_original_checksum so the
            upgrade orchestration can call this inside an outer
            BEGIN/COMMIT transaction (commit=False) and have the ratchets
            ride that transaction instead of auto-committing per-row.
            Defaults to True (auto-commit each ratchet) for standalone use.
    """
    for rel, new_sha in update_baselines.items():
        db.update_original_checksum(rel, new_sha, commit=commit)


# The system account databases. When one of these is protected (its live
# content diverges from the shipped stock and cannot be attributed), the stock
# is delivered as a .pkmnew sidecar exactly like any other /etc file — but the
# "mv <path>.pkmnew <path>" accept-the-new-default advice is an ACCOUNT WIPE for
# these four: the stock is the pristine skeleton (root + system accounts only),
# so moving it over a live database erases every real account. The generic
# advice is withheld for these paths and replaced with an explicit refusal.
_ACCOUNT_DB_RELS = ("etc/passwd", "etc/shadow", "etc/group", "etc/gshadow")


def _is_account_db_sidecar(pkmnew_path):
    """True if pkmnew_path is a <account-db>.pkmnew sidecar.

    Matches on the etc/<db> tail so it holds under any live_root prefix
    (real "/" installs and the tmpdir roots the tests use alike).
    """
    if not pkmnew_path.endswith(".pkmnew"):
        return False
    base = pkmnew_path[: -len(".pkmnew")]
    return any(base == rel or base.endswith("/" + rel) for rel in _ACCOUNT_DB_RELS)


def summary_lines(written_pkmnew):
    """Render a multi-line summary for end-of-upgrade output.

    Empty string when no .pkmnew sidecars were written (the common case
    for upgrades that did not touch user-edited config files).

    No block emits a bare `mv <path>.pkmnew <path>` accept step. A protected
    file is protected precisely because its live content could not be
    attributed to us, so the only honest guidance is review-then-merge: diff
    first, merge by hand, then record the result with refresh-baseline. A
    blind move discards the very content the protect arm just preserved, and
    printing it as the headline step made the tool recommend its own failure
    mode (the mv advice on the four healthy boxes' account-DB sidecars would
    have re-created the 2026-07-23 incident by hand).

    Account-database sidecars are reported in a separate block that goes
    further and refuses the move outright: promoting the pristine skeleton
    over a live account database would erase every account (the base-files
    first-install clobber class, inverted into a footgun by the accept step).
    """
    if not written_pkmnew:
        return ""
    account = sorted(p for p in written_pkmnew if _is_account_db_sidecar(p))
    regular = sorted(p for p in written_pkmnew if not _is_account_db_sidecar(p))
    lines = []
    if regular:
        lines.append(
            f"  Configuration files with new defaults pending review ({len(regular)}):"
        )
        for p in regular:
            lines.append(f"    {p}")
        lines.append("  Your live file was KEPT. To adopt the new default, review first:")
        lines.append("    diff <path> <path>.pkmnew")
        lines.append("  then merge the changes you want into <path> and record it:")
        lines.append("    pkm refresh-baseline <path>")
        lines.append(
            "  To keep your file as-is, delete the .pkmnew sidecar at your leisure."
        )
    if account:
        if lines:
            lines.append("")
        lines.append(
            f"  Account databases — new stock delivered as .pkmnew, your live "
            f"files were KEPT ({len(account)}):"
        )
        for p in account:
            lines.append(f"    {p}")
        lines.append(
            "  WARNING: do NOT `mv` these over the live files. The .pkmnew is the"
        )
        lines.append(
            "    pristine skeleton (root + system accounts only); replacing your live"
        )
        lines.append(
            "    account database would erase every real account. Keep the live file;"
        )
        lines.append(
            "    delete the .pkmnew sidecar, and copy across any wanted skeleton rows"
        )
        lines.append("    by hand.")
    return "\n".join(lines)
