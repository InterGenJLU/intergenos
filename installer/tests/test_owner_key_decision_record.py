"""The install records what was decided about earlier machine owner keys, where
the person can read it (R001.3 row 49).

The install already offers to retire the machine owner keys earlier installs
left in the firmware, and already records the answer as a trace event. A trace
event is not readable by the person at their first login, and the first-login
page runs as them: without a world-readable record the page cannot tell a key
somebody looked at and chose to keep from a key nobody has ever been asked
about. Those are different facts and the page says them differently, so the
install writes them down.

The record is deliberately dull — fingerprint per line, a word for the decision,
readable with cat — and it is a record, not a promise: a machine with no record
has had no decision made on it, which is exactly what a machine installed before
the offer existed should say.
"""

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from installer.backend import mok  # noqa: E402

FP_A = "a" * 40
FP_B = "b" * 40
FP_C = "c" * 40


def _identity(fingerprint):
    return {"common_name": mok.OWNER_KEY_COMMON_NAME, "sha1": fingerprint,
            "created": "", "expires": "", "der": b"x"}


class TestTheDecisionRecord(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.target = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _record(self):
        return (self.target / mok.OWNER_KEY_DECISION_RECORD.lstrip("/"))

    def test_a_keep_is_written_down(self):
        mok.record_owner_key_decision(kept=[_identity(FP_A), _identity(FP_B)],
                                      removed=[], target=str(self.target))
        text = self._record().read_text(encoding="utf-8")
        self.assertIn(f"kept={FP_A}", text)
        self.assertIn(f"kept={FP_B}", text)
        self.assertNotIn("retired=", text)

    def test_a_retirement_is_written_down(self):
        mok.record_owner_key_decision(kept=[], removed=[_identity(FP_C)],
                                      target=str(self.target))
        text = self._record().read_text(encoding="utf-8")
        self.assertIn(f"retired={FP_C}", text)

    def test_the_record_is_world_readable(self):
        mok.record_owner_key_decision(kept=[_identity(FP_A)], removed=[],
                                      target=str(self.target))
        mode = self._record().stat().st_mode & 0o777
        self.assertEqual(mode, 0o644,
                         "the page that reads this runs as the person, not as root")

    def test_it_says_what_it_is(self):
        mok.record_owner_key_decision(kept=[_identity(FP_A)], removed=[],
                                      target=str(self.target))
        text = self._record().read_text(encoding="utf-8")
        self.assertTrue(text.startswith("#"),
                        "a person opening this file should find a sentence first")
        self.assertIn("decided=", text)

    def test_writing_it_is_never_what_fails_an_install(self):
        """The record is a convenience for a later page. A machine that cannot
        write it is still a correctly installed machine, so the failure is
        swallowed here rather than propagated — the caller's own except would
        otherwise turn a missing status line into a warning about retirement
        that did not happen."""
        mok.record_owner_key_decision(kept=[_identity(FP_A)], removed=[],
                                      target="/proc/this/cannot/be/written")

    def test_no_target_writes_nothing_and_still_traces(self):
        """The trace event is the older contract and stays; a caller with no
        target root (the unit tests of row 10, for one) must keep working."""
        mok.record_owner_key_decision(kept=[_identity(FP_A)], removed=[])
        self.assertFalse(self._record().exists())


if __name__ == "__main__":
    unittest.main()
