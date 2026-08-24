"""GATE 3 — the real hardened-unit privilege boundary (section 9 line 2).

WHAT COMPOSITION PROPERTY THIS CATCHES. Two separately correct decisions compose into
a boundary that cannot work. The shipped user service sets ``NoNewPrivileges=yes``,
which is the right hardening for a daemon that talks to a language model. If the
privileged-tool path execs ``pkexec`` AS A CHILD OF THAT DAEMON, the kernel ignores
the setuid bit, so ``pkexec`` runs with the caller's euid, fails its own
``geteuid() != 0`` check and exits 127 — before PolicyKit is contacted at all.
Neither half is wrong on its own. Nothing in the source tree tests them together,
because the unit's hardening does not exist in a source-tree test and the dispatch
tests stub the call out.

WHO STARTS pkexec IS THE WHOLE QUESTION, AND IT MOVED. The shipped dispatch changed: the daemon no longer execs the helper itself, it asks the user manager to
start a short-lived unit that does, and passes an opaque request identifier rather
than the tool name and arguments. The daemon's own ``NoNewPrivileges`` does not reach
that unit, so the composition above no longer bites.

This gate used to decide it with a substring — ``'"pkexec",' in source`` — and that
string is present in BOTH shapes, because the new one still names pkexec, just after
the launcher and a ``--`` separator. Measured against both modules at once on
2026-08-24: substring True on the R001.1 module, where the boundary really is broken,
and True on the tree module, where it is fixed. The gate would have gone on reporting
a defect that had been corrected. The question is now PARSED from the dispatch's argv
by ``_shape_detectors.daemon_execs_the_setuid_helper_directly``, which is proved in
both directions by the ordinary suite.

⛔ THIS GATE NEVER INVOKES pkexec, AND NO GATE IN THIS TIER MAY.
There is no environment scrub that makes ``pkexec`` unattended-safe: it reaches
``polkitd`` over the SYSTEM bus, and a session with a registered authentication agent
renders a real password dialog that a person has to answer. A test that can stop and
wait for a human is a defect in the test, not a scheduling problem. The boundary is
therefore asserted from three things that can all be read without crossing it:

  * the shipped unit's EFFECTIVE settings, read from the service manager;
  * WHO execs the setuid helper, parsed from the shipped dispatch's argv, and the
    mode of that helper, read from the filesystem;
  * the shipped error-translation code, exercised with the subprocess call replaced.

EXPECTED TO FAIL ON R001.1 AS SHIPPED.
"""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

UNIT = "intergen.service"
RUNNER = "/usr/bin/intergen-privileged-runner"


def _systemctl_show(unit: str, properties: list[str]) -> dict[str, str]:
    """Read EFFECTIVE unit properties from the service manager.

    Deliberately not a parse of the unit file: a drop-in, an override or a manual
    edit can change what is in force, and the property in force is the one that
    decides whether the boundary works.
    """
    proc = subprocess.run(
        ["systemctl", "--user", "show", unit] + [f"-p{p}" for p in properties],
        capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0:
        pytest.fail(
            f"Could not read the effective settings of {unit} from the service "
            f"manager, so nothing about the privilege boundary is known.\n"
            f"exit={proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    out = {}
    for line in proc.stdout.splitlines():
        key, _, value = line.partition("=")
        out[key] = value
    return out


@pytest.fixture(scope="module")
def unit_properties() -> dict[str, str]:
    return _systemctl_show(
        UNIT,
        ["NoNewPrivileges", "CapabilityBoundingSet", "AmbientCapabilities",
         "ProtectSystem", "MainPID", "ActiveState"],
    )


def test_the_shipped_daemon_can_actually_reach_its_authentication_boundary(unit_properties):
    """NoNewPrivileges on the unit + a setuid helper in the dispatch path cannot compose.

    This is the whole finding in one assertion. It is stated as the property the
    product needs — a privileged request can reach PolicyKit — rather than as the
    absence of one setting, because the fix may legitimately be on either side.
    """
    nnp = unit_properties.get("NoNewPrivileges") == "yes"

    runner = Path(RUNNER)
    if not runner.exists():
        pytest.fail(
            f"The privileged runner {RUNNER} is absent, so the dispatch path cannot "
            "be characterised. (Note for whoever reads this: the shipped error text "
            "for a failed dispatch claims exactly this condition even when it is "
            "false — see the next test.)"
        )

    pkexec = Path("/usr/bin/pkexec")
    if not pkexec.exists():
        pytest.fail("/usr/bin/pkexec is absent; polkit is not installed.")
    pk_mode = stat.S_IMODE(pkexec.stat().st_mode)
    pk_setuid = bool(pk_mode & stat.S_ISUID)
    pk_root = pkexec.stat().st_uid == 0

    # The shipped dispatch path — PARSED from the installed module, not assumed and
    # not searched for a substring. A reader that cannot characterise the shape
    # raises, and that is reported as a gate failure rather than swallowed: "I could
    # not tell" must never read as "the boundary is fine".
    from intergen import tool_registry

    # Loaded by path, the way this tier's other gates load it. A bare
    # `from _shape_detectors import ...` depends on the tier directory being on
    # sys.path, which it is under an ordinary rootdir-relative collection and is
    # NOT when the tier is invoked the way the checklist invokes it — from
    # outside any checkout, so the installed package is what resolves. That is
    # how this import was found broken: by running the tier, not by reading it.
    _spec = importlib.util.spec_from_file_location(
        "_igos_shape_detectors", Path(__file__).resolve().parent / "_shape_detectors.py")
    shape = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(shape)

    source = Path(tool_registry.__file__).read_text(encoding="utf-8")
    try:
        daemon_execs_helper = shape.daemon_execs_the_setuid_helper_directly(source)
    except ValueError as exc:
        pytest.fail(
            f"The shipped dispatch path could not be characterised: {exc}\n"
            f"module: {tool_registry.__file__}\n"
            "The privilege boundary is therefore unknown on this system, which this "
            "gate reports as a failure rather than as a pass."
        )

    assert not (nnp and daemon_execs_helper and pk_setuid and pk_root), (
        "\nNo privileged action can complete from the shipped daemon, and nothing in "
        "the source tree can see it:\n"
        f"  the unit {UNIT} has NoNewPrivileges={unit_properties.get('NoNewPrivileges')} "
        f"in force (MainPID={unit_properties.get('MainPID')}, "
        f"ActiveState={unit_properties.get('ActiveState')});\n"
        f"  intergen.tool_registry execs pkexec AS A CHILD OF THE DAEMON for the\n"
        f"  privileged-tool tier (parsed from the dispatch's argv, whose first element\n"
        f"  is the helper itself rather than a transient-unit launcher);\n"
        f"  /usr/bin/pkexec is mode {pk_mode:04o} owned by uid {pkexec.stat().st_uid} — "
        "it depends on its setuid bit to become root.\n"
        "Under NoNewPrivileges the kernel ignores that setuid bit, so pkexec runs as "
        "the calling user, fails its own root check and exits 127 without ever "
        "contacting PolicyKit. The user is never asked to authenticate because the "
        "request never gets that far.\n"
        "This gate does not invoke pkexec to demonstrate it; crossing that boundary in "
        "a test raises a real password dialog. The composition above is sufficient."
    )


def test_a_failed_privileged_dispatch_is_not_reported_as_a_missing_runner():
    """The shipped translation asserts a cause it never checked.

    Exit code 127 from ``pkexec`` is translated to "runner not found … package may be
    misinstalled" without looking at the runner path and while discarding the real
    stderr. On this system the runner is present, so that message is false, and it is
    the message that hid the boundary defect above.

    The dispatch is exercised with ``subprocess.run`` replaced. Nothing is executed;
    pkexec is never reached.
    """
    from intergen import tool_registry
    from intergen.tool_registry import ToolCall
    from intergen.interfaces.provenance import Provenance

    runner_present = Path(RUNNER).exists()

    class _Completed:
        returncode = 127
        stdout = ""
        stderr = "pkexec must be setuid root"

    real_run = tool_registry.subprocess.run
    tool_registry.subprocess.run = lambda *a, **k: _Completed()
    try:
        result = tool_registry.ToolRegistry._dispatch_via_pkexec(
            ToolCall(name="manage_services", arguments={"action": "status",
                                                        "service": "intergen"},
                     call_id="gate-3",
                     source_of_request=Provenance.USER_DIRECT),
            "manage_services",
            {"action": "status", "service": "intergen"},
            dispatch_token="gate-3-token-not-verified-here",
        )
    finally:
        tool_registry.subprocess.run = real_run

    content = getattr(result, "content", "")
    claims_missing_runner = "not found" in content and RUNNER in content

    assert not (claims_missing_runner and runner_present), (
        "\nThe shipped code told the user and the model a cause it did not check.\n"
        f"  exit code presented to the translation: 127\n"
        f"  real stderr, which the translation discards: {_Completed.stderr!r}\n"
        f"  runner actually present at {RUNNER}: {runner_present}\n"
        f"  message returned to the user: {content!r}\n"
        "The message sends the user to reinstall a package that is intact, and it is "
        "the diagnostic that concealed the authentication boundary being unreachable."
    )


def test_the_privileged_dispatcher_refuses_outside_the_runner_context():
    """The authorization boundary's own refusal path, exercised without crossing it.

    ``intergen.privileged_dispatch`` is the module that runs as root once PolicyKit
    has authenticated. It must refuse when it is invoked directly — no PKEXEC_UID, a
    tool outside the allowlist, or no approval token. Run here as the ordinary user,
    which is exactly the position an attacker would be in.
    """
    import sys
    env = {k: v for k, v in os.environ.items()
           if k in ("PATH", "LANG", "LC_ALL")}
    env.setdefault("PATH", "/usr/bin:/bin")

    cases = [
        ("no PKEXEC_UID", {}, "manage_services"),
        ("tool outside the allowlist", {"PKEXEC_UID": str(os.getuid())}, "read_file"),
        ("no approval token", {"PKEXEC_UID": str(os.getuid()),
                               "PKEXEC_USER": "nobody"}, "manage_services"),
    ]
    accepted = []
    for label, extra, tool in cases:
        proc = subprocess.run(
            [sys.executable, "-m", "intergen.privileged_dispatch", tool,
             json.dumps({"action": "status", "service": "intergen"})],
            capture_output=True, text=True, timeout=120, env={**env, **extra},
        )
        if proc.returncode == 0:
            accepted.append(
                f"  {label}: exit 0\n    stdout: {proc.stdout.strip()!r}\n"
                f"    stderr: {proc.stderr.strip()!r}")
    assert not accepted, (
        "\nThe privileged dispatcher accepted an invocation it should have refused, "
        "from an ordinary user account:\n" + "\n".join(accepted)
    )
