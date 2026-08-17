#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""check-fabricated-devices.py — fail-closed fabricated-device kernel build gate.

A driver that fabricates a device the machine does not have is a masking
primitive on a security-first operating system: it makes a broken system look
healthy to everything that inspects it, including this repository's own hardware
smoke checks. The sharpest measured case is the fake ALSA sound card — the smoke
check tests audio by counting registered cards, so a fabricated card reports
working audio on a machine whose codec is dead.

WHY THIS GATE EXISTS RATHER THAN THE CONFIG CHECK ALONE (decided 2026-08-11,
from the 2026-08-07 kernel-coverage work). The kernel recipe already asserts that
no member of the enumerated class is enabled in the produced .config. That check
is necessary and NOT sufficient, and the shortfall was measured, not predicted:

  * A config check can only look for symbol names somebody already wrote down.
    Sweeping the BUILT MODULE LIST of a real kernel build found ten further
    fabricated-device drivers that no config-level check could have seen,
    including a fake block device, a fake ALSA PCM device, a simulated vDPA
    network device and a fabricated infrared remote.
  * A symbol name read off a module filename can simply be wrong. The module
    vdpa_sim_blk.ko is built by CONFIG_VDPA_SIM_BLOCK; guessing VDPA_SIM_BLK
    from the filename names a symbol the kernel does not have, so the config
    check would have passed while the module was in the build.
  * A module can have no Kconfig symbol at all. ddbridge-dummy-fe.ko is built
    unconditionally as a component of a real DVB card driver: no config value
    can remove it, and no config-level check can see it.

So the gate reads the artifact, not the request. It runs three sweeps, because
each one catches what the others structurally cannot, and it REFUSES rather than
reports: a build that fabricates devices does not proceed.

  SWEEP 1 — the produced .config, for every enumerated member of the class.
            This is the same assertion the recipe makes before compiling; it is
            repeated here against the config the build actually consumed so the
            gate's verdict does not depend on an earlier step having run.

  SWEEP 2 — the built module list, for every enumerated member of the class.
            The symbol-to-module mapping is resolved from the kernel's OWN
            Makefiles (obj-$(CONFIG_X) += foo.o), so it tracks the kernel rather
            than a hand-kept list of module names that would rot.

  SWEEP 3 — the built module list, for the whole fabricated-device VOCABULARY.
            This is the sweep that finds members nobody enumerated. Every hit
            must carry a stated reason in the allowlist; an unrecognised hit is
            a refusal, never a silent pass.

⚠️ THE CLASS IS ENUMERATED PER SYMBOL AND NEVER STRIPPED BY NAME PATTERN.
Sweeping the produced config for names containing DUMMY/TEST/STUB/FAKE matches
37 symbols and several are load-bearing real features: CONFIG_EFI_STUB is the
UEFI boot stub, CONFIG_DUMMY is the ordinary dummy0 network interface,
CONFIG_PCI_STUB serves VFIO passthrough. Sweep 3 uses the vocabulary to REPORT,
never to disable — which is why every one of its hits needs a human-written
reason or the build stops.

FAIL-CLOSED IN EVERY DIRECTION. The gate exits 2 — refusing the build — when it
cannot measure: an unreadable config, an unreadable or implausibly small class
list, a kernel source tree that is not there, a module list with nothing in it,
a missing allowlist. An instrument that cannot see anything must never report
that it saw nothing wrong.

Exit codes:
    0  every sweep clean — no fabricated device in the config or the build
    1  findings — at least one fabricated device is present
    2  the gate could not measure what it was asked to measure

Usage:
    scripts/check-fabricated-devices.py \
        --config /path/to/produced/.config \
        --kernel-source /path/to/kernel/build/tree

    scripts/check-fabricated-devices.py \
        --config <config> --module-list <file with one module path per line>
"""

import argparse
import ast
import re
import sys
from pathlib import Path

# A class list this short means the parse found something other than the real
# class (a renamed variable, a truncated file), and every sweep below would then
# pass for the wrong reason. The measured class carries 41 members; the floor is
# set well below that so legitimate curation is possible, and well above zero so
# an empty parse cannot certify a clean build.
MIN_CLASS_MEMBERS = 20

# The vocabulary sweep's word list. These words are how the fabricated-device
# class NAMES ITSELF in the kernel tree. Matching a word here is not evidence
# that a module is fabricated — it is the trigger for requiring a reason.
FABRICATED_VOCABULARY = (
    "mock", "dummy", "fake", "stub", "kunit", "test", "loopback",
    "simulat", "emul", "_sim", "sim_", "virt_", "null_", "sample",
)

RE_VOCABULARY = re.compile(
    r"(?:^|/)[^/]*(?:" + "|".join(FABRICATED_VOCABULARY) + r")[^/]*\.ko$",
    re.IGNORECASE,
)

# obj-$(CONFIG_FOO) += bar.o   /   obj-$(CONFIG_FOO) := bar.o baz.o
# The right-hand side may carry several objects and may continue across lines.
RE_OBJ_RULE = re.compile(
    r"obj-\$\(CONFIG_([A-Za-z0-9_]+)\)\s*[+:]?=\s*(?P<objects>[^\n]*)"
)

# The kernel names its build files in four ways; missing one of them would make
# a symbol look like it builds nothing, which reads as "absent" — a false clean.
MAKEFILE_NAMES = ("Makefile", "Kbuild")
MAKEFILE_SUFFIXES = (".mk",)

RE_ALLOWLIST_ENTRY = re.compile(r"^(?P<module>[A-Za-z0-9_.+-]+\.ko)\s*:\s*(?P<reason>\S.*)$")


class Unmeasurable(Exception):
    """The gate cannot see what it was asked to look at. Always a refusal."""


def read_class(class_source: Path) -> set:
    """The fabricated-device class, read from its single canonical definition.

    The class is defined once, as TEST_CLASS_EXPLICIT in the kernel config
    generator, because that is the code that writes the explicit
    "# CONFIG_X is not set" lines into the shipped fragment. Reading it here
    rather than restating it means the gate cannot drift away from what the
    build is actually asking the kernel for — a gate that checked a stale
    subset would report the class absent while part of it was present, which
    is the exact failure this gate exists to stop.
    """
    if not class_source.is_file():
        raise Unmeasurable(
            f"the fabricated-device class definition is not readable at {class_source}. "
            "The gate refuses rather than assume an empty class."
        )
    try:
        tree = ast.parse(class_source.read_text())
    except (OSError, SyntaxError) as exc:
        raise Unmeasurable(f"cannot parse {class_source}: {exc}")

    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "TEST_CLASS_EXPLICIT":
            try:
                members = set(ast.literal_eval(ast.unparse(node.value.args[0])))
            except (ValueError, SyntaxError, AttributeError, IndexError) as exc:
                raise Unmeasurable(
                    f"TEST_CLASS_EXPLICIT in {class_source} is not a literal set: {exc}"
                )
            if len(members) < MIN_CLASS_MEMBERS:
                raise Unmeasurable(
                    f"TEST_CLASS_EXPLICIT in {class_source} holds only {len(members)} "
                    f"members; at least {MIN_CLASS_MEMBERS} were expected. A parse that "
                    "yields almost nothing would let every sweep pass for the wrong reason."
                )
            return members

    raise Unmeasurable(f"TEST_CLASS_EXPLICIT is not defined in {class_source}")


def read_allowlist(path: Path) -> dict:
    """Module basename -> the stated reason it is a real device, not a fabricated one."""
    if not path.is_file():
        raise Unmeasurable(
            f"the module allowlist is not readable at {path}. Sweep 3 cannot tell a "
            "real feature from a fabricated device without it, so the gate refuses."
        )
    allowed = {}
    for lineno, raw in enumerate(path.read_text().splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = RE_ALLOWLIST_ENTRY.match(line)
        if not match:
            raise Unmeasurable(
                f"{path}:{lineno}: cannot read this line as '<module>.ko: <reason>': {raw!r}. "
                "A malformed allowlist is treated as unmeasurable rather than partially applied."
            )
        allowed[match.group("module")] = match.group("reason").strip()
    return allowed


def read_enabled_config_symbols(config: Path) -> set:
    """Every CONFIG_ symbol set to y or m in the produced config."""
    if not config.is_file():
        raise Unmeasurable(f"the produced kernel config is not readable at {config}")
    enabled = set()
    for line in config.read_text(errors="replace").splitlines():
        line = line.strip()
        if line.startswith("CONFIG_") and line.endswith(("=y", "=m")):
            enabled.add(line.rsplit("=", 1)[0])
    if not enabled:
        raise Unmeasurable(
            f"{config} contains no enabled symbols at all. That is not a kernel config "
            "this build produced, and an empty read must never certify a clean result."
        )
    return enabled


def collect_module_list(kernel_source: Path) -> list:
    """Every built module in the kernel tree, as tree-relative paths."""
    if not kernel_source.is_dir():
        raise Unmeasurable(f"the kernel source tree is not a directory: {kernel_source}")
    modules = sorted(
        str(p.relative_to(kernel_source)) for p in kernel_source.rglob("*.ko")
    )
    if not modules:
        raise Unmeasurable(
            f"no built modules were found under {kernel_source}. Either the kernel has "
            "not been compiled yet or this is the wrong tree; a module sweep over an "
            "empty list would certify a clean build it never looked at."
        )
    return modules


def read_module_list(path: Path) -> list:
    if not path.is_file():
        raise Unmeasurable(f"the module list is not readable at {path}")
    modules = sorted(
        line.strip() for line in path.read_text().splitlines() if line.strip()
    )
    if not modules:
        raise Unmeasurable(
            f"{path} lists no modules. An empty list would certify a clean build it "
            "never looked at."
        )
    return modules


def build_symbol_object_map(kernel_source: Path) -> dict:
    """Map every Kconfig symbol to the module object basenames its rules build.

    Read from the kernel's OWN build files, in ONE pass over the tree, so the
    mapping tracks the kernel rather than a hand-kept list of module names that
    would rot — and so a 41-symbol sweep does not walk a five-gigabyte tree
    forty-one times.

    A symbol absent from the result builds nothing directly: it is a menu gate,
    or it only selects other symbols. That is a legitimate outcome, not a
    finding — sweep 3 is what covers modules no symbol names.
    """
    if not kernel_source.is_dir():
        raise Unmeasurable(f"the kernel source tree is not a directory: {kernel_source}")

    mapping = {}
    seen_files = 0
    for path in kernel_source.rglob("*"):
        if not path.is_file():
            continue
        if path.name not in MAKEFILE_NAMES and path.suffix not in MAKEFILE_SUFFIXES:
            continue
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        seen_files += 1
        # Join continuation lines before matching: a rule written as
        #   obj-$(CONFIG_X) += \
        #           foo.o bar.o
        # would otherwise resolve to no objects at all and read as absent.
        text = text.replace("\\\n", " ")
        for match in RE_OBJ_RULE.finditer(text):
            symbol = match.group(1)
            for token in match.group("objects").split():
                if token.endswith(".o"):
                    mapping.setdefault(symbol, set()).add(Path(token).name[:-2])

    if seen_files == 0:
        raise Unmeasurable(
            f"no kernel build files were found under {kernel_source}. Without them no "
            "symbol resolves to a module, and every symbol would read as absent."
        )
    return mapping


def module_basenames(modules) -> dict:
    """Basename -> the first full path carrying it, for reporting."""
    index = {}
    for path in modules:
        index.setdefault(Path(path).name, path)
    return index


def main() -> int:
    default_repo_root = Path(__file__).resolve().parent.parent

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", required=True, type=Path,
                        help="the produced kernel .config the build consumed")
    parser.add_argument("--kernel-source", type=Path,
                        help="the kernel build tree to scan for built modules")
    parser.add_argument("--module-list", type=Path,
                        help="a file of built module paths, one per line, instead of scanning")
    parser.add_argument("--repo-root", type=Path, default=default_repo_root,
                        help="repository root the class definition and allowlist are read from")
    parser.add_argument("--class-source", type=Path,
                        help="override the path to the canonical class definition")
    parser.add_argument("--allowlist", type=Path,
                        help="override the path to the real-module allowlist")
    parser.add_argument("--makefile-source", type=Path,
                        help="kernel source tree to resolve symbol->module from, when "
                             "--module-list is used without --kernel-source")
    args = parser.parse_args()

    class_source = args.class_source or (
        args.repo_root / "docs/research/kernel_configs/analyze_convergence.py"
    )
    allowlist_path = args.allowlist or (
        args.repo_root / "config/kernel/fabricated-device-module-allowlist.txt"
    )

    print("=" * 70)
    print("FABRICATED-DEVICE BUILD GATE")
    print("=" * 70)

    try:
        if not args.kernel_source and not args.module_list:
            raise Unmeasurable("one of --kernel-source or --module-list is required")

        klass = read_class(class_source)
        allowlist = read_allowlist(allowlist_path)
        enabled = read_enabled_config_symbols(args.config)
        modules = (
            read_module_list(args.module_list) if args.module_list
            else collect_module_list(args.kernel_source)
        )
        makefile_source = args.makefile_source or args.kernel_source
        if makefile_source is None:
            raise Unmeasurable(
                "sweep 2 resolves each class member to the module it builds by reading "
                "the kernel's own build files, so a kernel source tree is required: pass "
                "--kernel-source, or --makefile-source alongside --module-list."
            )
        symbol_objects = build_symbol_object_map(makefile_source)
    except Unmeasurable as exc:
        print("", file=sys.stderr)
        print("  REFUSING THE BUILD — the fabricated-device gate cannot measure:", file=sys.stderr)
        print(f"  {exc}", file=sys.stderr)
        print("", file=sys.stderr)
        return 2

    print(f"class definition : {class_source} ({len(klass)} members)")
    print(f"module allowlist : {allowlist_path} ({len(allowlist)} entries)")
    print(f"produced config  : {args.config} ({len(enabled)} symbols enabled)")
    print(f"built modules    : {len(modules)}")
    print("")

    findings = []

    # ── SWEEP 1 — the produced config ────────────────────────────────────────
    print("-" * 70)
    print("SWEEP 1 — the enumerated class against the PRODUCED CONFIG")
    print("-" * 70)
    config_hits = sorted(s for s in klass if f"CONFIG_{s}" in enabled)
    for symbol in config_hits:
        print(f"  ENABLED (finding)  CONFIG_{symbol}")
        findings.append(
            f"CONFIG_{symbol} is enabled in the produced config. Being absent from a "
            "fragment does not disable a symbol: olddefconfig resolves it from its "
            "Kconfig default and from any 'imply' pointing at it, so it must be an "
            "explicit '# CONFIG_X is not set' line in a fragment."
        )
    print(f"  checked {len(klass)} class members, {len(config_hits)} enabled")
    print("")

    # ── SWEEP 2 — the enumerated class against the built modules ─────────────
    print("-" * 70)
    print("SWEEP 2 — the enumerated class against the BUILT MODULE LIST")
    print("-" * 70)
    by_basename = module_basenames(modules)
    resolved = 0
    module_hits = 0
    for symbol in sorted(klass):
        objects = symbol_objects.get(symbol, set())
        if not objects:
            continue
        resolved += 1
        for obj in sorted(objects):
            built = by_basename.get(f"{obj}.ko")
            if built:
                print(f"  BUILT (finding)    CONFIG_{symbol} -> {built}")
                module_hits += 1
                findings.append(
                    f"CONFIG_{symbol} built the module {built}, which fabricates a "
                    "device this machine does not have."
                )
    print(f"  {resolved} class members build a module directly; {module_hits} of those modules are in the build")
    print("")

    # ── SWEEP 3 — the vocabulary against the built modules ───────────────────
    print("-" * 70)
    print("SWEEP 3 — the fabricated-device VOCABULARY against the BUILT MODULE LIST")
    print("-" * 70)
    print("  Every hit is printed. A hit passes only with a stated reason; an")
    print("  unrecognised hit is a refusal, because that is exactly how the")
    print("  previously-missed members looked.")
    vocab_hits = [m for m in modules if RE_VOCABULARY.search(m)]
    unexplained = 0
    for path in vocab_hits:
        name = Path(path).name
        reason = allowlist.get(name)
        if reason:
            print(f"  allowed   {path}")
            print(f"            {reason}")
        else:
            print(f"  FINDING   {path}   (fabricated-device vocabulary, no stated reason)")
            unexplained += 1
            findings.append(
                f"the built module {path} carries the fabricated-device vocabulary and has "
                f"no entry in {allowlist_path}. Either it fabricates a device — in which "
                "case its symbol joins the class and the fragment must disable it — or it "
                "is a real feature, in which case add it to the allowlist with the reason "
                "it is real."
            )
    print("")
    print(f"  vocabulary hits {len(vocab_hits)}, unexplained {unexplained}")
    print("")

    print("=" * 70)
    if findings:
        print(f"REFUSING THE BUILD — {len(findings)} fabricated-device finding(s)")
        print("=" * 70)
        for finding in findings:
            print(f"  * {finding}", file=sys.stderr)
        print("", file=sys.stderr)
        print("  A driver that fabricates a device the machine does not have makes a", file=sys.stderr)
        print("  broken system look healthy to everything that inspects it, including", file=sys.stderr)
        print("  this repository's own hardware smoke checks. Refusing to build it.", file=sys.stderr)
        print("", file=sys.stderr)
        return 1

    print("CLEAN — no fabricated device in the produced config or the built modules")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
