#!/bin/bash
# nvidia post-install — runs at `pkm install nvidia` time on the live user
# system after pkm extracts the archive. Fires from pkm/installer.py:854
# against /var/lib/pkm/hooks/nvidia/post-install.
#
# Operational sequence:
#   1. Hardware check — Turing+ NVIDIA GPU present? Else refuse install.
#   2. Kernel-version detection — pick the running kernel ($(uname -r)).
#   3. Build the open-gpu-kernel-modules against the user's installed
#      kernel headers + sign each .ko with the per-machine MOK + install
#      signed modules to /lib/modules/<kver>/extra/nvidia/.
#   4. depmod + try modprobe -r nouveau (graceful: warn-and-continue
#      if nouveau is in-use by the framebuffer console).
#
# This hook is non-fatal per pkm/installer.py — its exit status is logged
# but does not roll back the archive deploy. The user can re-run the
# rebuild manually via /var/lib/pkm/hooks/nvidia/rebuild-modules.

set -uo pipefail

LOGFILE="${NVIDIA_HOOK_LOG:-/var/log/intergen-nvidia-postinstall.log}"
mkdir -p "$(dirname "$LOGFILE")" 2>/dev/null || true

# Where this package's hooks, the kernel's hook and the signing helper live,
# and the log above. Overridable so the preflight tests can run this script
# against stand-ins; on a machine these are the paths the packages install. The
# package manager starts this hook with an environment stripped to a fixed
# list that names none of them.
NVIDIA_HOOK_DIR="${NVIDIA_HOOK_DIR:-/var/lib/pkm/hooks/nvidia}"
KERNEL_HOOK="${KERNEL_HOOK:-/var/lib/pkm/hooks/linux-kernel/post-install}"
MOK_HELPER="${MOK_HELPER:-/usr/lib/intergen/mok-signing.sh}"

log() {
    local msg="[nvidia:post-install] $*"
    echo "$msg" >&2
    echo "$(date -u +'%Y-%m-%dT%H:%M:%SZ') $msg" >> "$LOGFILE" 2>/dev/null || true
}

log "starting (PKM package: ${PKM_PACKAGE_NAME:-?} ${PKM_PACKAGE_VERSION:-?})"

# The signing helper, for the one passphrase entry this install asks for (step
# 2b). Shipped by the kernel package; without it nothing here can be signed,
# which is a refusal (the helper's status, 77), not a warning.
if [ -r "$MOK_HELPER" ]; then
    # shellcheck source=/dev/null
    . "$MOK_HELPER"
else
    log "FATAL: $MOK_HELPER is missing, so the driver's modules and the boot image cannot be signed."
    log "  Nothing was built. Restore it with: sudo pkm reinstall linux-kernel, then: sudo pkm reinstall nvidia"
    exit 77
fi

# Step 1: hardware check
if [ -x "$NVIDIA_HOOK_DIR/check-hardware.sh" ]; then
    if ! "$NVIDIA_HOOK_DIR/check-hardware.sh" 2>&1 | tee -a "$LOGFILE" >&2; then
        log "FATAL: hardware check failed — no Turing+ NVIDIA GPU detected on this system"
        log "Aborting module rebuild. nouveau (already in kernel) handles pre-Turing NVIDIA hardware."
        log "If this is wrong (e.g. lspci returned stale data), re-run $NVIDIA_HOOK_DIR/rebuild-modules manually."
        exit 1
    fi
fi

# Step 2: kernel-version detection. uname -r gives the running kernel; in
# the Forge-install chroot context this resolves to the host kernel which
# is wrong. We probe /lib/modules/ for the InterGenOS kernel name pattern.
KVER=""
if [ -n "${PKM_PACKAGE_ROOT:-}" ] && [ "$PKM_PACKAGE_ROOT" = "/" ]; then
    # Live-system install — uname -r is reliable.
    KVER=$(uname -r)
fi
# Fallback: the one staged InterGenOS kernel (<version>-igos-<release>) that
# has a prepared build tree, through the shared helper — which refuses to guess
# when more than one is staged. (The earlier glob *-igos matched no real
# directory; independent review 2026-09-11.)
. "$(dirname "$(readlink -f "$0")")/kernel-paths.sh"
if [ -z "$KVER" ] || [ ! -d "/lib/modules/$KVER/build" ]; then
    if ! KVER=$(nvidia_kver_from_modules /lib/modules); then
        log "FATAL: more than one staged kernel under /lib/modules has a build tree; refusing to guess."
        log "Pass the kernel explicitly: $NVIDIA_HOOK_DIR/rebuild-modules <version>-igos-<release>"
        exit 1
    fi
fi

if [ -z "$KVER" ]; then
    log "FATAL: no kernel with /lib/modules/<ver>/build/ symlink found"
    log "linux-kernel-pass2 must be installed before nvidia can rebuild modules."
    exit 1
fi

log "rebuilding nvidia modules for kernel $KVER"

if [ ! -d "/lib/modules/$KVER/build" ]; then
    log "FATAL: /lib/modules/$KVER/build missing — linux-kernel-pass2 not properly installed"
    exit 1
fi

# Step 2b: ONE passphrase entry for this whole install. The driver signs five
# modules here and the kernel's hook then signs the boot image that carries the
# driver's cmdline; each signer is a process of its own, and each used to ask
# the owner again — six entries for one install, then a raw prompt from the
# boot-image signer (measured on a real install, 2026-10-16). Resolved once
# here, before the long compile, and held for every step this hook starts
# (mok-signing.sh rule 5). No key at all is left to the signers, which refuse
# with their own reason.
if [ -f "$MOK_KEY" ] && [ -f "$MOK_CERT" ]; then
    if ! mok_key_is_encrypted "$MOK_KEY"; then
        if ! mok_migrate_plain_key "$MOK_KEY"; then
            log "REFUSED: the signing key has no passphrase and none was set, so nothing was built"
            log "  or signed. To finish it: sudo pkm reinstall nvidia"
            exit "$MOK_REFUSED_EXIT"
        fi
    elif ! mok_resolve_passphrase "$MOK_KEY" "the NVIDIA driver and the boot image for kernel $KVER"; then
        log "REFUSED: no usable signing passphrase, so nothing was built or signed."
        log "  To finish it with the signing key passphrase in hand (the one set at install,"
        log "  not the MOK enrollment password): sudo pkm reinstall nvidia"
        exit "$MOK_REFUSED_EXIT"
    fi
fi

# Step 3: invoke the rebuild (factored so the linux-kernel hook can call
# the same code path on kernel upgrade — see hooks/rebuild-modules.sh).
# A refused signing keeps its status on the way out, so the package manager
# reports this install as not finished.
"$NVIDIA_HOOK_DIR/rebuild-modules" "$KVER"
REBUILD_RC=$?
if [ "$REBUILD_RC" -ne 0 ]; then
    log "WARNING: rebuild-modules exited $REBUILD_RC for kernel $KVER"
    log "Modules may not load on next boot. Diagnose with:"
    log "  cat $LOGFILE"
    log "  $NVIDIA_HOOK_DIR/rebuild-modules $KVER"
    if [ "$REBUILD_RC" -eq "$MOK_REFUSED_EXIT" ]; then
        exit "$MOK_REFUSED_EXIT"
    fi
    exit 1
fi

# Step 4: try to evict nouveau if currently loaded. Best-effort: nouveau
# is in-use when the framebuffer console is active, in which case unload
# fails with EBUSY and the user needs to reboot anyway for nvidia-drm to
# take over the framebuffer.
if lsmod 2>/dev/null | grep -q '^nouveau'; then
    log "nouveau is loaded — attempting modprobe -r nouveau"
    if modprobe -r nouveau 2>/dev/null; then
        log "nouveau unloaded"
    else
        log "nouveau is in-use (likely by the framebuffer console) — reboot required for nvidia to load"
    fi
fi

# Step 5: trigger UKI rebuild so /etc/kernel/cmdline.d/40-nvidia.conf
# (shipped by this package — nvidia-drm.modeset=1 + nvidia-drm.fbdev=1)
# gets merged into the signed UKI .cmdline section. linux-kernel post-
# install reads cmdline.d fragments at rebuild time. Without this trigger,
# the cmdline params don't take effect until the next kernel update.
# Skip in chroot install context — no UKI on the build VM.
#
# A boot image that was not rebuilt is a FAILURE of this install, not a note.
# Without nvidia-drm.modeset=1 in the signed cmdline the driver loads with KMS
# off and every output wired to the NVIDIA GPU stays dark, so a machine that
# reboots here reboots into a dead HDMI port. This used to log a warning and
# exit 0: the package reported installed and the next step said reboot
# (measured on a real install, 2026-10-16: the boot-image signer refused the
# key, modeset read N after the reboot). The kernel's hook says why and how to
# finish; this passes its status on.
if [ -n "${PKM_PACKAGE_ROOT:-}" ] && [ "$PKM_PACKAGE_ROOT" = "/" ]; then
    if [ -x "$KERNEL_HOOK" ]; then
        log "triggering UKI rebuild via $KERNEL_HOOK so cmdline.d/40-nvidia.conf takes effect"
        if "$KERNEL_HOOK" 2>&1 | tee -a "$LOGFILE" >&2; then
            log "UKI rebuild complete — nvidia cmdline params active on next boot"
        else
            KERNEL_HOOK_RC=${PIPESTATUS[0]}
            log "REFUSED: the boot image was NOT rebuilt (the kernel hook exited $KERNEL_HOOK_RC; its reason"
            log "  is above), so the driver's cmdline is not in the image this machine boots and the"
            log "  outputs wired to the NVIDIA GPU would stay dark. Do not reboot for the driver yet."
            log "  To finish it with the signing key passphrase in hand (the one set at install,"
            log "  not the MOK enrollment password): sudo pkm reinstall linux-kernel"
            if [ "$KERNEL_HOOK_RC" -eq "$MOK_REFUSED_EXIT" ]; then
                exit "$MOK_REFUSED_EXIT"
            fi
            exit 1
        fi
    else
        log "$KERNEL_HOOK absent — UKI rebuild skipped (cmdline params take effect on next kernel update)"
    fi
else
    log "chroot install context — skipping UKI rebuild trigger (no UKI on build VM)"
fi

log "post-install complete for kernel $KVER"
exit 0
