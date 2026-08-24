"""GATE 15 — every ROCm program on the installed system can actually load.

WHAT COMPOSITION PROPERTY THIS CATCHES. Package verification confirms that the
ROCm inference engine's files are installed, with the right hashes, in the right
places. It says nothing about whether the shared libraries those files link are
present on the same machine. They come from other packages, and a package is
fetched only when something in the installed dependency graph names it.

On the R001.1 install measured on 2026-08-22, nothing did. Two libraries had no
provider anywhere on the machine — librocprofiler-register.so.0, linked by the
HIP runtime and by the HSA runtime, and libroctx64.so.4, linked by the sparse
linear-algebra library. Both are shipped by packages that were published on the
mirror and installable the whole time; no installed package declared either, so
neither was ever fetched. The visible result was that all eighty programs under
/opt/rocm/bin, the inference server among them, exited immediately:

    /opt/rocm/bin/llama-server: error while loading shared libraries:
    librocprofiler-register.so.0: cannot open shared object file

The unit tests passed. The package verifier passed. The engine could not start.

WHY THIS GATE READS THE FILES RATHER THAN RUNNING THEM. Starting the inference
server needs a GPU, a model and a free port, and it would report a failure for
any of a dozen reasons unrelated to linkage. Reading each object's DT_NEEDED
entries and resolving them the way the dynamic loader would needs no privilege,
no device and no model, and it answers exactly the question that was false.

RESOLUTION IS ASKED OF THE LOADER ITSELF. This gate runs `ldd`, so the answer
comes from the same dynamic linker that will run the program, with the real
ld.so.conf, the real cache and the real RPATH/RUNPATH rules — not from a second
implementation of those rules that could disagree with it. It also keeps this
tier free of repository imports: these gates run on an installed machine, where
igos-build is not present.

The tree already audits this property for the ISO, in igos-build/needclosure.py.
That sweep never saw this defect because it audits the sealed image's own file
set, and the engine is opt-in and mirror-only: it is not on the ISO, so it was
never in the sweep. Authoring-time cover for the mirror-installed class is
scripts/check-runtime-link-deps.py.

EXPECTED TO FAIL ON R001.1 AS SHIPPED.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROCM_ROOT = Path("/opt/rocm")
SCANNED = ("bin", "lib")

#: `ldd` says this for a file that is not a dynamic executable at all.
NOT_DYNAMIC = "not a dynamic executable"


@pytest.fixture(scope="module")
def rocm_objects() -> list[Path]:
    """Every regular file the ROCm tree ships under bin/ and lib/.

    Symlinks are skipped: the loader resolves them to the same real file, and
    counting both would report one defect three times.
    """
    if not ROCM_ROOT.is_dir():
        pytest.fail(
            f"{ROCM_ROOT} does not exist on this machine. This gate covers the "
            f"ROCm engine's linkage; a machine without it cannot report a pass.")
    found: list[Path] = []
    for sub in SCANNED:
        base = ROCM_ROOT / sub
        if not base.is_dir():
            continue
        for dirpath, _dirs, files in os.walk(base, followlinks=False):
            for fname in files:
                p = Path(dirpath) / fname
                if p.is_symlink() or not p.is_file():
                    continue
                with open(p, "rb") as fh:
                    if fh.read(4) != b"\x7fELF":
                        continue
                found.append(p)
    if not found:
        pytest.fail(
            f"found ZERO ELF objects under {ROCM_ROOT} — an empty audit "
            f"is a failed audit, not a pass")
    return found


def test_every_rocm_object_resolves_every_library_it_links(rocm_objects):
    unresolved: list[str] = []
    examined = 0

    for path in rocm_objects:
        res = subprocess.run(["ldd", str(path)],
                             capture_output=True, text=True, timeout=120)
        out = (res.stdout or "") + (res.stderr or "")
        if NOT_DYNAMIC in out:
            continue
        examined += 1
        for line in out.splitlines():
            if "not found" in line:
                unresolved.append(f"{path}: {line.strip()}")

    assert examined, (
        f"`ldd` reported every object under {ROCM_ROOT} as non-dynamic — "
        f"nothing was audited, which is a failure and not a pass")

    assert not unresolved, (
        f"{len(unresolved)} linked librar(y/ies) have no provider on this "
        f"machine ({examined} dynamic objects examined). Every program that "
        f"loads one of these objects fails to start:\n  "
        + "\n  ".join(sorted(unresolved)))


def test_the_inference_server_starts_far_enough_to_answer_for_its_version():
    """The user-visible half: the shipped server runs its own --version.

    This is the command that failed on the measured install, and it needs
    neither a GPU nor a model — a dynamic-linker failure exits before main()
    with the missing library named on stderr.
    """
    import subprocess

    server = ROCM_ROOT / "bin" / "llama-server"
    if not server.exists():
        pytest.fail(f"{server} is not installed; this gate cannot report a pass")

    res = subprocess.run([str(server), "--version"],
                         capture_output=True, text=True, timeout=120)
    combined = (res.stdout or "") + (res.stderr or "")
    assert "error while loading shared libraries" not in combined, (
        f"the shipped inference server cannot load its libraries:\n{combined}")
    assert res.returncode == 0, (
        f"{server} --version exited {res.returncode}:\n{combined}")
