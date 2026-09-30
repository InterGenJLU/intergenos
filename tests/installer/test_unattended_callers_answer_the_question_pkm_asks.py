# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The unattended callers of pkm keep working under its new answers.

Two changes to pkm turn a missing terminal into a refusal: a removal asks
first, and an install that reaches beyond the package named is not done
unwatched; both proceed on --yes. A third makes `pkm info` exit zero for a
package the index describes and this machine has not installed. Three callers
in this tree relied on the old answers:

  - the assistant's package tool runs the command a person approved with no
    terminal attached (the privileged runner), so every approved removal was
    refused, and every approved install that pulls in a dependency;
  - the GE eval stage installs the gaming meta's whole closure unattended;
  - the gaming smoke check decided "installed" from `pkm info`'s exit status.

Each case runs the real pkm from this tree: the removal end to end against a
scratch root, as root in a user namespace with no terminal; the two install
lines through pkm's own argument parser and its own confirmation gate; the
gaming check against the real `pkm info` answer for a package this machine's
signed index describes and the scratch root has not installed.
"""
from __future__ import annotations

import io
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from pkm import rootpaths, txn
from pkm.cli import build_parser
from pkm.database import PackageDB

REPO = Path(__file__).resolve().parents[2]
SMOKE_DIR = REPO / "installer" / "smoke"


def _wrapper(bindir: Path, root: Path) -> Path:
    """`pkm` on PATH as this tree's real pkm aimed at the scratch root."""
    bindir.mkdir(parents=True, exist_ok=True)
    w = bindir / "pkm"
    w.write_text(
        "#!/bin/sh\n"
        f"exec env PYTHONPATH={shlex.quote(str(REPO))} PYTHONDONTWRITEBYTECODE=1 "
        f"{shlex.quote(sys.executable)} -P -m pkm --root {shlex.quote(str(root))} \"$@\"\n")
    w.chmod(0o755)
    return w


class _PlanReachingBeyond:
    """A resolution that added packages nobody named, as `pkm install gaming`
    does with its lib32 closure."""
    action = "Install"
    beyond_requested = True

    def summary_line(self):
        return "3 packages"

    def name_list(self):
        return ["  gaming", "  lib32-one", "  lib32-two"]


class _NoTerminal:
    def isatty(self):
        return False


class _Reporter:
    def __init__(self):
        self.lines = []

    def step(self, *a):
        self.lines.append(" ".join(str(x) for x in a))

    def step_continuation(self, line):
        self.lines.append(str(line))

    def info(self, line):
        self.lines.append(str(line))

    def error(self, line):
        self.lines.append(str(line))


def _confirms_unattended(argv):
    """What pkm's own gate answers, with no terminal, for an install run with
    these arguments: parsed by pkm's own parser, asked by pkm's own confirm."""
    args = build_parser().parse_args(argv)
    self_yes = bool(getattr(args, "assume_yes", False))
    return txn.confirm(_PlanReachingBeyond(), _Reporter(), assume_yes=self_yes,
                       stream=io.StringIO(), stdin=_NoTerminal())


class TheAssistantsApprovedRemovalRunsWithNoTerminal(unittest.TestCase):

    def setUp(self):
        if shutil.which("unshare") is None:
            self.skipTest("unshare is not available")
        probe = subprocess.run(["unshare", "--user", "--map-root-user", "--net", "true"],
                               capture_output=True)
        if probe.returncode != 0:
            self.skipTest("an unprivileged user namespace is not available here")
        self.tmp = tempfile.TemporaryDirectory()
        top = Path(self.tmp.name)
        self.root = top / "root"
        (self.root / "usr/share/demo").mkdir(parents=True)
        self.payload = self.root / "usr/share/demo/f"
        self.payload.write_bytes(b"payload\n")
        db = PackageDB(str(rootpaths.db_path(self.root)), root=str(self.root))
        try:
            pid = db.add_installed("demo", "1.0", release=1, tier="core")
            db.add_files(pid, ["usr/share/demo/f"])
        finally:
            db.close()
        self.pkm = _wrapper(top / "bin", self.root)
        self.handlers = top / "handlers"
        self.handlers.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_built_removal_is_carried_out(self):
        from intergen.tools.manage_packages import ManagePackagesTool
        cmd = ManagePackagesTool()._build_command("remove", "demo", "", pkm=str(self.pkm))
        env = {k: v for k, v in os.environ.items()
               if k not in ("IGOS_TRACE_RUNID", "IGOS_TRACE_ROOT")}
        env["PKM_PRETXN_HANDLER_DIR"] = str(self.handlers)
        res = subprocess.run(["unshare", "--user", "--map-root-user", "--net", *cmd],
                             capture_output=True, text=True, env=env,
                             stdin=subprocess.DEVNULL, timeout=120)
        self.assertEqual(res.returncode, 0, f"{cmd}\n{res.stdout}{res.stderr}")
        self.assertFalse(self.payload.exists(),
                         "the approved removal was refused and the file is still there")


class TheUnattendedInstallsAnswerTheConfirmation(unittest.TestCase):

    def test_the_assistants_install_command(self):
        from intergen.tools.manage_packages import ManagePackagesTool
        cmd = ManagePackagesTool()._build_command("install", "gaming", "")
        self.assertTrue(_confirms_unattended(cmd[1:]),
                        f"pkm refuses {cmd} with no terminal attached")

    def test_the_eval_stages_install_line(self):
        lines = [ln for ln in (SMOKE_DIR / "ge-eval-stage.sh").read_text().splitlines()
                 if ln.lstrip().startswith("pkm install")]
        self.assertEqual(len(lines), 1, lines)
        words = shlex.split(lines[0].split("||", 1)[0].replace("${META}", "gaming"))
        self.assertEqual(words[:2], ["pkm", "install"])
        self.assertTrue(_confirms_unattended(words[1:]),
                        f"pkm refuses the stage's `{' '.join(words)}` with no terminal attached")


class TheGamingCheckReadsTheInstalledRecord(unittest.TestCase):
    """Against the real `pkm info gaming`: this machine's signed index is copied
    into a scratch root that has not installed gaming, and verified there
    against the system keyring on every read, as pkm always does."""

    def setUp(self):
        from pkm import repo as _repo
        cache = Path(_repo.REPO_DB_CACHE)
        indexes = sorted(cache.glob("*.db")) if cache.is_dir() else []
        if not indexes or not Path(_repo.GPG_KEYRING).is_file():
            self.skipTest("no signed repository index is cached on this machine")
        self.tmp = tempfile.TemporaryDirectory()
        top = Path(self.tmp.name)
        self.root = top / "root"
        self.root.mkdir()
        PackageDB(str(rootpaths.db_path(self.root)), root=str(self.root)).close()
        dest = Path(rootpaths.repo_cache_dir(self.root)) / "db"
        dest.mkdir(parents=True, exist_ok=True)
        for db in indexes:
            shutil.copy2(db, dest / db.name)
            sig = db.with_name(db.name + ".sig")
            if sig.exists():
                shutil.copy2(sig, dest / sig.name)
        self.bindir = top / "bin"
        _wrapper(self.bindir, self.root)
        self.home = top / "home"
        self.home.mkdir()
        info = subprocess.run([str(self.bindir / "pkm"), "info", "gaming"],
                              capture_output=True, text=True)
        if "gaming" not in info.stdout or "Files:" in info.stdout:
            self.skipTest("this machine's index does not describe an uninstalled gaming")
        self.info_status = info.returncode

    def tearDown(self):
        self.tmp.cleanup()

    def test_an_available_uninstalled_meta_is_skipped(self):
        script = f"""
set -u
SMOKE_STRICT=1
SMOKE_JSON=0
SMOKE_VERBOSE=0
SCRIPT_DIR="{SMOKE_DIR}"
. "{SMOKE_DIR}/lib.sh"
. "{SMOKE_DIR}/checks/gaming.sh"
check_gaming_composed_path
"""
        env = dict(os.environ, PATH=f"{self.bindir}:{os.environ['PATH']}",
                   HOME=str(self.home))
        r = subprocess.run(["bash", "-c", script], env=env, capture_output=True,
                           text=True, timeout=120)
        self.assertIn("SKIP", r.stdout,
                      f"pkm info exited {self.info_status} for an available, "
                      f"uninstalled gaming and the check went on as if it were "
                      f"installed:\n{r.stdout}{r.stderr}")
        self.assertIn("not expected", r.stdout)


if __name__ == "__main__":
    unittest.main()
