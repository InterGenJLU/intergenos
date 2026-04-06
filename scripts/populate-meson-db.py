#!/usr/bin/env python3
"""Populate the meson feature database tables in blfs-packages.db.

Scans InterGenOS package templates for meson-based packages, extracts
their meson.options from source tarballs, cross-references with -D flags
in build.sh, and loads curated dependency mappings.

This tool is RE-RUNNABLE: it drops and recreates all 3 meson tables
each run, so updating a package version and re-running picks up all
changes automatically.

Attribution:
    This tool is an original work by InterGenOS (GPL-3.0-or-later).
    It extends the BLFS package database created by parse-blfs-book.py.

Usage:
    python3 scripts/populate-meson-db.py
    python3 scripts/populate-meson-db.py --verbose
    python3 scripts/populate-meson-db.py --sources-dir /mnt/igos/sources
"""

import argparse
import io
import json
import os
import re
import sqlite3
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML required. Install with: pip3 install pyyaml")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Schema — 3 new tables for the meson feature database
# ---------------------------------------------------------------------------

MESON_SCHEMA = """
DROP TABLE IF EXISTS meson_option_deps;
DROP TABLE IF EXISTS meson_options;
DROP TABLE IF EXISTS meson_packages;

CREATE TABLE meson_packages (
    id INTEGER PRIMARY KEY,
    igos_name TEXT NOT NULL UNIQUE,
    tier TEXT NOT NULL,
    version TEXT NOT NULL,
    tarball_name TEXT,
    tarball_dir TEXT,
    options_file TEXT,
    option_count INTEGER DEFAULT 0,
    feature_count INTEGER DEFAULT 0,
    auto_count INTEGER DEFAULT 0
);

CREATE TABLE meson_options (
    id INTEGER PRIMARY KEY,
    package_id INTEGER NOT NULL REFERENCES meson_packages(id),
    opt_name TEXT NOT NULL,
    opt_type TEXT NOT NULL,
    default_value TEXT,
    our_value TEXT,
    choices TEXT,
    description TEXT,
    category TEXT,
    is_deprecated INTEGER DEFAULT 0,
    UNIQUE(package_id, opt_name)
);

CREATE TABLE meson_option_deps (
    id INTEGER PRIMARY KEY,
    option_id INTEGER NOT NULL REFERENCES meson_options(id),
    dep_pkg_config TEXT,
    dep_igos_name TEXT,
    dep_blfs_anchor TEXT,
    in_tree INTEGER DEFAULT 0,
    notes TEXT,
    UNIQUE(option_id, dep_pkg_config)
);

CREATE INDEX idx_meson_options_pkg ON meson_options(package_id);
CREATE INDEX idx_meson_options_auto ON meson_options(package_id, opt_type, our_value);
CREATE INDEX idx_meson_option_deps_opt ON meson_option_deps(option_id);
CREATE INDEX idx_meson_option_deps_igos ON meson_option_deps(dep_igos_name);
"""

# ---------------------------------------------------------------------------
# Step 1: Scan packages/ for meson packages
# ---------------------------------------------------------------------------

def find_meson_packages(packages_dir: Path, verbose: bool = False) -> list[dict]:
    """Find all packages that use meson by scanning build.sh files."""
    meson_pkgs = []

    for tier_dir in sorted(packages_dir.iterdir()):
        if not tier_dir.is_dir():
            continue
        tier = tier_dir.name

        for pkg_dir in sorted(tier_dir.iterdir()):
            if not pkg_dir.is_dir():
                continue

            build_sh = pkg_dir / "build.sh"
            package_yml = pkg_dir / "package.yml"

            if not build_sh.exists() or not package_yml.exists():
                continue

            # Check if this package uses meson
            build_content = build_sh.read_text()
            if "meson setup" not in build_content and "meson_setup" not in build_content:
                # Also check package.yml build_style
                yml_content = package_yml.read_text()
                if "build_style: meson" not in yml_content:
                    continue

            # Parse package.yml for metadata
            name = ""
            version = ""
            source_url = ""
            for line in package_yml.read_text().splitlines():
                if line.startswith("name:"):
                    name = line.split(":", 1)[1].strip().strip("'\"")
                elif line.startswith("version:"):
                    version = line.split(":", 1)[1].strip().strip("'\"")
                elif line.strip().startswith("- url:"):
                    source_url = line.split("url:", 1)[1].strip().strip("'\"")

            if not name or not version:
                continue

            # Resolve ${version} in source URL
            resolved_url = source_url.replace("${version}", version)
            source_filename = resolved_url.split("/")[-1] if resolved_url else ""

            meson_pkgs.append({
                "igos_name": name,
                "tier": tier,
                "version": version,
                "source_url": resolved_url,
                "source_filename": source_filename,
                "build_sh_path": str(build_sh),
                "build_sh_content": build_content,
            })

            if verbose:
                print(f"  Found meson package: {name} {version} ({tier})")

    return meson_pkgs


# ---------------------------------------------------------------------------
# Step 2: Extract and parse meson options from tarballs
# ---------------------------------------------------------------------------

def find_tarball(source_filename: str, sources_dir: Path) -> Path | None:
    """Find a source tarball in the sources directory."""
    if not source_filename:
        return None

    tarball = sources_dir / source_filename
    if tarball.exists():
        return tarball

    # Try case-insensitive match
    lower = source_filename.lower()
    for f in sources_dir.iterdir():
        if f.name.lower() == lower:
            return f

    return None


def _get_top_dir(tarball_path: Path) -> str | None:
    """Get the top-level directory name from a tarball."""
    suffixes = tarball_path.suffixes
    # Determine open mode
    if ".xz" in suffixes:
        mode = "r:xz"
    elif ".gz" in suffixes or ".tgz" in suffixes:
        mode = "r:gz"
    elif ".bz2" in suffixes:
        mode = "r:bz2"
    else:
        mode = "r"

    try:
        with tarfile.open(str(tarball_path), mode) as tf:
            for member in tf:
                name = member.name.lstrip("./")
                if "/" in name:
                    return name.split("/")[0]
                elif member.isdir():
                    return name
    except (tarfile.TarError, EOFError):
        return None

    return None


def extract_meson_options_file(tarball_path: Path, verbose: bool = False) -> tuple[str | None, str | None, str | None]:
    """Extract the meson options file from a tarball.

    Returns: (file_content, options_filename, top_dir) or (None, None, top_dir)
    """
    suffixes = tarball_path.suffixes
    if ".xz" in suffixes:
        mode = "r:xz"
    elif ".gz" in suffixes or ".tgz" in suffixes:
        mode = "r:gz"
    elif ".bz2" in suffixes:
        mode = "r:bz2"
    else:
        mode = "r"

    try:
        with tarfile.open(str(tarball_path), mode) as tf:
            # Find top-level dir from first member
            top_dir = None
            for member in tf:
                name = member.name.lstrip("./")
                if "/" in name:
                    top_dir = name.split("/")[0]
                    break
                elif member.isdir():
                    top_dir = name

            if not top_dir:
                return None, None, None

            # Look for meson options file (top-level only, not subprojects)
            for opt_name in ("meson.options", "meson_options.txt"):
                target = f"{top_dir}/{opt_name}"
                # Also check with ./ prefix
                for candidate in (target, f"./{target}"):
                    try:
                        member = tf.getmember(candidate)
                        # Security: validate path
                        clean = member.name.lstrip("./")
                        if ".." in clean or clean.startswith("/"):
                            continue
                        f = tf.extractfile(member)
                        if f:
                            content = f.read().decode("utf-8", errors="replace")
                            return content, opt_name, top_dir
                    except KeyError:
                        continue

            return None, None, top_dir

    except (tarfile.TarError, EOFError) as e:
        if verbose:
            print(f"    WARNING: Failed to read tarball {tarball_path}: {e}")
        return None, None, None


def parse_meson_options(content: str) -> list[dict]:
    """Parse option() declarations from a meson options file.

    Returns a list of dicts with keys:
        name, type, value, description, choices, is_deprecated
    """
    options = []

    # Find each option( call and extract its full body
    # This handles multi-line declarations by matching balanced parens
    pos = 0
    while pos < len(content):
        match = re.search(r"\boption\s*\(", content[pos:])
        if not match:
            break

        start = pos + match.end()  # position after the opening (
        depth = 1
        i = start
        while i < len(content) and depth > 0:
            ch = content[i]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == "#":
                # Skip to end of line (comment)
                while i < len(content) and content[i] != "\n":
                    i += 1
            elif ch in ("'", '"'):
                # Skip quoted string
                quote = ch
                i += 1
                while i < len(content) and content[i] != quote:
                    if content[i] == "\\":
                        i += 1  # skip escaped char
                    i += 1
            i += 1

        if depth != 0:
            pos = start
            continue

        body = content[start : i - 1]  # content between ( and )
        pos = i

        # Parse the option body
        opt = _parse_option_body(body)
        if opt:
            options.append(opt)

    return options


def _parse_option_body(body: str) -> dict | None:
    """Parse the body of a single option() call."""
    # Remove comments
    lines = []
    for line in body.split("\n"):
        # Strip inline comments (but not inside strings)
        clean = _strip_comment(line)
        lines.append(clean)
    body = " ".join(lines)

    # First argument is the option name (a string)
    name_match = re.match(r"\s*'([^']+)'", body)
    if not name_match:
        name_match = re.match(r'\s*"([^"]+)"', body)
    if not name_match:
        return None

    name = name_match.group(1)
    rest = body[name_match.end() :]

    # Extract keyword arguments
    opt_type = _extract_kwarg_str(rest, "type") or "string"
    value = _extract_kwarg_value(rest, "value")
    description = _extract_kwarg_str(rest, "description")
    choices = _extract_kwarg_list(rest, "choices")
    deprecated = _extract_kwarg_raw(rest, "deprecated")

    is_deprecated = 0
    if deprecated is not None:
        is_deprecated = 1

    return {
        "name": name,
        "type": opt_type,
        "value": value,
        "description": description,
        "choices": json.dumps(choices) if choices else None,
        "is_deprecated": is_deprecated,
    }


def _strip_comment(line: str) -> str:
    """Strip a # comment from a line, respecting quoted strings."""
    in_quote = None
    for i, ch in enumerate(line):
        if ch in ("'", '"') and in_quote is None:
            in_quote = ch
        elif ch == in_quote:
            in_quote = None
        elif ch == "#" and in_quote is None:
            return line[:i]
    return line


def _extract_kwarg_str(text: str, name: str) -> str | None:
    """Extract a string keyword argument value: name: 'value' or name : 'a' + 'b'."""
    pattern = rf"\b{name}\s*:\s*"
    match = re.search(pattern, text)
    if not match:
        return None

    rest = text[match.end() :]
    parts = []

    while rest:
        rest = rest.lstrip()
        if rest.startswith("'"):
            end = rest.find("'", 1)
            if end == -1:
                break
            parts.append(rest[1:end])
            rest = rest[end + 1 :].lstrip()
            if rest.startswith("+"):
                rest = rest[1:]
                continue
            break
        elif rest.startswith('"'):
            end = rest.find('"', 1)
            if end == -1:
                break
            parts.append(rest[1:end])
            rest = rest[end + 1 :].lstrip()
            if rest.startswith("+"):
                rest = rest[1:]
                continue
            break
        else:
            break

    return "".join(parts) if parts else None


def _extract_kwarg_value(text: str, name: str) -> str | None:
    """Extract a keyword argument value that could be string, bool, int, or list."""
    pattern = rf"\b{name}\s*:\s*"
    match = re.search(pattern, text)
    if not match:
        return None

    rest = text[match.end() :].lstrip()

    # String value
    if rest.startswith("'") or rest.startswith('"'):
        return _extract_kwarg_str(text, name)

    # Boolean value
    bool_match = re.match(r"(true|false)\b", rest)
    if bool_match:
        return bool_match.group(1)

    # Integer value
    int_match = re.match(r"(-?\d+)\b", rest)
    if int_match:
        return int_match.group(1)

    # List value
    if rest.startswith("["):
        return str(_extract_list_at(rest))

    return None


def _extract_kwarg_list(text: str, name: str) -> list[str] | None:
    """Extract a list keyword argument: name: ['a', 'b']."""
    pattern = rf"\b{name}\s*:\s*\["
    match = re.search(pattern, text)
    if not match:
        return None

    rest = text[match.end() - 1 :]  # include the [
    return _extract_list_at(rest)


def _extract_list_at(text: str) -> list[str]:
    """Extract a [...] list starting at position 0 of text."""
    if not text.startswith("["):
        return []

    # Find matching ]
    depth = 0
    end = 0
    for i, ch in enumerate(text):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                end = i
                break

    inner = text[1:end]
    items = []
    for part in re.findall(r"'([^']*)'|\"([^\"]*)\"", inner):
        items.append(part[0] or part[1])
    return items


def _extract_kwarg_raw(text: str, name: str) -> str | None:
    """Extract a raw keyword argument (for deprecated: which can be bool, dict, or string)."""
    pattern = rf"\b{name}\s*:\s*"
    match = re.search(pattern, text)
    if not match:
        return None

    rest = text[match.end() :].lstrip()

    # Could be true/false, a string, or a dict
    if rest.startswith("true") or rest.startswith("false"):
        return rest.split(",")[0].split(")")[0].strip()
    elif rest.startswith("'") or rest.startswith('"'):
        return _extract_kwarg_str(text, name)
    elif rest.startswith("{"):
        # Find matching }
        end = rest.find("}")
        if end != -1:
            return rest[: end + 1]

    return "true"


# ---------------------------------------------------------------------------
# Step 3: Extract -D flags from build.sh
# ---------------------------------------------------------------------------

FLAG_RE = re.compile(r"-D\s*([a-zA-Z0-9_-]+)=(\S+)")


def extract_build_flags(build_sh_content: str) -> dict[str, str]:
    """Extract -D flags from a build.sh file's meson setup invocation."""
    flags = {}
    for m in FLAG_RE.finditer(build_sh_content):
        flag_name = m.group(1)
        flag_value = m.group(2).rstrip("\\")
        flags[flag_name] = flag_value
    return flags


# ---------------------------------------------------------------------------
# Step 4: Option category classification
# ---------------------------------------------------------------------------

TEST_DOC_NAMES = frozenset({
    "tests", "test", "build-tests", "build-testsuite", "build-test",
    "installed_tests", "installed-tests", "unit_tests", "functional_tests",
    "docs", "doc", "documentation", "man", "man-pages",
    "examples", "build-examples", "build-demos", "screenshots",
    "doxygen", "html-docs", "nls", "gtk_doc", "gtk-doc", "gtkdoc",
    "enable-gtk-doc", "enable-docs", "desktop_docs", "docbook",
    "docbook_docs", "apidocs", "bash_completion",
})


def classify_option(name: str, opt_type: str, description: str | None) -> str:
    """Classify a meson option into a category."""
    lower_name = name.lower()

    # Test/doc suppression
    if lower_name in TEST_DOC_NAMES:
        return "test_doc"
    if any(kw in lower_name for kw in ("test", "doc", "example", "demo")):
        return "test_doc"

    # Meson builtins (these are not user-defined options)
    if lower_name in ("prefix", "libdir", "buildtype", "wrap-mode", "auto_features"):
        return "builtin"

    # Path/platform configuration
    if opt_type in ("string", "integer"):
        return "path_config"

    # Feature flags (the ones that matter for auto-detection)
    if opt_type == "feature":
        return "feature"

    # Boolean options that act as feature toggles
    if opt_type == "boolean" and description:
        desc_lower = description.lower()
        if any(w in desc_lower for w in (
            "enable", "build with", "backend", "support", "use ",
            "build the", "build support", "plugin",
        )):
            return "feature"

    if opt_type == "array":
        return "platform"

    if opt_type == "combo":
        return "platform"

    return "other"


# ---------------------------------------------------------------------------
# Step 5: Load curations
# ---------------------------------------------------------------------------

def load_curations(curations_path: Path) -> dict:
    """Load the curated option-to-dependency mappings."""
    if not curations_path.exists():
        return {}

    with open(curations_path) as f:
        data = yaml.safe_load(f)

    return data or {}


def base_package_name(igos_name: str) -> str:
    """Strip pass suffixes to find the base package for curation lookup."""
    for suffix in ("-pass2", "-pass1", "-pass3"):
        if igos_name.endswith(suffix):
            return igos_name[: -len(suffix)]
    return igos_name


# ---------------------------------------------------------------------------
# Step 6: Populate database
# ---------------------------------------------------------------------------

def populate_db(
    conn: sqlite3.Connection,
    meson_pkgs: list[dict],
    curations: dict,
    sources_dir: Path,
    verbose: bool = False,
):
    """Populate all 3 meson tables."""
    # Drop and recreate tables
    conn.executescript(MESON_SCHEMA)

    # Track tarball extraction cache (for pass packages sharing tarballs)
    extraction_cache: dict[str, tuple] = {}  # tarball_name -> (content, options_file, top_dir)

    total_options = 0
    total_features = 0
    total_auto = 0
    pkgs_with_options = 0

    for pkg in meson_pkgs:
        igos_name = pkg["igos_name"]
        source_filename = pkg["source_filename"]
        base_name = base_package_name(igos_name)

        # Find tarball
        tarball = find_tarball(source_filename, sources_dir)
        tarball_name = source_filename if tarball else None
        top_dir = None
        options_file_name = None
        parsed_options = []

        if tarball:
            # Check extraction cache (pass packages share tarballs)
            cache_key = str(tarball)
            if cache_key in extraction_cache:
                content, options_file_name, top_dir = extraction_cache[cache_key]
            else:
                content, options_file_name, top_dir = extract_meson_options_file(
                    tarball, verbose
                )
                extraction_cache[cache_key] = (content, options_file_name, top_dir)

            if content:
                parsed_options = parse_meson_options(content)
                if verbose and parsed_options:
                    print(f"  {igos_name}: parsed {len(parsed_options)} options from {options_file_name}")
            elif verbose:
                print(f"  {igos_name}: no meson options file found in {source_filename}")
        elif verbose:
            print(f"  {igos_name}: tarball not found ({source_filename})")

        # Extract -D flags from build.sh
        build_flags = extract_build_flags(pkg["build_sh_content"])

        # Insert meson_packages row
        conn.execute(
            """INSERT INTO meson_packages
               (igos_name, tier, version, tarball_name, tarball_dir, options_file)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (igos_name, pkg["tier"], pkg["version"], tarball_name, top_dir, options_file_name),
        )
        pkg_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Insert meson_options rows
        opt_count = 0
        feat_count = 0
        auto_count = 0

        for opt in parsed_options:
            our_value = build_flags.get(opt["name"])
            category = classify_option(opt["name"], opt["type"], opt["description"])

            conn.execute(
                """INSERT OR IGNORE INTO meson_options
                   (package_id, opt_name, opt_type, default_value, our_value,
                    choices, description, category, is_deprecated)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    pkg_id, opt["name"], opt["type"], opt["value"],
                    our_value, opt["choices"], opt["description"],
                    category, opt["is_deprecated"],
                ),
            )
            opt_count += 1
            if opt["type"] == "feature" or (category == "feature" and opt["type"] == "boolean"):
                feat_count += 1
                if our_value is None and opt["value"] in ("auto", "true", None):
                    auto_count += 1

        # Also insert flags from build.sh that weren't in the options file
        # (custom project options defined in meson.build, not meson.options)
        known_opts = {o["name"] for o in parsed_options}
        for flag_name, flag_value in build_flags.items():
            if flag_name not in known_opts:
                # Skip meson builtins
                if flag_name in ("prefix", "libdir", "buildtype", "wrap-mode",
                                 "auto_features", "default_library"):
                    continue
                category = classify_option(flag_name, "unknown", None)
                conn.execute(
                    """INSERT OR IGNORE INTO meson_options
                       (package_id, opt_name, opt_type, default_value, our_value,
                        choices, description, category, is_deprecated)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (pkg_id, flag_name, "unknown", None, flag_value,
                     None, None, category, 0),
                )
                opt_count += 1

        # Update aggregate counts
        conn.execute(
            """UPDATE meson_packages
               SET option_count = ?, feature_count = ?, auto_count = ?
               WHERE id = ?""",
            (opt_count, feat_count, auto_count, pkg_id),
        )

        total_options += opt_count
        total_features += feat_count
        total_auto += auto_count
        if opt_count > 0:
            pkgs_with_options += 1

    # Insert curated dependency mappings
    dep_count = 0
    for pkg_base_name, options in curations.items():
        for opt_name, deps in options.items():
            # Find all packages that match this base name
            rows = conn.execute(
                "SELECT mp.id FROM meson_packages mp WHERE mp.igos_name = ? OR mp.igos_name LIKE ?",
                (pkg_base_name, f"{pkg_base_name}-pass%"),
            ).fetchall()

            for (mpkg_id,) in rows:
                # Find the option in this package
                opt_row = conn.execute(
                    "SELECT id FROM meson_options WHERE package_id = ? AND opt_name = ?",
                    (mpkg_id, opt_name),
                ).fetchone()

                if not opt_row:
                    continue

                opt_id = opt_row[0]

                for dep in deps:
                    dep_pkg_config = dep.get("pkg_config")
                    dep_igos_name = dep.get("igos_name")
                    notes = dep.get("notes", "")

                    # Check if dep is in our tree
                    in_tree = 0
                    if dep_igos_name and dep_igos_name != "null":
                        igos_row = conn.execute(
                            "SELECT 1 FROM igos_status WHERE blfs_anchor = ?",
                            (dep_igos_name,),
                        ).fetchone()
                        if igos_row:
                            in_tree = 1
                        else:
                            # Check aliases
                            alias_row = conn.execute(
                                "SELECT blfs_anchor FROM aliases WHERE igos_name = ?",
                                (dep_igos_name,),
                            ).fetchone()
                            if alias_row:
                                in_tree = 1

                    # Use a unique key for the constraint
                    # If pkg_config is null, use igos_name as the key
                    key = dep_pkg_config if dep_pkg_config and dep_pkg_config != "null" else dep_igos_name

                    try:
                        conn.execute(
                            """INSERT INTO meson_option_deps
                               (option_id, dep_pkg_config, dep_igos_name, dep_blfs_anchor,
                                in_tree, notes)
                               VALUES (?, ?, ?, ?, ?, ?)""",
                            (opt_id, key, dep_igos_name, dep_igos_name, in_tree, notes),
                        )
                        dep_count += 1
                    except sqlite3.IntegrityError:
                        pass  # duplicate

    conn.commit()

    # Summary
    print(f"\n  Meson Feature Database populated:")
    print(f"    Packages:          {len(meson_pkgs)}")
    print(f"    With options file: {pkgs_with_options}")
    print(f"    Total options:     {total_options}")
    print(f"    Feature options:   {total_features}")
    print(f"    Features at risk:  {total_auto} (auto/true with no -D flag)")
    print(f"    Dep mappings:      {dep_count}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Populate the meson feature database in blfs-packages.db"
    )
    parser.add_argument(
        "--db", default="build/blfs-packages.db",
        help="SQLite database path (default: build/blfs-packages.db)",
    )
    parser.add_argument(
        "--sources-dir", default="build/sources",
        help="Directory containing source tarballs (default: build/sources)",
    )
    parser.add_argument(
        "--packages-dir", default="packages",
        help="InterGenOS packages directory (default: packages/)",
    )
    parser.add_argument(
        "--curations", default="data/meson-curations.yaml",
        help="Curated dependency mappings (default: data/meson-curations.yaml)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Print detailed progress",
    )
    args = parser.parse_args()

    # Resolve paths relative to project root
    project_root = Path(__file__).parent.parent
    db_path = project_root / args.db
    sources_dir = project_root / args.sources_dir
    packages_dir = project_root / args.packages_dir
    curations_path = project_root / args.curations

    if not db_path.exists():
        print(f"ERROR: Database not found at {db_path}")
        print(f"  Run: python3 scripts/parse-blfs-book.py")
        sys.exit(1)

    if not packages_dir.exists():
        print(f"ERROR: Packages directory not found at {packages_dir}")
        sys.exit(1)

    print(f"Populating meson feature database...")
    print(f"  Database:    {db_path}")
    print(f"  Sources:     {sources_dir}")
    print(f"  Packages:    {packages_dir}")
    print(f"  Curations:   {curations_path}")

    # Step 1: Find meson packages
    print(f"\nScanning for meson packages...")
    meson_pkgs = find_meson_packages(packages_dir, args.verbose)
    print(f"  Found {len(meson_pkgs)} meson packages")

    # Step 4: Load curations
    curations = load_curations(curations_path)
    if curations:
        print(f"  Loaded curations for {len(curations)} packages")
    else:
        print(f"  No curations file found (will populate options only)")

    # Steps 2-3 + 5-6: Populate database
    conn = sqlite3.connect(str(db_path))
    populate_db(conn, meson_pkgs, curations, sources_dir, args.verbose)
    conn.close()


if __name__ == "__main__":
    main()
