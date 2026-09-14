# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Every install keeps the root password the person chose (R001.3 row 5).

WHAT WAS WRONG (proven on the Zephyrus 2026-09-04 by executing the install
phases; confirmed empty on three more machines). The installer writes the
chosen root hash in the "users" phase, then runs every package's post_install
hook inside the target in the "hooks" phase — and the shadow package's hook
ended with `usermod -p '!' root`. So every installed system booted with root
locked and no rescue credential. On the Intel HP laptop the set-password step
itself once returned success in 61 ms with no hash on the target.

WHAT THIS PROVES. (1) set_root_password reads the shadow field back and fails
loudly when the hash is not there. (2) verify_root_password_kept, run after
the hooks, fails loudly when a hook replaced the hash, and passes when it is
intact; the trace never carries the hash, only a description of the field.
(3) The shadow recipe's hook no longer touches root's password field, and the
media path locks root explicitly before the D-007 gate that proves it, with
the canonical sentinel. (4) The orchestrator wires the kept-check after the
hooks (source assertion on install.py).
"""

import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from installer.backend import users

REPO = Path(__file__).resolve().parent.parent.parent
SHADOW_BUILD = REPO / "packages/core/shadow/build.sh"
BUILD_SH = REPO / "scripts/build-intergenos.sh"
IMAGE_SH = REPO / "scripts/create-image.sh"
INSTALL_PY = REPO / "installer/backend/install.py"

HASH = "$6$saltsaltsalt$" + "A" * 86


class _Target:
    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "etc").mkdir()
        self.shadow = self.tmp / "etc/shadow"
        self.shadow.write_text("root:x:19000:0:99999:7:::\nbin:*:19000:0:99999:7:::\n")

    def set_root(self, field):
        lines = []
        for line in self.shadow.read_text().splitlines():
            parts = line.split(":")
            if parts[0] == "root":
                parts[1] = field
            lines.append(":".join(parts))
        self.shadow.write_text("\n".join(lines) + "\n")

    def close(self):
        shutil.rmtree(self.tmp)


class SetRootPasswordReadsBack(unittest.TestCase):
    def setUp(self):
        self.t = _Target()
        self.events = []

    def tearDown(self):
        self.t.close()

    def _run(self, chpasswd_writes):
        def fake_run(cmd, **kw):
            class R: returncode = 0; stdout = ""; stderr = ""
            if cmd[0] == "chpasswd" and chpasswd_writes:
                self.t.set_root(kw["input"].split(":", 1)[1].strip())
            return R()
        with patch.object(users, "_sha512crypt_hash", return_value=HASH), \
             patch.object(users.trace, "traced_run", side_effect=fake_run), \
             patch.object(users.trace, "trace_event",
                          side_effect=lambda *a, **k: self.events.append((a, k))):
            return users.set_root_password(self.t.tmp, password="hunter22-long")

    def test_hash_written_is_read_back_and_returned(self):
        self.assertEqual(self._run(chpasswd_writes=True), HASH)
        rec = [k for a, k in self.events if a[0] == "root_password_written"][0]
        self.assertEqual(rec["field"], "hash:6")
        self.assertNotIn(HASH, str(self.events))

    def test_silent_success_with_no_hash_on_target_fails_loudly(self):
        with self.assertRaises(RuntimeError) as cm:
            self._run(chpasswd_writes=False)
        msg = str(cm.exception)
        self.assertIn("read-back", msg)
        self.assertIn("locked-or-placeholder:x", msg)
        self.assertNotIn(HASH, msg)


class KeptAcrossHooks(unittest.TestCase):
    def setUp(self):
        self.t = _Target()
        self.events = []
        self.p = patch.object(users.trace, "trace_event",
                              side_effect=lambda *a, **k: self.events.append((a, k)))
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self.t.close()

    def test_intact_hash_passes_and_is_traced_without_the_hash(self):
        self.t.set_root(HASH)
        self.assertTrue(users.verify_root_password_kept(self.t.tmp, HASH))
        rec = [k for a, k in self.events if a[0] == "root_password_kept"][0]
        self.assertEqual(rec["field"], "hash:6")
        self.assertNotIn(HASH, str(self.events))

    def test_the_r0012_defect_is_caught(self):
        """A hook wrote `!` after the users phase (what shadow's hook did)."""
        self.t.set_root("!")
        with self.assertRaises(RuntimeError) as cm:
            users.verify_root_password_kept(self.t.tmp, HASH)
        self.assertIn("post-install hook replaced it", str(cm.exception))
        self.assertIn("locked-or-placeholder:!", str(cm.exception))

    def test_a_different_hash_is_caught(self):
        self.t.set_root("$6$other$" + "B" * 86)
        with self.assertRaises(RuntimeError):
            users.verify_root_password_kept(self.t.tmp, HASH)

    def test_missing_shadow_is_caught(self):
        self.t.shadow.unlink()
        with self.assertRaises(RuntimeError) as cm:
            users.verify_root_password_kept(self.t.tmp, HASH)
        self.assertIn("absent", str(cm.exception))


class FieldDescriptionNeverCarriesTheHash(unittest.TestCase):
    def test_descriptions(self):
        d = users._describe_password_field
        self.assertEqual(d(None), "absent")
        self.assertEqual(d(""), "empty")
        self.assertEqual(d("!"), "locked-or-placeholder:!")
        self.assertEqual(d("x"), "locked-or-placeholder:x")
        self.assertEqual(d("$y$j9T$abc"), "hash:y")
        self.assertEqual(d(HASH), "hash:6")
        self.assertNotIn("saltsalt", d(HASH))


class TheLockLivesInTheMediaPath(unittest.TestCase):
    def test_shadow_hook_no_longer_locks_root(self):
        src = SHADOW_BUILD.read_text()
        hook = src[src.index("post_install()"):]
        self.assertNotRegex(hook, r"^\s*(usermod|passwd)\b.*\broot\b", 
                            "the shadow post_install hook runs on installed targets after the "
                            "person's password is set; it must not touch root's field")

    def test_build_locks_root_with_the_canonical_sentinel_before_gate_d(self):
        src = BUILD_SH.read_text()
        lock = src.index("chroot \"$IGOS\" usermod -p '!' root")
        gate = src.index('check-d007-runtime.sh" "${IGOS}"')
        self.assertLess(lock, gate, "the lock must precede the gate that proves it")

    def test_image_script_uses_the_canonical_sentinel(self):
        src = IMAGE_SH.read_text()
        self.assertIn("usermod -p '!' root", src)
        self.assertNotRegex(src, r"^\s*chroot \"\$MOUNT_POINT\" passwd -l root", )

    def test_orchestrator_checks_the_hash_after_the_hooks(self):
        src = INSTALL_PY.read_text()
        users_phase = src.index("root_hash = users.set_root_password(")
        hooks_run = src.index("hooks.run_post_install_hooks(")
        kept = src.index("users.verify_root_password_kept(target, root_hash)")
        done = src.index("result.phase_completed = PHASE_HOOKS")
        self.assertLess(users_phase, hooks_run)
        self.assertLess(hooks_run, kept)
        self.assertLess(kept, done)


if __name__ == "__main__":
    unittest.main()
