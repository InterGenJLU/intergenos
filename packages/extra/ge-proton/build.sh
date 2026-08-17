#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# ge-proton 11.1 — GE-Proton (GloriousEggroll) download-helper
# (GE extra-tier wave, D3 decided ADD). Chrome-exemplar shape;
# pin-exact trust posture + system-wide compat-tools design per the
# package.yml notes and the research doc grounding this landing.

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
    mkdir -pv "${DESTDIR}/usr/bin"

    cat > "${DESTDIR}/usr/bin/igos-install-ge-proton" << 'HELPEREOF'
#!/bin/bash
# InterGenOS GE-Proton Installer
#
# Downloads GE-Proton from the pinned GloriousEggroll GitHub release,
# verifies it against the RECIPE-PINNED sha512 (the reviewed trust
# anchor — upstream publishes no GPG signature; its sha512 sidecar is
# same-channel corroboration only), installs it system-wide, and wires
# Steam's STEAM_EXTRA_COMPAT_TOOLS_PATHS so every user's Steam sees it.

set -e

source /usr/share/igos/helpers/helper-lib.sh

# THE PIN (load-bearing; a version bump is a recipe bump):
GE_TAG="GE-Proton11-1"
GE_TARBALL="${GE_TAG}.tar.gz"
GE_URL="https://github.com/GloriousEggroll/proton-ge-custom/releases/download/${GE_TAG}/${GE_TARBALL}"
GE_SIDECAR_URL="${GE_URL%.tar.gz}.sha512sum"
GE_SHA512="d5792f4ac81d3832f5fe40467090c67d561b780c6a4236e76f8b59cb1d4ca25c82df91018e79d156bb267a67224a41f0d621a1e6cbbeec79040cc60275dc9e5a"  # the pinned release checksum (sha512)

# /opt, NOT /usr: pressure-vessel RESERVES /usr (it replaces it with the
# container runtime's own), so a compat tool under /usr LISTS fine (its .vdf
# is read outside the container) but can NEVER execute — the exec crosses into
# the container where the /usr path does not exist (pv-adverb "No such file").
# /opt is a SHAREABLE root, so the tool both lists AND runs. Steam visibility
# is handled by the /usr/bin/steam wrapper exporting STEAM_EXTRA_COMPAT_TOOLS_PATHS
# (session-independent, no relogin), so this helper no longer writes environment.d.
COMPAT_ROOT="/opt/igos/compat-tools"
ACCEPTANCE_DIR="/var/lib/intergen/legal"
ACCEPTANCE_FILE="$ACCEPTANCE_DIR/ge-proton-${GE_TAG}-accepted.json"

TMPDIR=$(mktemp -d)
IGOS_HELPER_USER_CLEANUP="rm -rf $TMPDIR"

echo ""
echo "  InterGenOS GE-Proton Installer"
echo "  =============================="
echo ""
echo "  GE-Proton bundles Valve's Proton (see LICENSE.proton in the"
echo "  installed tree) plus components under their own licenses"
echo "  (Wine LGPL, DXVK zlib, and others — each carries its LICENSE)."
echo ""

if [ "$(id -u)" -ne 0 ]; then
    echo "  ERROR: Run via 'sudo pkm install ge-proton' instead."
    echo "  Installing this way does not record the files with pkm;"
    echo "  pkm files/verify/remove will not see the installed files."
    exit 1
fi

# Acceptance gate (the ffmpeg-nonfree/chrome K21.C pattern): the record
# is NOT manifest-tracked so reinstall skips the re-prompt; the consent
# event is logged via the post_install_action below.
if [ -f "$ACCEPTANCE_FILE" ]; then
    echo "  Acceptance already recorded at $ACCEPTANCE_FILE"
    echo "  Proceeding to install."
else
    echo ""
    echo "  Do you accept the licenses above (Valve's Proton license and"
    echo "  the bundled components' licenses) and authorize installing"
    echo "  GE-Proton on this machine for your own use?"
    echo "  Type 'I ACCEPT' (exact match, capitals) to proceed:"
    echo ""
    read -r REPLY
    if [ "$REPLY" != "I ACCEPT" ]; then
        echo "  Acceptance not given. Exiting."
        exit 10
    fi
    mkdir -p "$ACCEPTANCE_DIR"
    cat > "$ACCEPTANCE_FILE" <<JSON
{
  "helper": "ge-proton",
  "version": "${GE_TAG}",
  "payload_license": "LicenseRef-GE-Proton-Mixed",
  "accepted_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "user": "$(logname 2>/dev/null || echo unknown)"
}
JSON
    chmod 644 "$ACCEPTANCE_FILE"
    echo "  Acceptance recorded at $ACCEPTANCE_FILE"
fi

igos_helper_init "ge-proton"
igos_helper_record_post_install_action \
    "User accepted the GE-Proton payload licenses (acceptance artifact at $ACCEPTANCE_FILE)"

echo "  Downloading ${GE_TARBALL} (pinned release ${GE_TAG})..."
wget -q --show-progress -O "$TMPDIR/${GE_TARBALL}" "$GE_URL"

# THE RECIPE PIN IS THE GATE: refuse on any mismatch. Upstream's
# sidecar is fetched as corroboration — a sidecar/pin disagreement is
# ALSO a refuse (it means the release changed after our review).
echo "  Verifying against the recipe-pinned sha512..."
GOT=$(sha512sum "$TMPDIR/${GE_TARBALL}" | cut -d' ' -f1)
if [ "$GOT" != "$GE_SHA512" ]; then
    echo ""
    echo "  ERROR: sha512 MISMATCH for ${GE_TARBALL}."
    echo "  expected (recipe pin): $GE_SHA512"
    echo "  got:                   $GOT"
    echo "  The downloaded file is not the one this version of the package was"
    echo "  built to install, so it was not installed and nothing on this machine"
    echo "  was changed. This usually means the release was replaced after this"
    echo "  package was published. There is nothing to fix on this machine —"
    echo "  the package needs updating. Please report it."
    exit 1
fi
if wget -q -O "$TMPDIR/sidecar.sha512sum" "$GE_SIDECAR_URL"; then
    SIDECAR=$(cut -d' ' -f1 < "$TMPDIR/sidecar.sha512sum")
    if [ -n "$SIDECAR" ] && [ "$SIDECAR" != "$GE_SHA512" ]; then
        echo "  ERROR: the checksum published by the release's authors no longer"
        echo "  matches the one this package was built against, which means the"
        echo "  release was changed after this package was published. Nothing was"
        echo "  installed and nothing on this machine was changed. There is nothing"
        echo "  to fix here — the package needs updating. Please report it."
        exit 1
    fi
else
    echo "  NOTE: the upstream checksum file could not be fetched. The"
    echo "  download was already verified against the checksum shipped"
    echo "  with this package."
fi

echo "  Installing to ${COMPAT_ROOT}/${GE_TAG}/..."
# Never place-then-rollback: a rollback keyed on the ASSUMED tag
# dir-name would orphan a payload whose top directory differs from the
# tag. Instead extract into a staging dir ON the destination
# filesystem (same-fs move; /tmp may be tmpfs and this payload is
# large), assert the layout THERE, and only then move it into the live
# compat root — the live tree never holds a partial or unexpected
# payload, and the EXIT-trap cleanup removes whatever the archive
# ACTUALLY extracted, not what we assumed it would.
mkdir -p "$COMPAT_ROOT"
# Stage in a hidden SIBLING of the compat root (same filesystem, so the
# move stays cheap) rather than under it — Steam scans the compat root,
# and nothing transient should ever sit under a scanned path, even
# dot-prefixed and manifest-less (WC re-cert characterization, closed).
STAGING_DIR=$(mktemp -d "$(dirname "$COMPAT_ROOT")/.ge-proton-staging-XXXXXX")
IGOS_HELPER_USER_CLEANUP="rm -rf $TMPDIR $STAGING_DIR"
tar xf "$TMPDIR/${GE_TARBALL}" -C "$STAGING_DIR"
TOP_ENTRIES=$(find "$STAGING_DIR" -mindepth 1 -maxdepth 1 | wc -l)
TOP_DIR=$(find "$STAGING_DIR" -mindepth 1 -maxdepth 1 -type d)
if [ "$TOP_ENTRIES" -ne 1 ] || [ -z "$TOP_DIR" ] || [ ! -f "$TOP_DIR/proton" ]; then
    echo "  ERROR: the downloaded release does not contain the files this"
    echo "  package was built to install, so it was not installed. Nothing was"
    echo "  placed in ${COMPAT_ROOT} and nothing on this machine was changed."
    echo "  There is nothing to fix here — the package needs updating for the"
    echo "  new release layout. Please report it."
    exit 1
fi
if [ -e "${COMPAT_ROOT}/${GE_TAG}" ]; then
    echo "  NOTE: replacing existing ${COMPAT_ROOT}/${GE_TAG} with the"
    echo "  just-verified payload."
    rm -rf "${COMPAT_ROOT:?}/${GE_TAG}"
fi
mv "$TOP_DIR" "${COMPAT_ROOT}/${GE_TAG}"

# Steam discovers this payload via STEAM_EXTRA_COMPAT_TOOLS_PATHS, which the
# /usr/bin/steam launch wrapper exports to /opt/igos/compat-tools at every
# launch (session-independent — visible with no relogin, for every user).
# The wrapper is the single launch chokepoint on this system, so no
# environment.d snippet is written here (that only loads at a login-session
# start, the gap a first-time installer hit).

# The payload mixes 64-bit ELF (wine/proton binaries) and PE DLLs —
# declare the mixed width to the recorder's one-helper-one-width audit.
export IGOS_HELPER_ELF_CLASS=mixed

echo "  Recording the installed files with pkm..."
# Null-delimited enumeration: a newline-delimited read would let a
# payload path with an embedded newline escape the footprint record
# (an untracked deposited file). Unreachable on the reviewed pin, but
# the record loop must not depend on payload filename hygiene.
while IFS= read -r -d '' f; do
    igos_helper_record_file "$f"
done < <(find "${COMPAT_ROOT}/${GE_TAG}" \( -type f -o -type l \) -print0 2>/dev/null)

igos_helper_set_version "${GE_TAG}"
igos_helper_commit

echo ""
echo "  GE-Proton installed system-wide under ${COMPAT_ROOT}."
echo ""
echo "  Steam only looks for compatibility tools while it is starting, so if"
echo "  Steam is running it will not see ${GE_TAG} yet. Exit it fully —"
echo "  Steam menu > Exit, not the window close button — and start it again."
echo ""
echo "  Then choose ${GE_TAG} either way:"
echo "    for every game:  Steam > Settings > Compatibility, turn on Steam"
echo "                     Play for all other titles, and pick ${GE_TAG}"
echo "    for one game:    that game's Properties > Compatibility"
echo ""
HELPEREOF
    chmod 755 "${DESTDIR}/usr/bin/igos-install-ge-proton"
}
