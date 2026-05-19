#!/bin/bash
# ffmpeg-nonfree-helper 1.0 — Opt-in installer for ffmpeg with FDK-AAC
# InterGenOS extra tier
#
# The default InterGenOS ffmpeg ships under a redistributable license
# (no --enable-nonfree, no FDK-AAC linkage). Users who want the
# patent-encumbered nonfree variant of ffmpeg install this helper.
# It rebuilds ffmpeg from source with --enable-nonfree --enable-libfdk-aac
# and installs to /opt/ffmpeg-nonfree/ so the system ffmpeg remains
# untouched.
#
# Patent posture: see docs/legal/PATENTS.md (P-003 / P-015 audit findings).
# EULA: see docs/legal/payload-licenses.md (LicenseRef-FDK-AAC) and the
# in-helper acceptance prompt below.

configure() { set -e; :; }
build()     { set -e; :; }

do_install() {
    set -e
    mkdir -pv "${DESTDIR}/usr/bin"
    cat > "${DESTDIR}/usr/bin/igos-install-ffmpeg-nonfree" << 'HELPEREOF'
#!/bin/bash
# InterGenOS ffmpeg-nonfree Installer
#
# Rebuilds ffmpeg with --enable-nonfree --enable-libfdk-aac (FDK-AAC
# encoder) and installs to /opt/ffmpeg-nonfree/. The system
# /usr/bin/ffmpeg is NOT touched; this installer adds
# /usr/local/bin/ffmpeg-nonfree as a wrapper to /opt/ffmpeg-nonfree/bin/ffmpeg.

set -e

# H-007: source the helper-lib API for footprint tracking.
source /usr/share/igos/helpers/helper-lib.sh

ACCEPTANCE_DIR="/var/lib/intergen/legal"
ACCEPTANCE_FILE="$ACCEPTANCE_DIR/ffmpeg-nonfree-helper-1.0-accepted.json"
FFMPEG_VERSION="8.0.1"
FFMPEG_URL="https://ffmpeg.org/releases/ffmpeg-${FFMPEG_VERSION}.tar.xz"
FFMPEG_SHA256="05ee0b03119b45c0bdb4df654b96802e909e0a752f72e4fe3794f487229e5a41"
PREFIX="/opt/ffmpeg-nonfree"

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: Run via 'sudo pkm install-helper ffmpeg-nonfree' instead."
    echo "Direct invocation bypasses pkm's manifest ingestion;"
    echo "pkm files/verify/remove will not see the installed files."
    exit 1
fi

cat <<'BANNER'

  InterGenOS ffmpeg-nonfree Installer
  ====================================

  This helper builds and installs a variant of ffmpeg with PATENT-
  ENCUMBERED codecs that the default InterGenOS ffmpeg deliberately
  does NOT include:

    --enable-nonfree     (license-incompatible-with-redistribution mode)
    --enable-libfdk-aac  (Fraunhofer FDK AAC encoder/decoder)

  PATENT EXPOSURE
  ---------------
  FDK-AAC is patent-encumbered. The Fraunhofer FDK AAC license
  permits source and binary REDISTRIBUTION but does NOT grant patent
  licenses; users and redistributors in patent-enforcing jurisdictions
  must obtain those separately if their use case requires.

  The ffmpeg --enable-nonfree mode produces a binary that is NOT
  redistributable per FFmpeg's own documentation. Building this on
  your machine FOR YOUR OWN USE is generally low-risk personally;
  REDISTRIBUTING the resulting binary triggers the patent and
  license issues.

  WHAT THIS INSTALLER DOES
  ------------------------
  - Downloads ffmpeg-${FFMPEG_VERSION} from ffmpeg.org (sha256 verified)
  - Builds it with --enable-nonfree --enable-libfdk-aac
  - Installs to /opt/ffmpeg-nonfree/ (system /usr/bin/ffmpeg is untouched)
  - Installs /usr/local/bin/ffmpeg-nonfree as wrapper to the new binary
  - Records your acceptance at /var/lib/intergen/legal/ for audit

  Estimated time: 20-40 minutes on modern hardware.
  Estimated disk: ~500 MB build dir + ~150 MB install.

  REFERENCES
  ----------
  - https://www.ffmpeg.org/legal.html
  - https://github.com/mstorsjo/fdk-aac/blob/master/NOTICE
  - InterGenOS docs/legal/PATENTS.md
  - InterGenOS docs/legal/payload-licenses.md

BANNER

if [ -f "$ACCEPTANCE_FILE" ]; then
    echo "Acceptance already recorded at $ACCEPTANCE_FILE"
    echo "Proceeding to build."
else
    echo ""
    echo "  Do you accept the patent posture above and authorize"
    echo "  building ffmpeg-nonfree on this machine for your own use?"
    echo "  Type 'I ACCEPT' (exact match, capitals) to proceed:"
    echo ""
    read -r REPLY
    if [ "$REPLY" != "I ACCEPT" ]; then
        echo "Acceptance not given. Exiting."
        exit 1
    fi
    mkdir -p "$ACCEPTANCE_DIR"
    cat > "$ACCEPTANCE_FILE" <<JSON
{
  "helper": "ffmpeg-nonfree-helper",
  "version": "1.0",
  "payload_license": "GPL-3.0-or-later AND FDK-AAC",
  "accepted_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "user": "$(logname 2>/dev/null || echo unknown)",
  "ffmpeg_version": "${FFMPEG_VERSION}",
  "ffmpeg_url_sha256": "${FFMPEG_SHA256}"
}
JSON
    chmod 644 "$ACCEPTANCE_FILE"
    echo "Acceptance recorded at $ACCEPTANCE_FILE"
fi

# H-007: initialize the manifest now that root + acceptance are confirmed.
# Must come before any record_* call.
igos_helper_init "ffmpeg-nonfree"

# Record the acceptance artifact so pkm files / verify / remove track
# the user-given patent-posture consent record alongside the binaries.
igos_helper_record_file "$ACCEPTANCE_FILE"
igos_helper_record_post_install_action \
    "User accepted FDK-AAC patent posture (acceptance artifact at $ACCEPTANCE_FILE)"

TMPDIR=$(mktemp -d)
# BLOCKING-D fix (2026-05-19): register TMPDIR cleanup via the
# helper-lib's IGOS_HELPER_USER_CLEANUP env var instead of `trap EXIT`.
# Installing a native trap would collide with the one igos_helper_init
# installs for partial-manifest sidecar emission (bash trap-replace
# semantics; no native composition).
IGOS_HELPER_USER_CLEANUP="rm -rf $TMPDIR"

echo ""
echo "Downloading ffmpeg-${FFMPEG_VERSION}..."
cd "$TMPDIR"
wget -q --show-progress -O ffmpeg.tar.xz "$FFMPEG_URL"
echo "${FFMPEG_SHA256}  ffmpeg.tar.xz" | sha256sum -c -

echo ""
echo "Extracting and building..."
tar xf ffmpeg.tar.xz
cd "ffmpeg-${FFMPEG_VERSION}"

# H-007: record the upstream ffmpeg version on the manifest.
igos_helper_set_version "$FFMPEG_VERSION"

./configure --prefix="$PREFIX"      \
            --enable-gpl            \
            --enable-version3       \
            --enable-nonfree        \
            --enable-libfdk-aac     \
            --enable-libx264        \
            --enable-libx265        \
            --enable-libmp3lame     \
            --enable-libvpx         \
            --enable-libopus        \
            --enable-openssl        \
            --disable-static        \
            --enable-shared

make -j"$(nproc)"
make install

# H-007: record every file + symlink under the install prefix so
# pkm files / verify / remove see the deposited ffmpeg-nonfree
# footprint. The find walk catches binaries, shared libraries,
# headers, pkg-config files, and man pages — everything make install
# placed under /opt/ffmpeg-nonfree/.
while IFS= read -r f; do
    igos_helper_record_file "$f"
done < <(find /opt/ffmpeg-nonfree -type f -o -type l 2>/dev/null)

# Wrapper at /usr/local/bin/ffmpeg-nonfree
mkdir -p /usr/local/bin
cat > /usr/local/bin/ffmpeg-nonfree <<WRAPPER
#!/bin/bash
exec ${PREFIX}/bin/ffmpeg "\$@"
WRAPPER
chmod 755 /usr/local/bin/ffmpeg-nonfree

# H-007: record the /usr/local/bin/ffmpeg-nonfree wrapper.
igos_helper_record_file /usr/local/bin/ffmpeg-nonfree

# H-007: record runtime deps so pkm reverse-dep tracking warns the
# user before glibc or any codec lib gets removed while
# ffmpeg-nonfree still links against it.
igos_helper_record_dep glibc
igos_helper_record_dep fdk-aac
igos_helper_record_dep x264
igos_helper_record_dep x265
igos_helper_record_dep lame
igos_helper_record_dep libvpx
igos_helper_record_dep opus
igos_helper_record_dep openssl
igos_helper_record_dep zlib

# H-007: finalize the manifest. Atomic mv ensures pkm sees either the
# complete manifest or nothing at all — never a half-finished
# intermediate state.
igos_helper_commit

cat <<DONE

  ffmpeg-nonfree installed.

  System ffmpeg (redistributable): /usr/bin/ffmpeg
  Nonfree variant (your build):    /usr/local/bin/ffmpeg-nonfree
  Library install prefix:          ${PREFIX}

  To use the nonfree variant, invoke as 'ffmpeg-nonfree' explicitly.

DONE
HELPEREOF
    chmod 755 "${DESTDIR}/usr/bin/igos-install-ffmpeg-nonfree"
}
