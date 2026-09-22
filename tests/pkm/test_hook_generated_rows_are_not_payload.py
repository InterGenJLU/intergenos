# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A file a package's hook made on this machine is not the package's payload.

WHAT WAS MEASURED, AND WHERE. On an installed machine on 2026-09-22, after an
upgrade of one desktop package whose lifecycle hook re-applies a choice the
machine's owner had made, `pkm files <that package>` listed three of the
machine's OWN network connection files, the hook's dispatcher fragment and the
hook's record file as package-owned rows. Every one of those rows carries
is_generated=1, which is the column that already says "this row was not
deployed from the archive payload; the package's hook created it here". The
package row was written by that same transaction, so the rows are not an
artefact of some older install.

Ownership of those rows was full ownership, by the schema's own words, and that
is the defect this file pins:

  * `pkm remove <package>` would UNLINK them — the machine's network profiles
    among them. Removing a package may not delete files the package never
    shipped and cannot put back.
  * `pkm verify <package>` reported them MISSING once a documented, supported
    action removed them — undoing the choice removes the dispatcher and the
    record — so a person who followed the documentation and then verified was
    told their package was damaged.

Both are the same root: a hook's products are recorded in the same class as
payload. The fix belongs in the package manager, because every package with a
lifecycle hook has both exposures, not just the one that was measured.

What is NOT changed, and is pinned here too: ordinary payload is still removed,
a genuinely missing payload file still flags, and the rows are still OWNED —
`pkm files` and `pkm provides` still answer with them. What changes is what
removal and verification DO with them.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from pkm.database import PackageDB
from pkm.remover import PackageRemover
from pkm import cli


def _args(package=None, verify_all=False, mode="strict"):
    return SimpleNamespace(package=package, verify_all=verify_all,
                           verify_mode=mode, verify_detail=False)


class HookGeneratedRows(unittest.TestCase):
    """A package with one archive file and two files its hook made here."""

    ARCHIVE = "usr/libexec/example/helper"
    #: The shape measured on the machine: a file the package never shipped,
    #: which the hook caused to be written, under a directory that belongs to
    #: something else entirely.
    MACHINES_OWN = "etc/NetworkManager/system-connections/wired.nmconnection"
    #: The hook's own product, which the documented undo action deletes.
    HOOK_PRODUCT = "etc/NetworkManager/dispatcher.d/50-example"

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="pkm-generated-rows-")
        self.root = Path(self._tmp) / "root"
        self.db_path = Path(self._tmp) / "pkm.db"
        for rel in (self.ARCHIVE, self.MACHINES_OWN, self.HOOK_PRODUCT):
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f"contents of {rel}\n")
        self.db = PackageDB(self.db_path, root=str(self.root))
        pid = self.db.add_installed("example", "1.0", release=1, tier="desktop")
        self.db.add_files(pid, [self.ARCHIVE])
        self.db.record_generated_files(pid, [self.MACHINES_OWN,
                                             self.HOOK_PRODUCT])

    def tearDown(self):
        try:
            self.db.close()
        except Exception:
            pass
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _remove(self):
        remover = PackageRemover(self.db, root=str(self.root))
        return remover.remove("example", run_pre_remove_hook=False,
                              run_post_remove_hook=False)


class TestRemoveDoesNotUnlinkWhatTheHookMade(HookGeneratedRows):

    def test_the_machines_own_file_survives_the_removal(self):
        ok, msg = self._remove()
        self.assertTrue(ok, msg)
        self.assertTrue(
            (self.root / self.MACHINES_OWN).is_file(),
            "removing the package unlinked a file the package never shipped "
            "and cannot put back:\n" + msg)

    def test_the_hooks_own_product_survives_the_removal(self):
        ok, msg = self._remove()
        self.assertTrue(ok, msg)
        self.assertTrue((self.root / self.HOOK_PRODUCT).is_file(),
                        "removing the package unlinked its hook's product:\n"
                        + msg)

    def test_the_removal_says_what_it_left_behind(self):
        ok, msg = self._remove()
        self.assertTrue(ok, msg)
        self.assertIn("generated", msg.lower(),
                      "the removal left files on disk and said nothing about "
                      "them:\n" + msg)

    def test_the_packages_own_payload_is_still_removed(self):
        """The non-masking half: this is still a removal."""
        ok, msg = self._remove()
        self.assertTrue(ok, msg)
        self.assertFalse((self.root / self.ARCHIVE).exists(),
                         "the package's own payload was left on disk:\n" + msg)


class TestVerifyAfterASupportedUndo(HookGeneratedRows):

    def test_a_generated_file_that_is_gone_is_not_reported_missing(self):
        (self.root / self.HOOK_PRODUCT).unlink()
        result = self.db.verify_package("example")
        self.assertNotIn(self.HOOK_PRODUCT, result["missing"],
                         "undoing a documented choice made verify call the "
                         "package damaged")

    def test_it_is_named_in_its_own_bucket_rather_than_dropped(self):
        """Quieter is not the same as silent: the absence is still reported."""
        (self.root / self.HOOK_PRODUCT).unlink()
        result = self.db.verify_package("example")
        self.assertIn(self.HOOK_PRODUCT, result.get("generated_absent", []),
                      "the absence was neither a fault nor reported anywhere; "
                      "a masked absence is worse than a wrong one")

    def test_the_command_exits_zero_after_the_documented_undo(self):
        (self.root / self.HOOK_PRODUCT).unlink()
        (self.root / self.MACHINES_OWN).unlink()
        self.db.close()
        with PackageDB(self.db_path, root=str(self.root), read_only=True) as db:
            rc = cli.cmd_verify(db, _args(package="example"))
        self.assertIn(rc, (0, None),
                      "a person who followed the documentation and then "
                      f"verified was told the package is damaged (rc={rc})")

    def test_a_genuinely_missing_payload_file_still_flags(self):
        """The non-masking half: a real loss is still a fault."""
        (self.root / self.ARCHIVE).unlink()
        result = self.db.verify_package("example")
        self.assertIn(self.ARCHIVE, result["missing"])

    def test_the_rows_are_still_owned(self):
        """This lane changes what remove and verify DO with the rows, not who
        owns them: the package still answers for them."""
        owned = {f["path"] for f in self.db.get_files("example")}
        self.assertIn(self.MACHINES_OWN, owned)
        self.assertIn(self.HOOK_PRODUCT, owned)


if __name__ == "__main__":
    unittest.main()
