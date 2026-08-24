# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Where owner-only state STOPS: the boundaries the permission pass must respect.

``test_private_state.py`` proves the pass tightens what it should. This file
proves the opposite half — that it does not reach anything it does not own, and
that it says so when it cannot look.

Each test here was first run against the behaviour it describes and observed to
FAIL; the measurements are in the lane's red-reproduction capture. The seven
boundaries:

* a per-user tree the user has RELOCATED with a symbolic link is outside the
  home, and a walk that descends through it re-permissions someone else's
  directory (F-01);
* ``chmod(2)`` follows a symbolic link, so reading a mode from one inode and
  writing it to another both leaves the tree and can GRANT a bit the target did
  not have (F-02, F-12);
* the answer cache holds the last answer and the raw model output and is the
  one per-user tree the first pass did not cover (F-03);
* a mount point inside the tree is a different filesystem, or at least a
  different subtree, and belongs to whoever mounted it (F-04);
* a directory the walk cannot READ hides its contents, and reporting that as
  "nothing to do" is the failure shape the project's directives name (F-05);
* a permission pass that runs on every start reverses a sharing decision the
  user made deliberately, which is theirs to make (F-06);
* a path outside every owned tree — a system log directory a privileged daemon
  keeps — is not this module's to tighten (F-11).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from intergen import private_state
from intergen.private_state import (
    DIR_MODE,
    FILE_MODE,
    PrivateRotatingFileHandler,
    harden_user_state,
    harden_user_state_at_startup,
    owned_roots,
    private_dir,
    private_open,
    private_touch,
    private_write_text,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


def mode_of(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    """A throwaway home with every XDG base pointed inside it.

    0755, not 0700, for the reason given in ``test_private_state.py``: a tight
    home would shield every mode beneath it and the assertions would stop
    measuring what this module sets on its own.
    """
    h = tmp_path / "home"
    h.mkdir(mode=0o755)
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("XDG_STATE_HOME", str(h / ".local" / "state"))
    monkeypatch.setenv("XDG_DATA_HOME", str(h / ".local" / "share"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(h / ".config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(h / ".cache"))
    return h


@pytest.fixture
def outside(tmp_path) -> Path:
    """A directory that is NOT under the throwaway home. 0755 deliberately."""
    d = tmp_path / "somewhere-else"
    d.mkdir(mode=0o755)
    return d


# ── F-01 · a relocated tree is outside the home ───────────────────────────


def test_a_symlinked_owned_root_is_refused_and_never_walked(home, outside):
    """Relocating the data directory onto another disk is an ordinary thing.

    The root is a symbolic link, so everything it names is outside the home.
    Skipping the link itself is not enough: the walk that built the entry list
    already descended through it.
    """
    inner = outside / "sub"
    inner.mkdir(mode=0o755)
    victim = inner / "someone-elses-file"
    victim.write_text("payload")
    os.chmod(victim, 0o644)

    share = home / ".local" / "share"
    share.mkdir(parents=True, mode=0o755)
    (share / "intergen").symlink_to(outside)

    report = harden_user_state()

    assert mode_of(victim) == 0o644, (
        "a file outside the home was re-permissioned through a symlinked root"
    )
    assert mode_of(inner) == 0o755, (
        "a directory outside the home was re-permissioned through a symlinked "
        "root"
    )
    assert not report.dirs_changed and not report.files_changed, (
        f"the pass changed paths behind a symlinked root: "
        f"{report.dirs_changed + report.files_changed}"
    )
    assert str(share / "intergen") in report.symlinked_roots, (
        "a symlinked root must be named in its own report field, not folded "
        f"into the per-entry symlink list: {report}"
    )
    assert str(share / "intergen") not in report.roots_present


# ── F-02 and F-12 · chmod follows a symlink ───────────────────────────────


def test_tighten_never_acts_through_a_symlink(home, outside):
    """The mode must be read from and written to the SAME inode."""
    victim = outside / "target"
    victim.write_text("payload")
    os.chmod(victim, 0o644)

    tree = private_dir(private_state.state_dir_path())
    link = tree / "link-to-a-file"
    link.symlink_to(victim)

    with pytest.raises(private_state.SymlinkEncountered):
        private_state._tighten(link)

    assert mode_of(victim) == 0o644, (
        "the mode landed on the symlink's target, outside the tree"
    )


def test_tighten_never_widens_a_mode_through_a_symlink(home, outside):
    """A symlink lstats 0777, and 0777 with group/other removed is 0700.

    Applied to a 0644 target that is a GRANT of owner-execute — the module
    states in its own docstring that nothing here widens a mode.
    """
    victim = outside / "target"
    victim.write_text("payload")
    os.chmod(victim, 0o644)

    tree = private_dir(private_state.state_dir_path())
    link = tree / "link"
    link.symlink_to(victim)
    assert mode_of(link) == 0o777, "a symlink's own mode is 0777 on Linux"

    with pytest.raises(private_state.SymlinkEncountered):
        private_state._tighten(link)

    assert mode_of(victim) == 0o644
    assert not mode_of(victim) & stat.S_IXUSR, (
        "the target gained the owner-execute bit it never had"
    )


def test_create_helpers_leave_a_symlink_target_mode_alone(home, outside):
    """Every create helper, not just the migration, must stop at a symlink."""
    tree = private_dir(private_state.state_dir_path())

    file_target = outside / "file-target"
    file_target.write_text("payload")
    os.chmod(file_target, 0o644)
    dir_target = outside / "dir-target"
    dir_target.mkdir(mode=0o755)

    (tree / "as-open").symlink_to(file_target)
    (tree / "as-touch").symlink_to(file_target)
    (tree / "as-write").symlink_to(file_target)
    (tree / "as-handler").symlink_to(file_target)
    (tree / "as-dir").symlink_to(dir_target)

    with private_open(tree / "as-open", "a"):
        pass
    private_touch(tree / "as-touch")
    private_write_text(tree / "as-write", "text")
    handler = PrivateRotatingFileHandler(str(tree / "as-handler"))
    handler.close()
    private_dir(tree / "as-dir")

    assert mode_of(file_target) == 0o644, (
        "a create helper changed the mode of a file outside the tree"
    )
    assert mode_of(dir_target) == 0o755, (
        "a create helper changed the mode of a directory outside the tree"
    )


# ── F-03 · the answer cache ───────────────────────────────────────────────


def test_the_answer_cache_tree_is_one_of_the_owned_roots(home):
    """The cache holds the last answer AND the raw model output behind it."""
    cache = private_state.cache_dir_path()
    assert cache == home / ".cache" / "intergen"
    assert cache in owned_roots(), (
        f"the answer cache tree is not owned, so neither the migration nor the "
        f"fresh-home gate can see it: {owned_roots()}"
    )


def test_the_answer_cache_is_created_owner_only(home):
    from intergen import cli

    cli._deliver_answer({"response": "the answer",
                         "full_output": "the raw model output"})
    path = cli._last_answer_path()
    assert path.is_file(), "the cache file was not written at all"
    payload = json.loads(path.read_text())
    assert payload["full_output"] == "the raw model output"
    assert mode_of(path) == FILE_MODE, (
        f"the answer cache is {mode_of(path):04o} — readable by every other "
        f"local account"
    )
    assert mode_of(path.parent) == DIR_MODE, (
        f"the answer cache directory is {mode_of(path.parent):04o}"
    )


def test_the_migration_reaches_an_existing_answer_cache(home):
    cache = home / ".cache" / "intergen"
    cache.mkdir(parents=True, mode=0o755)
    stale = cache / "last-answer.json"
    stale.write_text("{}")
    os.chmod(stale, 0o644)

    report = harden_user_state()

    assert mode_of(stale) == FILE_MODE
    assert mode_of(cache) == DIR_MODE
    assert str(cache) in report.roots_present


# ── F-04 · a mount point is not part of the tree ──────────────────────────


_MOUNT_CHILD = r"""
import json, os, subprocess, sys, traceback
os.umask(0o022)
base, tree_root = sys.argv[1], sys.argv[2]
home = os.path.join(base, "home")
bindsrc = os.path.join(base, "bindsrc")
tree = os.path.join(home, ".local", "share", "intergen")
bind_at = os.path.join(tree, "bind")
other_at = os.path.join(tree, "other-fs")
for d in (home, tree, bind_at, other_at, bindsrc):
    os.makedirs(d, exist_ok=True)
    os.chmod(d, 0o755)
open(os.path.join(bindsrc, "inside.txt"), "w").close()
os.chmod(os.path.join(bindsrc, "inside.txt"), 0o644)

# Two mounts, because they fail differently: a bind mount of a directory on the
# SAME filesystem keeps the tree's st_dev, so a device comparison cannot see
# it; a tmpfs has its own device.
try:
    subprocess.run(["mount", "--bind", bindsrc, bind_at], check=True)
    subprocess.run(["mount", "-t", "tmpfs", "tmpfs", other_at], check=True)
except Exception:
    # The fixture itself could not be built. The parent SKIPS on this, so it
    # must be unmistakable and must never be reached after a successful mount.
    sys.stderr.write("---MOUNT-UNAVAILABLE---\n")
    traceback.print_exc(file=sys.stderr)
    raise SystemExit(3)
sys.stderr.write("---MOUNT-READY---\n")
sys.stderr.flush()

try:
    on_other = os.path.join(other_at, "inside.txt")
    open(on_other, "w").close()
    os.chmod(on_other, 0o644)

    devs = {"tree": os.stat(tree).st_dev,
            "bind": os.stat(bind_at).st_dev,
            "other_fs": os.stat(other_at).st_dev}

    sys.path.insert(0, tree_root)
    os.environ["HOME"] = home
    for var in ("XDG_STATE_HOME", "XDG_DATA_HOME", "XDG_CONFIG_HOME",
                "XDG_CACHE_HOME"):
        os.environ.pop(var, None)
    from intergen import private_state as ps
    report = ps.harden_user_state()

    result = {
        "devs": devs,
        "dirs_changed": report.dirs_changed,
        "files_changed": report.files_changed,
        "crossed_filesystems": list(
            getattr(report, "crossed_filesystems", ["<field absent>"])),
        "failures": report.failures,
        "bind_source_mode": "%04o" % (os.lstat(bindsrc).st_mode & 0o7777),
        "bind_file_mode": "%04o" % (
            os.lstat(os.path.join(bindsrc, "inside.txt")).st_mode & 0o7777),
        "other_fs_file_mode": "%04o" % (os.lstat(on_other).st_mode & 0o7777),
    }
    sys.stdout.write("---MOUNT-JSON---\n" + json.dumps(result))
finally:
    subprocess.run(["umount", other_at], check=False)
    subprocess.run(["umount", bind_at], check=False)
"""


def _run_in_mount_namespace(base: Path):
    """Run the mount fixture in an unprivileged user + mount namespace.

    Returns the child's measurements, or a ``pytest.skip``-able reason. The two
    outcomes are told apart by a marker the child writes only AFTER both mounts
    succeed: a machine that cannot make a mount point skips, and a child that
    mounted and then failed for any other reason is a real failure, because a
    skip there would be the silent pass this test exists to prevent.
    """
    if shutil.which("unshare") is None:
        return "unshare(1) is not installed, so no mount point can be made"
    script = base / "mount_child.py"
    script.write_text(_MOUNT_CHILD)
    proc = subprocess.run(
        ["unshare", "--map-root-user", "--mount",
         sys.executable, str(script), str(base), str(_REPO_ROOT)],
        capture_output=True, text=True, timeout=300,
    )
    mounted = "---MOUNT-READY---" in proc.stderr
    marker = "---MOUNT-JSON---\n"
    if marker in proc.stdout:
        return json.loads(proc.stdout.split(marker, 1)[1])
    if not mounted:
        return ("this machine cannot create a mount point in an unprivileged "
                f"user namespace (exit {proc.returncode}): "
                f"{proc.stderr.strip()[-400:]}")
    raise AssertionError(
        "the mount fixture was built but the measurement did not complete — "
        f"exit {proc.returncode}\n--- stdout ---\n{proc.stdout}\n"
        f"--- stderr ---\n{proc.stderr}"
    )


def test_the_walk_stops_at_a_mount_point(tmp_path):
    """Storage someone mounted under the tree belongs to them, not to us.

    Two mounts are made, because they fail differently: a bind mount of a
    directory on the SAME filesystem keeps the same ``st_dev``, so a device
    comparison alone does not see it, while a tmpfs has a different one.
    """
    base = tmp_path / "mountcase"
    base.mkdir()
    outcome = _run_in_mount_namespace(base)
    if isinstance(outcome, str):
        pytest.skip(outcome)

    assert outcome["devs"]["bind"] == outcome["devs"]["tree"], (
        "this fixture is only meaningful while the bind mount shares the "
        f"tree's device: {outcome['devs']}"
    )
    assert outcome["devs"]["other_fs"] != outcome["devs"]["tree"]

    assert outcome["bind_source_mode"] == "0755", (
        f"the mounted directory was re-permissioned: {outcome}"
    )
    assert outcome["bind_file_mode"] == "0644", (
        f"a file on the mounted filesystem was re-permissioned: {outcome}"
    )
    assert outcome["other_fs_file_mode"] == "0644", (
        f"a file on another filesystem was re-permissioned: {outcome}"
    )
    crossed = " ".join(outcome["crossed_filesystems"])
    assert "bind" in crossed and "other-fs" in crossed, (
        f"both mount points must be reported, not silently skipped: {outcome}"
    )


# ── F-05 · a directory the walk cannot read ───────────────────────────────


def test_a_directory_that_cannot_be_read_is_recorded_as_a_failure(home):
    """"Nothing to do" and "could not look" must not report the same way."""
    tree = private_dir(private_state.data_dir_path())
    blocked = tree / "blocked"
    blocked.mkdir(mode=0o755)
    hidden = blocked / "still-world-readable"
    hidden.write_text("payload")
    os.chmod(hidden, 0o644)
    os.chmod(blocked, 0o000)
    try:
        report = harden_user_state()
    finally:
        os.chmod(blocked, 0o755)

    assert report.failures, (
        "an unreadable directory left the report empty, so the caller cannot "
        "tell it from a clean tree"
    )
    assert any(str(blocked) in entry for entry in report.failures), (
        f"the unreadable directory is not named: {report.failures}"
    )
    assert not report.clean, "a tree with an unreadable directory is not clean"


# ── F-06 · the pass runs once, and says what it changed ───────────────────


def test_the_migration_runs_once_and_records_a_marker(home, caplog):
    tree = home / ".local" / "state" / "intergen"
    tree.mkdir(parents=True, mode=0o755)
    loose = tree / "old-transcript"
    loose.write_text("payload")
    os.chmod(loose, 0o644)

    first = harden_user_state_at_startup()
    assert mode_of(loose) == FILE_MODE
    assert first.changed

    marker = private_state.migration_marker_path()
    assert marker.is_file(), "no marker was left, so the next start walks again"
    assert mode_of(marker) == FILE_MODE
    recorded = json.loads(marker.read_text())
    assert str(loose) in recorded["paths_changed"]
    assert recorded["migration"] == private_state.MIGRATION_VERSION


def test_a_file_the_user_re_shares_survives_the_next_start(home):
    """The user's own machine: their change has to stick."""
    tree = home / ".local" / "state" / "intergen"
    tree.mkdir(parents=True, mode=0o755)
    note = tree / "note-the-user-shares"
    note.write_text("payload")
    os.chmod(note, 0o644)

    harden_user_state_at_startup()
    assert mode_of(note) == FILE_MODE

    os.chmod(note, 0o644)          # the user shares it again, deliberately
    second = harden_user_state_at_startup()

    assert mode_of(note) == 0o644, (
        "the second start reversed a sharing decision the user made"
    )
    assert not second.changed


def test_the_startup_line_names_the_paths_it_changed(home, caplog):
    tree = home / ".local" / "state" / "intergen"
    tree.mkdir(parents=True, mode=0o755)
    loose = tree / "old-transcript"
    loose.write_text("payload")
    os.chmod(loose, 0o644)

    with caplog.at_level(logging.INFO, logger="intergen.private_state"):
        harden_user_state_at_startup()

    text = "\n".join(record.getMessage() for record in caplog.records)
    assert str(loose) in text, (
        f"the user cannot tell WHICH files changed from this line: {text}"
    )


# ── F-07 · the provider panel is a first creator ──────────────────────────


def test_provider_config_creates_the_config_directory_owner_only(home,
                                                                 monkeypatch):
    """The dispatch signing key and the web-auth token live in that directory."""
    pytest.importorskip("yaml")
    target = home / ".config" / "intergen" / "config.yml"
    monkeypatch.setenv("INTERGEN_USER_CONFIG", str(target))
    from intergen import provider_config

    provider_config._save({"providers": [{"name": "p", "adapter": "custom"}]})

    assert mode_of(target.parent) == DIR_MODE, (
        f"~/.config/intergen is {mode_of(target.parent):04o} when the provider "
        f"panel is the first thing to create it"
    )
    assert mode_of(target) == FILE_MODE


# ── F-09 · an owned root that is not a directory ──────────────────────────


def test_an_owned_root_that_is_a_regular_file_is_recorded(home):
    config = home / ".config"
    config.mkdir(parents=True, mode=0o755)
    imposter = config / "intergen"
    imposter.write_text("not a directory")
    os.chmod(imposter, 0o644)

    report = harden_user_state()

    assert report.failures, (
        "an owned root that is a regular file was skipped without a word"
    )
    assert any(str(imposter) in entry and "directory" in entry
               for entry in report.failures), (
        f"the reason is not stated: {report.failures}"
    )


# ── F-10 · the mode is set at creation, for every owned component ─────────


def test_owned_intermediate_directories_are_created_at_the_final_mode(home,
                                                                      monkeypatch):
    """``mkdir(mode=..., parents=True)`` applies the mode to the LEAF only."""
    issued: list[tuple[str, str]] = []
    real_mkdir = os.mkdir

    def spy(path, mode=0o777, *args, **kwargs):
        issued.append((str(path), "%04o" % mode))
        return real_mkdir(path, mode, *args, **kwargs)

    monkeypatch.setattr(os, "mkdir", spy)
    private_dir(private_state.data_dir_path() / "sessions")

    owned = str(private_state.data_dir_path())
    for path, mode in issued:
        if path == owned or path.startswith(owned + os.sep):
            assert mode == "%04o" % DIR_MODE, (
                f"an InterGen-owned directory was created {mode} and tightened "
                f"afterwards, which is the window this module exists to close: "
                f"{issued}"
            )


# ── F-11 · outside every owned tree is not ours ───────────────────────────


def test_private_dir_does_not_tighten_a_path_outside_every_owned_root(home,
                                                                      outside):
    """A privileged daemon keeps its log directory in /var/log/intergen.

    That directory is root-owned and group-readable by design; taking it to
    0700 removes access this module was never asked to remove.
    """
    system_log = outside / "intergen"
    system_log.mkdir(mode=0o755)

    private_dir(system_log)

    assert mode_of(system_log) == 0o755, (
        "a directory outside every owned tree was tightened"
    )
