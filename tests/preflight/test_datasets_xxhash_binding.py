"""The datasets package imports a Python xxhash module, and we must carry it.

`datasets/fingerprint.py` does `import xxhash` at module scope, and
`datasets/__init__.py` imports from `fingerprint`, so `import datasets` executes
that import. Upstream's own metadata says the same thing (`Requires-Dist:
xxhash`). The tier: core `xxhash` package cannot satisfy it — that package is
the `libxxhash` shared library and the `xxhsum` CLI, which no Python import can
use. The binding is a separate package, `python-xxhash`, because package names
are globally unique in the dependency graph.

Nothing else in the tree checks this pairing. The tier-coverage gate proves a
package is reachable; the tier validator proves it is tiered defensibly;
neither notices if `datasets` goes back to depending on the C library, or if
the binding stops being compiled against the library it declares.

These tests also pin the system-link decision. Upstream vendors its own copy of
xxHash (0.8.2 in the 3.8.1 release archive) and uses it unless
`XXHASH_LINK_SO=1` is set, in which case it links `libxxhash` instead. We link.
A vendored copy compiled into a Python extension is invisible to the package
graph and would have to be patched separately from the library the rest of the
distribution links, so if `build.sh` ever stops setting that variable, the
recipe's declared dependency on `xxhash` silently stops being true.

Nothing here reads the network, needs privilege, or builds anything.
"""
from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

parser = importlib.import_module("igos-build.parser")

BINDING = "python-xxhash"
C_LIBRARY = "xxhash"
CONSUMER = "datasets"

BINDING_DIR = REPO / "packages" / "ai" / BINDING
AI_DRIVER = REPO / "scripts" / "chroot-build-ai.sh"
TIER_VALIDATOR = REPO / "scripts" / "validate-package-tiers.py"


def _recipe(tier: str, name: str):
    return parser.parse_template(REPO / "packages" / tier / name / "package.yml")


def _raw(tier: str, name: str) -> dict:
    path = REPO / "packages" / tier / name / "package.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_the_binding_recipe_exists_and_is_ai_tier_mirror_only():
    pkg = _recipe("ai", BINDING)
    assert pkg.name == BINDING
    assert pkg.tier == "ai"
    # Its only consumer is the ai-tier datasets package, which is itself
    # mirror-only; shipping the binding on the ISO would add a package nothing
    # on the target imports.
    assert pkg.iso_include is False


def test_the_binding_is_not_named_after_the_c_library():
    """Package names are globally unique in the dependency graph: a second
    recipe named `xxhash` is a duplicate-name halt, not a warning."""
    assert BINDING != C_LIBRARY
    providers = sorted(p.parent.parent.name for p in (REPO / "packages").glob(f"*/{C_LIBRARY}/package.yml"))
    assert providers == ["core"], (
        f"expected exactly one recipe named {C_LIBRARY!r}, in the core tier; found it under {providers}")


def test_the_c_library_recipe_is_still_the_c_library():
    """If this ever becomes the Python binding, the split below is wrong and
    the two packages need re-deciding rather than assuming."""
    pkg = _recipe("core", C_LIBRARY)
    assert pkg.tier == "core"
    raw = _raw("core", C_LIBRARY)
    paths = raw.get("verify_paths") or []
    assert any(p.endswith("/libxxhash.so") for p in paths), (
        f"{C_LIBRARY} no longer verifies libxxhash.so — it may no longer be the C library")


def test_datasets_depends_on_the_binding_and_not_on_the_bare_c_library():
    """The C library satisfies a resolver but not an import. Depending on it
    directly is what let datasets ship with an unsatisfiable import."""
    pkg = _recipe("ai", CONSUMER)
    runtime = set(pkg.dependencies.runtime)
    assert BINDING in runtime, (
        f"{CONSUMER} does not declare {BINDING!r}, so nothing installs the module "
        f"its fingerprint.py imports at load time")
    assert C_LIBRARY not in runtime, (
        f"{CONSUMER} declares {C_LIBRARY!r} directly. That is the C library; it makes the "
        f"dependency look satisfied while the Python import still fails. The binding "
        f"declares it instead, so it still resolves transitively.")


def test_the_binding_declares_the_c_library_both_ways():
    """It needs the header at build time and carries NEEDED libxxhash.so.0 at
    runtime, because it is compiled against the library rather than the
    vendored sources."""
    pkg = _recipe("ai", BINDING)
    assert C_LIBRARY in set(pkg.dependencies.build), (
        f"{BINDING} does not build-depend on {C_LIBRARY}; the linked build needs "
        f"/usr/include/xxhash.h")
    assert C_LIBRARY in set(pkg.dependencies.runtime), (
        f"{BINDING} does not runtime-depend on {C_LIBRARY}; the extension links "
        f"libxxhash.so.0")


def test_the_build_links_the_system_library_rather_than_the_vendored_copy():
    """The coupling that makes the declared runtime dependency true. Without
    XXHASH_LINK_SO upstream statically compiles its own bundled xxHash and the
    extension links nothing — the dependency above would then be a fiction."""
    text = (BINDING_DIR / "build.sh").read_text(encoding="utf-8")
    build_body = text[text.index("build()"):text.index("do_install()")]
    assert re.search(r"XXHASH_LINK_SO=1", build_body), (
        "packages/ai/python-xxhash/build.sh no longer sets XXHASH_LINK_SO=1 in build(); "
        "the extension would compile the vendored xxHash copy instead of linking the "
        "xxhash package, making its declared dependency on that package untrue")


def test_the_binding_verifies_the_compiled_extension():
    """A pure-Python install would satisfy an import test and still be the
    wrong artifact; the extension module is what proves the C binding built."""
    raw = _raw("ai", BINDING)
    paths = raw.get("verify_paths") or []
    assert any(re.search(r"/xxhash/_xxhash\.cpython-\d+-.*\.so$", p) for p in paths), (
        f"{BINDING} does not verify the compiled extension module; verify_paths={paths}")


def test_the_source_is_a_pinned_upstream_archive():
    pkg = _recipe("ai", BINDING)
    assert pkg.source, f"{BINDING} declares no source"
    for src in pkg.source:
        assert not src.generated
        assert src.sha256 and re.fullmatch(r"[0-9a-f]{64}", src.sha256)


def test_every_declared_dependency_names_a_package_we_carry():
    """A declared dep that names nothing is worse than an undeclared one: it
    reads as covered."""
    pkg = _recipe("ai", BINDING)
    for kind, deps in (("build", pkg.dependencies.build),
                       ("runtime", pkg.dependencies.runtime)):
        for dep in deps:
            found = list((REPO / "packages").glob(f"*/{dep}/package.yml"))
            assert found, f"{BINDING} {kind}-deps {dep!r}, which no recipe provides"


def test_the_binding_is_reachable_through_the_ai_tier_driver():
    """ai-tier packages carry no run_package line: the driver builds the whole
    tier through the resolver, so reachability is the tier declaration plus the
    driver actually asking for that tier."""
    assert (BINDING_DIR / "package.yml").exists()
    text = AI_DRIVER.read_text(encoding="utf-8")
    assert re.search(r"--tier\s+ai\b", text), (
        "scripts/chroot-build-ai.sh no longer builds the ai tier through igos-build; "
        "ai-tier packages would be unreachable")


def test_the_binding_is_named_in_the_tier_validators_ai_set():
    """Consumer inference in validate-package-tiers.py builds its reverse graph
    from build/host edges only, so a runtime-only library has no consumer edge
    and classifies UNCLEAR. Runtime-only ai libraries are named explicitly; if
    this entry goes away the validator returns a non-OK row."""
    text = TIER_VALIDATOR.read_text(encoding="utf-8")
    ai_stack = text[text.index("AI_STACK = {"):]
    ai_stack = ai_stack[:ai_stack.index("\n}")]
    assert f'"{BINDING}"' in ai_stack, (
        f"{BINDING} is not named in AI_STACK in scripts/validate-package-tiers.py")


@pytest.mark.parametrize("name,tier", [(BINDING, "ai"), (C_LIBRARY, "core")])
def test_both_packages_live_in_the_directory_their_tier_names(name, tier):
    """The tier field and the directory must agree; the tier-coverage gate
    walks directories."""
    assert (REPO / "packages" / tier / name / "package.yml").exists()
    assert _recipe(tier, name).tier == tier
