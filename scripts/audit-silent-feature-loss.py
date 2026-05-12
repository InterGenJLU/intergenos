#!/usr/bin/env python3
"""Audit tier:core and tier:base packages for the silent-feature-loss shape.

The shape (from Build #7 mitkrb halt 2026-05-10):
  - package.yml declares a build dep on X
  - build.sh's ./configure invocation does NOT pass --with-X / --enable-X
  - configure auto-disables X because it didn't find the flag
  - X is installed in the chroot but the package was built without X support

This is a real-feature-loss antipattern that the maintainer wants
flagged. The audit produces a TSV report; per the rulebook
"per-finding review only," each flagged package is investigated and
fixed individually — NEVER bulk-applied.

Heuristic:
  For each `tier:core` or `tier:base` package, parse build.sh for the
  configure line and look for --with-{dep} / --enable-{dep} or
  --without-{dep} / --disable-{dep}. Flag packages where the declared
  build-dep has NO mention as a configure flag — suspect for silent
  feature loss.

Limitations:
  - Some packages auto-detect deps without explicit flags (meson, cmake)
    — those produce false positives.
  - Some deps are build-time-only (cmake, ninja, pkg-config) and don't
    map to a configure flag — also false positives.
  - The script flags candidates for human review; it doesn't claim each
    flagged row is a bug.

Usage:
  python3 scripts/audit-silent-feature-loss.py

Exit codes: always 0 (informational report).
"""
import re, sys
from pathlib import Path
import yaml

REPO = Path("/mnt/intergenos")

# Build-time-only deps that don't map to a configure feature flag.
# These are excluded from the audit because they're never silent feature
# losses — they're just build tools.
BUILD_TOOLS_NO_FLAG = {
    "cmake", "meson", "ninja", "pkg-config", "pkgconf",
    "autoconf", "automake", "libtool", "m4", "bison", "flex",
    "gettext", "texinfo", "help2man", "makedepend", "perl", "python",
    "python3", "gperf", "intltool", "itstool", "asciidoc", "asciidoctor",
    "docbook-xml", "docbook-xsl", "docbook-xsl-nons",
    "xmlto", "doxygen", "sphinx", "docutils",
    "util-macros", "xorgproto",
    "nasm", "yasm", "cython", "rpcsvc-proto", "unifdef",
    # wayland-protocols removed 2026-05-12: see validate-package-tiers.py
    # for context — wayland-protocols is desktop-tier GUI substrate, not
    # a build-tool, and was wrongly classified in this set.
    "rust-bindgen", "cbindgen",
    "hatchling", "setuptools", "setuptools-scm", "wheel", "pip",
    "build", "pypa-build", "pyproject_hooks", "pyproject-hooks",
    "pyproject-metadata", "meson_python", "flit", "flit-core",
    "pdm-backend", "poetry-core", "maturin", "uv_build",
    "editables", "pathspec", "pluggy", "trove-classifiers",
    "packaging", "tomli", "tomllib",
    "hatch-vcs", "hatch-fancy-pypi-readme",
    # Common build-helper packages
    "git", "make",
}


def configure_flags_from_build_sh(build_sh: Path) -> str:
    """Return all the text from configure-line / meson_setup-line, joined."""
    if not build_sh.exists():
        return ""
    text = build_sh.read_text()
    # Capture configure() function body
    m = re.search(r'configure\s*\(\s*\)\s*\{([\s\S]*?)^\}', text, re.MULTILINE)
    if m:
        return m.group(1)
    return ""


def has_flag_for(text: str, dep: str) -> str:
    """Return the kind of flag found for `dep` in configure text, or '' if none.

    Looks for variations: --with-dep, --enable-dep, --without-dep,
    --disable-dep, -Ddep=true, -Ddep=false, etc. Returns:
      'enable' / 'with'     — the feature is explicitly enabled
      'disable' / 'without' — the feature is explicitly disabled (FLAG)
      'meson-on' / 'meson-off' — meson option enabled/disabled
      ''                    — no explicit flag found (CANDIDATE for silent loss)
    """
    # Normalize a few common dep-name variants
    norm = dep.replace("_", "-").lower()
    aliases = {norm, dep.lower(), norm.replace("lib", "", 1), norm.replace("-pass1", "")}
    for a in aliases:
        if not a:
            continue
        if re.search(rf'--enable-{re.escape(a)}\b', text):
            return "enable"
        if re.search(rf'--with-{re.escape(a)}\b', text):
            return "with"
        if re.search(rf'--disable-{re.escape(a)}\b', text):
            return "disable"
        if re.search(rf'--without-{re.escape(a)}\b', text):
            return "without"
        if re.search(rf'-D{re.escape(a)}=(true|yes|enabled)', text, re.I):
            return "meson-on"
        if re.search(rf'-D{re.escape(a)}=(false|no|disabled)', text, re.I):
            return "meson-off"
    return ""


def main():
    rows = []
    for yml in REPO.glob("packages/core/*/package.yml"):
        try:
            d = yaml.safe_load(yml.read_text())
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        if d.get("tier") not in ("core", "base"):
            continue
        deps = (d.get("dependencies") or {}).get("build") or []
        if not deps:
            continue
        build_sh = yml.parent / "build.sh"
        cfg = configure_flags_from_build_sh(build_sh)
        if not cfg:
            continue
        for dep in deps:
            if dep in BUILD_TOOLS_NO_FLAG:
                continue
            flag = has_flag_for(cfg, dep)
            if flag in ("disable", "without", "meson-off"):
                rows.append((d["name"], dep, f"EXPLICITLY_{flag.upper()}"))
            elif flag == "":
                rows.append((d["name"], dep, "NO_FLAG"))

    rows.sort()
    print("package\tdeclared_dep\tflag_status")
    print("-" * 70)
    for r in rows:
        print("\t".join(r))
    print()
    no_flag = sum(1 for r in rows if r[2] == "NO_FLAG")
    explicit_off = sum(1 for r in rows if r[2].startswith("EXPLICITLY"))
    print(f"# {len(rows)} candidates flagged for human review:")
    print(f"#   {no_flag} NO_FLAG (configure may auto-disable — investigate)")
    print(f"#   {explicit_off} EXPLICITLY OFF (configure DOES disable — verify intent)")
    print()
    print("# CAVEATS — many flagged rows are false positives:")
    print("#   - meson/cmake packages auto-detect, no explicit flag needed")
    print("#   - some deps are runtime-only or transitive, declared for completeness")
    print("#   - the audit flags candidates; per-package judgment determines real issues")


if __name__ == "__main__":
    main()
