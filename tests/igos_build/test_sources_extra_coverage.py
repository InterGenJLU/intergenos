#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Every tree file a build consumes must ride in its corresponding source.

The kernel taught the class (test_kernel_sources_extra.py): its published
source archive carried everything EXCEPT the config fragments that decide what
the kernel is. A 2026-08-11 survey of every build.sh found the same shape in
21 more recipes — payload members installed straight from the repository tree
(kernel lifecycle helpers, forge's trace library, openssh's unit file) and
build-behavior inputs that determine the built bytes without becoming payload
(the lib32 environment/cross/toolchain files).

This test is that survey, made standing. It walks every packages/*/*/build.sh,
extracts `/mnt/intergenos/<path>` references that resolve to real repository
paths, and fails when a recipe with a published source archive consumes one it
does not declare in `sources_extra:`. Without it, the next composed input is a
silent understatement of the published source — a re-survey is the only thing
that would catch it, and re-surveys do not happen on their own.

What is deliberately OUT of scope:
* Recipes with `source: []` — the documented skip class in
  scripts/build-source-archives.py: no source archive is published for them;
  their whole source lives in the public git tree.
* References under the recipe's OWN directory — build-source-archives.py
  always bundles build.sh + package.yml + patches/, and recipe-local files
  travel with the recipe in the git tree.
* References under build/ (build outputs, not inputs) and paths that do not
  exist in the tree (a nonexistent build input fails the build loudly on its
  own; it cannot ship a silently understated archive).
"""
import pathlib
import re

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGES = REPO_ROOT / "packages"

REF_RE = re.compile(r"/mnt/intergenos/([A-Za-z0-9._/@+-]+)")

# Prefixes that are never corresponding-source inputs: build outputs and the
# chroot-side staging areas the orchestrator owns.
IGNORED_PREFIXES = ("build/",)


def _iter_recipes():
    for tier_dir in sorted(PACKAGES.iterdir()):
        if not tier_dir.is_dir():
            continue
        for pkg_dir in sorted(tier_dir.iterdir()):
            if (pkg_dir / "package.yml").exists() and (pkg_dir / "build.sh").exists():
                yield pkg_dir


def _meta(pkg_dir):
    return yaml.safe_load((pkg_dir / "package.yml").read_text()) or {}


def _tree_refs(pkg_dir):
    """Repo-relative paths build.sh references under /mnt/intergenos/,
    comment lines excluded, resolved against the real tree."""
    refs = set()
    for raw in (pkg_dir / "build.sh").read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # Drop an inline trailing comment; a path in prose is not a consumption.
        code = re.split(r"\s+#\s", raw, maxsplit=1)[0]
        for m in REF_RE.finditer(code):
            rel = m.group(1).rstrip("/.")
            if not rel or rel.startswith(IGNORED_PREFIXES):
                continue
            if (REPO_ROOT / rel).exists():
                refs.add(rel)
    return refs


def _covered(rel, declared, pkg_rel):
    if rel.startswith(pkg_rel + "/") or rel == pkg_rel:
        return True                     # recipe-local: travels with the recipe
    if rel in declared:
        return True                     # exact declaration
    if (REPO_ROOT / rel).is_dir():
        # A directory reference is covered when at least one declared entry
        # lives beneath it (per-member completeness is the consuming recipe's
        # own specific test to hold, as the kernel fragments do).
        return any(e.startswith(rel + "/") for e in declared)
    return False


def test_every_consumed_tree_path_is_declared():
    findings = []
    for pkg_dir in _iter_recipes():
        meta = _meta(pkg_dir)
        if not (meta.get("source") or []):
            continue                    # documented skip class: no archive published
        declared = set(meta.get("sources_extra") or [])
        pkg_rel = str(pkg_dir.relative_to(REPO_ROOT))
        for rel in sorted(_tree_refs(pkg_dir)):
            if not _covered(rel, declared, pkg_rel):
                findings.append(f"{pkg_rel}/build.sh consumes {rel!r} (undeclared)")
    assert not findings, (
        "Recipes consume repository-tree inputs their published source archive "
        "will not carry — declare each in the recipe's `sources_extra:` (or, for "
        "a genuine non-input, justify its exclusion in this test):\n  "
        + "\n  ".join(findings)
    )


def test_every_declared_sources_extra_path_exists_repo_wide():
    """The kernel-specific existence check, generalized: a typo'd declaration
    refuses the archive at publish time — the worst moment. Caught here."""
    missing = []
    for pkg_dir in _iter_recipes():
        for entry in (_meta(pkg_dir).get("sources_extra") or []):
            if any(c in entry for c in "*?["):
                missing.append(f"{pkg_dir.name}: glob in entry {entry!r}")
            elif not (REPO_ROOT / entry).exists():
                missing.append(f"{pkg_dir.name}: nonexistent entry {entry!r}")
    assert not missing, "\n".join(missing)


def test_the_scanner_sees_a_known_consumption():
    """Positive control: the instrument must find the kernel's fragment-dir
    reference — an empty scan result must mean coverage, never blindness."""
    kernel = PACKAGES / "core/linux-kernel"
    refs = _tree_refs(kernel)
    assert any(r.startswith("config/kernel") for r in refs), (
        f"scanner found no config/kernel reference in {kernel}/build.sh — "
        "the instrument is blind, every other pass in this file is void"
    )
