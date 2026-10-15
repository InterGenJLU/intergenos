#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Run the installed-system gate tier and write a sealed record of what happened.

The tier in tests/installed/ measures properties that only exist once the
software is installed: real file modes under a real home, the real hardened
unit, the real embedding corpus answering real sentences. This runner is what
turns one of those runs into something a later stage can REFUSE on — a record
that names the machine, names the release it measured, carries the complete
output, lists every gate's outcome, and is sealed so it can be shown to be the
record that was written.

TWO REFUSALS, BOTH BEFORE ANY RECORD EXISTS. They are refusals rather than
failures because a record produced under either condition would be worse than no
record: it would look like evidence.

  1. NOT FROM A SOURCE CHECKOUT. Running with a checkout as the working
     directory risks the source tree shadowing the installed package, and a run
     against source says nothing about what a user receives. Checked twice —
     structurally, by looking for a checkout above the working directory, and by
     MEASUREMENT, by asking a subprocess where `intergen` actually resolves from
     the directory the gates will run in. The measurement is the one that
     matters; the structural check is what catches the mistake early and names
     it plainly.
  2. NOT WITH AN EMPTY TIER. If collection finds no gates, every later stage
     would read "0 failed" as success. A run that measured nothing must refuse
     to describe itself as a run.

A FAILING RUN IS NOT A REFUSAL. When the gates run and some of them fail, that
is a result, and the record is written and sealed exactly as a green one is. The
release gate refuses on it. That distinction is the whole point: this runner
reports, the gate decides.

MEASURED vs DECLARED, kept apart in the record. The release number, the
installed manifest digest, the package path and the machine are READ from the
system. The recipe content_hash cannot be recomputed from an installed system —
it describes the source that produced the archive — so when it is supplied it is
recorded under "declared" and never under "measured", and the record says which
is which rather than presenting both as facts of equal standing.

TWO TIERS, ONE RECORD. Most gates measure the account a person uses — its home,
its session bus, its own state — and read root's instead when they run as root.
A few can only measure as root; they carry the ``root_tier`` marker, which
tests/installed/conftest.py registers. Every launch runs the tier twice, once per
marker selection, and the record says which identity measured each gate:

  * LAUNCHED THROUGH SUDO, the user tier runs as the account sudo was invoked
    from (SUDO_USER, or the account named with --user), with that account's
    home, groups, runtime directory and session bus and none of sudo's
    variables; the root tier runs as root. The record is then copied into place
    by that account, from a staging directory only root can change, so it is
    handed back to the account and root never writes into a directory another
    account controls.
  * LAUNCHED WITHOUT ROOT, both runs measure as the invoking account. The
    root-tier gates cannot measure there and skip; the runner says so in one
    line, and the release gate refuses the record for its undeclared skips. A
    launch that could not measure the root tier never reads as one that did.

A ROOT LAUNCH RUNS ONLY CODE THAT ROOT ALONE CAN CHANGE. Root runs this runner,
pytest's configuration and conftests, every gate in the tier and the project
modules the root conftest imports — all from the tree this runner is in. So a
root launch refuses, in one line naming the path, when the tier is outside that
tree, or when the tree, anything in it, anything a symbolic link in it points
to, or any directory above it can be written by an account other than root. A
directory above the tree that carries the sticky bit is accepted: no other
account can rename or replace root's entry in it. And no interpreter the runner
starts puts its working directory on the import path (-P), so a launch from a
directory another account can write never imports that account's files as root.
The check runs inside the runner, so it cannot vouch for a runner that was
already replaced; what it does is refuse, before any gate runs, the launch form
that would allow that. docs/operations/11-resolving-validation-gates.md gives
the form that passes: the tree exported into a directory only root can write.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import pwd
import shutil
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_USAGE = 2

GATE_ENV = "INTERGENOS_INSTALLED_GATES"
# The shape of the machine a record measures, handed to the gates in the environment
# and written into the record: "fresh" (installed from the release medium) or
# "upgrade" (moved from an earlier release by the package manager).
GATE_SHAPE_ENV = "IGOS_RECORD_SHAPE"
RECORD_SHAPES = ("fresh", "upgrade")
SEAL_NAME = "SHA256SUMS"
SIG_NAME = "SHA256SUMS.asc"
PKM_DB = "/var/lib/igos/pkm.db"
_INSTALLED_PREFIXES = ("/usr/lib/", "/usr/lib64/", "/usr/local/lib/")

# The marker a gate carries when it can only measure as root; registered in
# tests/installed/conftest.py under this exact name.
ROOT_TIER_MARK = "root_tier"
# Each tier: its name in the record, the marker expression that selects it, and
# the capture and report its run writes. The two selections partition the tier.
TIERS = (
    ("user", f"not {ROOT_TIER_MARK}", "pytest-output.txt", "junit.xml"),
    ("root", ROOT_TIER_MARK, "pytest-output-root-tier.txt", "junit-root-tier.xml"),
)
# The variables a root launch sets afresh for the account the user tier measures
# as, after removing root's and sudo's values for them.
_ACCOUNT_ENV = ("HOME", "USER", "LOGNAME", "MAIL", "XDG_RUNTIME_DIR",
                "DBUS_SESSION_BUS_ADDRESS")
# Run as the measured account to hand the sealed record back: it creates the
# output directory itself and copies each sealed file into it from the staging
# directory. Isolated (-I), so nothing in the environment or the working
# directory reaches the copy.
_PLACE = (
    "import os, shutil, sys\n"
    "src, dst, names = sys.argv[1], sys.argv[2], sys.argv[3:]\n"
    "os.makedirs(os.path.dirname(dst), exist_ok=True)\n"
    "os.mkdir(dst, 0o700)\n"
    "for name in names:\n"
    "    shutil.copyfile(os.path.join(src, name), os.path.join(dst, name))\n"
)


def refuse(message: str) -> None:
    print(f"[run-installed-gates] REFUSED: {message}", file=sys.stderr)
    print("[run-installed-gates] No record was written. A record produced under "
          "this condition would look like evidence without being any.",
          file=sys.stderr)
    sys.exit(EXIT_REFUSED)


def _checkout_above(start: Path) -> Path | None:
    """The nearest ancestor that is a git working tree, or None."""
    for d in [start, *start.parents]:
        if (d / ".git").exists():
            return d
    return None


def _resolve_intergen_from(cwd: Path) -> str:
    """Where `import intergen` actually lands when run from ``cwd``.

    Measured in a subprocess rather than reasoned about, because the answer
    depends on sys.path construction that this process has already done
    differently for itself. Started the way the gate runs are, with -P, so it
    measures the import path they will have.
    """
    probe = subprocess.run(
        [sys.executable, "-P", "-c",
         "import intergen, sys; sys.stdout.write(intergen.__file__ or '')"],
        capture_output=True, text=True, cwd=str(cwd), timeout=120)
    if probe.returncode != 0:
        refuse("the installed assistant package could not be imported from the "
               f"directory the gates would run in ({cwd}): "
               f"{probe.stderr.strip() or 'no error text'}")
    return probe.stdout.strip()


def _installed_identity() -> dict:
    """Release and manifest digest of the INSTALLED assistant package."""
    if not Path(PKM_DB).is_file():
        refuse(f"{PKM_DB} is absent, so the installed release cannot be read. "
               f"A record that cannot name the release it measured is not a "
               f"record of anything.")
    try:
        con = sqlite3.connect(f"file:{PKM_DB}?immutable=1", uri=True)
        row = con.execute(
            "SELECT version, release, manifest_sha256, install_date "
            "FROM installed WHERE name = ? AND superseded_by IS NULL",
            ("intergen",)).fetchone()
    except sqlite3.Error as e:
        refuse(f"the package database could not be read ({e}).")
    if row is None:
        refuse("the assistant package is not recorded as installed on this "
               "machine, so there is nothing here to validate.")
    version, release, manifest_sha256, install_date = row
    return {
        "intergen_version": version,
        "intergen_release": int(release),
        "installed_manifest_sha256": manifest_sha256,
        "install_date": install_date,
    }


def _os_release() -> dict[str, str]:
    out: dict[str, str] = {}
    path = Path("/etc/os-release")
    if not path.is_file():
        refuse("/etc/os-release is absent; this machine cannot be identified.")
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _parse_junit(path: Path) -> list[dict]:
    """Per-gate outcomes, read from pytest's own report rather than from its
    printed summary — a summary line is a rendering, the report is the data."""
    gates: list[dict] = []
    tree = ET.parse(path)
    for case in tree.iter("testcase"):
        file_part = case.get("file") or case.get("classname") or ""
        test_id = f"{file_part}::{case.get('name')}"
        outcome, reason = "passed", ""
        for child in case:
            if child.tag == "failure":
                outcome = "failed"
                reason = (child.get("message") or "").strip()
            elif child.tag == "error":
                outcome = "error"
                reason = (child.get("message") or "").strip()
            elif child.tag == "skipped":
                outcome = "skipped"
                reason = (child.get("message") or "").strip()
        gates.append({"id": test_id, "outcome": outcome, "reason": reason})
    return gates


def _another_can_write(path: Path, *, above_tree: bool) -> str | None:
    """Why an account other than root could change ``path``, or None.

    Owned by anyone but root, the owner can change it. Writable by its group or
    by every account, they can. A symbolic link's own mode means nothing; the
    caller follows it. Above the tree, a directory with the sticky bit is
    accepted: no other account can rename or replace root's entry in it.
    """
    st = os.lstat(path)
    if st.st_uid != 0:
        return f"is owned by uid {st.st_uid}, not root"
    if stat.S_ISLNK(st.st_mode):
        return None
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        if above_tree and stat.S_ISDIR(st.st_mode) and st.st_mode & stat.S_ISVTX:
            return None
        return "can be written by its group or by every account"
    return None


def _chain_problem(path: Path) -> str | None:
    """The first directory from / down to ``path``'s parent that another
    account could change, as one line; None when there is none."""
    for d in reversed(path.parents):
        try:
            why = _another_can_write(d, above_tree=True)
        except FileNotFoundError:
            # Nothing below an absent directory exists either, and only an
            # account that can write the directory above could create it.
            return None
        if why:
            return f"{d} {why}"
    return None


def _root_launch_problem(tree: Path, tier: Path) -> str | None:
    """Why a root launch from ``tree`` would run code another account could
    change, as one line naming the path; None when it would not."""
    if tree != tier and tree not in tier.parents:
        return (f"the tier {tier} is outside the tree this runner is in "
                f"({tree}); a root launch runs the tier of its own tree")
    problem = _chain_problem(tree)
    if problem:
        return problem
    for dirpath, dirnames, filenames in os.walk(tree):
        dirnames.sort()
        here = Path(dirpath)
        why = _another_can_write(here, above_tree=False)
        if why:
            return f"{here} {why}"
        for name in sorted(filenames) + [d for d in dirnames
                                         if (here / d).is_symlink()]:
            entry = here / name
            why = _another_can_write(entry, above_tree=False)
            if why:
                return f"{entry} {why}"
            if entry.is_symlink():
                target = Path(os.path.realpath(entry))
                problem = _chain_problem(target)
                if problem is None and target.exists():
                    why = _another_can_write(target, above_tree=False)
                    problem = f"{target} {why}" if why else None
                if problem:
                    return f"{problem} (the target of {entry})"
    return None


def _account(requested: str | None) -> "tuple[pwd.struct_passwd, bool]":
    """The account the user tier measures as, and whether this is a root launch.

    Launched as root, the user tier must still measure the account a person
    uses, so the runner is told which one: --user, or the account sudo was
    invoked from. Root itself is refused there, because measuring the user tier
    as root is the failure the two tiers exist to end. Launched as anyone else,
    the runner can only measure as that account, and refuses to be asked for
    another.
    """
    euid = os.geteuid()
    if euid == 0:
        name = requested or os.environ.get("SUDO_USER") or ""
        if not name:
            refuse("launched as root with no account to measure the user tier "
                   "as. Launch through sudo from the account the gates are "
                   "about, or name that account with --user.")
        try:
            account = pwd.getpwnam(name)
        except KeyError:
            refuse(f"the account {name!r} is not in the password database, so "
                   f"the user tier has no account to measure as.")
        if account.pw_uid == 0:
            refuse(f"the user tier measures the account a person uses, and "
                   f"{name!r} is root (uid 0). Launch through sudo from that "
                   f"account, or name it with --user.")
        return account, True
    try:
        account = pwd.getpwuid(euid)
    except KeyError:
        refuse(f"the launching uid {euid} has no entry in the password "
               f"database, so the record could not name who measured it.")
    if requested is not None and requested != account.pw_name:
        refuse(f"--user {requested} names another account, and only a root "
               f"launch can measure as another account; this launch is "
               f"{account.pw_name} (uid {euid}).")
    return account, False


def _self_identity() -> dict:
    """This process's identity: what a run started without a change of account
    measures as."""
    euid = os.geteuid()
    return {"user": pwd.getpwuid(euid).pw_name, "uid": euid,
            "gid": os.getegid(), "groups": sorted(set(os.getgroups()))}


def _account_identity(account: pwd.struct_passwd) -> dict:
    """The identity a root launch gives the user tier: the account's own uid,
    primary group and supplementary groups, as a login would."""
    return {"user": account.pw_name, "uid": account.pw_uid,
            "gid": account.pw_gid,
            "groups": sorted(set(os.getgrouplist(account.pw_name,
                                                 account.pw_gid)))}


def _account_env(account: pwd.struct_passwd) -> dict[str, str]:
    """The account's own home, names, runtime directory and session bus."""
    runtime = f"/run/user/{account.pw_uid}"
    return {"HOME": account.pw_dir, "USER": account.pw_name,
            "LOGNAME": account.pw_name, "XDG_RUNTIME_DIR": runtime,
            "DBUS_SESSION_BUS_ADDRESS": f"unix:path={runtime}/bus"}


def _run_tier(name: str, selection: str, capture_name: str, junit_name: str, *,
              tier: Path, cwd: Path, staging: Path, env: dict[str, str],
              set_env: dict[str, str], measured_as: dict,
              account: "pwd.struct_passwd | None") -> dict:
    """One pytest run over the tier, restricted to one marker selection.

    With ``account`` the run measures as that account: the child changes to its
    uid, primary and supplementary groups before pytest starts. The staging
    directory is root's own, so for that run it is opened to traversal only
    (0711) and the report file is created in advance and given to the account —
    the one file in it the account may write. Returns the run's entry for the
    record.
    """
    junit = staging / junit_name
    argv = [sys.executable, "-P", "-m", "pytest", "-q", "-p", "no:cacheprovider",
            "-m", selection, f"--junitxml={junit}", str(tier)]
    change: dict = {}
    if account is not None:
        change = {"user": account.pw_uid, "group": account.pw_gid,
                  "extra_groups": measured_as["groups"]}
        os.chmod(staging, 0o711)
        fd = os.open(junit, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o644)
        try:
            os.fchown(fd, account.pw_uid, account.pw_gid)
        finally:
            os.close(fd)
    try:
        run = subprocess.run(argv, capture_output=True, text=True,
                             cwd=str(cwd), env=env, timeout=7200, **change)
    finally:
        if account is not None:
            os.chmod(staging, 0o700)
    (staging / capture_name).write_text(run.stdout + run.stderr,
                                        encoding="utf-8")
    return {"tier": name, "measured_as": measured_as, "argv": argv,
            "env": set_env, "returncode": run.returncode,
            "capture": capture_name, "junit": junit_name}


def _read_report(staging: Path, name: str, junit_name: str) -> list[dict]:
    """The tier's per-gate outcomes, or a refusal: a report that is absent,
    unreadable or empty measured nothing that can be recorded."""
    junit = staging / junit_name
    gates = None
    if junit.is_file():
        try:
            gates = _parse_junit(junit)
        except ET.ParseError:
            gates = None
    if gates is None:
        shutil.rmtree(staging, ignore_errors=True)
        refuse(f"pytest wrote no readable report for the {name} tier, so no "
               f"per-gate outcome can be read.")
    if not gates:
        shutil.rmtree(staging, ignore_errors=True)
        refuse(f"the {name} tier's report contains no gates, so nothing was "
               f"measured there.")
    return gates


def _hand_back(staging: Path, out: Path, account: pwd.struct_passwd,
               env: dict[str, str]) -> None:
    """Copy the sealed record into place AS the account it measured.

    The output directory is the account's, so root never writes into it: in a
    directory another account controls, a symbolic link can be put into the
    path between any check and any write, and a root write or chown made
    through it lands wherever the link points. Instead the staged files are
    given to the account inside root's own staging directory, which no other
    account can change, and the account creates the output directory and copies
    them itself. On failure the sealed record is kept in staging and the run
    says where.
    """
    names = sorted(p.name for p in staging.iterdir())
    for name in names:
        os.chown(staging / name, account.pw_uid, account.pw_gid)
        os.chmod(staging / name, 0o600)
    os.chmod(staging, 0o711)
    place = subprocess.run(
        [sys.executable, "-I", "-c", _PLACE, str(staging), str(out.absolute()),
         *names],
        capture_output=True, text=True, cwd="/", env=env, timeout=600,
        user=account.pw_uid, group=account.pw_gid,
        extra_groups=sorted(set(os.getgrouplist(account.pw_name,
                                                account.pw_gid))))
    os.chmod(staging, 0o700)
    if place.returncode != 0:
        detail = (place.stderr or place.stdout or "").strip().splitlines()
        print(f"[run-installed-gates] REFUSED: the sealed record could not be "
              f"handed back to {account.pw_name} at {out}: "
              f"{detail[-1] if detail else 'no error text'}", file=sys.stderr)
        print(f"[run-installed-gates] It is kept, sealed, at {staging} "
              f"(readable by root only).", file=sys.stderr)
        sys.exit(EXIT_REFUSED)
    shutil.rmtree(staging)


def _seal(record_dir: Path) -> str:
    lines = []
    for p in sorted(record_dir.rglob("*")):
        # The signature sidecar signs SHA256SUMS, so it is produced after this
        # file is final and can never be listed inside it. Excluded here and in
        # check-release-validation.py's seal check, which must agree or a signed
        # record would fail its own seal.
        if p.is_file() and p.name not in (SEAL_NAME, SIG_NAME):
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
            lines.append(f"{digest}  {p.relative_to(record_dir)}\n")
    seal = record_dir / SEAL_NAME
    seal.write_text("".join(lines), encoding="utf-8")
    return hashlib.sha256(seal.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the installed-system gates and seal a run record.")
    parser.add_argument("--output", required=True, type=Path,
                        help="directory to write the run record into")
    parser.add_argument("--tier", type=Path, default=None,
                        help="the installed-gate tier directory "
                             "(default: tests/installed beside this script's repo)")
    parser.add_argument("--declare-content-hash", default=None,
                        help="the candidate recipe content_hash, recorded as "
                             "DECLARED — it cannot be measured from an install")
    parser.add_argument("--shape", choices=RECORD_SHAPES, default="fresh",
                        help="the shape of this machine: fresh (installed from the "
                             "release medium; the default) or upgrade (moved from an "
                             "earlier release by the package manager). Written into the "
                             "record and handed to the gates; the identity gate reads it.")
    parser.add_argument("--user", default=None,
                        help="in a root launch, the account the user tier "
                             "measures as (default: the account sudo was invoked "
                             "from); the record is handed back to it")
    args = parser.parse_args(argv)

    cwd = Path.cwd()
    checkout = _checkout_above(cwd)
    if checkout is not None:
        refuse(f"the working directory {cwd} is inside a source checkout "
               f"({checkout}). Run from a directory outside any checkout and "
               f"pass the tier with --tier, so the installed package cannot be "
               f"shadowed by the source tree.")

    account, root_launch = _account(args.user)

    tree = Path(__file__).resolve().parents[1]
    tier = args.tier or (tree / "tests" / "installed")
    tier = tier.resolve()
    if not tier.is_dir():
        refuse(f"the installed-gate tier is not at {tier}.")
    if root_launch:
        problem = _root_launch_problem(tree, tier)
        if problem:
            refuse(f"a root launch runs this tree's code as root, and {problem}. "
                   f"Export the tree into a directory only root can write and "
                   f"launch from there (docs/operations/"
                   f"11-resolving-validation-gates.md, the record section).")

    resolved = _resolve_intergen_from(cwd)
    if not resolved.startswith(_INSTALLED_PREFIXES) or \
            "/site-packages/" not in resolved:
        refuse(f"`import intergen` resolves to {resolved!r}, which is not an "
               f"installed package path — it reads as a source checkout. The "
               f"gates would measure source, not the shipped system.")

    gate_env = {GATE_ENV: "1", GATE_SHAPE_ENV: args.shape,
                "PYTHONDONTWRITEBYTECODE": "1"}
    env = {**os.environ, **gate_env}
    # A root launch's user tier: root's and sudo's values removed, the
    # account's own set in their place.
    account_env = {**_account_env(account), **gate_env}
    user_env = {k: v for k, v in os.environ.items()
                if k not in _ACCOUNT_ENV and not k.startswith("SUDO_")}
    user_env.update(account_env)

    collect = subprocess.run(
        [sys.executable, "-P", "-m", "pytest", "--collect-only", "-q",
         "-p", "no:cacheprovider", str(tier)],
        capture_output=True, text=True, cwd=str(cwd), env=env, timeout=1800)
    collected = sum(1 for line in collect.stdout.splitlines() if "::" in line)
    if collected == 0:
        refuse("the tier collected no gates. Every later stage would read "
               "'0 failed' as success, so a run that measured nothing refuses "
               "to describe itself as a run.\n"
               f"collect-only said:\n{collect.stdout}\n{collect.stderr}")

    out: Path = args.output
    if out.exists():
        refuse(f"{out} already exists. A run record is written once; refusing "
               f"to write over one that may be somebody's evidence.")
    staging = Path(tempfile.mkdtemp(prefix="installed-gate-record-"))
    started = datetime.now(timezone.utc).isoformat()

    self_identity = _self_identity()
    runs: list[dict] = []
    gates: list[dict] = []
    for name, selection, capture_name, junit_name in TIERS:
        as_account = root_launch and name == "user"
        measured_as = (_account_identity(account) if as_account
                       else self_identity)
        runs.append(_run_tier(
            name, selection, capture_name, junit_name, tier=tier, cwd=cwd,
            staging=staging, env=user_env if as_account else env,
            set_env=account_env if as_account else dict(gate_env),
            measured_as=measured_as,
            account=account if as_account else None))
        for gate in _read_report(staging, name, junit_name):
            gate["tier"] = name
            gate["measured_as"] = {"user": measured_as["user"],
                                   "uid": measured_as["uid"]}
            gates.append(gate)
    finished = datetime.now(timezone.utc).isoformat()

    osr = _os_release()
    identity = _installed_identity()
    outcome = {
        "collected": len(gates),
        "passed": sum(1 for g in gates if g["outcome"] == "passed"),
        "failed": sum(1 for g in gates if g["outcome"] == "failed"),
        "skipped": sum(1 for g in gates if g["outcome"] == "skipped"),
        "errors": sum(1 for g in gates if g["outcome"] == "error"),
        # The first run that did not exit 0, so a red tier is never hidden
        # behind a green one; each run's own code is in the invocation.
        "pytest_returncode": next(
            (r["returncode"] for r in runs if r["returncode"] != 0), 0),
    }
    record = {
        "record_version": 2,
        "shape": args.shape,
        "machine": {
            "hostname": socket.gethostname(),
            "os_id": osr.get("ID", ""),
            "os_version_id": osr.get("VERSION_ID", ""),
            "kernel": platform.release(),
        },
        "candidate": {
            "intergen_release": identity["intergen_release"],
            "intergen_content_hash": args.declare_content_hash or "",
            "image_id": osr.get("IMAGE_ID", osr.get("ID", "")),
            "image_build_id": osr.get("IMAGE_VERSION", osr.get("VERSION_ID", "")),
        },
        "measured": {
            "intergen_version": identity["intergen_version"],
            "intergen_release": identity["intergen_release"],
            "installed_manifest_sha256": identity["installed_manifest_sha256"],
            "install_date": identity["install_date"],
            "resolved_package_file": resolved,
        },
        "declared": {
            "intergen_content_hash": args.declare_content_hash or "",
            "note": "a recipe content_hash describes the SOURCE that produced "
                    "the archive and cannot be recomputed from an installed "
                    "system; it is carried here as declared, not measured.",
        },
        "installed_package_path": str(Path(resolved).parent),
        "invocation": {
            "launched_by": {"user": self_identity["user"],
                            "uid": self_identity["uid"],
                            "sudo_user": os.environ.get("SUDO_USER", "")},
            "cwd": str(cwd),
            "tier": str(tier),
            "runs": runs,
        },
        "started_utc": started,
        "finished_utc": finished,
        "outcome": outcome,
        "gates": gates,
        "capture": "pytest-output.txt",
        "junit": "junit.xml",
    }
    (staging / "record.json").write_text(json.dumps(record, indent=2),
                                         encoding="utf-8")
    seal_digest = _seal(staging)

    if root_launch:
        _hand_back(staging, out, account, user_env)
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staging), str(out))
    verdict = "GREEN" if outcome["failed"] == 0 and outcome["errors"] == 0 \
        else "RED"
    print(f"[run-installed-gates] record written: {out}")
    if root_launch:
        print(f"[run-installed-gates] the user tier ran as {account.pw_name} "
              f"(uid {account.pw_uid}), the root tier as root (uid 0); the "
              f"record is handed back to {account.pw_name}")
    else:
        print(f"[run-installed-gates] ROOT TIER NOT MEASURED: launched as "
              f"{account.pw_name} (uid {account.pw_uid}) without root, so the "
              f"root-tier gates ran as {account.pw_name}, where they cannot "
              f"measure, and the release gate refuses this record; launch "
              f"through sudo to measure them")
    print(f"[run-installed-gates] {verdict} — {outcome['collected']} gates: "
          f"{outcome['passed']} passed, {outcome['failed']} failed, "
          f"{outcome['errors']} error, {outcome['skipped']} skipped")
    print(f"[run-installed-gates] release {identity['intergen_release']} on "
          f"{socket.gethostname()}; seal {SEAL_NAME} sha256 {seal_digest}")
    print("[run-installed-gates] This is a report, not a verdict on the "
          "release. scripts/check-release-validation.py decides.")

    # THE RECORD IS NOT YET AN ATTESTATION. Sealing proves the record did not
    # change; it does not say who stands behind it, and anyone who can run this
    # script can produce a sealed record saying anything. The operator's
    # signature over the seal is what a release path requires, and the exact
    # command is printed here rather than left to a document, because the moment
    # the record exists is the moment it is needed.
    print()
    print("[run-installed-gates] NEXT STEP — the operator signs this record, or "
          "no release path will accept it:")
    print(f"    bash scripts/sign-with-gpg.sh --file {out / SEAL_NAME} \\")
    print(f"         --sha256 {seal_digest}")
    print(f"  That writes {out / SIG_NAME}, using the release key on the "
          f"hardware token (PIN, then touch).")
    print("  Until it exists this record is unsigned, and "
          "check-release-validation.py refuses it on every release path.")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
