# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The command table builds only commands its executor runs.

THE DEFECT, read on the tree in intergen/router.py. The keyword route's
command table maps a phrase to the command that answers it, and run_command
runs ONE argv with no shell: it refuses a pipe, a redirection, "&&" and the
rest before anything runs. Thirty-five of the table's 103 entries, in six
commands, were written as shell lines: "lscpu | head -20", "ps … | head -8",
"lspci | grep -i vga", a du listing sorted and cut by a pipeline, and two
summaries joined by "&&". So "what cpu do i have" and "what's taking up all
my disk space" were answered with the executor's refusal, and the other
phrases made a dispatch that could never run before the model answered.

THE RULE these cells hold the table to: every command it builds carries none
of the characters the executor refuses. Where the pipeline only trimmed the
output, the command is the one argv and the template reads the whole output:
the CPU template reads the model and count lines of lscpu, the GPU template
takes the line naming VGA out of lspci, and the process reader keeps the first
eight rows of the process table. A phrase whose answer needs a pipeline (the
du listing, the two summaries) builds no command, so its question goes on to
the model's turn, and it stays in the table so the generic df keys cannot
claim it.

THE CONTROLS beside the cells are green at both ends: the df and free keys
resolve as before, the CPU template reads a whole lscpu output, and the
executor still refuses a pipeline.

The selector's on-PATH gate is told every binary is present. Where a turn is
routed, the registry's own validation and run_command's own execute run, and
`subprocess.run` is stood in: it answers lscpu with a fixed output, records
every command's own argv and runs nothing; the model is stubbed at both entry
points and counted, and the dispatch record's writer is recorded. The command
starts as its own service behind the credential boundary
(intergen/tool_process.py), so what the stand-in is handed is the launcher's
list with the command's own argv after its "--", and the boundary is given fake
credential paths so nothing here depends on this machine's.
"""
from __future__ import annotations

import os
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from intergen import howto
from intergen import router as router_module
from intergen.interfaces.types import HardwareTierLevel
from intergen.llm import LLMRouter
from intergen.router import ConversationRouter as R
from intergen.semantic import SemanticMatcher
from intergen.tool_registry import ToolRegistry
from intergen.tools.run_command import _SHELL_CONSTRUCT_CHARS, RunCommandTool

MODEL_ANSWER = "(a model's answer)"
LSCPU = "\n".join((
    "Architecture:                            x86_64",
    "CPU op-mode(s):                          32-bit, 64-bit",
    "Address sizes:                           39 bits physical, 48 bits virtual",
    "Byte Order:                              Little Endian",
    "CPU(s):                                  8",
    "On-line CPU(s) list:                     0-7",
    "Vendor ID:                               GenuineIntel",
    "Model name:                              11th Gen Intel(R) Core(TM) i7-1165G7 @ 2.80GHz",
    "CPU family:                              6",
    "Model:                                   140",
    "Thread(s) per core:                      2",
    "Core(s) per socket:                      4",
    "Socket(s):                               1",
    "Stepping:                                1",
    "CPU(s) scaling MHz:                      40%",
    "CPU max MHz:                             4700.0000",
    "CPU min MHz:                             400.0000",
    "BogoMIPS:                                5608.00",
    "Flags:                                   fpu vme de pse tsc msr pae mce",
    "Virtualization:                          VT-x",
    "L1d cache:                               192 KiB (4 instances)",
    "L2 cache:                                5 MiB (4 instances)",
    "NUMA node(s):                            1",
    "NUMA node0 CPU(s):                       0-7",
    "Vulnerability Meltdown:                  Not affected",
))
CPU_ANSWER = "CPU: 11th Gen Intel Core i7-1165G7 — 8 cores."
LSPCI = "\n".join((
    "00:00.0 Host bridge: Intel Corporation 11th Gen Core Processor Host Bridge/DRAM Registers (rev 01)",
    "00:02.0 VGA compatible controller: Intel Corporation TigerLake-LP GT2 [Iris Xe Graphics] (rev 01)",
    "00:04.0 Signal processing controller: Intel Corporation TigerLake-LP Dynamic Tuning (rev 01)",
    "00:14.0 USB controller: Intel Corporation Tiger Lake-LP USB 3.2 Gen 2x1 xHCI Host Controller (rev 20)",
    "00:1f.3 Audio device: Intel Corporation Tiger Lake-LP Smart Sound Technology Audio Controller (rev 20)",
))
GPU_ANSWER = "GPU: Intel Iris Xe Graphics."
PROCESSES = ["firefox", "Web Content", "gnome-shell", "pipewire", "Xwayland", "code",
             "kworker/u16:2-events_unbound", "python3", "systemd", "sshd", "bash", "top"]
PS_TABLE = "\n".join("%5.1f %4.1f %s" % (20.0 - i, 1.5, name)
                     for i, name in enumerate(PROCESSES))

# The class, read off the table at the commit before this change: every phrase
# whose command carried a refused character, by command.
CPU_MODEL = ("cpu", "cpu info")
CPU_LOAD = ("cpu usage", "using my cpu", "using all my cpu", "cpu so high", "cpu is high",
            "cpu too high")
GPU = ("gpu", "graphics card", "graphics")
WHERE_THE_SPACE_WENT = (
    "taking up all my disk", "taking up my disk", "taking up all my space",
    "taking up all the space", "taking up my space", "taking up all my storage",
    "taking up space", "taking up storage", "using all my disk", "using all the disk",
    "using all my space", "using all the space", "using all my storage", "largest files",
    "biggest files", "big files", "eating my disk", "eating up my disk", "using my disk",
    "hogging my disk", "hogging the disk")
SYSTEM_INFO = ("system info",)
SYSTEM_STATUS = ("system status", "system health")
THE_CLASS = CPU_MODEL + CPU_LOAD + GPU + WHERE_THE_SPACE_WENT + SYSTEM_INFO + SYSTEM_STATUS


def _which_all_present(name, *a, **kw):
    return "/usr/bin/" + name


class _Selector(unittest.TestCase):

    def setUp(self) -> None:
        router_module._readonly_state_requires.cache_clear()
        patcher = mock.patch.object(router_module.shutil, "which", _which_all_present)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(router_module._readonly_state_requires.cache_clear)

    def assertBuilds(self, phrases, command) -> None:
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                self.assertEqual(R._natural_language_to_command("what is " + phrase), command)


# ── the cells: red before the correction ────────────────────────────────────

class EveryCommandTheClassBuildsIsOneArgv(_Selector):

    def test_no_phrase_of_the_class_builds_a_refused_character(self) -> None:
        self.assertEqual(len(THE_CLASS), 35)
        for phrase in THE_CLASS:
            with self.subTest(phrase=phrase):
                command = R._natural_language_to_command("what is " + phrase)
                self.assertFalse(set(command or "") & _SHELL_CONSTRUCT_CHARS, command)

    def test_the_cpu_model_is_lscpu(self) -> None:
        self.assertBuilds(CPU_MODEL, "lscpu")

    def test_the_cpu_load_is_the_process_table(self) -> None:
        self.assertBuilds(CPU_LOAD, "ps -eo pcpu,pmem,comm --sort=-pcpu --no-headers")

    def test_the_gpu_is_lspci(self) -> None:
        self.assertBuilds(GPU, "lspci")

    def test_where_the_space_went_builds_no_command(self) -> None:
        self.assertBuilds(WHERE_THE_SPACE_WENT, None)

    def test_the_system_summary_builds_no_command(self) -> None:
        self.assertBuilds(SYSTEM_INFO, None)

    def test_the_system_status_builds_no_command(self) -> None:
        self.assertBuilds(SYSTEM_STATUS, None)


class TheTemplatesReadTheWholeOutput(unittest.TestCase):

    def test_the_gpu_template_takes_the_display_adapters_line(self) -> None:
        self.assertEqual(R._template_synthesis("what gpu do i have", LSPCI, "run_command"),
                         GPU_ANSWER)

    def test_the_process_reader_keeps_the_first_eight_rows(self) -> None:
        answer = R._template_synthesis("what's using my cpu", PS_TABLE, "run_command")
        self.assertIsNotNone(answer)
        self.assertTrue(answer.startswith("Top processes by CPU: firefox (20% CPU, 1.5% memory), "
                                          "Web Content (19% CPU"), answer)
        for name in PROCESSES[:8]:
            self.assertIn(name + " (", answer)
        for name in PROCESSES[8:]:
            self.assertNotIn(name + " (", answer)


class _Turn(_Selector):
    """A turn routed with the registry's own validation and run_command's own execute."""

    def _route(self, question: str):
        from intergen.intents import register_all_intents
        reg = ToolRegistry()
        reg.discover_tools()
        matcher = SemanticMatcher(embedder=None)
        register_all_intents(matcher)
        with mock.patch.dict(os.environ, {"INTERGEN_HOWTO_DIR": str(howto._REPO_DIR)}):
            r = R(tool_registry=reg, semantic_matcher=matcher, llm=LLMRouter(config=None),
                  lock_dispatch=False, hardware_tier=HardwareTierLevel.TIER_2)
        self.asked: list[str] = []

        def _stream_with_tools(messages, *, tools=(), **kw):
            self.asked.append("stream_with_tools")
            yield MODEL_ANSWER

        def _chat(messages, **kw):
            self.asked.append("chat")
            raise AssertionError("the synthesis model was asked")

        r._llm.chat = _chat
        r._llm.stream_with_tools = _stream_with_tools
        self.ran: list[list[str]] = []
        self.processes: list[str] = []
        self.connections: list[str] = []

        def _run(args, *a, **kw):
            argv = [str(x) for x in args] if not isinstance(args, str) else [args]
            own = argv[argv.index("--") + 1:] if "--" in argv else argv
            self.ran.append(own)
            out = LSCPU + "\n" if own == ["/usr/bin/lscpu"] else ""
            return subprocess.CompletedProcess(argv, 0 if out else 1, out, "")

        def _popen(args, *a, **kw):
            self.processes.append(args if isinstance(args, str) else " ".join(map(str, args)))
            raise OSError("a child process, refused by this case")

        def _connect(sock, address, *a, **kw):
            self.connections.append(repr(address))
            raise OSError("a connection, refused by this case")

        with tempfile.TemporaryDirectory() as home:
            paths = tuple(Path(home, name) for name in ("dispatch-key", "web-token"))
            for path in paths:
                path.write_text("fake\n", encoding="utf-8")
            with mock.patch("subprocess.run", _run), mock.patch("subprocess.Popen", _popen), \
                    mock.patch.object(socket.socket, "connect", _connect), \
                    mock.patch("intergen.tool_registry.write_record", lambda *a, **kw: None), \
                    mock.patch("intergen.tool_process.credential_paths", return_value=paths):
                return r.route(question)


class TheCpuQuestionIsAnsweredFromTheMachine(_Turn):

    def test_what_cpu_do_i_have(self) -> None:
        result = self._route("what cpu do i have")
        self.assertEqual(result.text, CPU_ANSWER)
        self.assertEqual(self.ran, [["/usr/bin/lscpu"]])
        self.assertEqual((self.asked, self.processes, self.connections), ([], [], []))


class TheDiskSpaceQuestionGoesToTheModel(_Turn):

    def test_whats_taking_up_all_my_disk_space(self) -> None:
        result = self._route("what's taking up all my disk space")
        self.assertNotIn("shell", result.text or "")
        self.assertEqual(result.text, MODEL_ANSWER)
        self.assertEqual(self.ran, [])
        self.assertEqual(self.asked, ["stream_with_tools"])
        self.assertEqual((self.processes, self.connections), ([], []))


# ── the controls: green at both ends ────────────────────────────────────────

class WhatWasRightStaysRight(_Selector):

    def test_the_df_and_free_keys(self) -> None:
        for question, command in (("how much disk space do i have", "df -h"),
                                  ("how much free space is left", "df -h"),
                                  ("what's using all my ram", "free -h")):
            with self.subTest(question=question):
                self.assertEqual(R._natural_language_to_command(question), command)

    def test_the_cpu_template_reads_a_whole_lscpu_output(self) -> None:
        self.assertEqual(R._template_synthesis("what cpu do i have", LSCPU, "run_command"),
                         CPU_ANSWER)

    def test_the_executor_still_refuses_a_pipeline(self) -> None:
        ran = []
        with mock.patch("subprocess.run", lambda *a, **kw: ran.append(a)):
            result = RunCommandTool().execute({"command": "lscpu | head -20"})
        self.assertFalse(result.success)
        self.assertIn("no shell", result.content)
        self.assertEqual(ran, [])


if __name__ == "__main__":
    unittest.main()
