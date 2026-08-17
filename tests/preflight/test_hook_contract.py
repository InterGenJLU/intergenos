#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""scripts/check-hook-contract.py — lifecycle hooks are maintenance-only.

A recipe's lifecycle functions now ship inside the signed archive and run on
the target, which makes them a delivery mechanism the manifest, the signature
and every downstream integrity gate cannot see. The contract is that a hook may
maintain — enable, refresh a cache or database, generate machine-unique state,
restore attributes on paths the package already owns — and may not deliver
content.

Two properties matter equally here, and the second is the one a lazier gate
gets wrong: it must CATCH a payload write, and it must NOT fire on the
maintenance a hook exists to do. A gate that cannot tell `cp
/usr/share/foo/template /var/lib/foo/conf` — a shipped template copied into
machine state — from `cp something /usr/bin/foo` would push recipes out of the
one mechanism that serves them.

The false-positive cases below are not hypothetical. Every one of them was a
finding this gate produced against the real tree before it was narrowed:
an English sentence containing the word "install" and a path, a `cp` judged on
its source operand rather than its destination, a heredoc's own contents read
as commands, and a wrapped command judged on the half of it before the
backslash.
"""
import importlib.util
import io
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE = REPO_ROOT / "scripts" / "check-hook-contract.py"

_spec = importlib.util.spec_from_file_location("check_hook_contract", GATE)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def _classify(line):
    hit = _mod.classify(line)
    return hit[0] if hit else None


class ViolationDetectionTest(unittest.TestCase):
    """What the contract forbids."""

    def test_payload_write_forms(self):
        for line, rule in (
            ("install -m755 helper /usr/bin/helper", "payload-write"),
            ("cp -a stuff /opt/vendor/stuff", "payload-write"),
            ("mv -v /usr/share/doc/a /usr/share/doc/b", "payload-write"),
            ("ln -sfv /opt/rustc/x /usr/share/zsh/site-functions", "payload-write"),
            ("ln -svfn rustc-1.0 /opt/rustc", "payload-write"),
        ):
            self.assertEqual(_classify(line), rule, line)

    def test_payload_redirect_forms(self):
        for line in ("tool --completions > /usr/share/bash-completion/completions/x",
                     'cat > /usr/bin/run-parts << "RUNPARTS"',
                     "tool | tee /usr/share/x"):
            self.assertEqual(_classify(line), "payload-redirect", line)

    def test_etc_creation_forms(self):
        for line in ('cat > /etc/pam.d/sudo << "EOF"',
                     'cat >> /etc/aliases << "EOF"',
                     'echo "ServerName x" > /etc/cups/client.conf',
                     "install -v -m644 /etc/login.defs /etc/login.defs.orig",
                     "ln -sfv /usr/share/zoneinfo/UTC /etc/zoneinfo-default"):
            self.assertEqual(_classify(line), "etc-creation", line)

    def test_directory_creation_is_reported_as_what_it_is(self):
        self.assertEqual(_classify("install -vdm755 /etc/pam.d"),
                         "etc-dir-creation")
        self.assertEqual(_classify("mkdir -pv /etc/ld.so.conf.d"),
                         "etc-dir-creation")
        self.assertEqual(_classify("install -v -m755 -d /usr/lib/cracklib"),
                         "payload-dir-create")

    def test_in_place_edit_forms(self):
        for line in ("sed -i 's/a/b/' /etc/nsswitch.conf",
                     'sed -e "$a x" -i /etc/pulse/client.conf',
                     "sed 's/x//' -i /etc/mke2fs.conf",
                     'sed -i "s/chage/$P/" "/etc/pam.d/${P}"',
                     'perl -pi -e "s/a/b/" /usr/lib/x.pm'):
            self.assertEqual(_classify(line), "in-place-edit", line)

    def test_a_quoted_sed_expression_does_not_hide_the_target(self):
        """Splitting on whitespace put an `&` token before the filename and
        stopped the scan there — a violation the gate saw and then dropped."""
        self.assertEqual(
            _classify('sed -i "s/^${FUNCTION}/# &/" /etc/login.defs'),
            "in-place-edit")


class MaintenanceIsNotAViolationTest:
    """Marker base — see NoFalsePositiveTest; kept separate for readability."""


class NoFalsePositiveTest(unittest.TestCase):
    """What the contract explicitly permits, and what merely looks like a write."""

    def test_permitted_maintenance(self):
        for line in ("ldconfig",
                     "systemctl enable sshd.service",
                     "install-info /usr/share/info/x.info /usr/share/info/dir",
                     "glib-compile-schemas /usr/share/glib-2.0/schemas",
                     "gtk-update-icon-cache -q /usr/share/icons/hicolor",
                     "fc-cache -fs",
                     "depmod -a",
                     "chown -R ldap:ldap /var/lib/openldap",
                     "chmod 700 /var/lib/x",
                     "setcap cap_net_raw+ep /usr/bin/ping"):
            self.assertIsNone(_classify(line), line)

    def test_a_shipped_template_copied_into_machine_state(self):
        """The maintenance a hook is FOR. Judging the line on any path it
        mentions instead of on its DESTINATION forbids exactly this."""
        for line in ("cp /usr/share/foo/foo.conf.template /var/lib/foo/foo.conf",
                     "install -m600 /usr/share/x/default /var/lib/x/conf",
                     "cat /usr/share/x/seed > /var/lib/x/state"):
            self.assertIsNone(_classify(line), line)

    def test_machine_unique_generation_under_var_and_run(self):
        for line in ("ssh-keygen -A",
                     "mkdir -p /var/lib/x",
                     "printf seed > /run/x/state",
                     "systemd-machine-id-setup"):
            self.assertIsNone(_classify(line), line)

    def test_prose_containing_a_verb_is_not_a_command(self):
        """A recipe echoing a sentence that happens to contain "install" and a
        path was reported as an install into /etc by the first version."""
        self.assertIsNone(_classify(
            'echo "AppArmor not enabled (chroot install context) — '
            'profiles staged to /etc/apparmor.d/; loaded at boot."'))
        # The sentence that ENDS on a payload path is the one that survives a
        # destination-only rule: only requiring the verb to sit in command
        # position keeps it from reading as a write.
        self.assertIsNone(_classify(
            'echo "run install to place it at /usr/bin/foo"'))
        self.assertIsNone(_classify(
            'warn "could not cp the file to /etc/foo.conf"'))

    def test_a_removal_is_not_a_write(self):
        for line in ("rm -f /usr/share/doc/x/README.old",
                     "rmdir /etc/x"):
            self.assertIsNone(_classify(line), line)

    def test_reading_a_payload_path_is_not_a_write(self):
        for line in ("grep -q x /etc/nsswitch.conf",
                     "cat /usr/share/x/version",
                     "test -f /usr/bin/x && ldconfig"):
            self.assertIsNone(_classify(line), line)

    def test_a_redirect_to_dev_null_is_not_a_write(self):
        self.assertIsNone(_classify("install -dm755 /var/lib/x 2>/dev/null"))
        self.assertIsNone(_classify("ldconfig >/dev/null 2>&1"))


class BodyScanTest(unittest.TestCase):
    """Whole-function properties: heredocs, continuations, line numbers."""

    def test_heredoc_contents_are_data_not_commands(self):
        body = textwrap.dedent("""\
            cat > /var/lib/x/conf << "EOF"
            # the config says:
            install -m755 thing /usr/bin/thing
            EOF
            ldconfig
            """)
        self.assertEqual(_mod.violations_in_body(body, 1), [])

    def test_the_scan_resumes_after_the_heredoc(self):
        """Dropping to end-of-function at the first heredoc would silently
        stop checking — the shape of every violation after it."""
        body = textwrap.dedent("""\
            cat > /var/lib/x/conf << "EOF"
            data
            EOF
            install -m755 thing /usr/bin/thing
            """)
        found = _mod.violations_in_body(body, 10)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][0], "payload-write")
        self.assertEqual(found[0][1], 13, "wrong build.sh line reported")

    def test_a_wrapped_command_is_judged_on_its_destination(self):
        body = ("ln -sfv /opt/rustc/share/zsh/site-functions/_cargo \\\n"
                "        /usr/share/zsh/site-functions\n")
        found = _mod.violations_in_body(body, 1)
        self.assertEqual([f[0] for f in found], ["payload-write"])

    def test_a_wrapped_command_into_machine_state_is_not_flagged(self):
        body = ("cp /usr/share/foo/template \\\n"
                "   /var/lib/foo/conf\n")
        self.assertEqual(_mod.violations_in_body(body, 1), [])

    def test_comments_are_not_scanned(self):
        body = "# install -m755 thing /usr/bin/thing\nldconfig\n"
        self.assertEqual(_mod.violations_in_body(body, 1), [])

    def test_line_numbers_are_build_sh_line_numbers(self):
        body = "ldconfig\ninstall -m755 t /usr/bin/t\n"
        found = _mod.violations_in_body(body, 100)
        self.assertEqual(found[0][1], 101)


class TreeScanTest(unittest.TestCase):
    """The gate over a packages tree, including its fail-closed edges."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def _recipe(self, tier, name, build_sh):
        d = self.root / tier / name
        d.mkdir(parents=True)
        (d / "build.sh").write_text(build_sh)
        return d

    def _run(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = _mod.main(["--packages", str(self.root), *extra])
        return rc, out.getvalue() + err.getvalue()

    def test_clean_tree_passes(self):
        self._recipe("core", "clean",
                     "post_install() {\n    ldconfig\n}\n")
        rc, text = self._run()
        self.assertEqual(rc, 0, text)
        self.assertIn("none writes payload", text)

    def test_offender_fails_and_is_named_with_recipe_and_line(self):
        self._recipe("core", "bad",
                     "post_install() {\n"
                     "    ldconfig\n"
                     "    install -m755 t /usr/bin/t\n"
                     "}\n")
        rc, text = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("bad", text)
        self.assertIn("core/bad/build.sh:3", text)
        self.assertIn("payload-write", text)

    def test_an_empty_inventory_is_not_a_pass(self):
        """A gate's exit 0 must mean the whole tree was checked and is clean,
        never that there was nothing to check (review finding H4)."""
        self._recipe("core", "nohooks", "build() {\n    make\n}\n")
        rc, text = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("empty inventory", text)

    def test_an_unreadable_lifecycle_function_fails_closed(self):
        """The seal seam refuses to ship a hook it cannot extract. A gate that
        passed the same recipe would certify what the builder rejects."""
        self._recipe("core", "clean", "post_install() {\n    ldconfig\n}\n")
        self._recipe("core", "truncated", "post_install() {\n    ldconfig\n")
        rc, text = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("could not be read", text)
        self.assertIn("truncated", text)

    def test_a_missing_packages_tree_is_an_argument_error(self):
        rc, _ = self._run()  # empty dir: no tiers at all
        self.assertEqual(rc, 1)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = _mod.main(["--packages", str(self.root / "nope")])
        self.assertEqual(rc, 2)

    def test_unknown_event_is_refused(self):
        self._recipe("core", "clean", "post_install() {\n    ldconfig\n}\n")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = _mod.main(["--packages", str(self.root),
                            "--events", "post_oops"])
        self.assertEqual(rc, 2)

    def test_every_sealed_event_is_gated_by_default(self):
        """The seal seam seals six lifecycle events and each one executes on
        the target. Gating only post_install — where today's violations happen
        to live — would leave five delivery surfaces unchecked."""
        sys.path.insert(0, str(REPO_ROOT / "igos-build"))
        try:
            import hookseal
        finally:
            sys.path.pop(0)
        for event in hookseal.LIFECYCLE_EVENTS:
            with self.subTest(event=event):
                td = tempfile.TemporaryDirectory()
                self.addCleanup(td.cleanup)
                root = Path(td.name)
                d = root / "core" / "bad"
                d.mkdir(parents=True)
                (d / "build.sh").write_text(
                    f"{event}() {{\n    install -m755 t /usr/bin/t\n}}\n")
                out, err = io.StringIO(), io.StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    rc = _mod.main(["--packages", str(root)])
                text = out.getvalue() + err.getvalue()
                self.assertEqual(rc, 1, text)
                self.assertIn(f"{event}()", text)

    def test_events_can_be_narrowed_explicitly(self):
        self._recipe("core", "pre",
                     "pre_install() {\n    install -m755 t /usr/bin/t\n}\n")
        rc, text = self._run("--events", "post_install")
        self.assertEqual(rc, 1)
        self.assertIn("empty inventory", text,
                      "narrowing to an event no recipe declares must report "
                      "an empty inventory, not certify a clean tree")


class MachineUniqueExemptionTest(unittest.TestCase):
    """The contract permits machine-unique generation; the detector cannot
    read intent, so the clause is made specific as a named list.

    The exemption is safe rather than a hole because the hook-output recorder
    registers what the hook creates to the owning package: the path is exempt
    from SHIPPING in the archive, not from being owned.
    """

    def test_the_exempt_path_is_reported_not_dropped(self):
        self.assertEqual(
            _classify("ln -sfv /usr/share/zoneinfo/UTC /etc/localtime"),
            "exempt-machine-unique")

    def test_the_list_is_specific_not_a_prefix(self):
        """A neighbouring path must not inherit the exemption."""
        for line in ("ln -sfv /usr/share/zoneinfo/UTC /etc/localtime.new",
                     "cat > /etc/local.conf << EOF",
                     "install -m644 x /etc/localtime.d/z"):
            self.assertNotEqual(_classify(line), "exempt-machine-unique", line)

    def test_the_list_is_small_and_reasoned(self):
        """It is an exemption list on a fail-closed gate: every entry carries
        its reason, and growth is the thing to notice."""
        self.assertIn("/etc/localtime", _mod.MACHINE_UNIQUE_ETC)
        self.assertLessEqual(len(_mod.MACHINE_UNIQUE_ETC), 3)
        for path, reason in _mod.MACHINE_UNIQUE_ETC.items():
            self.assertTrue(path.startswith("/etc/"), path)
            self.assertGreater(len(reason), 20, f"{path} has no real reason")

    def test_an_exemption_does_not_fail_the_gate_but_is_printed(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        d = root / "core" / "tz"
        d.mkdir(parents=True)
        (d / "build.sh").write_text(
            "post_install() {\n"
            "    ln -sfv /usr/share/zoneinfo/UTC /etc/localtime\n}\n")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = _mod.main(["--packages", str(root)])
        text = out.getvalue() + err.getvalue()
        self.assertEqual(rc, 0, text)
        self.assertIn("exempt as machine-unique", text)
        self.assertIn("/etc/localtime", text)


class LiveTreeTest(unittest.TestCase):
    """The gate against the repository as committed.

    The tree PASSES: the recipes that wrote payload from post_install have
    moved those lines into do_install, where the manifest records them and pkm
    owns them. This asserts the pass, and separately asserts that whatever the
    gate does report resolves — the recipe exists, the reported line number is
    that line, and the quoted text is what the file says there. A gate whose
    findings cannot be located is a gate nobody can act on.
    """

    def test_the_committed_tree_passes(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = _mod.main([])
        text = out.getvalue() + err.getvalue()
        self.assertEqual(rc, 0, text)

    def test_every_live_finding_resolves_to_the_line_it_names(self):
        sys.path.insert(0, str(REPO_ROOT / "igos-build"))
        try:
            import hookseal
        finally:
            sys.path.pop(0)

        packages = REPO_ROOT / "packages"
        walked = 0
        for tier in sorted(p for p in packages.iterdir() if p.is_dir()):
            for pkg in sorted(p for p in tier.iterdir() if p.is_dir()):
                build_sh = pkg / "build.sh"
                if not build_sh.is_file():
                    continue
                walked += 1
                text = build_sh.read_text(errors="replace")
                lines = text.splitlines()
                found, _ = _mod.scan_recipe(
                    text, list(hookseal.LIFECYCLE_EVENTS), hookseal)
                for event, rule_id, line_no, quoted, reason in found:
                    self.assertTrue(
                        1 <= line_no <= len(lines),
                        f"{pkg.name}: reported line {line_no} is outside "
                        f"build.sh ({len(lines)} lines)")
                    # A wrapped command is reported at the line it STARTS on,
                    # so the file's line is the first physical line of it.
                    self.assertTrue(
                        quoted.startswith(lines[line_no - 1].strip()[:40]),
                        f"{pkg.name}:{line_no} quoted {quoted!r} but the file "
                        f"says {lines[line_no - 1].strip()!r}")
        # The live tree may legitimately report NOTHING: the recipes that
        # wrote payload from post_install moved those lines into do_install,
        # and the last machine-unique writer (glibc-core's localtime pair,
        # this guard's former sentinel) was removed 2026-07-30 with the
        # install-time clobber fix. Zero findings must still be
        # distinguishable from a scan that did not run, so liveness is proven
        # here directly: the walk must have visited the real tree, and the
        # same scanner invocation must report a known-dirty body.
        self.assertGreater(
            walked, 400,
            "the walk visited almost no recipes — wrong tree root, not a "
            "clean tree")
        found, _ = _mod.scan_recipe(
            "post_install() {\n"
            "    install -m755 helper /usr/bin/helper\n}\n",
            list(hookseal.LIFECYCLE_EVENTS), hookseal)
        self.assertTrue(
            found,
            "the scanner failed to report a known payload write — the live "
            "scan above proved nothing")


class GateSharesTheSealsLocatorTest(unittest.TestCase):
    """The gate must check the text that actually ships.

    hookseal.locate_function is what the seal seam extracts and what pkm then
    executes. A gate with its own locator would drift from it, and the drift
    would be invisible: both would keep passing, on different text.
    """

    def test_the_gate_reads_the_seals_extraction(self):
        sys.path.insert(0, str(REPO_ROOT / "igos-build"))
        try:
            import hookseal
        finally:
            sys.path.pop(0)
        build_sh = ("build() {\n    install -m755 x /usr/bin/x\n}\n"
                    "post_install() {\n    ldconfig\n}\n")
        body, first = hookseal.locate_function(build_sh, "post_install")
        self.assertEqual(body.strip(), "ldconfig")
        # The build() write above is NOT a hook violation — it is do_install's
        # job, which is precisely what the contract redirects work into.
        found, unread = _mod.scan_recipe(build_sh, ["post_install"], hookseal)
        self.assertEqual((found, unread), ([], []))
        self.assertEqual(first, 5)


if __name__ == "__main__":
    unittest.main()
