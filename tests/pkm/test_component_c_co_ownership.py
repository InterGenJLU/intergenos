# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Component C — co-ownership-aware `pkm remove` / `iso-prep`.

Catch #9: `pkm iso-prep` pruning a MIRROR-only package (desktop/vala) unlinked
the 372-file payload a SHIPPED package (core/vala-pass1) co-owns at the same
paths, because remove unlinked every manifest path without asking whether
another installed package still owned it. The chokepoint in
PackageRemover.remove now skips any path a still-installed package co-owns,
counts it, and reports the owning package(s). The removed package's DB rows
still cascade away — the co-owner becomes sole owner, the truthful end state.
`--force` never overrides the skip (it scopes to reverse-dependency override).

Acceptance criteria implemented here:
  C1 two packages sharing paths — removing one retains every shared path
     (byte-identical), deletes its sole-owned paths, reports the retention with
     owner names.
  C2 iso-prep-style removal over a co-owning set — every path a still-installed
     package owns still exists on disk afterward (gate-4.5 audit clean).
  C3 config interplay — the co-ownership check runs FIRST (a co-owned config is
     retained by co-ownership); sole-owned config preservation still applies.
  C4 `--force` still refuses to delete a co-owned path.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from pkm.database import PackageDB
from pkm.remover import PackageRemover


class ComponentCCoOwnershipTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="pkm-c-test-")
        self.root = Path(self._tmp)
        self.db = PackageDB(db_path=str(self.root / "pkm.db"), root=str(self.root))

    def tearDown(self):
        try:
            self.db.close()
        except Exception:
            pass
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _write(self, rel, content=b"payload"):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)

    def _install(self, name, files):
        for rel in files:
            if not (self.root / rel).exists():
                self._write(rel, content=f"{rel}".encode())
        pid = self.db.add_installed(name=name, version="1.0",
                                    install_method="archive")
        self.db.add_files(pid, files)
        return pid

    # ---- C1 ---------------------------------------------------------------
    def test_c1_removing_co_owner_retains_shared_paths(self):
        shared = ["usr/lib/vala/libvala.so", "usr/share/vala/vala.h"]
        pass1_only = ["usr/bin/valac"]
        vala_only = ["usr/bin/vala-gen-introspect"]
        # shipped package registers the shared payload first
        self._install("vala-pass1", shared + pass1_only)
        # mirror-only package co-owns the same shared paths
        self._install("vala", shared + vala_only)

        ok, msg = PackageRemover(self.db, root=str(self.root)).remove("vala")
        self.assertTrue(ok, msg)

        # every shared path is byte-identical on disk (retained, not unlinked)
        for rel in shared:
            self.assertTrue((self.root / rel).exists(),
                            f"co-owned {rel} must be retained")
            self.assertEqual((self.root / rel).read_bytes(), rel.encode())
        # vala's sole-owned path is gone
        self.assertFalse((self.root / vala_only[0]).exists())
        # vala-pass1's own payload is untouched
        self.assertTrue((self.root / pass1_only[0]).exists())
        # the retention is reported, naming the owner
        self.assertIn("co-owned", msg)
        self.assertIn("vala-pass1", msg)

        # vala-pass1 now verifies clean (nothing of its lost)
        v = self.db.verify_package("vala-pass1")
        self.assertEqual((v["missing"], v["modified"]), ([], []))
        # vala's rows cascaded away
        self.assertIsNone(self.db.get_installed("vala"))

    # ---- C2 ---------------------------------------------------------------
    def test_c2_iso_prep_scale_audit_clean(self):
        # A shipped package co-owns paths with several mirror-only packages.
        shared = [f"usr/lib/common/lib{i}.so" for i in range(5)]
        self._install("core-shipped", shared + ["usr/bin/shipped"])
        mirror = []
        for m in range(3):
            name = f"mirror{m}"
            self._install(name, shared + [f"usr/bin/{name}"])
            mirror.append(name)

        rem = PackageRemover(self.db, root=str(self.root))
        for name in mirror:  # iso-prep prunes each mirror-only package
            ok, _ = rem.remove(name)
            self.assertTrue(ok)

        # gate-4.5 audit: every path a still-installed package owns exists.
        for f in self.db.get_files("core-shipped"):
            if f["is_dir"]:
                continue
            self.assertTrue((self.root / f["path"]).exists(),
                            f"shipped path {f['path']} must survive the prune")
        v = self.db.verify_package("core-shipped")
        self.assertEqual((v["missing"], v["modified"]), ([], []))

    # ---- C3 ---------------------------------------------------------------
    def test_c3_co_ownership_runs_before_config_and_sole_config_preserved(self):
        # A co-owned CONFIG path is retained by co-ownership (runs first), and a
        # sole-owned, user-edited config is still preserved by the A20 branch.
        co_cfg = "etc/shared/co.conf"
        sole_cfg = "etc/only/sole.conf"
        self._install("keeper", [co_cfg, "usr/bin/keeper"])
        pid = self._install("goner", [co_cfg, sole_cfg])
        # user edits the sole-owned config after install -> must be preserved
        (self.root / sole_cfg).write_bytes(b"user-edited")

        ok, msg = PackageRemover(self.db, root=str(self.root)).remove("goner")
        self.assertTrue(ok, msg)
        # co-owned config retained (by co-ownership, keeper still owns it)
        self.assertTrue((self.root / co_cfg).exists())
        self.assertIn("keeper", msg)
        # sole-owned edited config preserved (not co-owned, A20 branch)
        self.assertTrue((self.root / sole_cfg).exists())
        v = self.db.verify_package("keeper")
        self.assertEqual((v["missing"], v["modified"]), ([], []))

    # ---- C4 ---------------------------------------------------------------
    def test_c4_force_does_not_override_co_ownership(self):
        shared = ["usr/lib/x/libx.so"]
        self._install("owner-a", shared + ["usr/bin/a"])
        self._install("owner-b", shared + ["usr/bin/b"])
        ok, msg = PackageRemover(self.db, root=str(self.root)).remove(
            "owner-b", force=True)
        self.assertTrue(ok, msg)
        # --force scopes to reverse-dep override; co-owned bytes are NEVER deleted
        self.assertTrue((self.root / shared[0]).exists(),
                        "--force must not delete a co-owned path")
        self.assertIn("co-owned", msg)
        # owner-a intact
        self.assertFalse((self.root / "usr/bin/b").exists())
        self.assertTrue((self.root / "usr/bin/a").exists())

    def test_co_owned_query_chunks_large_lists(self):
        # The IN-list is chunked (SQLite 999-variable limit). Prove correctness
        # past a single chunk: >900 shared paths all come back co-owned.
        shared = [f"usr/share/many/f{i:04d}" for i in range(1000)]
        self._install("big-a", shared)
        self._install("big-b", shared + ["usr/bin/big-b"])
        rem = PackageRemover(self.db, root=str(self.root))
        co = rem._co_owned_paths(self.db.get_installed("big-b")["id"], shared)
        self.assertEqual(len(co), 1000)
        self.assertTrue(all(co[p] == ["big-a"] for p in shared))


if __name__ == "__main__":
    unittest.main()
