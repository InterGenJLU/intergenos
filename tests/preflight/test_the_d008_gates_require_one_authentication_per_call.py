# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The D-008 gates require one administrator authentication per privileged call.

scripts/check-d008-compliance.sh (Gate G, the source tree) and
scripts/check-d008-runtime.sh (Gate A, the assembled chroot) both run
scripts/lib/check-privileged-runner-policy.py, which parses the PolicyKit policy
per action: every action bound to the privileged runner must be auth_admin in
allow_any, allow_inactive and allow_active (no keep), carry its own exec.argv1,
and the argv1 set must equal _PRIVILEGED_TOOLS.

Until 2026-10-22 both gates did the opposite: they passed when the string
auth_admin_keep appeared anywhere in the policy and called that per-action
authentication. A kept authorization covers every runner for five minutes,
because every runner has the same parent (the person's systemd user manager)
and pkexec asks PolicyKit about that parent.

The REAL gate scripts run here, against a scratch copy of exactly the files they
read, with the tree's own policy and with planted ones. The positive control is
the planted keep: a gate that cannot refuse it has not been shown to check
anything.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKER = REPO_ROOT / "scripts" / "lib" / "check-privileged-runner-policy.py"
POLICY = REPO_ROOT / "intergen" / "data" / "org.intergenos.intergen.policy"
REGISTRY = REPO_ROOT / "intergen" / "tool_registry.py"

#: The files check-d008-compliance.sh reads, relative to the repository root.
COMPLIANCE_READS = (
    "intergen/interfaces/provenance.py",
    "intergen/provenance.py",
    "intergen/tool_registry.py",
    "intergen/review_modal.py",
    "intergen/audit_log.py",
    "intergen/data/intergen-privileged-runner",
    "intergen/data/org.intergenos.intergen.policy",
    "intergen/interfaces/types.py",
)

#: The deployed paths check-d008-runtime.sh reads, and their sources in the tree.
PYROOT = "usr/lib/python3.14/site-packages/intergen"
RUNTIME_READS = {
    f"{PYROOT}/interfaces/provenance.py": "intergen/interfaces/provenance.py",
    f"{PYROOT}/provenance.py": "intergen/provenance.py",
    f"{PYROOT}/tool_registry.py": "intergen/tool_registry.py",
    f"{PYROOT}/interfaces/types.py": "intergen/interfaces/types.py",
    f"{PYROOT}/review_modal.py": "intergen/review_modal.py",
    f"{PYROOT}/audit_log.py": "intergen/audit_log.py",
    "usr/share/polkit-1/actions/org.intergenos.intergen.policy":
        "intergen/data/org.intergenos.intergen.policy",
    "usr/bin/intergen-privileged-runner": "intergen/data/intergen-privileged-runner",
}


def _with_a_keep(text):
    """The policy with ONE runner-bound action's allow_active turned to a keep."""
    marker = "<allow_active>auth_admin</allow_active>"
    assert marker in text, "the policy carries no auth_admin allow_active to plant a keep into"
    return text.replace(marker, "<allow_active>auth_admin_keep</allow_active>", 1)


def _checker(policy, registry=REGISTRY):
    return subprocess.run(
        ["python3", "-I", str(CHECKER), "--policy", str(policy), "--registry", str(registry)],
        capture_output=True, text=True, check=False, timeout=60,
    )


def _compliance_tree(tmp_path, policy_text):
    root = tmp_path / "tree"
    shutil.copytree(REPO_ROOT / "scripts", root / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__"))
    for rel in COMPLIANCE_READS:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / rel, root / rel)
    (root / "packages" / "ai" / "intergen").mkdir(parents=True)
    (root / "intergen" / "data" / "org.intergenos.intergen.policy").write_text(policy_text)
    return root


def _run_compliance(root):
    return subprocess.run(
        ["bash", str(root / "scripts" / "check-d008-compliance.sh")],
        capture_output=True, text=True, check=False, timeout=120,
    )


def _runtime_root(tmp_path, policy_text):
    root = tmp_path / "chroot"
    for dest, source in RUNTIME_READS.items():
        (root / dest).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / source, root / dest)
    (root / "usr/share/polkit-1/actions/org.intergenos.intergen.policy").write_text(policy_text)
    (root / "usr/bin/intergen").write_text("#!/bin/sh\n")
    os.chmod(root / "usr/bin/intergen", 0o755)
    os.chmod(root / "usr/bin/intergen-privileged-runner", 0o755)
    (root / "etc/logrotate.d").mkdir(parents=True)
    (root / "etc/logrotate.d/intergen-tool-dispatch").write_text("# rotation\n")
    return root


def _run_runtime(root):
    return subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "check-d008-runtime.sh"), str(root)],
        capture_output=True, text=True, check=False, timeout=120,
    )


def test_the_checker_passes_the_shipped_policy():
    completed = _checker(POLICY)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.startswith("PASS: 4 action(s) bound to /usr/bin/intergen-privileged-runner")


def test_the_checker_refuses_a_planted_keep_naming_the_action(tmp_path):
    planted = tmp_path / "keep.policy"
    planted.write_text(_with_a_keep(POLICY.read_text()))
    completed = _checker(planted)
    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert "allow_active is 'auth_admin_keep', not 'auth_admin'" in completed.stdout


def test_the_checker_refuses_a_runner_action_with_no_argv1(tmp_path):
    text = POLICY.read_text()
    line = '    <annotate key="org.freedesktop.policykit.exec.argv1">write_file</annotate>\n'
    assert line in text
    planted = tmp_path / "no-argv1.policy"
    planted.write_text(text.replace(line, "", 1))
    completed = _checker(planted)
    assert completed.returncode == 1, completed.stdout
    assert "0 exec.argv1 annotations" in completed.stdout
    assert "privileged tools with no action of their own: ['write_file']" in completed.stdout


def test_the_checker_reads_actions_not_comments(tmp_path):
    """A keep in a comment is prose: it neither fails a no-keep policy nor
    rescues a keep one. The grep this replaced could be satisfied by either."""
    text = POLICY.read_text().replace(
        "<policyconfig>", "<!-- auth_admin_keep, named in a comment -->\n<policyconfig>", 1)
    planted = tmp_path / "comment.policy"
    planted.write_text(text)
    assert _checker(planted).returncode == 0


def test_the_checker_cannot_pass_a_policy_that_binds_nothing(tmp_path):
    planted = tmp_path / "empty.policy"
    planted.write_text('<?xml version="1.0"?>\n<policyconfig></policyconfig>\n')
    completed = _checker(planted)
    assert completed.returncode == 1
    assert "nothing was checked" in completed.stdout


def test_the_compliance_gate_passes_the_tree_and_refuses_a_planted_keep(tmp_path):
    clean = _run_compliance(_compliance_tree(tmp_path / "clean", POLICY.read_text()))
    assert clean.returncode == 0, clean.stdout + clean.stderr
    assert "PASS — every action bound to the privileged runner is auth_admin with no keep" in clean.stdout

    keep = _run_compliance(_compliance_tree(tmp_path / "keep", _with_a_keep(POLICY.read_text())))
    assert keep.returncode == 1, keep.stdout + keep.stderr
    assert ("VIOLATION: the PolicyKit policy does not require one administrator "
            "authentication per privileged call") in keep.stdout


def test_the_runtime_gate_passes_the_tree_and_refuses_a_planted_keep(tmp_path):
    clean = _run_runtime(_runtime_root(tmp_path / "clean", POLICY.read_text()))
    assert clean.returncode == 0, clean.stdout + clean.stderr
    assert "PASS — PolicyKit policy deployed + structurally valid" in clean.stdout

    keep = _run_runtime(_runtime_root(tmp_path / "keep", _with_a_keep(POLICY.read_text())))
    assert keep.returncode == 1, keep.stdout + keep.stderr
    assert ("VIOLATION: deployed PolicyKit policy does not require one administrator "
            "authentication per privileged call") in keep.stdout


@pytest.mark.parametrize("gate", ["check-d008-compliance.sh", "check-d008-runtime.sh"])
def test_no_gate_requires_or_names_the_keep_as_per_action_authentication(gate):
    """The words that certified the keep are gone from both gates' checks."""
    text = (REPO_ROOT / "scripts" / gate).read_text()
    assert 'grep -qE "auth_admin_keep"' not in text and 'grep -q "auth_admin_keep"' not in text
    assert "auth_admin_keep per-action authentication" not in text
    assert "check-privileged-runner-policy.py" in text
