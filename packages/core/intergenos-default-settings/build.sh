#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# intergenos-default-settings 1.0.0 — InterGenOS canonical SSoT for GNOME defaults
# https://github.com/InterGenJLU/intergenos
#
# Per D-006 (2026-05-18 requirement), this package is THE
# source-of-truth for system-wide GNOME / GTK / desktop defaults on
# InterGenOS installs. It replaces the install-theming.sh dconf write
# block (retired per D-006) with a gschema-override approach at the
# upstream layer:
#
#   - /usr/share/glib-2.0/schemas/90_intergenos.gschema.override
#       — GNOME interface (color-scheme, gtk/icon/cursor theme, fonts,
#         terminal colors, background, login banner, dock favorites,
#         window button layout)
#   - /usr/share/glib-2.0/schemas/91_intergenos-extensions.gschema.override
#       — GNOME shell extensions enable list + user-theme name
#   - /usr/share/glib-2.0/schemas/92_intergenos-desktop.gschema.override
#       — Desktop behavior (clock, touchpad, night light, window manager)
#
# post_install runs glib-compile-schemas to compile the overrides into
# the gschemas.compiled binary GNOME reads at session start.
#
# Also ships /etc/skel/.config/gtk-4.0/gtk.css as a SYMLINK to
# /usr/share/themes/InterGenOS/gtk-4.0/gtk.css (audit row J-005 +
# matrix line 343: install-theming.sh:382-389 wrote a regular-file
# copy; canonical posture is symlink so theme updates propagate to
# user homes via cp -a useradd behavior). Composes with the
# packages/desktop/intergenos-theme/ package that ships the theme
# assets at /usr/share/themes/InterGenOS/.
#
# Consolidates audit rows:
#   - J-002 / J-017 / J-029: the 3 gschema overrides were dead-letter
#     on installed systems (copied only by create-image.sh; no package
#     shipped them). This package IS the canonical shipper.
#   - J-005: /etc/skel libadwaita bridge as symlink (was regular-file
#     copy in install-theming.sh).
#
# Sequenced with T0-7-B (install-theming.sh retirement): this package
# lands first; install-theming.sh's dconf + libadwaita-copy + welcome-
# greeter blocks are removed under T0-7-B once this package is the
# canonical writer.
#
# tier=core: GNOME desktop defaults are core system policy on
# InterGenOS, not optional extras. Users who want to remove the
# InterGenOS defaults can `pkm remove intergenos-default-settings`
# (mirrors the intergenos-firewall-defaults pattern in D-011).
#
# GDM greeter customization (release 4, 2026-05-22):
#   - /etc/dconf/db/gdm.d/00-intergenos-greeter ships GDM-greeter-session
#     dconf settings (wallpaper = ItIsOnly, banner disabled, disable-user-
#     list=true, logo = wordmark, shell.theme = InterGenOS). D-006 retired
#     /etc/dconf/db/system.d/ but /etc/dconf/db/gdm.d/ is a DIFFERENT
#     mechanism (greeter-session-only, cannot be expressed via gschema)
#     and is untouched by D-006. post_install runs `dconf update` to
#     compile the gdm.d/ entries into /etc/dconf/db/gdm.
#
# Release 5 (2026-05-22):
#   - Wordmark brand-mark asset at /usr/share/intergenos/intergenos_
#     wordmark_transparent.png (referenced by the GDM logo key in the
#     dconf db above) is NOW PROVIDED BY THE intergen-mark PACKAGE
#     (theming-arc Item C closure, audit-row D-010 + J-016 RESOLVED).
#     The duplicate-wordmark bandaid in this package's assets/ + its
#     install block in do_install + defensive assert are REMOVED in
#     release 5; intergen-mark is added as a runtime dep so install
#     ordering keeps the wordmark path populated for the GDM logo
#     key. The GDM dconf db entry itself is unchanged (logo='/usr/
#     share/intergenos/intergenos_wordmark_transparent.png').

build() {
    set -e
    # No build step — pure-config package, files shipped from the
    # in-tree config/gsettings/ directory.
    return 0
}

do_install() {
    set -e
    local sources_dir="${IGOS_SOURCE_ROOT:-/mnt/intergenos}/config/gsettings"

    # 1. Ship the four gschema overrides to /usr/share/glib-2.0/schemas/.
    #    glib-compile-schemas (in post_install) picks them up + merges
    #    them into gschemas.compiled, which GNOME reads at session start.
    install -dm755 "${DESTDIR}/usr/share/glib-2.0/schemas"
    install -m644 \
        "${sources_dir}/90_intergenos.gschema.override" \
        "${DESTDIR}/usr/share/glib-2.0/schemas/90_intergenos.gschema.override"
    install -m644 \
        "${sources_dir}/91_intergenos-extensions.gschema.override" \
        "${DESTDIR}/usr/share/glib-2.0/schemas/91_intergenos-extensions.gschema.override"
    install -m644 \
        "${sources_dir}/92_intergenos-desktop.gschema.override" \
        "${DESTDIR}/usr/share/glib-2.0/schemas/92_intergenos-desktop.gschema.override"
    install -m644 \
        "${sources_dir}/93_intergenos-app-folders.gschema.override" \
        "${DESTDIR}/usr/share/glib-2.0/schemas/93_intergenos-app-folders.gschema.override"

    # 2. /etc/skel libadwaita bridge — SYMLINK (audit J-005 fix).
    #    Points at the InterGenOS theme's canonical gtk-4.0 stylesheet.
    #    useradd preserves symlinks via cp -a, so new user homes get
    #    the symlink + libadwaita apps (gnome-control-center, etc.)
    #    follow it to the canonical theme location and inherit updates
    #    from theme-package upgrades automatically.
    install -dm755 "${DESTDIR}/etc/skel/.config/gtk-4.0"
    ln -sf /usr/share/themes/InterGenOS/gtk-4.0/gtk.css \
        "${DESTDIR}/etc/skel/.config/gtk-4.0/gtk.css"

    # 2b. File-manager sidebar bookmark to the filesystem root. GNOME Files
    #     (Nautilus 49) dropped the old "Other Locations"/"Computer" view, so
    #     there is no GUI entry to drill into / . Seed a GTK bookmark to
    #     file:/// ("Computer") in /etc/skel so every new user gets a sidebar
    #     entry that opens the filesystem root (PRIME DIRECTIVE: the user can
    #     navigate their whole machine from the GUI). Nautilus reads
    #     ~/.config/gtk-3.0/bookmarks. Operator ask 2026-06-15.
    install -dm755 "${DESTDIR}/etc/skel/.config/gtk-3.0"
    printf 'file:/// Computer\n' \
        > "${DESTDIR}/etc/skel/.config/gtk-3.0/bookmarks"
    chmod 644 "${DESTDIR}/etc/skel/.config/gtk-3.0/bookmarks"

    # 3. burn-my-windows per-user profile config (NOT a dconf override).
    #    burn-my-windows v48 stores profile config in
    #    ~/.config/burn-my-windows/profiles/<microsecond-id>.conf files —
    #    NOT in dconf paths alone. The dconf path
    #    /org/gnome/shell/extensions/burn-my-windows/profile-close-0/ is
    #    a mirror the extension's ProfileManager.js generates FROM the
    #    .conf files it discovers; the .conf is the authoritative source.
    #    Without this file present, the extension generates an empty
    #    default profile at first session start ("default settings"
    #    appearance even when our gschema defaults loaded). Shipping
    #    via /etc/skel ensures every freshly-created user (including
    #    liveuser via init.sh's `cp -a /etc/skel/. /home/liveuser/`)
    #    gets the curated profile before the extension's first-run
    #    profile-init can write a blank. Filename matches the canonical-
    #    baseline workstation's profile ID for byte-level consistency;
    #    the extension treats filenames as opaque microsecond-stamped IDs.
    #
    #    Migrated from packages/desktop/intergenos-default-settings/
    #    (deleted in this commit; the dconf-system-db scope of that
    #    package was retired by D-006; this file-shipping mechanism
    #    was NOT in retirement scope so it lands here in the new
    #    canonical SSoT package).
    install -dm755 "${DESTDIR}/etc/skel/.config/burn-my-windows/profiles"
    install -m644 "${sources_dir%/*}/burn-my-windows/1775735161994164.conf" \
        "${DESTDIR}/etc/skel/.config/burn-my-windows/profiles/1775735161994164.conf"

    # Defensive asserts: confirm the four .gschema.override files
    # actually staged + the symlink staged as a symlink (not a regular
    # file) + the burn-my-windows profile staged. If the source-tree
    # paths drift, halt the build rather than shipping an empty /
    # J-005-regressing / burn-my-windows-empty-default-regressing package.
    for f in 90_intergenos 91_intergenos-extensions 92_intergenos-desktop 93_intergenos-app-folders; do
        if [ ! -f "${DESTDIR}/usr/share/glib-2.0/schemas/${f}.gschema.override" ]; then
            echo "FATAL: gschema override missing in DESTDIR: ${f}.gschema.override" >&2
            echo "Source path: ${sources_dir}/${f}.gschema.override" >&2
            exit 1
        fi
    done

    if [ ! -L "${DESTDIR}/etc/skel/.config/gtk-4.0/gtk.css" ]; then
        echo "FATAL: /etc/skel/.config/gtk-4.0/gtk.css did not stage as a symlink" >&2
        echo "(audit J-005 requires symlink, not regular-file copy)" >&2
        exit 1
    fi

    if [ ! -f "${DESTDIR}/etc/skel/.config/burn-my-windows/profiles/1775735161994164.conf" ]; then
        echo "FATAL: burn-my-windows profile missing in DESTDIR" >&2
        echo "Source path: ${sources_dir%/*}/burn-my-windows/1775735161994164.conf" >&2
        exit 1
    fi

    # 4. GDM greeter customization (Tier 2 release 4, 2026-05-22).
    #    Ships /etc/dconf/db/gdm.d/00-intergenos-greeter -- dconf source
    #    fragment that gets compiled into /etc/dconf/db/gdm by
    #    `dconf update` in post_install. Configures the GDM-greeter
    #    session (wallpaper + banner + user-list + logo + shell.theme).
    local assets="${ASSETS}"
    if [ -z "$assets" ] || [ ! -d "$assets" ]; then
        # build.sh is sourced (not invoked); use ${BASH_SOURCE[0]} for the
        # actual file path. $0 resolves to the calling script
        # (chroot-build-core-extra.sh), which would land at the wrong dir.
        assets="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/assets"
    fi
    install -dm755 "${DESTDIR}/etc/dconf/db/gdm.d"
    install -m644 "${assets}/gdm.d/00-intergenos-greeter" \
        "${DESTDIR}/etc/dconf/db/gdm.d/00-intergenos-greeter"

    # The gdm.d/ fragment compiled into /etc/dconf/db/gdm is consulted by the
    # greeter session ONLY if the active dconf PROFILE names it with a
    # `system-db:gdm` line. Vanilla upstream gdm ships
    # /usr/share/dconf/profile/gdm as `user-db:user` + a greeter file-db with
    # NO system-db:gdm (Debian/Fedora patch it in; we build vanilla), so
    # without this the entire 00-intergenos-greeter override is dead-letter --
    # the greeter falls back to upstream and renders the full clickable user
    # LIST (account enumeration) with default Adwaita branding instead of the
    # username field + InterGenOS branding. Ship an /etc/dconf/profile/gdm that
    # wires the gdm system-db in; /etc/dconf/profile takes precedence over
    # /usr/share/dconf/profile for the same profile name.
    install -dm755 "${DESTDIR}/etc/dconf/profile"
    install -m644 /dev/stdin "${DESTDIR}/etc/dconf/profile/gdm" << 'GDMPROFILE'
user-db:user
system-db:gdm
file-db:/usr/share/gdm/greeter-dconf-defaults
GDMPROFILE

    # Release 5 (2026-05-22): wordmark brand-mark asset at /usr/share/
    # intergenos/intergenos_wordmark_transparent.png is now provided by
    # the intergen-mark package (theming-arc Item C closure; runtime
    # dep declared in package.yml ensures install ordering). The
    # GDM dconf db above references that path; intergen-mark supplies
    # the file. The previous duplicate-asset-in-package bandaid was
    # removed in release 5.

    # Defensive assert for the GDM additions.
    if [ ! -f "${DESTDIR}/etc/dconf/db/gdm.d/00-intergenos-greeter" ]; then
        echo "FATAL: GDM dconf db source missing in DESTDIR" >&2
        echo "Source path: ${assets}/gdm.d/00-intergenos-greeter" >&2
        exit 1
    fi

    # 4b. Greeter background — the G3-3 per-resolution selector + its Before=gdm
    #     oneshot were RETIRED in GBC003.1. The greeter background is now set in
    #     the InterGenOS gnome-shell theme CSS (#lockDialogGroup, shipped by
    #     intergenos-theme), the standard durable mechanism — the GNOME-49 gdm
    #     greeter ignores org/gnome/desktop/background picture-uri (rendered
    #     solid-dark, obs-2), and the boot-time oneshot's pre-gdm ordering edge
    #     caused the AMD first-boot GDM SIGABRT (PI-14, operator-proven). Nothing
    #     to stage here anymore.

    # 4b-hidpi. Readable greeter on HiDPI displays that expose NO EDID (virtual
    #     GPUs — VirtualBox VMSVGA/vmwgfx, QEMU — and bare-metal panels shipping
    #     absent/bad EDID). GNOME scales the greeter from EDID-derived DPI; with
    #     no EDID it falls back to scale 1, so a 4K greeter renders unreadably
    #     small (the username field + clock shrink to a few px). Increasingly the
    #     first thing a new user sees is a 4K VM install, so this is a real
    #     adoption-path usability gap. A detection script forces scaling-factor=2
    #     ONLY for >=3840-wide no-EDID connectors (EDID displays keep GNOME's
    #     native auto-scale — never double-scaled; sub-4K is untouched — never
    #     bloated). It runs as a gdm.service ExecStartPre: SYNCHRONOUS inside gdm
    #     startup, NOT a `Before=gdm` oneshot (that inter-unit ordering was the
    #     retired PI-14 crash above), and fail-safe (always exit 0) so it can
    #     never block the greeter. Validated on a 4K VirtualBox install
    #     2026-06-19 (GBC0041): greeter readable, gdm stable across restarts,
    #     no-op confirmed on the simulated non-HiDPI path.
    install -dm755 "${DESTDIR}/usr/lib/intergenos"
    install -m755 "${assets}/greeter-hidpi/igos-greeter-hidpi.sh" \
        "${DESTDIR}/usr/lib/intergenos/igos-greeter-hidpi.sh"
    install -dm755 "${DESTDIR}/usr/lib/systemd/system/gdm.service.d"
    install -m644 /dev/stdin \
        "${DESTDIR}/usr/lib/systemd/system/gdm.service.d/10-intergenos-greeter-hidpi.conf" \
        << 'GDMHIDPI'
# InterGenOS: force a readable 2x greeter scale on HiDPI displays lacking EDID
# (VirtualBox/QEMU virtual GPUs, or panels with absent/bad EDID). ExecStartPre
# runs inside gdm startup -- NOT a Before=gdm oneshot (that caused PI-14). The
# script is fail-safe (exit 0); the leading '-' on ExecStartPre is belt-and-
# suspenders on top of that -- systemd treats ANY non-zero exit (or a
# missing/non-exec script) as non-fatal, so this greeter-scale helper can NEVER
# block the greeter from starting, only degrade to unscaled. Directly honors the
# PI-14 lesson (a greeter helper must never be able to keep the greeter down).
[Service]
ExecStartPre=-/usr/lib/intergenos/igos-greeter-hidpi.sh
GDMHIDPI

    if [ ! -x "${DESTDIR}/usr/lib/intergenos/igos-greeter-hidpi.sh" ]; then
        echo "FATAL: greeter HiDPI detection script missing/non-exec in DESTDIR" >&2
        exit 1
    fi
    if [ ! -f "${DESTDIR}/usr/lib/systemd/system/gdm.service.d/10-intergenos-greeter-hidpi.conf" ]; then
        echo "FATAL: greeter HiDPI gdm drop-in missing in DESTDIR" >&2
        exit 1
    fi

    # 4c. Windows-style dock defaults (G3-20 bake, approved 2026-06-10).
    #     Dash-to-Panel (bottom taskbar) + ArcMenu (branded start button) are the
    #     more-adoptable default layout. Their enable list lives in the 91
    #     gschema override (org.gnome.shell, system schema); but their per-
    #     extension settings can NOT be set via gschema override — DtP/ArcMenu
    #     ship their schemas inside the extension bundle dirs and read their OWN
    #     bundled schema at runtime, so a system gschema override is ineffective.
    #     The dconf layer is path-based and schema-independent, so the dock look
    #     ships as a dconf system db (db `local`), wired via /etc/dconf/profile/
    #     user (user-db:user + system-db:local). This does NOT reintroduce the
    #     D-006-retired system.d for core-GNOME settings — those stay gschema;
    #     this is the only mechanism that can seed extension defaults the running
    #     user actually reads.
    install -dm755 "${DESTDIR}/etc/dconf/profile"
    install -m644 "${assets}/dconf/profile/user" \
        "${DESTDIR}/etc/dconf/profile/user"
    install -dm755 "${DESTDIR}/etc/dconf/db/local.d"
    install -m644 "${assets}/dconf/db/local.d/00-intergenos-dock" \
        "${DESTDIR}/etc/dconf/db/local.d/00-intergenos-dock"
    # GNOME app-overview "InterGenOS" folder — collects every app tagged
    # X-InterGenOS via the folder's categories= match (operator branding 2026-06-11).
    install -m644 "${assets}/dconf/db/local.d/01-intergenos-app-folder" \
        "${DESTDIR}/etc/dconf/db/local.d/01-intergenos-app-folder"
    # Shell blur-effect defaults. The image enables the blur extension for every
    # new user in the 91 gschema override but shipped no settings for it, so
    # every default of a default-enabled component came from the third-party
    # bundle and could move on any version bump with nothing in the tree saying
    # what we intended. Same mechanism argument as the dock above: that
    # extension also reads its own bundled schema, so dconf is the only layer
    # that reaches it. The file itself carries the measurement and the read of
    # the component sources that justify the two keys it sets.
    install -m644 "${assets}/dconf/db/local.d/02-intergenos-shell-effects" \
        "${DESTDIR}/etc/dconf/db/local.d/02-intergenos-shell-effects"
    # Keyboard-shortcut defaults. Ctrl+Alt+T opens the shipped terminal. This
    # is a dconf fragment rather than a gschema override because a custom
    # keybinding's per-binding schema is RELOCATABLE — it has no fixed path, so
    # an override cannot set it. The companion show-desktop binding IS in a
    # fixed-path schema and lives in the 92 gschema override. The file itself
    # carries that argument and the measurement behind it.
    install -m644 "${assets}/dconf/db/local.d/03-intergenos-keybindings" \
        "${DESTDIR}/etc/dconf/db/local.d/03-intergenos-keybindings"
    # PI-3: lock folder-children so the gnome-shell first-login user-db seed
    # (['System','Utilities','YaST','Pardus']) cannot overwrite our system
    # default — without this lock the "InterGenOS" folder never appears
    # (confirmed on the development7/a development machine installs).
    install -dm755 "${DESTDIR}/etc/dconf/db/local.d/locks"
    install -m644 "${assets}/dconf/db/local.d/locks/00-app-folders" \
        "${DESTDIR}/etc/dconf/db/local.d/locks/00-app-folders"
    # The branded ArcMenu start badge (framed iOS-glass squircle + ECG pulse) at
    # the absolute system path the dconf db above points ArcMenu at.
    install -dm755 "${DESTDIR}/usr/share/icons/intergenos"
    install -m644 "${assets}/branding/start-badge.svg" \
        "${DESTDIR}/usr/share/icons/intergenos/start-badge.svg"
    for f in /etc/dconf/profile/user /etc/dconf/db/local.d/00-intergenos-dock \
             /etc/dconf/db/local.d/01-intergenos-app-folder \
             /etc/dconf/db/local.d/02-intergenos-shell-effects \
             /etc/dconf/db/local.d/locks/00-app-folders \
             /usr/share/icons/intergenos/start-badge.svg; do
        if [ ! -f "${DESTDIR}${f}" ]; then
            echo "FATAL: desktop default missing in DESTDIR: ${f}" >&2
            exit 1
        fi
    done

    # An empty or truncated fragment installs and compiles without complaint and
    # is indistinguishable from a correct one once compiled, so assert the two
    # keys are actually present in the bytes about to ship. Same reasoning as
    # the /etc/environment PATH check further down: a file that exists is not a
    # file that says anything.
    for key in \
        "org/gnome/shell/extensions/blur-my-shell/panel" \
        "org/gnome/shell/extensions/blur-my-shell/dash-to-dock"; do
        if ! grep -qx "\[${key}\]" \
            "${DESTDIR}/etc/dconf/db/local.d/02-intergenos-shell-effects"; then
            echo "FATAL: shell-effects dconf fragment is missing group [${key}]" >&2
            exit 1
        fi
    done
    if [ "$(grep -c '^static-blur=false$' \
        "${DESTDIR}/etc/dconf/db/local.d/02-intergenos-shell-effects")" != "2" ]; then
        echo "FATAL: shell-effects dconf fragment must set static-blur=false twice" >&2
        exit 1
    fi

    # 5. Stub /etc/environment so systemd's shipped symlink at
    #    /usr/lib/environment.d/99-environment.conf -> ../../../etc/environment
    #    resolves. systemd's package install creates that symlink expecting
    #    /etc/environment to be present (the canonical system-wide env-vars
    #    file PAM reads at login). Debian's base-files + Fedora's setup
    #    both ship it; we missed it, leaving a broken symlink under
    #    /usr/lib/environment.d/.
    #
    #    It also carries the system-wide PATH, and this is the right file for
    #    it rather than a drop-in under /etc/profile.d/. A profile.d script is
    #    sourced by /etc/profile and /etc/bashrc, so it reaches login shells
    #    and interactive shells and nothing else — a graphical session started
    #    by the display manager never runs it, so a user on the live desktop
    #    would still get a PATH without the sbin directories and would still
    #    be told that installed tools do not exist. /etc/environment reaches
    #    both: PAM's pam_env reads it for every login session including the
    #    display manager's, and systemd's user manager reads it through the
    #    /usr/lib/environment.d/99-environment.conf symlink above.
    #
    #    Without the PATH line, /usr/sbin and /sbin were absent from a normal
    #    user's session on the live medium, so present tools (ip, nft, fdisk,
    #    parted, dmesg) reported as not found. That is the system lying about
    #    itself to the person least able to check — someone diagnosing a
    #    machine from the live medium is exactly who needs those tools. The
    #    value matches systemd's own user-manager default so PAM sessions and
    #    systemd user sessions agree; /sbin and /bin are included for the
    #    merged-/usr compat symlinks base-files ships.
    install -dm755 "${DESTDIR}/etc"
    cat > "${DESTDIR}/etc/environment" <<'ENVEOF'
# /etc/environment — system-wide environment variables.
# PAM reads this at every login session, graphical sessions included.
# Format: NAME=VALUE per line, no shell expansion, no quoting (the value
# is literal). systemd's /usr/lib/environment.d/99-environment.conf
# symlink resolves to this file, so systemd user sessions read it too.
#
# PATH must list the sbin directories. Administrative tools live in
# /usr/sbin (ip, nft, fdisk, parted, dmesg, …) and are readable and
# runnable by ordinary users; leaving them off the path makes installed
# tools report as "command not found", which is a false statement about
# the system. /sbin and /bin are the merged-/usr compat symlinks.
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ENVEOF
    chmod 644 "${DESTDIR}/etc/environment"

    # Fail the build if the PATH line is not in what we are about to ship:
    # this file has been shipped empty before, and an empty /etc/environment
    # looks exactly like a correct one from the outside.
    if ! grep -qE '^PATH=.*(:|=)/usr/sbin(:|$)' "${DESTDIR}/etc/environment"; then
        echo "FATAL: /etc/environment ships without /usr/sbin on PATH" >&2
        exit 1
    fi

    if [ ! -f "${DESTDIR}/etc/environment" ]; then
        echo "FATAL: /etc/environment stub missing in DESTDIR" >&2
        exit 1
    fi

    # 6. INTERNVL-PI-C: system-wide scdaemon config so the OpenPGP smartcard
    #    applet stays reachable via gpg on an installed system. InterGenOS ships
    #    pcscd always-on (socket-activated for the PKCS#11 / PIV Secure-Boot path);
    #    scdaemon defaults to its INTERNAL CCID driver, which then contends with
    #    pcscd for the single USB CCID reader (mutual exclusion) — so
    #    `gpg --card-status` reports "OpenPGP card not available" while pcscd holds
    #    the reader. disable-ccid + pcsc-shared route scdaemon THROUGH pcscd in
    #    shared mode, so the PIV (pcscd) and OpenPGP (gpg) applets coexist on the
    #    one token. Without this the sign-manifest-class GPG ceremony cannot run on
    #    an installed box (found on the development machine InternVL-01 signing-infra test; the
    #    build host works only because its ~/.gnupg/scdaemon.conf carries these).
    #    System-wide /etc/gnupg applies to every user incl. root.
    install -dm755 "${DESTDIR}/etc/gnupg"
    install -m644 /dev/stdin "${DESTDIR}/etc/gnupg/scdaemon.conf" << 'SCDAEMONCONF'
# InterGenOS system-wide scdaemon configuration.
# Route scdaemon through pcscd (shared mode) instead of its internal CCID driver,
# so the OpenPGP applet stays reachable via gpg while pcscd holds the reader for
# the PKCS#11 / PIV (Secure Boot signing) path. Both applets then coexist on a
# single USB CCID smartcard. (INTERNVL-PI-C, 2026-06-25.)
disable-ccid
pcsc-shared
SCDAEMONCONF

    if [ ! -f "${DESTDIR}/etc/gnupg/scdaemon.conf" ]; then
        echo "FATAL: /etc/gnupg/scdaemon.conf missing in DESTDIR" >&2
        exit 1
    fi
}

post_install() {
    set -e
    # Compile the schemas. Overrides only take effect after compilation.
    # The || true is defensive — if glib2 isn't installed at this exact
    # moment (early-bootstrap edge case), the schemas stay in place and
    # a subsequent glib2 install can compile them. In normal install
    # ordering, glib2 is already present and compilation succeeds.
    glib-compile-schemas /usr/share/glib-2.0/schemas 2>/dev/null || true

    # Compile the GDM dconf db. The gdm.d/ fragments are merged into
    # /etc/dconf/db/gdm which the gdm user's dconf profile reads on
    # greeter-session start. Same defensive || true pattern as the
    # glib-compile-schemas call above.
    dconf update 2>/dev/null || true
}
