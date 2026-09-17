#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The security module that can name a task must be asked before one that cannot.

WHAT THIS GUARDS. The kernel asks the security modules for a task's security
context through security_lsmprop_to_secctx(). With no module named, that function
returns the answer of the FIRST module registered for the hook and does not try
the next one — unlike security_secid_to_secctx() beside it, which is built from
call_int_hook() and keeps going while a module answers with the framework's
default. The BPF security module registers a stub for every hook, and its stub
answers -EOPNOTSUPP, the framework default for this one.

MEASURED 2026-09-17 on a machine running 6.18.10 with the shipped configuration
(CONFIG_LSM="lockdown,yama,integrity,bpf,landlock,apparmor"), by reading the live
kernel's hook table and by tracing the calls:

  * slot 0 of the lsmprop_to_secctx hook holds bpf_lsm_lsmprop_to_secctx,
    slot 1 holds apparmor_lsmprop_to_secctx;
  * a kprobe on apparmor_lsmprop_to_secctx recorded 0 entries while the same
    probe on apparmor_secid_to_secctx recorded 21 — the second hook is reached
    through call_int_hook(), the first is not reached at all;
  * so security_lsmprop_to_secctx() answered -EOPNOTSUPP (errno 95) although
    AppArmor was present and able to name the task.

WHAT IT COST. audit_log_subj_ctx() treats any answer other than -EINVAL as a
failure, so every audit configuration record failed to build, and
audit_do_config_change() denies the change whose record could not be written.
With auditing enabled, EVERY runtime audit configuration change was refused with
errno 95: the backlog limit could not be raised from the kernel's default of 64
to the 262144 this distribution asks for, the rate limit and failure action could
not be set, and auditing could not even be switched back off. The kernel printed
"audit: error in audit_log_subj_ctx" for each attempt.

The kernel's own maintainers, on the security module list in June 2026, gave the
ordering as the resolution for exactly this report and did not merge the proposed
source change. So the fix is the order: a module that can produce a subject
context is asked before one that answers "not supported".

These tests pin that order in the fragment the kernel is configured from, pin the
same order in the produced-config gate both kernel recipes run, and fire the real
gate against a config carrying the old order so the instrument is known to detect
it.
"""
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FRAGMENT = REPO_ROOT / "config/kernel/fragments/99-intergenos-overrides.config"
EXACT_FILE = REPO_ROOT / "config/kernel/required-security-symbols.txt"
ENABLED_FILE = REPO_ROOT / "config/kernel/required-hardware-symbols.txt"
DISABLED_FILE = REPO_ROOT / "config/kernel/required-disabled-symbols.txt"
GATE = REPO_ROOT / "scripts/check-kernel-required-symbols.py"

CLEAN, FINDINGS = 0, 1

# Modules that can return a task's security context on this architecture, i.e.
# that register lsmprop_to_secctx with a real implementation rather than a stub.
# AppArmor is the one this distribution builds; SELinux and Smack are the others
# upstream carries, listed so a future switch of major module is not silently
# outside this rule.
CONTEXT_PROVIDERS = ("apparmor", "selinux", "smack")
STUB_ONLY = "bpf"

RE_LSM_LINE = re.compile(r'^CONFIG_LSM="([^"]*)"$', re.M)


def lsm_order(text: str, where: Path) -> list:
    match = RE_LSM_LINE.search(text)
    assert match, (
        f"{where} states no CONFIG_LSM=\"...\" line. This rule cannot be checked "
        "against a file that does not set the order, and a check that cannot "
        "measure must not pass."
    )
    return [name.strip() for name in match.group(1).split(",") if name.strip()]


def requirements(path: Path) -> list:
    return [
        line.strip() for line in path.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def assert_provider_precedes_stub(order: list, where: str):
    present = [name for name in order if name in CONTEXT_PROVIDERS]
    assert present, (
        f"{where} lists no security module that can produce a task context "
        f"({', '.join(CONTEXT_PROVIDERS)}); audit records would carry no subject."
    )
    assert STUB_ONLY in order, (
        f"{where} no longer lists the {STUB_ONLY} module. If that is intended, this "
        "rule needs rewriting rather than deleting: it exists because a module that "
        "answers 'not supported' first hides the module that can answer."
    )
    stub_at = order.index(STUB_ONLY)
    for name in present:
        assert order.index(name) < stub_at, (
            f"{where} orders {name} after {STUB_ONLY}: {','.join(order)}. "
            f"security_lsmprop_to_secctx() returns the FIRST registered module's "
            f"answer, so {STUB_ONLY}'s -EOPNOTSUPP stub would be the answer and "
            f"{name} would never be asked. Measured consequence: every runtime audit "
            "configuration change is refused with errno 95 while auditing is enabled."
        )


def test_the_fragment_asks_the_context_provider_first():
    assert_provider_precedes_stub(
        lsm_order(FRAGMENT.read_text(), FRAGMENT), str(FRAGMENT))


def test_the_produced_config_gate_pins_the_same_order():
    """The fragment states what is requested; the gate asserts what was produced.
    `cat fragments/*.config | olddefconfig` has no conflict detection, so a
    requested order can be silently replaced by a default."""
    pinned = [line for line in requirements(EXACT_FILE) if line.startswith("CONFIG_LSM=")]
    assert len(pinned) == 1, (
        f"{EXACT_FILE} holds {len(pinned)} CONFIG_LSM requirements; exactly one is "
        "expected, so the produced config's module order is asserted at build time "
        "rather than only requested in a fragment."
    )
    assert_provider_precedes_stub(lsm_order(pinned[0] + "\n", EXACT_FILE), str(EXACT_FILE))
    fragment_line = RE_LSM_LINE.search(FRAGMENT.read_text()).group(0)
    assert pinned[0] == fragment_line, (
        f"the order asked for and the order asserted differ.\n"
        f"  fragment: {fragment_line}\n  gate     : {pinned[0]}\n"
        "One of them would then be describing a kernel nobody builds."
    )


def satisfying_config(tmp_path: Path, replace=None) -> Path:
    """A produced config meeting every requirement, with one line optionally
    swapped for the shape being tested."""
    lines = ["CONFIG_64BIT=y", 'CONFIG_LOCALVERSION="-igos-4"']
    for req in requirements(EXACT_FILE):
        if replace and req.startswith(replace[0]):
            lines.append(replace[1])
        else:
            lines.append(req)
    lines += [f"{name}=m" for name in requirements(ENABLED_FILE)]
    lines += [f"# {name} is not set" for name in requirements(DISABLED_FILE)]
    path = tmp_path / "produced.config"
    path.write_text("\n".join(lines) + "\n")
    return path


def run_gate(config: Path):
    return subprocess.run(
        [sys.executable, str(GATE), "--repo-root", str(REPO_ROOT), "--config", str(config)],
        capture_output=True, text=True)


def test_a_config_carrying_the_pinned_order_passes_the_gate(tmp_path):
    """Otherwise the refusal below could be the gate refusing everything."""
    result = run_gate(satisfying_config(tmp_path))
    assert result.returncode == CLEAN, f"\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"


def test_the_gate_refuses_the_produced_config_that_asks_the_stub_first(tmp_path):
    """The shape this distribution shipped until 2026-09-17, fired at the real
    gate: an instrument never shown to detect the defect cannot certify its
    absence."""
    old_order = 'CONFIG_LSM="lockdown,yama,integrity,bpf,landlock,apparmor"'
    result = run_gate(satisfying_config(tmp_path, replace=("CONFIG_LSM=", old_order)))
    assert result.returncode == FINDINGS, (
        f"the gate did not refuse a config whose module order puts the stub first.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    assert "CONFIG_LSM" in result.stdout, (
        "the refusal does not name CONFIG_LSM, so a reader could not tell what to fix:\n"
        + result.stdout)
    assert old_order.split("=", 1)[1] in result.stdout, (
        "the refusal should say what the config produced INSTEAD of the pinned order:\n"
        + result.stdout)
