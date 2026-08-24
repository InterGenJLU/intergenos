"""The one path where a pkm mutation runs with no lock held — proved, not argued.

`_pkm_mutation_lock` skips locking entirely when it cannot create the lock file's
parent directory. The reasoning is sound and is written down at the call site: at
chroot-install time /var/lock is a dangling symlink into an unmounted /run, and a
build chroot runs one pkm at a time, so there is nothing to serialize against.
Before this file, that was an argument. Nothing tested that the escape fires ONLY
in that condition, and nothing anywhere asserted the premise the argument rests
on — that the build really does run one pkm at a time.

WHAT THESE TESTS PIN.

1. The escape does NOT fire when the lock directory can be created. This is the
   direction that matters: an escape that fires when it should not is a mutation
   running unserialized on a live system, which is the silent-failure class the
   lock exists to remove. Red-first means making it fire when it must not and
   seeing the test refuse that.
2. The escape DOES fire when the directory genuinely cannot be created, and the
   guarded work still runs — the fallback is non-fatal by design, because a build
   that cannot lock must still be able to install.
3. The warning names the CONDITION and the CONSEQUENCE in one sentence. "Skipping
   lock acquisition" alone tells a reader what happened and not what it costs
   them.

MEASURED WHILE WRITING THIS, and it corrects the call site's own comment: the
DANGLING SYMLINK case — /var/lock -> ../run/lock with /run unmounted, which the
comment names as the chroot shape — does NOT reach the escape any more. The code
detects the symlink, resolves it, and creates the TARGET, so the lock is taken
normally and nothing is skipped. The escape is therefore narrower than its
comment claims: it fires only when even the resolved target cannot be made. The
test below uses a path whose parent component is a regular FILE, which is a
condition mkdir genuinely cannot satisfy.

Every test runs as an ordinary user in its own tmp_path.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pkm import cli


def _uncreatable_lock_path(tmp_path: Path) -> Path:
    """A lock path whose parent cannot be created, because a component of it is
    a regular file. This is the condition that actually reaches the escape."""
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    return blocker / "lockdir" / "pkm.lock"


def _dangling_lock_path(tmp_path: Path) -> Path:
    """The field shape the call site's comment names: a parent symlinked to a
    path that does not exist. Kept because what it PROVES is that this case no
    longer reaches the escape at all."""
    parent = tmp_path / "lockdir"
    parent.symlink_to(tmp_path / "does-not-exist" / "run" / "lock")
    return parent / "pkm.lock"


def test_the_escape_does_not_fire_when_the_directory_can_be_created(
        monkeypatch, tmp_path, capsys):
    lock = tmp_path / "made" / "here" / "pkm.lock"
    monkeypatch.setenv("IGOS_PKM_LOCK", str(lock))
    with cli._pkm_mutation_lock("vacuum"):
        assert lock.exists(), "the lock file was not created, so nothing was held"
    err = capsys.readouterr().err
    assert "skipping lock acquisition" not in err.lower(), (
        "the chroot escape fired on a path that could be created — a mutation "
        "would have run with no lock held on a system where locking works")


def test_the_escape_fires_when_the_directory_cannot_be_created(
        monkeypatch, tmp_path, capsys):
    lock = _uncreatable_lock_path(tmp_path)
    monkeypatch.setenv("IGOS_PKM_LOCK", str(lock))
    ran = False
    with cli._pkm_mutation_lock("vacuum"):
        ran = True
    assert ran, "the guarded work did not run; the fallback must be non-fatal"
    err = capsys.readouterr().err
    assert "lock" in err.lower() and "skip" in err.lower(), (
        f"the escape fired without saying so; stderr was: {err!r}")


def test_the_warning_names_the_condition_and_the_consequence(
        monkeypatch, tmp_path, capsys):
    lock = _uncreatable_lock_path(tmp_path)
    monkeypatch.setenv("IGOS_PKM_LOCK", str(lock))
    with cli._pkm_mutation_lock("vacuum"):
        pass
    err = capsys.readouterr().err.lower()
    # the condition: which path, and that it could not be made
    assert str(lock.parent).lower() in err, "the warning does not name the path"
    # the consequence, in words a reader can act on
    assert "not serial" in err or "unserial" in err or "at a time" in err, (
        "the warning says what it skipped but not what that costs: it must say "
        "that concurrent pkm operations are NOT serialized while it is in force")


def test_the_escape_is_reached_only_through_the_directory_failure(
        monkeypatch, tmp_path, capsys):
    """A control on the control: with the directory creatable but the lock file
    itself unopenable, the escape must NOT be the answer. A blanket try/except
    around both would swallow a real permission fault as a chroot context."""
    parent = tmp_path / "readonly"
    parent.mkdir()
    lock = parent / "pkm.lock"
    monkeypatch.setenv("IGOS_PKM_LOCK", str(lock))
    os.chmod(parent, 0o500)  # traversable, not writable
    try:
        with pytest.raises(OSError):
            with cli._pkm_mutation_lock("vacuum"):
                pass
        err = capsys.readouterr().err.lower()
        assert "skipping lock acquisition" not in err, (
            "an unwritable lock FILE was treated as the chroot condition; that "
            "would let a real permission fault run a mutation unlocked")
    finally:
        os.chmod(parent, 0o700)


def test_a_read_only_command_never_reaches_the_escape(monkeypatch, tmp_path,
                                                      capsys):
    lock = _uncreatable_lock_path(tmp_path)
    monkeypatch.setenv("IGOS_PKM_LOCK", str(lock))
    with cli._pkm_mutation_lock("list"):
        pass
    assert "skip" not in capsys.readouterr().err.lower(), (
        "a read-only command produced lock output; it takes no lock at all")


def test_a_dangling_symlink_parent_is_REPAIRED_not_escaped(
        monkeypatch, tmp_path, capsys):
    """The case the call site's comment names as the chroot condition.

    It no longer reaches the escape: the symlink is resolved and its target is
    created, so the lock is taken normally. Pinned because the comment says
    otherwise, and a reader who trusts the comment would believe every
    chroot-time install runs unlocked when in fact it does not.
    """
    lock = _dangling_lock_path(tmp_path)
    monkeypatch.setenv("IGOS_PKM_LOCK", str(lock))
    with cli._pkm_mutation_lock("vacuum"):
        pass
    err = capsys.readouterr().err.lower()
    assert "skip" not in err, (
        "the dangling-symlink case reached the escape; if that is now true the "
        "comment is right and this test is what needs changing")
    assert lock.exists(), "the lock file was not created through the symlink"
