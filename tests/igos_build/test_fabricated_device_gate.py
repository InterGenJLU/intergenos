#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The fabricated-device gate must refuse a kernel that builds fabricated devices.

WHAT THIS GUARDS, and why a config-level check was not enough. A driver that
fabricates a device the machine does not have is a masking primitive: it makes a
broken system look healthy to everything that inspects it, including this
repository's own hardware smoke checks — the sharpest measured case is the fake
ALSA sound card, because the smoke check tests audio by counting registered
cards, so a fabricated card reports working audio on a machine whose codec is
dead.

The kernel recipe asserted the class absent from the produced .config. Sweeping
the BUILT MODULE LIST of a real kernel build on 2026-08-07 then found ten more
members that check could not have seen, for three structural reasons: a config
check only looks for names somebody already wrote down; a symbol name read off a
module filename can be wrong (vdpa_sim_blk.ko is built by CONFIG_VDPA_SIM_BLOCK,
and CONFIG_VDPA_SIM_BLK does not exist); and a module can have no Kconfig symbol
at all (ddbridge-dummy-fe.ko is built unconditionally as a component of a real
DVB card driver).

The sweep became scripts/check-fabricated-devices.py, fired from both kernel
recipes' build() after the compile. These tests fire the real gate against
purpose-built kernel trees and prove each sweep detects what only it can see,
that the gate REFUSES rather than reports, and that it refuses just as hard when
it cannot measure — an instrument that saw nothing must never certify nothing
wrong.
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE = REPO_ROOT / "scripts/check-fabricated-devices.py"
ALLOWLIST = REPO_ROOT / "config/kernel/fabricated-device-module-allowlist.txt"
CLASS_SOURCE = REPO_ROOT / "docs/research/kernel_configs/analyze_convergence.py"
RECIPES = ("packages/core/linux-kernel", "packages/core/linux-kernel-pass2")

# Exit codes the gate contracts on.
CLEAN, FINDINGS, UNMEASURABLE = 0, 1, 2

# A class member whose module name does NOT carry the fabricated-device
# vocabulary, so a hit on it can only have come from the symbol->module sweep.
SYMBOL_ONLY_MEMBER = ("SCSI_DEBUG", "scsi_debug")

# A real module whose name DOES carry the vocabulary, so it exercises the
# allowlist rather than the class.
ALLOWLISTED_REAL_MODULE = ("DUMMY", "dummy")


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def make_kernel_tree(root: Path, modules=(), obj_rules=()) -> Path:
    """A miniature kernel build tree: build files that map symbols to objects,
    plus the .ko files a compile would have left behind."""
    for makefile, body in obj_rules:
        write(root / makefile, body)
    for module in modules:
        write(root / module, "")
    return root


def make_config(path: Path, enabled=()) -> Path:
    lines = [
        "#",
        "# Automatically generated file; DO NOT EDIT.",
        "#",
        "CONFIG_64BIT=y",
        "CONFIG_X86_64=y",
        'CONFIG_LOCALVERSION="-igos-14"',
    ]
    lines += [f"CONFIG_{s}=m" for s in enabled]
    return write(path, "\n".join(lines) + "\n")


# The gate prints this when it refuses because it cannot see what it was asked
# to look at. Asserting the TEXT and not only the exit code is deliberate: a
# missing gate script makes the interpreter itself exit 2, so a code-only
# assertion would pass on a tree where the gate does not exist at all. That was
# measured on this file's own red-first run, 2026-08-11 — six assertions passed
# at the pristine base for exactly that reason before this check was added.
UNMEASURABLE_BANNER = "cannot measure"


def assert_refused_as_unmeasurable(result):
    assert result.returncode == UNMEASURABLE, (
        f"expected the gate to refuse as unmeasurable.\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert UNMEASURABLE_BANNER in result.stderr, (
        "exit code 2 came from something other than the gate's own refusal — the "
        f"gate never spoke.\nstderr:\n{result.stderr}"
    )


def run_gate(config: Path, kernel_source: Path, **overrides):
    argv = [
        sys.executable, str(GATE),
        "--repo-root", str(REPO_ROOT),
        "--config", str(config),
        "--kernel-source", str(kernel_source),
    ]
    for flag, value in overrides.items():
        argv += [f"--{flag.replace('_', '-')}", str(value)]
    return subprocess.run(argv, capture_output=True, text=True)


@pytest.fixture
def clean_tree(tmp_path):
    """A kernel tree with a real module whose name matches the vocabulary and
    which the allowlist explains. This must PASS — otherwise every refusal
    below could be the gate refusing everything."""
    symbol, module = ALLOWLISTED_REAL_MODULE
    root = make_kernel_tree(
        tmp_path / "linux",
        modules=[f"drivers/net/{module}.ko", "drivers/ata/libata.ko"],
        obj_rules=[("drivers/net/Makefile", f"obj-$(CONFIG_{symbol}) += {module}.o\n")],
    )
    config = make_config(tmp_path / "produced.config", enabled=[symbol])
    return config, root


# ── the gate exists, is wired, and its inputs are declared ──────────────────

def test_the_gate_script_exists_and_is_executable():
    assert GATE.is_file(), f"{GATE} is missing"
    assert GATE.stat().st_mode & 0o111, f"{GATE} is not executable"


@pytest.mark.parametrize("recipe", RECIPES)
def test_both_kernel_recipes_fire_the_gate_after_compiling(recipe):
    """Pass 2 supersedes pass 1 and ships the kernel the user boots, so a gate
    on pass 1 alone would leave the shipped kernel unchecked."""
    text = (REPO_ROOT / recipe / "build.sh").read_text()
    build_body = re.search(r"^build\(\)\s*\{(.*?)^\}", text, re.M | re.S)
    assert build_body, f"{recipe}/build.sh has no build() function"
    assert "check-fabricated-devices.py" in build_body.group(1), (
        f"{recipe}/build.sh does not run the fabricated-device gate in build(). "
        "The gate must fire where the modules exist — after the compile and "
        "before anything is staged."
    )


@pytest.mark.parametrize("recipe", RECIPES)
@pytest.mark.parametrize("field", ["source_tree", "sources_extra"])
def test_the_gates_inputs_are_declared_by_both_recipes(recipe, field):
    """A build input that is not declared is invisible to the rebuild trigger
    and to the release bump — measured on the config fragments 2026-08-07, when
    a fragment edit left the recipe fingerprint byte-identical. In sources_extra
    the same files are what lets published source rebuild the published binary:
    the gate REFUSES when it cannot read them."""
    text = (REPO_ROOT / recipe / "package.yml").read_text()
    block = re.search(rf"^{field}:\n((?:[ \t]*[-#].*\n)+)", text, re.M)
    assert block, f"{recipe}/package.yml declares no {field}"
    entries = {
        line.strip()[1:].strip()
        for line in block.group(1).splitlines()
        if line.strip().startswith("-")
    }
    for needed in (
        "scripts/check-fabricated-devices.py",
        "config/kernel/fabricated-device-module-allowlist.txt",
        "docs/research/kernel_configs/analyze_convergence.py",
    ):
        assert needed in entries, (
            f"{recipe}/package.yml does not declare {needed} in {field}. "
            f"build() reads it, so it decides whether this kernel builds."
        )


# ── the three sweeps, each proven on what only it can see ───────────────────

def test_a_clean_build_passes(clean_tree):
    config, root = clean_tree
    result = run_gate(config, root)
    assert result.returncode == CLEAN, (
        f"the gate refused a clean tree.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "CLEAN" in result.stdout


def test_sweep_1_refuses_a_class_member_enabled_in_the_produced_config(clean_tree, tmp_path):
    """Being absent from a fragment does not disable a symbol: olddefconfig
    resolves it from its Kconfig default and from any `imply`."""
    _, root = clean_tree
    symbol, _ = SYMBOL_ONLY_MEMBER
    config = make_config(tmp_path / "dirty.config", enabled=[ALLOWLISTED_REAL_MODULE[0], symbol])
    result = run_gate(config, root)
    assert result.returncode == FINDINGS, result.stdout
    assert f"CONFIG_{symbol}" in result.stdout


def test_sweep_2_refuses_a_class_member_that_was_actually_built(clean_tree, tmp_path):
    """The config can be clean while the module is in the build — that is how
    the mock SoundWire codec shipped. The symbol->module mapping is read from
    the kernel's own build files, so it tracks the kernel."""
    config, root = clean_tree
    symbol, module = SYMBOL_ONLY_MEMBER
    write(root / "drivers/scsi/Makefile", f"obj-$(CONFIG_{symbol}) += {module}.o\n")
    write(root / f"drivers/scsi/{module}.ko", "")
    result = run_gate(config, root)
    assert result.returncode == FINDINGS, (
        f"the gate passed a build containing {module}.ko while the config was "
        f"clean.\nstdout:\n{result.stdout}"
    )
    assert module in result.stdout


def test_sweep_2_survives_a_rule_split_across_continuation_lines(clean_tree):
    """Kernel Makefiles wrap long object lists. A rule the gate cannot parse
    resolves to no module and reads as absent — a false clean."""
    config, root = clean_tree
    symbol, module = SYMBOL_ONLY_MEMBER
    write(
        root / "drivers/scsi/Makefile",
        f"obj-$(CONFIG_{symbol}) += \\\n\tsome-other.o \\\n\t{module}.o\n",
    )
    write(root / f"drivers/scsi/{module}.ko", "")
    result = run_gate(config, root)
    assert result.returncode == FINDINGS, (
        "a continuation-line obj rule was not parsed, so a built fabricated "
        f"device read as absent.\nstdout:\n{result.stdout}"
    )


def test_sweep_3_refuses_a_fabricated_module_no_symbol_names(clean_tree):
    """The sweep that finds members nobody enumerated. A module with no obj
    rule and no allowlist entry is a refusal — that is exactly how the
    previously-missed members looked."""
    config, root = clean_tree
    write(root / "drivers/misc/widget-mockup.ko", "")
    result = run_gate(config, root)
    assert result.returncode == FINDINGS, (
        f"an unexplained fabricated-device name passed.\nstdout:\n{result.stdout}"
    )
    assert "widget-mockup.ko" in result.stdout


def test_sweep_3_accepts_a_vocabulary_match_that_carries_a_reason(clean_tree):
    """The vocabulary REPORTS; it never disables. dummy.ko is the ordinary
    dummy0 network interface and must not stop a build."""
    config, root = clean_tree
    result = run_gate(config, root)
    assert result.returncode == CLEAN
    assert "allowed" in result.stdout
    assert f"{ALLOWLISTED_REAL_MODULE[1]}.ko" in result.stdout


# ── it refuses just as hard when it cannot measure ──────────────────────────

def test_an_unreadable_class_definition_refuses(clean_tree, tmp_path):
    config, root = clean_tree
    result = run_gate(config, root, class_source=tmp_path / "not-here.py")
    assert_refused_as_unmeasurable(result)


def test_an_implausibly_small_class_refuses(clean_tree, tmp_path):
    """A parse that silently yields almost nothing would let every sweep pass
    for the wrong reason."""
    config, root = clean_tree
    stub = write(tmp_path / "tiny.py", 'TEST_CLASS_EXPLICIT = frozenset({"SCSI_DEBUG"})\n')
    result = run_gate(config, root, class_source=stub)
    assert_refused_as_unmeasurable(result)


def test_a_missing_allowlist_refuses(clean_tree, tmp_path):
    config, root = clean_tree
    result = run_gate(config, root, allowlist=tmp_path / "absent.txt")
    assert_refused_as_unmeasurable(result)


def test_a_malformed_allowlist_line_refuses(clean_tree, tmp_path):
    """Partially applying an allowlist would silently drop the reasons after
    the broken line, turning real modules into findings or worse."""
    config, root = clean_tree
    bad = write(tmp_path / "bad.txt", "dummy.ko: fine\nthis line has no module name\n")
    result = run_gate(config, root, allowlist=bad)
    assert_refused_as_unmeasurable(result)


def test_a_kernel_tree_with_no_built_modules_refuses(tmp_path, clean_tree):
    """Zero modules means the kernel was not compiled or this is the wrong
    tree. Sweeping an empty list would certify a build it never looked at."""
    config, _ = clean_tree
    empty = make_kernel_tree(
        tmp_path / "empty-linux",
        obj_rules=[("Makefile", "obj-$(CONFIG_DUMMY) += dummy.o\n")],
    )
    result = run_gate(config, empty)
    assert_refused_as_unmeasurable(result)


def test_a_config_with_no_enabled_symbols_refuses(clean_tree, tmp_path):
    config, root = clean_tree
    empty_config = write(tmp_path / "empty.config", "# nothing here\n")
    result = run_gate(empty_config, root)
    assert_refused_as_unmeasurable(result)


def test_a_kernel_tree_with_no_build_files_refuses(tmp_path, clean_tree):
    """Without build files no symbol resolves to a module, so every class
    member would read as absent."""
    config, _ = clean_tree
    root = make_kernel_tree(tmp_path / "no-makefiles", modules=["drivers/net/dummy.ko"])
    result = run_gate(config, root)
    assert_refused_as_unmeasurable(result)


# ── the allowlist is a decision record, not a dumping ground ────────────────

def test_every_allowlist_entry_states_a_reason():
    entries = [
        line for line in ALLOWLIST.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert entries, "the allowlist has no entries at all"
    for line in entries:
        module, _, reason = line.partition(":")
        assert module.strip().endswith(".ko"), f"not a module name: {line!r}"
        assert len(reason.strip()) >= 20, (
            f"{module.strip()} is allowed with no real explanation: {reason.strip()!r}. "
            "An entry here asserts a human read the driver and found real hardware."
        )


def test_no_allowlisted_module_is_built_by_a_class_member():
    """A module cannot be both a real device and a member of the fabricated
    class. If one ever is, one of the two records is wrong and the gate would
    be arguing with itself."""
    import ast

    tree = ast.parse(CLASS_SOURCE.read_text())
    klass = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "TEST_CLASS_EXPLICIT":
            klass = set(ast.literal_eval(ast.unparse(node.value.args[0])))
    assert klass, "the class definition did not parse"

    allowed = {
        line.split(":", 1)[0].strip()
        for line in ALLOWLIST.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    # The names are compared after the kbuild transform: an object foo_bar.o
    # becomes foo_bar.ko, and the class is written in Kconfig symbol form.
    collisions = {
        module for module in allowed
        if module[:-3].replace("-", "_").upper() in {s.upper() for s in klass}
    }
    assert not collisions, (
        f"these modules are allowlisted as real AND named by the fabricated class: "
        f"{sorted(collisions)}"
    )
