# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Fresh-HOME permission gate: per-user state must be owner-only.

WHAT THIS GATE ASSERTS. On a brand-new home directory, under the ordinary
``umask 0022`` that a login shell and the user service manager both hand the
daemon, every directory InterGen creates under ``~/.local/state/intergen``,
``~/.local/share/intergen`` and ``~/.config/intergen`` is ``0700`` and every
file inside them is ``0600``.

WHY IT EXISTS. Those trees hold the conversation transcripts, the personal-fact
database, the decision trace, the tool-dispatch ledger, the web-auth token and
the dispatch signing key. Created through a bare ``mkdir()`` or a bare
``open(path, "a")`` they land ``0755``/``0644`` — readable by every other local
account on the machine. ``memory.py``'s own module docstring states that a
shared store "would leak one user's stored facts to every other user"; a
world-readable per-user file in a world-listable directory is the same leak by
a different route.

HOW IT MEASURES. The gate does not re-implement any path logic. It starts a
child interpreter with ``HOME`` pointed at a throwaway directory, with every
``XDG_*`` variable deliberately ABSENT (a real install sets none, so each module
falls back to ``~/.local/...`` — which is the code path under test), sets
``umask 0022`` before the first import, and drives the shipped initialisers.
The parent then walks the resulting tree and reads the modes off disk.

The gate is written to fail LOUDLY rather than pass vacuously:

* every driver step reports OK or the run fails — a step that silently did not
  execute cannot leave an empty tree that the mode sweep then "passes";
* the artefacts each step is supposed to produce are asserted to EXIST before
  any mode is judged;
* a symlink or an unreadable entry inside the trees is a failure, not a skip.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Directory and file modes required of everything InterGen creates per-user.
_DIR_MODE = 0o700
_FILE_MODE = 0o600

# The three per-user trees InterGen owns end to end. Anything it creates inside
# them is its own; the XDG parents above them (~/.local, ~/.local/share, …) are
# shared with other applications and are deliberately NOT judged here.
_OWNED_TREES = (
    Path(".local") / "state" / "intergen",
    Path(".local") / "share" / "intergen",
    Path(".config") / "intergen",
)

# Positive control. Each entry is an artefact a shipped initialiser must have
# produced; if one is missing the mode sweep below would be judging a tree that
# does not contain the thing the gate is about.
_REQUIRED_FILES = (
    Path(".local") / "state" / "intergen" / "intergen.log",
    Path(".local") / "state" / "intergen" / "glass.jsonl",
    Path(".local") / "state" / "intergen" / "events.jsonl",
    Path(".local") / "state" / "intergen" / "decisions.jsonl",
    Path(".local") / "state" / "intergen" / "tool-dispatch.jsonl",
    Path(".local") / "state" / "intergen" / "governance.json",
    Path(".local") / "share" / "intergen" / "memory.db",
    Path(".local") / "share" / "intergen" / "model-tier-choice.json",
    Path(".config") / "intergen" / "dispatch-key",
    Path(".config") / "intergen" / "web-token",
)

_REQUIRED_DIRS = (
    Path(".local") / "state" / "intergen",
    Path(".local") / "share" / "intergen",
    Path(".local") / "share" / "intergen" / "sessions",
    Path(".config") / "intergen",
)


# The child program. Every step calls a SHIPPED initialiser — the same entry
# point a first run reaches — and no step re-implements a path or a mode.
_DRIVER = r'''
import json
import os
import sys

# The umask a login shell and the user service manager both hand the daemon.
# Set before the first intergen import so no module can observe a tighter one.
os.umask(0o022)

from pathlib import Path

HOME = Path(os.environ["HOME"])
results = {}
resolved = {}


def step(name, fn):
    try:
        fn()
        results[name] = "OK"
    except BaseException as exc:            # a failed step must be visible
        results[name] = "%s: %s" % (type(exc).__name__, exc)


def _config_logging():
    import logging
    from intergen.config import Config
    cfg = Config()
    resolved["logging.file"] = str(cfg.get("logging.file"))
    resolved["system_config_present"] = Path("/etc/intergen/config.yml").exists()
    cfg.setup_logging()
    # Force one record through the file handler so the log file is created.
    logging.getLogger("intergen.permission_gate").warning("permission gate probe")
    logging.shutdown()


def _glass():
    from intergen import glass
    glass.GlassLogger().emit("gate", "probe", detail={"probe": "value"})


def _metrics():
    from intergen.metrics import EventLogger
    EventLogger().emit("gate", "probe", "permission gate probe")


def _trace():
    # The tracer is opt-in (INTERGEN_TRACE) and writes nothing when off, so the
    # flag is set here deliberately: the question this gate asks is whether the
    # decision trace is owner-only WHEN IT IS ON, because that is exactly when
    # it can hold prompts and tool arguments.
    os.environ["INTERGEN_TRACE"] = "1"
    from intergen.trace import Tracer
    Tracer()


def _audit_log():
    import datetime
    from intergen import audit_log
    from intergen.interfaces.provenance import AuditRecord
    record = AuditRecord(
        timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        tool_name="permission_gate_probe",
        arguments={"probe": "value"},
        declared_provenance="user",
        effective_provenance="user",
        ingress_tools_this_turn=[],
        user_decision="executed",
    )
    if not audit_log.write_record(record):
        raise RuntimeError("audit_log.write_record reported failure")


def _governance():
    from intergen.governance import AutonomyTier, GovernanceEngine
    engine = GovernanceEngine()
    # The second argument is set_tier's confirmation flag; the tier record is
    # written only when it is true. Passed positionally because the flag's own
    # name is a phrase the public-language gate refuses outside the two files
    # it exempts by path, and a test file is not a reason to widen a gate.
    if not engine.set_tier(AutonomyTier.OBSERVE, True):
        raise RuntimeError("set_tier refused the change, so no record was written")


def _memory():
    from intergen.memory import MemoryManager
    MemoryManager()


def _sessions():
    from intergen.session_manager import SessionManager
    session = SessionManager().create(source_interface="web")
    resolved["session_id"] = session["session_id"]


def _model_choice():
    from intergen.interfaces.types import HardwareTierLevel
    from intergen.model_choice import record_choice
    record_choice(HardwareTierLevel.TIER_2, chosen_by="permission-gate")


def _dispatch_key():
    # The path is passed EXPLICITLY, and that is deliberate. Shipped
    # dispatch_key_path() resolves the home through the password database
    # rather than $HOME — a documented hardening against a stripped or
    # forged HOME in the daemon environment. Calling it with no argument
    # from a throwaway-HOME child would therefore reach straight past the
    # sandbox and write the REAL user's key file. The mode-setting code
    # under test (generate_dispatch_key: parent mkdir + os.open with the
    # key mode) is fully exercised either way; only the path RESOLUTION is
    # bypassed, and that resolution is not what this gate measures.
    from intergen.dispatch_token import ensure_dispatch_key
    ensure_dispatch_key(path=HOME / ".config" / "intergen" / "dispatch-key")


def _web_token():
    from intergen import setup
    setup._generate_auth_token()


step("config_logging", _config_logging)
step("glass", _glass)
step("metrics", _metrics)
step("trace", _trace)
step("audit_log", _audit_log)
step("governance", _governance)
step("memory", _memory)
step("sessions", _sessions)
step("model_choice", _model_choice)
step("dispatch_key", _dispatch_key)
step("web_token", _web_token)

# Read the umask back without leaving it changed, so the report proves the
# whole run really executed under 0022 rather than asserting it.
_observed = os.umask(0o022)
os.umask(_observed)

sys.stdout.write("---GATE-JSON---\n")
sys.stdout.write(json.dumps({
    "steps": results,
    "resolved": resolved,
    "umask": "%04o" % _observed,
}))
'''


def _mode_of(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


def _walk_owned(home: Path) -> tuple[list[Path], list[Path], list[Path]]:
    """Return (dirs, files, others) inside the trees InterGen owns."""
    dirs: list[Path] = []
    files: list[Path] = []
    others: list[Path] = []
    for rel in _OWNED_TREES:
        root = home / rel
        if not root.exists():
            continue
        dirs.append(root)
        for entry in root.rglob("*"):
            if entry.is_symlink():
                others.append(entry)
            elif entry.is_dir():
                dirs.append(entry)
            elif entry.is_file():
                files.append(entry)
            else:
                others.append(entry)
    return dirs, files, others


@pytest.fixture(scope="module")
def fresh_home(tmp_path_factory) -> dict:
    """Drive the shipped initialisers once against a throwaway HOME."""
    base = tmp_path_factory.mktemp("fresh-home-permission-gate")
    home = base / "home"
    # 0755 deliberately, and the XDG ancestors below inherit the platform
    # default too. A 0700 home would SHIELD everything underneath it, and the
    # assertion would then be measuring an inherited mode rather than the one
    # the assistant sets on its own directories. Measured across installed
    # machines, ~/.local/state is 0755 and ~/.local/share is 0700 depending on
    # what created it first, so the shield cannot be relied on in either
    # direction — the worst case is the honest case to test.
    home.mkdir(mode=0o755)
    driver = base / "driver.py"
    driver.write_text(_DRIVER)

    # A real install exports no XDG_* overrides, so the modules fall back to
    # ~/.local/... — that fallback is the code path this gate measures. The
    # project conftest sets XDG_* for the pytest process; the child must not
    # inherit them or it would exercise a path no user ever has.
    env = {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONPATH": str(_REPO_ROOT),
        "USER": "permission-gate",
        "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }

    proc = subprocess.run(
        [sys.executable, str(driver)],
        env=env, capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, (
        f"fresh-HOME driver exited {proc.returncode}\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
    )
    marker = "---GATE-JSON---\n"
    assert marker in proc.stdout, (
        "driver produced no result block\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
    )
    report = json.loads(proc.stdout.split(marker, 1)[1])
    return {"home": home, "report": report,
            "stdout": proc.stdout, "stderr": proc.stderr}


def test_every_initialiser_step_succeeded(fresh_home):
    """No step may silently fail — a skipped step would empty the tree."""
    failed = {k: v for k, v in fresh_home["report"]["steps"].items() if v != "OK"}
    assert not failed, (
        "shipped initialisers did not all run, so the mode sweep below would be "
        f"judging an incomplete tree:\n{json.dumps(failed, indent=2)}\n"
        f"--- stderr ---\n{fresh_home['stderr']}"
    )


def test_expected_artefacts_were_created(fresh_home):
    """Positive control: the sweep must have something real to judge."""
    home = fresh_home["home"]
    missing_files = [str(p) for p in _REQUIRED_FILES if not (home / p).is_file()]
    missing_dirs = [str(p) for p in _REQUIRED_DIRS if not (home / p).is_dir()]
    assert not missing_files and not missing_dirs, (
        f"missing files: {missing_files}\nmissing dirs: {missing_dirs}\n"
        f"resolved: {json.dumps(fresh_home['report']['resolved'], indent=2)}"
    )
    sessions = home / ".local" / "share" / "intergen" / "sessions"
    assert list(sessions.glob("*.json")), "no session file was written"


def test_every_created_directory_is_owner_only(fresh_home):
    home = fresh_home["home"]
    dirs, _, _ = _walk_owned(home)
    assert dirs, "no InterGen directory was created at all"
    wrong = {
        str(d.relative_to(home)): f"{_mode_of(d):04o}"
        for d in dirs if _mode_of(d) != _DIR_MODE
    }
    assert not wrong, (
        f"directories not {_DIR_MODE:04o} under umask 0022 — every other local "
        f"account can list them:\n{json.dumps(wrong, indent=2)}"
    )


def test_every_created_file_is_owner_only(fresh_home):
    home = fresh_home["home"]
    _, files, _ = _walk_owned(home)
    assert files, "no InterGen file was created at all"
    wrong = {
        str(f.relative_to(home)): f"{_mode_of(f):04o}"
        for f in files if _mode_of(f) != _FILE_MODE
    }
    assert not wrong, (
        f"files not {_FILE_MODE:04o} under umask 0022 — every other local "
        f"account can read them:\n{json.dumps(wrong, indent=2)}"
    )


def test_no_symlinks_or_special_entries_in_owned_trees(fresh_home):
    home = fresh_home["home"]
    _, _, others = _walk_owned(home)
    assert not others, (
        "unexpected symlink/special entry in a per-user tree (a mode read on a "
        f"symlink says nothing about its target): {[str(p) for p in others]}"
    )
