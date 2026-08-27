# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The capability gate checks a whole first-party command, not just its verb.

WHY THIS FILE EXISTS, measured 2026-08-26. A user asked how to clear temporary
files and was told to run `pkm remove /tmp -s 80`. `pkm remove` removes a
PACKAGE and pkm has no `-s` flag, so the command handed to the user for a real,
destructive tool does not exist. Every screen on the outbound path passed it: the
capability gate checked the SUBCOMMAND (`remove` — real) and looked at nothing
after it. A person reading the reply caught it; no assertion did.

So the gate now checks the whole invocation — tool, subcommand, and every flag —
for every shipped first-party command (`pkm`, `forge`, `intergen`, `igos-*`),
against that command's own interface as derived by
``scripts/gen-capability-surface.py``.

The tests below are in three groups, and the third is the one that matters most:

  1. THE DEFECT — the two replies from the field, which must not be delivered
     as written.
  2. THE CONTROLS — correct commands, which must pass through UNTOUCHED. A gate
     that catches the invented command by rejecting real ones has not helped
     anybody.
  3. THE WAYS OF BEING WRONG — prose that merely mentions a tool, a source path,
     a documentation placeholder, a manual-page reference, an honest correction.
     Every one of these was a real false positive during authoring, found by
     running the gate over all 437 first-party commands the tree itself writes;
     each is pinned here so it cannot come back.

Also pinned: the ground-truth artifact AGREES with the live tools. The artifact
had drifted from the parser it claims to be generated from — it was missing three
real pkm subcommands, so the shipped gate called `pkm vacuum` a fabrication —
and nothing detected that, because no generator was in the tree to disagree with.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import intergen.safety as safety
from intergen.safety import screen_capability_claim

REPO = Path(safety.__file__).resolve().parent.parent
GENERATOR = REPO / "scripts" / "gen-capability-surface.py"


def _verdict(reply: str) -> tuple[str, str | None]:
    return screen_capability_claim(reply)


class TheDefectFromTheField(unittest.TestCase):
    """The two replies that were delivered, which must not be delivered again."""

    def test_invented_flag_on_a_real_destructive_subcommand_is_caught(self):
        # The reply as it was sent. `remove` is real, `-s` is not, and the old
        # gate stopped reading at `remove`.
        reply = ("You can clear out temporary files with `pkm remove /tmp -s 80` "
                 "— that removes anything in /tmp older than 80 days.")
        verdict, marker = _verdict(reply)
        self.assertEqual(verdict, "violation",
                         "an invented flag on a real destructive subcommand "
                         "must not be delivered as written")
        self.assertIsNotNone(marker)
        self.assertIn("-s", marker,
                      "the marker must name the FLAG that does not exist, so the "
                      "corrective note tells the model what was actually wrong")

    def test_pkm_named_for_a_job_it_does_not_do_is_caught(self):
        reply = ("Sure — just use pkm to save a file, then you can find it "
                 "again later.")
        verdict, _ = _verdict(reply)
        self.assertEqual(verdict, "violation")


class CorrectCommandsPassUntouched(unittest.TestCase):
    """The controls. Every one of these is a command the tool really accepts."""

    def test_correct_install(self):
        self.assertEqual(_verdict("To get it, run `pkm install foo`."),
                         ("clean", None))

    def test_correct_info(self):
        self.assertEqual(_verdict("You can see the details with `pkm info bash`."),
                         ("clean", None))

    def test_real_flag_on_a_real_subcommand(self):
        self.assertEqual(_verdict("Run `pkm install -v firefox`."),
                         ("clean", None))

    def test_bare_global_flag_with_no_subcommand(self):
        # `pkm --help` and `pkm --version` are complete, correct command lines.
        for cmd in ("pkm --help", "pkm --version"):
            with self.subTest(cmd=cmd):
                self.assertEqual(_verdict(f"Run `{cmd}`."), ("clean", None))

    def test_a_real_subcommand_the_shipped_artifact_had_dropped(self):
        # `pkm vacuum` is in the parser. The artifact did not have it, so the
        # gate called a real command a fabrication until the generator landed.
        self.assertEqual(_verdict("Run `pkm vacuum` to compact the database."),
                         ("clean", None))

    def test_a_global_flags_value_is_not_read_as_the_subcommand(self):
        self.assertEqual(_verdict("Run `pkm --db /var/lib/pkm.db list`."),
                         ("clean", None))

    def test_a_nested_subcommand(self):
        self.assertEqual(_verdict("Run `pkm cache clean`."), ("clean", None))

    def test_an_alias_is_accepted_like_its_primary(self):
        # `uninstall` is an alias of `remove`; `ls` of `list`.
        for cmd in ("pkm uninstall firefox", "pkm ls"):
            with self.subTest(cmd=cmd):
                self.assertEqual(_verdict(f"Run `{cmd}`."), ("clean", None))

    def test_correct_forge_invocation(self):
        self.assertEqual(_verdict("Start the text installer with `forge --mode tui`."),
                         ("clean", None))

    def test_correct_intergen_invocations(self):
        for cmd in ("intergen ask what disks do I have", "intergen glass --json",
                    "intergen last --raw", "intergen status"):
            with self.subTest(cmd=cmd):
                self.assertEqual(_verdict(f"Run `{cmd}`."), ("clean", None))

    def test_a_shipped_shell_command_named_bare(self):
        # The NAME is ground truth even though the options are not.
        self.assertEqual(_verdict("Run `igos-install-chrome`."), ("clean", None))


class InventedCommandsOnEveryFirstPartyTool(unittest.TestCase):
    """The dispatch names four tool families; each is checked."""

    def test_invented_pkm_subcommand(self):
        verdict, marker = _verdict("Run `pkm frobnicate x`.")
        self.assertEqual(verdict, "violation")
        self.assertIn("frobnicate", marker)

    def test_invented_forge_flag(self):
        verdict, marker = _verdict("Reinstall the system with `forge --wipe-disk`.")
        self.assertEqual(verdict, "violation")
        self.assertIn("--wipe-disk", marker)

    def test_invented_intergen_subcommand(self):
        verdict, marker = _verdict("Check it with `intergen summarize`.")
        self.assertEqual(verdict, "violation")
        self.assertIn("summarize", marker)

    def test_invented_flag_on_a_real_intergen_subcommand(self):
        verdict, marker = _verdict("Run `intergen glass --export-everything`.")
        self.assertEqual(verdict, "violation")
        self.assertIn("--export-everything", marker)

    def test_the_worst_verdict_in_the_reply_is_the_one_returned(self):
        # A first-match-wins finder reads the good command, returns clean, and
        # delivers the bad one. This is the reply shape that would do it.
        reply = ("First run `pkm install foo`, then clear the cache with "
                 "`pkm purge --all`.")
        verdict, marker = _verdict(reply)
        self.assertEqual(verdict, "violation")
        self.assertIn("purge", marker)


class ArgumentsWeCannotCheckAreSaidToBeUnchecked(unittest.TestCase):
    """The honest middle verdict: real command, underivable option surface.

    Saying "that command might not exist" about a command that DOES exist would
    be a second fabrication, so this verdict is separate from both clean and
    violation."""

    def test_arguments_to_a_shell_command_are_unverifiable(self):
        verdict, marker = _verdict("Run `igos-install-chrome --beta`.")
        self.assertEqual(verdict, "unverifiable")
        self.assertIn("--beta", marker)

    def test_a_namespace_name_not_in_the_surface_is_unverifiable_not_invented(self):
        # `igos-build` is a real tool the contributor guide tells people to run,
        # and it ships no package recipe, so the derived surface cannot see it.
        # Refusing to vouch for it is honest; calling it fictional is not.
        verdict, _ = _verdict("Run `igos-build`.")
        self.assertEqual(verdict, "unverifiable")

    def test_the_unverifiable_reply_says_the_command_is_real(self):
        text = safety.capability_unintrospectable_fallback("igos-install-chrome --beta")
        self.assertIn("real InterGenOS command", text)
        self.assertNotIn("no such", text.lower())


class ACommandOnItsOwnLine(unittest.TestCase):
    """The shape the LIVE model actually used, which no test had covered.

    Asked for a command, the 9B on this machine answered:

        The exact pkm command to clean out old cached package downloads is:

        pkm clean

    — no backticks, no imperative lead-in, and `pkm clean` does not exist (the
    real one is `pkm cache clean`). The gate read only backticked and
    imperative-led invocations, so it passed that untouched. Presenting a command
    on its own line after a colon is one of the most ordinary things a model
    does; it is read now."""

    def test_an_invented_command_alone_on_a_line_is_caught(self):
        reply = ("The exact pkm command to clean out old cached package "
                 "downloads and free disk space is:\n\npkm clean")
        verdict, marker = _verdict(reply)
        self.assertEqual(verdict, "violation")
        self.assertIn("clean", marker)

    def test_a_real_command_alone_on_a_line_passes(self):
        for cmd in ("pkm cache clean", "pkm install firefox", "pkm info bash",
                    "forge --mode tui"):
            with self.subTest(cmd=cmd):
                self.assertEqual(_verdict(f"Run this:\n\n{cmd}"), ("clean", None))

    def test_a_shell_prompt_is_still_a_command_line(self):
        self.assertEqual(_verdict("  $ pkm prune")[0], "violation")

    def test_prose_beginning_with_a_tool_name_is_not_a_command(self):
        for text in (
            "pkm is a package manager, not a file saving tool.",
            "pkm is a package manager in InterGenOS, used for installing "
            "software packages",
            "pkm and forge are both shipped with the system",
            "intergen package install.",
        ):
            with self.subTest(text=text):
                self.assertEqual(_verdict(text), ("clean", None))

    def test_a_markdown_heading_is_not_a_command(self):
        # Measured against the tree's own prose: these were read as commands
        # until the line reader stopped accepting bullets and heading markers.
        for text in ("# InterGen Tool Author Guide",
                     "# InterGen Gating Model: the canonical permission spec",
                     "- pkm database populated correctly",
                     "7. PKM DATABASE UPDATE",
                     "# pkm repo index"):
            with self.subTest(text=text):
                self.assertEqual(_verdict(text), ("clean", None))

    def test_a_correction_on_its_own_line_is_still_a_correction(self):
        self.assertEqual(
            _verdict("There is no such command:\n\npkm clean"), ("clean", None))

    def test_no_function_word_is_a_real_subcommand(self):
        """The prose guard rests on this and says so.

        If a shipped tool ever gains a subcommand named `is` or `for`, the guard
        would silently stop reading it — so that possibility fails HERE instead,
        where it can be seen and the guard corrected."""
        for tool, spec in sorted(safety._tool_surface().items()):
            for name in sorted(safety._accepted_names(spec)):
                with self.subTest(tool=tool, subcommand=name):
                    self.assertNotIn(
                        name.lower(), safety._PROSE_OPENERS,
                        f"`{tool} {name}` is real, and the prose guard treats "
                        f"`{name}` as an English word — the guard must change")

    def test_every_real_subcommand_is_lower_case(self):
        """The other assumption the prose guard rests on, checked the same way."""
        for tool, spec in sorted(safety._tool_surface().items()):
            for name in sorted(safety._accepted_names(spec)):
                with self.subTest(tool=tool, subcommand=name):
                    self.assertEqual(
                        name, name.lower(),
                        f"`{tool} {name}` is capitalised, and the line reader "
                        "treats a capitalised word as English")


class ABareTokenWhereTheToolAcceptsNone(unittest.TestCase):
    """`forge` has no subcommands and no positional arguments.

    Measured live: asked how to run forge without questions, the model answered
    `forge install --yes`. Only the flag was caught — `install`, an invented
    subcommand for a tool that has none, was shrugged off as an argument, and
    argparse would reject it outright. A tool that accepts nothing bare now says
    so."""

    def test_an_invented_subcommand_on_a_tool_with_none(self):
        for cmd in ("forge install", "forge tui", "forge install --dry-run"):
            with self.subTest(cmd=cmd):
                verdict, marker = _verdict(f"Run `{cmd}`.")
                self.assertEqual(verdict, "violation")
                self.assertIn("forge", marker)

    def test_the_tools_real_flag_forms_still_pass(self):
        for cmd in ("forge --mode tui", "forge --dry-run",
                    "forge --archives /var/lib/igos/archives"):
            with self.subTest(cmd=cmd):
                self.assertEqual(_verdict(f"Run `{cmd}`."), ("clean", None))

    def test_prose_about_such_a_tool_is_not_a_command(self):
        # Measured against the tree: without the bare-token count this read as
        # an invented `forge operates`.
        for text in ("Forge operates on three foundational principles:",
                     "forge runs in three modes on this system"):
            with self.subTest(text=text):
                self.assertEqual(_verdict(text), ("clean", None))

    def test_a_tool_whose_arguments_were_never_introspected_is_left_alone(self):
        # igos-install-* record positionals as null, not [] — "unknown", not
        # "none". An empty list there would be a claim nothing derived it.
        surface = safety._tool_surface()
        self.assertIsNone(surface["igos-install-chrome"]["positionals"])
        self.assertEqual(_verdict("Run `igos-install-chrome`."), ("clean", None))


class TheWaysOfBeingWrong(unittest.TestCase):
    """Every one of these was a false positive during authoring."""

    def test_prose_naming_a_tool_is_not_an_invocation(self):
        self.assertEqual(
            _verdict("InterGenOS uses the pkm package manager for software."),
            ("clean", None))

    def test_a_source_path_is_not_a_command(self):
        for text in ("`pkm/cli.py`", "`pkm.repo.generate_index()`",
                     "`intergen/web_server.py`"):
            with self.subTest(text=text):
                self.assertEqual(_verdict(f"See {text} for the detail."),
                                 ("clean", None))

    def test_a_manual_page_reference_is_not_a_command(self):
        self.assertEqual(_verdict("See `intergen(1)` for the full list."),
                         ("clean", None))

    def test_a_documentation_placeholder_is_not_a_claim(self):
        for text in ("pkm install <package>", "pkm <subcommand>", "pkm {token}",
                     "pkm ..."):
            with self.subTest(text=text):
                self.assertEqual(_verdict(f"The form is `{text}`."),
                                 ("clean", None))

    def test_a_glob_is_not_a_command(self):
        self.assertEqual(_verdict("Sign `igos-install-*.efi` after the build."),
                         ("clean", None))

    def test_an_honest_correction_is_not_a_fabrication(self):
        for text in ("there is no `pkm add` — use `pkm install`",
                     "not `pkm add`, the real command is `pkm install`"):
            with self.subTest(text=text):
                self.assertEqual(_verdict(text), ("clean", None))

    def test_trailing_prose_is_not_read_as_arguments(self):
        # The command ends at the dash; "that removes anything" is English.
        self.assertEqual(
            _verdict("Run `pkm install firefox` — that fetches it from the mirror."),
            ("clean", None))


class TheRepliesTheLiveModelActuallyWrote(unittest.TestCase):
    """Regression pins taken verbatim from the model serving on this machine.

    Every one of these came out of driving the real 9B rather than out of
    imagining a reply, and each was wrong in a way no authored test had covered.
    """

    def test_a_paragraph_after_a_code_span_is_not_a_command(self):
        # The reply as the model wrote it. A CLOSING backtick is
        # indistinguishable from an opening one, so the lead pattern ran across
        # the blank line and read the next paragraph as `pkm 's`.
        reply = (
            "pkm is a package manager, not a file saving tool.\n\n"
            "If you want to save a file, you would typically use standard file "
            "operations like:\n"
            "- `touch filename` to create an empty file\n"
            "- Any text editor like `nano filename` or `vim filename`\n\n"
            "pkm's actual functionality includes:\n"
            "- Installing packages: `pkm install package-name`\n"
            "- Updating packages: `pkm sync` then `pkm upgrade`\n"
            "- Removing packages: `pkm remove package-name`\n")
        self.assertEqual(_verdict(reply), ("clean", None),
                         "every command in this reply is real and the prose "
                         "around them is prose")

    def test_the_possessive_is_not_an_argument(self):
        for text in ("pkm's actual functionality includes:",
                     "forge's three modes are gui, tui and live",
                     "intergen's daemon owns the bus name"):
            with self.subTest(text=text):
                self.assertEqual(_verdict(text), ("clean", None))

    def test_a_command_in_a_fenced_block_is_read(self):
        # The live model answered inside a fence, and TWO separate faults hid it:
        # `<package>` disqualified the whole line, and `--no-interactive`
        # contains the word "no", which the negation guard read as the reply
        # correcting itself. `forge` has neither an `install` subcommand nor a
        # `--no-interactive` flag.
        reply = ("To do that, run:\n\n```\n"
                 "forge install <package> --no-interactive\n```\n")
        verdict, marker = _verdict(reply)
        self.assertEqual(verdict, "violation")
        self.assertIn("install", marker)

    def test_a_command_cannot_negate_itself_through_a_no_flag(self):
        verdict, _ = _verdict("Run `pkm install --no-confirm firefox`.")
        self.assertEqual(verdict, "violation",
                         "`--no-confirm` is invented; the `no` inside it is not "
                         "the reply denying anything")

    def test_a_real_no_flag_still_passes(self):
        self.assertEqual(_verdict("Run `pkm sync --no-wait`."), ("clean", None))

    def test_a_correction_on_the_preceding_line_is_still_a_correction(self):
        for text in ("There is no such command:\n\npkm clean",
                     "This does not work:\n\npkm prune"):
            with self.subTest(text=text):
                self.assertEqual(_verdict(text), ("clean", None))

    def test_the_invented_commands_the_live_model_offered_are_caught(self):
        # Each of these was a real draft from the live model, and each names a
        # command that does not exist.
        for reply, fragment in (
            ("The exact pkm command to clean out old cached package downloads "
             "and free disk space is:\n\npkm clean", "clean"),
            ("The flag is `--purge`. The exact command is: "
             "`pkm remove --purge <package_name>`", "--purge"),
            ("Run `pkm install --no-confirm firefox`.", "--no-confirm"),
            ("Run `forge install --yes`.", "install"),
            ("Check it with `intergen --version`.", "--version"),
        ):
            with self.subTest(fragment=fragment):
                verdict, marker = _verdict(reply)
                self.assertEqual(verdict, "violation")
                self.assertIn(fragment, marker)


class GroundTruthDegradesLoudly(unittest.TestCase):
    """A missing artifact never becomes a silent green (fail-loud rule)."""

    def setUp(self):
        self._real = safety._CAP_SURFACE_PATH
        safety.reset_surface_cache()

    def tearDown(self):
        safety._CAP_SURFACE_PATH = self._real
        safety.reset_surface_cache()

    def test_missing_artifact_yields_unavailable_not_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            safety._CAP_SURFACE_PATH = Path(tmp) / "absent.json"
            safety.reset_surface_cache()
            verdict, marker = _verdict("Run `pkm install foo`.")
            self.assertEqual(verdict, "unavailable")
            self.assertIsNotNone(marker)

    def test_missing_artifact_with_no_command_still_reports_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            safety._CAP_SURFACE_PATH = Path(tmp) / "absent.json"
            safety.reset_surface_cache()
            verdict, marker = _verdict("Nothing to run here.")
            self.assertEqual(verdict, "unavailable")
            self.assertIsNone(marker)


class TheArtifactIsTheTool(unittest.TestCase):
    """The ground truth agrees with the tools it claims to be derived from.

    This is the test the drift got past: the artifact was three subcommands and
    four global flags behind `pkm/cli.py` and nothing in the tree disagreed with
    it, because the generator its own _meta named did not exist."""

    def test_generator_ships(self):
        self.assertTrue(GENERATOR.is_file(),
                        f"{GENERATOR} must ship — the artifact's _meta names it, "
                        "and without it the surface can only be hand-edited")

    def test_shipped_artifact_matches_the_live_tool_surfaces(self):
        proc = subprocess.run(
            [sys.executable, str(GENERATOR), "--check"],
            capture_output=True, text=True, cwd=str(REPO))
        self.assertEqual(
            proc.returncode, 0,
            "capability-surface.json has drifted from the real parsers. "
            "Regenerate it: python3 scripts/gen-capability-surface.py\n"
            f"stdout: {proc.stdout}\nstderr: {proc.stderr}")

    def test_every_real_pkm_subcommand_is_in_the_surface(self):
        sys.path.insert(0, str(REPO))
        from pkm.cli import build_parser
        import argparse as _ap
        live: set[str] = set()
        for action in build_parser()._actions:
            if isinstance(action, _ap._SubParsersAction):
                live.update(action.choices)
        valid, _ = safety._pkm_surface()
        missing = sorted(live - set(valid))
        self.assertEqual(missing, [],
                         "the surface is missing real pkm subcommands; the gate "
                         "would call each of these a fabrication")

    def test_the_surface_carries_a_flag_table_for_every_introspected_tool(self):
        surface = safety._tool_surface()
        self.assertIn("pkm", surface)
        introspected = [n for n, s in surface.items() if s.get("introspected")]
        self.assertGreaterEqual(len(introspected), 3,
                                "pkm, forge and intergen must all be introspected")
        for name in introspected:
            with self.subTest(tool=name):
                spec = surface[name]
                self.assertIsInstance(spec.get("global_flags"), list)
                for flag in spec["global_flags"]:
                    self.assertIn("takes_value", flag,
                                  "without takes_value the gate reads a flag's "
                                  "VALUE as the subcommand")


class TheToolsOwnHelpAgreesWithItsHandlers(unittest.TestCase):
    """`intergen --help` is the last hand-kept copy of that CLI's interface.

    It had drifted: all four of `tool-log`'s options were printed under `glass`,
    which accepts none of them, and glass's own `--tail` and `--turn` were not
    printed at all — so a user following the tool's own help was handed three
    flags it ignores, one of them reading as a data delete. Nothing detected it,
    because nothing compared the printed text with the code. This does."""

    @staticmethod
    def _documented() -> dict[str, set[str]]:
        """{command: the flags print_usage() prints under it}."""
        import io
        import contextlib
        from intergen.cli import print_usage
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            print_usage()
        def names(token: str) -> str | None:
            """The option NAME a printed token carries.

            Help text writes options the way people type them — `--tier=N`,
            `--yes,` in a list, `[--raw]` as optional — so the printed token is
            normalised to the bare name before it is compared with the parser."""
            token = token.strip("[](),.")
            token = token.split("=", 1)[0]
            return token if token.startswith("-") else None

        out: dict[str, set[str]] = {}
        current: str | None = None
        for line in buf.getvalue().splitlines():
            body = line.strip()
            if not body:
                continue
            indent = len(line) - len(line.lstrip())
            if indent == 2 and not body.startswith("-"):
                current = body.split()[0]
                out.setdefault(current, set())
                # `last [--raw]` puts the flag on the command's own line.
                for token in body.split()[1:]:
                    name = names(token)
                    if name:
                        out[current].add(name)
            elif indent > 2 and body.startswith("-") and current:
                # A continuation line ("changes nothing and installs nothing")
                # is indented too but does not start with a dash.
                for token in body.split():
                    name = names(token)
                    if name:
                        out[current].add(name)
                    else:
                        break  # the rest of the line is the description
        return out

    def test_every_documented_flag_is_one_the_command_accepts(self):
        surface = safety._tool_surface()["intergen"]
        subs = surface["subcommands"]
        global_flags = {n for f in surface["global_flags"] for n in f["names"]}
        for command, flags in self._documented().items():
            if command not in subs:
                continue
            real = {n for f in subs[command]["flags"] for n in f["names"]}
            for flag in sorted(flags):
                with self.subTest(command=command, flag=flag):
                    self.assertIn(
                        flag, real | global_flags,
                        f"`intergen --help` documents `{flag}` under "
                        f"`{command}`, which does not read it")

    def test_every_flag_a_command_accepts_is_documented(self):
        surface = safety._tool_surface()["intergen"]
        documented = self._documented()
        for command, spec in sorted(surface["subcommands"].items()):
            real = {n for f in spec["flags"] for n in f["names"]}
            # Short aliases (-y) and the setup path's internal switches are not
            # part of the printed summary; long-form options are.
            real = {f for f in real if f.startswith("--")}
            if not real:
                continue
            with self.subTest(command=command):
                missing = sorted(real - documented.get(command, set()))
                self.assertEqual(
                    missing, [],
                    f"`intergen {command}` reads these options and "
                    "`intergen --help` does not mention them")


class TheGeneratorRefusesRatherThanShipAThinSurface(unittest.TestCase):
    """A generator that quietly emitted less would hand the gate a ground truth
    that calls real commands fabrications. Each extractor has a floor; this
    proves a floor actually fires rather than trusting that it would."""

    def test_the_intergen_extractor_refuses_on_an_unreadable_dispatcher(self):
        source = GENERATOR.read_text()
        self.assertIn("REFUSING", source)
        # Drive the real floor: point the extractor at a cli.py whose main()
        # dispatches nothing, and require a non-zero exit naming the reason.
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp)
            (fake / "intergen").mkdir()
            (fake / "intergen" / "cli.py").write_text(
                "def main():\n    command = 'x'\n    print(command)\n")
            probe = fake / "probe.py"
            probe.write_text(
                "import sys, pathlib\n"
                f"sys.path.insert(0, {str(GENERATOR.parent)!r})\n"
                "import importlib.util\n"
                f"spec = importlib.util.spec_from_file_location('g', {str(GENERATOR)!r})\n"
                "mod = importlib.util.module_from_spec(spec)\n"
                "spec.loader.exec_module(mod)\n"
                f"mod.REPO = pathlib.Path({str(fake)!r})\n"
                "mod._intergen_commands_from_source()\n")
            proc = subprocess.run([sys.executable, str(probe)],
                                  capture_output=True, text=True)
            self.assertNotEqual(proc.returncode, 0,
                                "a dispatcher the extractor cannot read must be "
                                "a refusal, not a short list")
            self.assertIn("REFUSING", proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
