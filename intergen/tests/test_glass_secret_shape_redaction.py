# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Secret-shaped CONTENT must not reach the glass record verbatim.

THE DEFECT. intergen/glass.py replaces the value of any detail key whose NAME
looks credential-shaped. That is a name predicate, so it cannot see a secret
that arrives as content — and content is how a secret actually reaches this
writer: inside a prompt, a command line, a tool result, or a file the user asked
about. Measured on the shipped tree by a second seat: of an eight-case corpus,
SEVEN secret-shaped values written under ordinary key names ("user_msg",
"command", "file_content", "url", "remote", "text", "header_value") reached
glass.jsonl BYTE-IDENTICAL to the input. The eighth, a control placed under the
key name "api_key", was redacted — which proves the instrument can detect
redaction and that the seven are a real negative rather than a broken
measurement.

The file is 0600 and owner-only, so this is not a cross-account exposure on its
own. It is a durable plaintext copy of secrets the person handled, kept for up
to the full retention ceiling, on a disk they may later share, back up, or
attach to a bug report. The module's stated security line is that credential
VALUES are never written; on this evidence that line held only for values a
caller had already labelled as credentials.

THE CORPUS BELOW IS THAT SEAT'S, case for case, from its measurement script
c-c05-redaction-corpus.py and the finding it supports, c05-confirmation.md. Both
files, and their hashes, are in this change's sealed evidence set; the hashes are
not repeated here because a bare 64-character hex string in a public file is
refused by the repository's own content gate, and rightly so — it cannot tell a
digest from a key. None of the values below is a real credential: they are the
documentation examples those formats publish for the purpose (the vendor's own
EXAMPLE key, a truncated key body, a throwaway password).

WHAT IS DELIBERATELY NOT DONE HERE: no entropy heuristic. This writer's mandate
is full-fidelity capture, and a threshold that fires on ordinary content would
put unexplained holes in the record while still missing structured secrets. Only
named shapes, each testable against this corpus, each leaving its match visible
as an attested placeholder saying WHAT kind of thing was removed.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import intergen.glass as glass
from intergen.tests import glass_rows


def _reset(tmp: str) -> None:
    os.environ["XDG_STATE_HOME"] = tmp
    os.environ.pop("INTERGEN_GLASS", None)
    glass._glass = None


def _rows(tmp: str) -> list[dict]:
    return glass_rows.read(tmp)


def _row(tmp: str, phase: str, event: str) -> dict:
    """The row THIS test wrote, selected by what it IS rather than by position.

    This selection was written here first, when the writer's opening row moved
    every position in this file. It now delegates to
    intergen/tests/glass_rows.py so the corpus has ONE selection helper rather
    than a copy per file — the same reason the two secret-shape writers share
    one definition of what a secret looks like.
    """
    return glass_rows.first(_rows(tmp), phase=phase, event=event)


# ── The corpus, case for case from the confirming seat's measurement ────────
# (label, key name used, value written, the shape that must be named in the
#  placeholder that replaces it)
SECRET_CORPUS = [
    ("a private key body inside prompt text", "user_msg",
     "here is my key:\n-----BEGIN OPENSSH PRIVATE KEY-----\n"
     "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2\n"
     "-----END OPENSSH PRIVATE KEY-----",
     "private-key-block"),
    ("a provider key in free text", "text",
     "use sk-ant-api03-QYh2n8Zx7RmL0pWvTbKcJdFgHsNuEiAoXyZ1234567890abcdef",
     "provider-token"),
    ("an access key id in a command", "command",
     "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE "
     "AWS_SECRET=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY aws s3 ls",
     "provider-token"),
    ("a bearer value under a neutral key", "header_value",
     "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxIn0.sig",
     "json-web-token"),
    ("a shadow line in a file the user asked about", "file_content",
     "root:$6$rounds=5000$abcdefgh$K3sJd0.pW7xQ2mNv8LcTz1yR4uHgFb"
     ":19000:0:99999:7:::",
     "crypt-hash"),
    ("a database URL with the password inline", "url",
     "postgresql://intergen:hunter2@localhost:5432/intergen",
     "url-with-password"),
    ("a github token in a git remote", "remote",
     "https://ghp_16C7e42F292c6912E7710c838347Ae178B4a@github.com/x/y.git",
     "provider-token"),
    # ── Added by the command-line pass ──────────────────────────────────────
    # The installed-system gate tests/installed/test_gate_secret_redaction.py
    # measured these against the shipped release and they reached the record in
    # full. They are how a person actually hands a credential to a machine: in
    # a command they typed or pasted, under a key name that describes the
    # command rather than the secret inside it.
    ("a bearer token in a curl command", "command",
     "curl -H 'Authorization: Bearer abc123secretvalue' https://example.invalid",
     "bearer-token"),
    ("a password given as an attached short option", "command",
     "mysql -u admin -pHunter2Example --host db.example.invalid",
     "password-option"),
    ("a password given as a long option", "command",
     "mysqldump --password=Hunter2Example --host db.example.invalid intergen",
     "password-option"),
    ("a password given as a separated long option", "command",
     "curl --password Hunter2Example https://example.invalid/upload",
     "password-option"),
    ("a credential given as an environment assignment", "command",
     "PGPASSWORD=Hunter2Example psql -h db.example.invalid -U intergen",
     "credential-assignment"),
    ("a token in a URL query string", "url",
     "https://example.invalid/api/v1/things?api_key=Zx7RmL0pWvTbKcJdFg&page=2",
     "credential-assignment"),
]

# Ordinary content that must survive BYTE-IDENTICAL. A redactor that cannot be
# shown to leave normal text alone is not a redactor, it is a shredder — and an
# unexplained hole in a full-fidelity record is the failure this writer's
# mandate exists to prevent.
INNOCENT_CORPUS = [
    ("prose about secrets", "user_msg",
     "How do I store a password safely? I keep forgetting mine."),
    ("a version string", "text", "InterGenOS R001.2, kernel 6.18.10, forge 217"),
    ("an ordinary path", "file_content",
     "/usr/lib/python3.14/site-packages/intergen/glass.py"),
    ("an ordinary command", "command",
     "pkm install firefox && systemctl --user restart intergen"),
    ("a plain URL", "url", "https://github.com/InterGenJLU/intergenos"),
    ("an identifier that merely looks dense", "text",
     "turn 6f3a9c21b40d7e58 finished in 812 ms"),
    ("a currency figure with dollar signs", "text",
     "the plan is $6 a month, or $60 a year"),
    ("a dotted sequence", "text", "3.14.1.2600 is the recorded version"),
    # ── The controls the command-line shapes needed ────────────────────────
    # Each of these is a string the new shapes come close to and must not take.
    # They are here because a redactor that has never been shown to leave a
    # near-miss alone has not been shown to be a redactor at all.
    ("the word bearer followed by an ordinary word", "text",
     "bearer authentication is the scheme name, not a credential"),
    ("the -p option with its value in the next argument", "command",
     "mkdir -p /srv/intergen && cp -pr assets backup"),
    ("a published port that has no letter in it", "command",
     "docker run -p8080:8080 --name intergen-web image:latest"),
    ("an ssh port given with the attached short option", "command",
     "ssh -p2222 intergen@host.example.invalid"),
    ("the word password at the start of a sentence", "text",
     "PASSWORD policy requires twelve characters and a symbol"),
    ("a comparison that merely contains an equals sign", "text",
     "the token budget was 4096 and usage=3877, which is under the cap"),
]


def _shape_names():
    """The named shapes the content predicate matches, imported at call time.

    Imported here rather than at module level so a name that does not exist yet
    fails ONE test with a sentence instead of breaking collection for the file
    and hiding every other case behind it.
    """
    shapes = getattr(glass, "_SECRET_SHAPES", None)
    if shapes is None:
        raise AssertionError(
            "intergen.glass has no _SECRET_SHAPES. Its redaction is still a "
            "key-NAME predicate only, so a secret that arrives as content — in "
            "a prompt, a command line, a tool result, or a file the user asked "
            "about — is written to glass.jsonl verbatim and kept there.")
    return [name for name, _pattern in shapes]


class SecretShapedContentIsRedacted(unittest.TestCase):
    """The seven cases that reached the shipped record byte-identical."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="glass-shape-")
        _reset(self.tmp)

    def _write(self, key, value):
        with glass.turn(glass.new_turn_id(), "dbus"):
            glass.emit("prompt", "assembled", detail={key: value})
        return _row(self.tmp, "prompt", "assembled")["detail"][key]

    def test_every_corpus_case_is_no_longer_written_verbatim(self):
        for label, key, value, shape in SECRET_CORPUS:
            with self.subTest(case=label):
                self.setUp()
                written = self._write(key, value)
                self.assertNotEqual(written, value, (
                    f"{label}: the value reached glass.jsonl byte-identical to "
                    f"the input under the key {key!r}."))

    def test_every_corpus_case_names_the_shape_that_matched(self):
        for label, key, value, shape in SECRET_CORPUS:
            with self.subTest(case=label):
                self.setUp()
                written = self._write(key, value)
                self.assertIn(f"<redacted:{shape}>", written, (
                    f"{label}: the record does not say WHAT was removed. An "
                    f"attested placeholder naming the shape is what keeps a "
                    f"reconstructed timeline free of unexplained holes."))

    def test_the_surrounding_content_survives_the_redaction(self):
        """In place, not instead of: only the match goes."""
        self.setUp()
        label, key, value, _shape = SECRET_CORPUS[0]
        written = self._write(key, value)
        self.assertIn("here is my key:", written, (
            "the whole value was replaced. The name predicate replaces a whole "
            "value because the whole value IS the credential; a secret found "
            "inside content must take only its own bytes with it."))

    def test_the_control_under_a_credential_key_name_still_redacts(self):
        """The name predicate is not weakened by the content predicate."""
        self.setUp()
        written = self._write(
            "api_key",
            "sk-ant-api03-QYh2n8Zx7RmL0pWvTbKcJdFgHsNuEiAoXyZ1234567890abcdef")
        self.assertEqual(written, "<redacted:api_key>")

    def test_a_secret_nested_in_a_list_or_dict_is_reached(self):
        self.setUp()
        with glass.turn(glass.new_turn_id(), "dbus"):
            glass.emit("prompt", "assembled", detail={
                "tool_results": [
                    {"name": "read_file",
                     "content": "postgresql://intergen:hunter2@localhost/db"}],
            })
        got = _row(self.tmp, "prompt", "assembled")["detail"]["tool_results"][0]["content"]
        self.assertIn("<redacted:url-with-password>", got)


# One MINIMAL example per shape: the shortest string that shape can match. The
# class below drives each through the redactor, which is what keeps
# glass._MIN_SHAPE_LEN honest — that constant short-circuits the whole scan for
# any string below it, so a value set too high stops redaction with no symptom
# at all. It had happened: the constant was 20, derived from the provider token
# alone, while the URL shape beside it matches at ten, and a ten-character URL
# carrying a password was written verbatim.
SHORTEST_PER_SHAPE = {
    "private-key-block":
        "-----BEGIN PRIVATE KEY----------END PRIVATE KEY-----",
    "url-with-password": "ab://c:d@e",
    "crypt-hash": "$a$b$0123456789",
    "json-web-token": "eyJhbGciOiJ.eyJz.",
    "provider-token": "AKIA0123456789ABCDEF",
    "bearer-token": "bearer a123456789012345",
    "password-option": "-pa2345678",
    "credential-assignment": "token=a23456",
}


class TheLengthShortCircuitCannotSkipAShape(unittest.TestCase):
    """glass._MIN_SHAPE_LEN must not be above any shape's shortest match."""

    def test_every_shape_has_a_shortest_example(self):
        """A shape added without an example fails here, not silently later."""
        declared = {name for name, _pattern in glass.SECRET_SHAPES}
        self.assertEqual(declared, set(SHORTEST_PER_SHAPE), (
            "the shape table and the shortest-example table have drifted. Every "
            "shape needs an example here, because this file is the only thing "
            "that checks the length short-circuit against the shapes it is "
            "supposed to be derived from."))

    def test_each_shortest_example_is_still_redacted(self):
        for name, example in SHORTEST_PER_SHAPE.items():
            with self.subTest(shape=name):
                out = glass.redact_secret_shapes(example)
                self.assertNotEqual(out, example, (
                    f"{name}: its shortest match ({len(example)} characters) "
                    f"came back unchanged. glass._MIN_SHAPE_LEN is "
                    f"{glass._MIN_SHAPE_LEN}, so any string shorter than that "
                    f"skips the scan entirely and this shape stops redacting "
                    f"with no symptom."))

    def test_the_pattern_alone_agrees_with_the_redactor(self):
        """The example really is a match, so a pass above means what it says."""
        for name, pattern in glass.SECRET_SHAPES:
            with self.subTest(shape=name):
                self.assertIsNotNone(
                    pattern.search(SHORTEST_PER_SHAPE[name]),
                    f"{name}: the example in SHORTEST_PER_SHAPE is not a match "
                    f"for the pattern, so it proves nothing about the length "
                    f"short-circuit.")

    def test_the_constant_is_at_or_below_the_shortest_example(self):
        shortest = min(len(v) for v in SHORTEST_PER_SHAPE.values())
        self.assertLessEqual(glass._MIN_SHAPE_LEN, shortest, (
            f"glass._MIN_SHAPE_LEN is {glass._MIN_SHAPE_LEN} but the shortest "
            f"match any shape accepts is {shortest} characters. Everything "
            f"between the two is skipped without being scanned."))


class OrdinaryContentIsUntouched(unittest.TestCase):
    """The other direction, and the reason there is no entropy heuristic."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="glass-shape-ok-")
        _reset(self.tmp)

    def test_every_innocent_case_survives_byte_identical(self):
        for label, key, value in INNOCENT_CORPUS:
            with self.subTest(case=label):
                self.setUp()
                with glass.turn(glass.new_turn_id(), "dbus"):
                    glass.emit("prompt", "assembled", detail={key: value})
                written = _row(self.tmp, "prompt", "assembled")["detail"][key]
                self.assertEqual(written, value, (
                    f"{label}: ordinary content was altered. A hole in a "
                    f"full-fidelity record with nothing behind it is worse "
                    f"than the content it removed."))

    def test_non_string_values_are_left_alone(self):
        self.setUp()
        with glass.turn(glass.new_turn_id(), "dbus"):
            glass.emit("prompt", "assembled", detail={
                "count": 39, "ok": True, "ratio": 0.5, "nothing": None})
        d = _row(self.tmp, "prompt", "assembled")["detail"]
        self.assertEqual([d["count"], d["ok"], d["ratio"], d["nothing"]],
                         [39, True, 0.5, None])


class TheShapesAreNamedAndBounded(unittest.TestCase):
    """What the predicate is allowed to be, asserted rather than described."""

    def test_the_five_shapes_the_cut_names_are_present(self):
        names = set(_shape_names())
        for expected in ("private-key-block", "provider-token",
                         "json-web-token", "url-with-password", "crypt-hash"):
            self.assertIn(expected, names)

    def test_each_shape_name_is_readable_in_a_record(self):
        """A placeholder a person cannot interpret explains nothing."""
        for name in _shape_names():
            with self.subTest(shape=name):
                self.assertRegex(name, r"^[a-z0-9]+(-[a-z0-9]+)*$")


class TheKnownResidual(unittest.TestCase):
    """What this predicate does NOT catch, asserted so it cannot be forgotten.

    THE EARLIER RESIDUE IS CLOSED, and the case that recorded it is gone. It was
    the AWS pair written as "AWS_ACCESS_KEY_ID=... AWS_SECRET=...": the first
    half carries the AKIA prefix and was caught, the second half carries no
    shape at all and was not. That case said, in its own failure message, that
    closing it would need "a rule about NAME=value assignments inside content".
    The command-line pass added exactly that rule, the case started failing the
    way it said it would, and it has been replaced by the residue that is left.

    WHAT IS LEFT. A high-entropy string with no marker of any kind in front of
    it — no assignment, no option, no scheme word, no vendor prefix — is still
    written. Catching that needs an entropy threshold, which stays rejected: it
    would put holes in a full-fidelity record on ordinary content while still
    missing structured secrets. The limit is asserted here so that it is a
    stated, tested property of the release rather than something a reader
    assumes is covered.
    """

    def test_the_assignment_form_of_the_earlier_residue_is_now_caught(self):
        """The case this class used to record, now the other way round."""
        tmp = tempfile.mkdtemp(prefix="glass-shape-residual-")
        _reset(tmp)
        value = ("AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE "
                 "AWS_SECRET=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY aws s3 ls")
        with glass.turn(glass.new_turn_id(), "dbus"):
            glass.emit("prompt", "assembled", detail={"command": value})
        written = _row(tmp, "prompt", "assembled")["detail"]["command"]
        self.assertIn("<redacted:provider-token>", written,
                      "the access key id must be caught by its AKIA prefix")
        self.assertNotIn("wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", written, (
            "the secret access key came back. It arrives under a name the "
            "credential-assignment shape matches, so it must not."))
        self.assertIn("<redacted:credential-assignment>", written,
                      "and the record must say WHAT was removed")

    def test_a_secret_with_no_marker_at_all_still_reaches_the_record(self):
        """The residue that remains, asserted so nobody assumes it is covered."""
        tmp = tempfile.mkdtemp(prefix="glass-shape-residual2-")
        _reset(tmp)
        value = "the value to use is wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
        with glass.turn(glass.new_turn_id(), "dbus"):
            glass.emit("prompt", "assembled", detail={"note": value})
        written = _row(tmp, "prompt", "assembled")["detail"]["note"]
        self.assertIn("wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", written, (
            "This assertion records a KNOWN LIMIT, and it failing is good news: "
            "it would mean a bare high-entropy string with no marker in front "
            "of it is now caught. If that is deliberate, delete this case and "
            "say so in the release note, and say which predicate does it — if "
            "the answer is an entropy threshold, that is a decision this "
            "module's own documentation says must be argued, not slipped in."))


class TheRowBoundDoesNotUndoTheRedaction(unittest.TestCase):
    """The two changes this module received in one release, asserted together.

    One shortens a row that is larger than a single row may be, keeping a prefix
    of its detail so the record still says what it could not fit. The other
    replaces secret-shaped runs inside content. The order between them is the
    whole question: if the shortening kept a prefix of the ORIGINAL detail, a
    secret that was redacted in the row would come back verbatim inside that
    prefix, and a secret would be exposed exactly by being carried in a row too
    large to write. The order is asserted here rather than described, because a
    later change to either side can reverse it without either side noticing.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="glass-shape-bound-")
        _reset(self.tmp)
        # Reduced so the row is oversized at a few kilobytes instead of eight
        # megabytes; what is under test is the ORDER of two operations, and
        # that does not care about the scale. getattr, so the red state of this
        # file fails on the contract rather than erroring in setUp.
        self._rotate = glass._ROTATE_BYTES
        self._maxrow = getattr(glass, "_MAX_ROW_BYTES", None)
        glass._ROTATE_BYTES = 8 * 1024
        if self._maxrow is not None:
            glass._MAX_ROW_BYTES = glass._ROTATE_BYTES // 8

    def tearDown(self) -> None:
        glass._ROTATE_BYTES = self._rotate
        if self._maxrow is not None:
            glass._MAX_ROW_BYTES = self._maxrow
        glass._glass = None

    def test_a_secret_in_an_oversized_row_is_redacted_before_the_row_is_cut(self):
        secret = "postgresql://intergen:hunter2@localhost:5432/intergen"
        padding = "ordinary content that says nothing in particular. " * 200
        with glass.turn(glass.new_turn_id(), "dbus"):
            glass.emit("prompt", "assembled",
                       detail={"user_msg": secret + " " + padding})
        written = (Path(self.tmp) / "intergen" / "glass.jsonl").read_text()
        self.assertIn("glass_oversized_row", written, (
            "control: the row was never shortened, so this test measured "
            "nothing. Raise the padding or lower the row bound."))
        self.assertNotIn(secret, written, (
            "the secret reached the record verbatim inside the kept prefix of "
            "a shortened row. Redaction has to happen before the row is cut, "
            "or a secret is exposed by the size of the row carrying it."))
        self.assertIn("<redacted:url-with-password>", written, (
            "the secret is absent, but so is any statement that it was "
            "removed. A prefix that merely stopped short of the secret is not "
            "a redaction, and the next row with different lengths would "
            "expose it."))


if __name__ == "__main__":
    unittest.main()
