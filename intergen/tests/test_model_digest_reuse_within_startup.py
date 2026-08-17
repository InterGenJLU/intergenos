# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Reading the model file once per startup instead of twice.

A daemon startup verified the SAME model file twice — once loading the chat
model, once attaching the Sentinel deep scanner to the same file — and each
verification read every byte. On the machine this was measured on that was
about 1.2 s paid twice for a 1.2 GB model; the cost scales with the file.

These cases pin both halves of the fix at once, because only one of them is
about speed:

  * a second verification of the SAME file in the same process does not read
    it again, and
  * the reuse is keyed on the file's identity and stores the DIGEST rather
    than a verdict, so a file that changed between the two verifications is
    read again, a caller with a different pin gets its own answer, and a
    mismatch is still a mismatch.

The second half is what keeps this a deduplication rather than a weakening: a
cached pass/fail verdict would mean the second consumer trusts a file it never
looked at.
"""

from __future__ import annotations

import builtins
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from intergen import model_manager
from intergen.interfaces.types import HardwareTierLevel, ModelInfo


class _OpenCounter:
    """Counts full reads of one specific file, by intercepting open()."""

    def __init__(self, target: Path) -> None:
        self._target = str(target)
        self._real_open = builtins.open
        self.opens = 0

    def __call__(self, file, *args, **kwargs):
        if str(file) == self._target:
            self.opens += 1
        return self._real_open(file, *args, **kwargs)


class DigestReuseWithinOneProcessTests(unittest.TestCase):

    def setUp(self):
        # getattr, not a direct call: against a tree WITHOUT this fix the
        # helper does not exist, and a setUp that raises turns every case into
        # "the attribute is missing" — which proves nothing about what the
        # code DOES. Reached this way, each case below fails against such a
        # tree for its own reason, or passes because the property it pins was
        # already true.
        clear = getattr(model_manager, "clear_digest_cache", None)
        if callable(clear):
            clear()
            self.addCleanup(clear)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = Path(self._tmp.name) / "llm"
        self.store.mkdir(parents=True)
        self.model_path = self.store / "a-model.gguf"
        self.model_path.write_bytes(b"weights, version one")
        self.digest = hashlib.sha256(
            self.model_path.read_bytes()).hexdigest()

    def _manager(self) -> model_manager.ModelManager:
        return model_manager.ModelManager(
            model_dir=self.store,
            manifest_path=self.store / "manifest.json",
            pins_path=self.store / "pins.json",
        )

    def _info(self, pin: str | None = None) -> ModelInfo:
        return ModelInfo(
            name="A Model", filename=self.model_path.name,
            repo_id="test/a-model", quant="Q4_K_M", size_gb=0.0,
            sha256=self.digest if pin is None else pin,
            tier=HardwareTierLevel.TIER_1,
            local_path=str(self.model_path),
        )

    def _identity(self) -> tuple:
        """The file's identity, derived here rather than through the module
        under test, so this helper works against a tree without the fix."""
        st = self.model_path.stat()
        return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)

    def _rewrite(self, data: bytes) -> None:
        """Replace the file's bytes and make sure its identity moved with it.

        st_mtime_ns has nanosecond resolution but a filesystem may not, so the
        rewrite is not assumed to change the timestamp — the case is only
        meaningful if the identity actually differs, which is asserted here
        rather than hoped for.
        """
        before = self._identity()
        self.model_path.write_bytes(data)
        after = self._identity()
        self.assertNotEqual(
            before, after,
            "the rewrite did not change the file's identity, so this case "
            "would prove nothing about re-reading a changed file")

    # ---- the deduplication itself -------------------------------------

    def test_the_same_file_is_read_once_for_two_verifications(self):
        """The whole point: the chat load and the scanner attach between them
        read the model once, not twice."""
        counter = _OpenCounter(self.model_path)
        manager = self._manager()
        with mock.patch.object(builtins, "open", counter):
            first = manager.verify_model(self._info())
            second = manager.verify_model(self._info())
        self.assertTrue(first)
        self.assertTrue(second)
        self.assertEqual(
            counter.opens, 1,
            f"the model file was read {counter.opens} times for two "
            f"verifications in one process")

    def test_a_second_manager_instance_reuses_the_same_read(self):
        """The two real consumers do not share a ModelManager: the daemon's
        model load uses one and the Sentinel attach constructs its own, so
        reuse scoped to an instance would not deduplicate anything."""
        counter = _OpenCounter(self.model_path)
        with mock.patch.object(builtins, "open", counter):
            self.assertTrue(self._manager().verify_model(self._info()))
            self.assertTrue(self._manager().verify_model(self._info()))
        self.assertEqual(counter.opens, 1)

    # ---- what keeps it a deduplication and not a weakening -------------

    def test_a_file_that_changed_is_read_again_and_refused(self):
        """A model replaced between the two verifications must not inherit the
        first one's answer."""
        manager = self._manager()
        self.assertTrue(manager.verify_model(self._info()))
        self._rewrite(b"weights, tampered with, and longer")
        counter = _OpenCounter(self.model_path)
        with mock.patch.object(builtins, "open", counter):
            verdict = manager.verify_model(self._info())
        self.assertFalse(verdict, "a changed file passed on a reused digest")
        self.assertEqual(counter.opens, 1, "the changed file was not re-read")

    def test_a_same_length_change_is_read_again_too(self):
        """Identity is not just the path and the size: a swap that preserves
        the length still misses."""
        original = self.model_path.read_bytes()
        manager = self._manager()
        self.assertTrue(manager.verify_model(self._info()))
        self._rewrite(b"X" * len(original))
        counter = _OpenCounter(self.model_path)
        with mock.patch.object(builtins, "open", counter):
            verdict = manager.verify_model(self._info())
        self.assertFalse(verdict)
        self.assertEqual(counter.opens, 1)

    def test_a_different_pin_still_gets_its_own_answer(self):
        """What is remembered is what the bytes hash to, not whether some
        earlier caller was satisfied."""
        manager = self._manager()
        self.assertTrue(manager.verify_model(self._info()))
        wrong = "0" * 64
        self.assertFalse(
            manager.verify_model(self._info(pin=wrong)),
            "a second caller's mismatching pin was answered with the first "
            "caller's verdict")

    def test_an_unpinned_model_is_still_refused(self):
        """The fail-closed no-pin refusal is upstream of all of this and stays
        exactly where it was."""
        self.assertFalse(self._manager().verify_model(self._info(pin="")))

    # ---- scope -------------------------------------------------------

    def test_clearing_forces_a_fresh_read(self):
        """A process boundary does this for free; the explicit form exists so
        a caller that wants the file read again can have it."""
        manager = self._manager()
        self.assertTrue(manager.verify_model(self._info()))
        model_manager.clear_digest_cache()
        counter = _OpenCounter(self.model_path)
        with mock.patch.object(builtins, "open", counter):
            self.assertTrue(manager.verify_model(self._info()))
        self.assertEqual(counter.opens, 1)

    def test_nothing_is_remembered_across_processes(self):
        """'Per startup' is only true if a fresh process starts cold. A startup
        IS a fresh process, so this is the property that keeps every startup
        reading the file from disk."""
        import subprocess
        import sys

        root = Path(model_manager.__file__).resolve().parent.parent
        # Populate this process's memory first, so a child that reported the
        # same non-zero number would be a real finding rather than a vacuous
        # zero.
        self.assertTrue(self._manager().verify_model(self._info()))
        self.assertGreater(len(model_manager._DIGEST_CACHE), 0)  # noqa: SLF001
        child = subprocess.run(
            [sys.executable, "-c",
             "from intergen import model_manager as m; "
             "print(len(m._DIGEST_CACHE))"],
            cwd=str(root), capture_output=True, text=True, timeout=120)
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertEqual(child.stdout.strip(), "0",
                         "a fresh process started with digests already in hand")

    def test_concurrent_verifications_all_agree(self):
        """What concurrency is and is not promised.

        The reuse is deliberately NOT single-flight: threads that reach the
        check before any of them has finished reading will each read the file,
        because holding a lock across a multi-gigabyte read to save a duplicate
        read would be the worse trade. What must hold under concurrency is that
        every caller gets the right verdict and nothing deadlocks or corrupts.
        The measured saving comes from the daemon's two consumers, which run
        one after the other."""
        import threading

        counter = _OpenCounter(self.model_path)
        verdicts: list[bool] = []
        lock = threading.Lock()
        start = threading.Barrier(6)

        def _verify():
            manager = self._manager()
            start.wait(timeout=30)
            v = manager.verify_model(self._info())
            with lock:
                verdicts.append(v)

        with mock.patch.object(builtins, "open", counter):
            threads = [threading.Thread(target=_verify) for _ in range(6)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=60)
        self.assertEqual(len(verdicts), 6, "a verifying thread did not finish")
        self.assertTrue(all(verdicts), "a concurrent verification disagreed")
        self.assertGreaterEqual(counter.opens, 1, "nothing was read at all")
        self.assertLessEqual(
            counter.opens, 6,
            "more full reads than there were callers — something re-read the "
            "file for a caller that never asked")

    def test_nothing_is_remembered_for_a_file_that_cannot_be_stat_ed(self):
        """No identity means no reuse — the failure direction is 'do the
        work', never 'reuse a digest for a file we could not identify'."""
        self.assertIsNone(
            model_manager._file_identity(self.store / "not-here.gguf"))  # noqa: SLF001
        self.assertIsNone(model_manager.cached_digest(None))
        model_manager.record_digest(None, "irrelevant")
        self.assertIsNone(model_manager.cached_digest(None))


if __name__ == "__main__":
    unittest.main()
