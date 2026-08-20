#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The Rule 21 stub gate must read documentation, not only executable surfaces.

Origin, measured: packages/extra/nvidia/docs/KERNEL-CMDLINE.md stated that a
`post-remove.sh` hook rebuilds the UKI after a removal. The recipe installs no
post-remove script and pkm has no post-remove runner, so a reader was told the
signed cmdline had been cleared when it had not. The claim was corrected by
hand in bddd3761; scripts/check-aspirational-stubs.py passed the tree with the
false claim in it, both before and after, because it never scanned docs.

Two things that origin case teaches, and that these tests hold:

1. The false claim was written as a BARE FILENAME in backticks, not as an
   absolute path. A path-only matcher does not see it. The matcher keys on the
   lifecycle-hook NAME SHAPE — (pre|post)-(install|remove|upgrade) — so the
   citation form does not decide whether the claim is checked.

2. `post-remove.sh` EXISTS at packages/extra/nvidia/hooks/post-remove.sh. A
   check for "does this file exist in the tree" therefore resolves it and finds
   nothing wrong. What makes the claim false is that build.sh never installs it
   and pkm never runs it, so the question is what the package INSTALLS and what
   pkm RUNS — the same install-manifest reasoning the gate already applies to
   its other surfaces.
"""
import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE_PATH = REPO_ROOT / "scripts" / "check-aspirational-stubs.py"


@pytest.fixture(scope="module")
def gate():
    spec = importlib.util.spec_from_file_location("stub_gate", GATE_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["stub_gate"] = mod
    spec.loader.exec_module(mod)
    return mod


# --- the reconstructed origin case, verbatim from before bddd3761 -----------
DOC_BEFORE_THE_FIX = """\
`pkm remove nvidia` removes the package's shipped files — including
`/etc/kernel/cmdline.d/40-nvidia.conf` and
`/etc/modprobe.d/nvidia-nouveau-blacklist.conf` — as part of its standard
file-removal walk. The `pre-remove.sh` hook first stops NVIDIA services,
unloads the kernel modules, and purges the built `.ko` files under
`/lib/modules/*/extra/nvidia/`. After the files are gone, the
`post-remove.sh` hook triggers a UKI rebuild so the signed `.cmdline`
section no longer carries the nvidia parameters. On next boot, nouveau is
the active GPU driver.
"""

DOC_AFTER_THE_FIX = """\
`pkm remove nvidia` removes the package's shipped files as part of its
standard file-removal walk. Before that walk, `pkm` runs the package's
pre-remove hook (`/var/lib/pkm/hooks/nvidia/pre-remove`), which stops the
NVIDIA services, unloads the kernel modules, and purges the built `.ko`
files under `/lib/modules/*/extra/nvidia/`.

The UKI is NOT rebuilt as part of the removal.
"""


class TestLifecycleHookNameShape:
    def test_lifecycle_names_match(self, gate):
        for name in ("pre-remove", "post-install", "post-remove",
                     "pre-install", "post-upgrade", "pre-upgrade"):
            assert gate.is_lifecycle_hook_name(name), name

    def test_helper_script_names_do_not_match(self, gate):
        # Measured on the real tree: these are installed under
        # /var/lib/pkm/hooks/nvidia/ but are helpers invoked BY a lifecycle
        # hook, not lifecycle hooks. rebuild-modules is the one that proves a
        # ".sh suffix means helper" rule would be wrong — it carries no suffix.
        for name in ("rebuild-modules", "sign-module.sh",
                     "check-hardware.sh", "remove", "install", "postinstall"):
            assert not gate.is_lifecycle_hook_name(name), name

    def test_the_suffix_is_not_what_decides(self, gate):
        assert gate.is_lifecycle_hook_name("post-remove.sh")
        assert gate.is_lifecycle_hook_name("post-remove")


class TestDocHookClaimExtraction:
    def test_bare_backticked_filename_is_extracted(self, gate):
        claims = {c for c, _, _ in gate.extract_doc_hook_claims(DOC_BEFORE_THE_FIX)}
        assert "post-remove" in claims, (
            "the origin case cites the hook as a bare backticked filename; a "
            "matcher that only reads absolute paths cannot see it")
        assert "pre-remove" in claims

    def test_absolute_hook_path_is_extracted(self, gate):
        claims = {c for c, _, _ in gate.extract_doc_hook_claims(DOC_AFTER_THE_FIX)}
        assert "pre-remove" in claims

    def test_a_bare_name_before_the_word_hook_is_a_claim(self, gate):
        """The origin document's own hook script states the same false claim in
        bare words: "nvidia post-remove — fires at pkm remove time". A matcher
        that reads only backticks and paths cannot see that form."""
        claims = {c for c, _, _ in gate.extract_doc_hook_claims(
            "After the walk, the post-remove hook rebuilds the image.")}
        assert claims == {"post-remove"}
        plural = {c for c, _, _ in gate.extract_doc_hook_claims(
            "Its post-install hooks run at install time.")}
        assert plural == {"post-install"}

    def test_a_path_citation_carries_its_owning_package(self, gate):
        """A document may correctly describe ANOTHER package's hook. The path
        forms name their owner, and the owner is what the claim is checked
        against — otherwise a correct cross-package citation reads as false."""
        claims = gate.extract_doc_hook_claims(
            "Run `/var/lib/pkm/hooks/linux-kernel/post-install` by hand.")
        assert [(c, o) for c, _, o in claims] == [
            ("post-install", "linux-kernel")]
        bare = gate.extract_doc_hook_claims("The `post-install` hook runs.")
        assert [(c, o) for c, _, o in bare] == [("post-install", None)], (
            "a bare name names no owner, so it is a claim about the citing "
            "package's own hook")

    def test_prose_about_removal_is_not_a_claim(self, gate):
        # Docs speak loosely. None of these names a lifecycle hook.
        for prose in (
            "Remove the package with `pkm remove nvidia` before rebooting.",
            "The post-installation steps are described above.",
            "This runs after installation and before removal.",
            "See the install notes for the pre-flight checklist.",
        ):
            assert gate.extract_doc_hook_claims(prose) == [], prose


def _recipe(root, tier, pkg, build_sh):
    """Write a synthetic recipe tree and return its packages/ root."""
    d = root / "packages" / tier / pkg
    d.mkdir(parents=True, exist_ok=True)
    (d / "build.sh").write_text(build_sh)
    return root


class TestInstallDetectionAndAttribution:
    def test_a_mention_is_not_an_installation(self, gate, tmp_path):
        """A build.sh that only NAMES a hook path installs nothing. Reading a
        mention as an installation would let the gate resolve a documentation
        claim against a file that never reaches a system — the exact silent
        pass this gate exists to prevent."""
        _recipe(tmp_path, "extra", "sample",
                'do_install() {\n'
                '    echo "the hook lives at /var/lib/pkm/hooks/sample/post-install"\n'
                '}\n')
        assert gate.collect_installed_pkm_hooks(tmp_path) == {}

    def test_a_destination_on_a_continuation_line_is_an_installation(self, gate,
                                                                     tmp_path):
        """Every recipe in this tree writes the install destination on its own
        backslash continuation line, so a line-at-a-time read sees a path with
        no command attached to it."""
        _recipe(tmp_path, "extra", "sample",
                'do_install() {\n'
                '    install -m 755 "$SRC/hooks/post-install.sh" \\\n'
                '        "$DESTDIR/var/lib/pkm/hooks/sample/post-install"\n'
                '}\n')
        assert gate.collect_installed_pkm_hooks(tmp_path) == {
            "sample": {"post-install"}}

    def test_a_commented_out_install_is_not_an_installation(self, gate, tmp_path):
        _recipe(tmp_path, "extra", "sample",
                'do_install() {\n'
                '    # install -m 755 x "$DESTDIR/var/lib/pkm/hooks/sample/post-install"\n'
                '}\n')
        assert gate.collect_installed_pkm_hooks(tmp_path) == {}

    def test_a_nested_document_is_attributed_to_its_package(self, gate, tmp_path):
        """Taken from the path's position under packages/, not from the
        immediate parent directory: a document one level deeper would otherwise
        be checked against a package named "docs", which exists nowhere."""
        pkg_root = tmp_path / "packages"
        doc = pkg_root / "extra" / "sample" / "docs" / "nested" / "NOTE.md"
        doc.parent.mkdir(parents=True)
        doc.write_text("The `post-remove.sh` hook runs after removal.\n")
        assert gate.package_of(doc, pkg_root) == "sample"
        (pkg_root / "extra" / "sample" / "build.sh").write_text("do_install() { :; }\n")
        findings = gate.scan_documentation(tmp_path)
        assert [(f[2], f[3]) for f in findings] == [
            ("post-remove", "sample installs no post-remove hook")]

    def test_a_correct_cross_package_citation_is_not_a_finding(self, gate,
                                                              tmp_path):
        _recipe(tmp_path, "core", "other",
                'do_install() {\n'
                '    install -m 755 h "$DESTDIR/var/lib/pkm/hooks/other/post-install"\n'
                '}\n')
        _recipe(tmp_path, "extra", "sample", 'do_install() { :; }\n')
        doc = tmp_path / "packages" / "extra" / "sample" / "docs" / "N.md"
        doc.parent.mkdir(parents=True)
        doc.write_text("Run `/var/lib/pkm/hooks/other/post-install` to refresh.\n")
        assert gate.scan_documentation(tmp_path) == []

    def test_a_hook_script_without_a_suffix_is_still_read(self, gate, tmp_path):
        """Hook scripts install unsuffixed, and a recipe may carry them that
        way. A comment header that describes a hook nothing installs misleads
        the next author whatever the filename ends in."""
        _recipe(tmp_path, "extra", "sample", 'do_install() { :; }\n')
        hooks = tmp_path / "packages" / "extra" / "sample" / "hooks"
        hooks.mkdir()
        (hooks / "post-install").write_text(
            "#!/bin/sh\n# Paired with the `post-remove` hook.\n")
        findings = gate.scan_documentation(tmp_path)
        assert ("post-remove", "sample installs no post-remove hook") in [
            (f[2], f[3]) for f in findings]


class TestAgainstTheRealTree:
    def test_pkm_runs_exactly_the_hooks_the_gate_believes_it_runs(self, gate):
        """Derived from pkm's own source, so the gate's list cannot fall behind
        it silently — the same self-policing shape the source-tree coverage
        gate uses for its roots list."""
        found = set()
        for src in (REPO_ROOT / "pkm").glob("*.py"):
            for m in re.finditer(r'["\'](?:pre|post)-(?:install|remove|upgrade)["\']',
                                 src.read_text(errors="replace")):
                found.add(m.group(0).strip("'\""))
        assert found, "parsed zero hook names from pkm/ — the parse is wrong, not the tree"
        assert found == set(gate.PKM_HOOKS_RUN), (
            f"pkm invokes {sorted(found)} but the gate believes it invokes "
            f"{sorted(gate.PKM_HOOKS_RUN)}")

    def test_the_orphaned_nvidia_post_remove_script_is_detected(self, gate):
        """The true positive, on the real tree: post-remove.sh sits in the
        recipe's hooks/ dir, build.sh never installs it, and pkm has no
        post-remove runner. It is what made the false doc claim look plausible.
        """
        orphans = gate.find_orphan_hook_scripts(REPO_ROOT)
        assert ("nvidia", "post-remove.sh") in {(p, n) for p, n, _ in orphans}

    def test_every_hook_allowlist_entry_names_a_file_that_exists(self, gate):
        """An allowlist entry is a written decision about a real file. When the
        file goes, the entry must go with it — otherwise the allowlist quietly
        widens the gate's blind spot, which is the failure this gate exists to
        prevent."""
        allowlist_path = (REPO_ROOT / "config"
                          / "aspirational-stub-hook-allowlist.txt")
        entries = gate.load_hook_allowlist(allowlist_path)
        assert entries, "the allowlist parsed to zero entries — the parse is wrong"
        for entry in sorted(entries):
            pkg, _, script = entry.partition("/")
            matches = list((REPO_ROOT / "packages").glob(f"*/{pkg}/hooks/{script}"))
            assert matches, (
                f"allowlist entry {entry!r} names no file in the tree; delete "
                f"the entry or restore the file")

    def test_a_missing_hook_allowlist_is_an_operational_failure(self, gate, tmp_path):
        """Fail-closed. A gate that cannot read its own scope must say so, not
        scan a wider or narrower set in silence."""
        with pytest.raises(FileNotFoundError):
            gate.load_hook_allowlist(tmp_path / "not-here.txt")

    def test_a_named_but_missing_path_allowlist_is_refused(self, gate, tmp_path):
        """Same fail-closed rule as the hook allowlist: a scope file the caller
        NAMED and the gate cannot read is an operational failure. Omitting the
        argument is a different thing and stays an empty allowlist."""
        with pytest.raises(FileNotFoundError):
            gate.load_allowlist(tmp_path / "not-here.txt")
        assert gate.load_allowlist(None) == set()

    def test_installed_lifecycle_hooks_are_all_runnable(self, gate):
        installed = gate.collect_installed_pkm_hooks(REPO_ROOT)
        assert installed, "parsed zero installed hooks — the parse is wrong"
        for pkg, names in installed.items():
            for name in names:
                if gate.is_lifecycle_hook_name(name):
                    assert name in gate.PKM_HOOKS_RUN, (
                        f"{pkg} installs lifecycle hook {name!r}, which pkm "
                        f"never runs")


class TestGateIsWiredIntoValidate:
    """The gate must have an automatic caller.

    Origin: from 2026-05-15 to 2026-08-19 the gate existed and passed every
    hand-firing while docs/operations/README.md stated continuous gating was
    in place — a Rule 21 shape about the Rule 21 gate itself. Wired into
    phase_validate 2026-08-19. This test holds the wiring: the orchestrator
    must invoke the gate and fail closed on its exit status, in the same
    PIPESTATUS form as the sibling validate gates.
    """

    def test_build_orchestrator_fires_the_gate_fail_closed(self):
        build_sh = (REPO_ROOT / "scripts" / "build-intergenos.sh").read_text()
        call = re.search(
            r'python3 "\$\{SCRIPTS\}/check-aspirational-stubs\.py"'
            r'.*?\n\s*if \[ "\$\{PIPESTATUS\[0\]\}" -ne 0 \];.*?return 1',
            build_sh,
            re.DOTALL,
        )
        assert call, (
            "scripts/build-intergenos.sh no longer invokes "
            "check-aspirational-stubs.py with a fail-closed PIPESTATUS check "
            "— the gate has lost its automatic caller and "
            "docs/operations/README.md's continuous-gating statement is "
            "false again")
