# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""`intergen --version` is a command.

`docs/legal/payload-licenses.md` tells the reader two things about this
invocation: that it exists, and that it renders the "Powered by Qwen"
attribution required by Tongyi Qianwen License section 4. Neither was true.
`--version` reached the final `else` in `main()` and printed
"Unknown command: --version" before exiting 1, and the attribution string was
absent from the whole source tree.

These cases pin four properties:

* `--version` is dispatched and exits 0 — it is not an unknown command.
* The version it prints is DERIVED from the package's own `__version__`, not a
  literal typed into the CLI a second time. The derivation case changes
  `__version__` and requires the printed line to follow it, so a hard-coded
  string cannot pass — a second copy would drift silently the first time the
  release is bumped.
* The attribution is rendered when a Qwen-family model is actually on this
  machine, and is NOT rendered when none is. An attribution printed on a box
  serving InternVL3.5-2B would be a false statement about what powers it, so
  the absence case is as load-bearing as the presence case.
* Printing a version stays cheap: it loads no model, hashes no model file and
  starts no daemon. Both verification entry points and the hardware probe are
  replaced with something that fails loudly if reached, so the case cannot pass
  by those merely being slow.
"""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from unittest import mock

import intergen
from intergen import cli
from intergen.hardware import HardwareTierLevel
from intergen.model_manager import ModelInfo


def _model(name: str, tier: HardwareTierLevel) -> ModelInfo:
    """A downloaded-model record shaped like the ones list_downloaded returns."""
    return ModelInfo(
        name=name,
        filename=f"{name}-Q4_K_M.gguf",
        repo_id=f"test/{name}",
        quant="Q4_K_M",
        size_gb=1.0,
        sha256="0" * 64,
        tier=tier,
        local_path=f"/nonexistent/{name}-Q4_K_M.gguf",
        downloaded=True,
    )


QWEN_9B = _model("Qwen3.5-9B", HardwareTierLevel.TIER_2)
INTERNVL_2B = _model("InternVL3.5-2B", HardwareTierLevel.TIER_1)


def _run_version(downloaded):
    """Run `intergen --version` with a fixed set of models on the machine.

    Returns (stdout, exit_code); exit_code is None when main() returned without
    raising SystemExit.
    """
    from intergen.model_manager import ModelManager

    buf = io.StringIO()
    code = None
    with mock.patch.object(ModelManager, "list_downloaded",
                           return_value=list(downloaded)), \
         mock.patch.object(cli.sys, "argv", ["intergen", "--version"]):
        with redirect_stdout(buf):
            try:
                cli.main()
            except SystemExit as exc:
                code = exc.code
    return buf.getvalue(), code


class VersionIsACommandTests(unittest.TestCase):

    def test_version_is_dispatched_and_not_an_unknown_command(self):
        """The behaviour the whole cut is about."""
        out, code = _run_version([QWEN_9B])
        self.assertNotIn("Unknown command", out,
                         "--version still falls through to the unknown-command "
                         "branch")
        self.assertIn(intergen.__version__, out)
        self.assertIn(code, (None, 0),
                      f"--version exited {code!r}; a successful version print "
                      "must exit 0")

    def test_the_printed_version_is_derived_from_the_package_version(self):
        """A literal typed into cli.py would pass a fixed-string assertion and
        then drift silently. Move `__version__` and the printed line has to
        move with it."""
        sentinel = "9.9.9-test"
        with mock.patch.object(intergen, "__version__", sentinel):
            out, _ = _run_version([QWEN_9B])
        self.assertIn(sentinel, out,
                      "the printed version did not follow intergen.__version__ "
                      "— it is a second hard-coded copy, not the running "
                      "package version")

    def test_the_qwen_attribution_is_rendered_when_a_qwen_model_is_present(self):
        """Tongyi Qianwen License section 4, and the claim made about this
        command in docs/legal/payload-licenses.md."""
        out, _ = _run_version([QWEN_9B])
        self.assertIn("Powered by Qwen", out)
        self.assertIn("Qwen3.5-9B", out,
                      "the attribution should name the model it is about")

    def test_no_qwen_attribution_when_no_qwen_model_is_on_the_machine(self):
        """A Tier-1 box serves InternVL3.5-2B. Printing "Powered by Qwen"
        there would be a false statement about what is running.

        The two positive assertions come FIRST on purpose. An absence case can
        pass on a run that printed nothing at all — at base this file's output
        was "Unknown command: --version", which contains no "Qwen" either and
        would have scored this case green against a command that does not
        exist. Requiring the dispatch and the version line first makes the
        absence a statement about a command that ran."""
        out, code = _run_version([INTERNVL_2B])
        self.assertNotIn("Unknown command", out)
        self.assertIn(intergen.__version__, out)
        self.assertIn(code, (None, 0))
        self.assertNotIn("Powered by Qwen", out)
        self.assertNotIn("Qwen", out)

    def test_no_attribution_when_no_models_are_downloaded_at_all(self):
        out, code = _run_version([])
        self.assertNotIn("Powered by Qwen", out)
        self.assertIn(intergen.__version__, out,
                      "the version still prints on a machine with no model")
        self.assertIn(code, (None, 0))

    def test_a_failing_model_lookup_does_not_take_the_version_down(self):
        """The attribution is a courtesy line on top of a version print. If the
        manifest is unreadable the version must still print, and the
        attribution must be omitted rather than guessed at."""
        from intergen.model_manager import ModelManager

        def _boom(*_a, **_k):
            raise OSError("manifest unreadable")

        buf = io.StringIO()
        code = None
        with mock.patch.object(ModelManager, "list_downloaded", _boom), \
             mock.patch.object(cli.sys, "argv", ["intergen", "--version"]):
            with redirect_stdout(buf):
                try:
                    cli.main()
                except SystemExit as exc:
                    code = exc.code
        self.assertIn(intergen.__version__, buf.getvalue())
        self.assertNotIn("Powered by Qwen", buf.getvalue())
        self.assertIn(code, (None, 0))

    def test_printing_a_version_hashes_nothing_and_starts_no_daemon(self):
        """Same cheapness property `intergen status` had to be given: the
        command reports what can be established by looking."""
        import intergen.dbus_daemon as dbus_daemon
        from intergen.hardware import HardwareDetector
        from intergen.model_manager import ModelManager

        def _refuse(*_a, **_k):
            raise AssertionError(
                "printing the version hashed a model, probed the hardware or "
                "constructed a daemon")

        with mock.patch.object(ModelManager, "verify_model", _refuse), \
             mock.patch.object(ModelManager, "verify_arbitrary_path", _refuse), \
             mock.patch.object(HardwareDetector, "detect", _refuse), \
             mock.patch.object(dbus_daemon, "InterGenDaemon", _refuse):
            out, _ = _run_version([QWEN_9B])
        self.assertIn(intergen.__version__, out)

    def test_usage_lists_the_command_so_it_is_discoverable(self):
        """A command absent from `intergen help` is a command nobody finds."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.print_usage()
        self.assertIn("--version", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
