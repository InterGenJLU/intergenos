"""Two recipes may not write one source archive; the generator must refuse.

WHAT WENT WRONG. The generator names each archive
``<ships_as or name>-<version>-<release>.igos.src.tar.gz``, so a dual-name twin
collapses onto the recipe whose name it ships as. Where a plain recipe of that
name also exists, both write the same path and the loop's later visit silently
overwrites the earlier one.

Measured on the tree this test was written against, 2026-08-25: two pairs
collided. ``packages/core/m4-core`` (ships_as m4) and ``packages/toolchain/m4``
both emitted ``m4-1.4.21-1.igos.src.tar.gz``, 2083095 and 2082852 bytes;
``ncurses-core`` and ``packages/toolchain/ncurses`` both emitted
``ncurses-6.6-1.igos.src.tar.gz``, 3787218 and 3787005 bytes. The recipes are
visited in sorted path order, so ``packages/core/...`` was written first and
``packages/toolchain/...`` was the last writer.

What that costs is not a metadata detail. The published archive ends up holding
the toolchain recipe's package.yml and no build.sh at all, while the binary
published under that name is built from the core recipe. A corresponding-source
archive that describes a different recipe and omits the build script is the one
thing this generator exists to prevent.

A third pair, glibc, shared a ship name and was kept apart only by a release
number that happened to differ — nothing stopped it colliding.

THE PROPERTY. Overwriting is not something to detect after the fact; it must be
impossible to complete a generation that would do it. The generator refuses,
names both recipes, and exits non-zero, so a publish cannot be built on top of
an archive whose contents were decided by directory ordering.

The fix for WHICH recipe should have given way was a naming question about
three toolchain recipes, decided separately: they were renamed to m4-tmp,
ncurses-tmp and glibc-tmp the same day, so the tree no longer contains a
colliding pair. This test pins only that the generator never silently picks
one, which is what has to stay true for every pair added later.
"""

import subprocess
import sys
import textwrap
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_PATH = _PROJECT_ROOT / "scripts" / "build-source-archives.py"

UPSTREAM = ("demo-2.0.tar.xz", b"DEMO-UPSTREAM-BYTES")


def _sha256(data):
    import hashlib
    return hashlib.sha256(data).hexdigest()


def _recipe(text):
    return textwrap.dedent(text).lstrip()


def _make_repo(tmp_path, second_recipe_body):
    """A miniature tree: one twin recipe plus a second recipe under test."""
    repo = tmp_path / "repo"
    (repo / "packages" / "core" / "demo-core").mkdir(parents=True)
    (repo / "packages" / "toolchain" / "demo").mkdir(parents=True)
    sources = repo / "sources"
    sources.mkdir()
    sources.joinpath(UPSTREAM[0]).write_bytes(UPSTREAM[1])
    digest = _sha256(UPSTREAM[1])

    # The twin: ships under the plain name, so it claims demo-2.0-1.
    (repo / "packages" / "core" / "demo-core" / "package.yml").write_text(_recipe(f"""
        name: demo-core
        ships_as: demo
        version: "2.0"
        release: 1
        source:
          - url: https://example.invalid/demo-${{version}}.tar.xz
            filename: demo-${{version}}.tar.xz
            sha256: {digest}
        tier: core
        """))
    (repo / "packages" / "core" / "demo-core" / "build.sh").write_text("#!/bin/bash\n:\n")

    (repo / "packages" / "toolchain" / "demo" / "package.yml").write_text(
        _recipe(second_recipe_body).replace("@SHA@", digest))
    (repo / "packages" / "toolchain" / "demo" / "build.sh").write_text("#!/bin/bash\n:\n")
    return repo, sources


def _run(repo, sources, out):
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH),
         "--repo-root", str(repo),
         "--sources-dir", str(sources),
         "--output-dir", str(out)],
        capture_output=True, text=True, timeout=300,
    )


def test_two_recipes_claiming_one_archive_are_refused(tmp_path):
    """The colliding shape: a twin and a plain recipe of the ship's name."""
    repo, sources = _make_repo(tmp_path, """
        name: demo
        version: "2.0"
        release: 1
        source:
          - url: https://example.invalid/demo-${version}.tar.xz
            filename: demo-${version}.tar.xz
            sha256: @SHA@
        tier: toolchain
        """)
    out = tmp_path / "out"
    proc = _run(repo, sources, out)
    combined = proc.stdout + proc.stderr

    assert proc.returncode != 0, (
        "the generator completed a run in which two recipes claim one archive "
        f"path; it must refuse instead.\n{combined}")

    # The message has to be actionable: both recipes named, and the path.
    assert "demo-2.0-1.igos.src.tar.gz" in combined, combined
    assert "demo-core" in combined, (
        f"the refusal does not name the twin recipe:\n{combined}")
    assert "packages/toolchain/demo" in combined or "toolchain/demo" in combined, (
        f"the refusal does not name the second recipe:\n{combined}")


def test_a_non_colliding_pair_still_emits_both(tmp_path):
    """The guard must not refuse recipes that merely look similar.

    Same upstream, same ship family, different version — two distinct archive
    names, so both are emitted and nothing is refused.
    """
    repo, sources = _make_repo(tmp_path, """
        name: demo
        version: "2.0"
        release: 7
        source:
          - url: https://example.invalid/demo-${version}.tar.xz
            filename: demo-${version}.tar.xz
            sha256: @SHA@
        tier: toolchain
        """)
    out = tmp_path / "out"
    proc = _run(repo, sources, out)
    combined = proc.stdout + proc.stderr

    assert proc.returncode == 0, (
        f"a run with no colliding output path was refused:\n{combined}")
    emitted = sorted(p.name for p in out.iterdir())
    assert emitted == ["demo-2.0-1.igos.src.tar.gz",
                       "demo-2.0-7.igos.src.tar.gz"], emitted


def test_the_refusal_does_not_leave_a_half_decided_archive(tmp_path):
    """A refused run must not leave one of the two claimants on disk as the winner.

    If the first claimant is written and the run then refuses, a later step
    could still find that file and treat it as the answer. Whatever the
    generator leaves behind, it must not be a file whose contents were chosen
    by which recipe happened to be visited first while the run itself failed.
    """
    repo, sources = _make_repo(tmp_path, """
        name: demo
        version: "2.0"
        release: 1
        source:
          - url: https://example.invalid/demo-${version}.tar.xz
            filename: demo-${version}.tar.xz
            sha256: @SHA@
        tier: toolchain
        """)
    out = tmp_path / "out"
    proc = _run(repo, sources, out)
    assert proc.returncode != 0

    contested = out / "demo-2.0-1.igos.src.tar.gz"
    assert not contested.exists(), (
        "the run refused but left the contested archive on disk, so a later "
        "step can still read a file whose contents were decided by directory "
        "ordering")
