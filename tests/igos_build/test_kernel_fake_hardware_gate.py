#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The kernel recipe's fake-hardware gate must cover the whole class.

WHAT WENT WRONG, 2026-08-07. The kernel config generator strips a class of
drivers that fabricate devices the machine does not have — a fake SCSI disk, a
fake battery, a mock SoundWire codec. The strip was implemented by OMITTING
those symbols from the generated fragment, and that does not disable anything:
`make olddefconfig` resolves an unstated symbol from its Kconfig default and
from any `imply` pointing at it. Two Kconfigs carry `imply SND_SOC_SDW_MOCKUP`,
so the mock codec came back, reached the produced config at =m, and shipped as a
built module while the delivery said the class was absent.

The fix has two halves and this test guards the seam between them:

  * the generator writes an explicit `# CONFIG_X is not set` line for every
    member of TEST_CLASS_EXPLICIT, so omission is never relied on again;
  * the recipe asserts, after the merge, that no member is enabled in the
    PRODUCED config — the only place the truth is visible.

The two lists live in different languages and different files, so they can
drift. A gate covering only some members is worse than no gate: it reports the
class is absent while some of it is present, which is the exact failure the gate
was written for. This test fails the suite the moment they disagree.

WHY THE LISTS ARE ENUMERATED PER SYMBOL AND NEVER MATCHED BY NAME PATTERN:
sweeping the produced config for names containing DUMMY/TEST/STUB/FAKE matches
37 symbols, and several are load-bearing real features — CONFIG_EFI_STUB is the
UEFI boot stub, CONFIG_DUMMY is the ordinary dummy0 network interface,
CONFIG_PCI_STUB serves VFIO passthrough. A pattern strip would produce an
unbootable kernel. The final test below pins that reasoning down so nobody
"simplifies" the two lists into a pattern later.
"""
import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATOR = REPO_ROOT / "docs/research/kernel_configs/analyze_convergence.py"
RECIPE = REPO_ROOT / "packages/core/linux-kernel/build.sh"

# Real features whose names resemble the class. If any of these is ever added to
# either list, the kernel loses a real capability — EFI_STUB most severely, since
# without it the kernel cannot be booted by UEFI firmware at all.
MUST_NEVER_BE_STRIPPED = (
    "EFI_STUB",
    "DUMMY_CONSOLE",
    "PCI_STUB",
    "PCI_PF_STUB",
    "XEN_PCI_STUB",
    "DUMMY",
    "SND_SEQ_DUMMY",
)


def generator_class():
    """The class as the generator defines it, read from its own source."""
    tree = ast.parse(GENERATOR.read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "TEST_CLASS_EXPLICIT":
            return set(ast.literal_eval(ast.unparse(node.value.args[0])))
    pytest.fail(f"TEST_CLASS_EXPLICIT not found in {GENERATOR}")


def recipe_gate_symbols():
    """The symbols the recipe's post-merge fake-hardware loop checks."""
    text = RECIPE.read_text()
    match = re.search(r"^\s*for _fake in \\\n(.*?);\s*do$", text, re.M | re.S)
    if match is None:
        pytest.fail(f"the `for _fake in` assertion loop was not found in {RECIPE}")
    symbols = set()
    for line in match.group(1).splitlines():
        line = line.strip().rstrip("\\").strip()
        if line:
            symbols.add(line)
    return symbols


def test_the_generator_class_is_not_empty():
    """A parse that silently yields nothing would make every other assertion
    below pass for the wrong reason."""
    assert len(generator_class()) >= 20


def test_the_recipe_gate_is_not_empty():
    assert len(recipe_gate_symbols()) >= 20


def test_the_recipe_gate_covers_every_member_of_the_class():
    """Every symbol the generator forces off must be asserted absent by the
    recipe. An uncovered member can return via `imply` with nothing failing."""
    expected = {f"CONFIG_{s}" for s in generator_class()}
    missing = sorted(expected - recipe_gate_symbols())
    assert not missing, (
        "the kernel recipe's fake-hardware gate does not check these members of "
        f"the class the generator strips: {missing}. Add them to the `for _fake in` "
        "loop in packages/core/linux-kernel/build.sh."
    )


def test_the_recipe_gate_checks_nothing_outside_the_class():
    """The reverse direction. A symbol asserted absent by the recipe but not
    stripped by the generator would fail every build the moment a distro enables
    it, for a reason nobody could find from the generator."""
    expected = {f"CONFIG_{s}" for s in generator_class()}
    extra = sorted(recipe_gate_symbols() - expected)
    assert not extra, (
        "the kernel recipe asserts these symbols absent but the generator does not "
        f"strip them: {extra}. Either add them to TEST_CLASS_EXPLICIT in "
        "docs/research/kernel_configs/analyze_convergence.py or drop them from the loop."
    )


@pytest.mark.parametrize("symbol", MUST_NEVER_BE_STRIPPED)
def test_real_features_are_never_in_either_list(symbol):
    """These have names that resemble the class and are real hardware or
    boot-critical features. EFI_STUB is the UEFI boot stub: stripping it
    produces a kernel UEFI firmware cannot start."""
    assert symbol not in generator_class(), (
        f"CONFIG_{symbol} is a real feature and must never be stripped"
    )
    assert f"CONFIG_{symbol}" not in recipe_gate_symbols(), (
        f"CONFIG_{symbol} is a real feature and must never be asserted absent"
    )


def test_the_generator_emits_an_explicit_disable_rather_than_omitting():
    """The whole defect was that omission does not disable. If the generator
    ever goes back to dropping symbols silently, this fails."""
    text = GENERATOR.read_text()
    assert "is not set" in text, (
        "the generator no longer writes explicit disable lines; omitting a symbol "
        "does not disable it, because olddefconfig resolves it from its Kconfig "
        "default and from any `imply`"
    )


@pytest.mark.parametrize(
    "recipe",
    ["packages/core/linux-kernel", "packages/core/linux-kernel-pass2"],
)
def test_both_kernel_recipes_declare_the_config_fragments_as_source(recipe):
    """A fragment edit must flip the rebuild trigger and the release bump.

    Measured 2026-08-07 before this was declared: editing
    config/kernel/fragments/00-universal-baseline.config left BOTH kernel
    recipes' content fingerprints byte-identical. A fragment-only change was
    therefore invisible to `--skip-built` and to the release auto-bump, so a
    targeted build would skip rebuilding the kernel and ship the previous one
    while the tree claimed new hardware coverage, and no installed system would
    ever see an upgrade. The fragments ARE the kernel's build input — pass 1
    concatenates them into .config, and pass 2 exists specifically to rebuild
    with them — so they belong in source_tree.
    """
    text = (REPO_ROOT / recipe / "package.yml").read_text()
    block = re.search(r"^source_tree:\n((?:[ \t]*[-#].*\n)+)", text, re.M)
    assert block, f"{recipe}/package.yml declares no source_tree"
    entries = [
        line.strip()[1:].strip()
        for line in block.group(1).splitlines()
        if line.strip().startswith("-")
    ]
    assert "config/kernel/fragments" in entries, (
        f"{recipe}/package.yml does not declare config/kernel/fragments in "
        f"source_tree (declared: {entries}). Without it a fragment-only change "
        "does not trigger a kernel rebuild or a release bump."
    )


def test_the_shipped_fragment_carries_a_disable_line_for_every_member():
    """End of the chain: the artifact that the build actually consumes."""
    fragment = (REPO_ROOT / "config/kernel/fragments/00-universal-baseline.config").read_text()
    disabled = set(re.findall(r"^# (CONFIG_[A-Za-z0-9_]+) is not set$", fragment, re.M))
    missing = sorted({f"CONFIG_{s}" for s in generator_class()} - disabled)
    assert not missing, (
        "the shipped baseline fragment has no explicit disable line for: "
        f"{missing}. Regenerate it with docs/research/kernel_configs/analyze_convergence.py."
    )
