# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""THE PLAIN STATUS ANSWER SAYS WHEN THE NEWEST VERSION IS SHORT OF ITS SOURCE.

`chronicle status` is the surface a person reads to learn whether this machine
is protected. The engine's payload has carried the fact for as long as the field
has existed — `newest_unreadable` names each layer's newest version and the
number of paths it could not read — and `status --json` hands it to a script
unchanged. The plain form read the capture time and stopped there, so a person
asking the machine directly was told a version short of its source was a whole
one: the same omission the window correction removed, on the last surface still
carrying it.

One case below is red at this lane's parent and green here. The two beside it are
controls, green on both, and they are what keeps the disclosure worth reading: a
machine whose newest versions read everything must say nothing about omissions,
or the line becomes noise a person learns to read past; and the JSON form, which
already carried the field, must be untouched by all of this.

Nothing here opens a socket or a display: the engine call is replaced by a
backend that returns a payload of this file's own making, which is what lets the
case state the rendering rule exactly.
"""

import io
import json
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest import mock

from chronicle import cli as _cli


def _status(payload, *argv):
    """`chronicle status` over a backend that answers with `payload`."""
    out, err = io.StringIO(), io.StringIO()
    backend = mock.Mock()
    backend.call.return_value = payload
    with mock.patch.object(_cli, "Backend", return_value=backend), \
            redirect_stdout(out), redirect_stderr(err):
        rc = _cli.main(["status", *argv])
    return rc, out.getvalue(), err.getvalue()


def _payload(newest_unreadable):
    """The shape `engine.status()` returns, with only this case's field varying."""
    return {
        "target": {"mountpoint": "/run/media/p/backup", "class": "removable"},
        "target_present": True,
        "target_free_bytes": 512 * 1024 * 1024 * 1024,
        "last_capture": {"config": 1759100000, "user-data": 1759200000},
        "newest_unreadable": newest_unreadable,
        "queue": {},
        "clock_skew_events": [],
        "retention_events": [],
        "pins": [],
    }


class ThePlainStatusAnswer(unittest.TestCase):

    def test_a_newest_version_short_of_its_source_is_named_with_its_count(self):
        rc, out, err = _status(_payload({
            "user-data": {"version_id": "0000000004-6b2dc07a701c", "unreadable": 1},
            "config": {"version_id": "0000000009-1f0a2b3c4d5e", "unreadable": 0},
        }))
        written = out + err
        self.assertEqual(rc, 0)
        self.assertIn("0000000004-6b2dc07a701c", written, (
            "the plain status answer did not name the newest user-data version "
            f"that could not read everything; it said {written!r}"))
        self.assertIn("1 path(s) could not be read", written, (
            "the count was not disclosed in the wording every other shipped "
            f"path uses; it said {written!r}"))
        self.assertNotIn("0000000009-1f0a2b3c4d5e", written, (
            "a layer whose newest version read everything was reported anyway"))

    def test_a_machine_whose_newest_versions_read_everything_says_nothing(self):
        """CONTROL. A line printed on every run is a line nobody reads."""
        rc, out, err = _status(_payload({
            "user-data": {"version_id": "0000000005-b15634aa7439", "unreadable": 0},
            "config": {"version_id": "0000000009-1f0a2b3c4d5e", "unreadable": 0},
        }))
        written = out + err
        self.assertEqual(rc, 0)
        self.assertNotIn("could not be read", written)
        self.assertNotIn("Newest", written)

    def test_the_json_form_is_untouched(self):
        """CONTROL. The field was already there for a script; only the human
        surface changed, so the payload must come back exactly as given."""
        given = _payload({
            "user-data": {"version_id": "0000000004-6b2dc07a701c", "unreadable": 1},
        })
        rc, out, _err = _status(given, "--json")
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["newest_unreadable"], {
            "user-data": {"version_id": "0000000004-6b2dc07a701c", "unreadable": 1},
        })


if __name__ == "__main__":
    unittest.main()
