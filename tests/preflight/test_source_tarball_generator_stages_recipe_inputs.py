"""The in-tree source-tarball generator must stage every file the recipe installs.

`scripts/build-intergenos-source-tarballs.sh` snapshots `assets/intergen-welcome/`
into the package's generated tarball, file by file (an explicit `install` per
asset). `packages/desktop/intergen-welcome/build.sh` installs those files from the
extracted tarball by bare name. The two lists are maintained by hand in two files,
and they have drifted twice: `org.intergenos.Wiki.svg` (2026-07-30) and
`org.intergenos.welcome.policy` (2026-08-27) were each added to the recipe's
do_install without being staged by the generator, so a build from a freshly
generated tarball failed at `install: cannot stat`. The tarball-membership gate
catches that at build pre-flight — the most expensive place. This test catches it
at commit time, from the two files alone: no tarball is built and nothing under
build/ is touched.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GENERATOR = REPO_ROOT / "scripts" / "build-intergenos-source-tarballs.sh"
RECIPE = REPO_ROOT / "packages" / "desktop" / "intergen-welcome" / "build.sh"
ASSET_DIR = REPO_ROOT / "assets" / "intergen-welcome"

# `install [-m NNN] <bare-file> <dest>` inside do_install: a source that is a
# bare relative name (no `/`, no `$`) comes from the extracted tarball root.
_INSTALL_SRC = re.compile(r"^\s*install\s+(?:-m\s*\d+\s+)?([A-Za-z0-9_.@+][A-Za-z0-9_.@+-]*)\s")


def _do_install_body(text: str) -> str:
    start = text.index("do_install()")
    depth = 0
    for i, ch in enumerate(text[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i]
    raise AssertionError("do_install() body has no closing brace")


def _recipe_tarball_inputs() -> list[str]:
    body = _do_install_body(RECIPE.read_text(encoding="utf-8"))
    found = []
    for line in body.splitlines():
        m = _INSTALL_SRC.match(line)
        if m and m.group(1) not in found:
            found.append(m.group(1))
    return found


def _generator_welcome_block() -> str:
    text = GENERATOR.read_text(encoding="utf-8")
    start = text.index("build_intergen_welcome()")
    end = text.index("\n}\n", start)
    return text[start:end]


def test_recipe_installs_at_least_the_known_inputs():
    inputs = _recipe_tarball_inputs()
    for name in ("intergen-welcome.py", "intergen-welcome-privhelper",
                 "org.intergenos.welcome.policy", "net_diagnostics.py"):
        assert name in inputs, f"{name} not found among do_install inputs: {inputs}"


def test_generator_stages_every_file_the_recipe_installs():
    block = _generator_welcome_block()
    missing = [name for name in _recipe_tarball_inputs()
               if f'"$stage/iw-pkg/{name}"' not in block]
    assert not missing, (
        "packages/desktop/intergen-welcome/build.sh installs these files from the "
        "generated tarball, but scripts/build-intergenos-source-tarballs.sh "
        f"build_intergen_welcome() never stages them: {missing}")


def test_every_staged_asset_exists_in_the_tree():
    block = _generator_welcome_block()
    staged = re.findall(r'install -m\d+ "\$src/([A-Za-z0-9_.@+-]+)" "\$stage/iw-pkg/', block)
    assert staged, "no asset-dir stage lines found in build_intergen_welcome()"
    absent = [name for name in staged if not (ASSET_DIR / name).is_file()]
    assert not absent, f"generator stages files absent from assets/intergen-welcome/: {absent}"
