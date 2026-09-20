#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The ROCm toolkit's programs answer by name, and its compiler can find its card.

Two decisions live in two different recipes and can drift apart silently, which
is what this file exists to prevent.

1. The compiler driver needs the agent enumerator. `hipcc` and `hipconfig` shell
   out to /opt/rocm/bin/rocm_agent_enumerator to discover the installed card's
   architecture. That program comes from the rocminfo package. Nothing declared
   it, so on a machine carrying only the HIP package every hipcc and hipconfig
   call opened with "sh: /opt/rocm/bin/rocm_agent_enumerator: No such file or
   directory" — at exit code 0, so nothing failed, it just could not detect a
   target. Measured on an installed machine on 2026-09-20.

2. The toolkit's programs must be reachable by name. /opt/rocm/bin holds about
   450 programs and is on no default PATH; four of them (hipcc, hipconfig,
   rocminfo, rocm_agent_enumerator) carry /usr/bin symlinks from their own
   recipes, and everything else — rocm-smi, amd-smi, rocgdb, rocprofv3,
   hipify-perl among them — answered "command not found" on a machine that had
   just installed ten gigabytes of ROCm to get them. The base-metadata package
   owns the /opt/rocm layout, so it ships the PATH drop-in.

   The drop-in APPENDS rather than prepends, and that is not a style choice:
   llama-bench, llama-cli and llama-server exist under BOTH /opt/rocm/bin and
   /usr/bin — the HIP-built copies beside the CPU-built ones. Prepending would
   change which inference engine a person gets by typing llama-cli, silently.
   A future edit that "tidies" the append into a prepend would do exactly that,
   so the direction is asserted here.

The assertions read the recipes rather than restating them, so they stay true
when a recipe is edited for some other reason.
"""
import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parents[2]
HIP_DIR = REPO_ROOT / "packages" / "compute" / "rocm-hip"
CORE_DIR = REPO_ROOT / "packages" / "compute" / "rocm-core"
PROFILE_PATH = "/etc/profile.d/rocm.sh"
ROCM_BIN = "/opt/rocm/bin"
# The three names that exist in both /opt/rocm/bin and /usr/bin, measured on an
# installed machine 2026-09-20. They are why the drop-in appends.
COLLIDING_NAMES = ("llama-bench", "llama-cli", "llama-server")


def recipe(directory: Path) -> dict:
    return yaml.safe_load((directory / "package.yml").read_text(encoding="utf-8"))


def uncommented(text: str) -> str:
    """Drop whole-line comments so an assertion cannot be satisfied by prose."""
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


def core_build_script() -> str:
    return (CORE_DIR / "build.sh").read_text(encoding="utf-8")


def test_the_hip_package_declares_the_agent_enumerators_package():
    runtime = recipe(HIP_DIR).get("dependencies", {}).get("runtime") or []
    assert "rocminfo" in runtime, (
        "rocm-hip no longer declares rocminfo as a runtime dependency; hipcc and "
        "hipconfig call /opt/rocm/bin/rocm_agent_enumerator, which rocminfo ships, "
        "and without it the compiler cannot detect the card's architecture and "
        "prints a missing-file line on every invocation"
    )


def test_the_hip_package_still_ships_the_bare_name_symlinks():
    """Non-login processes have no profile drop-in and rely on these."""
    declared = recipe(HIP_DIR).get("verify_paths") or []
    for path in ("/usr/bin/hipcc", "/usr/bin/hipconfig"):
        assert path in declared, (
            f"{path} is no longer declared; a service subprocess resolves the "
            "compiler by bare name with no login shell and would find nothing"
        )


def test_the_base_package_ships_the_path_drop_in():
    code = uncommented(core_build_script())
    assert PROFILE_PATH.lstrip("/") in code.replace("${DESTDIR}", ""), (
        "the base-metadata recipe no longer writes the PATH drop-in; the "
        "toolkit's programs answer only by full path again"
    )
    declared = recipe(CORE_DIR).get("verify_paths") or []
    assert PROFILE_PATH in declared, (
        "the PATH drop-in is not declared in verify_paths; if it stops landing, "
        "nothing objects"
    )


def test_the_drop_in_appends_and_does_not_prepend():
    """Prepending would shadow the CPU-built inference binaries in /usr/bin."""
    code = uncommented(core_build_script())
    export_lines = [
        line.strip()
        for line in code.splitlines()
        if "export PATH=" in line and ROCM_BIN in line
    ]
    assert export_lines, "the drop-in no longer exports a PATH containing " + ROCM_BIN
    for line in export_lines:
        assert re.search(r"export PATH=\$\{PATH\}:" + re.escape(ROCM_BIN), line), (
            "the drop-in must APPEND /opt/rocm/bin to PATH, not prepend it: "
            f"{', '.join(COLLIDING_NAMES)} exist under both /opt/rocm/bin and "
            "/usr/bin, and prepending silently changes which inference engine a "
            f"person gets by typing llama-cli. Offending line: {line}"
        )


def test_the_drop_in_does_not_grow_the_path_on_every_shell():
    """A drop-in without the guard appends again in every nested login shell."""
    code = uncommented(core_build_script())
    assert re.search(r'case ":\$\{PATH\}:" in', code), (
        "the drop-in no longer guards against adding the directory twice"
    )
    assert f"*:{ROCM_BIN}:*" in code, (
        "the guard no longer tests for the directory already being on PATH"
    )


def test_the_drop_in_is_owned_payload_and_not_a_post_install_hook():
    """A hook that writes into /etc leaves a file no package owns."""
    shipped = sorted(p.name for p in CORE_DIR.rglob("*") if p.is_file())
    assert not any(
        name.startswith("post_install") or name == "post_install.sh"
        for name in shipped
    ), f"the base-metadata recipe carries a post-install script: {shipped}"
