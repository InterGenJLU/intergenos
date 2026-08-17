"""A recipe cannot be allowed to build a Python source it has no backend for.

WHAT HAPPENED. The timm recipe declared `dependencies.build: [setuptools]` while
the pinned 1.0.28 source distribution declares
`requires = ["pdm-backend"]` / `build-backend = "pdm.backend"`. Its build.sh
builds with `pip wheel --no-build-isolation`, and that flag means pip installs
nothing for the build: whatever the source names must already be present.
Setuptools cannot provide `pdm.backend`, so the declared set could never have
built the package. Nothing read a recipe and its own pinned source together, so
the only way to discover it was to spend a build cycle failing on it.

WHY DECLARING IT IS THE FIX. `dependencies.build` and `dependencies.host` are
build-ORDER edges — `igos_build.graph.DependencyGraph.resolve` unions them and
topologically orders the build. Declaring the backend is what guarantees it is
installed before this package builds. A recipe that omits it can still build
today, but only because some other tier happened to install the backend first;
that is an assumption, not a guarantee, and it is exactly the kind of assumption
this project converts into a checked gate.

WHAT THIS FILE ASSERTS. Every branch of the gate, in both directions — a
verdict that can only ever pass is not a test. Each acceptance case below has a
matching refusal case that differs in one fact, so an assertion that stopped
discriminating would fail here rather than go quiet:

  * a source demanding a backend the recipe does not supply is REFUSED, and the
    same recipe with the backend declared is ACCEPTED;
  * a source with no pyproject.toml is accepted with setuptools supplied and
    refused without it (the PEP 518 legacy fallback, in both directions);
  * a pyproject with no [build-system] table behaves the same way;
  * an in-tree backend (`backend-path`) and a self-hosting source (empty
    `requires`) are accepted, and neither acceptance leaks into a case that
    should refuse;
  * a backend whose providing distribution cannot be named from the module alone
    is accepted only when every requirement is supplied, and refused otherwise;
  * supply is TRANSITIVE, matching the builder's own ordering — and a recipe
    whose closure does not reach the backend is still refused;
  * a build.sh that only MENTIONS --no-build-isolation in a comment is not in
    the class at all;
  * an unreadable source and an unstaged source are distinct loud failures, never
    skips.

Nothing here reads the network, needs privilege, or writes inside the tree: each
case builds a throwaway packages tree and a throwaway sdist under tmp_path.
"""
from __future__ import annotations

import io
import subprocess
import sys
import tarfile
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE = REPO_ROOT / "scripts" / "preflight-build-backend.py"

# Exit codes are part of the gate's contract: the pipeline distinguishes "a
# recipe is wrong" from "we could not tell" from "we could not read it".
EXIT_OK = 0
EXIT_VIOLATION = 1
EXIT_UNDETERMINED = 2
EXIT_UNSTAGED = 3

BUILD_SH_REAL = "#!/bin/bash\nbuild() {\n    pip3 wheel -w dist --no-build-isolation --no-deps $PWD\n}\n"
BUILD_SH_COMMENT_ONLY = (
    "#!/bin/bash\n"
    "# This package is built by cmake. A python sibling would use\n"
    "# --no-build-isolation here, but this recipe never invokes pip.\n"
    "build() {\n    cmake --build .\n}\n"
)


def write_recipe(packages_dir: Path, name: str, *, build_deps=(), host_deps=(),
                 filename: str | None = None, build_sh: str = BUILD_SH_REAL,
                 tier: str = "core") -> Path:
    """A minimal but real recipe: the gate reads name, version, source, deps."""
    d = packages_dir / tier / name
    d.mkdir(parents=True, exist_ok=True)
    src = ""
    if filename is not None:
        src = (f"source:\n"
               f"- url: https://example.invalid/{filename}\n"
               f"  filename: {filename}\n"
               f"  sha256: {'0' * 64}\n")
    (d / "package.yml").write_text(textwrap.dedent(f"""\
        name: {name}
        version: "1.0.0"
        release: 1
        description: fixture
        license: MIT
        tier: {tier}
        build_style: custom
        """) + src + textwrap.dedent(f"""\
        dependencies:
          build: {list(build_deps)}
          host: {list(host_deps)}
          runtime: []
        """), encoding="utf-8")
    (d / "build.sh").write_text(build_sh, encoding="utf-8")
    return d


def make_sdist(sources_dir: Path, filename: str, pyproject: str | None) -> Path:
    """A real .tar.gz laid out like a Python sdist: one top-level directory."""
    sources_dir.mkdir(parents=True, exist_ok=True)
    path = sources_dir / filename
    top = filename[: -len(".tar.gz")]
    with tarfile.open(path, "w:gz") as tf:
        def add(rel: str, text: str):
            data = text.encode("utf-8")
            info = tarfile.TarInfo(f"{top}/{rel}")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        add("PKG-INFO", "Metadata-Version: 2.1\n")
        if pyproject is not None:
            add("pyproject.toml", pyproject)
    return path


def run_gate(packages_dir: Path, sources_dir: Path):
    return subprocess.run(
        [sys.executable, str(GATE), "--packages-dir", str(packages_dir),
         "--sources-dir", str(sources_dir)],
        capture_output=True, text=True)


@pytest.fixture()
def tree(tmp_path):
    """A throwaway tree plus the four backend recipes the real tree carries."""
    packages = tmp_path / "packages"
    sources = tmp_path / "sources"
    sources.mkdir()
    for backend in ("setuptools", "flit-core", "hatchling", "pdm-backend",
                    "meson-python", "cython"):
        # Backend recipes themselves are not in the class: no source, no pip.
        write_recipe(packages, backend, build_sh="#!/bin/bash\nbuild() { :; }\n")
    return packages, sources


PDM_PYPROJECT = '[build-system]\nrequires = ["pdm-backend"]\nbuild-backend = "pdm.backend"\n'


def test_wrong_backend_is_refused_and_declaring_it_accepts(tree):
    """The timm defect itself, and its correction — one fact apart."""
    packages, sources = tree
    make_sdist(sources, "widget-1.0.0.tar.gz", PDM_PYPROJECT)

    write_recipe(packages, "widget", build_deps=["setuptools"],
                 filename="widget-1.0.0.tar.gz")
    bad = run_gate(packages, sources)
    assert bad.returncode == EXIT_VIOLATION, bad.stdout
    assert "REFUSED  widget" in bad.stdout
    # The refusal has to name the recipe, what it declared, and what the source
    # demands — a refusal that does not say what to do is a wall, not a gate.
    assert "pdm.backend" in bad.stdout
    assert "pdm-backend" in bad.stdout
    assert "['setuptools']" in bad.stdout
    assert "correction:" in bad.stdout

    write_recipe(packages, "widget", build_deps=["pdm-backend"],
                 filename="widget-1.0.0.tar.gz")
    good = run_gate(packages, sources)
    assert good.returncode == EXIT_OK, good.stdout


def test_legacy_sdist_without_pyproject_needs_setuptools_both_ways(tree):
    """PEP 518's fallback is setuptools — accepted with it, refused without."""
    packages, sources = tree
    make_sdist(sources, "legacy-1.0.0.tar.gz", None)

    write_recipe(packages, "legacy", build_deps=["setuptools"],
                 filename="legacy-1.0.0.tar.gz")
    ok = run_gate(packages, sources)
    assert ok.returncode == EXIT_OK, ok.stdout

    write_recipe(packages, "legacy", build_deps=[],
                 filename="legacy-1.0.0.tar.gz")
    bad = run_gate(packages, sources)
    assert bad.returncode == EXIT_VIOLATION, bad.stdout
    assert "no pyproject.toml" in bad.stdout


def test_pyproject_without_build_system_table_behaves_as_legacy(tree):
    packages, sources = tree
    make_sdist(sources, "notable-1.0.0.tar.gz", '[project]\nname = "notable"\n')

    write_recipe(packages, "notable", build_deps=["setuptools"],
                 filename="notable-1.0.0.tar.gz")
    assert run_gate(packages, sources).returncode == EXIT_OK

    write_recipe(packages, "notable", build_deps=[],
                 filename="notable-1.0.0.tar.gz")
    bad = run_gate(packages, sources)
    assert bad.returncode == EXIT_VIOLATION, bad.stdout
    assert "no [build-system] table" in bad.stdout


def test_in_tree_backend_needs_no_declaration(tree):
    """backend-path means the backend ships inside the source itself."""
    packages, sources = tree
    make_sdist(sources, "intree-1.0.0.tar.gz",
               '[build-system]\nrequires = []\n'
               'build-backend = "local_backend"\nbackend-path = ["."]\n')
    write_recipe(packages, "intree", build_deps=[], filename="intree-1.0.0.tar.gz")
    res = run_gate(packages, sources)
    assert res.returncode == EXIT_OK, res.stdout


def test_self_hosting_source_with_empty_requires_is_accepted(tree):
    """setuptools/flit_core/hatchling build themselves with requires = []."""
    packages, sources = tree
    make_sdist(sources, "boot-1.0.0.tar.gz",
               '[build-system]\nrequires = []\n'
               'build-backend = "boot.build_meta"\n')
    write_recipe(packages, "boot", build_deps=[], filename="boot-1.0.0.tar.gz")
    res = run_gate(packages, sources)
    assert res.returncode == EXIT_OK, res.stdout


def test_ambiguous_provider_requires_every_requirement_both_ways(tree):
    """`mesonpy` is provided by `meson-python`; the name cannot say so.

    The gate must not guess. It demands the whole requires set, which is a
    superset of whatever provides the backend — accepted when all are supplied,
    refused when one is missing.
    """
    packages, sources = tree
    make_sdist(sources, "amb-1.0.0.tar.gz",
               '[build-system]\nrequires = ["meson-python>=0.18", "cython>=3"]\n'
               'build-backend = "mesonpy"\n')

    write_recipe(packages, "amb", build_deps=["meson-python", "cython"],
                 filename="amb-1.0.0.tar.gz")
    ok = run_gate(packages, sources)
    assert ok.returncode == EXIT_OK, ok.stdout

    write_recipe(packages, "amb", build_deps=["meson-python"],
                 filename="amb-1.0.0.tar.gz")
    bad = run_gate(packages, sources)
    assert bad.returncode == EXIT_VIOLATION, bad.stdout
    assert "cannot be identified from the name alone" in bad.stdout
    assert "cython" in bad.stdout


def test_supply_is_transitive_and_still_refuses_when_it_does_not_reach(tree):
    """Ordering is transitive, so the gate credits the whole closure — but only
    when the closure actually reaches the backend."""
    packages, sources = tree
    make_sdist(sources, "leaf-1.0.0.tar.gz",
               '[build-system]\nrequires = ["flit-core"]\n'
               'build-backend = "flit_core.buildapi"\n')

    # middle -> flit-core, leaf -> middle. leaf never names flit-core itself.
    write_recipe(packages, "middle", build_deps=["flit-core"],
                 build_sh="#!/bin/bash\nbuild() { :; }\n")
    write_recipe(packages, "leaf", build_deps=["middle"],
                 filename="leaf-1.0.0.tar.gz")
    ok = run_gate(packages, sources)
    assert ok.returncode == EXIT_OK, ok.stdout

    # Same shape, but the middle package does not reach flit-core.
    write_recipe(packages, "middle", build_deps=["setuptools"],
                 build_sh="#!/bin/bash\nbuild() { :; }\n")
    bad = run_gate(packages, sources)
    assert bad.returncode == EXIT_VIOLATION, bad.stdout
    assert "REFUSED  leaf" in bad.stdout


def test_declaring_the_backend_under_host_also_supplies_it(tree):
    """The builder unions build and host into one ordering namespace."""
    packages, sources = tree
    make_sdist(sources, "hostdep-1.0.0.tar.gz",
               '[build-system]\nrequires = ["flit-core"]\n'
               'build-backend = "flit_core.buildapi"\n')
    write_recipe(packages, "hostdep", build_deps=[], host_deps=["flit-core"],
                 filename="hostdep-1.0.0.tar.gz")
    res = run_gate(packages, sources)
    assert res.returncode == EXIT_OK, res.stdout


def test_comment_only_mention_is_not_in_the_class(tree):
    """A recipe that talks about the flag but never invokes pip is not gated."""
    packages, sources = tree
    make_sdist(sources, "cmake-1.0.0.tar.gz", PDM_PYPROJECT)
    write_recipe(packages, "cmakepkg", build_deps=[],
                 filename="cmake-1.0.0.tar.gz", build_sh=BUILD_SH_COMMENT_ONLY)
    res = run_gate(packages, sources)
    assert res.returncode == EXIT_OK, res.stdout
    # And it is genuinely excluded, not merely passing: the class count is zero.
    assert "0 recipes build a Python source distribution" in res.stdout


def test_unstaged_source_is_named_and_fails_rather_than_passing(tree):
    packages, sources = tree
    write_recipe(packages, "absent", build_deps=["setuptools"],
                 filename="absent-1.0.0.tar.gz")
    res = run_gate(packages, sources)
    assert res.returncode == EXIT_UNSTAGED, res.stdout
    assert "NOT STAGED absent" in res.stdout


def test_unreadable_source_is_undetermined_not_a_pass(tree):
    """A source we cannot read is a loud failure. Reporting it as a pass would
    let the build read as covered while nothing was checked."""
    packages, sources = tree
    (sources / "corrupt-1.0.0.tar.gz").write_bytes(b"this is not a tar archive")
    write_recipe(packages, "corrupt", build_deps=["setuptools"],
                 filename="corrupt-1.0.0.tar.gz")
    res = run_gate(packages, sources)
    assert res.returncode == EXIT_UNDETERMINED, res.stdout
    assert "UNDETERMINED corrupt" in res.stdout


def test_unresolvable_source_filename_is_undetermined(tree):
    packages, sources = tree
    write_recipe(packages, "nosource", build_deps=["setuptools"], filename=None)
    res = run_gate(packages, sources)
    assert res.returncode == EXIT_UNDETERMINED, res.stdout
    assert "could not be resolved" in res.stdout


def test_real_tree_is_in_the_class_and_the_gate_reads_it(tmp_path):
    """The gate must find the real population, not silently match nothing.

    A gate pointed at the real packages tree with an empty sources dir should
    report a non-trivial class and name every member as unstaged. If this ever
    reports zero recipes, the class detector has stopped matching and every
    other assertion in this file would still pass while the gate checked
    nothing.
    """
    empty = tmp_path / "empty-sources"
    empty.mkdir()
    res = run_gate(REPO_ROOT / "packages", empty)
    assert res.returncode == EXIT_UNSTAGED, res.stdout
    first = res.stdout.splitlines()[0]
    count = int(first.split(":")[1].strip().split()[0])
    assert count > 100, f"class detector matched only {count} recipes: {first}"
    assert "NOT STAGED timm" in res.stdout
