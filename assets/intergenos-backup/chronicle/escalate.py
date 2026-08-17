# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Restore capability escalation (spec §6, §16.2).

Capture and the always-on sentinel run at a deliberately LOW capability set
(`CAP_DAC_READ_SEARCH` only — read every user's home, write nothing outside the
store). Restore needs MORE: it recreates files with their recorded ownership and
mode (`_apply_meta` -> `os.chown`/`os.chmod`), which requires
`CAP_CHOWN`/`CAP_FOWNER`/`CAP_DAC_OVERRIDE`. Granting those to the always-on
daemon would defeat the point of the low set, so restore runs in a SEPARATE,
higher-capability transient unit — `chronicle-restore@<id>.service`.

This module is the single seam that decides between the two paths:

  * A caller that ALREADY holds `CAP_CHOWN` (a root `chronicle` CLI, and the
    restore unit itself) performs the restore in-process.
  * A caller that lacks it (the low-cap `chronicled.service` handling a socket
    "restore" verb) stages the request under /run/chronicle and starts the
    higher-capability unit, then reads the result back.

`engine.restore_apply` consults `has_cap_chown()` at its top and delegates here
when the capability is absent, so both clients (CLI and GUI) escalate
transparently through the same path and neither embeds the policy.
"""

import json
import os
import subprocess
import uuid
from pathlib import Path

from . import paths as _paths

# CAP_CHOWN is capability number 0 -> bit 0 of the effective capability mask.
_CAP_CHOWN_BIT = 1 << 0

# The template unit that carries the higher restore capability set.
RESTORE_UNIT_TEMPLATE = "chronicle-restore@{id}.service"


def has_cap_chown(status_path="/proc/self/status"):
    """True when this process's EFFECTIVE capability set includes CAP_CHOWN.

    Read from /proc/self/status CapEff so it reflects the systemd
    CapabilityBoundingSet the unit actually granted — euid==0 is NOT sufficient
    (the always-on daemon runs as root but with CAP_CHOWN dropped). Falls back
    to a euid check only when CapEff is unreadable (a non-Linux/proc-less test
    host), where a real restore would run as root anyway.
    """
    try:
        with open(status_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("CapEff:"):
                    mask = int(line.split()[1], 16)
                    return bool(mask & _CAP_CHOWN_BIT)
    except (OSError, ValueError, IndexError):
        pass
    try:
        return os.geteuid() == 0
    except AttributeError:  # pragma: no cover - non-POSIX
        return False


def _new_request_id():
    return uuid.uuid4().hex


def run_restore_via_unit(layer, version_id, paths_, mode="replace-confirm",
                         runtime_dir=None, runner=None, request_id=None):
    """Stage a restore request and run it in the higher-capability unit.

    Writes the request as JSON under the runtime dir, starts
    `chronicle-restore@<id>.service --wait`, reads the result the unit writes
    beside the request, and cleans both up. `runner` (defaults to a
    `systemctl start --wait` subprocess call) and `runtime_dir` are injectable
    for tests so the seam is exercised without a live systemd.
    """
    rid = request_id or _new_request_id()
    rt = Path(runtime_dir) if runtime_dir else _paths.RUNTIME_DIR
    rt.mkdir(parents=True, exist_ok=True)
    req_path = rt / f"restore-{rid}.json"
    res_path = rt / f"restore-{rid}.result.json"
    request = {
        "layer": layer,
        "version_id": version_id,
        "paths": list(paths_),
        "mode": mode,
    }
    # 0600 — the request names paths a restore will write; root-only.
    fd = os.open(str(req_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(request, f)
    try:
        unit = RESTORE_UNIT_TEMPLATE.format(id=rid)
        if runner is None:
            runner = _systemctl_runner
        runner(unit)
        if not res_path.exists():
            raise RestoreEscalationError(
                f"restore unit {unit} produced no result at {res_path}")
        result = json.loads(res_path.read_text(encoding="utf-8"))
        return result
    finally:
        for p in (req_path, res_path):
            try:
                p.unlink()
            except OSError:
                pass


def _systemctl_runner(unit):
    proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["systemctl", "start", "--wait", unit],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if proc.returncode != 0:
        raise RestoreEscalationError(
            f"systemctl start --wait {unit} failed "
            f"(rc={proc.returncode}): {proc.stderr.decode(errors='replace').strip()}")


class RestoreEscalationError(Exception):
    pass
