# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Command-separator / construct superset + block-device + exec-prefix class.

Independent-security-review fold-in (2026-07-01). One root-cause class: the
classifier modeled fewer command separators/constructs than the shell honors, so
a benign leading command could hide a destructive trailing one. Closed here:

- separator SUPERSET: `|` `||` `&&` `;` `&`(background) `|&` newline + subshell
  grouping `()`/`{}` — each segment classified independently, worst wins.
- pipe scoping is PER-SEGMENT: a pager is a non-interactive data-sink (AUTO) only
  when adjacent to a REAL pipe; `||` is a logical-or, so `foo || less` -> CONFIRM.
- the device-mapper named volumes (`/dev/mapper/cryptroot` = the unlocked LUKS
  root, `/dev/mapper/igos-root` = the verity root) join the block-device family:
  a redirect there is a redirect onto the disk -> BLOCKED.
- transparent exec-prefix wrappers (nohup/nice/ionice/timeout/stdbuf/setsid/time/
  command) are classified by their WRAPPED command, so a base-command-keyed block
  cannot soften one notch to CONFIRM behind the wrapper.

(Sibling to test_safety_interactive_escape / test_safety_write_form.)
"""

from __future__ import annotations

import unittest

from intergen.safety import classify_command, is_destructive_execution
from intergen.interfaces.types import SafetyTier


class SeparatorSupersetHidesNothing(unittest.TestCase):
    def test_trailing_command_behind_a_separator_is_classified(self):
        # A benign leading command must not launder a dangerous trailing one via a
        # separator the classifier previously did not split on.
        cases = {
            "ls & systemctl restart nginx": SafetyTier.CONFIRM,   # background &
            "echo a\nsystemctl restart nginx": SafetyTier.CONFIRM,  # newline
            "echo hi ; mount /dev/sdb /mnt": SafetyTier.BLOCKED,  # semicolon + blocked
            "cat f | nft flush ruleset": SafetyTier.BLOCKED,      # pipe + blocked
            "true && apt install x": SafetyTier.BLOCKED,          # and-list + wrong pkgmgr
            "ls |& rm -rf /x": SafetyTier.BLOCKED,                # pipe-both + rm -rf
            "(rm -rf /x)": SafetyTier.BLOCKED,                    # subshell group
            "{ mount /dev/sda /mnt ; }": SafetyTier.BLOCKED,      # brace group
            "ls 5& systemctl restart x": SafetyTier.CONFIRM,     # background & with NO space
            "sleep 1& mount /dev/sdb /mnt": SafetyTier.BLOCKED,  # no-space & hides blocked
        }
        for cmd, tier in cases.items():
            self.assertEqual(classify_command(cmd), tier, repr(cmd))

    def test_benign_compound_stays_auto(self):
        for cmd in ("ls & echo done", "pwd && ls", "pwd ; whoami",
                    "df -h | grep sda"):
            self.assertEqual(classify_command(cmd), SafetyTier.AUTO, cmd)


class PipeScopingPerSegment(unittest.TestCase):
    # A pager is a non-interactive data-sink (AUTO) only when adjacent to a REAL
    # pipe; a logical-or / and-list leaves it a bare interactive pager -> CONFIRM.
    def test_logical_operators_do_not_mark_a_pager_piped(self):
        for cmd in ("foo || less", "missing && top", "a || htop", "x && more f"):
            self.assertEqual(classify_command(cmd), SafetyTier.CONFIRM, cmd)

    def test_real_pipe_makes_pager_a_data_sink(self):
        for cmd in ("du -sh /var | less", "ps aux | less", "journalctl | head",
                    "cat f |& less"):
            self.assertEqual(classify_command(cmd), SafetyTier.AUTO, cmd)


class BlockDeviceMapperRedirect(unittest.TestCase):
    # The unlocked-root device-mapper names are block devices: a redirect there is
    # a redirect onto the disk (the plaintext view of the encrypted root).
    def test_mapper_redirect_is_blocked(self):
        for cmd in ("echo x > /dev/mapper/cryptroot",
                    "cat f >> /dev/mapper/igos-root",
                    "dd if=/dev/zero of=/dev/mapper/cryptroot"):
            self.assertEqual(classify_command(cmd), SafetyTier.BLOCKED, cmd)

    def test_router_pre_gate_shares_the_mapper_block(self):
        # _BLOCK_DEVICE is reused by the router's natural-language decline gate, so
        # the coverage fix closes both layers at once.
        self.assertTrue(
            is_destructive_execution("please run echo x > /dev/mapper/cryptroot"))

    def test_char_device_redirect_still_fine(self):
        for cmd in ("echo hi > /dev/null", "cat f > /dev/stdout"):
            self.assertEqual(classify_command(cmd), SafetyTier.AUTO, cmd)


class TransparentExecPrefixWrappers(unittest.TestCase):
    # An unrecognized prefix wrapper must not soften a base-command-keyed block by
    # one notch: classify by the WRAPPED command.
    def test_wrapped_blocked_command_stays_blocked(self):
        cases = (
            "nohup sudo rm x",
            "timeout 5 mount /dev/sdb /mnt",
            "nice ip addr",                       # netlink family -> BLOCKED
            "timeout -s KILL 5 nft flush",
            "nice -n 10 apt install x",           # wrong package manager
            "ionice -c2 -n0 dd of=/dev/sda",
            "setsid reboot",
            "command mount /dev/sda /mnt",
        )
        for cmd in cases:
            self.assertEqual(classify_command(cmd), SafetyTier.BLOCKED, cmd)

    def test_wrapped_read_stays_auto(self):
        for cmd in ("timeout 5 ls", "nice cat f", "nohup free", "command ls",
                    "stdbuf -oL grep x f", "time df -h"):
            self.assertEqual(classify_command(cmd), SafetyTier.AUTO, cmd)

    def test_wrapped_write_confirms(self):
        for cmd in ("nohup cp a b", "timeout 5 rm f"):
            self.assertEqual(classify_command(cmd), SafetyTier.CONFIRM, cmd)


class PrivilegeEscalationHelpers(unittest.TestCase):
    # pkexec / runuser are root-gated exec helpers that run an arbitrary inner as
    # another user -- peers of sudo/su -> BLOCKED as base commands, never softened.
    def test_priv_esc_helpers_blocked(self):
        for cmd in ("pkexec rm x", "pkexec ls", "runuser -u bob rm x",
                    "runuser -c 'mount /x' bob"):
            self.assertEqual(classify_command(cmd), SafetyTier.BLOCKED, cmd)


class ShellCommandStringRecursion(unittest.TestCase):
    # A shell (or `script`) invoked with a -c command-string re-enters below the
    # classifier: recurse INTO the string so the inner reaches its true tier and the
    # separator model applies inside the quoted argument.
    def test_shell_c_inner_reaches_true_tier(self):
        cases = {
            "bash -c 'mount /dev/sda /mnt'": SafetyTier.BLOCKED,
            "sh -c 'ip addr'": SafetyTier.BLOCKED,          # netlink
            "bash -lc 'apt install x'": SafetyTier.BLOCKED,  # combined flag + wrong pkgmgr
            "dash -c 'sudo rm'": SafetyTier.BLOCKED,
            "bash -c 'ip addr ; mount x'": SafetyTier.BLOCKED,  # separator inside -c
            "script -c 'mount x' out.log": SafetyTier.BLOCKED,
            "zsh -c 'df -h'": SafetyTier.AUTO,
            "bash -c 'echo hi' argv0 argv1": SafetyTier.AUTO,
        }
        for cmd, tier in cases.items():
            self.assertEqual(classify_command(cmd), tier, repr(cmd))

    def test_bare_shell_or_script_file_confirms(self):
        # No inline -c command to inspect -> the unknown-command CONFIRM default.
        for cmd in ("bash", "sh", "bash script.sh"):
            self.assertEqual(classify_command(cmd), SafetyTier.CONFIRM, cmd)

    def test_value_option_before_c_does_not_soften(self):
        # F1' fix (2026-07-02, independent-red-team finding): a value-taking shell
        # option with a SEPARATE-token value before -c must not be misread as a
        # script file — the -c payload's true tier must still win.
        blocked = (
            "bash -o emacs -c 'mount /x'",
            "bash -O extglob -c 'nft flush ruleset'",
            "bash --rcfile /tmp/rc -c 'mount /x'",
            "bash --init-file /tmp/i -c 'ip addr'",
            "sh +o posix -c 'umount /y'",
            "bash --rcfile=/tmp/rc -c 'mount /x'",
        )
        for cmd in blocked:
            self.assertEqual(classify_command(cmd), SafetyTier.BLOCKED, cmd)
        for cmd in ("bash -o vi -c 'ls -la'", "bash --rcfile /tmp/rc -c 'free -h'"):
            self.assertEqual(classify_command(cmd), SafetyTier.AUTO, cmd)


class ExecPrefixWrapperLongTail(unittest.TestCase):
    # The out-of-set exec-prefix wrappers (each RUNS its inner) are transparent, so a
    # base-command-keyed block reaches its true tier instead of softening to CONFIRM.
    def test_wrapped_blocked_inner_stays_blocked(self):
        cases = (
            "flock /tmp/lock mount /x",
            "flock -w 5 /tmp/l pkexec sh",
            "taskset 0x1 mount /x",
            "taskset -c 0-3 nft flush",
            "chrt 50 mount /x",
            "chrt mount /x",                    # malformed: mount in the prio slot, still blocked
            "strace -o out ip addr",
            "valgrind --tool=memcheck mount /x",
            "xargs -n 1 mount",                 # value flag consumes the count
            "xargs -I{} mount {}",              # replacement-string: attached brace form
            "xargs -I {} mount /dev/sda /mnt",  # replacement-string: separate-token form
            "xargs -i mount {}",               # deprecated -i alias
            "xargs --replace mount",           # long form, no-arg (optional value)
            "xargs --replace={} mount",        # long form, inline value
            "xargs -I mount",                  # malformed -I: command in the value slot
            "nice -n mount",                   # malformed -n: command in the value slot
            "proot -r /root ip addr",
            "busybox mount /x",
            "catchsegv mount /x",              # installed-artifact verify observation
            "catchsegv 'nft flush ruleset'",   # quoted inner via the shared dequote
        )
        for cmd in cases:
            self.assertEqual(classify_command(cmd), SafetyTier.BLOCKED, cmd)

    def test_wrapped_read_stays_auto(self):
        for cmd in ("flock /tmp/lock ls", "taskset 0x1 df -h", "firejail cat f",
                    "strace ls", "busybox ls", "catchsegv df -h"):
            self.assertEqual(classify_command(cmd), SafetyTier.AUTO, cmd)


class CommandSubstitutionStaysBlocked(unittest.TestCase):
    def test_substitution_forms_blocked(self):
        for cmd in ("echo $(rm -rf /x)", "echo `id`", "x=$(whoami) echo $x"):
            self.assertEqual(classify_command(cmd), SafetyTier.BLOCKED, cmd)


class CommonRedirectsNotMisSplit(unittest.TestCase):
    # The background-& separator must exclude fd-redirect forms so a common
    # `2>&1` / `>&` does not over-split into a spurious CONFIRM segment.
    def test_fd_redirects_stay_auto(self):
        for cmd in ("grep -r foo . 2>&1", "ls -la 2>&1 | grep txt",
                    "cat f >& /dev/null", "df -h 2>&1 | head", "echo hi 1>&2"):
            self.assertEqual(classify_command(cmd), SafetyTier.AUTO, cmd)


class WrapperQuotedInnerNotSoftened(unittest.TestCase):
    """F2 correctness fix (2026-07-02): a QUOTED wrapped command must classify by
    its true tier — the recognition-tier inner must not soften to CONFIRM because
    a leading quote is glued to the token. Inners are NON-pattern-listed (mount /
    nft / ip) on purpose: a pattern-listed inner (`rm -rf`) would be caught by the
    anywhere-match layer regardless and mask the recognition-layer gap (WC's own
    methodology note). Every exec-wrapper re-entry point is exercised."""

    def test_quoted_blocked_inner_reaches_blocked(self):
        for cmd in (
            "watch -n 5 'mount /dev/sda1 /mnt'",
            'watch "umount /y"',
            "nohup 'mount /x'",
            "env 'nft flush ruleset'",
            "nice 'ip addr'",
            "timeout 5 'mount /x'",
            "setsid 'nft -f /tmp/r'",
            "flock /tmp/l 'mount /x'",
        ):
            self.assertEqual(classify_command(cmd), SafetyTier.BLOCKED, cmd)

    def test_quoted_read_inner_stays_auto(self):
        # The over-approximation must not over-harden a benign quoted inner.
        for cmd in ("watch 'free -h'", "nohup 'ls -la'", "env 'grep x f'",
                    "timeout 5 'df -h'"):
            self.assertEqual(classify_command(cmd), SafetyTier.AUTO, cmd)


class RmRecursiveForceRegexTokenAnchored(unittest.TestCase):
    """F4 correctness fix (2026-07-02): the recursive-force rm block must fire on
    real flag tokens, never on a hyphenated FILENAME. Pre-fix, `rm my-report-final.txt`
    read `-report`/`-final` as flags and was HARD-blocked (a benign single-file
    delete). Both the classify_command copy and the router NL-gate copy are covered."""

    def test_hyphenated_filenames_are_not_blocked(self):
        for cmd in ("rm my-report-final.txt", "rm the-red-folder.tar",
                    "rm -red-fox.txt", "rm notes.txt", "rm a-really-fine-name"):
            self.assertEqual(classify_command(cmd), SafetyTier.CONFIRM, cmd)

    def test_true_recursive_force_still_blocked(self):
        for cmd in ("rm -rf /tmp/x", "rm -r -f x", "rm x -rf", "rm -Rf d",
                    "rm -fr d", "rm --recursive --force d", "rm -rf /"):
            self.assertEqual(classify_command(cmd), SafetyTier.BLOCKED, cmd)

    def test_single_recursive_or_force_stays_confirm(self):
        for cmd in ("rm -r dir", "rm -f file", "rm -i file"):
            self.assertEqual(classify_command(cmd), SafetyTier.CONFIRM, cmd)

    def test_nl_gate_matches_command_gate(self):
        from intergen.safety import is_destructive_execution
        self.assertFalse(is_destructive_execution("rm my-report-final.txt"))
        self.assertTrue(is_destructive_execution("rm -rf /home/x"))


if __name__ == "__main__":
    unittest.main()
