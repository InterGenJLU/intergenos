#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# steam 1.0.0.85 — Steam client download-helper (GE arc, operator
# decision 2 + RT-4). Ships, at build time: the fail-closed /usr/bin/steam
# launch wrapper, the 32-bit closure manifest, the pinned Valve keyring,
# and the igos-install-steam download helper. The helper fetches +
# signed-Release-verifies + installs Valve's steam-launcher at pkm-install
# time (chrome-exemplar), with the decided SCOPED weak-digest
# posture (helper-lib's 7th-arg pinned-fingerprint path).

# Pinned Valve apt-repo signing key (armored .asc committed for review).
STEAM_KEYRING_ASC_SHA256="82507b1e03fe44f14176477a10e1a995ed685b62be018de1dbfa1d464232bd4e"

BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

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
    install -dm755 "${DESTDIR}/usr/bin"
    install -dm755 "${DESTDIR}/usr/share/igos/helpers/keyrings"
    install -dm755 "${DESTDIR}/usr/share/igos/helpers/steam"

    # --- pinned Valve keyring: sha-check the committed .asc, dearmor ---
    local asc="${IGOS_SOURCE_ROOT:-/mnt/intergenos}/packages/extra/steam/assets/steam-keyring.asc"
    local got
    got=$(sha256sum "$asc" | cut -d' ' -f1)
    if [ "$got" != "$STEAM_KEYRING_ASC_SHA256" ]; then
        echo "FATAL: steam-keyring.asc sha256 mismatch (expected $STEAM_KEYRING_ASC_SHA256, got $got)" >&2
        return 1
    fi
    gpg --dearmor < "$asc" \
        > "${DESTDIR}/usr/share/igos/helpers/keyrings/steam-keyring.gpg"
    chmod 644 "${DESTDIR}/usr/share/igos/helpers/keyrings/steam-keyring.gpg"

    # --- the 32-bit closure manifest (RT-4): curated ldd-anchored +
    # dlopen sonames the Steam client host-side actually loads. The
    # launch wrapper asserts every one resolves in the 32-bit ABI. The
    # runtime deps in package.yml provide these; the install-time ldd is
    # the final arbiter and the wrapper catches any drift. ---
    cat > "${DESTDIR}/usr/share/igos/helpers/steam/closure.json" <<'JSONEOF'
{
  "note": "32-bit host-side shared-library closure for the Steam client. Each soname MUST resolve in the 32-bit (i686) ABI or /usr/bin/steam refuses to launch. Curated from the measured Steam-client demand; the install-time ldd is the final arbiter.",
  "abi": "i386",
  "sonames": [
    "libX11.so.6", "libXext.so.6", "libXinerama.so.1", "libXrandr.so.2",
    "libXrender.so.1", "libXfixes.so.3", "libXss.so.1", "libxcb.so.1",
    "libXau.so.6", "libXdmcp.so.6", "libxkbcommon.so.0",
    "libGL.so.1", "libGLX.so.0", "libEGL.so.1", "libvulkan.so.1",
    "libdrm.so.2", "libpulse.so.0", "libpulse-simple.so.0",
    "libasound.so.2", "libnss3.so", "libnspr4.so", "libudev.so.1",
    "libgpg-error.so.0", "libz.so.1", "libexpat.so.1",
    "libwayland-client.so.0"
  ]
}
JSONEOF
    chmod 644 "${DESTDIR}/usr/share/igos/helpers/steam/closure.json"

    # --- the FAIL-CLOSED launch wrapper (RT-4). Valve's own /usr/bin/steam
    # is a symlink to ../lib/steam/bin_steam.sh; the helper installs the
    # payload under /usr/lib/steam but does NOT install that symlink, so
    # THIS wrapper owns /usr/bin/steam and execs the bootstrap only after
    # the 32-bit closure holds. Valve's own client check merely WARNS on a
    # missing lib32; this refuses. ---
    cat > "${DESTDIR}/usr/bin/steam" <<'WRAPEOF'
#!/bin/bash
# InterGenOS Steam launch wrapper (RT-4).
# Checks that the 32-bit shared libraries Steam needs can actually be
# resolved in the i686 ABI before exec'ing Valve's bootstrap, and refuses
# to launch if they cannot.
#
# WHERE THE REFUSAL GOES. Writing it to stderr alone was not enough: a
# launch from the desktop has no terminal attached and the message went
# nowhere, so a refusal that worked correctly looked to the user like
# nothing happening at all. It hit twice, on 2026-08-05, when a package
# manager cache-rebuild gap left 26 sonames unresolvable. The refusal now
# also goes to the desktop notification service when one is reachable.
# notify-send is NOT a dependency of this package: if it is absent, or
# there is no session bus to talk to, the stderr message is still written
# and the exit status is unchanged.
set -u

MANIFEST="/usr/share/igos/helpers/steam/closure.json"
BOOTSTRAP="/usr/lib/steam/bin_steam.sh"

# refuse <summary> <line>...  — every line to stderr, the summary plus the
# first line to the desktop if that is possible. Never fails the script.
refuse() {
    local summary="$1"; shift
    local first="${1:-}"
    local line
    for line in "$@"; do
        printf 'steam: %s\n' "$line" >&2
    done
    if command -v notify-send >/dev/null 2>&1 \
       && { [ -n "${WAYLAND_DISPLAY:-}" ] || [ -n "${DISPLAY:-}" ]; }; then
        notify-send --app-name=Steam --urgency=critical \
            "$summary" "$first" >/dev/null 2>&1 || true
    fi
}

if [ ! -f "$MANIFEST" ]; then
    refuse "Steam cannot start" \
        "the list of required 32-bit libraries is missing at $MANIFEST." \
        "Reinstall Steam: sudo pkm install steam"
    exit 1
fi
if [ ! -x "$BOOTSTRAP" ] && [ ! -f "$BOOTSTRAP" ]; then
    refuse "Steam is not installed" \
        "the Steam client is not installed yet." \
        "Install it: sudo pkm install steam"
    exit 1
fi

# ldconfig's cache tags a 32-bit lib "(libc6)" and a 64-bit one
# "(libc6,x86-64)". A soname that resolves ONLY as x86-64 is a 32-bit
# gap — exactly what Steam needs and what a naive presence check misses.
# NOTE: `ldconfig -p` lines are TAB-indented, so the name must be
# matched as awk field 1 (a leading-space grep never matches); the tag
# can carry extras ("(libc6, OS ABI: ...)"), so accept any libc6 tag
# NOT marked x86-64 rather than demanding the bare "(libc6)" literal.
# Parse the manifest FIRST and refuse on an empty/unparseable soname
# list — feeding the loop from a silenced python3 would otherwise turn
# a parse failure into a zero-iteration pass (a masked no-op check).
sonames=$(python3 -c "import json; [print(s) for s in json.load(open('$MANIFEST'))['sonames']]" 2>/dev/null)
if [ -z "$sonames" ]; then
    refuse "Steam cannot start" \
        "could not read the list of required 32-bit libraries at $MANIFEST," \
        "or it is empty. Reinstall Steam: sudo pkm install steam"
    exit 1
fi
missing=()
snapshot=$(ldconfig -p)
while IFS= read -r soname; do
    [ -n "$soname" ] || continue
    if ! printf '%s\n' "$snapshot" | awk -v s="$soname" '$1 == s' \
         | grep -v 'x86-64' | grep -q 'libc6'; then
        missing+=("$soname")
    fi
done <<< "$sonames"

if [ "${#missing[@]}" -ne 0 ]; then
    refuse "Steam cannot start" \
        "${#missing[@]} of the 32-bit libraries Steam needs cannot be found." \
        "Reinstall them: sudo pkm install steam" \
        "Missing:"
    for m in "${missing[@]}"; do echo "  - $m" >&2; done
    exit 1
fi

# System-wide Steam compatibility tools (e.g. the ge-proton helper payload
# under /opt/igos/compat-tools). Exported HERE, at the single launch
# chokepoint, rather than via environment.d so a fresh helper install is
# visible to Steam with NO relogin and for every user — an environment.d
# snippet only loads when a login session begins, which is exactly the gap a
# first-time installer hit (Steam already running, snippet unseen). The
# payload lives under /opt (a pressure-vessel-SHAREABLE root); a tool under
# /usr LISTS but can never EXECUTE because /usr is reserved and replaced by
# the container framework (the pv-adverb "No such file" death). Append so a
# user's own value is preserved.
IGOS_COMPAT_TOOLS=/opt/igos/compat-tools
export STEAM_EXTRA_COMPAT_TOOLS_PATHS="${STEAM_EXTRA_COMPAT_TOOLS_PATHS:+$STEAM_EXTRA_COMPAT_TOOLS_PATHS:}$IGOS_COMPAT_TOOLS"

# Game window density. A Proton prefix is created at the Wine default of 96
# dots per inch, so on a display scaled above 1.0 everything Wine draws inside
# a windowed game -- its title bar and its window buttons -- renders at a
# fraction of the size the rest of the desktop uses. igos-game-window-density
# writes a protonfixes hook into this user's configuration; GE-Proton then
# runs that hook for every game and sets the density from inside the prefix,
# which is the only ordering that does not race Wine's own registry flush.
#
# This runs HERE because this wrapper is the one place that executes as the
# person launching Steam, inside their graphical session, at every launch --
# the two conditions for reading the live display scale and for writing into
# that person's own configuration. It rewrites nothing when the value is
# already current, and never touches a hook file the user wrote themselves.
#
# A failure is reported and the launch continues: the density is a comfort
# setting, and refusing to start Steam over it would be the wrong trade. It
# is never silent -- the reason is printed.
if command -v igos-game-window-density >/dev/null 2>&1; then
    if ! igos-game-window-density --quiet sync-hook; then
        echo "steam: could not update the game window density hook (see the message above); starting Steam anyway." >&2
    fi
fi

exec "$BOOTSTRAP" "$@"
WRAPEOF
    chmod 755 "${DESTDIR}/usr/bin/steam"

    # --- the game-window-density command ---
    # Kept as a repository file rather than a heredoc so the test suite can
    # import and exercise it directly (the nvidia eula-helper pattern). It is
    # installed without its .py suffix because it is a command a person runs.
    install -m 755 "$BUILD_DIR/assets/igos-game-window-density.py" \
        "${DESTDIR}/usr/bin/igos-game-window-density"

    # --- the download helper (fetch + verify + install Valve's payload) ---
    cat > "${DESTDIR}/usr/bin/igos-install-steam" << 'HELPEREOF'
#!/bin/bash
# InterGenOS Steam client installer.
#
# Downloads Valve's steam-launcher from the SIGNED apt repo
# (repo.steampowered.com, component `steam`, package steam-launcher
# amd64), verifies the signed-Release integrity chain with the SCOPED
# weak-digest posture (operator decision 2), installs the payload under
# /usr/lib/steam, and manifest-tracks the footprint. /usr/bin/steam (the
# fail-closed wrapper) and the closure manifest ship with the package.

set -e

source /usr/share/igos/helpers/helper-lib.sh

ACCEPTANCE_DIR="/var/lib/intergen/legal"
ACCEPTANCE_FILE="$ACCEPTANCE_DIR/steam-1.0-accepted.json"

STEAM_APT_BASE="https://repo.steampowered.com/steam"
STEAM_DIST="stable"
STEAM_COMPONENT="steam"        # NOT main
STEAM_PKG_NAME="steam-launcher"
STEAM_KEYRING="/usr/share/igos/helpers/keyrings/steam-keyring.gpg"
# Valve's pinned primary key fingerprint (the historically-SHA1 2012 key
# steam-for-linux#12050 is about). The scoped weak-digest retry accepts a
# weak SIGNATURE digest ONLY when the InRelease resolves Good to THIS key.
STEAM_KEY_FPR="BA1816EF8E75005FCF5E27A1F24AEA9FB05498B7"

TMPDIR=$(mktemp -d)
IGOS_HELPER_USER_CLEANUP="rm -rf $TMPDIR"

echo ""
echo "  InterGenOS Steam Installer"
echo "  =========================="
echo ""
echo "  Steam is proprietary software governed by the Steam Subscriber"
echo "  Agreement: https://store.steampowered.com/subscriber_agreement/"
echo ""

if [ "$(id -u)" -ne 0 ]; then
    echo "  ERROR: Run via 'sudo pkm install steam' instead."
    echo "  Installing this way does not record the files with pkm;"
    echo "  pkm files/verify/remove will not see the installed files."
    exit 1
fi

if [ -f "$ACCEPTANCE_FILE" ]; then
    echo "  Acceptance already recorded at $ACCEPTANCE_FILE"
else
    echo ""
    echo "  Do you accept the Steam Subscriber Agreement above and"
    echo "  authorize installing Steam on this machine for your own use?"
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
  "helper": "steam",
  "version": "1.0",
  "payload_license": "LicenseRef-Valve-SSA",
  "accepted_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "user": "$(logname 2>/dev/null || echo unknown)"
}
JSON
    chmod 644 "$ACCEPTANCE_FILE"
    echo "  Acceptance recorded at $ACCEPTANCE_FILE"
fi

igos_helper_init "steam"
igos_helper_record_post_install_action \
    "User accepted the Steam Subscriber Agreement (acceptance artifact at $ACCEPTANCE_FILE)"

echo "  Finding steam-launcher in Valve's signed apt metadata (component ${STEAM_COMPONENT})..."
LATEST=$(igos_helper_find_latest_deb_in_packages "$STEAM_PKG_NAME" "$STEAM_APT_BASE" "$STEAM_DIST" "$STEAM_COMPONENT")
if [ -z "$LATEST" ]; then
    echo "  ERROR: Steam could not be found in Valve's own package listing, so"
    echo "  there was nothing to download. Nothing was installed and nothing on"
    echo "  this machine was changed. This is usually a temporary problem at"
    echo "  Valve's end or with the network — try again later. If it keeps"
    echo "  happening, please report it; the listing that was checked is"
    echo "  ${STEAM_APT_BASE}/dists/${STEAM_DIST}/${STEAM_COMPONENT}/"
    exit 1
fi
DEB_NAME=$(echo "$LATEST" | cut -d'|' -f1)
STEAM_VERSION=$(echo "$LATEST" | cut -d'|' -f2)
POOL_PATH=$(echo "$LATEST" | cut -d'|' -f3)
igos_helper_set_version "${STEAM_VERSION:-unknown}"

echo "  Downloading ${DEB_NAME}..."
wget -q --show-progress -O "$TMPDIR/steam.deb" "${STEAM_APT_BASE}/${POOL_PATH}"

# The SCOPED weak-digest posture (operator decision 2): STRICT-first;
# only Valve's specific SHA1 weak-digest rejection triggers a permissive
# retry, pinned to Valve's fingerprint, with the exception logged loudly.
# Honest bound (review residual, accepted 2026-07-02): on the STRICT path
# the sha256 chain binds every byte; on an ACTIVE weak-path retry, a
# SHA1-collision-forged InRelease could carry attacker-chosen SHA256
# Packages hashes, so end-to-end integrity reduces to SHA1-collision
# resistance on the InRelease — the fingerprint pin and the loud log line
# are the mitigations there, not the sha256 chain. The 6th arg is the
# component; the 7th arg (the pinned fingerprint) enables the scoped path.
echo "  Verifying Valve's signed-Release integrity chain..."
if ! igos_helper_verify_deb_via_signed_release \
    "$DEB_NAME" \
    "$TMPDIR/steam.deb" \
    "$STEAM_APT_BASE" \
    "$STEAM_KEYRING" \
    "$STEAM_DIST" \
    "$STEAM_COMPONENT" \
    "$STEAM_KEY_FPR"; then
    echo ""
    echo "  ERROR: the download could not be verified as genuinely coming from"
    echo "  Valve, so it was NOT installed and nothing on this machine was"
    echo "  changed. This can mean a network problem, or that the download was"
    echo "  tampered with in transit. Try again later. Do not install the"
    echo "  downloaded file by hand to work around this — the check is the only"
    echo "  thing establishing that the bytes are Valve's."
    exit 1
fi

echo "  Extracting..."
cd "$TMPDIR"
ar x steam.deb
tar xf data.tar.xz

# Pre-flight payload-completeness gate: every path the .deb ships must
# fall under a root this helper HANDLES (installs + records) or
# DELIBERATELY excludes. A new Valve path fails loudly HERE — before
# anything is copied — instead of silently landing on disk untracked.
#   handled:  usr/lib/steam/, usr/share/, usr/bin/steamdeps, lib/udev/
#   excluded: usr/bin/steam (the fail-closed wrapper owns that path),
#             etc/apt/ (pkm owns updates, not Valve's apt sources)
UNEXPECTED=$(tar tf data.tar.xz | grep -v '/$' | sed 's|^\./||' \
    | grep -vE '^(usr/lib/steam/|usr/share/|usr/bin/steam$|usr/bin/steamdeps$|lib/udev/|etc/apt/)' || true)
if [ -n "$UNEXPECTED" ]; then
    echo "  ERROR: this Steam release contains files in places this version of"
    echo "  the package was not built to install. Installing only part of it"
    echo "  would leave files on this machine that nothing could later remove,"
    echo "  so nothing was installed and nothing on this machine was changed."
    echo "  There is nothing to fix here — the package needs updating. Please"
    echo "  report it, and include the list below."
    echo "$UNEXPECTED" | sed 's/^/    /'
    exit 1
fi
echo "  Installing the Steam bootstrap under /usr/lib/steam..."
cp -a usr/lib/steam /usr/lib/

echo "  Recording the installed files with pkm..."
while IFS= read -r -d '' f; do
    igos_helper_record_file "$f"
done < <(find /usr/lib/steam \( -type f -o -type l \) -print0 2>/dev/null)

# usr/share + lib/udev payload: install + record EVERY file in one
# pass — completeness by construction. (A curated pattern list drifts
# the moment Valve adds a file; the ge-proton manifest-completeness
# standard applies: pkm remove must reach everything this install put
# on disk.)
while IFS= read -r -d '' f; do
    cp --parents -a "$f" /
    igos_helper_record_file "/$f"
done < <(find usr/share lib/udev \( -type f -o -type l \) -print0 2>/dev/null)

if [ -f usr/bin/steamdeps ]; then
    cp -a usr/bin/steamdeps /usr/bin/steamdeps
    igos_helper_record_file /usr/bin/steamdeps
fi

igos_helper_record_dep intergenos-helper-lib
igos_helper_record_dep lib32-glibc

gtk-update-icon-cache /usr/share/icons/hicolor 2>/dev/null || true
igos_helper_record_post_install_action \
    "gtk-update-icon-cache /usr/share/icons/hicolor"

igos_helper_commit

echo ""
echo "  Steam installed. Launch it with: steam"
echo ""
HELPEREOF
    chmod 755 "${DESTDIR}/usr/bin/igos-install-steam"
}
