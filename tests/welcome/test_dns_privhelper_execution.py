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
    "awk", "basename", "bash", "cat", "chmod", "chown", "cmp", "cut",
    "getent", "grep", "id", "ls", "mkdir", "mktemp", "rm", "sed",
    "sort", "stat", "tail",
)

# Two pass-through stand-ins that exist so a case can make ONE named call
# fail the way the real program fails, and leave every other call alone.
# Without an instruction in the environment each simply runs the real
# program, so a case that does not ask for a failure sees no difference.
#
# The real program's path is baked in when the stub is written, never looked
# up on PATH: the stub IS what PATH finds under that name, so resolving by
# name would call itself.
CHMOD_STUB = r'''#!/bin/bash
# The file a chmod acts on is its last argument.
target="${@: -1}"
case "${WELCOME_STUB_CHMOD_FAIL:-}" in
    copy)
        # The copy the helper takes of the existing drop-in: the drop-in's
        # own name with mktemp's suffix after it.
        case "$target" in
            "$WELCOME_STUB_DROPIN".??????)
                echo "chmod: cannot access '$target': No such file or directory" >&2
                exit 1 ;;
        esac ;;
    restore)
        # The SECOND chmod of the drop-in itself. On the failing path the
        # first is the helper setting the mode of the drop-in it just wrote,
        # and the second is the rollback putting the prior mode back.
        if [ "$target" = "$WELCOME_STUB_DROPIN" ]; then
            n=0
            [ -f "$WELCOME_STUB_CHMOD_COUNT" ] && n=$(@REAL_CAT@ "$WELCOME_STUB_CHMOD_COUNT")
            n=$((n + 1))
            printf '%s' "$n" > "$WELCOME_STUB_CHMOD_COUNT"
            if [ "$n" -ge 2 ]; then
                echo "chmod: cannot access '$target': No such file or directory" >&2
                exit 1
            fi
        fi ;;
esac
exec @REAL_CHMOD@ "$@"
'''

CAT_STUB = r'''#!/bin/bash
# The helper reads the existing drop-in exactly once before it writes
# anything: the copy it takes so it can put the machine back.
if [ "${WELCOME_STUB_CAT_FAIL:-}" = "backup" ] && [ "$1" = "$WELCOME_STUB_DROPIN" ]; then
    n=0
    [ -f "$WELCOME_STUB_CAT_COUNT" ] && n=$(@REAL_CAT@ "$WELCOME_STUB_CAT_COUNT")
    n=$((n + 1))
    printf '%s' "$n" > "$WELCOME_STUB_CAT_COUNT"
    if [ "$n" -eq 1 ]; then
        echo "cat: $1: No such file or directory" >&2
        exit 1
    fi
fi
exec @REAL_CAT@ "$@"
'''



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
        real_chmod = shutil.which("chmod")
        real_cat = shutil.which("cat")
        self.chmod_count = self.root / "chmod-calls-on-the-dropin"
        self.cat_count = self.root / "cat-calls-on-the-dropin"
        for directory in (self.bin, self.bin_without_nmcli):
            directory.mkdir()
            self._write_program(directory / "systemctl", SYSTEMCTL_STUB)
            for program in BORROWED_PROGRAMS:
                found = shutil.which(program)
                if found:
                    (directory / program).symlink_to(found)
            # These two replace the symlinks just made. They run the real
            # program unless a case asks for one named call to fail.
            if real_chmod:
                (directory / "chmod").unlink()
                self._write_program(
                    directory / "chmod",
                    CHMOD_STUB.replace("@REAL_CHMOD@", real_chmod)
                              .replace("@REAL_CAT@", real_cat or "/bin/cat"))
            if real_cat:
                (directory / "cat").unlink()
                self._write_program(
                    directory / "cat",
                    CAT_STUB.replace("@REAL_CAT@", real_cat))
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
                 resolver_restart_fails=False, chmod_fails=None,
                 backup_read_fails=False):
        env = {
            "PATH": str(self.bin if with_nmcli else self.bin_without_nmcli),
            "HOME": str(self.root),
            "INTERGEN_WELCOME_ROOT": str(self.root),
            "WELCOME_STUB_LOG": str(self.calls),
            "WELCOME_STUB_PROFILES": str(self.profiles),
            "WELCOME_STUB_DROPIN": str(self.dropin),
            "WELCOME_STUB_CHMOD_COUNT": str(self.chmod_count),
            "WELCOME_STUB_CAT_COUNT": str(self.cat_count),
        }
        if client_fails:
            env["WELCOME_STUB_FAIL"] = "1"
        if resolver_restart_fails:
            env["WELCOME_STUB_SYSTEMCTL_FAIL"] = "1"
        if chmod_fails:
            env["WELCOME_STUB_CHMOD_FAIL"] = chmod_fails
        if backup_read_fails:
            env["WELCOME_STUB_CAT_FAIL"] = "backup"
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


class TestTheRollbackPutsBackExactlyWhatWasThere(DnsVerbHarness):
    """A rollback that says the machine was left as it was must mean it.

    A second read of this lane measured the gap. The failure path restores a
    prior drop-in by writing a shell variable back with `printf '%s\n'` and
    then `chmod 0644`. Both of those are the helper describing its OWN file
    shape, not the file it found:

      * `$(cat file)` strips every trailing newline and `printf '%s\n'` adds
        exactly one back, so a file with none, or with two, comes back with
        one. The bytes are not the bytes.
      * `chmod 0644` on a drop-in that was 0600 WIDENS it. A resolver
        configuration file that its owner had made owner-only becomes
        world-readable because an unrelated choice failed.

    The existing case that covers this path could not see either, because the
    drop-in it compares against was written by this same helper a moment
    earlier — so it already had the helper's own shape and the helper's own
    mode, and writing them back looked like preservation.

    The sentence on the failure path says "this machine has been left as it
    was". These cases hold it to that: byte for byte, and mode for mode.
    """

    #: A prior drop-in that this helper did not write: owner-only, and with a
    #: trailing-newline shape of its own.
    PRIOR_BYTES = b"[Resolve]\nDNS=192.0.2.1\n\n"
    PRIOR_MODE = 0o600

    def write_prior_dropin(self, data=None, mode=None):
        path = self.dropin
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.PRIOR_BYTES if data is None else data)
        path.chmod(self.PRIOR_MODE if mode is None else mode)
        return path

    def test_the_bytes_come_back_exactly(self):
        path = self.write_prior_dropin()
        before = path.read_bytes()
        result = self.run_verb("dns-use-cloudflare", resolver_restart_fails=True)
        self.assertNotEqual(result.returncode, 0,
                            "the failing restart did not fail the choice")
        self.assertEqual(
            path.read_bytes(), before,
            "the rollback said the machine was left as it was and rewrote the "
            "prior drop-in's bytes:\n" + result.stderr)

    def test_the_mode_comes_back_exactly(self):
        import stat
        path = self.write_prior_dropin()
        before = stat.S_IMODE(path.stat().st_mode)
        result = self.run_verb("dns-use-cloudflare", resolver_restart_fails=True)
        self.assertNotEqual(result.returncode, 0)
        after = stat.S_IMODE(path.stat().st_mode)
        self.assertEqual(
            after, before,
            f"the rollback changed the prior drop-in's mode from "
            f"{before:04o} to {after:04o}; a resolver configuration file its "
            f"owner had made owner-only is now readable by everyone because "
            f"an unrelated choice failed:\n" + result.stderr)

    def test_a_prior_dropin_with_no_trailing_newline_is_not_given_one(self):
        path = self.write_prior_dropin(data=b"[Resolve]\nDNS=192.0.2.1")
        before = path.read_bytes()
        self.run_verb("dns-use-cloudflare", resolver_restart_fails=True)
        self.assertEqual(path.read_bytes(), before,
                         "the rollback added a trailing newline the file did "
                         "not have")

    def test_the_failure_still_says_the_machine_was_left_as_it_was(self):
        """The non-masking control: the sentence, and the failure, both stay."""
        self.write_prior_dropin()
        result = self.run_verb("dns-use-cloudflare", resolver_restart_fails=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("left as it was", result.stderr, result.stderr)

    def test_a_machine_with_no_prior_dropin_still_ends_with_none(self):
        """The other non-masking control: nothing of the failed choice stays."""
        self.assertFalse(self.dropin.exists())
        result = self.run_verb("dns-use-cloudflare", resolver_restart_fails=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.dropin.exists(),
                         "a failed choice left its drop-in on a machine that "
                         "had none:\n" + result.stderr)

    def test_nothing_of_the_captured_copy_is_left_behind(self):
        """Whatever the helper uses to remember the file must not survive."""
        self.write_prior_dropin()
        self.run_verb("dns-use-cloudflare", resolver_restart_fails=True)
        strays = [p for p in self.dropin.parent.iterdir()
                  if p.name != self.dropin.name]
        self.assertEqual(strays, [],
                         f"the rollback left files behind: {strays}")


class TestASnapshotThatCouldNotBeTakenOrPutBackSaysSo(DnsVerbHarness):
    """Preserving a file is only preservation if it is checked.

    A second read of the previous correction measured what its own delivery
    had named as untested residue, and it is worse than untested. The two
    expressions that carry the prior file's mode were written
    `chmod --reference=... 2>/dev/null || true`, so when the reference lookup
    fails the helper says nothing and carries on:

      * failing while SAVING the mode leaves the copy carrying the mode the
        temporary file was created with, and the rollback then puts THAT on
        the drop-in — a 0640 file comes back 0600;
      * failing while RESTORING it leaves the drop-in with the 0644 this
        helper wrote a moment earlier — a 0600 file, owner-only, comes back
        readable by everyone.

    In both cases the helper prints that the machine has been left as it was,
    and removes the copy although the restoration was never verified. A third
    boundary sits beside them: when the read that takes the copy fails, the
    helper stops before touching anything — which is right — but leaves the
    half-written copy behind, which contradicts what it says about that
    copy's lifetime.

    These cases make each named call fail the way the real program fails, and
    then read the file's bytes and mode off the disk.
    """

    PRIOR_BYTES = b"[Resolve]\nDNS=192.0.2.1\n\n"

    def write_prior_dropin(self, mode):
        path = self.dropin
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.PRIOR_BYTES)
        path.chmod(mode)
        return path

    def strays(self):
        return sorted(p.name for p in self.dropin.parent.iterdir()
                      if p.name != self.dropin.name)

    def mode_of(self, path):
        import stat as _stat
        return _stat.S_IMODE(path.stat().st_mode)

    # -- saving the mode ---------------------------------------------------

    def test_a_snapshot_whose_mode_could_not_be_saved_stops_before_writing(self):
        path = self.write_prior_dropin(0o640)
        before = path.read_bytes()
        result = self.run_verb("dns-use-cloudflare", chmod_fails="copy")
        self.assertNotEqual(result.returncode, 0,
                            "the choice reported success although the machine "
                            "could not be remembered:\n" + result.stdout)
        self.assertEqual(self.mode_of(path), 0o640,
                         "the prior drop-in's mode changed although the "
                         "helper could not record what it was:\n"
                         + result.stderr)
        self.assertEqual(path.read_bytes(), before,
                         "the prior drop-in was rewritten although the helper "
                         "could not record it:\n" + result.stderr)

    def test_it_says_the_snapshot_failed_rather_than_nothing(self):
        self.write_prior_dropin(0o640)
        result = self.run_verb("dns-use-cloudflare", chmod_fails="copy")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("could not", result.stderr.lower(), result.stderr)

    def test_the_incomplete_copy_is_not_left_behind(self):
        self.write_prior_dropin(0o640)
        result = self.run_verb("dns-use-cloudflare", chmod_fails="copy")
        self.assertEqual(self.strays(), [],
                         "an incomplete copy of the drop-in was left beside "
                         "it:\n" + result.stderr)

    # -- restoring the mode ------------------------------------------------

    def test_a_restoration_that_failed_is_not_reported_as_success(self):
        path = self.write_prior_dropin(0o600)
        result = self.run_verb("dns-use-cloudflare",
                               resolver_restart_fails=True,
                               chmod_fails="restore")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(
            "left as it was", result.stderr,
            "the helper said the machine was left as it was although it "
            "could not put the prior mode back:\n" + result.stderr)

    def test_a_restoration_that_failed_keeps_a_recovery_copy(self):
        self.write_prior_dropin(0o600)
        result = self.run_verb("dns-use-cloudflare",
                               resolver_restart_fails=True,
                               chmod_fails="restore")
        self.assertNotEqual(result.returncode, 0)
        kept = self.strays()
        self.assertEqual(
            len(kept), 1,
            "the copy of the prior drop-in was removed although the "
            "restoration was never verified, so nothing holds the original "
            "any more:\n" + result.stderr)

    def test_the_recovery_copy_is_named_where_a_person_can_read_it(self):
        self.write_prior_dropin(0o600)
        result = self.run_verb("dns-use-cloudflare",
                               resolver_restart_fails=True,
                               chmod_fails="restore")
        kept = self.strays()
        self.assertTrue(kept, result.stderr)
        self.assertIn(kept[0], result.stderr,
                      "the kept copy is not named in anything the person "
                      "running this is told:\n" + result.stderr)

    def test_the_recovery_copy_holds_the_original_bytes_and_mode(self):
        self.write_prior_dropin(0o600)
        result = self.run_verb("dns-use-cloudflare",
                               resolver_restart_fails=True,
                               chmod_fails="restore")
        kept = self.strays()
        self.assertTrue(kept, result.stderr)
        copy = self.dropin.parent / kept[0]
        self.assertEqual(copy.read_bytes(), self.PRIOR_BYTES,
                         "the kept copy does not hold the original bytes")
        self.assertEqual(self.mode_of(copy), 0o600,
                         "the kept copy does not carry the original mode, so "
                         "it is not a usable recovery copy")

    # -- reading the file into the copy ------------------------------------

    def test_a_backup_read_that_failed_leaves_nothing_behind(self):
        path = self.write_prior_dropin(0o600)
        before = path.read_bytes()
        result = self.run_verb("dns-use-cloudflare", backup_read_fails=True)
        self.assertNotEqual(result.returncode, 0,
                            "the choice reported success although the machine "
                            "could not be remembered:\n" + result.stdout)
        self.assertEqual(self.strays(), [],
                         "a half-written copy of the drop-in was left beside "
                         "it:\n" + result.stderr)
        self.assertEqual(path.read_bytes(), before,
                         "the prior drop-in was rewritten although the helper "
                         "could not read it first")
        self.assertEqual(self.mode_of(path), 0o600)

    # -- the non-masking controls -----------------------------------------

    def test_with_no_call_made_to_fail_the_ordinary_rollback_is_unchanged(self):
        path = self.write_prior_dropin(0o600)
        before = path.read_bytes()
        result = self.run_verb("dns-use-cloudflare",
                               resolver_restart_fails=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.mode_of(path), 0o600)
        self.assertIn("left as it was", result.stderr, result.stderr)
        self.assertEqual(self.strays(), [],
                         "a verified rollback kept a copy it did not need")

    def test_with_no_call_made_to_fail_the_choice_still_takes(self):
        self.write_prior_dropin(0o600)
        result = self.run_verb("dns-use-cloudflare")
        self.assertEqual(result.returncode, 0,
                         result.stdout + result.stderr)
        self.assertEqual(self.strays(), [],
                         "a successful choice left a copy behind")


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


class TestAResolverThatWillNotRestartLeavesNothingBehind(DnsVerbHarness):
    """A choice whose resolver restart fails is a choice that was not applied.

    The verb writes the drop-in and then restarts the resolver, because the
    resolver has no reload path and reads its configuration only at start. If
    that restart fails, the servers in the drop-in are not the ones answering
    and nothing else the choice needs has been done yet — the half-configured
    state the ordinary failure path already refuses to leave. The restart sat
    OUTSIDE that failure handling, so the script ended on it and the drop-in
    stayed on disk with no record and no dispatcher.

    The machine this runs against answers `systemctl restart` with a failure
    on demand, through the service-manager stand-in the harness already
    writes, so these cases exercise the REAL helper's real failure path.
    """

    def test_a_failed_restart_removes_the_dropin_it_just_wrote(self):
        result = self.run_verb("dns-use-cloudflare", resolver_restart_fails=True)
        self.assertNotEqual(result.returncode, 0,
                            "the verb reported success although the resolver "
                            "never restarted:\n" + result.stdout)
        self.assertFalse(self.dropin.exists(),
                         "the drop-in was left on disk naming servers that "
                         "nothing was told to use")
        self.assertFalse(self.dispatcher.exists(),
                         "a choice that was not applied left its dispatcher")
        self.assertFalse(self.record.exists(),
                         "a choice that was not applied left a record of "
                         "connections it never changed")

    def test_a_failed_restart_says_the_machine_was_left_as_it_was(self):
        result = self.run_verb("dns-use-cloudflare", resolver_restart_fails=True)
        self.assertIn("left as it was", result.stderr,
                      "nothing told the caller the machine is unchanged:\n"
                      + result.stderr)

    def test_a_failed_restart_puts_an_earlier_choice_back(self):
        self.assertEqual(self.run_verb("dns-use-cloudflare").returncode, 0)
        before = self.dropin.read_text(encoding="utf-8")
        result = self.run_verb("dns-use-quad9", resolver_restart_fails=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.dropin.exists(),
                        "a failed restart removed the choice that was standing")
        self.assertEqual(self.dropin.read_text(encoding="utf-8"), before,
                         "a failed restart left the earlier choice rewritten")

    def test_the_connections_are_not_left_ignoring_their_own_servers(self):
        result = self.run_verb("dns-use-cloudflare", resolver_restart_fails=True)
        self.assertNotEqual(result.returncode, 0)
        for uuid in self.uuids():
            self.assertNotEqual(
                self.profile_property(uuid, "ipv4.ignore-auto-dns"), "yes",
                f"{uuid} was left ignoring the servers its network hands out "
                "by a choice that was never applied")


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

    def test_a_repair_with_nothing_to_do_does_not_announce_re_applying(self):
        """The line is printed into the upgrade's own output. A machine that
        needed nothing should not be told work was done on it."""
        self.assertEqual(self.run_verb("dns-use-cloudflare").returncode, 0)
        result = self.run_verb("dns-reapply-selection")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("already has", result.stdout,
                      "the repair did not say the machine was already as its "
                      "choice says:\n" + result.stdout)
        self.assertNotIn("re-applying", (result.stdout + result.stderr).lower(),
                         "the repair announced re-applying the choice and "
                         "then re-applied nothing:\n" + result.stdout
                         + result.stderr)

    def test_a_repair_that_does_the_work_still_announces_it(self):
        """The quieting must not reach the case the line exists for.

        On STDERR: the package manager's hook runner surfaces a hook's stderr
        as its NOTE lines and discards its stdout, so a line printed to stdout
        here is a line no upgrade ever shows. This repair rewrites the resolver
        configuration and restarts the resolver; it may not do that silently.
        """
        self.assertEqual(self.run_verb("dns-use-cloudflare").returncode, 0)
        self.dispatcher.unlink()
        result = self.run_verb("dns-reapply-selection")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("re-applying", result.stderr.lower(),
                      "a repair that did the work said nothing an upgrade "
                      "would show:\n" + result.stdout + result.stderr)
        self.assertNotIn("re-applying", result.stdout.lower(),
                         "the announcement is on stdout, which the hook "
                         "runner discards")

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
