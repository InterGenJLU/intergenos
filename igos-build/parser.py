# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Package template parser for igos-build.

Reads package.yml templates, validates required fields and types,
resolves variable substitutions, and returns validated Package objects.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Forensic-trace shim. Defensive import.
try:
    from . import _trace
    _TRACE_AVAILABLE = True
except ImportError:
    _trace = None
    _TRACE_AVAILABLE = False


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Source:
    """A source tarball or file to download — or generate in-tree.

    A `generated: true` source is a first-party tarball built from in-tree
    content by scripts/build-*-tarball*.sh. It is NOT downloaded and carries
    NO sha256: its compressed bytes depend on the local tar/xz, so a committed
    pin would be unportable across builders. Integrity comes from git (the
    source content) + the deterministic generator, not a hash of the output.
    """
    url: str
    sha256: str | None = None
    filename: str | None = None
    generated: bool = False
    # extract: false = the source is a plain file, not an archive (e.g. a
    # pinned .phar or .run payload); the builder verifies its checksum but
    # skips extraction — build.sh consumes it from IGOS_SOURCES directly.
    extract: bool = True
    # redistributable: false = this input may be fetched and used to BUILD, but
    # its bytes may not be republished by us. It is staged into the build like
    # any other pinned source and is never placed in a corresponding-source
    # archive; scripts/build-source-archives.py refuses to bundle it and writes
    # a pointer note (vendor URL + sha256) in its place, so the source archive
    # states plainly what is absent and where the vendor publishes it.
    #
    # First user: the NVIDIA CUDA toolkit runfile that compute/llama-cpp-cuda
    # compiles against. nvcc is not redistributable under NVIDIA's CUDA EULA.
    # The USER-facing half of the same problem is solved by the download-helper
    # package compute/cuda-toolkit; this flag is the BUILD-facing half, and it
    # exists so the guarantee is enforced rather than incidental — without it,
    # the exclusion holds only because the generator happens to bundle source[0]
    # alone, and a later completeness fix there would start republishing a
    # vendor's non-redistributable installer with nothing to stop it.
    redistributable: bool = True


@dataclass
class Dependencies:
    """Package dependency declarations."""
    build: list[str] = field(default_factory=list)
    host: list[str] = field(default_factory=list)
    runtime: list[str] = field(default_factory=list)


@dataclass
class PatchEntry:
    """A patch file with optional integrity verification."""
    file: str
    sha256: str | None = None


@dataclass
class ValidationCheck:
    """A post-build validation step."""
    type: str                        # sanity_check, footprint, checksum, test_suite
    description: str = ""
    script: str | None = None
    expect_contains: str | None = None
    fatal: bool = True


@dataclass
class Package:
    """A fully parsed and validated package template."""
    name: str
    version: str
    release: int
    description: str
    license: str

    source: list[Source]
    dependencies: Dependencies
    build_style: str                 # autotools, cmake, meson, make, custom

    # Classification
    tier: str = "core"               # toolchain, core, base, desktop, ai, compute, extra

    # ISO inclusion. tier:extra default = False (MIRROR — pkm install on
    # demand from the InterGenOS mirror). All other tiers default = True
    # (shipped in the squashfs). Explicit override available per-package.
    # See docs/extra-tier-classification.md for the v1.0 ISO whitelist.
    iso_include: bool | None = None  # None => apply tier-based default at parse time

    # In-tree source paths (repo-relative dirs OR files) for first-party
    # packages whose content lives outside the recipe dir: `source: []`
    # packages that build straight from a tree (intergen -> [intergen],
    # pkm -> [pkm]), and `generated: true` tarball packages declaring the
    # CANONICAL INPUTS their tarball is generated from (asset files + the
    # generator script). The recursive file content is folded into the
    # skip-built fingerprint (tracker.source_content_hash) so a source-only
    # edit forces a rebuild instead of being silently skipped. For a
    # `generated: true` source, declaring source_tree ALSO switches its
    # content fingerprint from output-tarball bytes to these inputs —
    # byte-hashing proved context-sensitive (umask-inherited modes, staged
    # bump metadata) and phantom-bumped releases with zero content change
    # (ledger item 8, 2026-07-05). Generated sources WITHOUT source_tree
    # fall back to byte-hashing (legacy).
    source_tree: list[str] = field(default_factory=list)

    # `installer_hooks: true` — forge-only (item-8 completion, 2026-07-08). The
    # forge tarball bundles, beyond source_tree's static core, a NORMALIZED
    # installer-hooks tree: every non-toolchain package's build.sh (raw) +
    # package.yml (release:/content_hash: stripped), staged by
    # scripts/build-forge-tarball.sh. Those per-package hook bytes change forge's
    # shipped tarball, so they must feed forge's content fingerprint too — a
    # static source_tree omitting them would ship a stale-hooks forge unbumped
    # (the L30/forge-10 silent-loss class). When set, content_hash folds the
    # normalized hooks fingerprint (content_hash._installer_hooks_fingerprint,
    # which mirrors the staging loop; a parity test guards against drift).
    installer_hooks: bool = False

    # Archive-time ELF word-size contract (elfaudit.py). "64" (default):
    # every ELF object the package archives must be ELFCLASS64. "32": the
    # lib32-* case. "mixed": the package legitimately carries both widths
    # (declare explicitly with an in-recipe comment — never a hidden
    # allowlist in the audit).
    elf_class: str = "64"

    # Path-scoped width-audit exemptions (elfaudit.py, launch-gate L9):
    # root-relative fnmatch globs covering INERT foreign-width ELF payload
    # (e.g. go's src testdata fixtures). Every exempted file is reported
    # loudly; a glob exempting nothing refuses the archive (stale
    # declaration). Declare with an in-recipe comment, same governance as
    # elf_class: mixed.
    elf_class_exempt: list[str] = field(default_factory=list)

    # Optional metadata
    homepage: str | None = None

    # Build configuration
    configure_flags: list[str] = field(default_factory=list)
    patches: list[PatchEntry] = field(default_factory=list)

    # Toolchain-specific
    target_triple: str | None = None
    pass_number: int | None = None
    bundled_deps: list[str] = field(default_factory=list)

    # Install function name for custom build style
    install_func: str = "do_install"  # "do_install" (default) or "install" (toolchain only)

    # Install directly to / instead of DESTDIR staging (for multi-pass builds)
    direct_install: bool = False

    # Skip package tracking (for pass packages that overwrite existing files)
    skip_tracking: bool = False

    # Declared GPU ISA targets for target-sensitive compute packages,
    # semicolon-separated. Two vendor token shapes are accepted, because the
    # two GPU compute stacks name their ISAs differently and both are the
    # SAME declaration: the build chroot has no GPU, so the recipe states its
    # target set and the compiler is given exactly that.
    #   AMD  (HIP/ROCm)  gfx tokens          e.g. "gfx1100;gfx1102;gfx1201"
    #   NVIDIA (CUDA)    compute-capability  e.g. "75-virtual;86-real;120a-real"
    # Exported to build.sh as IGOS_GPU_TARGETS. None = not target-sensitive.
    gpu_targets: str | None = None

    # Names of packages this one supersedes at install time. Each name must
    # match another package's `name` field. The supersedes relationship
    # transfers file ownership atomically when this package's deploy succeeds
    # (per RFC §4 — gate-3 retirement). Used by pass1/pass2 cycle-break and
    # cross-tier rebuild patterns where the successor overwrites the
    # predecessor's installed paths with content built against
    # later-available dependencies.
    supersedes: list[str] = field(default_factory=list)

    # Shipped-name alias (the LFS ch8 dual-name convention). The bash ch8
    # driver builds recipe `<name>-core` but archives/registers it under the
    # USER-facing name (`run_package "gcc-core" "gcc" ...` in
    # scripts/chroot-build-ch8.sh), so the mirror index, .PKGINFO depend=
    # lines, and installed systems know ONLY the ship name. Declaring it here
    # makes the tree own its ship namespace: graph.resolve() accepts RUNTIME
    # deps on ship names (runtime deps are user-side contracts, emitted
    # verbatim to .PKGINFO — tracker.py H-004), and
    # scripts/derive-rebuild-set.py resolves chroot archives back to recipes
    # through it instead of guessing the -core suffix. Build/host deps stay
    # recipe-namespace. (F25 namespace wave, 2026-07-21.)
    ships_as: str | None = None

    # EULA install-helper name (hybrid-model, decided 2026-05-28).
    # When non-None, pkm runs /usr/lib/intergen/eula-helpers/<eula_helper>
    # BEFORE the package install proceeds. The helper checks a system-wide
    # marker, fetches the upstream EULA at install time, presents it in a
    # prompt_toolkit pager, and writes the marker + verbatim transcript
    # on ACCEPT. Helper exit code 0 -> install proceeds; non-zero -> abort.
    # First instance: nvidia ships eula_helper: nvidia-eula for the
    # proprietary userspace EULA. See packages/extra/nvidia/eula-helper/
    # for the canonical helper layout.
    eula_helper: str | None = None

    # Vendor EULA covering proprietary software a download-helper fetches
    # (vscode/chrome/...). pkm threads it into the repo index and routes
    # `pkm install <app>` through the unified continue-prompt + helper-download
    # flow when present (pkm/cli.py _proprietary_install). Threaded onto the
    # dataclass so tracker.py can emit it to .PKGINFO; also read directly from
    # package.yml by scripts/generate-third-party-notices.py.
    payload_license: str | None = None

    # Activation semantics (3.0-F28): when True, the package ships a payload
    # that CANNOT take effect on the running system until the next reboot —
    # an out-of-tree kernel module gated behind a blacklist (nvidia's .ko
    # behind the nouveau blacklist), the kernel image itself, or another
    # boot-path component. tracker.py emits it into .PKGINFO as
    # `reboot_required=true`; pkm reads it (repo._parse_pkginfo), persists it
    # on the installed row (database.reboot_required column), and prints a
    # LOUD aggregated post-transaction banner so the user is never left to
    # infer that a just-installed component is on disk but not yet active.
    # Prime Directive: the user must never have to guess activation state.
    reboot_required: bool = False

    # Test-suite policy for the builder-driven check phase — the SAME spec
    # scripts/pkg-functions.sh pkg_run_tests enforces on the bash tiers
    # (docs/test-allow-list.md, adopted 2026-05-08). Before this field the
    # yml lane had NO policy layer: every style emitted a bare `make check`
    # and any failure was fatal, so a pure-yml package could not express the
    # Rule-10 governed waiver for environmental failures (root
    # CAP_DAC_OVERRIDE, no-TPM, etc.) that custom-style siblings route
    # through pkg_run_tests. Parsed FAIL-CLOSED: enabled=false and
    # failure_policy=known_failures both REQUIRE tests_reason (an unreasoned
    # waiver refuses the template, mirroring pkg_run_tests).
    tests_enabled: bool = True
    tests_failure_policy: str = "strict"  # "strict" | "known_failures"
    tests_reason: str | None = None
    # tests.jobs: serialize (or bound) the check phase's make parallelism
    # when upstream's suite is not parallel-safe. None = inherit MAKEFLAGS.
    # Ground each use in the book: BLFS 13.0's own libvorbis instruction is
    # `make -j1 check` (the test/ Makefile races under -jN — duplicate
    # .Tpo→.Po mv, two make levels in one dir; hit on-run, GE-01 L11).
    # This is the honest alternative to waiving a race with known_failures
    # (which would mask the suite's real signal, not verify it).
    tests_jobs: int | None = None

    # Validation steps
    validation: list[ValidationCheck] = field(default_factory=list)

    # Where this template was loaded from
    template_path: Path | None = None


# ---------------------------------------------------------------------------
# Allowed values
# ---------------------------------------------------------------------------

VALID_BUILD_STYLES = {"autotools", "cmake", "meson", "make", "custom"}
VALID_TIERS = {"toolchain", "core", "base", "desktop", "ai", "compute", "extra"}

REQUIRED_FIELDS = {"name", "version", "release", "description", "license",
                   "source", "build_style"}

# Every top-level key parse_template recognizes (consumed by parse_template
# itself OR by an external consumer like verify_paths_derive.py /
# generate-third-party-notices.py / build pipeline scripts). Keys NOT in
# this set are REJECTED at parse time (TemplateError — see the unknown-key
# check below). Silent drops were the original failure mode (snappy's
# verify_paths field was a real instance caught only by audit); the interim
# warn-on-stderr scrolled past unread in long build logs, so unknown keys
# now fail closed.
#
# When adding a new field to the schema, add it here AND wire its consumer.
KNOWN_FIELDS = REQUIRED_FIELDS | {
    # consumed by parse_template directly
    "tier", "dependencies", "validation", "configure_flags", "patches",
    "bundled_deps", "supersedes", "ships_as", "iso_include", "homepage", "source_tree",
    "installer_hooks",
    "target_triple", "pass_number", "install_func", "direct_install",
    "skip_tracking", "eula_helper", "payload_license", "reboot_required",
    "elf_class",
    "elf_class_exempt", "tests",
    # consumed by external readers of package.yml (not parse_template):
    "content_hash",        # scripts/bump-changed-releases.py — recorded source
                           # fingerprint of the current release (auto-bump baseline)
    "verify_paths",        # igos-build/verify_paths_derive.py
    "sources_extra",       # scripts/build-source-archives.py — repo-relative
                           # paths to first-party files the build composes into
                           # the binary but that are not `source:` entries, so
                           # the corresponding-source archive carries them.
                           #
                           # Registered 2026-08-07, and the reason is worth
                           # keeping: the generator had documented and consumed
                           # this field since it was written, naming the kernel
                           # config fragments as its own example, while this set
                           # did not contain it. Every recipe that tried to
                           # declare it therefore died at parse time with
                           # "unknown top-level key(s): sources_extra", so no
                           # package could use it and none did. The published
                           # corresponding source for the kernel consequently
                           # omitted the configuration the kernel was built
                           # from — the one input that decides which drivers it
                           # has — and nobody could rebuild our kernel from our
                           # own source archive.
                           #
                           # Entries are LITERAL paths, files or directories.
                           # There is no glob expansion: the generator resolves
                           # each entry with Path.exists() and refuses the
                           # archive when one is missing, so a "*.config" entry
                           # fails closed rather than matching anything.
    "release_staged_source",  # scripts/validate-tarball-membership.py — a
                              # non-empty string declaring that this package's
                              # `generated: true` tarball is produced only when
                              # an input staged at release time is present, and
                              # saying which input and why. It changes exactly
                              # one verdict in that gate (an absent tarball is
                              # reported as its own named unverified state
                              # instead of halting as could-not-determine); a
                              # tarball that IS present is still checked in
                              # full. Registered here so a misspelled key fails
                              # loudly at parse time rather than reverting the
                              # package to the halting behaviour in silence.
    "build_artifacts",     # scripts/audit-yaml-source-pinning.sh, build-intergenos.sh
    "lib32_source",        # scripts/validate-package-tiers.py — RT-14 sibling
                           # mapping + RT-9 version lock + W2-a source-identity
                           # gate for lib32-* twins (every twin declares it;
                           # unregistered it warn-spammed every parse)
    "working_dir",         # build-helper-side; package.yml hand-written
    "silent_loss_accepted",  # scripts/preflight-silent-loss.py Rule F (the
                             # 6-rule gate's `silent_loss_accepted` declaration —
                             # documents intentional non-integrations of deps
                             # configure probes for. Canonical: linux-pam ←
                             # libnsl, per project_silent_loss_gate_6_rules
                             # memory + docs.)
    "requires_pci_vendor",   # installer/backend/packages.py get_group_packages —
                             # install-time GPU-vendor gate (skip nvidia on
                             # non-NVIDIA hardware; GBC001 libEGL blocker fix)
    "gpu_targets",           # igos-build/builder.py _build_env — declared GPU
                             # ISA set for target-sensitive compute packages
                             # (rocblas/rocwmma/llama-cpp-hip AMD gfx tokens;
                             # llama-cpp-cuda NVIDIA compute-capability tokens);
                             # exported to the build as IGOS_GPU_TARGETS. Chroot
                             # builds DECLARE (no GPU present); detection is a
                             # target-box concern (rocminfo / nvidia-smi).
}


# ---------------------------------------------------------------------------
# ISO inclusion — one rule, one implementation
# ---------------------------------------------------------------------------

#: Tiers whose packages do NOT ship on the ISO unless the recipe says otherwise.
#: `extra` and `compute` are mirror-only: their payload installs on-target from
#: the mirror. `toolchain` packages are build intermediates (the pass*/tmp
#: twins) torn down before the image exists — they neither ship on the ISO nor
#: publish to the mirror (2026-08-06: the ISO SBOM gate's first release-lane
#: firing counted all 25 as shipped packages with no archives).
NON_ISO_DEFAULT_TIERS = ("extra", "compute", "toolchain")


def effective_iso_include(iso_include_raw, tier: str) -> bool:
    """Resolve a recipe's EFFECTIVE iso_include: does this package ship on the ISO?

    THE single implementation of this rule. It is deliberately callable
    without a Package or a template path so that every consumer — the parser
    itself, scripts/pre-squashfs-audit.py, and anything added later — asks the
    same function instead of restating the rule. A hand-mirrored copy of it in
    the pre-squashfs audit drifted twice: `compute` landed here and not there
    (2026-07-18, caught as a mint Step-4.5 halt), then `toolchain` did the same
    (2026-08-06, found by cross-review 2026-08-13 while still latent).

    An explicit boolean wins. Anything else non-None is REFUSED, not coerced:
    bool() made the string "false" truthy, so a quoting accident flipped a
    ship/don't-ship flag. Callers translate the ValueError into whatever their
    own error channel is.
    """
    if iso_include_raw is None:
        return tier not in NON_ISO_DEFAULT_TIERS
    if isinstance(iso_include_raw, bool):
        return iso_include_raw
    raise ValueError(
        f"iso_include: must be a boolean "
        f"(got {type(iso_include_raw).__name__} {iso_include_raw!r})")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class TemplateError(Exception):
    """Raised when a package template is invalid."""

    def __init__(self, path: Path, message: str):
        self.path = path
        self.message = message
        # Emit a structured template_error event so the forensic trail
        # captures every template-validation failure without depending on
        # the caller's traceback handling.
        if _TRACE_AVAILABLE:
            try:
                _trace.trace_event("template_error",
                                   path=str(path), error=message)
            except Exception:
                pass
        super().__init__(f"{path}: {message}")


# ---------------------------------------------------------------------------
# Variable resolution
# ---------------------------------------------------------------------------

_VAR_RE = re.compile(r"\$\{(\w+)\}")

# Closed lexical grammars for package identity. name/version are interpolated
# into work/staging/log/manifest/archive paths — several of which are
# recursively DELETED before rebuild — so they must never be able to carry
# path separators, dot-dot components, or a leading dot. First character
# alphanumeric kills '.', '..' and hidden-file shapes; the body set is the
# full character inventory of the current 961-package corpus plus '+'
# (conventional in upstream names). Anything outside is a template error.
_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*\Z")
_VERSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*\Z")

# Closed lexical grammar for one `gpu_targets:` token. The value is
# interpolated by the recipe into a compiler/cmake argument
# (-DGPU_TARGETS= for HIP, -DCMAKE_CUDA_ARCHITECTURES= for CUDA), so the
# grammar is an allow-list of the two vendors' ISA spellings and nothing
# else — a token carrying shell metacharacters dies at parse time and
# never reaches a shell.
#   AMD    gfx1100, gfx1201, gfx90a:xnack+       (ROCm gfx identifiers)
#   NVIDIA 75, 86-real, 90a, 120a-real, 121a-real, 80-virtual
#          (CMAKE_CUDA_ARCHITECTURES spelling: two- or three-digit compute
#          capability, optional 'a'/'f' architecture-specific suffix,
#          optional -real (SASS only) / -virtual (PTX only) qualifier.
#          No suffix = both PTX and SASS. Upstream llama.cpp uses exactly
#          this vocabulary in ggml/src/ggml-cuda/CMakeLists.txt.)
_GPU_TARGET_TOKEN_RE = re.compile(
    r"gfx[0-9a-f]+(:[a-z0-9+-]+)?"
    r"|[0-9]{2,3}[af]?(-real|-virtual)?"
)


def _resolve_variables(text: str, variables: dict[str, str]) -> str:
    """Replace ${name} placeholders with values from the variables dict."""

    def _replace(match: re.Match) -> str:
        key = match.group(1)
        if key not in variables:
            raise KeyError(f"unknown variable '${{{key}}}'")
        return variables[key]

    return _VAR_RE.sub(_replace, text)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

_SHA256_RE = re.compile(r"[0-9a-fA-F]{64}\Z")


# Every key a source entry may carry. Keys NOT in this set are REJECTED at
# parse time, the same way unknown TOP-LEVEL keys are (see KNOWN_FIELDS above
# and the snappy verify_paths incident it records). Source entries were the
# remaining silent-drop surface: a key nothing reads used to parse cleanly and
# do nothing, so a recipe could state an availability or integrity property the
# build never implemented. Measured 2026-08-25: one such key existed across
# 1305 source entries.
#
# This set is asserted equal to the Source dataclass's own fields by
# tests/igos_build/test_parser_unknown_source_keys.py, so adding a field to one
# and not the other fails loudly instead of reopening the drop.
KNOWN_SOURCE_FIELDS = frozenset(
    {"url", "sha256", "filename", "generated", "extract", "redistributable"})


def _parse_sources(raw: list, variables: dict, path: Path) -> list[Source]:
    """Parse and validate the source list."""
    sources = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise TemplateError(path, f"source[{i}]: must be a mapping with 'url' and 'sha256'")
        # Reject unknown keys BEFORE any other check, so a typo is reported as
        # the typo it is rather than as the consequence it causes: `sha256sum:`
        # would otherwise surface as "missing sha256" on a recipe that plainly
        # carries a hash, and `fallback_url:` surfaced as nothing at all.
        unknown = sorted(set(entry) - KNOWN_SOURCE_FIELDS)
        if unknown:
            raise TemplateError(
                path,
                f"source[{i}]: unknown key(s) {', '.join(unknown)} — nothing "
                f"reads them, so they would be silently dropped. Supported "
                f"keys: {', '.join(sorted(KNOWN_SOURCE_FIELDS))}")
        url = entry.get("url")
        sha256 = entry.get("sha256")
        generated = entry.get("generated", False)
        if not isinstance(generated, bool):
            # bool() coercion made any non-empty value — including the
            # string "false" — truthy, silently waiving the sha-pin
            # requirement. Security booleans parse strictly (the
            # installer_hooks pattern).
            raise TemplateError(
                path,
                f"source[{i}]: generated: must be a boolean "
                f"(got {type(generated).__name__} {generated!r})")
        if not url:
            raise TemplateError(path, f"source[{i}]: missing 'url'")
        # Downloaded sources REQUIRE a sha256 (supply-chain integrity). A
        # first-party tarball built in-tree carries `generated: true` instead —
        # it is regenerated from git-controlled source every build and is not
        # byte-pinnable across builders (see the Source docstring).
        if not sha256 and not generated:
            raise TemplateError(
                path,
                f"source[{i}]: missing 'sha256' "
                "(or mark 'generated: true' for an in-tree-built tarball)")
        # A present pin must BE a pin: exactly 64 hex chars. This closes the
        # old placeholder-prefix bypass (a 'placeholder…' string parsed fine
        # and the builder skipped verification on it) and catches truncated
        # or mistyped hashes at parse time instead of at first use.
        if sha256 is not None:
            sha256 = str(sha256)
            if not _SHA256_RE.fullmatch(sha256):
                raise TemplateError(
                    path,
                    f"source[{i}]: sha256 must be exactly 64 hex characters "
                    f"(got {sha256[:32]!r}…) — placeholder values are retired; "
                    f"a source is either pinned or 'generated: true'")
        url = _resolve_variables(url, variables)
        filename = entry.get("filename")
        if filename:
            filename = _resolve_variables(filename, variables)
            # The cache filename is joined onto the sources dir — it must be
            # a single, plain path component. A separator or dot-shape here
            # is a sources-dir escape, not a filename.
            if ("/" in filename or "\\" in filename
                    or filename in (".", "..") or filename.startswith(".")):
                raise TemplateError(
                    path,
                    f"source[{i}]: filename '{filename}' must be a single "
                    f"plain path component (no separators, no leading dot)")
        extract = entry.get("extract", True)
        if not isinstance(extract, bool):
            raise TemplateError(
                path,
                f"source[{i}]: extract must be a plain boolean "
                f"(got {extract!r}) — strict-bool like 'generated'")
        redistributable = entry.get("redistributable", True)
        if not isinstance(redistributable, bool):
            raise TemplateError(
                path,
                f"source[{i}]: redistributable must be a plain boolean "
                f"(got {redistributable!r}) — strict-bool like 'generated'")
        # A non-redistributable source must be pinned. The flag's whole job is
        # to keep specific bytes out of what we publish while still letting the
        # build depend on them, and "which bytes" is only answerable for a
        # hashed input. A generated first-party tarball is ours by definition,
        # so the combination is a declaration error rather than a policy.
        if not redistributable and generated:
            raise TemplateError(
                path,
                f"source[{i}]: redistributable: false is meaningless on a "
                f"'generated: true' first-party tarball — the tarball is ours")
        if not redistributable and not sha256:
            raise TemplateError(
                path,
                f"source[{i}]: redistributable: false requires a sha256 pin — "
                f"an unpinned input cannot be excluded by identity")
        sources.append(Source(url=url, sha256=sha256, filename=filename,
                              generated=generated, extract=extract,
                              redistributable=redistributable))
    return sources


def _parse_dependencies(raw: dict | None, path: Path) -> Dependencies:
    """Parse and validate the dependencies block."""
    if raw is None:
        return Dependencies()
    if not isinstance(raw, dict):
        raise TemplateError(path, "dependencies: must be a mapping")
    return Dependencies(
        build=raw.get("build", []) or [],
        host=raw.get("host", []) or [],
        runtime=raw.get("runtime", []) or [],
    )


def _parse_patches(raw: list, path: Path) -> list[PatchEntry]:
    """Parse the patches list, supporting both string and dict formats.

    Accepts:
      patches:
        - simple.patch                          # string → PatchEntry(file=..., sha256=None)
        - file: verified.patch                  # dict   → PatchEntry(file=..., sha256=...)
          sha256: abc123...
    """
    entries = []
    for i, item in enumerate(raw):
        if isinstance(item, str):
            entries.append(PatchEntry(file=item))
        elif isinstance(item, dict):
            filename = item.get("file")
            if not filename:
                raise TemplateError(path, f"patches[{i}]: dict entry missing 'file' key")
            entries.append(PatchEntry(file=filename, sha256=item.get("sha256")))
        else:
            raise TemplateError(path, f"patches[{i}]: must be a string or mapping")
    return entries


def _parse_validation(raw: list | None, path: Path) -> list[ValidationCheck]:
    """Parse and validate the validation block."""
    if raw is None:
        return []
    checks = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise TemplateError(path, f"validation[{i}]: must be a mapping")
        vtype = entry.get("type")
        if not vtype:
            raise TemplateError(path, f"validation[{i}]: missing 'type'")
        checks.append(ValidationCheck(
            type=vtype,
            description=entry.get("description", ""),
            script=entry.get("script"),
            expect_contains=entry.get("expect_contains"),
            fatal=entry.get("fatal", True),
        ))
    return checks


def _parse_tests(
    raw: dict | None, path: Path
) -> tuple[bool, str, str | None, int | None]:
    """Parse the optional `tests:` block (docs/test-allow-list.md).

    Returns (enabled, failure_policy, reason, jobs). Fail-closed, mirroring
    pkg_run_tests in scripts/pkg-functions.sh: a waiver (enabled=false OR
    failure_policy=known_failures) without a reason refuses the template.
    jobs (optional, int >= 1) bounds the check phase's make parallelism for
    suites that are not parallel-safe; it too requires a reason (the
    serialization must be grounded, e.g. in the BLFS book's own command).
    """
    if raw is None:
        return True, "strict", None, None
    if not isinstance(raw, dict):
        raise TemplateError(path, "tests: must be a mapping")
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise TemplateError(path, "tests.enabled: must be a boolean")
    policy = raw.get("failure_policy", "strict")
    if policy not in ("strict", "known_failures"):
        raise TemplateError(
            path,
            f"tests.failure_policy: invalid value '{policy}' "
            "(expected strict|known_failures)",
        )
    reason = raw.get("reason")
    if reason is not None and not isinstance(reason, str):
        raise TemplateError(path, "tests.reason: must be a string")
    if not enabled and not reason:
        raise TemplateError(path, "tests.enabled=false requires a reason")
    if policy == "known_failures" and not reason:
        raise TemplateError(
            path, "tests.failure_policy=known_failures requires a reason")
    jobs = raw.get("jobs")
    if jobs is not None:
        if not isinstance(jobs, int) or isinstance(jobs, bool) or jobs < 1:
            raise TemplateError(
                path, "tests.jobs: must be an integer >= 1")
        if not reason:
            raise TemplateError(
                path, "tests.jobs requires a reason (ground the "
                      "serialization — e.g. the BLFS book's own command)")
    return enabled, policy, reason, jobs


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------

def parse_template(template_path: Path) -> Package:
    """Parse a package.yml file and return a validated Package.

    Args:
        template_path: Path to the package.yml file.

    Returns:
        A fully validated Package object.

    Raises:
        TemplateError: If the template is missing required fields,
                       has invalid values, or fails validation.
        FileNotFoundError: If the template file doesn't exist.
    """
    template_path = Path(template_path)
    if not template_path.exists():
        raise FileNotFoundError(f"template not found: {template_path}")

    with open(template_path) as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise TemplateError(template_path, "template must be a YAML mapping")

    # --- Check required fields ---
    missing = REQUIRED_FIELDS - set(raw.keys())
    if missing:
        raise TemplateError(template_path, f"missing required fields: {', '.join(sorted(missing))}")

    # --- Reject unknown keys ---
    # An unknown top-level key is a typo'd CONTROL FIELD running default
    # semantics: 'direct_instal:' silently tracks, 'verify_path:' silently
    # skips the squashfs audit (snappy's verify_paths was the original
    # catching incident). The old warn-on-stderr scrolled past unread in a
    # 15-hour build log — fail at parse time instead, where the fix is a
    # one-line edit and nothing has built yet.
    unknown = set(raw.keys()) - KNOWN_FIELDS
    if unknown:
        raise TemplateError(
            template_path,
            f"unknown top-level key(s): {', '.join(sorted(unknown))} — "
            f"remove them, or add to KNOWN_FIELDS (with a consumer) if the "
            f"field is real")

    # --- Basic fields ---
    name = str(raw["name"])
    version = str(raw["version"])
    if not _NAME_RE.fullmatch(name):
        raise TemplateError(
            template_path,
            f"name: '{name}' violates the package-name grammar "
            f"(first char alphanumeric, then [A-Za-z0-9._+-] only — no path "
            f"separators, no leading dot)")
    if not _VERSION_RE.fullmatch(version):
        raise TemplateError(
            template_path,
            f"version: '{version}' violates the version grammar "
            f"(first char alphanumeric, then [A-Za-z0-9._+-] only)")
    release = int(raw["release"])
    description = str(raw["description"])
    pkg_license = str(raw["license"])
    build_style = str(raw["build_style"])
    tier = str(raw.get("tier", "core"))

    # --- Validate enums ---
    if build_style not in VALID_BUILD_STYLES:
        raise TemplateError(
            template_path,
            f"invalid build_style '{build_style}' — must be one of: {', '.join(sorted(VALID_BUILD_STYLES))}"
        )
    if tier not in VALID_TIERS:
        raise TemplateError(
            template_path,
            f"invalid tier '{tier}' — must be one of: {', '.join(sorted(VALID_TIERS))}"
        )

    # --- Variable resolution context ---
    # Computed variables for URL path templating (avoids hardcoding when
    # upstream mirrors organize releases by major.minor series, e.g.
    # rpm.org's /releases/rpm-4.18.x/ directory layout).
    version_parts = version.split(".")
    variables = {
        "name": name,
        "version": version,
        "version_major": version_parts[0] if version_parts else "",
        "version_major_minor": ".".join(version_parts[:2]) if len(version_parts) >= 2 else version,
        # Per §3 P6: support ${version_patch} for packages that need the
        # third version segment (e.g., GCC bundled-deps version). Falls
        # back to "0" if the version doesn't have a patch segment so the
        # template doesn't break on shorter version strings.
        "version_patch": version_parts[2] if len(version_parts) >= 3 else "0",
    }

    # --- Parse complex fields ---
    source_raw = raw.get("source", [])
    if not isinstance(source_raw, list):
        raise TemplateError(template_path, "source: must be a list")
    sources = _parse_sources(source_raw, variables, template_path)

    dependencies = _parse_dependencies(raw.get("dependencies"), template_path)
    validation = _parse_validation(raw.get("validation"), template_path)
    (tests_enabled, tests_failure_policy,
     tests_reason, tests_jobs) = _parse_tests(
        raw.get("tests"), template_path)

    # --- Simple optional fields ---
    configure_flags = raw.get("configure_flags", []) or []
    patches = _parse_patches(raw.get("patches", []) or [], template_path)

    # bundled_deps — 'name -> dest' strings; the dest is joined under the
    # extracted source tree by the builder, so it must be a clean relative
    # path (the builder's is_relative_to containment is the runtime belt;
    # this rejects the malformed template at parse time).
    bundled_deps = raw.get("bundled_deps", []) or []
    if not isinstance(bundled_deps, list):
        raise TemplateError(template_path, "bundled_deps: must be a list")
    for i, entry in enumerate(bundled_deps):
        if not isinstance(entry, str):
            raise TemplateError(
                template_path,
                f"bundled_deps[{i}]: must be a 'name -> dest' string")
        if " -> " in entry:
            dep_name, dest_rel = entry.split(" -> ", 1)
            if not dep_name.strip():
                raise TemplateError(
                    template_path, f"bundled_deps[{i}]: empty dep name")
            if (not dest_rel.strip() or dest_rel.startswith("/")
                    or ".." in Path(dest_rel).parts):
                raise TemplateError(
                    template_path,
                    f"bundled_deps[{i}]: dest '{dest_rel}' must be a "
                    f"relative path with no '..' components")

    # source_tree — repo-relative in-tree source dirs for first-party
    # `source: []` packages (folded into the skip-built fingerprint so a
    # source-only edit forces a rebuild). See Package.source_tree.
    source_tree_raw = raw.get("source_tree", []) or []
    if not isinstance(source_tree_raw, list) or not all(isinstance(x, str) for x in source_tree_raw):
        raise TemplateError(template_path, "source_tree: must be a list of repo-relative path strings")
    for entry in source_tree_raw:
        if entry.startswith("/") or ".." in Path(entry).parts:
            raise TemplateError(template_path, f"source_tree: '{entry}' must be a repo-relative path with no '..'")
    source_tree = source_tree_raw

    # installer_hooks — forge-only boolean (item-8 completion). See
    # Package.installer_hooks + content_hash._installer_hooks_fingerprint.
    installer_hooks_raw = raw.get("installer_hooks", False)
    if not isinstance(installer_hooks_raw, bool):
        raise TemplateError(template_path, "installer_hooks: must be a boolean")
    installer_hooks = installer_hooks_raw

    # supersedes — list of package names this one replaces at install time
    supersedes_raw = raw.get("supersedes", []) or []
    if not isinstance(supersedes_raw, list):
        raise TemplateError(template_path, "supersedes: must be a list of package names")
    supersedes = []
    for i, entry in enumerate(supersedes_raw):
        if not isinstance(entry, str):
            raise TemplateError(template_path, f"supersedes[{i}]: must be a string (package name)")
        if entry == name:
            raise TemplateError(template_path, f"supersedes[{i}]: '{entry}' — a package cannot supersede itself")
        supersedes.append(entry)

    # ships_as — the shipped/user-facing package name, declared only when it
    # differs from the recipe name (the ch8 dual-name twins). Same grammar
    # as name:; equal-to-name declarations are refused (a no-op alias is a
    # stale-declaration hazard, same policy as an exempting-nothing glob).
    ships_as_raw = raw.get("ships_as")
    if ships_as_raw is not None:
        if not isinstance(ships_as_raw, str) or not _NAME_RE.fullmatch(ships_as_raw):
            raise TemplateError(
                template_path,
                f"ships_as: '{ships_as_raw}' violates the package-name grammar",
            )
        if ships_as_raw == name:
            raise TemplateError(
                template_path,
                f"ships_as: '{ships_as_raw}' equals name: — declare only when "
                "the shipped name differs from the recipe name",
            )

    # ISO inclusion — the rule itself lives in effective_iso_include() above,
    # which is the ONE implementation every consumer calls (see its docstring
    # for why). Here it only needs its ValueError turned into the parser's own
    # TemplateError so the message carries the offending template path.
    try:
        iso_include = effective_iso_include(raw.get("iso_include", None), tier)
    except ValueError as e:
        raise TemplateError(template_path, str(e))

    # direct_install / skip_tracking gate the whole tracking pipeline —
    # strict booleans for the same reason as iso_include above.
    direct_install_raw = raw.get("direct_install", False)
    if not isinstance(direct_install_raw, bool):
        raise TemplateError(
            template_path,
            f"direct_install: must be a boolean "
            f"(got {type(direct_install_raw).__name__} {direct_install_raw!r})")
    skip_tracking_raw = raw.get("skip_tracking", False)
    if not isinstance(skip_tracking_raw, bool):
        raise TemplateError(
            template_path,
            f"skip_tracking: must be a boolean "
            f"(got {type(skip_tracking_raw).__name__} {skip_tracking_raw!r})")

    # ELF word-size contract: default 64; YAML may carry it as an int
    # (64) or string ("64"/"32"/"mixed"). Anything else is a template
    # error — the audit's vocabulary is closed on purpose.
    elf_class = str(raw.get("elf_class", "64"))
    if elf_class not in ("64", "32", "mixed"):
        raise TemplateError(
            template_path,
            f"elf_class: must be one of 64, 32, mixed (got '{elf_class}')",
        )

    # Path-scoped width-audit exemptions (L9): a list of non-empty,
    # root-relative glob strings. Meaningless under mixed (the whole
    # package is already waived) — declaring both is a template error.
    elf_class_exempt_raw = raw.get("elf_class_exempt", [])
    if not isinstance(elf_class_exempt_raw, list) or not all(
        isinstance(g, str) and g.strip() for g in elf_class_exempt_raw
    ):
        raise TemplateError(
            template_path,
            "elf_class_exempt: must be a list of non-empty glob strings",
        )
    elf_class_exempt = [g.strip().lstrip("/") for g in elf_class_exempt_raw]
    if elf_class_exempt and elf_class == "mixed":
        raise TemplateError(
            template_path,
            "elf_class_exempt: meaningless with elf_class: mixed (the whole "
            "package is already waived) — declare one or the other",
        )

    gpu_targets_raw = raw.get("gpu_targets")
    if gpu_targets_raw is not None:
        if not isinstance(gpu_targets_raw, str) or not gpu_targets_raw.strip():
            raise TemplateError(
                template_path,
                "gpu_targets: must be a non-empty string of semicolon-separated "
                "GPU ISA tokens — AMD gfx (e.g. \"gfx1100;gfx1201\") or NVIDIA "
                "compute capability (e.g. \"75-virtual;86-real\")",
            )
        for _tok in gpu_targets_raw.strip().split(";"):
            if not _GPU_TARGET_TOKEN_RE.fullmatch(_tok.strip()):
                raise TemplateError(
                    template_path,
                    f"gpu_targets: malformed target token '{_tok.strip()}' — "
                    "expected gfxNNNN (optional :feature suffix) for AMD, or "
                    "NN[a|f][-real|-virtual] compute capability for NVIDIA",
                )

    return Package(
        name=name,
        version=version,
        release=release,
        description=description,
        license=pkg_license,
        source=sources,
        dependencies=dependencies,
        build_style=build_style,
        tier=tier,
        iso_include=iso_include,
        elf_class=elf_class,
        elf_class_exempt=elf_class_exempt,
        source_tree=source_tree,
        installer_hooks=installer_hooks,
        homepage=raw.get("homepage"),
        configure_flags=configure_flags,
        patches=patches,
        target_triple=raw.get("target_triple"),
        pass_number=raw.get("pass_number"),
        bundled_deps=bundled_deps,
        install_func=raw.get("install_func", "do_install"),
        direct_install=direct_install_raw,
        skip_tracking=skip_tracking_raw,
        gpu_targets=(gpu_targets_raw.strip() if gpu_targets_raw else None),
        supersedes=supersedes,
        ships_as=ships_as_raw,
        eula_helper=raw.get("eula_helper"),
        payload_license=raw.get("payload_license"),
        reboot_required=bool(raw.get("reboot_required", False)),
        tests_enabled=tests_enabled,
        tests_failure_policy=tests_failure_policy,
        tests_reason=tests_reason,
        tests_jobs=tests_jobs,
        validation=validation,
        template_path=template_path,
    )


def discover_templates(packages_dir: Path) -> list[Path]:
    """Find all package.yml files under the packages directory.

    Args:
        packages_dir: Root of the packages tree (e.g., /mnt/intergenos/packages)

    Returns:
        Sorted list of paths to package.yml files.
    """
    packages_dir = Path(packages_dir)
    return sorted(packages_dir.rglob("package.yml"))


def _validate_supersedes_no_cycles(packages: list[Package]) -> None:
    """Ensure no cycles exist in the supersedes relation graph.

    Three-color DFS over the directed graph where an edge A → B means A
    declares `supersedes: [B]`. Cycles include direct (A→B, B→A), indirect
    (A→B→C→A), or any longer chain that closes back on itself. Self-edges
    (A→A) are rejected at parse_template time.
    """
    by_name = {pkg.name: pkg for pkg in packages}
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {pkg.name: WHITE for pkg in packages}

    def visit(name: str, stack: list[str]) -> None:
        if color.get(name) == GRAY:
            cycle = stack[stack.index(name):] + [name]
            raise TemplateError(
                by_name[name].template_path,
                f"supersedes cycle detected: {' → '.join(cycle)}"
            )
        if color.get(name) == BLACK:
            return
        color[name] = GRAY
        stack.append(name)
        pkg = by_name.get(name)
        if pkg:
            for target in pkg.supersedes:
                if target in by_name:
                    visit(target, stack)
        stack.pop()
        color[name] = BLACK

    for pkg in packages:
        if color[pkg.name] == WHITE:
            visit(pkg.name, [])


def _warn_missing_supersedees(packages: list[Package]) -> list[str]:
    """Return warnings for any supersedes targets that don't match a known package.

    Per RFC §11: missing supersedee is allowed (the supersede becomes a no-op
    at install time) but worth surfacing so a typo doesn't silently degrade.
    """
    by_name = {pkg.name: pkg for pkg in packages}
    warnings = []
    for pkg in packages:
        for target in pkg.supersedes:
            if target not in by_name:
                warnings.append(
                    f"{pkg.template_path}: supersedes '{target}' — no package "
                    f"with this name exists; supersede will be a no-op at install"
                )
    return warnings


def load_all_packages(packages_dir: Path) -> list[Package]:
    """Discover and parse all package templates.

    Args:
        packages_dir: Root of the packages tree.

    Returns:
        List of validated Package objects.

    Raises:
        TemplateError: If any template fails validation, including supersedes
                       cycle detection across the entire package set.
    """
    templates = discover_templates(packages_dir)
    packages = []
    for path in templates:
        pkg = parse_template(path)
        if _TRACE_AVAILABLE:
            try:
                _trace.trace_event(
                    "template_parse",
                    path=str(path),
                    pkg=pkg.name,
                    version=pkg.version,
                    tier=pkg.tier,
                )
            except Exception:
                pass
        packages.append(pkg)
    _validate_supersedes_no_cycles(packages)
    warnings = _warn_missing_supersedees(packages)
    if warnings:
        import sys
        for w in warnings:
            print(f"WARNING: {w}", file=sys.stderr)
            if _TRACE_AVAILABLE:
                try:
                    _trace.trace_event("template_warning", message=w)
                except Exception:
                    pass
    return packages
