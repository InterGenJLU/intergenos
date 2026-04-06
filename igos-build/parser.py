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
    tier: str = "core"               # toolchain, core, base, desktop

    # Optional metadata
    homepage: str | None = None

    # Build configuration
    configure_flags: list[str] = field(default_factory=list)
    patches: list[str] = field(default_factory=list)

    # Toolchain-specific
    target_triple: str | None = None
    pass_number: int | None = None
    bundled_deps: list[str] = field(default_factory=list)

    # Install function name for custom build style
    install_func: str = "do_install"  # "do_install" (default) or "install" (toolchain only)

    # Install directly to / instead of DESTDIR staging (for multi-pass builds)
    direct_install: bool = False

    # Validation steps
    validation: list[ValidationCheck] = field(default_factory=list)

    # Where this template was loaded from
    template_path: Path | None = None


# ---------------------------------------------------------------------------
# Allowed values
# ---------------------------------------------------------------------------

VALID_BUILD_STYLES = {"autotools", "cmake", "meson", "make", "custom"}
VALID_TIERS = {"toolchain", "core", "base", "desktop", "extra"}

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
    variables = {
        "name": name,
        "version": version,
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
    patches = raw.get("patches", []) or []
    bundled_deps = raw.get("bundled_deps", []) or []

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
        homepage=raw.get("homepage"),
        configure_flags=configure_flags,
        patches=patches,
        target_triple=raw.get("target_triple"),
        pass_number=raw.get("pass_number"),
        bundled_deps=bundled_deps,
        install_func=raw.get("install_func", "do_install"),
        direct_install=bool(raw.get("direct_install", False)),
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


def load_all_packages(packages_dir: Path) -> list[Package]:
    """Discover and parse all package templates.

    Args:
        packages_dir: Root of the packages tree.

    Returns:
        List of validated Package objects.

    Raises:
        TemplateError: If any template fails validation.
    """
    templates = discover_templates(packages_dir)
    packages = []
    for path in templates:
        packages.append(parse_template(path))
    return packages
