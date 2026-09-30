# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The command line can show what a version holds, so nobody reads the store by hand.

WHY THIS FILE EXISTS. The graphical client has always listed a version's entries
(it calls the engine's manifest verb to build its file list), and the command
line had no verb that did. A person at a terminal who wanted to know what a
version held had two options: restore something and see, or open the store
directly. The second is the one the engine exists to prevent, and it was the
only one that answered the question.

The verb reads the same engine surface the graphical client reads, so it stays
behind the administrator action: a version's entry list IS a path list, and the
user-data store holds every account's home directory in one place. This file
pins that tier as part of the behaviour.
"""

import io
import json
from contextlib import redirect_stdout, redirect_stderr
from unittest import mock

import pytest

from chronicle import api as _api
from chronicle import cli as _cli


MANIFEST = {
    "version_id": "v42",
    "layer": "user-data",
    "entries": [
        {"path": "/home/ada/documents", "type": "dir", "mode": 0o755},
        {"path": "/home/ada/documents/note", "type": "file", "size": 12,
         "sha256": "a" * 64, "mode": 0o644},
        {"path": "/home/ada/documents/link", "type": "symlink",
         "target": "note", "mode": 0o777},
    ],
}


def _run(argv, result):
    out, err = io.StringIO(), io.StringIO()
    backend = mock.Mock()
    backend.call.return_value = result
    with mock.patch.object(_cli, "Backend", return_value=backend), \
            redirect_stdout(out), redirect_stderr(err):
        rc = _cli.main(argv)
    return rc, out.getvalue(), err.getvalue(), backend


def test_the_command_line_has_a_verb_for_a_versions_entries():
    assert "contents" in _cli.COMMANDS
    parser_choices = _cli.build_parser()
    sub = next(a for a in parser_choices._actions
               if getattr(a, "choices", None) and "status" in getattr(a, "choices", {}))
    assert "contents" in sub.choices


def test_it_reads_the_engine_verb_the_graphical_client_reads():
    rc, _out, _err, backend = _run(["contents", "user-data", "v42"], MANIFEST)
    assert rc == 0
    verb = backend.call.call_args[0][0]
    assert verb == "manifest"
    assert verb in _api._VERBS


def test_plain_output_names_every_entry_with_its_kind_and_size():
    rc, out, _err, _backend = _run(["-v", "contents", "user-data", "v42"], MANIFEST)
    assert rc == 0
    for entry in MANIFEST["entries"]:
        assert entry["path"] in out
    assert "dir" in out and "file" in out and "symlink" in out
    assert "12" in out, "a file's size is not shown"


def test_json_output_is_machine_readable():
    rc, out, _err, _backend = _run(["contents", "user-data", "v42", "--json"], MANIFEST)
    assert rc == 0
    parsed = json.loads(out)
    assert [e["path"] for e in parsed["entries"]] == \
        [e["path"] for e in MANIFEST["entries"]]
    note = next(e for e in parsed["entries"] if e["path"].endswith("/note"))
    assert note["type"] == "file"
    assert note["size"] == 12


def test_the_entry_list_stays_behind_the_administrator_action():
    # A version's entry list is a path list, and one user-data store holds every
    # account's home directory. Adding a command-line reader must not widen the
    # read tier to it.
    assert "manifest" not in _api.READ_ONLY_VERBS


def test_a_name_that_is_not_valid_utf8_is_listed_without_a_crash():
    """A real home directory holds names that are not valid UTF-8 — a foreign
    archive, a bad rename. The manifest carries them as surrogate-escaped
    strings, and writing one bare to a UTF-8 stream raises UnicodeEncodeError.
    The listing quotes the path, as every other path this CLI prints is quoted.
    """
    import os
    odd = "/home/ada/" + os.fsdecode(b"broken-\xff\xfe-name.bin")
    manifest = {"version_id": "v43", "layer": "user-data",
                "entries": [{"path": odd, "type": "file", "size": 3,
                             "sha256": "b" * 64, "mode": 0o644}]}
    out = io.StringIO()
    backend = mock.Mock()
    backend.call.return_value = manifest
    with mock.patch.object(_cli, "Backend", return_value=backend), \
            redirect_stdout(out), redirect_stderr(io.StringIO()):
        rc = _cli.main(["-v", "contents", "user-data", "v43"])
    assert rc == 0
    printed = out.getvalue()
    # The escaped spelling is what reaches the terminal, and it round-trips.
    assert repr(odd) in printed
    # It really would have failed bare: the stream cannot encode the surrogate.
    with pytest.raises(UnicodeEncodeError):
        odd.encode("utf-8")
