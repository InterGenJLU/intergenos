#!/usr/bin/env python3
"""What packages PRINT during an install is for the person at the terminal.

The bar, set 2026-08-05 after two live incidents: user-facing install output
must never require project-internal knowledge to parse. A printed line that
names internal audit machinery, internal decision codes, or our own design
rationale is not user output — it is a note to ourselves on someone else's
screen.

Two lines are pinned here by name because they are the ones that were actually
reported, and a scrub with no test is a scrub that comes back:

  * the shared helper library announced, on every successful install by a
    helper that deposits both 32-bit and 64-bit files, that a "deposit width
    audit is waived by that declaration for this run". The fact is real and is
    still recorded — igos_helper_commit writes elf_class into the package's
    manifest — but the terminal is not where it belongs.
  * the Steam recipe told the user its launcher "halts the launch loudly, never
    silently". That was not true in the case that mattered: a desktop launch
    has no terminal, so the refusal went nowhere. The claim is gone and the
    refusal now reaches the desktop.

The third check is the general one: no shipped script may print an internal
decision code (a letter-and-digits tag like K21.F or H-024) at the user.
"""

import re
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Assembled at run time so this file does not itself carry the banned strings
# as literals a future grep-based sweep would trip over.
WAIVER_PHRASE = "deposit width " + "audit is waived"
LOUD_CLAIM = "halts the launch " + "loudly, never silently"

# An internal decision code: a capital letter or two, digits, then an optional
# dotted or hyphenated part — K21.F, H-024, RT-4, D-007, PKM-A19.
DECISION_CODE = re.compile(r"\b[A-Z]{1,4}-?\d{1,3}(?:[.-][A-Z0-9]{1,4})?\b")

# Vendor and standards vocabulary that legitimately looks like a code.
ALLOWED_CODE_LIKE = re.compile(
    r"\b(?:SHA-?\d+|MD5|GPG|TLS|HTTPS?|UTF-8|AGPL-3|GPL-3|MPL-2|LGPL|BSD-\d"
    r"|CUDA|GB|MiB|GiB|X11|GL|EGL|ID|OS|PATH|USB|PCI|CPU|GPU|RAM|API|CLI"
    r"|IPv[46]|RFC-?\d+|ISO-?\d+|AMD64|ARM64|I386|VSIX|JSON|YAML|XML)\b")


def _destdir_heredoc_spans(lines):
    """Line ranges of heredoc bodies that build a script into DESTDIR.

    This is the distinction the whole file turns on. A build.sh `echo` at the
    top level runs on a build machine while the package is compiled, and no
    user ever sees it — internal vocabulary is correct there. The same `echo`
    written INSIDE a heredoc that lands in DESTDIR ships to the user's machine
    and runs on their terminal.
    """
    spans = []
    i = 0
    while i < len(lines):
        m = re.search(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?", lines[i])
        if m and "DESTDIR" in lines[i]:
            tag = m.group(1)
            for j in range(i + 1, len(lines)):
                if lines[j].strip() == tag:
                    spans.append((i + 1, j))
                    i = j
                    break
        i += 1
    return spans


def shipped_script_lines():
    """(path, lineno, text) for every printed line that runs on a USER's
    machine: the helper and wrapper scripts a recipe writes into DESTDIR, the
    scripts tracked beside the recipe, and the shared helper library."""
    out = subprocess.run(
        ["git", "ls-files", "packages/"],
        cwd=REPO_ROOT, capture_output=True, text=True, errors="replace")
    for rel in out.stdout.split():
        if rel.endswith("package.yml"):
            continue
        p = REPO_ROOT / rel
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        lines = text.splitlines()
        if rel.endswith("build.sh"):
            spans = _destdir_heredoc_spans(lines)
        else:
            spans = [(0, len(lines))]
        for (a, b) in spans:
            for n in range(a, min(b, len(lines))):
                s = lines[n].strip()
                if s.startswith(("echo ", "printf ", "igos_helper_emit ")):
                    yield rel, n + 1, s


class PackageOutputIsForUsers(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.lines = list(shipped_script_lines())

    def test_the_population_is_not_empty(self):
        # A zero from an empty population would prove nothing.
        self.assertGreater(
            len(self.lines), 200,
            "the recipes should carry hundreds of printed lines; a small "
            "number here means nothing was read and the checks below are "
            "vacuous")

    def test_no_waived_audit_notice_on_the_terminal(self):
        hits = [f"{r}:{n}" for r, n, s in self.lines if WAIVER_PHRASE in s]
        self.assertEqual(
            hits, [],
            "the waived-width notice is recorded in the package manifest; it "
            f"must not be printed at the user: {hits}")

    def test_no_claim_that_a_refusal_is_always_visible(self):
        hits = [f"{r}:{n}" for r, n, s in self.lines if LOUD_CLAIM in s]
        self.assertEqual(
            hits, [],
            "a desktop launch has no terminal, so this claim was false in the "
            f"case that mattered: {hits}")

    def test_no_wrapper_internals_in_the_compatibility_tool_message(self):
        # The compatibility-tool installer used to end by explaining that the
        # launch wrapper exports a path so no logout is needed. That is a true
        # statement about our own mechanism and it answered a question the
        # reader had not asked, while leaving the one they did have — why the
        # tool does not appear — unanswered.
        hits = [f"{r}:{n}" for r, n, s in self.lines
                if "wrapper exports" in s or "no logout needed" in s]
        self.assertEqual(
            hits, [],
            f"internal mechanism printed at the user: {hits}")

    def test_the_compatibility_tool_message_answers_what_the_user_asks(self):
        # Measured 2026-08-05: a compatibility tool installed while Steam was
        # running stayed invisible until Steam was fully exited and relaunched,
        # because Steam scans for these tools only at startup (Steam started
        # 12:25:18, the tool installed 12:27:39). The closing message said
        # "Restart Steam" without the reason, and named only the per-title
        # route — not the global default the reader actually wanted. All three
        # facts must be in the shipped message.
        message = " ".join(
            s for r, n, s in self.lines if "ge-proton" in r.lower())
        self.assertIn(
            "only looks for compatibility tools while it is starting", message,
            "the message must give the REASON a restart is needed, not just "
            "the instruction")
        self.assertIn(
            "Exit it fully", message,
            "closing the window is not exiting Steam; the message must say so")
        self.assertIn(
            "Settings > Compatibility", message,
            "the message must name the global-default route")
        self.assertIn(
            "Properties > Compatibility", message,
            "the message must name the per-title route")

    def test_a_refusal_the_user_cannot_act_on_says_so(self):
        """The five refusals on the Steam / GE-Proton lane must tell the reader
        the three things they need, decided 2026-08-06.

        Each of these stops an install for a reason the person at the terminal
        did not cause and cannot correct: a download whose bytes do not match
        what the package was built against, a release whose layout or file set
        changed after publication, or a signature that will not verify. They
        used to be written from the package's point of view — "not the bytes
        this package expects", "paths this helper does not handle", "Refusing to
        install" — which describes our machinery and leaves the reader without
        the two facts that actually matter to them: whether their machine was
        altered, and whether there is anything for them to do.

        This pins the outcome, not the phrasing, so the text can still be
        improved: every refusal must say that nothing was changed.
        """
        lane = ("packages/extra/steam/build.sh",
                "packages/extra/ge-proton/build.sh")

        def _printed_text(s):
            """The words a reader sees, without the shell that prints them.

            A sentence long enough to matter is wrapped across two or three
            `echo` calls, so joining the raw source lines puts `echo "` in the
            middle of it and no phrase spanning a line break can ever match.
            The first version of this check did exactly that and reported a
            message as missing while it was on screen.
            """
            body = re.sub(r"^(?:echo|printf|igos_helper_emit)\s+", "", s.strip())
            body = body.strip('"\'').replace('\\n', ' ')
            # Collapse runs of whitespace: the source lines carry their own
            # indentation, so joining two of them leaves a gap in the middle of
            # a sentence and an exact phrase spanning the break never matches.
            return re.sub(r"\s+", " ", body)

        by_file = {}
        for r, n, s in self.lines:
            if r in lane:
                by_file.setdefault(r, []).append((n, s))

        def _names_a_command_to_run(rel, lineno):
            """True for an INVOCATION error, which is a different class.

            Running the helper directly instead of through the package manager
            is something the reader did and can immediately correct, and that
            refusal already tells them the command to use. It is deliberately
            not covered here: demanding it also report that nothing changed
            would be asking for a reassurance about a step that never started.
            """
            window = re.sub(r"\s+", " ", " ".join(
                _printed_text(t) for m, t in by_file[rel]
                if lineno <= m <= lineno + 4))
            return "pkm install" in window

        refusals = [(r, n, s) for r, n, s in self.lines
                    if r in lane and "ERROR:" in s
                    and not _names_a_command_to_run(r, n)]
        # A zero here would prove nothing; the lane carries these refusals.
        self.assertGreaterEqual(
            len(refusals), 4,
            f"the Steam / GE-Proton lane should carry several install refusals; "
            f"found {len(refusals)} — this check has stopped reading them")

        for r, n, s in refusals:
            # The reassurance may sit on a continuation line, so look at the
            # refusal and the handful of printed lines that follow it.
            # Collapsed AFTER the join: each source line carries its own
            # indentation, so joining leaves a double space in the middle of a
            # sentence and a phrase spanning the break never matches.
            following = re.sub(r"\s+", " ", " ".join(
                _printed_text(t) for m, t in by_file[r] if n <= m <= n + 8))
            self.assertTrue(
                "nothing on this machine was changed" in following
                or "nothing was installed and nothing on this machine" in following,
                f"{r}:{n} refuses an install without telling the reader whether "
                f"their machine was altered: {s}")

    def test_a_helper_library_contract_failure_is_explained_to_the_reader(self):
        """A refusal only a faulty installer can reach must not print bare
        internal machinery at the user.

        These fire when an installer script calls the recording library out of
        order or with the wrong arguments. The reader did nothing and can change
        nothing. They used to print only the internal detail — the name of the
        library function that was called too early — which tells the person
        looking at it nothing about their machine. They now go through
        igos_helper_internal_fault, which states what happened and that nothing
        was installed, and keeps the internal detail on a line marked for a
        maintainer.
        """
        lib = REPO_ROOT / "packages" / "core" / "intergenos-helper-lib" / "helper-lib.sh"
        text = lib.read_text()

        self.assertIn(
            "igos_helper_internal_fault()", text,
            "the shared explanation for a faulty-installer refusal is gone; "
            "each site would be back to printing its own internal detail")

        bare = [
            f"{n}: {line.strip()}"
            for n, line in enumerate(text.splitlines(), 1)
            if "igos_helper_emit" in line
            and ("not called yet" in line or "usage:" in line)
        ]
        self.assertEqual(
            bare, [],
            "these refusals print internal machinery straight at the user "
            "instead of going through igos_helper_internal_fault:\n  "
            + "\n  ".join(bare))

    def test_no_internal_decision_code_is_printed_at_the_user(self):
        hits = []
        for rel, n, s in self.lines:
            # A bracket expression in a pattern (e.g. [a-zA-Z0-9._-]) is not
            # prose; strip those before looking for a code.
            body = re.sub(r"\[[^\]]*\]", " ", s)
            body = ALLOWED_CODE_LIKE.sub(" ", body)
            for m in DECISION_CODE.finditer(body):
                hits.append(f"{rel}:{n}: {m.group(0)}  in  {s}")
        self.assertEqual(
            hits, [],
            "these printed lines carry an internal decision code, which means "
            "nothing to the person reading it:\n  " + "\n  ".join(hits))


if __name__ == "__main__":
    unittest.main()
