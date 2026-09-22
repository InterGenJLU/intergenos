"""Execution tests for the Welcomer's privileged name-server verbs.

WHY THIS FILE EXISTS. The half of the name-lookup page that makes a chosen
server the one that answers was pinned by assertions on the helper's SOURCE
TEXT: that a line appears inside a function, that the dispatcher prints a
guard. Two real defects passed that suite — a reversal that cleared the two
properties on profiles the page had never touched, and a choice write that
reported success on a machine whose network client could not answer. Text
that reads correctly is not behaviour; only running the verb is.

HOW A TEST RUNS A PRIVILEGED VERB WITHOUT PRIVILEGE. The helper reads
INTERGEN_WELCOME_ROOT once, and honours it ONLY when it is not running as
root. Every path its name-server verbs touch then sits under that directory.
pkexec runs the helper as root, so on the privileged path the variable is
ignored; it is also stripped by pkexec's environment reset before that check
is reached. The tests below run as an ordinary user, point the helper at a
directory of their own, and put a stand-in network client and service manager
first on the PATH, so nothing here reads or writes this machine's own
resolver configuration or connection profiles.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PRIVHELPER = REPO_ROOT / "assets" / "intergen-welcome" / "intergen-welcome-privhelper"

# The stand-in network client. It keeps each connection profile as a file of
# key=value lines, answers the four listing and modify calls the helper makes,
# and records every call it was given. WELCOME_STUB_FAIL makes every call fail
# the way a client whose daemon is not running does.
NMCLI_STUB = r'''#!/bin/bash
printf 'nmcli %s\n' "$*" >> "$WELCOME_STUB_LOG"
if [ -n "${WELCOME_STUB_FAIL:-}" ]; then
    echo "Error: NetworkManager is not running." >&2
    exit 1
fi
profiles="$WELCOME_STUB_PROFILES"
prop_of() {  # prop_of <file> <key>
    sed -n "s/^$2=//p" "$1" | tail -1
}
case "$*" in
    "-t -f UUID connection show")
        for f in "$profiles"/*.profile; do
            [ -e "$f" ] || continue
            basename "$f" .profile
        done
        ;;
    "-t -f UUID,DEVICE connection show --active")
        for f in "$profiles"/*.profile; do
            [ -e "$f" ] || continue
            [ "$(prop_of "$f" active)" = "yes" ] || continue
            device=$(prop_of "$f" device)
            printf '%s:%s\n' "$(basename "$f" .profile)" "${device:---}"
        done
        ;;
    "-t -f ipv4.ignore-auto-dns connection show "*|"-t -f ipv6.ignore-auto-dns connection show "*)
        family=$3
        uuid=${!#}   # the LAST argument; ${*##* } does not strip a word off $*
        f="$profiles/$uuid.profile"
        [ -e "$f" ] || exit 1
        printf '%s:%s\n' "$family" "$(prop_of "$f" "$family")"
        ;;
    "connection modify "*)
        uuid=$3
        f="$profiles/$uuid.profile"
        [ -e "$f" ] || { echo "Error: unknown connection." >&2; exit 1; }
        if [ "$(prop_of "$f" readonly)" = "yes" ]; then
            echo "Error: this profile is read-only." >&2
            exit 1
        fi
        shift 3
        while [ "$#" -ge 2 ]; do
            printf '%s=%s\n' "$1" "$2" >> "$f"
            shift 2
        done
        ;;
    "device reapply "*)
        ;;
    *)
        echo "the stand-in network client was given a call it does not know: $*" >&2
        exit 64
        ;;
esac
exit 0
'''

# The stand-in service manager. The helper restarts the resolver through it;
# nothing else about this machine's services is touched.
SYSTEMCTL_STUB = r'''#!/bin/bash
printf 'systemctl %s\n' "$*" >> "$WELCOME_STUB_LOG"
if [ -n "${WELCOME_STUB_SYSTEMCTL_FAIL:-}" ]; then
    echo "Failed to restart systemd-resolved.service" >&2
    exit 1
fi
exit 0
'''

# The programs the helper's name-server verbs call besides those two. The
# directory that stands in for a machine WITHOUT a network client holds these
# and nothing else, so the real /usr/bin/nmcli cannot be reached through it.
BORROWED_PROGRAMS = (
    "awk", "basename", "bash", "cat", "chmod", "chown", "cut", "getent",
    "grep", "id", "ls", "mkdir", "mktemp", "rm", "sed", "sort", "tail",
)


class DnsVerbHarness(unittest.TestCase):
    """A machine of this test's own: a root directory, connection profiles,
    and stand-ins for the network client and the service manager."""

    def setUp(self):
        if os.geteuid() == 0:
            self.skipTest(
                "these tests point the helper at a temporary root, which it "
                "honours only when it is NOT running as root")
        self.root = Path(tempfile.mkdtemp(prefix="welcome-dns-testroot-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

        self.profiles = self.root / "profiles"
        self.profiles.mkdir()
        self.calls = self.root / "external-commands-called.log"
        self.calls.write_text("", encoding="utf-8")

        self.bin = self.root / "bin"
        self.bin_without_nmcli = self.root / "bin-without-nmcli"
        for directory in (self.bin, self.bin_without_nmcli):
            directory.mkdir()
            self._write_program(directory / "systemctl", SYSTEMCTL_STUB)
            for program in BORROWED_PROGRAMS:
                found = shutil.which(program)
                if found:
                    (directory / program).symlink_to(found)
        self._write_program(self.bin / "nmcli", NMCLI_STUB)

        self.add_profile("11111111-1111-1111-1111-111111111111",
                         name="wired", device="eno2", active=True)
        self.add_profile("22222222-2222-2222-2222-222222222222",
                         name="spare-wifi")

    # -- the machine this test hands the helper ---------------------------

    def _write_program(self, path, text):
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755)

    def add_profile(self, uuid, name, device="", active=False,
                    properties=None, readonly=False):
        lines = [f"name={name}", f"device={device}",
                 f"active={'yes' if active else 'no'}"]
        if readonly:
            lines.append("readonly=yes")
        for key, value in (properties or {}).items():
            lines.append(f"{key}={value}")
        (self.profiles / f"{uuid}.profile").write_text(
            "\n".join(lines) + "\n", encoding="utf-8")

    def profile_property(self, uuid, key):
        """The value the stand-in client would report: the LAST one written."""
        path = self.profiles / f"{uuid}.profile"
        value = None
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{key}="):
                value = line.split("=", 1)[1]
        return value

    def uuids(self):
        return sorted(p.stem for p in self.profiles.glob("*.profile"))

    # -- running a verb ----------------------------------------------------

    def run_verb(self, *args, with_nmcli=True, client_fails=False,
                 resolver_restart_fails=False):
        env = {
            "PATH": str(self.bin if with_nmcli else self.bin_without_nmcli),
            "HOME": str(self.root),
            "INTERGEN_WELCOME_ROOT": str(self.root),
            "WELCOME_STUB_LOG": str(self.calls),
            "WELCOME_STUB_PROFILES": str(self.profiles),
        }
        if client_fails:
            env["WELCOME_STUB_FAIL"] = "1"
        if resolver_restart_fails:
            env["WELCOME_STUB_SYSTEMCTL_FAIL"] = "1"
        return subprocess.run(["bash", str(PRIVHELPER), *args],
                              capture_output=True, text=True, timeout=120,
                              env=env)

    # -- what the verbs write ---------------------------------------------

    @property
    def dropin(self):
        return self.root / "etc/systemd/resolved.conf.d/50-intergen-welcome-dns.conf"

    @property
    def record(self):
        return self.root / "var/lib/intergen/welcome/dns-connections"

    @property
    def dispatcher(self):
        return self.root / "etc/NetworkManager/dispatcher.d/50-intergen-welcome-dns"

    def commands(self):
        return [line for line in
                self.calls.read_text(encoding="utf-8").splitlines() if line]


class TestTheVerbsRunAgainstATestRoot(DnsVerbHarness):
    """The harness itself, and the choice write's ordinary path.

    Each of these is a statement about what the verb DOES, taken from the
    files it left behind and the calls it made.
    """

    def test_a_choice_writes_its_dropin_under_the_test_root(self):
        result = self.run_verb("dns-use-cloudflare")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.dropin.is_file(),
                        "the choice wrote no drop-in under the test root; "
                        f"stderr was: {result.stderr}")
        text = self.dropin.read_text(encoding="utf-8")
        self.assertIn("# Selection: cloudflare", text)
        self.assertIn("Domains=~.", text)

    def test_a_choice_sets_both_properties_on_every_profile(self):
        result = self.run_verb("dns-use-cloudflare")
        self.assertEqual(result.returncode, 0, result.stderr)
        for uuid in self.uuids():
            for family in ("ipv4.ignore-auto-dns", "ipv6.ignore-auto-dns"):
                self.assertEqual(self.profile_property(uuid, family), "yes",
                                 f"{family} was not set on {uuid}")

    def test_a_choice_installs_the_dispatcher_and_restarts_the_resolver(self):
        result = self.run_verb("dns-use-cloudflare")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.dispatcher.is_file(),
                        "the dispatcher fragment was not installed")
        self.assertIn("systemctl restart systemd-resolved", self.commands())

    def test_the_verbs_write_nothing_outside_the_test_root(self):
        real_dropin = Path(
            "/etc/systemd/resolved.conf.d/50-intergen-welcome-dns.conf")
        before = real_dropin.exists() and real_dropin.stat().st_mtime_ns
        self.run_verb("dns-use-cloudflare")
        self.run_verb("dns-use-network-default")
        after = real_dropin.exists() and real_dropin.stat().st_mtime_ns
        self.assertEqual(before, after,
                         "a verb reached this machine's own resolver "
                         "configuration")


class TestTheReversalUndoesOnlyItsOwnWork(DnsVerbHarness):
    """(finding 1) Giving up a choice puts back what the choice changed, and
    nothing else.

    The two properties are not this page's private property. A profile can
    carry ignore-auto-dns=yes because its owner set it — a work connection
    whose name servers must not be mixed with a network's, for one. A
    reversal that writes `no` across every profile destroys that, and it did
    so on machines where this page had never written anything at all.
    """

    OWNER = "33333333-3333-3333-3333-333333333333"

    def add_owner_profile(self):
        self.add_profile(self.OWNER, name="work-vpn", properties={
            "ipv4.ignore-auto-dns": "yes", "ipv6.ignore-auto-dns": "yes"})

    def test_a_machine_that_never_chose_is_left_alone(self):
        self.add_owner_profile()
        result = self.run_verb("dns-use-network-default")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.profile_property(self.OWNER,
                                               "ipv4.ignore-auto-dns"), "yes")
        self.assertEqual(self.profile_property(self.OWNER,
                                               "ipv6.ignore-auto-dns"), "yes")
        self.assertEqual(
            [c for c in self.commands() if "connection modify" in c], [],
            "the reversal changed connection profiles on a machine that never "
            "chose a name server")

    def test_the_reversal_puts_back_only_what_the_choice_changed(self):
        self.add_owner_profile()
        self.assertEqual(self.run_verb("dns-use-cloudflare").returncode, 0)
        result = self.run_verb("dns-use-network-default")
        self.assertEqual(result.returncode, 0, result.stderr)
        for uuid in ("11111111-1111-1111-1111-111111111111",
                     "22222222-2222-2222-2222-222222222222"):
            self.assertEqual(self.profile_property(uuid,
                                                   "ipv4.ignore-auto-dns"),
                             "no", f"{uuid} was not put back")
        self.assertEqual(
            self.profile_property(self.OWNER, "ipv4.ignore-auto-dns"), "yes",
            "the reversal destroyed a setting its owner made, which this page "
            "never changed")
        self.assertEqual(
            self.profile_property(self.OWNER, "ipv6.ignore-auto-dns"), "yes")

    def test_the_reversal_removes_both_fragments(self):
        self.assertEqual(self.run_verb("dns-use-cloudflare").returncode, 0)
        self.assertEqual(self.run_verb("dns-use-network-default").returncode, 0)
        self.assertFalse(self.dropin.exists(), "the drop-in survived")
        self.assertFalse(self.dispatcher.exists(), "the dispatcher survived")

    def test_a_connection_the_dispatcher_caught_is_put_back_too(self):
        # A profile made AFTER the choice is set by the dispatcher, not by the
        # verb, so the verb's own record cannot know about it. The dispatcher
        # records what it changed for the same reason the verb does.
        self.assertEqual(self.run_verb("dns-use-cloudflare").returncode, 0)
        later = "44444444-4444-4444-4444-444444444444"
        self.add_profile(later, name="a-network-met-later", device="wlan0",
                         active=True)
        dispatcher = subprocess.run(
            ["bash", str(self.dispatcher), "wlan0", "up"],
            capture_output=True, text=True, timeout=120,
            env={"PATH": str(self.bin), "HOME": str(self.root),
                 "CONNECTION_UUID": later,
                 "WELCOME_STUB_LOG": str(self.calls),
                 "WELCOME_STUB_PROFILES": str(self.profiles)})
        self.assertEqual(dispatcher.returncode, 0, dispatcher.stderr)
        self.assertEqual(self.profile_property(later, "ipv4.ignore-auto-dns"),
                         "yes", "the dispatcher did not apply the choice")
        self.assertIn(later, self.record.read_text(encoding="utf-8"),
                      "the dispatcher applied the choice to a connection and "
                      "left no record of it, so the reversal cannot know it "
                      "has to be put back")
        self.assertEqual(self.run_verb("dns-use-network-default").returncode, 0)
        self.assertEqual(self.profile_property(later, "ipv4.ignore-auto-dns"),
                         "no",
                         "a connection the dispatcher applied the choice to "
                         "was left ignoring the servers its network hands out")


class TestTheChoiceFailsWhenItCannotBeApplied(DnsVerbHarness):
    """(finding 2) A choice that reached no connection is not a choice made.

    The verb writes the servers for the resolver and then makes them the ones
    that answer by setting two properties on every connection profile. On a
    machine whose network client is not installed, or whose client cannot
    answer, the second half silently did nothing and the verb still exited 0 —
    so the page told the user their name server had been changed while the
    machine went on using the one its network hands out.
    """

    def assert_nothing_was_left_behind(self, result):
        self.assertNotEqual(result.returncode, 0,
                            "the verb reported success")
        self.assertFalse(self.dropin.exists(),
                         "a failed choice left its drop-in on disk")
        self.assertFalse(self.dispatcher.exists(),
                         "a failed choice left its dispatcher on disk")
        self.assertFalse(self.record.exists(),
                         "a failed choice left a record of connections it did "
                         "not end up changing")

    def test_a_machine_with_no_network_client_fails_the_choice(self):
        result = self.run_verb("dns-use-cloudflare", with_nmcli=False)
        self.assert_nothing_was_left_behind(result)
        self.assertIn("connections", result.stderr.lower(),
                      "nothing told the caller why:\n" + result.stderr)

    def test_a_client_that_cannot_answer_fails_the_choice(self):
        result = self.run_verb("dns-use-cloudflare", client_fails=True)
        self.assert_nothing_was_left_behind(result)

    def test_a_profile_that_refuses_puts_the_others_back(self):
        self.add_profile("55555555-5555-5555-5555-555555555555",
                         name="read-only-one", readonly=True)
        result = self.run_verb("dns-use-cloudflare")
        self.assert_nothing_was_left_behind(result)
        for uuid in ("11111111-1111-1111-1111-111111111111",
                     "22222222-2222-2222-2222-222222222222"):
            self.assertNotEqual(
                self.profile_property(uuid, "ipv4.ignore-auto-dns"), "yes",
                f"{uuid} was left ignoring the servers its network hands out "
                "by a choice that failed")

    def test_a_failed_choice_leaves_an_earlier_choice_standing(self):
        self.assertEqual(self.run_verb("dns-use-cloudflare").returncode, 0)
        before = self.dropin.read_text(encoding="utf-8")
        result = self.run_verb("dns-use-quad9", client_fails=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.dropin.exists(),
                        "a failed choice removed the choice that was standing")
        self.assertEqual(self.dropin.read_text(encoding="utf-8"), before,
                         "a failed choice left the earlier choice rewritten")

    def test_a_machine_with_no_connection_profiles_still_takes_the_choice(self):
        for profile in self.profiles.glob("*.profile"):
            profile.unlink()
        result = self.run_verb("dns-use-cloudflare")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.dropin.is_file())
        self.assertFalse(self.record.exists(),
                         "there was nothing to change, so there is nothing to "
                         "put back later")


class TestTheUpgradeRepairOnlyRepairs(DnsVerbHarness):
    """(finding 3) The repair that runs at every upgrade does nothing to a
    machine that is already as its choice says.

    The repair exists for machines that chose a name server before this
    helper learned what else that choice needs. It runs from the package's
    post_install hook, as root, inside a package transaction, on every
    upgrade. On a machine already in order it rewrote the drop-in, restarted
    the resolver, wrote both properties on every profile and reapplied the
    device — work with no effect, in the one place where a failure stops an
    upgrade.
    """

    def writing_commands(self):
        """The calls that CHANGE this machine. Reading is how the repair
        establishes that there is nothing to do, so reads are not touches."""
        return [c for c in self.commands()
                if ("connection modify" in c or "device reapply" in c
                    or "systemctl" in c)]

    def state(self):
        return {path: path.stat().st_mtime_ns
                for path in (self.dropin, self.dispatcher, self.record)
                if path.exists()}

    def test_a_machine_already_as_its_choice_says_is_left_untouched(self):
        self.assertEqual(self.run_verb("dns-use-cloudflare").returncode, 0)
        before = self.state()
        self.calls.write_text("", encoding="utf-8")
        result = self.run_verb("dns-reapply-selection")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.writing_commands(), [],
                         "the repair changed this machine although it needed "
                         "no repair")
        self.assertEqual(self.state(), before,
                         "the repair rewrote files on a machine that needed "
                         "no repair")

    def test_a_machine_that_needs_the_repair_still_gets_it(self):
        self.assertEqual(self.run_verb("dns-use-cloudflare").returncode, 0)
        # What a machine that chose before this half existed looks like: the
        # drop-in, and nothing else the choice now needs.
        self.dispatcher.unlink()
        self.record.unlink()
        for uuid in self.uuids():
            path = self.profiles / f"{uuid}.profile"
            path.write_text("".join(
                line + "\n" for line in
                path.read_text(encoding="utf-8").splitlines()
                if "ignore-auto-dns" not in line), encoding="utf-8")
        result = self.run_verb("dns-reapply-selection")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.dispatcher.is_file(),
                        "the repair did not install the dispatcher")
        for uuid in self.uuids():
            self.assertEqual(
                self.profile_property(uuid, "ipv4.ignore-auto-dns"), "yes",
                f"the repair left {uuid} unrepaired")

    def test_a_machine_that_never_chose_is_not_touched_by_the_repair(self):
        result = self.run_verb("dns-reapply-selection")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.writing_commands(), [])
        self.assertFalse(self.dropin.exists())


class TestWhatTheUserIsTold(DnsVerbHarness):
    """(finding 4) The public account of the reversal matches what it does.

    Undoing the choice puts back the connections the choice changed. It does
    not, and must not, force the machine onto the servers its networks hand
    out on a connection its owner set that way on purpose — so text promising
    that the machine returns "exactly to what the network hands out" says
    more than the code does. The drop-in's own comment had the same problem
    from the other side: deleting the file by hand leaves every connection
    still ignoring the servers its network supplies.
    """

    CHANGELOG = REPO_ROOT / "CHANGELOG.md"
    RECIPE = REPO_ROOT / "packages/desktop/intergen-welcome/package.yml"

    def test_the_dropin_says_how_to_undo_the_whole_choice(self):
        self.assertEqual(self.run_verb("dns-use-cloudflare").returncode, 0)
        text = self.dropin.read_text(encoding="utf-8")
        self.assertIn("Use what this network provides", text)
        self.assertIn("deleting this file", text.lower(),
                      "the drop-in does not warn that deleting it by hand "
                      "leaves the rest of the choice in place:\n" + text)

    def test_the_dispatcher_says_the_same(self):
        proc = subprocess.run(["bash", str(PRIVHELPER), "dns-dispatcher-script"],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Use what this network provides", proc.stdout)

    def test_no_public_text_promises_more_than_the_reversal_does(self):
        claim = "exactly to what the network hands out"
        other = "exactly what the network hands out"
        for path in (self.CHANGELOG, self.RECIPE,
                     Path(PRIVHELPER)):
            text = path.read_text(encoding="utf-8")
            for wording in (claim, other):
                self.assertFalse(
                    wording in text,
                    f"{path.name} says \"{wording}\": it promises the whole "
                    "machine goes back to the servers its networks hand out. "
                    "The reversal puts back the connections the choice "
                    "changed, and leaves a connection its owner set alone")


if __name__ == "__main__":
    unittest.main()
