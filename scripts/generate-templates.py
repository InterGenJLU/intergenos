#!/usr/bin/env python3
"""InterGenOS Package Template Generator

Reads a YAML package definition file and generates package.yml + build.sh
templates for each package. Designed for batch-generating desktop tier
packages from BLFS research data.

Usage:
    python3 scripts/generate-templates.py <input.yml> [--download-checksums]

Input format (YAML):
    tier: desktop
    packages:
      - name: libgpg-error
        version: "1.59"
        url: https://example.com/libgpg-error-${version}.tar.bz2
        license: LGPL-2.1-or-later
        description: GPG error code library
        homepage: https://www.gnupg.org/
        build_style: autotools
        configure_flags:
          - "--prefix=/usr"
          - "--disable-static"
        deps:
          build: []
          host: []
          runtime: []
        pre_configure: |
          sed -i 's/something/else/' configure
        post_install: |
          ln -sfv foo ${DESTDIR}/usr/lib/bar

Output:
    packages/<tier>/<name>/package.yml
    packages/<tier>/<name>/build.sh
"""

import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml


PACKAGES_ROOT = Path(__file__).parent.parent / "packages"

# ============================================================================
# Build style templates
# ============================================================================

AUTOTOOLS_BUILD_SH = '''\
#!/bin/bash
# {name} {version} — {description}
# {source}
{pre_configure_block}
configure() {{
    ./configure {configure_flags}
}}

build() {{
    make -j${{IGOS_JOBS}}
}}
{check_block}
do_install() {{
    make DESTDIR="$DESTDIR" install{post_install_block}
}}
'''

MESON_BUILD_SH = '''\
#!/bin/bash
# {name} {version} — {description}
# {source}
{pre_configure_block}
configure() {{
    mkdir build
    cd    build

    meson setup ..            \\
          --prefix=/usr       \\
          --libdir=/usr/lib   \\
          --buildtype=release {meson_flags}
}}

build() {{
    cd build
    ninja
}}
{check_block}
do_install() {{
    cd build
    DESTDIR="$DESTDIR" ninja install{post_install_block}
}}
'''

CMAKE_BUILD_SH = '''\
#!/bin/bash
# {name} {version} — {description}
# {source}
{pre_configure_block}
configure() {{
    cmake -B build                    \\
          -DCMAKE_INSTALL_PREFIX=/usr \\
          -DCMAKE_BUILD_TYPE=Release  {cmake_flags}
}}

build() {{
    cmake --build build -j${{IGOS_JOBS}}
}}
{check_block}
do_install() {{
    DESTDIR="$DESTDIR" cmake --install build{post_install_block}
}}
'''

MAKE_BUILD_SH = '''\
#!/bin/bash
# {name} {version} — {description}
# {source}
{pre_configure_block}
build() {{
    make {make_flags} -j${{IGOS_JOBS}}
}}
{check_block}
do_install() {{
    make {make_install_flags} DESTDIR="$DESTDIR" install{post_install_block}
}}
'''


def compute_sha256(url: str, version: str) -> str:
    """Download a URL and compute its SHA256 hash."""
    resolved_url = url.replace("${version}", version)
    print(f"  Downloading {resolved_url}...")
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        try:
            result = subprocess.run(
                ["wget", "-q", "-O", tmp.name, resolved_url],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                print(f"    FAILED to download: {result.stderr.strip()}")
                return "DOWNLOAD_FAILED"

            sha = hashlib.sha256()
            with open(tmp.name, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    sha.update(chunk)
            return sha.hexdigest()
        finally:
            os.unlink(tmp.name)


def format_flags(flags: list[str], prefix: str = "", join_str: str = " \\\n    ") -> str:
    """Format a list of flags for shell scripts."""
    if not flags:
        return ""
    formatted = [f"{prefix}{f}" if prefix else f for f in flags]
    return join_str.join(formatted)


def generate_package_yml(pkg: dict, tier: str) -> str:
    """Generate package.yml content."""
    deps = pkg.get("deps", {})
    build_deps = deps.get("build", [])
    host_deps = deps.get("host", [])
    runtime_deps = deps.get("runtime", [])

    # Format sources
    sources = []
    urls = pkg.get("url", "")
    if isinstance(urls, str):
        urls = [urls]

    for i, url in enumerate(urls):
        entry = {"url": url, "sha256": pkg.get("sha256", "NEEDS_CHECKSUM")}
        if isinstance(pkg.get("sha256"), list):
            entry["sha256"] = pkg["sha256"][i] if i < len(pkg["sha256"]) else "NEEDS_CHECKSUM"
        if pkg.get("filename") and i == 0:
            entry["filename"] = pkg["filename"]
        sources.append(entry)

    yml = {
        "name": pkg["name"],
        "version": str(pkg["version"]),
        "release": 1,
        "description": pkg.get("description", f"{pkg['name']} library"),
        "license": pkg.get("license", "UNKNOWN"),
        "homepage": pkg.get("homepage", ""),
        "tier": tier,
        "build_style": pkg.get("build_style", "autotools"),
        "install_func": "do_install",
        "source": sources,
        "dependencies": {
            "build": build_deps,
            "host": host_deps,
            "runtime": runtime_deps,
        },
    }

    # Add configure_flags for autotools style (used by Python builder)
    if pkg.get("build_style") == "autotools" and pkg.get("configure_flags"):
        yml["configure_flags"] = pkg["configure_flags"]

    # Add patches
    if pkg.get("patches"):
        yml["patches"] = pkg["patches"]

    # Add direct_install
    if pkg.get("direct_install"):
        yml["direct_install"] = True

    return yaml.dump(yml, default_flow_style=False, sort_keys=False)


def generate_build_sh(pkg: dict) -> str:
    """Generate build.sh content based on build_style."""
    style = pkg.get("build_style", "autotools")
    name = pkg["name"]
    version = str(pkg["version"])
    description = pkg.get("description", "")
    source = pkg.get("source_note", "BLFS 13.0")

    # Pre-configure commands
    pre_configure = pkg.get("pre_configure", "").strip()
    pre_configure_block = ""
    if pre_configure:
        pre_configure_block = f"\nconfigure_pre() {{\n    {pre_configure}\n}}\n"

    # Post-install commands
    post_install = pkg.get("post_install", "").strip()
    post_install_block = ""
    if post_install:
        post_install_block = "\n    " + post_install.replace("\n", "\n    ")

    # Check block
    check_cmd = pkg.get("check", "")
    check_block = ""
    if check_cmd:
        check_block = f"\ncheck() {{\n    {check_cmd} || true\n}}\n"

    if style == "autotools":
        flags = pkg.get("configure_flags", ["--prefix=/usr", "--disable-static"])
        flags_str = format_flags(flags, join_str=" \\\n                ")

        # Handle pre_configure as part of configure()
        configure_body = f"./configure {flags_str}"
        if pre_configure:
            configure_body = f"{pre_configure}\n\n    ./configure {flags_str}"

        return AUTOTOOLS_BUILD_SH.format(
            name=name, version=version, description=description,
            source=source,
            pre_configure_block="" if pre_configure else "",
            configure_flags=flags_str,
            check_block=check_block,
            post_install_block=post_install_block,
        ).replace(
            f"    ./configure {flags_str}",
            f"    {configure_body}" if pre_configure else f"    ./configure {flags_str}",
        )

    elif style == "meson":
        flags = pkg.get("meson_flags", [])
        flags_str = ""
        if flags:
            flags_str = "\\\n          " + " \\\n          ".join(flags)

        result = MESON_BUILD_SH.format(
            name=name, version=version, description=description,
            source=source,
            pre_configure_block="" if not pre_configure else f"\n{pre_configure}\n",
            meson_flags=flags_str,
            check_block=check_block,
            post_install_block=post_install_block,
        )
        return result

    elif style == "cmake":
        flags = pkg.get("cmake_flags", [])
        flags_str = ""
        if flags:
            flags_str = "\\\n          " + " \\\n          ".join(f"-D{f}" for f in flags)

        return CMAKE_BUILD_SH.format(
            name=name, version=version, description=description,
            source=source,
            pre_configure_block="" if not pre_configure else f"\n{pre_configure}\n",
            cmake_flags=flags_str,
            check_block=check_block,
            post_install_block=post_install_block,
        )

    elif style == "make":
        return MAKE_BUILD_SH.format(
            name=name, version=version, description=description,
            source=source,
            pre_configure_block="" if not pre_configure else f"\n{pre_configure}\n",
            make_flags=pkg.get("make_flags", ""),
            make_install_flags=pkg.get("make_install_flags", ""),
            check_block=check_block,
            post_install_block=post_install_block,
        )

    elif style == "custom":
        # Custom style — build.sh must be provided separately
        return f"#!/bin/bash\n# {name} {version} — {description}\n# Custom build — provide build.sh manually\n"

    else:
        raise ValueError(f"Unknown build_style: {style}")


def process_input(input_path: str, download_checksums: bool = False):
    """Process an input YAML file and generate templates."""
    with open(input_path) as f:
        data = yaml.safe_load(f)

    tier = data.get("tier", "desktop")
    packages = data.get("packages", [])

    print(f"\nGenerating {len(packages)} package templates for tier: {tier}\n")

    created = 0
    skipped = 0

    for pkg in packages:
        name = pkg["name"]
        version = str(pkg["version"])
        pkg_dir = PACKAGES_ROOT / tier / name

        if pkg_dir.exists():
            print(f"  [SKIP] {name} — already exists")
            skipped += 1
            continue

        # Download and compute checksum if requested
        if download_checksums and pkg.get("sha256", "NEEDS_CHECKSUM") == "NEEDS_CHECKSUM":
            url = pkg.get("url", "")
            if isinstance(url, list):
                url = url[0]
            if url:
                pkg["sha256"] = compute_sha256(url, version)

        # Generate files
        pkg_yml = generate_package_yml(pkg, tier)
        build_sh = generate_build_sh(pkg)

        # Write files
        pkg_dir.mkdir(parents=True, exist_ok=True)
        (pkg_dir / "package.yml").write_text(pkg_yml)
        (pkg_dir / "build.sh").write_text(build_sh)

        print(f"  [OK  ] {name} {version}")
        created += 1

    print(f"\nDone: {created} created, {skipped} skipped")
    print(f"Templates in: {PACKAGES_ROOT / tier}/")


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 generate-templates.py <input.yml> [--download-checksums]")
        sys.exit(1)

    input_path = sys.argv[1]
    download_checksums = "--download-checksums" in sys.argv

    if not os.path.exists(input_path):
        print(f"Error: {input_path} not found")
        sys.exit(1)

    process_input(input_path, download_checksums)


if __name__ == "__main__":
    main()
