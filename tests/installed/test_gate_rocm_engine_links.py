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
import sqlite3
import subprocess
from pathlib import Path

import pytest

ROCM_ROOT = Path("/opt/rocm")
SCANNED = ("bin", "lib")

#: The package database the rest of this tier's tooling reads.
PKM_DB = Path("/var/lib/igos/pkm.db")

#: The package whose recipe lists /opt/rocm/bin/llama-server among the paths it
#: verifies. Its presence in the installed set is what makes this gate apply.
ROCM_ENGINE_PACKAGE = "llama-cpp-hip"


def _rocm_engine_is_installed() -> bool:
    """Is the opt-in ROCm engine package recorded as installed on this machine?

    ASKED OF THE PACKAGE DATABASE, not inferred from the filesystem, because the
    filesystem is the thing under test: "/opt/rocm is missing" is the symptom
    this gate exists to catch when the package IS installed, and the ordinary
    state of a machine that never opted in when it is not.

    A database that cannot be read raises. "I could not look" must not be able to
    turn into "it is not installed here", which would let the gate skip on the
    machine whose engine is broken.
    """
    if not PKM_DB.is_file():
        raise OSError(f"{PKM_DB} is absent, so the installed package set cannot "
                      f"be read and this gate cannot tell whether it applies")
    con = sqlite3.connect(f"file:{PKM_DB}?immutable=1", uri=True)
    try:
        row = con.execute(
            "SELECT 1 FROM installed WHERE name = ? AND superseded_by IS NULL",
            (ROCM_ENGINE_PACKAGE,)).fetchone()
    finally:
        con.close()
    return row is not None


def _require_rocm_applies() -> None:
    """FAIL when the engine is installed and its tree is gone; SKIP when it is not.

    WHY THIS IS A SKIP AND NOT A FAILURE, added 2026-08-24. Both cases in this
    file previously failed outright on any machine without /opt/rocm. The engine
    is opt-in, mirror-only and needs an AMD card, so that is the ordinary state
    of most machines — including the one this tier is run from. Because
    scripts/check-release-validation.py refuses a release on ANY failing gate and
    can only accept a DECLARED skip, a machine without the engine could never
    produce a validating record at all: a gate about an optional component was
    refusing every release on every machine that had not opted into it.

    A skip is not a softening. It still says NOTHING WAS MEASURED, it still has
    to be declared in scripts/data/installed-gate-expected-skips.txt before the
    release gate will accept it, and the case where the property could really be
    broken — the package installed and its files absent — still FAILS.
    """
    try:
        installed = _rocm_engine_is_installed()
    except (OSError, sqlite3.Error) as exc:
        pytest.fail(
            f"Whether the ROCm engine applies to this machine could not be "
            f"determined ({exc}). This gate will not skip on an unanswered "
            f"question: an unread package database is not an absent package.")
    if ROCM_ROOT.is_dir():
        return
    if installed:
        pytest.fail(
            f"The package database records {ROCM_ENGINE_PACKAGE} as installed, "
            f"but {ROCM_ROOT} does not exist. The engine this machine is "
            f"supposed to have is not on it.")
    pytest.skip(
        f"NOT VERIFIED: {ROCM_ROOT} does not exist and {ROCM_ENGINE_PACKAGE} is "
        f"not in this machine's installed package set, so the opt-in ROCm engine "
        f"is not present here and its linkage was not checked. This is a SKIP, "
        f"not a pass — nothing about the engine has been verified on this "
        f"machine, and the release gate accepts it only because it is declared "
        f"in scripts/data/installed-gate-expected-skips.txt.")

#: `ldd` says this for a file that is not a dynamic executable at all.
NOT_DYNAMIC = "not a dynamic executable"


@pytest.fixture(scope="module")
def rocm_objects() -> list[Path]:
    """Every regular file the ROCm tree ships under bin/ and lib/.

    Symlinks are skipped: the loader resolves them to the same real file, and
    counting both would report one defect three times.
    """
    _require_rocm_applies()
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

    _require_rocm_applies()
    server = ROCM_ROOT / "bin" / "llama-server"
    if not server.exists():
        pytest.fail(
            f"{ROCM_ROOT} is present on this machine but {server} is not in it; "
            f"the engine is installed and its server binary is missing")

    res = subprocess.run([str(server), "--version"],
                         capture_output=True, text=True, timeout=120)
    combined = (res.stdout or "") + (res.stderr or "")
    assert "error while loading shared libraries" not in combined, (
        f"the shipped inference server cannot load its libraries:\n{combined}")
    assert res.returncode == 0, (
        f"{server} --version exited {res.returncode}:\n{combined}")
