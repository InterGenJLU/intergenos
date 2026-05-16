#!/bin/sh
# /init — InterGenOS live-boot dispatcher (custom 100-line initramfs entry point)
#
# Loaded by kernel as initrd entry point. Resolves the live-boot squashfs,
# sets up overlayfs root, dispatches to live GNOME desktop OR Forge installer
# based on `igos.mode=` kernel cmdline.
#
# Q-INIT resolved 2026-05-05/06: April-10 custom-init stands. UKI wraps this
# initramfs via systemd-stub + objcopy + sbsign (NK#1 PIV slot 9c).
#
# Q-PERSISTENCE resolved: off by default for v1.0; design document at
# `docs/research/installer/forge_iso_concept_design_2026-05-04.md`.
# Q-DM-VERITY: deferred to v1.x.
#
# Use of busybox-static for the userland inside this initramfs is intentional:
# minimal attack surface, no glibc dependency in the early-boot envelope.

set -e

# ---- Utilities -------------------------------------------------------------
fatal() {
    echo "[init] FATAL: $*" >&2
    echo "[init] Dropping to recovery shell. Type 'exit' to continue (no-op)."
    exec /bin/sh
}

info() {
    echo "[init] $*"
}

# ---- Mount essential virtual filesystems -----------------------------------
mount -t proc     -o nosuid,noexec,nodev proc     /proc
mount -t sysfs    -o nosuid,noexec,nodev sysfs    /sys
mount -t devtmpfs -o nosuid             devtmpfs  /dev

# ---- Parse cmdline ---------------------------------------------------------
# `igos.mode=` selects the userspace dispatch:
#   live         -> live GNOME desktop (liveuser autologin)
#   install-gui  -> auto-launch Forge GUI installer (skips GNOME shell)
#   install-tui  -> auto-launch Forge TUI declarative-builder on tty1
MODE=$(awk -v RS=' ' '/^igos\.mode=/ {sub(/^igos\.mode=/, ""); print}' /proc/cmdline)
[ -z "$MODE" ] && MODE=live
case "$MODE" in
    live|install-gui|install-tui) ;;
    *) fatal "unknown igos.mode=$MODE (expected: live|install-gui|install-tui)" ;;
esac
info "boot mode: $MODE"

# ---- Load required modules -------------------------------------------------
# These modules are present in the initramfs cpio (see
# installer/init/build-initramfs.sh — sibling file in this directory; invoked
# from scripts/chroot-build-bootloader.sh during phase_bootloader).
# squashfs: read-only root filesystem
# overlay:  writable upper layer over squashfs
# loop:     squashfs is loop-mounted off the ISO
for mod in squashfs overlay loop isofs vfat; do
    modprobe "$mod" 2>/dev/null || info "module $mod not loadable (may be built-in)"
done

# ---- Locate ISO live filesystem --------------------------------------------
# Strategy modeled on Debian-live / Ubuntu / Arch live discovery:
#   1. Fast path: try canonical label `IGOS_LIVE` via blkid.
#   2. Slow path: scan block devices for `/live/filesystem.squashfs` — the
#      authoritative artifact regardless of how the media is labeled.
# This survives user-renamed media (USB stick reformatted to a different
# label, etc.) and matches real-distro behavior. Retry loop accommodates
# slow USB enumeration on some firmware.
find_live_device() {
    # Fast path: canonical label
    local dev
    dev=$(blkid -L IGOS_LIVE 2>/dev/null)
    if [ -n "$dev" ]; then
        echo "$dev"
        return 0
    fi

    # Slow path: scan all block devices for /live/filesystem.squashfs
    local d tmpdir
    tmpdir=$(mktemp -d 2>/dev/null) || tmpdir=/tmp/scan-live
    mkdir -p "$tmpdir"
    for d in /dev/sd[a-z][0-9]* /dev/vd[a-z][0-9]* /dev/sr[0-9]* \
             /dev/nvme[0-9]n[0-9]p[0-9]* /dev/mmcblk[0-9]p[0-9]*; do
        [ -b "$d" ] || continue
        if mount -t auto -o ro "$d" "$tmpdir" 2>/dev/null; then
            if [ -f "$tmpdir/live/filesystem.squashfs" ]; then
                umount "$tmpdir" 2>/dev/null
                rmdir "$tmpdir" 2>/dev/null
                echo "$d"
                return 0
            fi
            umount "$tmpdir" 2>/dev/null
        fi
    done
    rmdir "$tmpdir" 2>/dev/null
    return 1
}

ISO_DEV=""
for tries in 1 2 3 4 5 6 7 8 9 10; do
    ISO_DEV=$(find_live_device) && [ -n "$ISO_DEV" ] && break
    sleep 1
done
[ -z "$ISO_DEV" ] && fatal "live media not found: no block device has /live/filesystem.squashfs (canonical label IGOS_LIVE also tried)"
info "ISO device: $ISO_DEV"

# ---- Mount ISO + squashfs --------------------------------------------------
mkdir -p /run/iso /run/squashfs /run/overlay
mount -o ro "$ISO_DEV" /run/iso || fatal "cannot mount ISO at $ISO_DEV"

SQUASHFS_PATH=/run/iso/live/filesystem.squashfs
[ -f "$SQUASHFS_PATH" ] || fatal "squashfs not found at $SQUASHFS_PATH"

# ---- SHA256 verify squashfs against shipped hash ---------------------------
# The UKI signature (Secure Boot) covers init.sh + initramfs + cmdline only.
# /live/filesystem.squashfs lives outside the UKI; without independent
# verification, an attacker swapping the squashfs on the media yields a
# trusted-boot to malicious payload. scripts/build-iso.sh writes
# /live/filesystem.sha256 alongside the squashfs at every ISO build; this
# block verifies the on-media squashfs matches before mounting.
#
# busybox-static's sha256sum applet does the heavy lift. Fatal on any of:
# missing hash file (suggests media corruption or pre-`build-iso` build),
# unreadable hash (same), digest mismatch (active tampering or media decay).
SHA256_FILE=/run/iso/live/filesystem.sha256
[ -f "$SHA256_FILE" ] || fatal "missing $SHA256_FILE — cannot verify squashfs integrity"
EXPECTED=$(awk '{print $1}' "$SHA256_FILE")
[ -n "$EXPECTED" ] || fatal "could not parse expected sha256 from $SHA256_FILE"
info "verifying squashfs sha256 (this takes a few seconds)..."
ACTUAL=$(sha256sum "$SQUASHFS_PATH" | awk '{print $1}')
[ "$EXPECTED" = "$ACTUAL" ] || fatal "squashfs sha256 mismatch: expected $EXPECTED got $ACTUAL"
info "squashfs sha256 verified ($EXPECTED)"

mount -t squashfs -o ro,loop "$SQUASHFS_PATH" /run/squashfs || fatal "cannot mount squashfs"

# ---- Set up overlayfs root -------------------------------------------------
mount -t tmpfs tmpfs /run/overlay
mkdir -p /run/overlay/upper /run/overlay/work
mkdir -p /newroot
mount -t overlay overlay \
    -o lowerdir=/run/squashfs,upperdir=/run/overlay/upper,workdir=/run/overlay/work \
    /newroot \
    || fatal "cannot create overlayfs root"

# ---- Move mounts into newroot ----------------------------------------------
# Preserve our virtual + ISO mounts so userspace can find them.
# The squashfs is built with -e proc -e sys -e dev -e run -e tmp (those are
# runtime pseudo-fs paths, intentionally excluded from the static image),
# so we must create the mount-point directories in /newroot's upper layer
# (overlayfs upperdir is tmpfs, writable) before mount --move can target them.
mkdir -p /newroot/run/iso /newroot/run/squashfs \
         /newroot/sys /newroot/proc /newroot/dev /newroot/tmp
mount --move /run/iso       /newroot/run/iso
mount --move /run/squashfs  /newroot/run/squashfs
mount --move /sys           /newroot/sys
mount --move /proc          /newroot/proc
mount --move /dev           /newroot/dev

# ---- Mode-specific overlay setup -------------------------------------------
# These writes land in the overlayfs upper layer (tmpfs), not the squashfs
# itself (read-only). systemd in /newroot reads them as if they were always
# there. Lets us correct squashfs gaps without rebuilding the squashfs.
#
# The squashfs is built from the chroot root, which is an INSTALLED-SYSTEM
# layout — full set of multi-user.target.wants/ symlinks (mariadb, httpd,
# caddy, influxdb, etcd, valkey, lighttpd, ...) plus the systemd-firstboot
# machinery for first-boot-of-installed-system flow. Both are wrong context
# for a live ISO: the persistent services fail because the live filesystem
# lacks their users / config / mount namespaces, and systemd-firstboot fires
# the interactive user-creation prompt because the squashfs has no
# /etc/machine-id. These overlay writes patch the live ISO into something
# minimally usable WITHOUT modifying the squashfs (which is shared with
# install modes + ends up on the installed target post-install). Proper
# live.target architecture is a v1.0 design arc.
#
# All three live-ISO modes share a minimal base scaffold (machine-id,
# hostname, firstboot masks, noisy-server masks). Without these, ANY
# live-ISO boot of the shared squashfs would: (a) trigger
# systemd-firstboot interactively because the squashfs ships /etc/
# machine-id == "uninitialized" — fatal on install-tui because the
# prompt competes with forge-tui.service for tty1 — and (b) fire the
# server-class Wave 1b daemons (mariadb, httpd, ...) which fail in
# tight Restart= loops and drown the boot console.
#
# Live + install-gui then add the GDM-class scaffold on top (liveuser,
# automatic GDM login, graphical.target default, tty2 root autologin,
# dconf screen-lock disable). install-tui doesn't need any of that —
# forge-tui.service claims tty1 directly under multi-user.target.
info "$MODE mode: writing shared base scaffold (machine-id, firstboot/noisy-service masks)"

# Generate a valid 32-hex-char machine-id. Writing literal "uninitialized"
# TRIGGERS systemd's ConditionFirstBoot=yes path (which fires
# systemd-firstboot interactive); a real ID suppresses it. This ID is
# per-boot (regenerated each live session) since the overlay is tmpfs.
#
# Kernel's UUID source produces a 36-char string of form
# xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx; sed strips the dashes leaving
# the required 32 hex chars. busybox-static in this initramfs has
# `cat` and `sed` but not `tr` or `head`, hence this form rather than
# the `tr -dc ... | head -c 32 < /dev/urandom` idiom that's common in
# full-userland live-init scripts.
cat /proc/sys/kernel/random/uuid | sed 's/-//g' > /newroot/etc/machine-id

# Pre-set hostname so any downstream firstboot machinery has no field
# left to prompt for.
echo "intergenos-live" > /newroot/etc/hostname

# Belt-and-suspenders: explicitly mask the firstboot services even
# though a valid machine-id should suppress them. Symlink to /dev/null
# is systemd's "never start this unit" idiom.
ln -sf /dev/null /newroot/etc/systemd/system/systemd-firstboot.service
ln -sf /dev/null /newroot/etc/systemd/system/systemd-homed-firstboot.service

# Mask the Wave 1b databases + webservers that auto-start at
# multi-user.target.wants/. In live context they fail noisily with
# USER / NAMESPACE errors (the live filesystem has no per-service users,
# no /var/log/<svc>/, no Restart=no policy, etc.) and systemd's Restart=
# spins them in tight loops, drowning the console. Mask each in the
# overlay so the live boot stays quiet. Installed systems get these
# back because the masks live only in the tmpfs overlay.
for svc in mariadb httpd caddy influxdb etcd valkey lighttpd nginx apache postgresql memcached \
           haproxy transmission-daemon atopgpu apparmor systemd-pcrlock-secureboot-policy; do
    ln -sf /dev/null "/newroot/etc/systemd/system/${svc}.service"
done
# NOTE on the last 5 masks: in a normal installed boot, apparmor +
# pcrlock-secureboot-policy are part of our security posture and MUST
# run. They're masked here only because: apparmor's profile-load fails
# against the live squashfs (profile paths the overlay can't satisfy);
# pcrlock fails against the virtual TPM that test VMs (swtpm) provide.
# haproxy, transmission-daemon, and atopgpu are server-class daemons
# that shouldn't be running in a try-it-out live session anyway.
# All masks are tmpfs-overlay only — installed targets boot the
# underlying squashfs without these overlay symlinks, so the secure
# default re-engages on install.

if [ "$MODE" = "live" ] || [ "$MODE" = "install-gui" ]; then
    info "$MODE mode: writing live-class scaffold (liveuser+GDM autologin+graphical.target)"

    # ---- Liveuser creation -------------------------------------------------
    # busybox-static initramfs lacks `useradd`, so we hand-write the standard
    # /etc/{passwd,group,shadow,gshadow} entries. Per-boot ephemeral (overlay
    # is tmpfs); the underlying squashfs stays clean of the liveuser account,
    # so installed systems get no leftover ghost user.
    #
    # UID/GID 1000 — first human-user range. LFS base layout reserves
    # < 1000 for system accounts, so 1000 is free in our squashfs.
    echo 'liveuser:x:1000:1000:Live User:/home/liveuser:/bin/bash' >> /newroot/etc/passwd
    echo 'liveuser:x:1000:' >> /newroot/etc/group
    # Password disabled (* in shadow). Console auth via GDM autologin only;
    # SSH auth is not configured (operator-driven `ssh-copy-id` per boot for
    # development, by design — no pre-shipped keys).
    # lastchg=19800 (~2024-03) ensures PAM doesn't treat the account as
    # expired the moment GDM tries to switch into it.
    echo 'liveuser:*:19800:0:99999:7:::' >> /newroot/etc/shadow
    echo 'liveuser:!::' >> /newroot/etc/gshadow

    # Capability groups. LFS base ships each line with empty member list
    # (trailing colon); appending bare `liveuser` after the `:` produces a
    # valid first-member entry.
    for grp in wheel audio video input plugdev render dialout lp users cdrom; do
        sed -i "/^${grp}:/s/\$/liveuser/" /newroot/etc/group
    done

    # Home dir + tmpfiles-driven ownership (busybox-static may lack chown;
    # delegating to systemd-tmpfiles at sysinit.target sidesteps the
    # dependency).
    mkdir -p /newroot/home/liveuser
    cp -a /newroot/etc/skel/. /newroot/home/liveuser/ 2>/dev/null || true
    cat > /newroot/etc/tmpfiles.d/liveuser-home.conf <<'TMPFILES'
d /home/liveuser 0755 liveuser liveuser - -
Z /home/liveuser - liveuser liveuser - -
TMPFILES

    # ---- GDM autologin to liveuser ------------------------------------------
    # /etc/gdm/custom.conf is GDM's runtime config. AutomaticLogin lands at
    # the GNOME session for the named user immediately, no greeter.
    mkdir -p /newroot/etc/gdm
    cat > /newroot/etc/gdm/custom.conf <<'GDMCONF'
[daemon]
AutomaticLoginEnable=true
AutomaticLogin=liveuser

[security]

[xdmcp]

[chooser]

[debug]
GDMCONF

    # display-manager.service -> gdm.service. The 90-gdm.preset shipped by
    # the gdm package handles this for installed systems via
    # systemctl preset-all; live overlay re-creates the symlink for belt-
    # and-suspenders.
    ln -sf /usr/lib/systemd/system/gdm.service \
           /newroot/etc/systemd/system/display-manager.service

    # ---- Default target = graphical ----------------------------------------
    # graphical.target pulls in display-manager.service -> gdm -> Wayland.
    ln -sf /usr/lib/systemd/system/graphical.target \
           /newroot/etc/systemd/system/default.target

    # ---- TTY2 root autologin (emergency fallback) --------------------------
    # GDM claims tty1 for its Wayland greeter/session. tty2 has root-auto
    # for emergency diagnostics when GDM doesn't come up (bare-metal first
    # boot, GPU driver gap, etc.). Ctrl-Alt-F2 from any context to reach.
    mkdir -p /newroot/etc/systemd/system/getty@tty2.service.d
    cat > /newroot/etc/systemd/system/getty@tty2.service.d/autologin.conf <<'AUTOLOGIN_TTY2'
[Service]
ExecStart=
ExecStart=-/sbin/agetty -o '-p -f -- \\u' --noclear --autologin root --keep-baud 115200,38400,9600 %I $TERM
AUTOLOGIN_TTY2
    mkdir -p /newroot/etc/systemd/system/getty.target.wants
    ln -sf /usr/lib/systemd/system/getty@.service \
           /newroot/etc/systemd/system/getty.target.wants/getty@tty2.service

    # ---- Root password expiry refresh --------------------------------------
    # squashfs /etc/shadow root entry has lastchg=0 -> PAM forces a password
    # change at first console login. Push lastchg into the future so the
    # tty2 fallback (and any direct console) doesn't prompt.
    sed -i 's@^root:\([^:]*\):0:@root:\1:19800:@' /newroot/etc/shadow

    # ---- Screen-lock disable for live session ------------------------------
    # liveuser's /etc/shadow entry is `*` (password disabled by design); the
    # default GNOME idle screen-lock would lock the live session out with no
    # recovery path. Override via a system-wide dconf keyfile compiled to the
    # `local` db, with a oneshot systemd unit that runs `dconf update` before
    # the display manager starts. Per-liveuser semantics are preserved because
    # this overlay-write only happens in the live-mode if-block; installed
    # systems boot through the no-MODE path and keep the secure default
    # (lock-enabled=true).
    mkdir -p /newroot/etc/dconf/db/local.d
    cat > /newroot/etc/dconf/db/local.d/00-live-screensaver <<'DCONF_LIVE'
[org/gnome/desktop/screensaver]
lock-enabled=false
idle-activation-enabled=false

[org/gnome/desktop/session]
idle-delay=uint32 0
DCONF_LIVE

    mkdir -p /newroot/etc/dconf/profile
    cat > /newroot/etc/dconf/profile/user <<'DCONF_PROFILE'
user-db:user
system-db:local
DCONF_PROFILE

    cat > /newroot/etc/systemd/system/igos-live-dconf-compile.service <<'DCONF_SVC'
[Unit]
Description=Compile dconf databases for InterGenOS live session
DefaultDependencies=no
Before=display-manager.service
After=local-fs.target
ConditionPathExists=/etc/dconf/db/local.d

[Service]
Type=oneshot
ExecStart=/usr/bin/dconf update
RemainAfterExit=yes

[Install]
WantedBy=display-manager.service
DCONF_SVC

    mkdir -p /newroot/etc/systemd/system/display-manager.service.wants
    ln -sf /etc/systemd/system/igos-live-dconf-compile.service \
           /newroot/etc/systemd/system/display-manager.service.wants/igos-live-dconf-compile.service
fi

# ---- install-gui mode: Forge GUI autostart for liveuser --------------------
# Live + install-gui share the same liveuser+GDM scaffold above (Wayland
# session under graphical.target). What differs is what auto-starts in
# that session: live mode wants intergen-welcome; install-gui mode wants
# Forge GUI to fire instead.
#
# Two overlay writes for install-gui:
#   1. /home/liveuser/.config/autostart/forge-gui.desktop — XDG autostart
#      entry that fires `/usr/bin/forge-gui-launch` at session start.
#      The launcher is the liveuser-side half of a two-stage flow that
#      captures the calling session's DISPLAY / WAYLAND_DISPLAY /
#      XAUTHORITY / XDG_RUNTIME_DIR / DBUS_SESSION_BUS_ADDRESS into a
#      file in /run/user/1000, then pkexecs /usr/bin/forge-gui-runner
#      which restores those env vars under root and execs forge in GUI
#      mode. The mode + archive + packages args are hardcoded in
#      forge-gui-runner so the passwordless polkit grant covers only
#      this exact root invocation — liveuser can't tamper with the
#      .desktop file to invoke forge with alternate args.
#      Squashfs already has /usr/bin/forge + forge-gui-launch +
#      forge-gui-runner + the polkit policy + rule installed by the
#      forge package; the rule grants passwordless YES to
#      subject.user=="liveuser" for the run-installer action.
#   2. /home/liveuser/.config/autostart/intergen-welcome.desktop with
#      `Hidden=true` — per the XDG Autostart spec, a user-level autostart
#      entry shadows the system-wide one at /etc/xdg/autostart/. Setting
#      Hidden=true prevents the entry from firing. Replaces the prior
#      `ln -sf /dev/null /etc/xdg/autostart/intergen-welcome.desktop`
#      technique, which produced `gnome-session-service: Desktop file ...
#      couldn't be parsed` warnings on every install-gui boot (gnome-
#      session reads /dev/null, parses nothing, fails). Hidden=true is
#      spec-compliant and emits no warning. Confirmed in xdg-autostart
#      spec: "If the .desktop file has Hidden set to true, the file MUST
#      be ignored. Otherwise, the entries in Hidden are honored."
if [ "$MODE" = "install-gui" ]; then
    info "install-gui mode: Forge GUI autostart + welcomer shadow"

    # Minimal autostart entry. NoDisplay + OnlyShowIn dropped 2026-05-16 because
    # cycle-4 install-GUI smoke test confirmed: autostart never fired with those
    # filters. Now plain Type=Application + Exec= so any XDG-autostart consumer
    # honors it. Sh-wrapper writes a marker file at /tmp/forge-autostart-fired
    # so post-mortem can distinguish "autostart never fired" from "forge-gui-launch
    # crashed early". Also written system-wide under /etc/xdg/autostart for
    # belt-and-suspenders coverage.
    mkdir -p /newroot/home/liveuser/.config/autostart
    cat > /newroot/home/liveuser/.config/autostart/forge-gui.desktop <<'FORGEGUI'
[Desktop Entry]
Type=Application
Name=InterGenOS Forge Installer
Exec=sh -c 'touch /tmp/forge-autostart-fired; exec /usr/bin/forge-gui-launch'
Terminal=false
FORGEGUI

    # System-wide copy — fires regardless of XDG_CONFIG_HOME or user-vs-system
    # autostart-dir preference of the session manager.
    mkdir -p /newroot/etc/xdg/autostart
    cp /newroot/home/liveuser/.config/autostart/forge-gui.desktop \
       /newroot/etc/xdg/autostart/forge-gui.desktop

    # Shadow the system-wide welcomer autostart with a Hidden=true user-
    # level entry. This session only (overlay tmpfs evaporates on next
    # boot); installed systems unaffected.
    cat > /newroot/home/liveuser/.config/autostart/intergen-welcome.desktop <<'WELCOMEHIDE'
[Desktop Entry]
Type=Application
Name=InterGenOS Welcome (shadowed in install-gui mode)
Exec=true
Hidden=true
WELCOMEHIDE
fi

# ---- install-tui mode: mask GDM so forge-tui owns tty1 ---------------------
# forge-tui.service has Conflicts=getty@tty1.service but NOT
# Conflicts=gdm.service. In install-tui mode the squashfs's enabled GDM
# starts on tty1 BEFORE forge-tui gets a chance (gdm.service has earlier
# target dependency satisfaction than forge-tui's multi-user.target).
# Net: cycle-4 install-tui smoke test landed at GDM's "Username:" prompt
# on tty1, with forge-tui never running.
#
# Fix: in install-tui mode, mask gdm.service via overlay symlink to
# /dev/null so systemd refuses to start it. Live + install-gui still
# need GDM, so this is mode-gated.
if [ "$MODE" = "install-tui" ]; then
    info "install-tui mode: masking gdm.service so forge-tui owns tty1"
    mkdir -p /newroot/etc/systemd/system
    ln -sf /dev/null /newroot/etc/systemd/system/gdm.service
fi

# ---- Hand off mode to userspace --------------------------------------------
# Diagnostic-only marker. Not a routing input — see comment below. Useful for
# post-mortem of install or live-session failures (`cat /run/intergenos/boot-mode`
# tells you which kernel cmdline mode the system actually came up in).
mkdir -p /newroot/run/intergenos
echo "$MODE" > /newroot/run/intergenos/boot-mode

# ---- Switch root and exec PID 1 --------------------------------------------
# Mode dispatch happens at two layers, both upstream of switch_root:
#   live + install-gui: this script's overlay-write block above installs the
#     liveuser+GDM+autologin scaffold (and, for install-gui only, an XDG
#     autostart for `forge --mode gui`). graphical.target is the default in
#     the squashfs payload, so systemd reaches GDM and the liveuser session
#     fires whichever autostart wins.
#   install-tui: this script skips the live-class scaffold. The squashfs
#     payload contains `forge-tui.service` (shipped by `packages/desktop/forge`)
#     gated by `ConditionKernelCommandLine=igos.mode=install-tui`; the unit
#     fires on tty1 once multi-user.target is reached.
info "switching root to /newroot, PID 1 = systemd"
exec switch_root /newroot /sbin/init
