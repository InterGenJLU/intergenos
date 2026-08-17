"""The BLFS book parser's imports must be satisfied by packages we carry.

scripts/parse-blfs-book.py runs on the build host and produces
build/blfs-packages.db, the database preflight-audit-coverage.py and
preflight-silent-loss.py read. It imports beautifulsoup4, which upstream
declares as requiring soupsieve and typing-extensions.

Nothing else in the tree checks that pairing. The tier-coverage gate proves a
package is wired; the iso-closure gate proves shipped packages' runtime deps
ship; neither notices if beautifulsoup4 stops declaring the library its own
bs4/css.py imports, or if the parser keeps importing a package that was
removed. These tests pin exactly that: the importer, the recipes, the declared
dependency set, and the build order between them.

Nothing here reads the network, needs privilege, or builds anything.
"""
from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

parser = importlib.import_module("igos-build.parser")

PARSER_SCRIPT = REPO / "scripts" / "parse-blfs-book.py"
CORE_EXTRA = REPO / "scripts" / "chroot-build-core-extra.sh"

# What upstream beautifulsoup4 4.15.0 declares as required (its own
# Requires-Dist, minus the optional `extra ==` parser backends). Dropping
# either one does not fail a build — it produces a package whose css.py
# raises on import, which is a degraded package, not a smaller one.
REQUIRED_RUNTIME = {"soupsieve", "typing-extensions"}


def _recipe(name: str):
    return parser.parse_template(REPO / "packages" / "core" / name / "package.yml")


def test_the_blfs_parser_still_imports_beautifulsoup4():
    """If this stops being true the packages below have no consumer, and the
    reason they are carried at all needs re-deciding rather than assuming."""
    text = PARSER_SCRIPT.read_text(encoding="utf-8")
    assert re.search(r"^\s*from bs4 import ", text, re.M), (
        "scripts/parse-blfs-book.py no longer imports bs4 — re-examine why "
        "packages/core/beautifulsoup4 is carried")


@pytest.mark.parametrize("name", ["beautifulsoup4", "soupsieve"])
def test_recipe_is_core_and_mirror_only(name):
    pkg = _recipe(name)
    assert pkg.tier == "core"
    # Mirror-only: the importer is a build-host script that is never present
    # on an installed system, so shipping these would add packages nothing on
    # the target uses.
    assert pkg.iso_include is False


@pytest.mark.parametrize("name", ["beautifulsoup4", "soupsieve"])
def test_source_is_pinned_not_generated(name):
    """These are upstream tarballs: every one carries a 64-hex sha256 and none
    is marked generated."""
    pkg = _recipe(name)
    assert pkg.source, f"{name} declares no source"
    for src in pkg.source:
        assert not src.generated
        assert src.sha256 and re.fullmatch(r"[0-9a-f]{64}", src.sha256)


def test_beautifulsoup4_declares_every_required_runtime_dependency():
    pkg = _recipe("beautifulsoup4")
    declared = set(pkg.dependencies.runtime)
    missing = REQUIRED_RUNTIME - declared
    assert not missing, (
        f"beautifulsoup4 does not declare {sorted(missing)} — upstream requires "
        f"them and bs4/css.py imports soupsieve at module import time")


def test_every_declared_runtime_dependency_is_a_package_we_carry():
    """A declared dep that names nothing is worse than an undeclared one: it
    reads as covered."""
    pkg = _recipe("beautifulsoup4")
    for dep in pkg.dependencies.runtime:
        found = list((REPO / "packages").glob(f"*/{dep}/package.yml"))
        assert found, f"beautifulsoup4 runtime-deps {dep!r}, which no recipe provides"


@pytest.mark.parametrize("name", ["beautifulsoup4", "soupsieve"])
def test_package_is_wired_into_the_core_extra_driver(name):
    """Rule 2: a tier:core package absent from a driver's run_package list is
    silently never built."""
    text = CORE_EXTRA.read_text(encoding="utf-8")
    assert re.search(rf'^run_package "{re.escape(name)}" ', text, re.M), (
        f"{name} has no run_package line in scripts/chroot-build-core-extra.sh")


def test_soupsieve_builds_before_beautifulsoup4():
    """Dependency order inside the driver: the bash core drivers build in file
    order, so the consumer must come second."""
    text = CORE_EXTRA.read_text(encoding="utf-8")
    soup = text.index('run_package "soupsieve" ')
    bs4 = text.index('run_package "beautifulsoup4" ')
    assert soup < bs4, (
        "soupsieve must be wired before beautifulsoup4 — it is a required "
        "runtime dependency and the bash driver builds in file order")
