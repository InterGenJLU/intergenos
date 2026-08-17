#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# zoom 1.0 — Download and install Zoom Workplace
# InterGenOS extra tier
#
# Zoom Workplace is proprietary software. This helper downloads it from
# Zoom's official distribution channel. The user accepts Zoom's license
# terms by running this installer.

configure() { :; }
build() { :; }

do_install() {
    set -e
    mkdir -pv "${DESTDIR}/usr/bin"

    # Pinned Zoom package-signing key (armored .asc in repo for
    # review-ability), dearmored to a binary keyring at
    # /usr/share/igos/helpers/keyrings/zoom-keyring.gpg. Trust-anchor
    # introduction methodology: the PGP public key block was obtained
    # from Zoom's primary publication at
    # https://zoom.us/linux/download/pubkey (the package-verification
    # key Zoom documents at https://support.zoom.com/hc/en/article?id=
    # zm_kb&sysparm_article=KB0063726). The key's User ID is "Zoom
    # Communications, Inc. <CryptoOpsCodeSignProd@zoom.us>".
    #
    # VERIFICATION POSTURE REWRITTEN 2026-07-02 (the §5 rider): Zoom
    # RETIRED its apt repository — https://zoom.us/linux/download/prod
    # now 404s for the whole dists tree (GET-confirmed 2026-06-29 and
    # re-confirmed 2026-07-02), so the signed InRelease->Packages->.deb
    # chain the K21.F design relied on no longer exists upstream, and
    # the only third-party apt mirror is unofficial (supply-chain
    # non-starter). The helper now uses the PIN-EXACT posture
    # (ge-proton precedent) with the vendor signature kept as a second
    # gate: (1) the recipe pins the exact version + whole-file sha256,
    # verified byte-exact at authoring-time ingest — the load-bearing
    # strong binding; (2) Zoom's .deb carries an embedded debsigs
    # `_gpgbuilder` member signed by the key above — the helper gpgv's
    # it against this pinned keyring (fingerprint-pinned) and
    # cross-checks the signed member digests, proving the pinned
    # artifact is Zoom-published. NOTE: Zoom's builder signature uses a
    # SHA1 digest (the same weak-digest class as Valve's repo,
    # steam-for-linux#12050) — accepted HERE ONLY because the sha256
    # pin in (1) already binds every byte, so the weak signature digest
    # cannot weaken content integrity; it adds authenticity on top.
    # A version update = re-run the ingest (download, sha256, gpgv,
    # member cross-check), bump the pins in ONE commit.
    install -dm755 "${DESTDIR}/usr/share/igos/helpers/keyrings"
    gpg --dearmor < \
        "${IGOS_SOURCE_ROOT:-/mnt/intergenos}/packages/extra/zoom/assets/zoom-keyring.asc" \
        > "${DESTDIR}/usr/share/igos/helpers/keyrings/zoom-keyring.gpg"
    chmod 644 "${DESTDIR}/usr/share/igos/helpers/keyrings/zoom-keyring.gpg"

    cat > "${DESTDIR}/usr/bin/igos-install-zoom" << 'HELPEREOF'
#!/bin/bash
# InterGenOS Zoom Workplace Installer
#
# Downloads and installs Zoom Workplace from Zoom's official source.
# License: https://explore.zoom.us/en/terms/
#
# H-007 Phase B migration: records the install footprint via the
# /usr/share/igos/helpers/helper-lib.sh API.

set -e

source /usr/share/igos/helpers/helper-lib.sh

ACCEPTANCE_DIR="/var/lib/intergen/legal"
ACCEPTANCE_FILE="$ACCEPTANCE_DIR/zoom-1.0-accepted.json"

# PIN-EXACT posture (Zoom retired its apt repo — see the build-time
# comment in this package's build.sh): the recipe pins the exact
# version + whole-file sha256 (verified byte-exact at authoring-time
# ingest, 2026-07-02), and the .deb's embedded debsigs `_gpgbuilder`
# signature is verified against Zoom's pinned key as a second gate.
# Do NOT add fallback repositories, "latest" URLs, or third-party
# mirrors without a security review: alternate download sources are a
# supply-chain vector, and the unofficial apt mirror that exists for
# Zoom is exactly that.
ZOOM_VERSION="7.1.0.3715"
ZOOM_DEB_SHA256="95ba9b55badc3af45ea562d45a7bf4d67a494fb6b319dece1377e9fad3aafaf1"
ZOOM_DEB_URL="https://cdn.zoom.us/prod/${ZOOM_VERSION}/zoom_amd64.deb"
# Zoom's package-signing key fingerprint (CryptoOpsCodeSignProd@zoom.us)
ZOOM_KEY_FPR="84C365D6CC9A4886CA926BCC4F2197399706AC24"
ZOOM_KEYRING="/usr/share/igos/helpers/keyrings/zoom-keyring.gpg"
TMPDIR=$(mktemp -d)
# BLOCKING-D fix (2026-05-19): register TMPDIR cleanup via the
# helper-lib's IGOS_HELPER_USER_CLEANUP env var instead of `trap EXIT`.
# Installing a native trap would collide with the one igos_helper_init
# installs for partial-manifest sidecar emission (bash trap-replace
# semantics; no native composition).
IGOS_HELPER_USER_CLEANUP="rm -rf $TMPDIR"

echo ""
echo "  InterGenOS Zoom Workplace Installer"
echo "  ====================================="
echo ""
echo "  Zoom Workplace is proprietary software."
echo "  License: https://explore.zoom.us/en/terms/"
echo ""

if [ "$(id -u)" -ne 0 ]; then
    echo "  ERROR: Run via 'sudo pkm install zoom' instead."
    echo "  Installing this way does not record the files with pkm;"
    echo "  pkm files/verify/remove will not see the installed files."
    exit 1
fi

# EULA acceptance gate. The acceptance record at /var/lib/intergen/legal/
# is intentionally NOT manifest-tracked: pkm remove leaves it in place
# so a subsequent reinstall reads existing acceptance and skips the
# re-prompt (Row F decision 2026-05-19; ffmpeg-nonfree canonical
# K21.C pattern). The record IS captured in pkm's operation log as
# transparency content via the post_install_action below.
if [ -f "$ACCEPTANCE_FILE" ]; then
    echo "  Acceptance already recorded at $ACCEPTANCE_FILE"
    echo "  Proceeding to install."
else
    echo ""
    echo "  Do you accept Zoom's license terms above and authorize"
    echo "  installing Zoom Workplace on this machine for your own use?"
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
  "helper": "zoom",
  "version": "1.0",
  "payload_license": "LicenseRef-Zoom-Terms-of-Service",
  "accepted_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "user": "$(logname 2>/dev/null || echo unknown)"
}
JSON
    chmod 644 "$ACCEPTANCE_FILE"
    echo "  Acceptance recorded at $ACCEPTANCE_FILE"
fi

igos_helper_init "zoom"

# K21.C: capture the consent event in pkm's operation log as
# transparency content. The acceptance JSON itself is intentionally
# NOT manifest-tracked (see EULA acceptance gate above).
igos_helper_record_post_install_action \
    "User accepted Zoom license terms (acceptance artifact at $ACCEPTANCE_FILE)"

igos_helper_set_version "$ZOOM_VERSION"

echo "  Downloading Zoom Workplace ${ZOOM_VERSION} (pinned)..."
wget -q --show-progress -O "$TMPDIR/zoom.deb" "$ZOOM_DEB_URL"

# GATE 1 (load-bearing): whole-file sha256 against the recipe pin.
# This is the strong byte-exact binding — verified at authoring-time
# ingest and re-verified here on every install.
echo "  Verifying the pinned sha256..."
GOT_SHA=$(sha256sum "$TMPDIR/zoom.deb" | cut -d' ' -f1)
if [ "$GOT_SHA" != "$ZOOM_DEB_SHA256" ]; then
    echo ""
    echo "  ERROR: zoom_amd64.deb sha256 MISMATCH."
    echo "    expected (recipe pin): $ZOOM_DEB_SHA256"
    echo "    actual:                $GOT_SHA"
    echo "  Refusing to install. Either the download corrupted, the"
    echo "  artifact was tampered with, or Zoom re-published ${ZOOM_VERSION}"
    echo "  with different bytes — every case needs human review."
    echo "  Do NOT extract the .deb manually."
    exit 1
fi

echo "  Extracting..."
cd "$TMPDIR"
ar x zoom.deb
if [ ! -f _gpgbuilder ]; then
    echo "  ERROR: the .deb carries no embedded _gpgbuilder signature."
    echo "  Zoom has signed its Linux .debs with debsigs; its absence in a"
    echo "  pin-matching artifact is unexpected — refusing to install."
    exit 1
fi

# GATE 2 (authenticity): Zoom's embedded debsigs signature, verified
# against the pinned keyring and pinned to Zoom's key fingerprint,
# then the SIGNED member digests cross-checked against the actual
# members. Zoom signs with a SHA1 digest (weak-digest class) — the
# default gpgv accepts it, and that acceptance is sanctioned HERE ONLY
# because GATE 1's sha256 pin already binds every byte; this gate adds
# vendor provenance on top. LC_ALL=C because the output is parsed.
echo "  Verifying Zoom's embedded package signature..."
if ! LC_ALL=C gpgv --keyring "$ZOOM_KEYRING" _gpgbuilder 2>"$TMPDIR/gpgv.err"; then
    echo "  ERROR: the embedded _gpgbuilder signature did not verify:"
    cat "$TMPDIR/gpgv.err"
    echo "  Refusing to install."
    exit 1
fi
FPR_NOSPACE="${ZOOM_KEY_FPR// /}"
if ! grep -qi 'Good signature' "$TMPDIR/gpgv.err" \
   || ! tr -d ' ' < "$TMPDIR/gpgv.err" | grep -qi "$FPR_NOSPACE"; then
    echo "  ERROR: the embedded signature did not resolve to the pinned"
    echo "  Zoom key ${ZOOM_KEY_FPR}:"
    cat "$TMPDIR/gpgv.err"
    echo "  Refusing to install."
    exit 1
fi
# Member digest cross-check: the signed Files lines carry
# "md5 sha1 size name" per member — recompute sha1 + size for every
# member named and refuse on any mismatch, so the Good signature above
# provably covers THESE bytes, not just any document riding along.
while read -r _md5 want_sha1 want_size member; do
    [ -n "$member" ] || continue
    if [ ! -f "$member" ]; then
        echo "  ERROR: signed manifest names '$member' but the .deb lacks it. Refusing."
        exit 1
    fi
    got_sha1=$(sha1sum "$member" | cut -d' ' -f1)
    got_size=$(stat -c%s "$member")
    if [ "$got_sha1" != "$want_sha1" ] || [ "$got_size" != "$want_size" ]; then
        echo "  ERROR: member '$member' does not match Zoom's signed digest manifest. Refusing."
        exit 1
    fi
done < <(awk '/^\t/{print $1, $2, $3, $4}' _gpgbuilder)
echo "  Signature chain verified: sha256 pin + Zoom builder signature + member digests."

tar xf data.tar.xz 2>/dev/null || tar xf data.tar.gz 2>/dev/null || tar --zstd -xf data.tar.zst 2>/dev/null

echo "  Installing to /opt/zoom/..."
# Zoom's .deb installs the client under /opt/zoom and ships its .desktop
# launcher + icon under usr/share.
cp -a opt/zoom /opt/
cp -a usr/share/applications/* /usr/share/applications/ 2>/dev/null || true
cp -a usr/share/icons/* /usr/share/icons/ 2>/dev/null || true
cp -a usr/share/pixmaps/* /usr/share/pixmaps/ 2>/dev/null || true

# The .deb's Zoom.desktop uses Exec=/usr/bin/zoom (created by the .deb's
# postinst). We do not run postinst, so create the launcher symlink
# ourselves — otherwise the menu entry's Exec target is dead.

# H-007: record everything deposited under /opt/zoom + the system-wide
# .desktop launcher + icons. Null-delimited enumeration (the §5-riders
# hygiene sweep): the helper-lib chokepoint refuses newline-embedded
# paths loudly, and -print0 removes the delimiter ambiguity entirely.
# Both finds use explicit parens — a bare `-a -o` chain misgroups
# (the old icons loop matched Zoom*-named dirs via that precedence).
while IFS= read -r -d '' f; do
    igos_helper_record_file "$f"
done < <(find /opt/zoom \( -type f -o -type l \) -print0 2>/dev/null)
for f in /usr/share/applications/Zoom*.desktop /usr/share/applications/zoom*.desktop; do
    if [ -f "$f" ]; then
        igos_helper_record_file "$f"
    fi
done
while IFS= read -r -d '' f; do
    igos_helper_record_file "$f"
done < <(find /usr/share/icons /usr/share/pixmaps \( -name 'Zoom*' -o -name 'zoom*' \) -type f -print0 2>/dev/null)

ln -sf /opt/zoom/ZoomLauncher /usr/bin/zoom
igos_helper_record_symlink /usr/bin/zoom /opt/zoom/ZoomLauncher

igos_helper_record_dep glibc

gtk-update-icon-cache /usr/share/icons/hicolor 2>/dev/null || true
igos_helper_record_post_install_action "gtk-update-icon-cache /usr/share/icons/hicolor"

igos_helper_commit

echo ""
echo "  Zoom Workplace installed."
echo "  Run: zoom"
echo ""
HELPEREOF
    chmod 755 "${DESTDIR}/usr/bin/igos-install-zoom"
}
