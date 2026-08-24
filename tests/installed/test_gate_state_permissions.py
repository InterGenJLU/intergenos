"""GATE 2 — fresh-account state permissions under the REAL umask (section 9 line 1).

WHAT COMPOSITION PROPERTY THIS CATCHES. The assistant creates its conversation
store, its session transcripts, its tool-dispatch audit log and its application log
at runtime, taking whatever modes the process umask gives them. A source-tree unit
test runs under the test runner's umask inside a temporary directory, so it can never
see what the shipped code produces on a real account. The property only exists once
the shipped module, the system's real default umask and a real home directory are
composed.

TWO DESIGN DECISIONS THAT MAKE THE RESULT ATTRIBUTABLE.

1. THE TEST CREATES NOTHING. Every directory and every file measured here is created
   by the SHIPPED module during the probe. An earlier draft of this gate pre-created
   ``~/.local/share/intergen`` and then measured its mode, which measured the test's
   own umask rather than the product's behaviour. Each probe now calls one shipped
   entry point with no path arguments and lets it resolve and create its own paths.

2. EACH PROBE GETS ITS OWN FRESH HOME. ``intergen.audit_log.write_record`` chmods its
   parent directory to 0700; ``intergen.memory.MemoryManager`` does not. Run in one
   home in one order, whichever ran first would decide the mode of a shared parent and
   the verdict would be an artefact of ordering. Separate homes make each mode
   attributable to exactly one shipped code path, which is what a finding has to be.

The probes run as subprocesses on purpose: the modes depend on the process umask and
on the XDG variables, and the repository's root ``conftest.py`` has already rewritten
those for the pytest process. A child with an explicit clean environment is the only
way to observe what the shipped code does on a real account.

EXPECTED TO FAIL ON R001.1 AS SHIPPED.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

# ── What each artefact is owed, and why ────────────────────────────────────────
#
# The conversation store holds the verbatim record of what a person said to their
# assistant. It is owed 0600 for the same reason a mail spool is: no other account on
# the machine has any business reading it. The directories that contain these things
# are owed 0700 for the same reason — a directory another account can traverse is a
# directory whose contents can be reached by name even when the files themselves are
# tightened later.
OWED_DIR = 0o700
OWED_FILE = 0o600

# ── The probes ─────────────────────────────────────────────────────────────────
#
# Each entry is (probe id, what shipped code path it exercises, the body of the child
# program). Each body must print one JSON object mapping a label to an absolute path.
# The body may not create any directory or file itself.

_PROBE_MEMORY = """
from intergen.memory import MemoryManager
m = MemoryManager()                      # no db_path: the shipped default resolver
p = m._db_path
emit({"the assistant data directory": str(p.parent),
      "the conversation store": str(p)})
"""

_PROBE_SESSIONS = """
from intergen.session_manager import SessionManager
sm = SessionManager()                    # no sessions_dir: the shipped default
s = sm.create(source_interface="web", title="gate probe")
d = sm._dir
emit({"the assistant data directory": str(d.parent),
      "the session transcript directory": str(d),
      "a session transcript": str(d / (s["session_id"] + ".json"))})
"""

_PROBE_AUDIT = """
import datetime
from intergen import audit_log
from intergen.interfaces.provenance import AuditRecord
rec = AuditRecord(
    timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    tool_name="gate_probe", arguments={}, declared_provenance="user",
    effective_provenance="user", ingress_tools_this_turn=[],
    user_decision="executed")
ok = audit_log.write_record(rec)
assert ok, "the shipped audit writer reported failure"
p = audit_log.default_log_path()
emit({"the assistant state directory": str(p.parent),
      "the tool-dispatch audit log": str(p)})
"""

_PROBE_APP_LOG = """
from pathlib import Path
from intergen.config import Config
Config().setup_logging()                 # resolves and creates its own log path
import logging
logging.getLogger("intergen.gateprobe").warning("gate probe line")
logging.shutdown()
state = Path(os.environ["HOME"]) / ".local" / "state" / "intergen"
found = sorted(str(q) for q in state.glob("*.log")) if state.is_dir() else []
out = {"the assistant state directory": str(state)}
for q in found:
    out["the application log " + Path(q).name] = q
emit(out)
"""

PROBES = [
    ("memory", "intergen.memory.MemoryManager()", _PROBE_MEMORY),
    ("sessions", "intergen.session_manager.SessionManager().create()", _PROBE_SESSIONS),
    ("audit", "intergen.audit_log.write_record()", _PROBE_AUDIT),
    ("app_log", "intergen.config.Config().setup_logging()", _PROBE_APP_LOG),
]

_PREAMBLE = """
import json, os, sys
def emit(d):
    sys.stdout.write("GATEJSON " + json.dumps(d) + "\\n")
os.umask(%d)
"""


def _default_umask() -> int:
    """The system's real default umask, read rather than assumed."""
    old = os.umask(0o022)
    os.umask(old)
    return old


def _run_probe(body: str, home: Path, site_dir: Path, umask: int):
    """Run one probe in its own fresh HOME and return {label: path}."""
    env = {
        "HOME": str(home),
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(site_dir),
        "LANG": "C.UTF-8",
    }
    proc = subprocess.run(
        [sys.executable, "-c", (_PREAMBLE % umask) + body],
        capture_output=True, text=True, env=env, timeout=180,
    )
    return proc


@pytest.fixture(scope="module")
def probe_results(installed_intergen_dir, tmp_path_factory):
    """Every probe, each in its own fresh HOME, under the system's real umask.

    A probe that cannot run is a FAILURE of the gate, never a skip: an unmeasured
    permission must not read as a correct one.
    """
    umask = _default_umask()
    site_dir = installed_intergen_dir.parent
    results = {}
    broken = []
    for pid, entry_point, body in PROBES:
        home = tmp_path_factory.mktemp("fresh-home-" + pid)
        proc = _run_probe(body, home, site_dir, umask)
        marker = [ln for ln in proc.stdout.splitlines() if ln.startswith("GATEJSON ")]
        if proc.returncode != 0 or not marker:
            broken.append(
                f"probe {pid} ({entry_point}) exit={proc.returncode}\n"
                f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
            )
            continue
        results[pid] = (entry_point, json.loads(marker[-1][len("GATEJSON "):]), proc.stderr)
    if broken:
        pytest.fail(
            "The shipped state-creation paths could not be exercised against a fresh "
            "account, so nothing about their permissions is known:\n\n"
            + "\n\n".join(broken)
        )
    return results, umask


def test_shipped_code_creates_its_state_private_under_the_real_umask(probe_results):
    results, umask = probe_results
    bad = []
    checked = 0
    for pid, (entry_point, paths, _stderr) in sorted(results.items()):
        for label, raw in sorted(paths.items()):
            p = Path(raw)
            if not p.exists():
                bad.append(f"  [{pid}] {label} was not created at {p}")
                continue
            checked += 1
            owed = OWED_DIR if p.is_dir() else OWED_FILE
            mode = stat.S_IMODE(p.stat().st_mode)
            extra = mode & ~owed
            if extra:
                bad.append(
                    f"  [{pid}] {label} at {p} is {mode:04o}, owed {owed:04o} "
                    f"(reachable by others through mode bits {extra:04o}; created by "
                    f"{entry_point})"
                )
    assert checked, "no artefact was measured; the gate proved nothing"
    assert not bad, (
        f"\nThe shipped code created group- or world-reachable state on a fresh "
        f"account under the system's real default umask ({umask:04o}). "
        f"{len(bad)} of {checked} measured artefacts:\n" + "\n".join(bad) +
        "\n\nThis is what a first-time user gets. Nothing in the shipped code sets or "
        "checks the mode of the directories that hold the conversation store, so the "
        "privacy of that store rests on a parent directory the code never looks at."
    )


def test_no_shipped_state_path_warns_about_a_fallback(probe_results):
    """A probe that fell back to a second location did not exercise the real path.

    ``config.setup_logging`` and the glass writer both carry an OSError fallback that
    logs a warning and writes somewhere else. If a probe took a fallback branch, the
    permission verdict above is about the fallback and not about the shipped default —
    the warning is reported here rather than being swallowed.
    """
    results, _ = probe_results
    noisy = []
    for pid, (entry_point, _paths, stderr) in sorted(results.items()):
        for line in stderr.splitlines():
            if "Cannot write" in line or "using " in line or "Logging to user state dir" in line:
                noisy.append(f"  [{pid}] {entry_point}: {line.strip()}")
    assert not noisy, (
        "\nA shipped state path took a fallback branch during the probe, so the modes "
        "measured by this gate describe the fallback location and not the default one:\n"
        + "\n".join(noisy)
    )
