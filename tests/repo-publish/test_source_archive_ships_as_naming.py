"""A ships_as twin's source archive carries the SHIP name, not the recipe name.

A dual-name twin recipe (``ships_as:``) publishes its binary under the ship
name — the staged archive's .PKGINFO says ``glibc``, never ``glibc-core`` —
and the publish preflight's correspondence gate matches source archives
against that shipped identity. The generator used to name the emitted
archive (filename, top directory, README identity) from the recipe name, so
the first publish staging a ships_as twin refused at the gate: the staged
``glibc`` binary could not be answered by a ``glibc-core-...`` source
archive (found live on the ge9b-13 publish, 2026-08-06).

Pinned here:

  1. the emitted filename is ``<ship>-<version>-<release>.igos.src.tar.gz``;
  2. the archive top directory matches that same identity (the SOURCES.md §3
     layout promise);
  3. upstream tarball lookup still resolves ``${name}`` from the RECIPE name —
     that is the name the builder caches downloads under, and switching it
     would break every twin whose url/filename uses substitution;
  4. a recipe with no ships_as is unaffected.
"""

import importlib.util
import sys
import tarfile
import tempfile
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_PATH = _PROJECT_ROOT / "scripts" / "build-source-archives.py"

spec = importlib.util.spec_from_file_location("build_source_archives_sa", SCRIPT_PATH)
_bsa = importlib.util.module_from_spec(spec)
sys.modules["build_source_archives_sa"] = _bsa
spec.loader.exec_module(_bsa)

TARBALL = ("demo-lib-core-2.43.tar.xz", b"TWIN-UPSTREAM-SOURCE")


@pytest.fixture
def workspace():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        pkg_dir = root / "packages" / "core" / "demo-lib-core"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "build.sh").write_text("#!/bin/bash\n:\n")
        (pkg_dir / "package.yml").write_text("name: demo-lib-core\n")
        sources = root / "sources"
        sources.mkdir()
        sources.joinpath(TARBALL[0]).write_bytes(TARBALL[1])
        out = root / "out"
        out.mkdir()
        yield root, pkg_dir, sources, out


def _meta(ships_as=None):
    import hashlib
    meta = {
        "name": "demo-lib-core",
        "version": "2.43",
        "release": 4,
        # ${name} substitution resolves from the RECIPE name (pin 3); the pin
        # matches the staged bytes so verification lets emission proceed.
        "source": [{"url": "https://example.org/x/${name}-${version}.tar.xz",
                    "sha256": hashlib.sha256(TARBALL[1]).hexdigest()}],
    }
    if ships_as:
        meta["ships_as"] = ships_as
    return meta


def test_ships_as_twin_emits_ship_named_archive(workspace):
    root, pkg_dir, sources, out = workspace
    status, msg = _bsa.build_source_archive(
        pkg_dir, _meta(ships_as="demo-lib"), sources, out, root)
    assert status == "ok", msg
    emitted = out / "demo-lib-2.43-4.igos.src.tar.gz"
    assert emitted.is_file(), (
        f"expected ship-named archive, out dir holds: "
        f"{[p.name for p in out.iterdir()]}")
    # Top directory carries the same ship identity (§3 layout).
    with tarfile.open(emitted) as tf:
        tops = {m.name.split("/")[0] for m in tf.getmembers()}
        assert tops == {"demo-lib-2.43-4"}, tops
        # Pin 3: the bundled upstream tarball resolved from the RECIPE name.
        members = {m.name.split("/", 1)[1] for m in tf.getmembers()
                   if "/" in m.name}
        assert TARBALL[0] in members, sorted(members)
    # No recipe-named sibling was emitted.
    assert not (out / "demo-lib-core-2.43-4.igos.src.tar.gz").exists()


def test_plain_recipe_naming_unchanged(workspace):
    root, pkg_dir, sources, out = workspace
    status, msg = _bsa.build_source_archive(
        pkg_dir, _meta(), sources, out, root)
    assert status == "ok", msg
    assert (out / "demo-lib-core-2.43-4.igos.src.tar.gz").is_file()
