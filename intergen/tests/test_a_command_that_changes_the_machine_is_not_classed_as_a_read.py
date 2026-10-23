# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A command that changes the machine is not classed as a read.

THE DEFECT. The classifier's AUTO tables key on a command's first word, or its
first two, and AUTO executes under every provenance. timedatectl and hostnamectl
are AUTO because their plain forms print, so `timedatectl set-ntp true` and
`hostnamectl set-hostname x` were AUTO as well; so were `hostname x`, `date -s`
and a bare date operand, `dmesg -C` and the console-level forms, the `git remote`
subcommands that rewrite the remotes, and dpkg and rpm, whose install and erase
forms change the system. A command the model composed after reading a page ran
them with no word from anyone.

THE RULE PINNED HERE. Those forms are CONFIRM, so they are held for the person's
review under every provenance (run_command's CONFIRM is the privileged tier),
while the read forms stay AUTO, the code's own fixed reads among them (the time
and date answers' formats, the host name, the state map's hostnamectl).
"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from intergen.interfaces.provenance import (
    ConversationTrustState,
    IngressTracker,
    Provenance,
)
from intergen.interfaces.types import ToolCall, ToolResult
from intergen.safety import classify_command
from intergen.tool_registry import ToolRegistry

CHANGES = [
    "timedatectl set-ntp true", "timedatectl set-time 10:00", "timedatectl set-timezone UTC",
    "timedatectl set-local-rtc 1", "timedatectl ntp-servers eth0 time.example",
    "timedatectl revert eth0", "hostnamectl set-hostname x", "hostnamectl hostname x",
    "hostnamectl icon-name computer", "hostnamectl chassis laptop", "hostname x",
    "hostname -F /tmp/name", "hostname -b x", "date -s 10:00", "date --set=10:00",
    "date -us 10:00", "date 10221200", "dmesg -C", "dmesg -c", "dmesg -n 1",
    "dmesg --console-level=1", "dmesg -D", "dmesg -E", "dmesg --console-off",
    "git remote rename origin old", "git remote remove origin", "git remote rm origin",
    "git remote set-branches origin main", "rpm -ivh x.rpm", "rpm -qa", "dpkg -i x.deb",
    "dpkg -l",
    # every git form asks first (git runs the programs its configuration names): CONFIRM, listed here for the verdict, not as changes
    "git remote", "git remote -v", "git remote get-url origin",
]

READS = [
    "timedatectl", "timedatectl status", "timedatectl show", "timedatectl show -p NTP",
    "timedatectl list-timezones", "timedatectl timesync-status", "timedatectl show-timesync",
    "hostnamectl", "hostnamectl status", "hostnamectl hostname", "hostnamectl chassis",
    "hostname", "hostname -I", "hostname -f", "hostname -s", "date", "date -u",
    "date -d yesterday", "date -R", "date --iso-8601", "dmesg", "dmesg -T",
    "dmesg --level=err,warn", "dmesg -lcrit", "dmesg -H",
]

#: The code's own fixed reads (router.py's direct answers, the read-only state
#: map): they must stay AUTO, or the answers they feed would raise a card.
CODE_OWNED_READS = ["date '+%-I:%M %p %Z'", "date '+%A, %B %-d, %Y'", "hostname",
                    "hostnamectl", "date"]


class AChangeIsNotARead(unittest.TestCase):

    def test_each_change_is_confirm(self):
        for command in CHANGES:
            with self.subTest(command=command):
                self.assertEqual(classify_command(command).value, "confirm")

    def test_each_read_stays_auto(self):
        for command in READS + CODE_OWNED_READS:
            with self.subTest(command=command):
                self.assertEqual(classify_command(command).value, "auto")

    def test_a_line_that_does_not_split_is_a_change(self):
        self.assertEqual(classify_command("date '+%H").value, "confirm")


#: The six cells: (ingress on the conversation, declared provenance).
CELLS = [(False, Provenance.USER_DIRECT), (False, Provenance.USER_IMPLIED),
         (False, Provenance.INGRESS_DERIVED), (True, Provenance.USER_DIRECT),
         (True, Provenance.USER_IMPLIED), (True, Provenance.INGRESS_DERIVED)]


class TheChokepointHoldsAChangeAndRunsARead(unittest.TestCase):

    def setUp(self) -> None:
        state = tempfile.TemporaryDirectory()
        self.addCleanup(state.cleanup)
        env = mock.patch.dict(os.environ, {"XDG_STATE_HOME": state.name})
        env.start()
        self.addCleanup(env.stop)

    def cells(self, command: str) -> str:
        out = []
        for ingress, declared in CELLS:
            ran, asked = [], []
            registry = ToolRegistry()
            registry.discover_tools()

            def record(arguments):
                ran.append(arguments)
                return ToolResult(call_id="c", name="run_command", content="(recorded)",
                                  success=True)
            registry._tools["run_command"].execute = record
            # A held privileged call reaches the review first; the review denies,
            # so nothing is dispatched and no token is minted.
            tracker = IngressTracker()
            if ingress:
                tracker.record_tool_call("web_search")
            registry.execute(
                ToolCall(name="run_command", arguments={"command": command}, call_id="c",
                         source_of_request=declared),
                ingress_tracker=tracker, trust_state=ConversationTrustState(),
                review_callback=lambda call, decision: asked.append(call) or "deny")
            out.append("E" if ran else ("H" if asked else "R"))
        return "".join(out)

    def test_the_ntp_switch_and_the_host_name_are_held_in_every_cell(self):
        for command in ("timedatectl set-ntp true", "hostnamectl set-hostname x"):
            with self.subTest(command=command):
                self.assertEqual(self.cells(command), "HHHHHH")

    def test_their_read_forms_run_in_every_cell(self):
        for command in ("timedatectl status", "timedatectl show", "hostnamectl status"):
            with self.subTest(command=command):
                self.assertEqual(self.cells(command), "EEEEEE")


if __name__ == "__main__":
    unittest.main()
