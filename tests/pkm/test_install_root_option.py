# SPDX-License-Identifier: GPL-3.0-or-later
"""pkm can install into a root that is not the running system's — proved, not argued.

WHY THIS FILE EXISTS.

pkm's library layer has been able to work against an alternate root for a long
time: PackageDB takes ``root=``, PackageInstaller takes ``root=``, PackageRemover
defaults its root to the database's, the canonical hooks in pkm/hooks.py each
adapt (``depmod -b``, ``ldconfig -r``) or skip when the root is not "/", and the
Forge installer uses all of it to install a whole system into /mnt/target. None
of that is reachable from the command line. The only way to install into another
root today is to write Python, which is what Forge does.

That gap is the subject here. A ``--root`` option that moves SOME of pkm's paths
and leaves others pointing at the running system would be worse than no option at
all: it would report a successful install into a directory while writing the
package database, the text manifest or the archive index onto the live machine.
Rule 21 — a stub is a lie — applies directly. Either every piece of state pkm
writes derives from the root, or the option must not exist.

WHAT THESE TESTS PIN.

1. The option exists on the command line and is answerable without running a
   command. A test that has to invoke pkm to find out whether a flag exists is a
   test that runs the program to ask it a question about itself.
2. Every path that is the ROOT'S OWN STATE derives from the root: the database,
   the text-manifest directory, the archive directory, the helper-manifest
   directory, the lock, the repository cache, the available-updates file, the
   per-package hook directory and the pre-transaction handler directory.
3. The default root reproduces today's paths EXACTLY. A run without the option
   must be byte-identical in its path resolution to the shipped behaviour, or
   this change is a migration rather than an addition.
4. The trust inputs do NOT derive from the root. The repository configuration and
   the signature keyring are read from the RUNNING system. This is the one place
   where "derive everything from the root" would be the wrong answer: a target
   root that is being bootstrapped has no keyring at all, so deriving would leave
   a fresh root with nothing to verify against, and deriving from a target that
   already has one would mean the target decides which keys pkm trusts. Where
   packages are PUT is what the option controls; which signatures pkm BELIEVES is
   not.
5. A subcommand that cannot be honoured under an alternate root refuses, loudly,
   naming itself and the reason — before it changes anything. Half-applying is
   the failure this pins: a command that does the part it can and stays quiet
   about the part it cannot is exactly the silent-degradation class.
6. A package whose own post-install hook cannot be executed under the given root
   is refused BEFORE any file is written. pkm runs a per-package post-install
   hook under ``chroot(root)`` when the root is not "/", which needs a working
   interpreter inside that root. A scratch directory does not have one, and the
   shipped code treats a hook that will not run as a non-fatal warning — correct
   for Forge, where the pipeline has already bind-mounted /dev, /proc and /sys
   into a fully populated target, and wrong for a bare directory, where it means
   the install completes with a step silently skipped.
7. An install into a root records the database, the manifest and the files under
   that root, and touches nothing outside it.

Every test runs in its own tmp_path. None of them touches the live system.
"""

from __future__ import annotations

import importlib
import io
import os
import tarfile
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _rootpaths():
    """The module under test, or a failure that says what is missing.

    Imported through importlib rather than at module scope so that its absence
    is a readable test failure instead of a collection error.
    """
    try:
        return importlib.import_module("pkm.rootpaths")
    except ImportError as exc:  # pragma: no cover - the red state
        pytest.fail(
            "pkm.rootpaths does not exist, so there is nothing that derives "
            "pkm's state paths from an install root: %s" % (exc,)
        )


def _build_archive(tmp, name, version, members=(), pkginfo_extra=()):
    """A minimal, well-formed .igos.tar.gz: .PKGINFO plus payload members.

    members: iterable of (arcname, bytes, mode).
    """
    lines = [
        f"pkgname={name}", f"pkgver={version}", "pkgrel=1",
        "pkgdesc=install-root test package", "license=GPL", "tier=core",
        "builddate=2026-08-25T00:00:00Z", "size=64", "filecount=%d" % len(members),
    ]
    lines += list(pkginfo_extra)
    archive = Path(tmp) / f"{name}-{version}.igos.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        data = ("\n".join(lines) + "\n").encode()
        ti = tarfile.TarInfo("./.PKGINFO")
        ti.size = len(data)
        tf.addfile(ti, io.BytesIO(data))
        for arcname, payload, mode in members:
            ti = tarfile.TarInfo(arcname)
            ti.size = len(payload)
            ti.mode = mode
            tf.addfile(ti, io.BytesIO(payload))
    return archive


def _scratch_root(tmp_path):
    root = tmp_path / "target"
    (root / "etc").mkdir(parents=True)
    (root / "etc" / "passwd").write_text("root:x:0:0:root:/root:/bin/bash\n")
    (root / "etc" / "group").write_text("root:x:0:\n")
    return root


# ---------------------------------------------------------------------------
# 1 — the option exists, and the parser can be asked about it
# ---------------------------------------------------------------------------

def test_the_command_line_offers_an_install_root_option():
    """`pkm --root <dir>` is accepted and lands in a parsed attribute."""
    from pkm import cli

    parser = cli.build_parser()
    args = parser.parse_args(["--root", "/tmp/some-target", "list"])
    assert getattr(args, "root", None) == "/tmp/some-target", (
        "the parser accepted --root but did not carry it through to the parsed "
        "arguments, so no command could act on it"
    )


def test_the_option_is_described_as_an_install_root_not_a_general_prefix():
    """The help text says what the option does and what it does NOT do.

    A reader who takes `--root` to mean "run entirely as if this directory were
    the system" would expect the signature keyring to come from it too. The help
    is where that misreading is prevented, so it is pinned here.
    """
    from pkm import cli

    parser = cli.build_parser()
    help_text = parser.format_help()
    assert "--root" in help_text
    lowered = help_text.lower()
    assert "install" in lowered
    for word in ("key", "verif"):
        assert word in lowered, (
            "the --root help does not say where verification keys come from, "
            "which is the one thing a reader is most likely to assume wrongly"
        )


# ---------------------------------------------------------------------------
# 2 + 3 + 4 — what derives from the root, and what deliberately does not
# ---------------------------------------------------------------------------

STATE_ACCESSORS = (
    "db_path",
    "manifest_dir",
    "archive_dir",
    "helper_manifest_dir",
    "lock_path",
    "repo_cache_dir",
    "available_updates_path",
    "package_hooks_dir",
    "pretxn_handler_dir",
)


def test_every_state_path_derives_from_the_root(tmp_path):
    """Not one piece of pkm's own state is left pointing at the live system."""
    rootpaths = _rootpaths()
    target = tmp_path / "target"
    target.mkdir()

    missing = [n for n in STATE_ACCESSORS if not hasattr(rootpaths, n)]
    assert not missing, (
        "pkm.rootpaths does not resolve these pieces of state from the install "
        "root, so a --root run would write them onto the live system: "
        + ", ".join(missing)
    )

    stray = []
    for name in STATE_ACCESSORS:
        resolved = Path(getattr(rootpaths, name)(target))
        if target not in resolved.parents and resolved != target:
            stray.append(f"{name} -> {resolved}")
    assert not stray, (
        "these paths did not land under the install root:\n  "
        + "\n  ".join(stray)
    )


def test_the_default_root_reproduces_todays_paths():
    """With no root given, every path is exactly what pkm resolves today.

    This is the test that makes the option an ADDITION rather than a migration.
    The right-hand sides are the shipped constants, read from the modules that
    own them, so this cannot pass by agreeing with itself.
    """
    rootpaths = _rootpaths()
    from pkm import cli as cli_mod
    from pkm import database as db_mod
    from pkm import installer as inst_mod
    from pkm import repo as repo_mod

    default = Path("/")
    assert Path(rootpaths.db_path(default)) == Path("/var/lib/igos/pkm.db")
    assert Path(rootpaths.manifest_dir(default)) == db_mod.MANIFEST_DIR
    assert Path(rootpaths.archive_dir(default)) == db_mod.ARCHIVE_DIR
    assert Path(rootpaths.helper_manifest_dir(default)) == inst_mod.HELPER_MANIFEST_DIR
    assert Path(rootpaths.lock_path(default)) == cli_mod.PKM_LOCK_PATH
    assert Path(rootpaths.repo_cache_dir(default)) == repo_mod.REPO_CACHE_DIR
    assert Path(rootpaths.available_updates_path(default)) == cli_mod.AVAILABLE_UPDATES_PATH


def test_trust_inputs_do_not_derive_from_the_root(tmp_path):
    """The repository configuration and the keyring stay on the running system.

    Stated as a test rather than a comment because it is the one asymmetry in
    the design, and an asymmetry that is only written down in prose is one that
    a later change will quietly remove.
    """
    rootpaths = _rootpaths()
    target = tmp_path / "target"
    target.mkdir()

    for name in ("repo_config_path", "keyring_path"):
        assert hasattr(rootpaths, name), (
            f"pkm.rootpaths does not name {name}, so nothing records that this "
            f"input is deliberately NOT taken from the install root"
        )
        resolved = Path(getattr(rootpaths, name)(target))
        assert target not in resolved.parents, (
            f"{name} resolved to {resolved}, inside the install root — the "
            f"target would then choose which signatures pkm believes"
        )


# ---------------------------------------------------------------------------
# 5 — a command that cannot be honoured refuses, and says so
# ---------------------------------------------------------------------------

def test_a_command_that_cannot_be_honoured_under_a_root_is_named():
    """pkm carries an explicit, reasoned set of commands it will not root."""
    from pkm import cli

    assert hasattr(cli, "PKM_ROOT_CAPABLE_COMMANDS"), (
        "there is no recorded set of subcommands proven to work under an "
        "alternate root, so nothing can refuse the ones that do not"
    )
    capable = cli.PKM_ROOT_CAPABLE_COMMANDS
    for expected in ("install", "remove", "verify", "list"):
        assert expected in capable, (
            f"{expected} is not in the root-capable set, but the whole point of "
            f"the option is to be able to run it against another root"
        )
    for refused in ("restart-services", "install-helper"):
        assert refused not in capable, (
            f"{refused} is in the root-capable set, but it acts on the running "
            f"system rather than on the root it is pointed at"
        )


def test_an_unhonourable_command_refuses_before_it_changes_anything(tmp_path, capsys):
    """The refusal names the command, names the reason, and returns non-zero."""
    from pkm import cli

    target = tmp_path / "target"
    target.mkdir()
    before = sorted(p.name for p in target.iterdir())

    refusal = cli.refuse_unrooted_command("restart-services", target)
    assert refusal is not None, (
        "restart-services was allowed to proceed against an alternate root"
    )
    code, message = refusal
    assert code != 0
    assert "restart-services" in message
    assert str(target) in message
    assert sorted(p.name for p in target.iterdir()) == before, (
        "the refusal path wrote something into the target root"
    )


# ---------------------------------------------------------------------------
# 6 — a hook that cannot run is a refusal, not a warning
# ---------------------------------------------------------------------------

def test_a_package_hook_that_cannot_run_under_the_root_is_refused(tmp_path):
    """A hook pkm cannot execute under the root stops the install up front.

    The shipped code runs a per-package post-install hook under chroot(root)
    when the root is not "/", and treats a failure to run it as a non-fatal
    warning. In Forge's pipeline that is right: /dev, /proc and /sys are already
    bind-mounted into a fully populated target. In a bare directory it means the
    package's own post-install step never happens and the install still reports
    success.
    """
    rootpaths = _rootpaths()
    from pkm import cli

    assert hasattr(cli, "refuse_unrunnable_package_hook"), (
        "nothing checks, before deploying, whether a package's post-install "
        "hook can actually be executed under the given root"
    )

    target = _scratch_root(tmp_path)
    hook = Path(rootpaths.package_hooks_dir(target)) / "demo" / "post-install"
    hook.parent.mkdir(parents=True)
    hook.write_text("#!/bin/sh\nexit 0\n")
    hook.chmod(0o755)

    refusal = cli.refuse_unrunnable_package_hook("demo", target)
    assert refusal is not None, (
        "a post-install hook whose interpreter does not exist inside the root "
        "was accepted; the install would have completed with the hook skipped"
    )
    code, message = refusal
    assert code != 0
    assert "demo" in message
    assert "/bin/sh" in message, (
        "the refusal does not name the interpreter that is missing, so the "
        "operator is not told what would make the root usable"
    )


def test_a_hook_whose_interpreter_exists_in_the_root_is_not_refused(tmp_path):
    """The gate must not refuse a root that genuinely can run the hook.

    A guard that also blocks the legitimate case is not a fix. This is the
    direction that keeps the refusal honest.
    """
    rootpaths = _rootpaths()
    from pkm import cli

    target = _scratch_root(tmp_path)
    (target / "bin").mkdir(parents=True, exist_ok=True)
    interpreter = target / "bin" / "sh"
    interpreter.write_text("#!/bin/sh\n")
    interpreter.chmod(0o755)

    hook = Path(rootpaths.package_hooks_dir(target)) / "demo" / "post-install"
    hook.parent.mkdir(parents=True)
    hook.write_text("#!/bin/sh\nexit 0\n")
    hook.chmod(0o755)

    assert cli.refuse_unrunnable_package_hook("demo", target) is None, (
        "a root that carries the hook's interpreter was refused anyway"
    )


# ---------------------------------------------------------------------------
# 7 — the install itself lands wholly inside the root
# ---------------------------------------------------------------------------

def test_an_install_into_a_root_records_itself_under_that_root(tmp_path):
    """Database, text manifest and payload all land under the root."""
    rootpaths = _rootpaths()
    from pkm.database import PackageDB
    from pkm.installer import PackageInstaller

    target = _scratch_root(tmp_path)
    archive = _build_archive(
        tmp_path, "rootdemo", "1.0",
        members=(("./usr/bin/rootdemo", b"#!/bin/sh\nexit 0\n", 0o755),),
    )

    db_path = Path(rootpaths.db_path(target))
    db = PackageDB(str(db_path), root=str(target))
    try:
        installer = PackageInstaller(db, root=str(target))
        ok, msg = installer.install("rootdemo", archive_path=str(archive))
        assert ok, f"install into the root failed: {msg}"
    finally:
        db.close()

    assert db_path.is_file(), f"the package database was not created at {db_path}"
    assert (target / "usr" / "bin" / "rootdemo").is_file(), (
        "the package's payload did not land under the install root"
    )
    manifest_dir = Path(rootpaths.manifest_dir(target))
    manifests = sorted(p.name for p in manifest_dir.iterdir()) if manifest_dir.is_dir() else []
    assert any(m.startswith("rootdemo-") for m in manifests), (
        f"no text manifest for the package under {manifest_dir}: {manifests}"
    )


def test_an_install_into_a_root_writes_nothing_outside_it(tmp_path, monkeypatch):
    """Every path the install opens for writing is inside the root.

    Recorded rather than argued: os.open is wrapped for the duration of the
    install and every write-mode path is checked against the root. A single
    escape is the whole failure this option exists to make impossible.
    """
    rootpaths = _rootpaths()
    from pkm.database import PackageDB
    from pkm.installer import PackageInstaller

    target = _scratch_root(tmp_path)
    archive = _build_archive(
        tmp_path, "rootdemo", "1.0",
        members=(("./usr/bin/rootdemo", b"#!/bin/sh\nexit 0\n", 0o755),),
    )

    escapes = []
    real_open = os.open
    write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC

    def watched_open(path, flags, *a, **kw):
        if flags & write_flags:
            try:
                resolved = Path(os.fsdecode(path)).resolve()
            except (TypeError, ValueError):
                resolved = None
            if resolved is not None:
                inside_target = str(resolved).startswith(str(target.resolve()))
                inside_tmp = str(resolved).startswith(str(tmp_path.resolve()))
                if not inside_target and not inside_tmp:
                    escapes.append(str(resolved))
        return real_open(path, flags, *a, **kw)

    db_path = Path(rootpaths.db_path(target))
    db = PackageDB(str(db_path), root=str(target))
    try:
        installer = PackageInstaller(db, root=str(target))
        monkeypatch.setattr(os, "open", watched_open)
        installer.install("rootdemo", archive_path=str(archive))
    finally:
        monkeypatch.undo()
        db.close()

    assert not escapes, (
        "the install opened these paths for writing outside the install root:\n  "
        + "\n  ".join(sorted(set(escapes)))
    )


# ---------------------------------------------------------------------------
# 8 — the wiring: naming a root actually moves what the program uses
# ---------------------------------------------------------------------------
#
# The accessors above are a table. These pin that the program reads it: that
# setting the invocation's root moves the lock, the caches, the report file and
# the helper questions, and that clearing it puts every one of them back. A
# table nothing consults would satisfy every test before this point and still
# be a lie.

@pytest.fixture
def rooted(tmp_path):
    """Run the body with pkm's invocation root set to a scratch directory."""
    from pkm import cli

    target = tmp_path / "target"
    target.mkdir()
    cli.set_install_root(target)
    try:
        yield target
    finally:
        cli.set_install_root(None)


def test_naming_a_root_moves_the_lock(rooted):
    from pkm import cli

    assert cli.resolve_lock_path() == rooted / "var" / "lock" / "pkm.lock"


def test_clearing_the_root_puts_the_lock_back(tmp_path):
    from pkm import cli

    cli.set_install_root(tmp_path)
    cli.set_install_root(None)
    assert cli.resolve_lock_path() == cli.PKM_LOCK_PATH


def test_naming_a_root_moves_the_caches_and_the_report(rooted):
    from pkm import cli

    assert cli.repo_pkg_cache() == rooted / "var" / "cache" / "pkm" / "packages"
    assert cli.repo_rollback_dir() == rooted / "var" / "cache" / "pkm" / "rollback"
    assert cli.available_updates_path() == (
        rooted / "var" / "lib" / "pkm" / "available-updates.json"
    )


def test_the_repository_manager_reads_the_rooted_cache(rooted):
    from pkm import cli

    manager = cli.repo_manager()
    assert manager.cache_dir() == rooted / "var" / "cache" / "pkm"
    assert manager.db_cache() == rooted / "var" / "cache" / "pkm" / "db"
    assert manager.pkg_cache() == rooted / "var" / "cache" / "pkm" / "packages"


def test_the_helper_questions_are_asked_of_the_root_not_the_machine(rooted):
    """A helper present on the LIVE machine must not answer for the target.

    The failure this pins is quiet and specific: pkm decides whether a package
    is a proprietary-download one by looking for /usr/bin/igos-install-<name>.
    Asked of the running machine while installing into a target, it would route
    a target install down the vendor-helper path because THIS machine happens to
    have that helper.
    """
    from pkm import cli

    (rooted / "usr" / "bin").mkdir(parents=True)
    (rooted / "usr" / "bin" / "igos-install-demo").write_text("#!/bin/sh\n")
    assert cli.helper_is_present("demo") is True
    # A name present nowhere is absent, and the live machine is not consulted
    # for either answer.
    assert cli.helper_is_present("not-a-package-on-any-machine") is False

    helpers = rooted / "var" / "lib" / "igos" / "helpers"
    helpers.mkdir(parents=True)
    assert cli.helper_payload_present("demo") is False
    (helpers / "demo.manifest").write_text("{}\n")
    assert cli.helper_payload_present("demo") is True


def test_a_proprietary_package_is_refused_for_another_root(rooted, monkeypatch):
    """The vendor-helper path cannot be performed on behalf of another root.

    It downloads a payload and puts a vendor's licence in front of a person at
    this machine's keyboard. Doing the pkm-package half and skipping the payload
    half is the half-application the option refuses.
    """
    from pkm import cli

    class _Args:
        packages = ["demo"]
        archive = None
        verbose = False
        quiet = False
        assume_yes = True
        allow_downgrade = False
        archive_trust = "strict"

    class _Repo:
        def get_package(self, name):
            return {"payload_license": "a vendor licence"}

    called = []
    monkeypatch.setattr(cli, "repo_manager", lambda: _Repo())
    monkeypatch.setattr(cli, "_proprietary_install",
                        lambda *a, **k: called.append(a))
    monkeypatch.setattr(cli, "package_installer", lambda db: object())
    from pkm import pretxn
    monkeypatch.setattr(pretxn, "run_pre_transaction_hook",
                        lambda *a, **k: None)

    class _DB:
        root = rooted

        def get_installed(self, name):
            return None

    rc = cli.cmd_install(_DB(), _Args())
    assert rc == 1, "the proprietary path was not refused for an alternate root"
    assert not called, "the vendor helper flow ran for an alternate root"
