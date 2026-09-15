#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Every post-merge kernel assertion must apply to the pass that SHIPS.

WHAT THIS GUARDS. The kernel config is built by concatenating the fragments and
running `make olddefconfig`, a merge with no conflict detection: a symbol can be
requested and silently not appear, either because a dependency downgrade demoted
it or because its parent symbol was never requested and olddefconfig discarded
the children without a word. The measured case in this tree is the second kind —
the baseline fragment asked for thirteen CONFIG_MMC_* host-controller drivers and
never asked for the parent `menuconfig MMC`, so the produced kernel had no MMC
subsystem at all and nothing failed.

Those assertions lived in linux-kernel's configure(). linux-kernel-pass2 declares
`supersedes: [linux-kernel]`, both passes stage the identical
/boot/vmlinuz-<KVER>, and the installer enforces that a superseding package
installs AFTER its predecessor — so the kernel a user boots is pass 2's, and
pass 2 asserted none of this. Decided 2026-08-11: one requirement set, in data
files, read by one gate that BOTH passes run.

These tests fire the real gate. They prove each requirement kind detects its own
measured failure shape, that a gate which cannot measure refuses rather than
passes, and — the point of the change — that BOTH recipes actually run it.
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE = REPO_ROOT / "scripts/check-kernel-required-symbols.py"
EXACT_FILE = REPO_ROOT / "config/kernel/required-security-symbols.txt"
ENABLED_FILE = REPO_ROOT / "config/kernel/required-hardware-symbols.txt"
DISABLED_FILE = REPO_ROOT / "config/kernel/required-disabled-symbols.txt"
RECIPES = ("packages/core/linux-kernel", "packages/core/linux-kernel-pass2")

# The hub's real running-kernel config, when this test runs on an InterGenOS
# machine: the R001.2 kernels carry CONFIG_COMPAT_BRK=y, which is the measured
# defect the disabled kind was added for. Used as a RED input when present.
RUNNING_KERNEL_CONFIG = Path("/boot") / f"config-{Path('/proc/sys/kernel/osrelease').read_text().strip()}" \
    if Path("/proc/sys/kernel/osrelease").is_file() else None

CLEAN, FINDINGS, UNMEASURABLE = 0, 1, 2

# The gate says this when it refuses because it cannot see. Asserting the TEXT
# and not only the exit code is deliberate: a missing script makes the
# interpreter itself exit 2, so a code-only assertion would pass on a tree where
# the gate does not exist at all.
UNMEASURABLE_BANNER = "cannot measure"


def requirements(path: Path) -> list:
    return [
        line.strip() for line in path.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def write_config(path: Path, lines) -> Path:
    path.write_text("\n".join(lines) + "\n")
    return path


def satisfying_config(tmp_path: Path, drop=(), demote=(), extra=(), enable=()) -> Path:
    """A config meeting every requirement, minus whatever the test removes.

    `drop` removes a symbol from the config entirely (for a disabled-list symbol
    that means neither set nor stated off); `enable` turns a disabled-list
    symbol back ON, the measured COMPAT_BRK shape."""
    lines = ["CONFIG_64BIT=y", 'CONFIG_LOCALVERSION="-igos-15"']
    for req in requirements(EXACT_FILE):
        name = req.split("=", 1)[0]
        if name in drop:
            continue
        lines.append(f"{name}=m" if name in demote else req)
    for name in requirements(ENABLED_FILE):
        if name in drop:
            continue
        lines.append(f"{name}=m")
    for name in requirements(DISABLED_FILE):
        if name in drop:
            continue
        lines.append(f"{name}=y" if name in enable else f"# {name} is not set")
    lines.extend(extra)
    return write_config(tmp_path / "produced.config", lines)


def run_gate(config: Path, **overrides):
    argv = [sys.executable, str(GATE), "--repo-root", str(REPO_ROOT), "--config", str(config)]
    for flag, value in overrides.items():
        argv += [f"--{flag.replace('_', '-')}", str(value)]
    return subprocess.run(argv, capture_output=True, text=True)


def assert_refused_as_unmeasurable(result):
    assert result.returncode == UNMEASURABLE, (
        f"expected a refusal as unmeasurable.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert UNMEASURABLE_BANNER in result.stderr, (
        f"exit 2 came from something other than the gate's own refusal.\nstderr:\n{result.stderr}"
    )


# ── the gate is wired into BOTH passes, which is the whole point ────────────

def test_the_gate_exists_and_is_executable():
    assert GATE.is_file(), f"{GATE} is missing"
    assert GATE.stat().st_mode & 0o111, f"{GATE} is not executable"


@pytest.mark.parametrize("recipe", RECIPES)
def test_both_kernel_recipes_run_the_gate_in_configure(recipe):
    """In configure(), not build(): the produced config exists there, which is
    BEFORE the multi-hour compile. A dropped hardware class should cost seconds."""
    text = (REPO_ROOT / recipe / "build.sh").read_text()
    body = re.search(r"^configure\(\)\s*\{(.*?)^\}", text, re.M | re.S)
    assert body, f"{recipe}/build.sh has no configure() function"
    assert "check-kernel-required-symbols.py" in body.group(1), (
        f"{recipe}/build.sh does not run the required-symbol gate in configure(). "
        "Pass 2 supersedes pass 1 and its payload lands last on an installed system, "
        "so an assertion only pass 1 makes is an assertion the shipped kernel never gets."
    )


@pytest.mark.parametrize("recipe", RECIPES)
@pytest.mark.parametrize("field", ["source_tree", "sources_extra"])
def test_the_gates_inputs_are_declared_by_both_recipes(recipe, field):
    text = (REPO_ROOT / recipe / "package.yml").read_text()
    block = re.search(rf"^{field}:\n((?:[ \t]*[-#].*\n)+)", text, re.M)
    assert block, f"{recipe}/package.yml declares no {field}"
    entries = {
        line.strip()[1:].strip()
        for line in block.group(1).splitlines()
        if line.strip().startswith("-")
    }
    for needed in ("scripts/check-kernel-required-symbols.py",
                   "config/kernel/required-security-symbols.txt",
                   "config/kernel/required-hardware-symbols.txt",
                   "config/kernel/required-disabled-symbols.txt"):
        assert needed in entries, (
            f"{recipe}/package.yml does not declare {needed} in {field}; configure() "
            "reads it, so it decides what this kernel must contain."
        )


def test_neither_recipe_still_carries_an_inline_requirement_loop():
    """Two copies of these lists in two shell scripts is the drift class this
    extraction exists to remove. If one comes back, this fails."""
    for recipe in RECIPES:
        text = (REPO_ROOT / recipe / "build.sh").read_text()
        assert "for _sym in" not in text, f"{recipe}/build.sh has an inline exact-value loop again"
        assert "for _hw in" not in text, f"{recipe}/build.sh has an inline hardware loop again"


# ── each requirement kind detects its own measured failure shape ────────────

def test_a_config_meeting_every_requirement_passes(tmp_path):
    """Otherwise every refusal below could be the gate refusing everything."""
    result = run_gate(satisfying_config(tmp_path))
    assert result.returncode == CLEAN, f"\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"


def test_an_exact_value_requirement_demoted_to_module_is_refused(tmp_path):
    """The measured shape: DM_VERITY=y silently demoted to =m because its
    BLK_DEV_DM dependency was =m. The build still succeeds; the guarantee is gone."""
    result = run_gate(satisfying_config(tmp_path, demote={"CONFIG_DM_VERITY"}))
    assert result.returncode == FINDINGS, result.stdout
    assert "CONFIG_DM_VERITY=y" in result.stdout
    assert "CONFIG_DM_VERITY=m" in result.stdout, (
        "the refusal should say what the config produced INSTEAD, not merely that "
        "something is missing"
    )


def test_a_dropped_hardware_class_is_refused(tmp_path):
    """The measured shape: the parent symbol was never requested, so olddefconfig
    discarded thirteen MMC host-controller drivers without a word."""
    result = run_gate(satisfying_config(tmp_path, drop={"CONFIG_MMC"}))
    assert result.returncode == FINDINGS, result.stdout
    assert "CONFIG_MMC" in result.stdout


def test_a_hardware_class_built_as_a_module_still_passes(tmp_path):
    """For a driver, module-versus-built-in is a packaging choice; absence is the
    defect. A gate that demanded =y here would fail every legitimate build."""
    result = run_gate(satisfying_config(tmp_path))
    assert result.returncode == CLEAN, result.stdout


def test_lockdown_resolving_back_to_force_none_is_refused(tmp_path):
    """The failure a positive check for FORCE_INTEGRITY cannot catch."""
    config = satisfying_config(tmp_path, extra=["CONFIG_LOCK_DOWN_KERNEL_FORCE_NONE=y"])
    result = run_gate(config)
    assert result.returncode == FINDINGS, result.stdout
    assert "FORCE_NONE" in result.stdout


# ── the DISABLED kind: a symbol that must be stated off (R001.3 row 23) ─────

def test_a_disabled_symbol_turned_back_on_is_refused(tmp_path):
    """The measured shape: olddefconfig turned CONFIG_COMPAT_BRK on by default in
    every shipped kernel, and with it on the kernel clamps
    kernel.randomize_va_space to 1 whatever the sysctl says."""
    result = run_gate(satisfying_config(tmp_path, enable={"CONFIG_COMPAT_BRK"}))
    assert result.returncode == FINDINGS, result.stdout
    assert "CONFIG_COMPAT_BRK" in result.stdout
    assert "CONFIG_COMPAT_BRK=y" in result.stdout, (
        "the refusal should say what the config produced INSTEAD of the not-set line"
    )


def test_a_disabled_symbol_absent_from_the_config_is_refused(tmp_path):
    """Every symbol in the disabled list exists on x86_64, so a config that does not
    mention it is not the config this build produced. Absence must not read as
    'off'."""
    result = run_gate(satisfying_config(tmp_path, drop={"CONFIG_COMPAT_BRK"}))
    assert result.returncode == FINDINGS, result.stdout
    assert "CONFIG_COMPAT_BRK" in result.stdout
    assert "ABSENT" in result.stdout


def test_a_disabled_symbol_stated_off_passes(tmp_path):
    """The satisfying config states every disabled-list symbol as
    `# CONFIG_X is not set`; that is the only shape that passes."""
    result = run_gate(satisfying_config(tmp_path))
    assert result.returncode == CLEAN, result.stdout
    assert "must-be-disabled" in result.stdout


def test_a_disabled_file_holding_exact_values_refuses(tmp_path):
    """Same claim-shape discipline as the other two files: a `CONFIG_X=n` line is
    not how a produced config says off, so it is refused rather than guessed at."""
    wrong = write_config(tmp_path / "wrong.txt", ["CONFIG_COMPAT_BRK=n"])
    assert_refused_as_unmeasurable(run_gate(satisfying_config(tmp_path), disabled_file=wrong))


def test_an_empty_disabled_file_refuses(tmp_path):
    """The floor for this list is one entry; a parse that finds none is unmeasurable."""
    empty = write_config(tmp_path / "empty.txt", ["# nothing required"])
    assert_refused_as_unmeasurable(run_gate(satisfying_config(tmp_path), disabled_file=empty))


@pytest.mark.skipif(
    RUNNING_KERNEL_CONFIG is None or not RUNNING_KERNEL_CONFIG.is_file()
    or "CONFIG_COMPAT_BRK=y" not in RUNNING_KERNEL_CONFIG.read_text(errors="replace"),
    reason="the running kernel's config is not readable here or does not carry the "
           "R001.2 defect (CONFIG_COMPAT_BRK=y); the synthetic shape above covers it",
)
def test_the_shipped_r0012_kernel_config_is_refused_for_compat_brk(tmp_path):
    """RED against reality: the running R001.2 kernel's own /boot/config carries
    CONFIG_COMPAT_BRK=y. The gate must refuse it for exactly that symbol — and the
    same config with only that line flipped must pass, so the refusal is that
    line's and nothing else's."""
    real = run_gate(RUNNING_KERNEL_CONFIG)
    assert real.returncode == FINDINGS, real.stdout
    assert "CONFIG_COMPAT_BRK   [produced: CONFIG_COMPAT_BRK=y]" in real.stdout
    flipped_lines = [
        "# CONFIG_COMPAT_BRK is not set" if line == "CONFIG_COMPAT_BRK=y" else line
        for line in RUNNING_KERNEL_CONFIG.read_text(errors="replace").splitlines()
    ]
    flipped = run_gate(write_config(tmp_path / "flipped.config", flipped_lines))
    assert flipped.returncode == CLEAN, (
        "the real config refused for more than the one flipped line:\n" + flipped.stdout
    )


# ── it refuses just as hard when it cannot measure ──────────────────────────

def test_a_config_with_no_enabled_symbols_refuses(tmp_path):
    assert_refused_as_unmeasurable(run_gate(write_config(tmp_path / "e.config", ["# nothing"])))


def test_a_missing_requirement_file_refuses(tmp_path):
    assert_refused_as_unmeasurable(
        run_gate(satisfying_config(tmp_path), exact_file=tmp_path / "absent.txt")
    )


def test_a_truncated_requirement_file_refuses(tmp_path):
    """A file that parses to almost nothing would let the gate pass for the
    wrong reason."""
    short = write_config(tmp_path / "short.txt", ["CONFIG_DM_VERITY=y"])
    assert_refused_as_unmeasurable(run_gate(satisfying_config(tmp_path), exact_file=short))


def test_a_malformed_requirement_line_refuses(tmp_path):
    bad = write_config(tmp_path / "bad.txt", ["CONFIG_DM_VERITY=y", "this is not a requirement"])
    assert_refused_as_unmeasurable(run_gate(satisfying_config(tmp_path), exact_file=bad))


def test_an_enabled_file_holding_exact_values_refuses(tmp_path):
    """The two files carry different claims. Feeding one where the other belongs
    must refuse rather than silently assert the wrong thing."""
    wrong = write_config(tmp_path / "wrong.txt", [f"{n}=y" for n in requirements(ENABLED_FILE)])
    assert_refused_as_unmeasurable(run_gate(satisfying_config(tmp_path), enabled_file=wrong))


# ── the requirement files are a decision record ─────────────────────────────

def test_the_requirement_files_are_populated_and_well_formed():
    exact, enabled, disabled = requirements(EXACT_FILE), requirements(ENABLED_FILE), requirements(DISABLED_FILE)
    assert len(exact) >= 20, f"only {len(exact)} exact-value requirements"
    assert len(enabled) >= 30, f"only {len(enabled)} enabled requirements"
    assert len(disabled) >= 1, "the disabled list is empty"
    for line in exact:
        assert re.fullmatch(r"CONFIG_[A-Za-z0-9_]+=\S+", line), f"malformed exact entry: {line!r}"
    for line in enabled + disabled:
        assert re.fullmatch(r"CONFIG_[A-Za-z0-9_]+", line), f"malformed bare-name entry: {line!r}"


def test_no_symbol_is_required_by_two_files():
    """A symbol required at an exact value and separately required as merely
    enabled would let the weaker claim mask a violation of the stronger one; a
    symbol required both on and off could never be satisfied and would refuse
    every build for a reason nobody can fix in the config."""
    exact_names = {line.split("=", 1)[0] for line in requirements(EXACT_FILE)}
    enabled_names = set(requirements(ENABLED_FILE))
    disabled_names = set(requirements(DISABLED_FILE))
    assert not sorted(exact_names & enabled_names), f"exact and enabled: {sorted(exact_names & enabled_names)}"
    assert not sorted(exact_names & disabled_names), f"exact and disabled: {sorted(exact_names & disabled_names)}"
    assert not sorted(enabled_names & disabled_names), f"enabled and disabled: {sorted(enabled_names & disabled_names)}"


def test_the_overrides_fragment_states_every_disabled_symbol_off():
    """The gate refuses a produced config that turned a listed symbol on; the
    fragment is where the build ASKS for it to be off. Omitting a symbol never
    disables it (olddefconfig resolves it from its default), so the request has
    to be the literal not-set line, and it has to be there for every entry."""
    fragment = (REPO_ROOT / "config/kernel/fragments/99-intergenos-overrides.config").read_text()
    for name in requirements(DISABLED_FILE):
        assert f"# {name} is not set" in fragment, (
            f"{name} is required off but 99-intergenos-overrides.config does not state "
            f"`# {name} is not set`; the produced config would take the Kconfig default "
            "and the gate would refuse every kernel build"
        )


def test_no_requirement_is_listed_twice():
    for path in (EXACT_FILE, ENABLED_FILE, DISABLED_FILE):
        entries = requirements(path)
        dupes = sorted({e for e in entries if entries.count(e) > 1})
        assert not dupes, f"{path.name} lists these more than once: {dupes}"
