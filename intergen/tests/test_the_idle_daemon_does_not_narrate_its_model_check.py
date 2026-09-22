# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""An idle daemon stops narrating a check whose answer has not changed.

The assistant re-verifies the embedding model's digest twice a minute while it
is doing nothing else, and wrote two lines at INFO every time — one saying it
already had the digest in this process, one saying the digest matched. Measured
on this project's workstation over ten idle minutes on 2026-09-22: FORTY journal
lines, every one of them one of those two, and nothing else logged at all. A log
in which a machine at rest produces only repetition is one nobody reads, and the
one line that matters — a digest that does NOT match — arrives in the middle of
it looking the same size as the rest.

What these cases pin:

* the FIRST verification after a start still speaks at INFO, because reading a
  model file and finding it correct is an event;
* a later verification of the SAME file, where nothing was re-read and nothing
  changed, drops to DEBUG — the fact is still recorded for anyone who turns
  debug logging on, and is not thrown away;
* a MISMATCH is at ERROR every time, first or hundredth. The quieting must not
  reach the case the check exists for;
* the refusal to verify without a pin stays at ERROR.

The check itself is untouched: the same digest is compared against the same pin
in the same place. Only the level of the sentence about it moves.
"""

from __future__ import annotations

import hashlib
import logging
import tempfile
import unittest
from pathlib import Path

from intergen import model_manager
from intergen.hardware import HardwareTierLevel
from intergen.model_manager import ModelInfo, ModelManager

LOGGER_NAME = "intergen.model_manager"


def _a_model_file(tmp: str, body: bytes = b"a small stand-in for a model"):
    """A real file on disk with its real digest, so nothing here is stubbed."""
    path = Path(tmp) / "nomic-embed-text-v1.5.Q8_0.gguf"
    path.write_bytes(body)
    digest = hashlib.sha256(body).hexdigest()
    return path, digest


def _model(path: Path, sha256: str) -> ModelInfo:
    return ModelInfo(
        name="nomic-embed-text-v1.5", filename=path.name,
        repo_id="test/nomic-embed-text-v1.5", quant="Q8_0", size_gb=0.1,
        sha256=sha256, tier=HardwareTierLevel.TIER_1,
        local_path=str(path), downloaded=True,
    )


def _levels(records, needle):
    """The level names of the captured records whose message mentions needle."""
    return [r.levelname for r in records if needle in r.getMessage()]


class TheRepeatedCheckIsQuiet(unittest.TestCase):

    def setUp(self):
        model_manager.clear_digest_cache()
        self.addCleanup(model_manager.clear_digest_cache)
        self.manager = ModelManager.__new__(ModelManager)

    def test_the_first_verification_after_a_start_speaks_at_info(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, digest = _a_model_file(tmp)
            model = _model(path, digest)
            with self.assertLogs(LOGGER_NAME, level=logging.DEBUG) as caught:
                self.assertTrue(self.manager.verify_model(model))
        self.assertEqual(_levels(caught.records, "SHA256 verified"), ["INFO"],
                         "the first verification after a start should be "
                         "visible at INFO")

    def test_a_repeat_of_the_same_check_drops_to_debug(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, digest = _a_model_file(tmp)
            model = _model(path, digest)
            self.assertTrue(self.manager.verify_model(model))
            with self.assertLogs(LOGGER_NAME, level=logging.DEBUG) as caught:
                self.assertTrue(self.manager.verify_model(model))
        at_info = [r.getMessage() for r in caught.records
                   if r.levelno >= logging.INFO]
        self.assertEqual(at_info, [],
                         "an idle repeat still writes at INFO or above:\n"
                         + "\n".join(at_info))

    def test_the_repeat_is_still_recorded_at_debug(self):
        """Quieter, not silent. The fact stays available to anyone who asks."""
        with tempfile.TemporaryDirectory() as tmp:
            path, digest = _a_model_file(tmp)
            model = _model(path, digest)
            self.assertTrue(self.manager.verify_model(model))
            with self.assertLogs(LOGGER_NAME, level=logging.DEBUG) as caught:
                self.assertTrue(self.manager.verify_model(model))
        self.assertEqual(_levels(caught.records, "SHA256 verified"), ["DEBUG"])
        self.assertEqual(
            _levels(caught.records, "already computed in this process"),
            ["DEBUG"])

    def test_the_check_still_happens_on_every_call(self):
        """The quieting must not turn into skipping. A file whose digest stops
        matching the pin is refused on the repeat, not waved through."""
        with tempfile.TemporaryDirectory() as tmp:
            path, digest = _a_model_file(tmp)
            model = _model(path, digest)
            self.assertTrue(self.manager.verify_model(model))
            wrong = _model(path, "0" * 64)
            self.assertFalse(self.manager.verify_model(wrong))

    def test_a_mismatch_is_loud_even_on_a_repeat(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, digest = _a_model_file(tmp)
            self.assertTrue(self.manager.verify_model(_model(path, digest)))
            wrong = _model(path, "0" * 64)
            with self.assertLogs(LOGGER_NAME, level=logging.DEBUG) as caught:
                self.assertFalse(self.manager.verify_model(wrong))
        self.assertEqual(_levels(caught.records, "SHA256 MISMATCH"), ["ERROR"],
                         "a mismatch on a repeated check must stay at ERROR")

    def test_a_model_with_no_pin_is_still_refused_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, _digest = _a_model_file(tmp)
            unpinned = _model(path, "")
            with self.assertLogs(LOGGER_NAME, level=logging.DEBUG) as caught:
                self.assertFalse(self.manager.verify_model(unpinned))
        self.assertEqual(_levels(caught.records, "Cannot verify"), ["ERROR"])


class TheFirstReadIsStillAnnounced(unittest.TestCase):

    def setUp(self):
        model_manager.clear_digest_cache()
        self.addCleanup(model_manager.clear_digest_cache)

    def test_the_read_itself_is_announced_at_info(self):
        """The full read of a model file is real work and stays visible."""
        manager = ModelManager.__new__(ModelManager)
        with tempfile.TemporaryDirectory() as tmp:
            path, digest = _a_model_file(tmp)
            with self.assertLogs(LOGGER_NAME, level=logging.DEBUG) as caught:
                self.assertTrue(manager.verify_model(_model(path, digest)))
        self.assertEqual(_levels(caught.records, "Verifying SHA256"), ["INFO"])


if __name__ == "__main__":
    unittest.main()
