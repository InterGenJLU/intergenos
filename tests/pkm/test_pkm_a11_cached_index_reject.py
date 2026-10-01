#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""PKM-A11 regression: a rejected cached index is surfaced, never swallowed.

_ensure_synced loads cached repo indexes lazily. It wrapped _parse_index —
where the fail-closed gates live (anti-rollback / freshness / Valid-Until /
min_pkm_version) — in `except Exception: pass`. So when one of those gates
REJECTED a stale or rolled-back cached index, the repo silently vanished
from self.indexes and every subsequent package lookup read "not found"
with no explanation: a rollback attack or an expired index degraded into a
confusing empty repo.

Fixed contract:
  * IndexFormatError (a deliberate fail-closed rejection) is caught
    distinctly and surfaced LOUDLY on stderr, naming the repo + reason +
    the `pkm sync` remediation; the cached artifact is LEFT in place for
    inspection (mirrors sync()'s L-020 handler);
  * a genuinely-unexpected parse error still soft-skips that repo (others
    load) but is LOGGED, never a silent pass.

Since 2026-10-01 this module also holds the OTHER branch of the same method,
the one that runs when the verification did not succeed. It deleted the cached
index and said nothing, for two different facts at once: a signature that does
not verify, and bytes the filesystem would not let pkm read. Measured at the
real mechanism (a real cached index at mode 000 under a throwaway root, the
real gpgv, the shipped commands): the index whose only fault was being
unreadable was destroyed, and the person was then told "No repository index
yet" by `search`, "No packages available" by `list available`, and, by both
archive-trust paths, that the package is one the signed index does not list.
Three false statements about one read error, with the file that would have
explained it already gone.

The rule pinned below: a read failure leaves the artefact in place, names the
repo and the reason, and REACHES THE CALLER (both archive-trust call sites
catch OSError and report it in their own words); a verification failure keeps
today's behaviour — the index is removed — and now says so, naming which of the
two reasons it was. The fast path is untouched: when the signature verifies,
nothing extra is read.

Since 2026-10-01 one of those two reasons no longer removes anything. A keyring
that is ABSENT means nothing checked the index — not that a check failed — and
only a check that failed justifies destroying the file. With no keyring the fault
is the keyring's, the index is still not loaded and still not trusted, and the
file stays for the person to compare once the keyring is back. Both sites that did
this are pinned here: `_ensure_synced`, where the file is the cache that was
working before, and `sync`, where it is the body the mirror just served. A
signature that genuinely fails against the pinned release keys still has its index
removed, at both sites, in the words each site already used.
"""

import contextlib
import io
import os
import tempfile
import errno
import types
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import MagicMock, patch

import pkm.repo as repo_mod
from pkm.repo import RepoManager, IndexFormatError


class CachedIndexRejectSurfaceTest(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.cache = Path(self._td.name) / "db"
        self.cache.mkdir(parents=True)
        # Cached artifacts for one repo "intergenos" exist on disk.
        self.db_path = self.cache / "intergenos.db"
        self.sig_path = self.cache / "intergenos.db.sig"
        self.db_path.write_bytes(b"cached-index-bytes")
        self.sig_path.write_bytes(b"sig")
        # A keyring file that .exists().
        self.keyring = Path(self._td.name) / "trusted.gpg"
        self.keyring.write_bytes(b"keyring")

        # Build a RepoManager without running __init__'s filesystem setup.
        self.mgr = RepoManager.__new__(RepoManager)
        self.mgr.indexes = {}
        self.mgr.repos = {
            "intergenos": {"url": "https://repo.intergenos.org/x86_64/current"}
        }

    def tearDown(self):
        self._td.cleanup()

    def _run_ensure_synced(self, parse_side_effect):
        buf = io.StringIO()
        with patch.object(repo_mod, "REPO_DB_CACHE", self.cache), \
             patch.object(repo_mod, "GPG_KEYRING", self.keyring), \
             patch.object(self.mgr, "_verify_signature", return_value=True), \
             patch.object(self.mgr, "_parse_index",
                          side_effect=parse_side_effect), \
             redirect_stderr(buf):
            self.mgr._ensure_synced()
        return buf.getvalue()

    def test_index_format_error_is_surfaced_loud_and_artifact_kept(self):
        err = self._run_ensure_synced(
            IndexFormatError("anti-rollback: cached generated 2026-06-10 is "
                             "older than last-seen 2026-06-15")
        )
        # Loud, not swallowed.
        self.assertNotEqual(err.strip(), "", "rejection must not be silent")
        self.assertIn("REJECTED", err)
        self.assertIn("intergenos", err)
        self.assertIn("anti-rollback", err)  # the gate's reason carried through
        self.assertIn("pkm sync", err)        # remediation (sync is the canonical index-refresh command)
        # The repo did not load (fail-closed), but the cached artifact is
        # LEFT in place for operator inspection.
        self.assertNotIn("intergenos", self.mgr.indexes)
        self.assertTrue(self.db_path.exists())
        self.assertTrue(self.sig_path.exists())

    def test_unexpected_error_is_logged_not_silent(self):
        err = self._run_ensure_synced(ValueError("totally unexpected"))
        self.assertNotEqual(err.strip(), "", "unexpected error must be logged")
        self.assertIn("could not be loaded", err)
        self.assertIn("ValueError", err)
        self.assertIn("intergenos", err)
        self.assertNotIn("intergenos", self.mgr.indexes)

    def test_clean_index_loads_silently(self):
        sentinel = object()
        buf = io.StringIO()
        with patch.object(repo_mod, "REPO_DB_CACHE", self.cache), \
             patch.object(repo_mod, "GPG_KEYRING", self.keyring), \
             patch.object(self.mgr, "_verify_signature", return_value=True), \
             patch.object(self.mgr, "_parse_index", return_value=sentinel), \
             redirect_stderr(buf):
            self.mgr._ensure_synced()
        # No noise on the happy path; the index loaded.
        self.assertEqual(buf.getvalue(), "")
        self.assertIs(self.mgr.indexes["intergenos"], sentinel)


class SyncKeepsWhatNothingCouldCheck(unittest.TestCase):
    """The other site that removed a cached index for a missing keyring.

    `sync` downloads the index and its signature and then verifies. The download
    is stood in — the body, not the fetch, is what these cases are about, and a
    unit test reaches no mirror — while the cache paths, the keyring check, the
    removal and the result tuples are the shipped ones.

    Downloads remain in staging until the signature and index are accepted.
    A missing keyring keeps the unverified pair there for inspection without
    replacing a previously verified cache; a failed signature removes only
    that new pair.
    """

    REPO = "intergenos"
    URL = "https://repo.intergenos.org/x86_64/current"

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.cache = Path(self._td.name) / "db"
        self.cache.mkdir(parents=True)
        self.keyring = Path(self._td.name) / "trusted.gpg"
        self.keyring.write_bytes(b"keyring")
        self.mgr = RepoManager.__new__(RepoManager)
        self.mgr.indexes = {}
        self.mgr.repos = {self.REPO: {"url": self.URL, "enabled": True}}

    def _served(self, url, dest):
        """The stood-in fetch: a body on disk, exactly as _download leaves it."""
        self.downloads.append(Path(dest))
        Path(dest).write_bytes(b"the index the mirror served\n")

    def _sync(self, verify=False, parse=None):
        """Run the real sync with the fetch stood in; return (results, state)."""
        parsed = parse if parse is not None else types.SimpleNamespace(
            package_count=1, generated="2026-10-01T00:00:00Z")
        self.downloads = []
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                patch.object(repo_mod, "REPO_DB_CACHE", self.cache))
            stack.enter_context(
                patch.object(repo_mod, "GPG_KEYRING", self.keyring))
            stack.enter_context(
                patch.object(repo_mod, "PKM_STATE_DIR", Path(self._td.name) / "state"))
            stack.enter_context(
                patch.object(self.mgr, "_download", side_effect=self._served))
            stack.enter_context(
                patch.object(self.mgr, "_verify_signature", return_value=verify))
            stack.enter_context(
                patch.object(self.mgr, "_parse_index", return_value=parsed))
            results = self.mgr.sync()
        db_path = self.cache / f"{self.REPO}.db"
        sig_path = self.cache / f"{self.REPO}.db.sig"
        return results, (db_path.exists(), sig_path.exists())

    def test_an_absent_keyring_leaves_the_downloaded_index_in_the_cache(self):
        self.keyring.unlink()
        results, (db_there, sig_there) = self._sync()
        self.assertEqual(len(results), 1)
        name, ok, message = results[0]
        self.assertEqual(name, self.REPO)
        self.assertFalse(ok, "a sync that verified nothing is not a success")
        self.assertIn("keyring not found", message)
        self.assertIn(str(self.keyring), message, "the keyring is named")
        self.assertIn("LEFT", message)
        self.assertFalse(db_there, "an unverified download must not become the canonical cache")
        self.assertFalse(sig_there)
        self.assertEqual(len(self.downloads), 2)
        for path in self.downloads:
            self.assertTrue(path.exists(), "a missing keyring must not destroy the index served")
            self.assertIn(self.cache, path.parents)
            self.assertIn(str(path.parent), message)
        self.assertEqual(self.mgr.indexes, {},
                         "kept is not loaded and is not trusted")

    def test_a_signature_that_genuinely_fails_still_removes_the_index(self):
        """The control: the keyring is there, gpgv ran, the answer was no."""
        results, (db_there, sig_there) = self._sync(verify=False)
        _name, ok, message = results[0]
        self.assertFalse(ok)
        self.assertIn("signature verification FAILED", message)
        self.assertFalse(db_there, "an index that FAILED a check is removed")
        self.assertFalse(sig_there)
        self.assertEqual(self.mgr.indexes, {})
        self.assertTrue(all(not path.exists() for path in self.downloads))

    def test_a_healthy_sync_still_loads_and_reports_its_count(self):
        """The control that the command still does its job."""
        results, (db_there, _sig) = self._sync(verify="FPR")
        _name, ok, message = results[0]
        self.assertTrue(ok, message)
        self.assertIn("synced", message)
        self.assertTrue(db_there)
        self.assertIn(self.REPO, self.mgr.indexes)


class _PartWayHandle:
    """A file handle whose FIRST read returns bytes and whose second raises.

    No mode can present a read that fails part-way through on a small file, and
    that is exactly the fault the read check reads to the end to catch.
    """

    def __init__(self, blocks_before_the_fault=1):
        self._left = blocks_before_the_fault

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def read(self, _size=None):
        if self._left > 0:
            self._left -= 1
            return b"x" * 1024
        raise OSError(errno.EIO, "Input/output error")


def _handle_that_fails_part_way(_path, *_args, **_kwargs):
    """Stands in for Path.open, returning the handle above."""
    return _PartWayHandle()


class AnUnreadableCachedIndexIsReportedNotDeleted(unittest.TestCase):
    """The cases for the branch that runs when the verification did not succeed.

    The cached files are REAL files with real modes, because the fact under test
    is what the filesystem does when pkm reads them. The signature gate itself is
    stood in at the answer the real gpgv was measured to give for each of these
    cache states (falsy), which is what the module's older cases do too: the
    question here is not whether gpgv is right, it is what pkm does with the
    file afterwards.
    """

    REPO = "intergenos"
    URL = "https://repo.intergenos.org/x86_64/current"

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.cache = Path(self._td.name) / "db"
        self.cache.mkdir(parents=True)
        self.keyring = Path(self._td.name) / "trusted.gpg"
        self.keyring.write_bytes(b"keyring")
        # Built without __init__'s filesystem setup, as the cases above are.
        self.mgr = RepoManager.__new__(RepoManager)
        self.mgr.indexes = {}
        self.mgr.repos = {self.REPO: {"url": self.URL}}
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        # A case that left a file at mode 000 — or a directory without its
        # traverse bit — would otherwise stop the temporary tree's removal.
        for path in self.cache.iterdir():
            try:
                path.chmod(0o755 if path.is_dir() else 0o644)
            except OSError:
                pass
        self._td.cleanup()

    def _cached_pair(self, name=None):
        name = name or self.REPO
        db_path = self.cache / f"{name}.db"
        sig_path = self.cache / f"{name}.db.sig"
        db_path.write_bytes(b"cached-index-bytes")
        sig_path.write_bytes(b"sig")
        return db_path, sig_path

    def _unopenable(self, name=None):
        """A cached index no uid can read through: the path is a directory, so
        the open itself fails (IsADirectoryError IS an OSError). Root reads a
        mode-000 file, so the permission case alone would not hold everywhere."""
        name = name or self.REPO
        db_path = self.cache / f"{name}.db"
        db_path.mkdir()
        sig_path = self.cache / f"{name}.db.sig"
        sig_path.write_bytes(b"sig")
        return db_path, sig_path

    def _run(self, verify=False, parse=None, extra=()):
        """Run the real method over the real files.

        Returns (stderr, raised); `raised` is whatever left the method, which
        for an unreadable index is the error itself. `extra` holds patches a
        case needs on top of these, entered last, for the faults no mode can
        present on this filesystem as this uid."""
        verifier = verify if callable(verify) else (lambda *_a, **_k: verify)
        buf = io.StringIO()
        raised = None
        patches = [
            patch.object(repo_mod, "REPO_DB_CACHE", self.cache),
            patch.object(repo_mod, "GPG_KEYRING", self.keyring),
            patch.object(self.mgr, "_verify_signature", side_effect=verifier),
        ]
        if parse is not None:
            patches.append(
                patch.object(self.mgr, "_parse_index", return_value=parse))
        patches.extend(extra)
        with contextlib.ExitStack() as stack:
            for one in patches:
                stack.enter_context(one)
            stack.enter_context(redirect_stderr(buf))
            try:
                self.mgr._ensure_synced()
            except BaseException as exc:          # noqa: BLE001 — recorded
                raised = exc
        return buf.getvalue(), raised

    @unittest.skipIf(os.geteuid() == 0,
                     "mode 000 does not refuse a root reader; the directory "
                     "case holds the same rule for every uid")
    def test_a_read_refused_by_the_mode_is_kept_reported_and_raised(self):
        db_path, sig_path = self._cached_pair()
        db_path.chmod(0o000)
        err, raised = self._run(verify=False)
        self.assertIsInstance(
            raised, OSError,
            "an index that cannot be read must reach the caller: the two "
            "archive-trust paths catch OSError here and report it, and an "
            "absence is not what happened")
        self.assertTrue(db_path.exists(),
                        "the unreadable index must be LEFT for inspection")
        self.assertTrue(sig_path.exists(), "its signature goes with it")
        self.assertIn("could not be READ", err)
        self.assertIn("PermissionError", err)
        self.assertIn(self.REPO, err, "the repo must be named")
        self.assertIn("LEFT IN PLACE", err)
        self.assertEqual(self.mgr.indexes, {})

    def test_a_read_no_uid_can_complete_is_kept_reported_and_raised(self):
        db_path, _sig = self._unopenable()
        err, raised = self._run(verify=False)
        self.assertIsInstance(raised, OSError)
        self.assertIn("IsADirectoryError", err)
        self.assertIn("could not be READ", err)
        self.assertIn(self.REPO, err)
        self.assertTrue(db_path.is_dir(),
                        "the artefact must be left exactly as it was")
        self.assertNotIn(
            "REMOVED", err,
            "a read failure that is reported and then falls through to the "
            "removal would still say this; the raise is what stops it, and "
            "this is the assertion that holds the raise for every uid")

    def test_a_second_call_reports_again_rather_than_answering_from_a_part_load(self):
        """The early `if self.indexes: return` must not turn a reported read
        failure into a silent partial answer for every later call in the same
        process."""
        self.mgr.repos = {"first": {"url": self.URL},
                          "second": {"url": self.URL}}
        first_db, _ = self._cached_pair("first")
        self._unopenable("second")
        sentinel = object()

        def verify(data_path, _sig_path):
            # The first repo's index verifies; the second's does not, which is
            # where the read is then asked about.
            return "FPR" if Path(data_path).name == "first.db" else False

        err, raised = self._run(verify=verify, parse=sentinel)
        self.assertIsInstance(raised, OSError)
        self.assertIn("could not be READ", err)
        self.assertEqual(
            self.mgr.indexes, {},
            "nothing may be published from a walk that reported a fault, or "
            "the next call answers silently from the repos that happened to "
            "load first")
        self.assertTrue(first_db.exists())
        self.assertNotIn("REMOVED", err,
                         "the second repo's index was read, not removed")

        err2, raised2 = self._run(verify=verify, parse=sentinel)
        self.assertIsInstance(
            raised2, OSError,
            "the fault is still true, so the second call must report it too")
        self.assertIn("could not be READ", err2)
        self.assertNotIn("REMOVED", err2)

    def test_a_signature_that_does_not_verify_is_still_removed_and_now_said(self):
        db_path, sig_path = self._cached_pair()
        err, raised = self._run(verify=False)
        self.assertIsNone(
            raised,
            "a readable index that does not verify is not a read error; the "
            "caller sees a repo that did not load, as before")
        self.assertFalse(db_path.exists(),
                         "an index that cannot be verified is never trusted")
        self.assertFalse(sig_path.exists())
        self.assertIn("REMOVED", err)
        self.assertIn("did not verify", err)
        self.assertIn(self.REPO, err)
        self.assertIn("pkm sync", err)

    def test_an_absent_keyring_keeps_the_cached_index_and_names_the_keyring(self):
        """Nothing checked it, so nothing is destroyed.

        This case pinned the REMOVAL until 2026-10-01, when the distinction it
        already drew in words was carried into the behaviour: a missing keyring
        is a fault in the keyring, and the index that was working before is the
        one file a person can compare once it is back.
        """
        db_path, sig_path = self._cached_pair()
        self.keyring.unlink()
        # The keyring check short-circuits, so the gate is never asked here.
        err, raised = self._run(verify=False)
        self.assertIsNone(raised)
        self.assertTrue(db_path.exists(),
                        "a missing keyring must not destroy the cached index")
        self.assertTrue(sig_path.exists(), "nor its signature")
        self.assertIn("NOT TRUSTED", err)
        self.assertIn("LEFT IN PLACE", err)
        self.assertIn(str(self.keyring), err,
                      "the reason is the absent keyring, not a bad signature")
        self.assertNotIn("did not verify", err,
                         "nothing checked it, so nothing may claim it failed")
        self.assertNotIn("REMOVED", err)
        self.assertEqual(self.mgr.indexes, {},
                         "unverified is still unloaded and still untrusted")

    def test_an_index_that_verifies_is_loaded_kept_and_silent(self):
        """The control: the fast path reads nothing extra and says nothing."""
        db_path, sig_path = self._cached_pair()
        sentinel = object()
        err, raised = self._run(verify="FPR", parse=sentinel)
        self.assertIsNone(raised)
        self.assertEqual(err, "", "the happy path is silent")
        self.assertIs(self.mgr.indexes[self.REPO], sentinel)
        self.assertTrue(db_path.exists())
        self.assertTrue(sig_path.exists())

    # ---- the removal is reported from what happened, not from the intention

    @unittest.skipIf(os.geteuid() == 0,
                     "a directory root may write refuses nobody; the "
                     "stood-in case below holds the same rule for every uid")
    def test_a_removal_the_cache_directory_refuses_is_said_in_its_own_words(self):
        """The shipped layout, for anyone who is not root.

        /var/cache/pkm/db is root-owned and mode 755, so a reader who is not
        root cannot unlink anything inside it. Until 2026-10-01 this branch
        wrote "was REMOVED" before trying, so that reader was told a security
        artefact was gone while both files were still on disk, and through
        `pkm info` — which absorbs the error — the false sentence was the only
        thing said.
        """
        db_path, sig_path = self._cached_pair()
        self.addCleanup(self.cache.chmod, 0o755)
        self.cache.chmod(0o555)
        err, raised = self._run(verify=False)
        self.assertIsNone(
            raised,
            "a failed removal is not a read error and must not be raised as "
            "one: every caller's decision is already the one for an index "
            "that is not there")
        self.assertTrue(db_path.exists(),
                        "the file the sentence talks about is still here")
        self.assertTrue(sig_path.exists())
        self.assertNotIn("was REMOVED", err,
                         "nothing was removed, so nothing may say it was")
        self.assertIn("could not be removed", err)
        self.assertIn("PermissionError", err)
        self.assertIn("did not verify", err)
        self.assertIn("NOT trusted and was NOT loaded", err)
        self.assertIn("sudo pkm sync", err)
        self.assertIn(self.REPO, err, "the repo must be named")
        self.assertEqual(self.mgr.indexes, {},
                         "an index that did not verify is never loaded")

    def test_a_removal_that_fails_for_every_uid_is_said_not_claimed_done(self):
        """The same rule where no mode can present it: a read-only filesystem
        refuses root too, and that is the state this case stands in."""
        db_path, sig_path = self._cached_pair()
        refusal = OSError(errno.EROFS, "Read-only file system")

        def refuse(_self, **_kwargs):
            raise refusal

        err, raised = self._run(
            verify=False,
            extra=[patch.object(Path, "unlink", refuse)])
        self.assertIsNone(raised)
        self.assertTrue(db_path.exists())
        self.assertTrue(sig_path.exists())
        self.assertNotIn("was REMOVED", err)
        self.assertIn("could not be removed", err)
        self.assertIn("Read-only file system", err)
        self.assertEqual(self.mgr.indexes, {})

    # ---- what the read check reads, and when it is asked at all

    def test_a_signature_that_cannot_be_read_is_reported_like_the_index(self):
        """The read check asks about BOTH files. A narrowing of it to the index
        alone would delete an unreadable signature with no word, and the
        signature is half of what a later comparison needs."""
        db_path = self.cache / f"{self.REPO}.db"
        db_path.write_bytes(b"cached-index-bytes")
        sig_path = self.cache / f"{self.REPO}.db.sig"
        sig_path.mkdir()                      # unreadable for every uid
        err, raised = self._run(verify=False)
        self.assertIsInstance(
            raised, OSError,
            "an index whose signature cannot be read has not been shown to be "
            "untrustworthy either")
        self.assertIn("could not be READ", err)
        self.assertIn("IsADirectoryError", err)
        self.assertTrue(db_path.exists(), "neither file is destroyed")
        self.assertTrue(sig_path.is_dir())
        self.assertNotIn("was REMOVED", err)

    def test_a_read_that_fails_part_way_through_is_reported(self):
        """The check reads each file THROUGH TO THE END, which is the reason
        the module gives for a full read: a read that fails part-way through
        shows up nowhere else. One block and a stop would call this file
        readable and delete it."""
        self._cached_pair()
        err, raised = self._run(
            verify=False,
            extra=[patch.object(Path, "open", _handle_that_fails_part_way)])
        self.assertIsInstance(raised, OSError)
        self.assertIn("could not be READ", err)
        self.assertIn("Input/output error", err)
        self.assertNotIn("was REMOVED", err)

    def test_the_path_that_verifies_asks_no_read_check(self):
        """The control for the cost the module claims: when the signature
        verifies, nothing extra is read at all."""
        self._cached_pair()
        sentinel = object()
        spy = MagicMock(return_value=None)
        err, raised = self._run(
            verify="FPR", parse=sentinel,
            extra=[patch.object(self.mgr, "_unreadable_cause", spy)])
        self.assertIsNone(raised)
        self.assertEqual(err, "")
        spy.assert_not_called()
        self.assertIs(self.mgr.indexes[self.REPO], sentinel)

    # ---- the cache directory itself

    @unittest.skipIf(os.geteuid() == 0,
                     "a directory root may search refuses nobody; the "
                     "stood-in case below holds the same rule for every uid")
    def test_a_cache_directory_that_cannot_be_searched_is_reported(self):
        """The one unreadable-cache state the branch above never sees.

        Path.exists() answers False for every file inside a directory with no
        traverse bit, so before this the layer reported no index at all: the
        install path refused the archive as one the index does not list, and
        the loose path simply proceeded.
        """
        db_path, _sig = self._cached_pair()
        self.addCleanup(self.cache.chmod, 0o755)
        self.cache.chmod(0o000)
        err, raised = self._run(verify=False)
        self.assertIsInstance(
            raised, OSError,
            "a cache pkm cannot look inside is not a cache with nothing in it")
        self.assertIn("could not be READ", err)
        self.assertIn("PermissionError", err)
        self.assertIn(str(self.cache), err, "the directory must be named")
        self.assertIn("LEFT IN PLACE", err)
        self.assertEqual(self.mgr.indexes, {})
        self.cache.chmod(0o755)
        self.assertTrue(db_path.exists(), "nothing was touched")

    def test_a_cache_directory_the_walk_cannot_list_is_reported_for_every_uid(self):
        """The same rule where no mode can present it (a root reader searches
        any directory): the listing itself is refused."""
        db_path, _sig = self._cached_pair()
        err, raised = self._run(
            verify=False,
            extra=[patch("pkm.repo.os.listdir",
                         side_effect=PermissionError(errno.EACCES,
                                                     "Permission denied"))])
        self.assertIsInstance(raised, OSError)
        self.assertIn("could not be READ", err)
        self.assertIn(str(self.cache), err)
        self.assertTrue(db_path.exists())
        self.assertEqual(self.mgr.indexes, {})

    def test_an_absent_cache_directory_is_still_an_absence(self):
        """The control for the check above: a cache directory that is not there
        is the state of a fresh system before the first update, and it stays an
        absence — silent here, explained by each read command in its own
        words."""
        missing = Path(self._td.name) / "not-synced-yet"
        err, raised = self._run(
            verify=False,
            extra=[patch.object(repo_mod, "REPO_DB_CACHE", missing)])
        self.assertIsNone(raised)
        self.assertEqual(err, "")
        self.assertEqual(self.mgr.indexes, {})



if __name__ == "__main__":
    unittest.main()
