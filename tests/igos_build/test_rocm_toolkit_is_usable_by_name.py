#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The ROCm toolkit's programs answer by name, and its compiler can find its card.

Three decisions live in three different recipes and can drift apart silently,
which is what this file exists to prevent.

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

3. The compiler has to be installed where its own driver looks. The AMD clang
   driver works out the ROCm root from the directory it is running out of: it
   strips a trailing "bin", then strips one more component if that component is
   named "llvm" (DeduceROCmPath, clang/lib/Driver/ToolChains/AMDGPU.cpp). From
   /opt/rocm/llvm/bin that gives /opt/rocm and everything is found. From the
   /opt/rocm/lib/llvm/bin this project used to install to, it gave /opt/rocm/lib
   — a directory with no include/ and no lib/ under it — and the documented
   command `hipcc file.hip -o file` failed with "'hip/hip_runtime.h' file not
   found". Measured on an installed machine on 2026-09-20. A symlink at
   /opt/rocm/llvm does not fix it, because the driver resolves its own path to
   the real directory first; the install prefix itself has to be the right one.
   /opt/rocm/lib/llvm stays as a compatibility symlink pointing back, because
   about twenty downstream compute recipes and two installed files name the
   compiler under the old path.

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
LLVM_DIR = REPO_ROOT / "packages" / "compute" / "rocm-llvm"
TOOLCHAIN_PREFIX = "/opt/rocm/llvm"
COMPAT_LINK = "/opt/rocm/lib/llvm"
# Top-level directory names the package manager's UsrMerge remap rewrites when a
# relative symlink target begins with one of them.
USRMERGE_NAMES = ("lib", "lib64", "bin", "sbin")
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


def llvm_build_script() -> str:
    return (LLVM_DIR / "build.sh").read_text(encoding="utf-8")


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


def test_the_compiler_installs_where_its_driver_deduces_the_rocm_root():
    """Installing under /opt/rocm/lib/llvm makes the driver deduce /opt/rocm/lib."""
    code = uncommented(llvm_build_script())
    assert f"-DCMAKE_INSTALL_PREFIX={TOOLCHAIN_PREFIX} " in code, (
        f"the compiler recipe no longer installs to {TOOLCHAIN_PREFIX}; its driver "
        "strips a trailing bin and then one component named llvm to find the ROCm "
        "root, so any other prefix makes `hipcc file.hip` fail to find "
        "hip/hip_runtime.h with no --rocm-path given"
    )
    assert "-DCMAKE_INSTALL_PREFIX=/opt/rocm/lib/llvm" not in code, (
        "the compiler recipe installs to /opt/rocm/lib/llvm again; that is the "
        "layout the documented compile command cannot work under"
    )


def test_the_old_compiler_path_stays_as_a_compatibility_symlink():
    """Around twenty compute recipes still name the compiler under the old path."""
    code = uncommented(llvm_build_script())
    assert f'ln -sv ../llvm "${{DESTDIR}}{COMPAT_LINK}"' in code, (
        f"the compiler recipe no longer ships {COMPAT_LINK} as a symlink back to "
        f"{TOOLCHAIN_PREFIX}; every downstream recipe that names the compiler "
        "under the old path would stop finding it"
    )
    declared = recipe(LLVM_DIR).get("verify_paths") or []
    assert COMPAT_LINK in declared, (
        f"{COMPAT_LINK} is not declared in verify_paths; if the compatibility "
        "symlink stops landing, nothing objects"
    )


def test_every_shipped_symlink_target_survives_the_usrmerge_remap():
    """A target beginning with lib/ or bin/ is rewritten at install time."""
    code = uncommented(llvm_build_script())
    targets = [
        line.split("ln -sv ", 1)[1].split()[0]
        for line in code.splitlines()
        if "ln -sv " in line
    ]
    assert targets, "the compiler recipe no longer creates any symlink"
    for target in targets:
        assert not target.startswith("/"), (
            f"symlink target {target} is absolute; the installer rewrites absolute "
            "targets to relative form and the result is harder to reason about"
        )
        head = target.split("/", 1)[0]
        assert head not in USRMERGE_NAMES, (
            f"symlink target {target} begins with {head}/, which the package "
            "manager's UsrMerge remap rewrites to usr/" + head + "/ at install "
            "time. That is how /opt/rocm/llvm was silently installed pointing at "
            "usr/lib/llvm, a path that does not exist"
        )


def test_the_declared_paths_follow_the_install_prefix():
    declared = recipe(LLVM_DIR).get("verify_paths") or []
    assert f"{TOOLCHAIN_PREFIX}/bin/clang" in declared, (
        "verify_paths no longer names the compiler under the install prefix, so a "
        "build that installed nowhere useful would still verify"
    )
    for path in declared:
        assert not path.startswith("/opt/rocm/lib/llvm/"), (
            f"verify_paths still names {path}, a path under the compatibility "
            "symlink rather than under the real install prefix"
        )
