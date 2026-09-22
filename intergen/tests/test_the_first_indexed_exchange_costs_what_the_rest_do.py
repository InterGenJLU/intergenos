# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The first indexed exchange on a machine costs what every later one costs.

The conversation index embeds each exchange on a background worker, and the
numeric library that work needs was imported INSIDE the functions the worker
calls. So the first exchange a machine ever indexed paid for loading that
library and every exchange after it did not — measured on this project's
workstation on 2026-09-22: 166 ms for the first call and under a tenth of a
millisecond for the next five, the whole difference being the import. Nothing
in the tree said so, which is the part that matters: a first turn that is
slower than every later turn, for a reason nobody wrote down, is the kind of
thing that gets explained by guessing.

The import moves to the top of the module, so the cost is paid when the daemon
starts and nobody is waiting on an answer.

These cases pin:

* importing the module imports the numeric library, measured in a FRESH
  interpreter rather than inferred from the source text — the module may
  already be loaded in the process running these cases, which would make an
  in-process check pass without proving anything;
* the functions that use it no longer import it themselves;
* a machine WITHOUT that library still imports this module and still degrades
  to an empty answer, which is what the whole embedding path does when it
  cannot do its work. The import is guarded for exactly that reason, and this
  case is what keeps the guard honest.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

from intergen import memory as memory_module

REPO_ROOT = Path(memory_module.__file__).resolve().parents[1]


def _in_a_fresh_interpreter(code: str):
    """Run code in a new process against THIS tree, and return its output."""
    return subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, timeout=120, cwd=str(REPO_ROOT))


class TheImportIsPaidAtModuleImport(unittest.TestCase):

    def test_importing_the_module_imports_the_numeric_library(self):
        proc = _in_a_fresh_interpreter(
            "import sys\n"
            "from intergen import memory\n"
            "print(memory.__file__)\n"
            "print('numpy' in sys.modules)\n")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        printed_file, printed_flag = proc.stdout.split()[:2]
        self.assertEqual(Path(printed_file).resolve(),
                         Path(memory_module.__file__).resolve(),
                         "the fresh interpreter imported a different tree than "
                         "the one these cases are running against")
        self.assertEqual(printed_flag, "True",
                         "importing the conversation index does not load the "
                         "numeric library, so the first indexed exchange on "
                         "this machine still pays for it on the worker thread")

    def test_the_worker_functions_no_longer_import_it_themselves(self):
        source = Path(memory_module.__file__).read_text(encoding="utf-8")
        indented = [line for line in source.splitlines()
                    if "import numpy" in line and line.startswith("    ")
                    and not line.strip().startswith("#")]
        self.assertEqual(
            [line for line in indented if not line.startswith("    import numpy")
             or line.startswith("        ")], [],
            "a function in this module still imports the numeric library on "
            "its own call path:\n" + "\n".join(indented))


class WithoutTheLibraryTheModuleStillWorks(unittest.TestCase):
    """The guard is only honest if the guarded case is exercised."""

    def test_the_module_imports_and_degrades_when_the_library_is_absent(self):
        proc = _in_a_fresh_interpreter(
            "import sys\n"
            "import builtins\n"
            "real = builtins.__import__\n"
            "def no_numpy(name, *a, **k):\n"
            "    if name == 'numpy' or name.startswith('numpy.'):\n"
            "        raise ImportError('numpy is not installed here')\n"
            "    return real(name, *a, **k)\n"
            "builtins.__import__ = no_numpy\n"
            "from intergen import memory\n"
            "builtins.__import__ = real\n"
            "print('imported', memory.np is None)\n"
            "index = memory.SessionTurnIndex.__new__(memory.SessionTurnIndex)\n"
            "index._embedder = lambda texts: [[0.1] * 4 for _ in texts]\n"
            "print('embed_one', index._embed_one('x') is None)\n")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("imported True", proc.stdout,
                      "the module did not import without the numeric library:\n"
                      + proc.stdout + proc.stderr)
        self.assertIn("embed_one True", proc.stdout,
                      "without the numeric library the embedding path did not "
                      "degrade to an empty answer:\n" + proc.stdout)


if __name__ == "__main__":
    unittest.main()
