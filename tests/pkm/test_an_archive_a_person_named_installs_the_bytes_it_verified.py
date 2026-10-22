# SPDX-License-Identifier: GPL-3.0-or-later
"""An archive a person names is installed as the bytes pkm verified.

`pkm install NAME --archive FILE` and `pkm upgrade NAME --archive FILE` take a
file from a directory the person's account can change. The hash the trust gate
compares with the signed index, the header and payload checks, and every use
after them (the staging extract, the deploy, the special-bit pass) each opened
the path again, so whatever the path named when the deploy ran was what landed
as root. Each case here swaps the file after every check has passed and just
before the deploy (at the stale-bytecode purge, the last step before it) in
one of four ways: a link to another archive, another file renamed over it, the
same file rewritten in place, and its parent directory swapped. The installed
bytes must be the verified ones. The open itself is checked too: a link or a
FIFO named as the archive is refused, at once, and the private copy pkm reads
is made in a directory no other account can change and is gone afterwards.
The path is judged before it is opened to read: a FIFO whose writer waits in
its open is refused without the writer being let go, and a file that belongs
neither to the account that asked for the command nor to root is refused.
At the installed version and release, `pkm upgrade --archive` compares the
archive with the installed build before it says there is nothing to do; that
comparison reads the copy too: the same four swaps after the hold, to the
installed build's content or to another build, and a FIFO whose writer waits,
change neither what is compared nor what is said.
"""

import errno
import io
import os
import shutil
import stat
import tempfile
import threading
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from pkm import cli, installer as installer_module, output, pretxn
from pkm.database import PackageDB
from pkm.installer import PackageInstaller
from tests.pkm.test_upgrade_from_local_archive import FakeRepo, _make_archive, _sha

VERIFIED = b"#!/bin/sh\necho the verified build\n"
SWAPPED = b"#!/bin/sh\necho another build\n"


@pytest.fixture(autouse=True)
def _asked_by_this_account(monkeypatch):
    # The archive must belong to the account that asked for the command;
    # every case runs as the account that made its files unless it says so.
    monkeypatch.delenv("PKEXEC_UID", raising=False)
    monkeypatch.delenv("SUDO_UID", raising=False)


@pytest.fixture
def local(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    db = PackageDB(tmp_path / "pkm.db", root=str(root))
    installer = PackageInstaller(db, root=str(root))
    kept = tmp_path / "kept"
    kept.mkdir()
    named = _make_archive(kept / "widget-1.0-4.igos.tar.gz", "widget", "1.0", 4,
                          {"usr/bin/widget": VERIFIED})
    (tmp_path / "other").mkdir()
    other = _make_archive(tmp_path / "other" / "widget-1.0-4.igos.tar.gz",
                          "widget", "1.0", 4, {"usr/bin/widget": SWAPPED})
    yield SimpleNamespace(root=root, db=db, installer=installer, named=named,
                          other=other, tmp=tmp_path,
                          repo=FakeRepo({"widget": {
                              "name": "widget", "version": "1.0", "release": 4,
                              "sha256": _sha(named)}}))
    db.close()


def _install_old(local):
    old = _make_archive(local.tmp / "widget-1.0-3.igos.tar.gz", "widget", "1.0", 3,
                        {"usr/bin/widget": b"#!/bin/sh\necho the old build\n"})
    ok, message = local.installer.install("widget", archive_path=old)
    assert ok, message


def invoke(local, verb, archive=None, trust="strict"):
    args = cli.build_parser().parse_args(
        [verb, "widget", "--archive", str(archive or local.named),
         "--archive-trust", trust] + (["--yes"] if verb == "upgrade" else []))
    said = io.StringIO()
    level = output.process_level()
    old_root = cli._INSTALL_ROOT
    output.set_process_level(output.NORMAL)
    output._process_reporter.stream = said
    output._process_reporter.err_stream = said
    cli.set_install_root(local.root)
    try:
        with redirect_stdout(said), redirect_stderr(said), \
                patch.object(cli, "repo_manager", return_value=local.repo), \
                patch.object(cli, "_save_rollback_archive", return_value=None), \
                patch.object(cli, "refresh_available_updates_after_transaction"), \
                patch.object(cli, "_print_transaction_next_steps"), \
                patch.object(pretxn, "run_pre_transaction_hook",
                             side_effect=lambda *a, **k: None):
            try:
                rc = getattr(cli, "cmd_" + verb)(local.db, args)
            except SystemExit as error:
                rc = error.code
    finally:
        cli._INSTALL_ROOT = old_root
        output.set_process_level(level)
        output._process_reporter.stream = None
        output._process_reporter.err_stream = None
    return rc, " ".join(said.getvalue().split())


def _swap_by_link(local):
    staged = local.named.with_name(".swap-link")
    os.symlink(local.other, staged)
    os.replace(staged, local.named)


def _swap_by_rename(local):
    # Another file renamed over the verified one: a new inode at the path, as
    # an account that can write the directory makes it. Neither the old code
    # nor the new reads the owner of what is there.
    staged = local.named.with_name(".swap-file")
    shutil.copyfile(local.other, staged)
    os.replace(staged, local.named)


def _swap_by_rewrite(local):
    with open(local.named, "r+b") as handle:
        handle.write(local.other.read_bytes())
        handle.truncate()


def _swap_the_parent(local):
    kept = local.named.parent
    os.rename(kept, kept.with_name("kept-as-verified"))
    kept.mkdir()
    shutil.copyfile(local.other, local.named)


SWAPS = {"a link": _swap_by_link, "another file renamed over it": _swap_by_rename,
         "a rewrite in place": _swap_by_rewrite, "a parent swap": _swap_the_parent}


@pytest.mark.parametrize("verb", ["install", "upgrade"])
@pytest.mark.parametrize("swap", list(SWAPS))
def test_a_swap_after_every_check_does_not_change_what_lands(local, verb, swap):
    if verb == "upgrade":
        _install_old(local)
    purge = installer_module._purge_stale_bytecode
    swapped = []

    def purge_then_swap(root, file_list):
        if not swapped:
            SWAPS[swap](local)
            swapped.append(swap)
        return purge(root, file_list)

    with patch.object(installer_module, "_purge_stale_bytecode", purge_then_swap):
        rc, said = invoke(local, verb)
    assert swapped == [swap], said
    assert not rc, said  # cmd_install returns None, cmd_upgrade 0
    assert (local.root / "usr/bin/widget").read_bytes() == VERIFIED, (
        f"after {swap} the deploy installed bytes the signed index never named")
    row = local.db.get_installed("widget")
    assert (row["version"], row["release"]) == ("1.0", 4)


@pytest.mark.parametrize("verb", ["install", "upgrade"])
@pytest.mark.parametrize("swap", list(SWAPS))
def test_a_swap_right_after_the_first_read_does_not_change_what_lands(local, verb, swap):
    """The same four swaps, made as soon as the file has been read once: at the
    index lookup, the first step after it on both commands. Every later check
    (the trust gate's hash, the payload preflight) reads what was held."""
    if verb == "upgrade":
        _install_old(local)
    lookup = local.repo.get_package
    swapped = []

    def get_package_then_swap(name):
        if not swapped:
            SWAPS[swap](local)
            swapped.append(swap)
        return lookup(name)

    local.repo.get_package = get_package_then_swap
    rc, said = invoke(local, verb)
    assert swapped == [swap], said
    assert not rc, said  # cmd_install returns None, cmd_upgrade 0
    assert (local.root / "usr/bin/widget").read_bytes() == VERIFIED, (
        f"after {swap} at the index lookup the install did not land the verified bytes")
    row = local.db.get_installed("widget")
    assert (row["version"], row["release"]) == ("1.0", 4)


@pytest.mark.parametrize("verb", ["install", "upgrade"])
def test_a_link_named_as_the_archive_is_refused(local, verb):
    if verb == "upgrade":
        _install_old(local)
    # Under the archive's own name, so nothing but the link is refused.
    (local.tmp / "links").mkdir()
    link = local.tmp / "links" / local.named.name
    os.symlink(local.named, link)
    before = (local.root / "usr/bin/widget").read_bytes() if verb == "upgrade" else None
    rc, said = invoke(local, verb, archive=link)
    assert rc == 1, said
    assert "is a symbolic link" in said, said
    assert "Nothing was changed" in said, said
    if verb == "install":
        assert not (local.root / "usr/bin/widget").exists()
        assert local.db.get_installed("widget") is None
    else:
        assert (local.root / "usr/bin/widget").read_bytes() == before
        assert local.db.get_installed("widget")["release"] == 3


@pytest.mark.parametrize("verb", ["install", "upgrade"])
def test_a_fifo_named_as_the_archive_is_refused_without_waiting(local, verb):
    if verb == "upgrade":
        _install_old(local)
    fifo = local.tmp / "widget-1.0-4.igos.tar.gz"
    os.mkfifo(fifo)
    seen = []
    done = threading.Event()

    def release_waiting_readers():
        # Each reader that blocks on the FIFO is let go every half second, so
        # the old code's waits (one per open of the path) end and the command
        # finishes; finding one is the proof that it waited.
        while not done.wait(0.5):
            try:
                fd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
            except OSError:
                continue
            seen.append("a reader was waiting")
            os.close(fd)

    releaser = threading.Thread(target=release_waiting_readers)
    releaser.start()
    try:
        rc, said = invoke(local, verb, archive=fifo)
    finally:
        done.set()
        releaser.join()
    assert seen == [], f"pkm waited on the FIFO {len(seen)} time(s): {said}"
    assert rc == 1, said
    assert "is not a regular file" in said, said
    if verb == "install":
        assert local.db.get_installed("widget") is None


def _wait_until_blocked_in_open(tid):
    # The writer is waiting for a reader once the kernel parks it in the
    # FIFO's open (wchan wait_for_partner; openat is syscall 257 on x86_64,
    # 56 on aarch64), so a release can only come from an open of the FIFO.
    for _ in range(500):
        try:
            with open(f"/proc/self/task/{tid}/wchan") as handle:
                if handle.read().strip() == "wait_for_partner":
                    return
            with open(f"/proc/self/task/{tid}/syscall") as handle:
                if handle.read().split()[0] in ("257", "56"):
                    return
        except (OSError, IndexError):
            pass
        threading.Event().wait(0.01)
    raise AssertionError("the writer never reached the FIFO's open")


@pytest.mark.parametrize("verb", ["install", "upgrade"])
def test_a_fifo_with_a_writer_waiting_is_refused_and_the_writer_is_not_let_go(local, verb):
    """Opening a FIFO for reading lets a writer waiting in its open go on,
    whatever the opener does next; a device's open runs its driver the same
    way. pkm must judge the named path without opening it to read."""
    if verb == "upgrade":
        _install_old(local)
    (local.tmp / "waiting").mkdir()
    fifo = local.tmp / "waiting" / local.named.name
    os.mkfifo(fifo)
    tid = []
    let_go = threading.Event()

    def writer():
        tid.append(threading.get_native_id())
        fd = os.open(fifo, os.O_WRONLY)
        let_go.set()
        os.close(fd)

    waiting = threading.Thread(target=writer, daemon=True)
    waiting.start()
    while not tid:
        threading.Event().wait(0.01)
    _wait_until_blocked_in_open(tid[0])
    try:
        rc, said = invoke(local, verb, archive=fifo)
        released = let_go.wait(0.5)
    finally:
        if not let_go.is_set():
            fd = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
            waiting.join(5)
            os.close(fd)
        waiting.join(5)
    assert not released, f"pkm opened the FIFO and let its writer go: {said}"
    assert rc == 1, said
    assert "is not a regular file" in said, said
    if verb == "install":
        assert local.db.get_installed("widget") is None
    else:
        assert local.db.get_installed("widget")["release"] == 3


INSTALLED = b"#!/bin/sh\necho the installed build\n"


def _install_the_same_release(local, content):
    """widget 1.0-4 installed from an archive carrying `content`: the version
    and release of the archive the person names, so `pkm upgrade --archive`
    reaches its same-build decision."""
    (local.tmp / "installed").mkdir()
    built = _make_archive(local.tmp / "installed" / local.named.name, "widget", "1.0", 4,
                          {"usr/bin/widget": content})
    ok, message = local.installer.install("widget", archive_path=built)
    assert ok, message
    return built


def _swap_at_the_index_lookup(local, swap):
    lookup = local.repo.get_package
    swapped = []

    def get_package_then_swap(name):
        if not swapped:
            swap()
            swapped.append(True)
        return lookup(name)

    local.repo.get_package = get_package_then_swap
    return swapped


@pytest.mark.parametrize("swap", list(SWAPS))
def test_the_same_build_decision_reads_the_held_copy_not_the_name(local, swap):
    """At the installed version and release the archive is compared with the
    installed build before "nothing to do" is said. That comparison opened the
    name again: swapped after the hold to an archive of the installed build's
    content, it was read as the archive compared, and the command said the
    archive's build was already installed while the verified copy, another
    build, was never compared or deployed."""
    local.other = _install_the_same_release(local, INSTALLED)
    swapped = _swap_at_the_index_lookup(local, lambda: SWAPS[swap](local))
    rc, said = invoke(local, "upgrade")
    assert swapped, said
    assert not rc, said
    assert "Nothing to do" not in said, said
    assert "is NOT the installed build" in said, said
    assert (local.root / "usr/bin/widget").read_bytes() == VERIFIED, (
        f"after {swap} the same-build decision compared what the name reaches, not the copy")


@pytest.mark.parametrize("swap", list(SWAPS))
def test_a_verified_copy_of_the_installed_build_is_nothing_to_do_whatever_the_name_reaches(
        local, swap):
    """The converse: the verified copy is the installed build and the name is
    swapped after the hold to another build. Read from the copy, the command
    says there is nothing to do, and never reads the swapped file."""
    _install_the_same_release(local, VERIFIED)
    swapped = _swap_at_the_index_lookup(local, lambda: SWAPS[swap](local))
    rc, said = invoke(local, "upgrade")
    assert swapped, said
    assert not rc, said
    assert "is already installed at exactly the archive's build" in said, said
    assert (local.root / "usr/bin/widget").read_bytes() == VERIFIED


def test_a_fifo_swapped_in_after_the_hold_is_never_opened_by_the_same_build_decision(local):
    """The name swapped after the hold to a FIFO whose writer waits in its
    open: the comparison opened it by name and let the writer go. Read from
    the copy, the FIFO is never opened and the verified build is deployed."""
    _install_the_same_release(local, INSTALLED)
    fifo = local.named
    tid = []
    let_go = threading.Event()
    seen = []
    done = threading.Event()

    def writer():
        tid.append(threading.get_native_id())
        fd = os.open(fifo, os.O_WRONLY)
        let_go.set()
        os.close(fd)

    def release_waiting_readers():
        # The old comparison opened the name once for each compression tarfile
        # tries; every open after the writer had gone waited for another one.
        # Each reader found waiting is let go so the command finishes; finding
        # one is the proof that the name was opened again.
        while not done.wait(0.2):
            try:
                fd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
            except OSError:
                continue
            seen.append("a reader was waiting")
            os.close(fd)

    releaser = threading.Thread(target=release_waiting_readers)

    def swap_in_a_fifo():
        os.unlink(fifo)
        os.mkfifo(fifo)
        threading.Thread(target=writer, daemon=True).start()
        while not tid:
            threading.Event().wait(0.01)
        _wait_until_blocked_in_open(tid[0])
        releaser.start()

    swapped = _swap_at_the_index_lookup(local, swap_in_a_fifo)
    try:
        rc, said = invoke(local, "upgrade")
        released = let_go.wait(0.5)
    finally:
        done.set()
        if releaser.is_alive():
            releaser.join()
        if not let_go.is_set():
            fd = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
            let_go.wait(5)
            os.close(fd)
    assert swapped, said
    assert not released, f"the same-build decision opened the FIFO and let its writer go: {said}"
    assert seen == [], f"pkm waited in the FIFO's open {len(seen)} more time(s): {said}"
    assert not rc, said
    assert (local.root / "usr/bin/widget").read_bytes() == VERIFIED, said


def _failing_under_proc_fd(error_number, real):
    def call(path, *args, **kwargs):
        if str(path).startswith("/proc/self/fd/"):
            raise OSError(error_number, os.strerror(error_number), str(path))
        return real(path, *args, **kwargs)
    return call


def _private_copies():
    return {p for p in os.listdir(tempfile.gettempdir()) if p.startswith("pkm-named-archive-")}


def _refusal_of_the_hold(local, name, replacement):
    before = _private_copies()
    with patch.object(installer_module.os, name, replacement):
        with pytest.raises(installer_module.NamedArchiveRefused) as refused:
            installer_module.hold_named_archive(local.named)
    assert _private_copies() == before, "the refused hold left its private copy behind"
    words = str(refused.value)
    assert "Nothing was changed" in words, words
    return words


@pytest.mark.parametrize("call", ["open", "stat"])
def test_a_missing_path_under_proc_fd_asks_whether_proc_is_mounted(local, call):
    """ENOENT under /proc/self/fd is what a /proc that is not mounted gives:
    the reading open of the named file (open) and the read-back of the private
    copy (stat) both say so."""
    words = _refusal_of_the_hold(local, call, _failing_under_proc_fd(
        errno.ENOENT, getattr(os, call)))
    assert "No such file or directory" in words and "is /proc mounted?" in words, words


@pytest.mark.parametrize("call", ["open", "stat"])
def test_another_error_under_proc_fd_is_named_and_not_blamed_on_proc(local, call):
    """At EACCES (an account that cannot read a root's file, a run with --root
    as the account) the words said "is /proc mounted?", pointing away from the
    cause; they now name the error."""
    words = _refusal_of_the_hold(local, call, _failing_under_proc_fd(
        errno.EACCES, getattr(os, call)))
    assert "Permission denied, EACCES" in words, words
    assert "/proc mounted" not in words, words


def test_a_private_copy_that_is_not_the_file_written_is_named_as_such(local):
    """The read-back reaching another file than the one written was put down
    to an unmounted /proc as well."""
    real = os.stat

    def another_file(path, *args, **kwargs):
        if str(path).startswith("/proc/self/fd/"):
            return real(local.other)
        return real(path, *args, **kwargs)

    words = _refusal_of_the_hold(local, "stat", another_file)
    assert "is not the file it wrote" in words, words
    assert "/proc mounted" not in words, words


@pytest.mark.parametrize("verb", ["install", "upgrade"])
@pytest.mark.parametrize("variable", ["SUDO_UID", "PKEXEC_UID"])
def test_a_file_of_another_account_is_refused(local, verb, variable, monkeypatch):
    """The account that asked (pkexec's PKEXEC_UID, else sudo's SUDO_UID,
    else the account pkm runs as) must own the archive it names, or root
    must; a file another account owns is refused and nothing changes. Here
    the asking account is one that does not own the test's archive."""
    if verb == "upgrade":
        _install_old(local)
    owner = os.stat(local.named).st_uid
    asker = owner + 1 if owner + 1 != 0 else owner + 2
    monkeypatch.setenv(variable, str(asker))
    before = (local.root / "usr/bin/widget").read_bytes() if verb == "upgrade" else None
    rc, said = invoke(local, verb)
    assert rc == 1, said
    assert f"belongs to another account (uid {owner})" in said, said
    assert f"(uid {asker})" in said, said
    assert "Nothing was changed" in said, said
    if verb == "install":
        assert not (local.root / "usr/bin/widget").exists()
        assert local.db.get_installed("widget") is None
    else:
        assert (local.root / "usr/bin/widget").read_bytes() == before
        assert local.db.get_installed("widget")["release"] == 3


@pytest.mark.parametrize("value", ["alice", "1000x", "", "١٠٠٠"])
def test_an_asking_account_that_is_not_a_number_is_refused(local, value, monkeypatch):
    monkeypatch.setenv("SUDO_UID", value)
    rc, said = invoke(local, "install")
    assert rc == 1, said
    assert "is not an account number" in said, said
    assert local.db.get_installed("widget") is None


def test_the_asking_accounts_own_file_installs(local, monkeypatch):
    monkeypatch.setenv("SUDO_UID", str(os.stat(local.named).st_uid))
    rc, said = invoke(local, "install")
    assert not rc, said  # cmd_install returns None on success
    assert (local.root / "usr/bin/widget").read_bytes() == VERIFIED


def test_the_private_copy_is_this_accounts_alone_and_gone_afterwards(local):
    seen = {}
    purge = installer_module._purge_stale_bytecode

    def look(root, file_list):
        held = list(installer_module._HELD.values())
        seen["held"] = len(held)
        seen["path"] = held[0].path
        seen["named"] = held[0].named
        seen["directory"] = Path(held[0].directory)
        seen["dir"] = os.stat(held[0].path.parent)
        seen["file"] = os.stat(held[0].path)
        return purge(root, file_list)

    with patch.object(installer_module, "_purge_stale_bytecode", look):
        rc, said = invoke(local, "install")
    assert not rc, said  # cmd_install returns None, cmd_upgrade 0
    assert seen["held"] == 1
    assert seen["named"] == str(local.named)
    assert str(seen["path"]).startswith("/proc/self/fd/")
    assert seen["path"].name == local.named.name
    assert stat.S_IMODE(seen["dir"].st_mode) == 0o700
    assert stat.S_IMODE(seen["file"].st_mode) == 0o600
    assert seen["dir"].st_uid == seen["file"].st_uid == os.geteuid()
    assert not seen["directory"].exists()
    assert installer_module._HELD == {}
    # The row records the file the person named, never the copy's /proc path.
    assert local.db.get_installed("widget")["archive_path"] == str(local.named)


def test_the_private_directory_swapped_by_the_temporary_directorys_owner_changes_nothing(local):
    """Whoever can write the temporary directory can rename the directory pkm
    made in it and put another in its place; the copy is read through the
    descriptor pkm holds on its own directory, so the name is never walked."""
    purge = installer_module._purge_stale_bytecode
    swapped = []

    def purge_then_swap(root, file_list):
        held = list(installer_module._HELD.values())[0]
        made = Path(held.directory)
        os.rename(made, made.with_name(made.name + "-moved"))
        made.mkdir()
        shutil.copyfile(local.other, made / local.named.name)
        swapped.append(made)
        return purge(root, file_list)

    with patch.object(installer_module, "_purge_stale_bytecode", purge_then_swap):
        rc, said = invoke(local, "install")
    assert len(swapped) == 1, said
    assert not rc, said  # cmd_install returns None, cmd_upgrade 0
    assert (local.root / "usr/bin/widget").read_bytes() == VERIFIED
    assert "was moved from" in said, said
    shutil.rmtree(swapped[0])
    shutil.rmtree(swapped[0].with_name(swapped[0].name + "-moved"))
