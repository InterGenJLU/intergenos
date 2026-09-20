# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The higher-capability restore leg can write where every layer's files live,
may apply the ownership it is granted the capability for, and the command line
reports a failed restore in its exit status.

WHY THIS FILE EXISTS. On 2026-09-20 an installed machine took a package restore
point before a release-only upgrade (2402 paths, /usr/bin/forge among them) and
then could not restore a single file from it:

1. chronicle-restore@.service ran under ProtectSystem=strict with a writable
   set of /home, /etc and the media mounts. /usr, /opt, /var and /boot — where
   package files live — were read-only inside the unit, so the restore failed
   with EROFS on a /usr that was mounted read-write. The same restore of a file
   under /etc wrote the file.
2. That /etc restore then died with SIGSYS: the unit denied @privileged, which
   contains @chown, while granting CAP_CHOWN. The bytes had landed; applying the
   recorded ownership killed the process, so the file kept the wrong owner and
   no result was written. A capability and a syscall filter are separate gates.
3. The failed /usr restore exited 0 with the failure only in its printed
   results; the half-done /etc restore exited 1. A script reading the exit
   status was told the opposite of what happened in both cases.

WHAT THESE TESTS PIN.
- Every top-level directory that a shipped package installs into (derived from
  the recipes' verify_paths, plus the FHS directories the merged-usr symlinks
  point at) is inside the restore leg's ReadWritePaths.
- The restore leg's filter re-allows @chown AFTER the ~@privileged deny, and the
  always-on sentinel (chronicled.service) does NOT — the documented asymmetry.
- `chronicle restore` exits non-zero when any named path was not restored, in
  plain and --json output alike, and 0 when every path was.

Nothing here starts a unit. Both unit behaviours were measured on a running
machine with transient units on scratch files (evidence held by the project).
"""

import io
import json
import re
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import pytest

import chronicle.cli as _cli

REPO = Path(__file__).resolve().parents[2]
UNITS = REPO / "assets" / "intergenos-backup" / "systemd"
RESTORE_UNIT = UNITS / "chronicle-restore@.service"
SENTINEL_UNIT = UNITS / "chronicled.service"
PACKAGES = REPO / "packages"

# Where the merged-usr symlinks resolve: a path under /lib is a path under /usr.
MERGED_USR = {"/lib": "/usr", "/lib64": "/usr", "/bin": "/usr", "/sbin": "/usr"}


def _directive_values(unit: Path, key: str):
    """Every assignment of `key`, in file order (systemd merges list directives)."""
    out = []
    for raw in unit.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";", "[")) or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip() == key:
            out.append(v.strip())
    return out


def _writable_prefixes(unit: Path):
    prefixes = []
    for v in _directive_values(unit, "ReadWritePaths"):
        prefixes.extend(p for p in v.split() if p)
    return prefixes


def _top_level_install_dirs():
    """The first path component of every verify_paths entry in every recipe."""
    tops = set()
    for pkg in PACKAGES.glob("*/*/package.yml"):
        text = pkg.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"^verify_paths:\n((?:[ \t]+-.*\n)+)", text, re.M)
        if not m:
            continue
        for line in m.group(1).splitlines():
            p = line.strip().lstrip("-").strip().strip("\"'")
            if p.startswith("/") and len(p) > 1:
                top = "/" + p.split("/")[1]
                tops.add(MERGED_USR.get(top, top))
    return tops


def _covered(path, prefixes):
    return any(path == p or path.startswith(p.rstrip("/") + "/") for p in prefixes)


# --- the writable set covers where every layer's files live ------------------

def test_recipes_name_install_directories():
    tops = _top_level_install_dirs()
    assert {"/usr", "/etc"} <= tops, tops  # the derivation reads real recipes


def test_every_directory_a_package_installs_into_is_writable_by_the_restore_leg():
    prefixes = _writable_prefixes(RESTORE_UNIT)
    missing = sorted(t for t in _top_level_install_dirs() if not _covered(t, prefixes))
    assert not missing, (
        f"package files live under {missing} but chronicle-restore@.service's "
        f"ReadWritePaths ({prefixes}) does not cover them: under ProtectSystem=strict "
        "a restore of any file there fails with a read-only file system, and a package "
        "restore point cannot restore a package."
    )


def test_the_user_data_and_config_layers_stay_writable():
    prefixes = _writable_prefixes(RESTORE_UNIT)
    for p in ("/home", "/etc", "/var/lib/chronicle", "/mnt", "/media", "/run/media"):
        assert _covered(p, prefixes), (p, prefixes)


def test_the_restore_leg_keeps_the_strict_mount_view():
    assert _directive_values(RESTORE_UNIT, "ProtectSystem") == ["strict"]


# --- the ownership calls the capability is granted for are allowed -----------

def test_the_restore_leg_re_allows_chown_after_the_privileged_deny():
    filters = _directive_values(RESTORE_UNIT, "SystemCallFilter")
    assert "~@privileged" in filters, filters
    assert "@chown" in filters, (
        f"chronicle-restore@.service filters {filters}: @chown is a member of @privileged, "
        "so applying a restored file's ownership is answered with SIGSYS and the process is "
        "killed after the bytes land — the capability grant alone does not allow the call."
    )
    assert filters.index("@chown") > filters.index("~@privileged"), (
        "the re-allow must come AFTER the deny; systemd applies the list in order"
    )


def test_the_restore_leg_is_granted_the_capability_the_call_needs():
    caps = " ".join(_directive_values(RESTORE_UNIT, "AmbientCapabilities"))
    assert "CAP_CHOWN" in caps and "CAP_FOWNER" in caps, caps


def test_the_always_on_sentinel_does_not_re_allow_chown():
    filters = _directive_values(SENTINEL_UNIT, "SystemCallFilter")
    assert "~@privileged" in filters, filters
    assert "@chown" not in filters, (
        "the sentinel never chowns (it declares Group= instead); re-allowing @chown there "
        "would widen the always-on daemon for nothing"
    )


# --- the exit status carries the verdict --------------------------------------

def _run(argv, result):
    """Drive the real command with a backend that answers the plan call with a
    plan for every named path and the restore call with `result`."""
    out, err = io.StringIO(), io.StringIO()
    backend = mock.Mock()

    def _call(verb, **kw):
        if verb == "restore-plan":
            return {"actions": [{"action": "restore", "path": p} for p in kw["paths"]]}
        assert verb == "restore", verb
        return result

    backend.call.side_effect = _call
    with mock.patch.object(_cli, "Backend", return_value=backend), \
            redirect_stdout(out), redirect_stderr(err):
        rc = _cli.main(argv)
    return rc, out.getvalue(), err.getvalue()


def _restore_argv(json_mode):
    return ["restore", "restore-point", "v1", "/usr/bin/forge", "--mode", "beside", "-y",
            *(["--json"] if json_mode else [])]


@pytest.mark.parametrize("json_mode", [False, True])
def test_a_restore_that_failed_on_a_path_exits_non_zero(json_mode):
    failed = {"version_id": "v1", "results": [
        {"path": "/usr/bin/forge", "ok": False,
         "reason": "[Errno 30] Read-only file system: '/usr/bin/.tmp-x'"}]}
    rc, out, err = _run(_restore_argv(json_mode), failed)
    assert rc == 1, (rc, out, err)
    if json_mode:
        # The plan lines print before the JSON object; parse from its start.
        assert json.loads(out[out.index("{"):])["results"][0]["ok"] is False
    else:
        assert "Read-only file system" in err


@pytest.mark.parametrize("json_mode", [False, True])
def test_a_restore_that_restored_every_path_exits_zero(json_mode):
    ok = {"version_id": "v1", "results": [
        {"path": "/usr/bin/forge", "ok": True,
         "written_to": "/usr/bin/forge.chronicle-restored-v1"}]}
    rc, out, err = _run(_restore_argv(json_mode), ok)
    assert rc == 0, (rc, out, err)
    assert not err


def test_a_restore_with_one_failed_path_among_several_exits_non_zero():
    mixed = {"version_id": "v1", "results": [
        {"path": "/etc/hostname", "ok": True, "written_to": "/etc/hostname.chronicle-restored-v1"},
        {"path": "/usr/bin/forge", "ok": False, "reason": "not in version"}]}
    rc, out, err = _run(_restore_argv(False), mixed)
    assert rc == 1
    assert "not in version" in err


def test_a_restore_that_restored_nothing_exits_non_zero():
    rc, out, err = _run(_restore_argv(False), {"version_id": "v1", "results": []})
    assert rc == 1
