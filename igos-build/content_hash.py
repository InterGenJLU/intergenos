# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Source-aware change detection — the single source of truth for "did this
package's recipe OR its first-party source change since last build?"

Why this module exists (and is dependency-free): the build's skip-built
fingerprint used to hash only package.yml + build.sh. That is blind to
first-party source that lives OUTSIDE the recipe — a `generated: true` tarball
(no sha256 pin), a `source: []` package that builds straight from an in-tree
dir, or data/man/hook files sitting beside the recipe. A source-only edit
(e.g. assets/intergen-welcome/intergen-welcome.py, or an edit under intergen/)
therefore did NOT flip the recipe hash, so `--skip-built` silently SKIPPED the
package and a targeted build shipped the STALE binary. Folding source content
in closes that hole.

Kept import-light (stdlib only, duck-typed `pkg`) so BOTH the builder
(igos-build/tracker.py) and the host-side release auto-bump
(scripts/bump-changed-releases.py) use the EXACT same hashing — no drift
between "what triggers a rebuild" and "what triggers a release bump".
"""

import hashlib
import re
from pathlib import Path
from urllib.parse import urlparse


# Machine-owned bump lines stripped from every staged installer-hook package.yml
# by scripts/build-forge-tarball.sh (`sed -E '/^(release|content_hash):/d'`,
# item-8a). Mirrored byte-for-byte here so forge's hooks fingerprint depends on
# real hook content only — a release-only bump in another package (which rewrites
# just these two lines) must NOT drift forge. Col-0 anchored, whole line + its
# newline, exactly like the sed.
_HOOK_BUMP_LINE_RE = re.compile(rb"(?m)^(?:release|content_hash):[^\n]*\n?")


def _strip_hook_bump_lines(data: bytes) -> bytes:
    """Drop the release:/content_hash: top-level lines — byte-exact with the
    build-forge-tarball.sh sed that strips them from staged hook ymls."""
    return _HOOK_BUMP_LINE_RE.sub(b"", data)


def installer_hooks_fingerprint(repo_root, own_tier=None, own_name=None) -> str:
    """Hex digest of forge's NORMALIZED installer-hooks tree (item-8 completion,
    2026-07-08). Mirrors scripts/build-forge-tarball.sh's staging loop EXACTLY:

        for every packages/<tier>/<pkg>/ that has a build.sh, tier != toolchain:
            fold build.sh raw bytes + package.yml with the machine-owned
            release:/content_hash: lines stripped (item-8a).

    The forge tarball ships this per-package hook tree, so a hook edit in ANY
    non-toolchain package changes forge's shipped bytes and MUST drift forge's
    content fingerprint (the L30/forge-10 stale-hooks silent-loss class a static
    source_tree would miss); a release-only bump elsewhere rewrites only the
    stripped lines and must NOT drift forge (the item-8a feedback-loop guard).

    forge's OWN recipe (own_tier/own_name) is self-excluded — template_hash
    hashes it directly, and folding it here would double-count the recipe and
    re-couple content_hash to the very metadata the strip removes.

    Fail-closed: a missing packages/ dir raises (a malformed tree must never
    silently hash nothing — the stub-shape this module exists to prevent). The
    hook-set / strip parity with the real staging script is guarded by
    igos-build/tests (test_forge_hooks_fingerprint).
    """
    packages = Path(repo_root) / "packages"
    if not packages.is_dir():
        raise FileNotFoundError(
            f"installer_hooks fingerprint: {packages} missing — cannot mirror "
            "the forge staging loop"
        )
    h = hashlib.sha256()
    for tier_dir in sorted(p for p in packages.iterdir() if p.is_dir()):
        tier_name = tier_dir.name
        if tier_name == "toolchain":   # generator: `[ "$tier_name" = toolchain ] && continue`
            continue
        for pkg_dir in sorted(p for p in tier_dir.iterdir() if p.is_dir()):
            build_sh = pkg_dir / "build.sh"
            yml = pkg_dir / "package.yml"
            # generator: `[ -f "$build_sh" ] || [ -f "$pkg_yml" ] || continue`
            # (PI-ge9b04-C: yml-only recipes MUST ship — Forge's install-set
            # discovery + dep closure read the yml; dropping the dir made
            # plutosvg/plutovg invisible and the dogfood install shipped an
            # unloadable libSDL3_ttf. Fingerprint mirrors the staging rule.)
            if not build_sh.is_file() and not yml.is_file():
                continue
            pkg_name = pkg_dir.name
            if tier_name == own_tier and pkg_name == own_name:
                continue               # self-exclusion (template_hash covers it)
            h.update(f"{tier_name}/{pkg_name}".encode())
            if build_sh.is_file():
                h.update(b"\0hbuild\0")
                h.update(build_sh.read_bytes())
            if yml.is_file():
                h.update(b"\0hyml\0")
                h.update(_strip_hook_bump_lines(yml.read_bytes()))
    return h.hexdigest()


def url_basename(url: str) -> str:
    """Filename portion of a URL, stripping any ?query / #fragment."""
    return urlparse(url).path.rsplit("/", 1)[-1]


def repo_root_of(pkg) -> Path | None:
    """Repo root for a package, from its template path.

    `.../packages/<tier>/<name>/package.yml` -> the repo root is 4 levels up.
    Returns None if the layout doesn't match (tree hashing then no-ops).
    """
    tp = getattr(pkg, "template_path", None)
    if tp is None or len(tp.parents) < 4:
        return None
    return tp.parents[3]


# Ephemeral / build-and-test churn that must NEVER affect a content hash.
# The hash runs in TWO trees that differ: the host-side release auto-bump
# walks a DEV working tree (where tests/builds have left .pytest_cache,
# .coverage, *.egg-info, .mypy_cache, editor swaps), while the build VM's
# chroot walks an rsync'd copy. Hashing those would make the recorded
# baseline one the builder never reproduces — drift that breaks the exact
# no-drift property this module exists to guarantee (review finding on
# e72aa5f8). Pure git-tracked hashing is the textbook answer but is unviable
# here: the chroot recipe copy carries NO .git (the rsync copies the source
# subdirs, not the repo-root .git), so `git ls-files` cannot run builder-side.
# A comprehensive, identical-in-both-contexts exclusion set is the no-drift
# fix that works without git. Keep this superset of the deterministic
# source-tarball generator's excludes so a tree hash still matches what gets
# packaged.
_EPHEMERAL_DIR_PARTS = frozenset({
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox",
    ".git", ".cache", "htmlcov", "node_modules", ".idea", ".vscode",
})
_EPHEMERAL_SUFFIXES = (".pyc", ".pyo", ".swp", ".swo", ".orig", ".rej")

# Generated eval-corpus artifacts (the demand-bank MERGE OUTPUTS): corpus_merge
# writes bank.jsonl + bank.report.json beside the committed half-files, and the
# co-located intergen/tests/demand_corpus/.gitignore declares them generated —
# they are regenerated per --demand-bank run and NEVER committed. A dev tree that
# has run that battery carries them while a clean checkout (and the chroot's rsync
# of TRACKED files) does not, so hashing them records a baseline the builder never
# reproduces — the poisoned-baseline class this exclusion set exists to prevent
# (the exact drift the module header warns about; a recorded hash matching no clean
# state also masks the NEXT real content change from skip-built). Matched on
# (parent-dir, basename) so a legitimately-committed same-named file elsewhere is
# never dropped. NEW generated eval-corpus artifacts get added here
# (self-unenforceable allowlist, same maintenance class as _BUILD_AFFECTING_YML_KEYS).
_EPHEMERAL_GENERATED = frozenset({
    ("demand_corpus", "bank.jsonl"),
    ("demand_corpus", "bank.report.json"),
})


def _is_ephemeral(p: Path) -> bool:
    parts = p.parts
    if _EPHEMERAL_DIR_PARTS.intersection(parts):
        return True
    if any(part.endswith(".egg-info") for part in parts):
        return True
    if len(parts) >= 2 and (parts[-2], parts[-1]) in _EPHEMERAL_GENERATED:
        return True
    if p.suffix in _EPHEMERAL_SUFFIXES:
        return True
    name = p.name
    return (
        name in (".DS_Store", ".coverage")
        or name.startswith(".coverage.")
        or name.endswith("~")
    )


def iter_tree_files(root: Path):
    """Yield (relative-name, Path) for files under `root`, deterministically.

    Sorted order; skips ephemeral build/test/editor churn (see
    `_is_ephemeral`) — applied identically host-side and in the chroot — so the
    digest depends only on real source content and not on working-tree
    cleanliness. Accepts a file root too (single file).
    """
    if not root.exists():
        return
    if root.is_file():
        candidates = [root]
        base = root.parent
    else:
        candidates = sorted(p for p in root.rglob("*") if p.is_file())
        base = root
    for p in candidates:
        # Evaluate ephemerality on the path RELATIVE to the tree base — NOT the
        # absolute path. The absolute parts include every ANCESTOR dir of the
        # checkout, so if the repo lived under a dir whose name is in the
        # ephemeral set (.cache/.vscode/.tox/git-metadata/...), every file would
        # classify ephemeral, the digest would collapse to empty, and the
        # no-drift guarantee would invert SILENTLY into never-detecting-a-change
        # → shipping the stale binary, the exact hole this module closes. The
        # relative path confines the match strictly inside the hashed tree.
        # (WC review of 6848bc17.)
        rel = p.relative_to(base)
        if _is_ephemeral(rel):
            continue
        yield str(rel), p


def source_content_hash(pkg, sources_dir) -> str:
    """Hex digest of a package's SOURCE content only — NOT package.yml/build.sh.

    Folds in (a) each `generated: true` tarball's deterministic bytes, (b) the
    package's own dir contents (data/man/hooks/helpers) for first-party
    packages NOT carried by a sha-pinned upstream tarball, and (c) each
    declared `source_tree:` external in-tree dir. Returns "" when the package
    has no out-of-recipe source (upstream sha-pinned, or genuinely sourceless)
    — those are already fully covered by the recipe hash, so excluding them
    keeps their fingerprint byte-identical to the pre-change scheme (no
    spurious mass rebuild of the ~700 upstream packages).

    package.yml and build.sh are excluded by design: the recipe is hashed
    directly by template_hash, and excluding package.yml keeps the release
    auto-bump from re-triggering on its own bump.
    """
    h = hashlib.sha256()
    contributed = False
    sources = list(getattr(pkg, "source", None) or [])

    # (a) generated tarballs — OUTPUT bytes, hashed ONLY for packages that do
    # NOT declare their canonical inputs via `source_tree:`. Byte-hashing every
    # generated source was the original scheme, on the assumption the
    # generators were fully deterministic; ledger item 8 (2026-07-05) DISPROVED
    # that — a regeneration in a different context (umask-inherited staged-dir
    # modes, staged bump metadata) drifted the bytes with ZERO content change
    # and phantom-bumped 7 releases. A package that declares `source_tree:`
    # therefore fingerprints its INPUTS (the asset files + generator script,
    # hashed in (c) below) and the tarball bytes are deliberately ignored: a
    # fingerprint must never depend on a generation context it cannot see.
    # Packages without a declared source_tree keep byte-hashing, and the bump
    # tool keeps refusing to run when their tarball is missing.
    inputs_declared = bool(getattr(pkg, "source_tree", None))
    for src in sources:
        if getattr(src, "generated", False) and sources_dir and not inputs_declared:
            tarball = Path(sources_dir) / (getattr(src, "filename", None) or url_basename(src.url))
            if tarball.exists():
                h.update(b"\0gen\0")
                h.update(tarball.read_bytes())
                contributed = True

    # (b) the package's own dir, for first-party (non-sha-pinned) packages.
    pinned = any(getattr(s, "sha256", None) for s in sources)
    tp = getattr(pkg, "template_path", None)
    if not pinned and tp is not None:
        for name, p in iter_tree_files(tp.parent):
            if p.name in ("package.yml", "build.sh"):
                continue
            h.update(b"\0pkg\0")
            h.update(name.encode())
            h.update(b"\0")
            h.update(p.read_bytes())
            contributed = True

    # (c) declared external in-tree source dirs (intergen -> intergen/, etc.).
    repo_root = repo_root_of(pkg)
    if repo_root is not None:
        for rel in getattr(pkg, "source_tree", None) or []:
            for name, p in iter_tree_files(repo_root / rel):
                h.update(b"\0tree\0")
                h.update(name.encode())
                h.update(b"\0")
                h.update(p.read_bytes())
                contributed = True

    # (d) forge-only (installer_hooks: true): the NORMALIZED installer-hooks
    # fingerprint. The forge tarball bundles every non-toolchain package's
    # build.sh + stripped package.yml as installer-hooks/, beyond its source_tree
    # static core (installer/, man/forge.1, docs/users, the generator script),
    # so those hook bytes must feed forge's fingerprint or a stale-hooks forge
    # ships unbumped. Retires forge's tarball-byte hashing (item 8 disproved it):
    # declaring source_tree above already set inputs_declared, so (a) skipped the
    # byte-hash; this fold supplies the dynamic remainder. See
    # installer_hooks_fingerprint for the exact staging-loop mirror + self-excl.
    if getattr(pkg, "installer_hooks", False) and repo_root is not None:
        own_tier = own_name = None
        tp2 = getattr(pkg, "template_path", None)
        if tp2 is not None:
            own_tier = tp2.parent.parent.name
            own_name = tp2.parent.name
        h.update(b"\0hooks\0")
        h.update(installer_hooks_fingerprint(repo_root, own_tier, own_name).encode())
        contributed = True

    return h.hexdigest() if contributed else ""


def _feature_matrix_bytes(tp) -> bytes:
    """Raw bytes of the recipe's feature-matrix.json sidecar (b"" when absent).

    The single source for BOTH template_hash and content_fingerprint: the
    matrix pins the recipe's resolved build surface (mesa/lib32-mesa consume
    it in configure), so an edit must flip the skip-built key AND advance the
    release exactly like a configure_flags change. Feeding both hashes from
    this one helper is what keeps them from drifting apart again (a
    matrix-only edit used to advance the release but stay invisible to
    --skip-built, silently shipping the stale build).
    """
    matrix = tp.parent / "feature-matrix.json"
    if matrix.exists():
        return matrix.read_bytes()
    return b""


def sibling_shipped_bytes(pkg) -> bytes:
    """The files in a recipe's own directory that NOTHING ELSE hashes.

    WHAT WAS UNCOVERED. source_content_hash clause (b) already folds a
    package's own directory — but only when the package is not carried by a
    sha-pinned upstream tarball. A pinned package can still ship files we
    wrote: install hooks, helper programs, whole files/ trees, apparmor
    profiles, systemd units, patches, shipped documents. Those become
    installed bytes and reached neither hash. Measured 2026-08-05: 68 packages
    were in exactly that position, and a probe line planted in one of their
    shipped hook scripts produced a drift report naming a DIFFERENT package.

    So this helper is the COMPLEMENT of clause (b), not a repeat of it:
      * pinned package    -> fold every sibling file and symlink;
      * unpinned package  -> fold symlinks ONLY, because clause (b) already
        folds the regular files, and re-folding them would move every existing
        baseline and force a rebuild of the whole first-party set for no gain
        in coverage.

    SYMLINKS ARE COVERED FOR BOTH, and that is a second small hole closed
    here. iter_tree_files selects on is_file(), so a shipped symlink whose
    target is a directory or does not resolve is invisible to clause (b) —
    the base-files package ships three such compatibility links, and
    retargeting one changes what lands on an installed system while moving no
    hash. The link is hashed as its target TEXT, never followed: following it
    would read the wrong bytes or none.

    THE SET IS DERIVED, NOT LISTED. Naming the directories (hooks/, helper/,
    files/, …) would reproduce, one level up, the very defect this closes —
    the next directory somebody invents would be invisible again. The rule is
    positional: everything under the recipe's directory except the two files
    folded by another route — build.sh, hashed directly by both callers, and
    package.yml, whose build-affecting FIELDS are folded instead, because
    folding the whole file would make the fingerprint self-referential (the
    release auto-bump writes release: and content_hash: back into it).
    Ephemeral churn is filtered by the same rule the source trees use, so the
    digest depends on real content and not on working-tree cleanliness.

    FED TO BOTH HASHES FROM HERE, for the reason the feature-matrix helper
    below already states in its own words: a fold that reaches the release
    gate but not the skip-built key advances a release while the build is
    skipped, which ships the previous bytes under a new release number. That
    is worse than either gap alone, so the two must never be able to drift.
    """
    tp = getattr(pkg, "template_path", None)
    if tp is None:
        return b""
    d = tp.parent
    if not d.is_dir():
        return b""
    pinned = any(getattr(s, "sha256", None) for s in (getattr(pkg, "source", None) or []))
    h = hashlib.sha256()
    contributed = False
    for p in sorted(d.rglob("*")):
        rel = p.relative_to(d)
        if str(rel) in ("build.sh", "package.yml"):
            continue
        if _is_ephemeral(rel):
            continue
        if p.is_symlink():
            payload = b"\0link\0" + str(p.readlink()).encode()
        elif pinned and p.is_file():
            payload = b"\0file\0" + p.read_bytes()
        else:
            # A directory (its files are reached on their own iterations), or
            # an unpinned package's regular file, which source_content_hash
            # clause (b) already folds.
            continue
        h.update(b"\0name\0")
        h.update(str(rel).encode())
        h.update(payload)
        contributed = True
    return h.digest() if contributed else b""


def template_hash(pkg, sources_dir) -> str:
    """16-char fingerprint of recipe + source content (the skip-built key).

    Backward-compatible: when a package has no out-of-recipe source content
    (source_content_hash == "") and no feature-matrix sidecar, this returns
    sha256(package.yml + build.sh) EXACTLY as the pre-change scheme did, so
    existing manifests still match and sha-pinned packages do not rebuild
    spuriously. Matrix-carrying packages flip ONCE when the fold lands —
    that one-time rebuild is the fix, not a regression. Returns "" when pkg
    has no template_path (skip-built then defaults to skip — unchanged).
    """
    tp = getattr(pkg, "template_path", None)
    if tp is None:
        return ""
    h = hashlib.sha256()
    for tpl_file in [tp, tp.parent / "build.sh"]:
        if tpl_file.exists():
            h.update(tpl_file.read_bytes())
    fm = _feature_matrix_bytes(tp)
    if fm:
        h.update(b"\0feature-matrix\0")
        h.update(fm)
    sib = sibling_shipped_bytes(pkg)
    if sib:
        h.update(b"\0siblings\0")
        h.update(sib)
    sch = source_content_hash(pkg, sources_dir)
    if sch:
        h.update(b"\0src\0")
        h.update(sch.encode())
    return h.hexdigest()[:16]


# package.yml top-level keys that change installed BYTES but live in the recipe
# metadata, not build.sh: the configure/build flag sets + the auto-applied patch
# list. A change to these must advance the auto-bump release (skip-built's
# template_hash already rebuilds on them — it hashes the whole package.yml — so
# this closes only the bump-SIDE gap, the inverse of the stale-ship bug). These
# keys are ABSENT on every first-party (auto-bump-tracked) package today, so
# folding them changes NO existing content_hash baseline (backward-compatible).
# version/release/content_hash (auto-written) and build_style/dependencies
# (present on existing packages) are deliberately NOT folded — they would
# spuriously re-baseline the whole first-party set. (WC review finding D.)
#
# MAINTENANCE (self-unenforceable allowlist, same class as
# check-source-tree-coverage.py's _EXTERNAL_TOPS): a NEW build-affecting
# top-level recipe key added to the schema will REBUILD (template_hash hashes
# the whole package.yml) but will NOT advance the auto-bump release until it is
# added here too — the exact finding-D class, one key over. Add any new
# byte-affecting top-level key to this tuple. (build_style/dependencies stay out
# by design — folding them would mass-re-baseline; a build_style change in
# isolation from build.sh is a known narrow residual, rare since it usually
# co-varies with the build script.)
_BUILD_AFFECTING_YML_KEYS = ("configure_flags", "patches", "cmake_args", "meson_args")


def _build_affecting_recipe_fields(tp) -> bytes:
    """Raw bytes of the build-affecting top-level package.yml blocks (see
    `_BUILD_AFFECTING_YML_KEYS`). Empty when none are present — so a package
    without them contributes nothing and its fingerprint is byte-identical to the
    pre-change scheme. Text-based (stdlib-only, no yaml dep): a top-level key has
    no leading whitespace; its block is the key line plus the following
    more-indented lines."""
    if tp is None or not tp.exists():
        return b""
    out = []
    capturing = False
    for line in tp.read_text().splitlines(keepends=True):
        body = line.rstrip("\n")
        if body and not body[0].isspace():            # a top-level key line
            key = body.split(":", 1)[0].strip()
            capturing = key in _BUILD_AFFECTING_YML_KEYS
            if capturing:
                out.append(line)
            continue
        if capturing:                                 # indented/blank continuation
            out.append(line)
    return "".join(out).encode()


def content_fingerprint(pkg, sources_dir, include_siblings=True) -> str:
    """Full-precision content fingerprint for the release auto-bump.

    = sha256(build.sh + build-affecting package.yml fields + source_content_hash).
    EXCLUDES the auto-written package.yml fields (version/release/content_hash) and
    the non-byte-affecting/already-present ones so that writing the bumped release
    (and the recorded content_hash) back does not re-trigger a bump, and so the
    existing first-party baselines do not spuriously re-bump. INCLUDES build.sh and
    the build-affecting recipe fields (configure_flags/patches/...) — both change
    installed bytes — so a change to either advances the release. Returns "" for a
    package with no build.sh, no out-of-recipe source, and no recipe fields.
    """
    h = hashlib.sha256()
    contributed = False
    tp = getattr(pkg, "template_path", None)
    if tp is not None:
        build_sh = tp.parent / "build.sh"
        if build_sh.exists():
            h.update(b"\0buildsh\0")
            h.update(build_sh.read_bytes())
            contributed = True
        recipe_fields = _build_affecting_recipe_fields(tp)
        if recipe_fields:
            h.update(b"\0recipe\0")
            h.update(recipe_fields)
            contributed = True
        # RT-3 feature-matrix sidecar (decided 2026-07-02): the matrix pins
        # the recipe's resolved build surface, so an edit to it changes
        # installed bytes exactly like configure_flags — fold its raw bytes
        # in. Absent on every package without a matrix, so no existing
        # baseline moves (the same backward-compat property as the
        # recipe-fields fold above). Sourced from the shared helper so the
        # skip-built key (template_hash) can never drift from this fold.
        fm = _feature_matrix_bytes(tp)
        if fm:
            h.update(b"\0feature-matrix\0")
            h.update(fm)
            contributed = True
        # The recipe-directory files nothing else hashes — see
        # sibling_shipped_bytes for exactly which those are and why the set is
        # derived rather than listed. Same helper as template_hash, so the
        # release gate and the skip-built key cannot disagree about what a
        # recipe contains.
        #
        # include_siblings=False reproduces the fingerprint EXACTLY as it was
        # defined before this fold existed. It has one caller and one purpose:
        # the release tool's re-baseline mode proves a package's drift is
        # explained entirely by the definition widening, and refuses to absorb
        # it otherwise. Nothing else may pass False — a caller that did would
        # be asking to be told the old answer.
        sib = sibling_shipped_bytes(pkg) if include_siblings else b""
        if sib:
            h.update(b"\0siblings\0")
            h.update(sib)
            contributed = True
    sch = source_content_hash(pkg, sources_dir)
    if sch:
        h.update(b"\0src\0")
        h.update(sch.encode())
        contributed = True
    # Truncated to 16 hex: enough to detect a content change (matches the
    # template_hash convention) AND short enough that the public-content audit
    # does not flag the recorded baseline as a possible 64-hex secret.
    return h.hexdigest()[:16] if contributed else ""
