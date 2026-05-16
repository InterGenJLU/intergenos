"""Package template parser for igos-build.

Reads package.yml templates, validates required fields and types,
resolves variable substitutions, and returns validated Package objects.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Source:
    """A source tarball or file to download."""
    url: str
    sha256: str
    filename: str | None = None


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
    tier: str = "core"               # toolchain, core, base, desktop, extra, ai

    # ISO inclusion. tier:extra default = False (MIRROR — pkm install on
    # demand from the InterGenOS mirror). All other tiers default = True
    # (shipped in the squashfs). Explicit override available per-package.
    # See docs/extra-tier-classification.md for the v1.0 ISO whitelist.
    iso_include: bool | None = None  # None => apply tier-based default at parse time

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

    # Names of packages this one supersedes at install time. Each name must
    # match another package's `name` field. The supersedes relationship
    # transfers file ownership atomically when this package's deploy succeeds
    # (per RFC §4 — gate-3 retirement). Used by pass1/pass2 cycle-break and
    # cross-tier rebuild patterns where the successor overwrites the
    # predecessor's installed paths with content built against
    # later-available dependencies.
    supersedes: list[str] = field(default_factory=list)

    # Validation steps
    validation: list[ValidationCheck] = field(default_factory=list)

    # Where this template was loaded from
    template_path: Path | None = None


# ---------------------------------------------------------------------------
# Allowed values
# ---------------------------------------------------------------------------

VALID_BUILD_STYLES = {"autotools", "cmake", "meson", "make", "custom"}
VALID_TIERS = {"toolchain", "core", "base", "desktop", "ai", "extra"}

REQUIRED_FIELDS = {"name", "version", "release", "description", "license",
                   "source", "build_style"}


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class TemplateError(Exception):
    """Raised when a package template is invalid."""

    def __init__(self, path: Path, message: str):
        self.path = path
        self.message = message
        super().__init__(f"{path}: {message}")


# ---------------------------------------------------------------------------
# Variable resolution
# ---------------------------------------------------------------------------

_VAR_RE = re.compile(r"\$\{(\w+)\}")


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

def _parse_sources(raw: list, variables: dict, path: Path) -> list[Source]:
    """Parse and validate the source list."""
    sources = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise TemplateError(path, f"source[{i}]: must be a mapping with 'url' and 'sha256'")
        url = entry.get("url")
        sha256 = entry.get("sha256")
        if not url:
            raise TemplateError(path, f"source[{i}]: missing 'url'")
        if not sha256:
            raise TemplateError(path, f"source[{i}]: missing 'sha256'")
        url = _resolve_variables(url, variables)
        filename = entry.get("filename")
        if filename:
            filename = _resolve_variables(filename, variables)
        sources.append(Source(url=url, sha256=sha256, filename=filename))
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

    # --- Basic fields ---
    name = str(raw["name"])
    version = str(raw["version"])
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

    # --- Simple optional fields ---
    configure_flags = raw.get("configure_flags", []) or []
    patches = _parse_patches(raw.get("patches", []) or [], template_path)
    bundled_deps = raw.get("bundled_deps", []) or []

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

    # ISO inclusion: explicit override wins, otherwise apply tier-based
    # default. `tier: extra` defaults to MIRROR (iso_include=False); all
    # other tiers ship in the ISO (iso_include=True).
    iso_include_raw = raw.get("iso_include", None)
    if iso_include_raw is None:
        iso_include = (tier != "extra")
    else:
        iso_include = bool(iso_include_raw)

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
        homepage=raw.get("homepage"),
        configure_flags=configure_flags,
        patches=patches,
        target_triple=raw.get("target_triple"),
        pass_number=raw.get("pass_number"),
        bundled_deps=bundled_deps,
        install_func=raw.get("install_func", "do_install"),
        direct_install=bool(raw.get("direct_install", False)),
        skip_tracking=bool(raw.get("skip_tracking", False)),
        supersedes=supersedes,
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
        packages.append(parse_template(path))
    _validate_supersedes_no_cycles(packages)
    warnings = _warn_missing_supersedees(packages)
    if warnings:
        import sys
        for w in warnings:
            print(f"WARNING: {w}", file=sys.stderr)
    return packages
