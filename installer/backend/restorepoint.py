# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Take a restore point of the system as installed, before the unmount.

Until this step existed, a finished install had nothing to roll back to.
The backup utility's restore-point layer is written by the package
manager before each transaction, so the oldest state a person could
return to was whatever the machine happened to be in when they first
installed a package — and on a machine where nobody installs anything,
there was no restore point at all. Measured on an installed R001.2-03
machine: the install finished at 11:38 and the restore-point history
begins at 13:21, every entry of it taken by a package transaction.

This is deliberately best effort. The backup utility is an optional
package, so a system without it is a skip and not a fault. A capture that
fails is reported as a warning and does not fail an install that is
otherwise complete on disk — but it is never swallowed, because a person
who believes they can roll back and cannot is worse off than one who was
told they cannot.
"""

import json
import os
import shlex

from . import trace

#: The wrapper the package installs; absent means the utility is not installed.
CHRONICLE_BINARY = "/usr/bin/chronicle"

#: The layer that holds system restore points (the package manager's own
#: pre-transaction handler writes to this same layer).
LAYER = "restore-point"

#: Recorded with the version so the timeline says what this one is. The
#: health check on the installed system looks for a restore point at or
#: after the install; this reason is what makes it recognizable to a person
#: reading the timeline.
INSTALL_REASON = "the system as installed"


def _parse(text):
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def take_install_restore_point(target):
    """Capture a restore point on the mounted target and read it back.

    Returns a dict with ``status`` (taken, skipped or failed),
    ``version_id`` and ``detail``. The caller decides what a failure means
    for the install; this function never raises for a tool-level failure.
    """
    installed = os.path.exists(
        os.path.join(target, CHRONICLE_BINARY.lstrip("/")))
    if not installed:
        return {
            "status": "skipped",
            "version_id": None,
            "detail": ("the backup utility is not installed on this system, "
                       "so there is no restore-point store to write to"),
        }

    rc, out, err = trace.traced_run_chroot(
        target,
        f"{CHRONICLE_BINARY} capture {LAYER} --json "
        f"--reason {shlex.quote(INSTALL_REASON)}",
    )
    if rc != 0:
        return {
            "status": "failed",
            "version_id": None,
            "detail": (err or out or "the capture failed with no output").strip(),
        }

    # A zero exit status is the tool's claim about what it did. The state of
    # the machine is what the timeline says, so the version is read back and
    # the one this install wrote is identified by its own reason.
    rc_list, out_list, err_list = trace.traced_run_chroot(
        target, f"{CHRONICLE_BINARY} list {LAYER} --json")
    versions = _parse(out_list) if rc_list == 0 else None
    if not isinstance(versions, list):
        return {
            "status": "failed",
            "version_id": None,
            "detail": ("the capture reported success but the restore-point "
                       "timeline could not be read back"
                       + (f": {err_list.strip()}" if err_list.strip() else "")),
        }

    mine = [v for v in versions
            if isinstance(v, dict) and v.get("reason") == INSTALL_REASON]
    if not mine:
        return {
            "status": "failed",
            "version_id": None,
            "detail": ("the capture reported success but no restore point of "
                       "the system as installed could be read back from the "
                       "timeline"),
        }

    return {
        "status": "taken",
        "version_id": mine[-1].get("version_id"),
        "detail": INSTALL_REASON,
    }
