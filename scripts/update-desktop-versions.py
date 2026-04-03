#!/usr/bin/env python3
"""Batch-update desktop package versions to match BLFS 13.0.

Reads the mismatch data, updates each package.yml:
  - Sets new version
  - Updates GNOME URL paths (major.minor directory)
  - Resets SHA256 to NEEDS_CHECKSUM for re-download

Run download-sources.py --tier desktop --update-checksums afterward
to download new tarballs and compute checksums.

Usage:
    python3 scripts/update-desktop-versions.py
    python3 scripts/update-desktop-versions.py --dry-run
"""

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
PACKAGES_DIR = PROJECT_ROOT / "packages" / "desktop"

# Mismatch data from the BLFS 13.0 audit (2026-04-03)
# Format: (package_name, old_version, new_version)
UPDATES = [
    ("Mako", "1.3.6", "1.3.10"),
    ("abseil-cpp", "20240722.0", "20260107.1"),
    ("adwaita-icon-theme", "47.0", "49.0"),
    ("alsa-lib", "1.2.13", "1.2.15.3"),
    ("alsa-utils", "1.2.13", "1.2.15.2"),
    ("appstream", "1.0.4", "1.1.2"),
    ("at-spi2-core", "2.54.0", "2.58.3"),
    ("blueprint-compiler", "0.14.0", "0.18.0"),
    ("bluez", "5.79", "5.86"),
    ("boost", "1.86.0", "1.90.0"),
    ("brotli", "1.1.0", "1.2.0"),
    ("bubblewrap", "0.10.0", "0.11.0"),
    ("c-ares", "1.34.4", "1.34.6"),
    ("cairo", "1.18.2", "1.18.4"),
    ("colord", "1.4.7", "1.4.8"),
    ("cracklib", "2.10.2", "2.10.3"),
    ("cups", "2.4.11", "2.4.16"),
    ("cython", "3.0.11", "3.2.4"),
    ("dav1d", "1.5.0", "1.5.3"),
    ("dconf", "0.40.0", "0.49.0"),
    ("docutils", "0.21.2", "0.22.4"),
    ("enchant", "2.8.2", "2.8.15"),
    ("evolution-data-server", "3.54.3", "3.58.3"),
    ("exiv2", "0.28.3", "0.28.7"),
    ("ffmpeg", "7.1", "8.0.1"),
    ("flac", "1.4.3", "1.5.0"),
    ("font-alias", "1.0.5", "1.0.6"),
    ("fontconfig", "2.15.0", "2.17.1"),
    ("freetype2", "2.13.3", "2.14.1"),
    ("fuse3", "3.16.2", "3.18.1"),
    ("gcr4", "4.3.0", "4.4.0.1"),
    ("gdk-pixbuf", "2.42.12", "2.44.5"),
    ("gdm", "47.0", "49.2"),
    ("geoclue2", "2.7.2", "2.8.0"),
    ("gexiv2", "0.14.3", "0.14.6"),
    ("gjs", "1.82.1", "1.86.0"),
    ("glslang", "15.1.0", "16.2.0"),
    ("gnome-autoar", "0.4.4", "0.4.5"),
    ("gnome-backgrounds", "47.0", "49.0"),
    ("gnome-control-center", "47.4", "49.4"),
    ("gnome-desktop", "44.1", "44.5"),
    ("gnome-menus", "3.36.0", "3.38.1"),
    ("gnome-session", "47.0", "49.2"),
    ("gnome-settings-daemon", "47.1", "49.1"),
    ("gnome-shell", "47.4", "49.4"),
    ("gnome-shell-extensions", "47.2", "49.0"),
    ("gnome-terminal", "3.54.2", "3.58.1"),
    ("gnupg2", "2.4.7", "2.5.17"),
    ("gnutls", "3.8.8", "3.8.12"),
    ("gpgme", "1.24.1", "2.0.1"),
    ("gsettings-desktop-schemas", "47.1", "49.1"),
    ("gst-plugins-bad", "1.24.10", "1.28.1"),
    ("gst-plugins-base", "1.24.10", "1.28.1"),
    ("gst-plugins-good", "1.24.10", "1.28.1"),
    ("gstreamer", "1.24.10", "1.28.1"),
    ("gtk3", "3.24.43", "3.24.51"),
    ("gtk4", "4.16.12", "4.20.3"),
    ("gvfs", "1.54.2", "1.58.2"),
    ("harfbuzz", "10.1.0", "12.3.2"),
    ("hatchling", "1.25.0", "1.28.0"),
    ("hwdata", "0.389", "0.404"),
    ("ibus", "1.5.31", "1.5.33"),
    ("icu", "76-1", "78.2"),
    ("inih", "58", "62"),
    ("iptables", "1.8.11", "1.8.12"),
    ("iso-codes", "4.17.0", "4.20.1"),
    ("json-glib", "1.10.6", "1.10.8"),
    ("lcms2", "2.16", "2.18"),
    ("libFS", "1.0.9", "1.0.10"),
    ("libSM", "1.2.4", "1.2.6"),
    ("libX11", "1.8.10", "1.8.13"),
    ("libXScrnSaver", "1.2.4", "1.2.5"),
    ("libXau", "1.0.11", "1.0.12"),
    ("libXcomposite", "0.4.6", "0.4.7"),
    ("libXdamage", "1.1.6", "1.1.7"),
    ("libXext", "1.3.6", "1.3.7"),
    ("libXfixes", "6.0.1", "6.0.2"),
    ("libXft", "2.3.8", "2.3.9"),
    ("libXinerama", "1.1.5", "1.1.6"),
    ("libXmu", "1.2.1", "1.3.1"),
    ("libXpm", "3.5.17", "3.5.18"),
    ("libXpresent", "1.0.1", "1.0.2"),
    ("libXrandr", "1.5.4", "1.5.5"),
    ("libXv", "1.0.12", "1.0.13"),
    ("libXvMC", "1.0.14", "1.0.15"),
    ("libXxf86dga", "1.1.6", "1.1.7"),
    ("libXxf86vm", "1.1.5", "1.1.7"),
    ("libadwaita1", "1.6.2", "1.8.4"),
    ("libassuan", "3.0.1", "3.0.2"),
    ("libavif", "1.1.1", "1.3.0"),
    ("libclc", "19.1.7", "21.1.8"),
    ("libdisplay-info", "0.2.0", "0.3.0"),
    ("libdrm", "2.4.124", "2.4.131"),
    ("libei", "1.3.0", "1.5.0"),
    ("libevdev", "1.13.3", "1.13.6"),
    ("libexif", "0.6.24", "0.6.25"),
    ("libfontenc", "1.1.8", "1.1.9"),
    ("libgcrypt", "1.11.0", "1.12.0"),
    ("libgpg-error", "1.51", "1.59"),
    ("libical", "3.0.18", "3.0.20"),
    ("libinput", "1.27.1", "1.31.0"),
    ("libjpeg-turbo", "3.1.0", "3.1.3"),
    ("libjxl", "0.11.1", "0.11.2"),
    ("libmbim", "1.30.0", "1.34.0"),
    ("libnl", "3.11.0", "3.12.0"),
    ("libnotify", "0.8.4", "0.8.8"),
    ("libogg", "1.3.5", "1.3.6"),
    ("libpcap", "1.10.5", "1.10.6"),
    ("libpng", "1.6.44", "1.6.55"),
    ("libportal", "0.8.1", "0.9.1"),
    ("libppd", "2.0.0", "2.1.1"),
    ("libqmi", "1.34.0", "1.38.0"),
    ("librest", "0.9.1", "0.10.2"),
    ("libsecret", "0.21.4", "0.21.7"),
    ("libshumate", "1.3.2", "1.5.3"),
    ("libsoup3", "3.6.4", "3.6.6"),
    ("libtiff", "4.7.0", "4.7.1"),
    ("libusb", "1.0.27", "1.0.29"),
    ("libva", "2.22.0", "2.23.0"),
    ("libvpx", "1.15.0", "1.16.0"),
    ("libwacom", "2.13.0", "2.18.0"),
    ("libwebp", "1.5.0", "1.6.0"),
    ("libxcvt", "0.1.2", "0.1.3"),
    ("libxkbcommon", "1.7.0", "1.13.1"),
    ("libxkbfile", "1.1.3", "1.2.0"),
    ("libxml2", "2.13.5", "2.15.1"),
    ("libxmlb", "0.3.21", "0.3.25"),
    ("libxshmfence", "1.3.2", "1.3.3"),
    ("libxslt", "1.1.42", "1.1.45"),
    ("lua", "5.4.7", "5.4.8"),
    ("lvm2", "2.03.28", "2.03.38"),
    ("lxml", "5.3.0", "6.0.2"),
    ("markdown", "3.7", "3.10.2"),
    ("mitkrb", "1.21.3", "1.22.2"),
    ("modemmanager", "1.22.0", "1.24.2"),
    ("mpg123", "1.32.9", "1.33.4"),
    ("mupdf", "1.24.11", "1.26.12"),
    ("mutter", "47.4", "49.4"),
    ("nasm", "2.16.03", "3.01"),
    ("nautilus", "47.2", "49.3"),
    ("nettle", "3.10.1", "3.10.2"),
    ("networkmanager", "1.50.0", "1.56.0"),
    ("newt", "0.52.24", "0.52.25"),
    ("npth", "1.7", "1.8"),
    ("openjpeg2", "2.5.3", "2.5.4"),
    ("opus", "1.5.2", "1.6.1"),
    ("pango", "1.54.0", "1.57.0"),
    ("pinentry", "1.3.1", "1.3.2"),
    ("pipewire", "1.2.7", "1.6.0"),
    ("pixman", "0.44.2", "0.46.4"),
    ("pluggy", "1.5.0", "1.6.0"),
    ("polkit", "125", "127"),
    ("poppler", "24.12.0", "26.02.0"),
    ("power-profiles-daemon", "0.23", "0.30"),
    ("protobuf", "28.3", "33.5"),
    ("protobuf-c", "1.5.0", "1.5.2"),
    ("pycairo", "1.27.0", "1.29.0"),
    ("pygments", "2.18.0", "2.19.2"),
    ("pygobject3", "3.50.0", "3.54.5"),
    ("qpdf", "11.9.1", "12.3.2"),
    ("ruby", "3.3.6", "4.0.1"),
    ("samba", "4.21.3", "4.23.5"),
    ("sbc", "2.0", "2.2"),
    ("sessreg", "1.1.3", "1.1.4"),
    ("setuptools-scm", "8.1.0", "9.2.2"),
    ("smproxy", "1.0.7", "1.0.8"),
    ("soundtouch", "2.3.3", "2.4.0"),
    ("spirv-headers", "1.3.290.0", "1.4.341.0"),
    ("spirv-llvm-translator", "19.1.7", "21.1.4"),
    ("spirv-tools", "1.3.290.0", "1.4.341.0"),
    ("svt-av1", "3.0.0", "4.0.1"),
    ("tecla", "47.0", "49.0"),
    ("tinysparql", "3.8.1", "3.10.1"),
    ("udisks2", "2.10.1", "2.11.1"),
    ("upower", "1.90.6", "1.91.1"),
    ("vte", "0.78.2", "0.82.3"),
    ("vulkan-headers", "1.3.290.0", "1.4.341.0"),
    ("vulkan-loader", "1.3.290.0", "1.4.341.0"),
    ("wayland", "1.23.1", "1.24.0"),
    ("wayland-protocols", "1.38", "1.47"),
    ("wireplumber", "0.5.7", "0.5.13"),
    ("xauth", "1.1.3", "1.1.5"),
    ("xcb-util-cursor", "0.1.5", "0.1.6"),
    ("xdg-desktop-portal", "1.18.4", "1.20.3"),
    ("xdg-desktop-portal-gtk", "1.15.2", "1.15.3"),
    ("xdg-user-dirs", "0.18", "0.19"),
    ("xdpyinfo", "1.3.4", "1.4.0"),
    ("xdriinfo", "1.0.7", "1.0.8"),
    ("xhost", "1.0.9", "1.0.10"),
    ("xkbcomp", "1.4.7", "1.5.0"),
    ("xkeyboard-config", "2.43", "2.46"),
    ("xorgproto", "2024.1", "2025.1"),
    ("xprop", "1.2.7", "1.2.8"),
    ("xtrans", "1.5.2", "1.6.0"),
    ("xwayland", "24.1.5", "24.1.9"),
    ("yelp-xsl", "42.1", "49.0"),
]


def gnome_major_minor(version: str) -> str:
    """Extract GNOME-style major.minor from a version string.

    GNOME uses the first component (or first two dot-separated parts)
    as the directory name in download.gnome.org URLs.

    Examples:
        49.2 -> 49
        3.58.3 -> 3.58
        2.44.5 -> 2.44
        0.49.0 -> 0.49
        44.5 -> 44
        4.4.0.1 -> 4.4
    """
    parts = version.split(".")
    if len(parts) == 1:
        return parts[0]
    # If major is a single digit or two digits < 40, use major.minor
    # If major >= 40 (GNOME 40+ versioning), use just major
    try:
        major = int(parts[0])
        if major >= 40 and len(parts) == 2:
            return parts[0]
        return f"{parts[0]}.{parts[1]}"
    except ValueError:
        return f"{parts[0]}.{parts[1]}"


def update_package(name: str, old_version: str, new_version: str, dry_run: bool = False) -> bool:
    """Update a package.yml with the new version.

    Returns True if the file was updated.
    """
    pkg_dir = PACKAGES_DIR / name
    pkg_yml = pkg_dir / "package.yml"

    if not pkg_yml.exists():
        print(f"  SKIP: {name} — package.yml not found")
        return False

    content = pkg_yml.read_text()

    # Verify the old version is actually in the file
    if f"version: {old_version}" not in content and f"version: '{old_version}'" not in content:
        # Check if already updated
        if f"version: {new_version}" in content:
            print(f"  SKIP: {name} — already at {new_version}")
            return False
        print(f"  WARN: {name} — version {old_version} not found in package.yml")
        return False

    if dry_run:
        print(f"  [DRY] {name}: {old_version} -> {new_version}")
        return True

    # Update version field
    new_content = content.replace(f"version: {old_version}", f"version: {new_version}")
    new_content = new_content.replace(f"version: '{old_version}'", f"version: '{new_version}'")

    # Update GNOME URL paths: /sources/pkg/MAJOR.MINOR/ -> new major.minor
    if "download.gnome.org" in new_content:
        old_mm = gnome_major_minor(old_version)
        new_mm = gnome_major_minor(new_version)
        if old_mm != new_mm:
            new_content = new_content.replace(f"/{old_mm}/", f"/{new_mm}/")

    # Reset SHA256 to NEEDS_CHECKSUM for the primary source
    # (vendor tarballs and local sources keep their checksums)
    lines = new_content.split("\n")
    in_source = False
    first_sha_reset = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("source:"):
            in_source = True
            continue
        if in_source and (stripped.startswith("- url:") or stripped.startswith("url:")):
            # Check if this is a local/vendor source — don't reset those
            if "local://" in stripped or "vendor" in stripped or "Cargo.lock" in stripped:
                continue
            first_sha_reset = True
            continue
        if first_sha_reset and "sha256:" in stripped:
            indent = len(line) - len(line.lstrip())
            if "placeholder" not in stripped:
                lines[i] = " " * indent + "sha256: NEEDS_CHECKSUM"
            first_sha_reset = False

    new_content = "\n".join(lines)
    pkg_yml.write_text(new_content)
    print(f"  OK: {name}: {old_version} -> {new_version}")
    return True


def main():
    dry_run = "--dry-run" in sys.argv

    print(f"\nUpdating {len(UPDATES)} desktop packages to BLFS 13.0 versions\n")
    if dry_run:
        print("  (DRY RUN — no files will be changed)\n")

    updated = 0
    skipped = 0
    failed = 0

    for name, old_ver, new_ver in UPDATES:
        result = update_package(name, old_ver, new_ver, dry_run)
        if result:
            updated += 1
        else:
            skipped += 1

    print(f"\nDone: {updated} updated, {skipped} skipped/already current")
    if not dry_run and updated > 0:
        print(f"\nNext step: python3 scripts/download-sources.py --tier desktop --update-checksums")


if __name__ == "__main__":
    main()
