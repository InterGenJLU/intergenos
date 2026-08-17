# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""A rendered answer must come from the tool that actually ran.

Reported class: `search for a pdf editor` dispatched the package tool, the
package tool executed and returned a real listing, and the turn's trace
truthfully named it — yet the delivered answer was "Disk usage is available."
The trace was not naming a phantom tool; the ANSWER was the untruth, because
template selection reads the user's wording to pick a renderer while the tool
that ran is what determines the shape of the output being rendered.

Two mechanisms are pinned here, and they are independent:

1. PROVENANCE GATE. A system-info template only renders output produced by the
   shell executor whose command produces that shape. Fixing the one substring
   that made this specific pair reachable (`df` inside `pdf`) closes an
   instance; gating on the executed tool closes the class, including future
   token collisions nobody has enumerated.

2. NOTHING-PARSED CONTRACT. A summariser that extracts nothing from its input
   returns None so synthesis falls through to the real content, instead of
   emitting a stock sentence ("Disk usage is available.") that reads as an
   answer while carrying no data from the dispatch. That stock sentence is
   invisible to the M8-2 result-delivery invariant — it is neither empty, nor a
   deflection, nor an explain-instead-of-result — which is why the reported turn
   raised no defect anywhere. Both directions are asserted: the vacuous sentence
   must not be produced, and a real parse (including a real zero) must survive.
"""
import unittest

from intergen import safety
from intergen.interfaces.types import AnswerLinkage
from intergen.router import ConversationRouter as R


PKG_SEARCH_OUTPUT = (
    "pdfarranger  1.11.0   available\n"
    "okular       24.08.1  available\n"
    "xournalpp    1.2.3    available\n"
)
PKG_LIST_OUTPUT = (
    "bash         5.3.0    installed\n"
    "coreutils    9.7      installed\n"
)
DF_OUTPUT = (
    "Filesystem      Size  Used Avail Use% Mounted on\n"
    "/dev/sda1       100G   50G   50G  50% /\n"
)
LSCPU_OUTPUT = "Architecture:  x86_64\nModel name:  Example CPU X1\nCPU(s):  8\n"
LSBLK_OUTPUT = "NAME SIZE TYPE\nsda  500G disk\n"
LSPCI_OUTPUT = "00:02.0 VGA compatible controller: Example Graphics G1\n"
OS_RELEASE_OUTPUT = 'NAME="InterGenOS"\nPRETTY_NAME="InterGenOS 1.0"\n'
LSUSB_OUTPUT = "Bus 001 Device 002: ID 1d6b:0002 Linux Foundation 2.0 root hub\n"


class ProvenanceGateTests(unittest.TestCase):
    """Output from a non-shell tool must not reach a system-info template."""

    def test_the_reported_turn_is_not_rendered_as_disk(self):
        """PKG-dispatch-04, at the mechanism: even if the wording matched, the
        package tool's output can never be handed to the disk summariser."""
        self.assertIsNone(
            R._template_synthesis("search for a pdf editor",
                                  PKG_SEARCH_OUTPUT, "manage_packages"))

    def test_wording_that_names_a_system_topic_cannot_capture_package_output(self):
        """The gate holds even when the user's words legitimately name the
        topic — because the tool that ran did not produce that kind of output.
        These are the turns a word-boundary fix alone would still mis-render."""
        cases = [
            ("is there a disk cleanup tool I can search for", "manage_packages"),
            ("search for a memory profiler", "manage_packages"),
            ("find me a cpu benchmark package", "manage_packages"),
            ("search for a gpu driver", "manage_packages"),
            ("is there a usb formatting tool", "manage_packages"),
            ("search for a block device editor", "manage_packages"),
            ("search for an os installer", "manage_packages"),
        ]
        for query, tool in cases:
            with self.subTest(query=query):
                got = R._template_synthesis(query, PKG_SEARCH_OUTPUT, tool)
                self.assertIsNone(
                    got,
                    f"{query!r} dispatched {tool} — its output must not be "
                    f"rendered by a system-info template (got {got!r})")

    def test_gate_applies_to_every_non_shell_tool(self):
        """Not a package-tool special case: any executor that is not the shell
        one produces a shape the system-info templates were not written for."""
        for tool in ("manage_packages", "manage_services", "web_search",
                     "read_file", "write_file", "open_application",
                     "analyze_file"):
            with self.subTest(tool=tool):
                self.assertIsNone(
                    R._template_synthesis("how much disk space", DF_OUTPUT, tool),
                    f"{tool} output must not render through the disk summariser")

    def test_single_value_templates_are_gated_too(self):
        """The one-line wrappers (hostname/kernel/uptime/…) are shell-output
        templates as much as the multi-line summarisers are."""
        for query, out in (("what is my hostname", "box-01"),
                           ("what kernel am I running", "6.18.10-igos-8"),
                           ("what is my uptime", "2 days"),
                           ("how many cores do I have", "8")):
            with self.subTest(query=query):
                self.assertIsNone(
                    R._template_synthesis(query, out, "manage_packages"))


class ProvenanceGatePrecisionTests(unittest.TestCase):
    """The gate must cost nothing that legitimately worked before."""

    def test_shell_output_still_renders_every_family(self):
        cases = [
            ("what is my disk usage", DF_OUTPUT, "Disk"),
            ("show me cpu info", LSCPU_OUTPUT, "CPU"),
            ("what block devices do I have", LSBLK_OUTPUT, "disk"),
            ("what gpu do I have", LSPCI_OUTPUT, "GPU"),
            ("what os am I running", OS_RELEASE_OUTPUT, "OS"),
            ("what is my hostname", "box-01", "hostname"),
            ("what kernel am I running", "6.18.10-igos-8", "kernel"),
        ]
        for query, out, marker in cases:
            with self.subTest(query=query):
                got = R._template_synthesis(query, out, "run_command")
                self.assertIsNotNone(
                    got, f"{query!r} over shell output must still be answered")
                self.assertIn(marker, got)

    def test_undeclared_provenance_keeps_the_historical_contract(self):
        """The two-argument form is the pre-existing shell-output contract; it
        must behave exactly as it did, so no existing caller silently changes."""
        self.assertEqual(R._template_synthesis("what is my hostname", "box-01"),
                         R._template_synthesis("what is my hostname", "box-01",
                                               "run_command"))

    def test_raw_request_is_tool_agnostic(self):
        """An explicit ask for the unsummarised output is about the DELIVERY
        shape, not the tool — it must survive the gate for every tool."""
        for tool in (None, "run_command", "manage_packages"):
            with self.subTest(tool=tool):
                self.assertEqual(
                    R._template_synthesis("show me the raw output",
                                          PKG_SEARCH_OUTPUT, tool),
                    PKG_SEARCH_OUTPUT.strip())

    def test_service_status_is_tool_agnostic(self):
        """A single-line active/inactive verdict is a shape both the service
        tool and the shell executor produce, so the gate must not eat it."""
        for tool in ("manage_services", "run_command", None):
            with self.subTest(tool=tool):
                self.assertEqual(
                    R._template_synthesis("is sshd running", "active", tool),
                    "Yes, it's running. active")
                self.assertEqual(
                    R._template_synthesis("is sshd running", "inactive", tool),
                    "No, it's not running. inactive")

    def test_package_output_reaches_llm_synthesis_not_a_template(self):
        """Returning None is the POINT: the caller then synthesises over the
        real package content instead of delivering a foreign summary."""
        for query in ("search for a pdf editor",
                      "what packages do I have installed?"):
            with self.subTest(query=query):
                self.assertIsNone(
                    R._template_synthesis(query, PKG_LIST_OUTPUT,
                                          "manage_packages"))


class StatusVerdictTests(unittest.TestCase):
    """The service-status verdict is read from the OUTPUT, and the same
    substring collision that mis-picked a renderer on the input side inverts an
    answer here: `"active" in "inactive"` is True."""

    def test_inactive_is_never_reported_as_running(self):
        for out in ("inactive", "inactive (dead)", "failed", "stopped"):
            with self.subTest(out=out):
                got = R._template_synthesis("is sshd running", out, "run_command")
                self.assertEqual(
                    got, f"No, it's not running. {out}",
                    "a stopped unit must never be reported as running")

    def test_active_still_reports_running(self):
        for out in ("active", "active (running)", "running"):
            with self.subTest(out=out):
                self.assertEqual(
                    R._template_synthesis("is sshd running", out, "run_command"),
                    f"Yes, it's running. {out}")

    def test_output_with_no_verdict_is_not_dressed_as_one(self):
        """A question containing "running" over output that carries no verdict
        token is not a status result; echoing it raw delivers (for example) a
        kernel version as a service verdict."""
        self.assertIsNone(
            R._template_synthesis("what kernel am I running",
                                  "6.18.10-igos-8", "manage_packages"))


class PluralRecallTests(unittest.TestCase):
    """Word-boundary selection must not cost the plural forms — the recall
    hole a bare boundary opens ("disks", "cores", "processors")."""

    def test_plural_forms_reach_the_same_template(self):
        for query, out, marker in (
            ("what disks do I have", DF_OUTPUT, "Disk"),
            ("how many cores do I have", "8", "core"),
            ("show me processors", LSCPU_OUTPUT, "CPU"),
        ):
            with self.subTest(query=query):
                got = R._template_synthesis(query, out, "run_command")
                self.assertIsNotNone(
                    got, f"{query!r} must reach the same template as its "
                    "singular form")
                self.assertIn(marker, got)

    def test_the_plural_does_not_reopen_the_substring_leaks(self):
        """Every leak the boundary closed is a token appearing as a prefix or
        infix of a longer word, so an OPTIONAL TRAILING "s" cannot revive it."""
        for query, out in (("convert this pdf", DF_OUTPUT),
                           ("install telegram", "MemTotal: 1"),
                           ("what is a namespace", DF_OUTPUT),
                           ("is this hardcore mode", "32"),
                           ("what is blockchain", LSBLK_OUTPUT),
                           ("sometimes it fails", "12:04:11")):
            with self.subTest(query=query):
                self.assertIsNone(
                    R._template_synthesis(query, out, "run_command"))


class NothingParsedContractTests(unittest.TestCase):
    """A summariser that extracts nothing must not emit a stock sentence."""

    def test_summarisers_return_none_when_nothing_parses(self):
        for name, fn, junk in (
            ("disk", R._summarize_disk, PKG_SEARCH_OUTPUT),
            ("cpu", R._summarize_cpu, PKG_SEARCH_OUTPUT),
            ("gpu", R._summarize_gpu, ""),
            ("block", R._summarize_block, PKG_SEARCH_OUTPUT),
            ("os", R._summarize_os, PKG_SEARCH_OUTPUT),
        ):
            with self.subTest(summariser=name):
                self.assertIsNone(
                    fn(junk),
                    f"_summarize_{name} must fall through when it parses "
                    "nothing, not emit a sentence carrying no dispatched data")

    def test_no_summariser_can_emit_the_vacuous_sentence(self):
        """The exact shape the reported turn delivered."""
        for fn in (R._summarize_disk, R._summarize_cpu, R._summarize_gpu,
                   R._summarize_block, R._summarize_os, R._summarize_usb):
            for junk in ("", "   ", PKG_SEARCH_OUTPUT):
                got = fn(junk)
                with self.subTest(fn=fn.__name__, junk=junk[:12]):
                    self.assertNotRegex(
                        got or "", r"(?i)\b(is|information is) available\.",
                        "a summariser that parsed nothing must fall through, "
                        "not deliver a content-free 'available' sentence")

    def test_a_real_parse_still_answers(self):
        self.assertIn("Disk", R._summarize_disk(DF_OUTPUT) or "")
        self.assertIn("CPU", R._summarize_cpu(LSCPU_OUTPUT) or "")
        self.assertIn("GPU", R._summarize_gpu(LSPCI_OUTPUT) or "")
        self.assertIn("disk", R._summarize_block(LSBLK_OUTPUT) or "")
        self.assertIn("OS", R._summarize_os(OS_RELEASE_OUTPUT) or "")

    def test_a_real_zero_is_a_finding_not_a_failure(self):
        """lsusb with only root hubs parsed FINE and found zero external
        devices. That is an answer, and it must not be swept into the
        fall-through with the genuine parse failures."""
        self.assertEqual(R._summarize_usb(LSUSB_OUTPUT),
                         "No external USB devices detected.")

    def test_template_synthesis_falls_through_instead_of_pretending(self):
        """End of the mechanism: an unparseable shell result now reaches LLM
        synthesis over the real output rather than a vacuous template."""
        self.assertIsNone(
            R._template_synthesis("what is my disk usage",
                                  "df: /nonexistent: No such file or directory",
                                  "run_command"))


class UnconsumedDispatchTests(unittest.TestCase):
    """The delivery invariant, including the SUBSTITUTED class.

    This class previously pinned a known LIMIT: a dispatch whose result was
    substituted rather than dropped was invisible, because the invariant's
    reasons were all text shapes and a confident wrong answer is none of them.
    The limit is now closed — not by inspecting the text, but by reading the
    answer→dispatch linkage the composing route records (AnswerLinkage). Text
    overlap remains rejected as unsound: the memory and disk summarisers answer
    from an authoritative live source by ratified design and share no token with
    the tool output beside them.
    """

    class _Result:
        call_id = "call-1"
        name = "manage_packages"
        content = PKG_SEARCH_OUTPUT
        model_summary = None
        executed = True
        success = True
        blocked = False

    def test_the_invariant_catches_the_shapes_it_names(self):
        r = self._Result()
        self.assertEqual(
            safety.find_unconsumed_dispatches("", [r])[0][1], "empty_delivery")
        self.assertEqual(
            safety.find_unconsumed_dispatches(
                "I don't have access to current data.", [r])[0][1],
            "deflection_despite_result")

    def test_a_substituted_result_is_now_caught(self):
        """The reported turn's exact shape: "Disk usage is available." delivered
        while a package dispatch succeeded. Not empty, not a deflection, not an
        explain — caught because the linkage says the text came from cached
        state, not from the dispatch in hand."""
        problems = safety.find_unconsumed_dispatches(
            "Disk usage is available.", [self._Result()],
            AnswerLinkage(kind="cache", renderer="template"))
        self.assertEqual([p[1] for p in problems], ["substituted"])

    def test_a_different_dispatch_is_also_a_substitution(self):
        problems = safety.find_unconsumed_dispatches(
            "Disk usage is available.", [self._Result()],
            AnswerLinkage(kind="dispatch", tool="run_command",
                          call_id="call-9", renderer="template"))
        self.assertEqual([p[1] for p in problems], ["substituted"])

    def test_the_matching_dispatch_is_not_a_substitution(self):
        """The precision control — a correct answer must stay silent."""
        self.assertEqual(
            safety.find_unconsumed_dispatches(
                "No packages matching 'pdf editor'.", [self._Result()],
                AnswerLinkage(kind="dispatch", tool="manage_packages",
                              call_id="call-1", renderer="llm_synth")),
            [])

    def test_absent_linkage_is_not_evidence_of_substitution(self):
        """An uninstrumented path must not be accused. Absence of a signal is
        recorded by the caller as `undeclared`, never graded as a defect."""
        self.assertEqual(
            safety.find_unconsumed_dispatches("Disk usage is available.",
                                              [self._Result()]),
            [])

    def test_a_code_owned_refusal_over_a_blocked_dispatch_is_silent(self):
        """A safety block is code-owned by design and the dispatch is `blocked`,
        so it is skipped before the linkage is ever consulted."""
        class _Blocked(self._Result):
            blocked = True
            success = False
            executed = False
        self.assertEqual(
            safety.find_unconsumed_dispatches(
                "I won't run that.", [_Blocked()],
                AnswerLinkage(kind="code", renderer="safety_block")),
            [])


if __name__ == "__main__":
    unittest.main()
