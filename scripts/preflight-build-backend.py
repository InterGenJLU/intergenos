#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Refuse a build whose recipes cannot supply the build backend their pinned
Python sources demand.

THE CLASS THIS KILLS
--------------------
A recipe builds a Python source distribution with `pip wheel --no-build-isolation`.
That flag means pip installs NOTHING for the build: PEP 518's `[build-system]`
table is read, but its `requires` are expected to be present already. If the
backend the source names is not in the build environment, the build dies at the
first wheel invocation with `BackendUnavailable: Cannot import '<backend>'`.

Discovered on the timm recipe, which declared `dependencies.build: [setuptools]`
while the pinned 1.0.28 sdist declares `requires = ["pdm-backend"]` with
`build-backend = "pdm.backend"`. Setuptools cannot provide `pdm.backend`, so the
declared set could never have built the package — and nothing read the recipe and
its own pinned source together, so the defect could only be found by burning a
build cycle on it.

WHAT "SUPPLY" MEANS HERE
------------------------
`dependencies.build` and `dependencies.host` are BUILD-ORDER edges, not an
environment installer: `igos_build.graph.DependencyGraph.resolve` unions the two
sets and orders the build by them. So declaring the backend is what GUARANTEES it
is built and installed before this package builds. A recipe that omits it may
still build today — but only by the accident of another tier having installed the
backend first, which is an unverified assumption, not a checked one. Ordering is
transitive, so this gate credits the whole transitive closure of a recipe's
build+host edges, exactly as the builder's own topological order does.

WHAT IS SATISFIED WITHOUT A DECLARATION
---------------------------------------
Three cases are satisfied intrinsically and are PASSES, not exemptions:

  * No `pyproject.toml`, or a `pyproject.toml` with no `[build-system]` table.
    PEP 518 defines the fallback as the setuptools legacy backend, so a recipe
    whose closure carries setuptools satisfies it.
  * `backend-path` is set. The backend ships inside the source tree itself
    (PEP 517 in-tree backends), so no external distribution is needed.
  * `requires` is empty while a backend is named. This is the self-hosting
    bootstrap shape — setuptools, flit_core and hatchling all build themselves
    this way — and by declaring an empty requires the source states that it needs
    nothing installed.

RESOLVING WHICH DISTRIBUTION PROVIDES THE BACKEND
-------------------------------------------------
A backend is named as a MODULE (`pdm.backend`, `mesonpy`) while a dependency is a
DISTRIBUTION (`pdm-backend`, `meson-python`). The two are not derivable from each
other in general, and this gate deliberately carries NO hand-maintained
module-to-distribution table: such a table is exactly the sort of memory-derived
artifact that goes stale silently.

Instead the providing distribution is resolved from the source's own
`requires` list, which is where PEP 518 says the backend's provider must appear:

  1. If exactly one `requires` entry matches the backend module's root name after
     PEP 503 normalisation (comparing with separators removed, so `flit_core`
     matches `flit-core`), that entry is the provider.
  2. If there is exactly one `requires` entry at all, it is the provider.
  3. Otherwise the provider is AMBIGUOUS — and rather than guess, the gate falls
     back to a sound superset: it requires that EVERY `requires` entry be
     supplied. Whichever entry provides the backend, that is covered. This is how
     `mesonpy` (provided by `meson-python`, alongside `cython`) resolves with no
     table and no guess.

A case that still cannot be decided is a FAILURE, never a skip — the
tarball-membership gate's rule, for the same reason: an undetermined verdict
reported as a pass lets the build read as covered while nothing was checked.

EXIT CODES
----------
  0  every in-class recipe supplies its backend
  1  at least one recipe cannot supply the backend its pinned source demands
  2  at least one recipe could not be determined (unreadable source, unparseable
     pyproject, unresolvable source filename, ambiguous backend with an
     unsupplied requirement)
  3  at least one in-class recipe's pinned source is not staged

Exit 3 cannot occur in the pipeline: this gate runs inside verify-sources, after
the sha audit has already proven every pinned source is on disk. It exists so
that firing the gate by hand on a partially-staged tree names what it could not
read instead of quietly reporting a clean sweep over half the population.
"""

from __future__ import annotations

import argparse
import re
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - environment defect, not a recipe defect
    print("FATAL: pyyaml required", file=sys.stderr)
    sys.exit(2)


# Mirrors igos_build.parser._resolve_variables. package.yml urls and filenames
# carry ${name}/${version}/... placeholders the build pipeline expands; this gate
# reads the YAML directly, so it must expand them the same way. If that set
# drifts in the parser, audit both consumers — the same note the verify-sources
# sha sweep carries.
_VAR_RE = re.compile(r"\$\{(\w+)\}")

# A build.sh line whose first non-space character is '#' is prose about the
# build, not the build. Several recipes discuss --no-build-isolation in their
# header comments; counting those puts packages in the class that never invoke
# pip at all.
_COMMENT_RE = re.compile(r"^\s*#")

# pip is invoked as `pip3`, `pip`, or `python3 -m pip` across the tree.
_PIP_RE = re.compile(r"\bpip3?\b|\bpython3?\s+-m\s+pip\b")

_NBI = "--no-build-isolation"


def _resolve(text: str, variables: dict) -> str:
    return _VAR_RE.sub(lambda m: variables.get(m.group(1), m.group(0)), text)


def normalize(name: str) -> str:
    """PEP 503 normalised distribution name."""
    return re.sub(r"[-_.]+", "-", name.strip()).lower()


def requirement_name(spec: str) -> str:
    """The distribution name out of a PEP 508 requirement string."""
    s = spec.strip()
    s = re.split(r"[;\[]", s, maxsplit=1)[0]        # environment marker / extras
    s = re.split(r"[<>=!~ ()]", s, maxsplit=1)[0]   # version specifier
    return normalize(s)


def builds_with_no_build_isolation(build_sh: Path) -> bool:
    """True when this recipe actually INVOKES pip with --no-build-isolation.

    Backslash continuations are joined first so a flag written on its own line is
    attributed to the command that owns it.
    """
    try:
        text = build_sh.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    if _NBI not in text:
        return False
    joined = re.sub(r"\\\s*\n\s*", " ", text)
    for line in joined.splitlines():
        if _COMMENT_RE.match(line):
            continue
        if _NBI in line and _PIP_RE.search(line):
            return True
    return False


def read_build_system(path: Path):
    """Return the sdist's [build-system] table.

    Returns (found_pyproject, table_or_None). Raises RuntimeError when the
    archive or its pyproject cannot be read — an undetermined verdict, never a
    silent pass.
    """
    try:
        if path.suffix in (".zip", ".whl"):
            with zipfile.ZipFile(path) as zf:
                names = [n for n in zf.namelist()
                         if n.count("/") == 1 and n.endswith("/pyproject.toml")]
                if not names:
                    return False, None
                return True, tomllib.loads(zf.read(sorted(names)[0]).decode("utf-8")).get("build-system")
        with tarfile.open(path) as tf:
            members = [m for m in tf.getmembers()
                       if m.isfile() and m.name.count("/") == 1
                       and m.name.endswith("/pyproject.toml")]
            if not members:
                return False, None
            member = sorted(members, key=lambda m: m.name)[0]
            handle = tf.extractfile(member)
            if handle is None:
                raise RuntimeError(f"{path.name}: {member.name} is not extractable")
            return True, tomllib.loads(handle.read().decode("utf-8")).get("build-system")
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"{path.name}: {exc}") from exc


def load_recipes(packages_dir: Path):
    """name -> (yml_path, parsed data). Unparseable YAML is an undetermined verdict."""
    recipes, broken = {}, []
    for yml in sorted(packages_dir.rglob("package.yml")):
        try:
            data = yaml.safe_load(yml.read_text(encoding="utf-8")) or {}
        except (yaml.YAMLError, OSError) as exc:
            broken.append(f"{yml}: {exc}")
            continue
        if not isinstance(data, dict):
            broken.append(f"{yml}: top-level YAML is not a mapping")
            continue
        recipes[normalize(str(data.get("name", yml.parent.name)))] = (yml, data)
    return recipes, broken


def order_edges(recipes: dict) -> dict:
    """Direct build-order edges per recipe: build | host, exactly as the builder's
    graph resolves them (igos-build/graph.py)."""
    edges = {}
    for name, (_yml, data) in recipes.items():
        deps = data.get("dependencies") or {}
        build = deps.get("build") or []
        host = deps.get("host") or []
        edges[name] = {normalize(str(x)) for x in build} | {normalize(str(x)) for x in host}
    return edges


def supplied_set(name: str, edges: dict) -> set:
    """Everything guaranteed built before `name` — the transitive closure of its
    build-order edges. Cycle-safe."""
    seen, stack = set(), list(edges.get(name, ()))
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        stack.extend(edges.get(node, ()))
    return seen


def resolve_source_filename(data: dict):
    """The staged filename of the recipe's primary source, or None."""
    src = data.get("source")
    if not src or not isinstance(src, list) or not isinstance(src[0], dict):
        return None
    version = str(data.get("version", ""))
    parts = version.split(".")
    variables = {
        "name": str(data.get("name", "")),
        "version": version,
        "version_major": parts[0] if parts else "",
        "version_major_minor": ".".join(parts[:2]) if len(parts) >= 2 else version,
        "version_patch": parts[2] if len(parts) >= 3 else "0",
    }
    entry = src[0]
    raw_filename = entry.get("filename")
    if raw_filename:
        return _resolve(str(raw_filename), variables)
    url = _resolve(str(entry.get("url", "")), variables)
    if not url:
        return None
    return url.rsplit("/", 1)[-1].split("?")[0]


def evaluate(name, data, sdist, edges):
    """Verdict for one in-class recipe.

    Returns (status, detail) where status is one of:
      "ok"          the backend is supplied (detail says how)
      "violation"   named backend is not supplied (detail = what is missing)
      "undetermined" could not be decided (detail = why)
    """
    supplied = supplied_set(name, edges)

    try:
        had_pyproject, table = read_build_system(sdist)
    except RuntimeError as exc:
        return "undetermined", str(exc)

    if not had_pyproject or not table:
        # PEP 518 fallback: the setuptools legacy backend.
        why = "no pyproject.toml" if not had_pyproject else "no [build-system] table"
        if "setuptools" in supplied:
            return "ok", f"{why} -> setuptools legacy default, supplied"
        return "violation", (
            f"{why} -> PEP 518 falls back to the setuptools legacy backend, "
            f"but setuptools is not supplied")

    requires_raw = table.get("requires")
    if requires_raw is None:
        requires = []
    elif isinstance(requires_raw, list):
        requires = [requirement_name(str(x)) for x in requires_raw]
    else:
        return "undetermined", "[build-system].requires is not a list"

    backend = table.get("build-backend")

    if table.get("backend-path"):
        return "ok", f"backend-path set -> in-tree backend {backend!r}, nothing external needed"
    if not backend:
        if "setuptools" in supplied:
            return "ok", "no build-backend key -> setuptools legacy default, supplied"
        return "violation", (
            "no build-backend key -> PEP 518 falls back to the setuptools legacy "
            "backend, but setuptools is not supplied")
    if not requires:
        return "ok", f"backend {backend!r} with empty requires -> self-hosting bootstrap"

    root = normalize(re.split(r"[.:]", str(backend))[0])
    matches = [r for r in requires
               if r == root or r.replace("-", "") == root.replace("-", "")]
    if len(matches) == 1:
        provider = matches[0]
    elif len(requires) == 1:
        provider = requires[0]
    else:
        # Ambiguous provider. Do not guess: require the whole requires set, which
        # is a superset of whatever provides the backend.
        missing = sorted({r for r in requires if r not in supplied})
        if not missing:
            return "ok", (
                f"backend {backend!r} provider ambiguous among {requires}; "
                f"every requirement is supplied, so the provider is too")
        return "violation", (
            f"backend {backend!r} is provided by one of {requires} and the "
            f"provider cannot be identified from the name alone; not all of them "
            f"are supplied (missing: {', '.join(missing)})")

    if provider in supplied:
        return "ok", f"backend {backend!r} <- {provider}, supplied"
    return "violation", f"backend {backend!r} requires {provider!r}, which is not supplied"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--packages-dir", required=True, type=Path)
    ap.add_argument("--sources-dir", required=True, type=Path)
    ap.add_argument("--verbose", action="store_true",
                    help="print the satisfied recipes too, with how each is satisfied")
    args = ap.parse_args()

    recipes, broken = load_recipes(args.packages_dir)
    edges = order_edges(recipes)

    violations, undetermined, unstaged, passed = [], [], [], []

    for name in sorted(recipes):
        yml, data = recipes[name]
        build_sh = yml.parent / "build.sh"
        if not build_sh.exists() or not builds_with_no_build_isolation(build_sh):
            continue

        filename = resolve_source_filename(data)
        if not filename:
            undetermined.append(
                (name, "builds with --no-build-isolation but its primary source "
                       "filename could not be resolved from package.yml"))
            continue

        sdist = args.sources_dir / filename
        if not sdist.exists():
            unstaged.append((name, filename))
            continue

        status, detail = evaluate(name, data, sdist, edges)
        if status == "ok":
            passed.append((name, detail))
        elif status == "violation":
            violations.append((name, data, detail))
        else:
            undetermined.append((name, detail))

    in_class = len(passed) + len(violations) + len(undetermined) + len(unstaged)
    print(f"build-backend gate: {in_class} recipes build a Python source "
          f"distribution with --no-build-isolation")

    if args.verbose:
        for name, detail in passed:
            print(f"  ok        {name}: {detail}")

    for name, filename in unstaged:
        print(f"  NOT STAGED {name}: {filename} is not in "
              f"{args.sources_dir} — not checked")

    for name, detail in undetermined:
        print(f"  UNDETERMINED {name}: {detail}")

    for name, data, detail in violations:
        deps = data.get("dependencies") or {}
        declared_build = list(deps.get("build") or [])
        declared_host = list(deps.get("host") or [])
        print("")
        print(f"  REFUSED  {name}")
        print(f"    declared dependencies.build : {declared_build}")
        print(f"    declared dependencies.host  : {declared_host}")
        print(f"    the pinned source demands   : {detail}")
        print(f"    correction: add the missing distribution to "
              f"dependencies.build in packages/*/{name}/package.yml, so the "
              f"build order guarantees it is installed before this package "
              f"builds.")

    if broken:
        print("")
        for line in broken:
            print(f"  UNDETERMINED (unparseable recipe) {line}")

    print("")
    print(f"  satisfied: {len(passed)}   refused: {len(violations)}   "
          f"undetermined: {len(undetermined) + len(broken)}   "
          f"not staged: {len(unstaged)}")

    if violations:
        print("FAIL: a recipe cannot supply the build backend its pinned source "
              "demands; --no-build-isolation installs nothing, so the build would "
              "die at the first wheel invocation.")
        return 1
    if undetermined or broken:
        print("FAIL: at least one recipe's backend requirement could not be "
              "determined. An undetermined verdict is a failure, never a skip.")
        return 2
    if unstaged:
        print("FAIL: at least one in-class recipe's pinned source is not staged, "
              "so it was not checked. The named recipes above are unverified.")
        return 3
    print("OK: every recipe that builds with --no-build-isolation supplies the "
          "build backend its pinned source demands.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
