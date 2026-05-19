#!/bin/bash
# chrome-helper 1.0 — Download and install Google Chrome
# InterGenOS extra tier
#
# Google Chrome is proprietary software. This helper downloads it
# from Google's official distribution channel. The user accepts
# Google's license terms by running this installer.

configure() {
    set -e
    :
}

build() {
    set -e
    :
}

do_install() {
    set -e
    # Install the helper script
    mkdir -pv "${DESTDIR}/usr/bin"
    cat > "${DESTDIR}/usr/bin/igos-install-chrome" << 'HELPEREOF'
#!/bin/bash
# InterGenOS Google Chrome Installer
#
# Downloads and installs Google Chrome from Google's official source.
# License: https://www.google.com/intl/en/chrome/terms/
#
# H-007 canary migration: records the install footprint via the
# /usr/share/igos/helpers/helper-lib.sh API so pkm files/verify/remove
# see chrome's deposited files.

set -e

# H-007: source the helper-lib API for footprint tracking.
source /usr/share/igos/helpers/helper-lib.sh

CHROME_URL="https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb"
TMPDIR=$(mktemp -d)
# BLOCKING-D fix (2026-05-19): register TMPDIR cleanup via the
# helper-lib's IGOS_HELPER_USER_CLEANUP env var instead of `trap EXIT`.
# Installing a native trap would collide with the one igos_helper_init
# installs for partial-manifest sidecar emission (bash trap-replace
# semantics; no native composition).
IGOS_HELPER_USER_CLEANUP="rm -rf $TMPDIR"

echo ""
echo "  InterGenOS Google Chrome Installer"
echo "  ==================================="
echo ""
echo "  Google Chrome is proprietary software."
echo "  License: https://www.google.com/intl/en/chrome/terms/"
echo ""

# Check for root
if [ "$(id -u)" -ne 0 ]; then
    echo "  ERROR: Run via 'sudo pkm install-helper chrome' instead."
    echo "  Direct invocation bypasses pkm's manifest ingestion;"
    echo "  pkm files/verify/remove will not see the installed files."
    exit 1
fi

# H-007: initialize the manifest. Must come before any record_* call.
igos_helper_init "chrome"

echo "  Downloading Google Chrome..."
wget -q --show-progress -O "$TMPDIR/chrome.deb" "$CHROME_URL"

echo "  Extracting..."
cd "$TMPDIR"
ar x chrome.deb
tar xf data.tar.xz

# Capture the upstream version from .deb control metadata for the
# manifest's version_installed field. Falls back to "unknown" if
# control is missing or malformed.
CHROME_VERSION="unknown"
if [ -f "$TMPDIR/control.tar.xz" ]; then
    mkdir -p "$TMPDIR/control-extracted"
    tar -xf "$TMPDIR/control.tar.xz" -C "$TMPDIR/control-extracted"
    if [ -f "$TMPDIR/control-extracted/control" ]; then
        CHROME_VERSION=$(awk -F': ' '/^Version:/ {print $2; exit}' \
                         "$TMPDIR/control-extracted/control" || echo "unknown")
    fi
fi
igos_helper_set_version "$CHROME_VERSION"

echo "  Installing to /opt/google/chrome/..."
cp -a opt/google /opt/
cp -a usr/share/applications/* /usr/share/applications/ 2>/dev/null || true
cp -a usr/share/icons/* /usr/share/icons/ 2>/dev/null || true
cp -a usr/share/man/* /usr/share/man/ 2>/dev/null || true

# H-007: record every file deposited into /opt/google/chrome (the bulk
# of chrome's install footprint). Walk recursively + record each
# regular file. Symlinks inside /opt/google/chrome are recorded as
# regular files too — pkm-remove's os.remove() unlinks the symlink
# itself (POSIX unlink semantics) which is the desired behavior.
while IFS= read -r f; do
    igos_helper_record_file "$f"
done < <(find /opt/google/chrome -type f -o -type l 2>/dev/null)

# Record .desktop launchers + icons + man pages that were copied
# system-wide (best-effort; the cp -a above may have skipped some
# subtrees if the .deb didn't include them).
for subtree in /usr/share/applications/google-chrome*.desktop \
               /usr/share/man/man*/google-chrome*; do
    for f in $subtree; do
        if [ -f "$f" ]; then
            igos_helper_record_file "$f"
        fi
    done
done

# Create the /usr/bin/google-chrome symlink + record it.
ln -sf /opt/google/chrome/google-chrome /usr/bin/google-chrome
igos_helper_record_symlink /usr/bin/google-chrome /opt/google/chrome/google-chrome

# Record glibc as runtime dependency (chrome is dynamically linked
# against libc); other shared-library deps come along transitively.
igos_helper_record_dep glibc

# Update icon cache — descriptive only in v1.0 (per H-007 design Q3);
# pkm logs the action to operation history but does not replay it on
# remove.
gtk-update-icon-cache /usr/share/icons/hicolor 2>/dev/null || true
igos_helper_record_post_install_action \
    "gtk-update-icon-cache /usr/share/icons/hicolor"

# H-007: finalize the manifest. Atomic mv ensures pkm sees either the
# complete manifest or nothing at all — never a half-finished
# intermediate state.
igos_helper_commit

echo ""
echo "  Google Chrome installed successfully!"
echo "  Run: google-chrome"
echo ""
HELPEREOF
    chmod 755 "${DESTDIR}/usr/bin/igos-install-chrome"
}
