#!/bin/sh
# fde-init.sh — InterGenOS FDE (Full Disk Encryption) initramfs entry point
#
# Loaded by kernel as initrd entry point ONLY on installed systems where
# the root filesystem is wrapped in LUKS2. Wired into the UKI via
# scripts/build-fde-initramfs.sh (Phase D activation; see note below) per
# D-005 Option A's UKI-bundles-FDE-initramfs composition with D-001
# (LUKS-at-install v1.0 ratified opt-in encryption).
#
# Scope: prompt user for LUKS passphrase, open cryptsetup mapping, mount
# the unlocked root, switch_root into it. ~50 lines per D-005's
# implementation-backlog text: "custom busybox + cryptsetup-static; ~50
# lines of init in the spirit of installer/init/init.sh; only built and
# installed for LUKS-enabled installs."
#
# Plain (non-LUKS) installs do NOT use this script. Per 2026-04-09
# ratification (narrowed by D-001/D-005), plain installs boot with
# kernel-builtin storage drivers + PARTUUID + rootwait; the UKI's only
# bundled cpio for those is microcode (intel-ucode.img).
#
# Phase D ACTIVATION DEPENDENCY CHAIN (this script is foundational; the
# below pieces are required for the script to actually run on a user
# system):
#   1. packages/core/cryptsetup-static (NEW PACKAGE — not yet in tree;
#      mirror the packages/core/busybox-static pattern but for
#      cryptsetup, statically linked against json-c + popt +
#      libdevmapper)
#   2. installer/init/build-fde-initramfs.sh (NEW PACKAGER — mirror
#      build-initramfs.sh but bundling cryptsetup-static + dm_crypt +
#      ext4 + storage drivers; fde-init.sh as /init)
#   3. packages/core/linux-kernel/hooks/post-install.sh (EXISTING — extend
#      to detect LUKS install via /etc/crypttab presence; pass the
#      FDE initramfs cpio to ukify --initrd= instead of the plain
#      install's empty/microcode-only initramfs)
#   4. Forge installer/backend/disks.py (EXISTING — wire LUKS opt-in path
#      per D-001's "Opt-in encryption checkbox in Forge" backlog;
#      passphrase capture + LUKS2 format + /etc/crypttab write)

set -e

# ---- Setup pseudo-filesystems ----------------------------------------------
mount -t proc     none /proc
mount -t sysfs    none /sys
mount -t devtmpfs none /dev

# ---- Locate the LUKS volume ------------------------------------------------
# Source-of-truth: /etc/crypttab (Forge writes this at install). Fallback:
# kernel cmdline `cryptdev=` (operator override during boot).
CRYPT_NAME=cryptroot
CRYPT_DEV=$(awk -v n="$CRYPT_NAME" '$1==n {print $2}' /etc/crypttab 2>/dev/null || true)

if [ -z "$CRYPT_DEV" ]; then
    for arg in $(cat /proc/cmdline); do
        case "$arg" in
            cryptdev=*) CRYPT_DEV="${arg#cryptdev=}" ;;
        esac
    done
fi

if [ -z "$CRYPT_DEV" ]; then
    echo "[fde-init] FATAL: no LUKS volume specified (/etc/crypttab + cryptdev= cmdline both empty)"
    echo "[fde-init] Dropping to recovery shell. Type 'exit' to retry init."
    exec /bin/sh
fi

# Resolve PARTUUID/UUID/LABEL forms via /dev/disk/by-*
case "$CRYPT_DEV" in
    PARTUUID=*) CRYPT_DEV="/dev/disk/by-partuuid/${CRYPT_DEV#PARTUUID=}" ;;
    UUID=*)     CRYPT_DEV="/dev/disk/by-uuid/${CRYPT_DEV#UUID=}" ;;
    LABEL=*)    CRYPT_DEV="/dev/disk/by-label/${CRYPT_DEV#LABEL=}" ;;
esac

# Wait briefly for the device node to appear (udev-less environment)
i=0
while [ ! -e "$CRYPT_DEV" ] && [ "$i" -lt 30 ]; do
    sleep 1
    i=$((i + 1))
done
if [ ! -e "$CRYPT_DEV" ]; then
    echo "[fde-init] FATAL: LUKS volume $CRYPT_DEV not found after 30s wait"
    exec /bin/sh
fi

# ---- Read /etc/crypttab options field (field 4) ----------------------------
# D-001 EXPERIMENTAL wiring (operator Option A 2026-05-18T22:52Z):
# crypttab field 4 carries a comma-separated options list. The "tpm2"
# token enables the TPM2 sealed-key unlock attempt; "fido2" enables
# the FIDO2 hmac-secret unlock attempt. Both are EXPERIMENTAL per
# D-001 ratified scope. Default (no tokens) = passphrase only, same
# behavior as Phase D foundational artifact.
CRYPT_OPTS=$(awk -v n="$CRYPT_NAME" '$1==n {print $4}' /etc/crypttab 2>/dev/null || true)
USE_TPM2="no"
USE_FIDO2="no"
case ",$CRYPT_OPTS," in
    *,tpm2,*)  USE_TPM2="yes" ;;
esac
case ",$CRYPT_OPTS," in
    *,fido2,*) USE_FIDO2="yes" ;;
esac

# ---- Mount ESP for sealed-key metadata (TPM2 / FIDO2 paths only) -----------
# fde-init.sh classically does not touch the ESP — passphrase unlock has
# no metadata to read. EXPERIMENTAL unlock methods store small public-
# only metadata on ESP at /intergen/tpm2/ + /intergen/fido2/. The files
# are NOT secrets: a TPM2 sealed blob is useless without the TPM that
# sealed it; a FIDO2 credential+nonce is useless without the physical
# token. Mount ESP read-only at /esp, read metadata, leave mounted until
# switch_root (systemd will re-mount at /boot/efi via fstab post-handoff).
ESP_MOUNT=/esp
mkdir -p "$ESP_MOUNT"

mount_esp_readonly() {
    # Strategy: try canonical label IGOS_ESP first (D-001/I-E sets this on
    # fresh installs via mkfs.fat -F32 -n IGOS_ESP). Fallback: scan FAT32
    # partitions for /intergen/ subdir (handles pre-label installs +
    # operator-renamed media).
    dev=$(blkid -L IGOS_ESP 2>/dev/null)
    if [ -n "$dev" ]; then
        if mount -t vfat -o ro "$dev" "$ESP_MOUNT" 2>/dev/null; then
            return 0
        fi
    fi
    for d in /dev/sd[a-z][0-9]* /dev/vd[a-z][0-9]* \
             /dev/nvme[0-9]n[0-9]p[0-9]* /dev/mmcblk[0-9]p[0-9]*; do
        [ -b "$d" ] || continue
        if mount -t vfat -o ro "$d" "$ESP_MOUNT" 2>/dev/null; then
            if [ -d "$ESP_MOUNT/intergen" ]; then
                return 0
            fi
            umount "$ESP_MOUNT" 2>/dev/null
        fi
    done
    return 1
}

# ---- TPM2 unlock attempt (EXPERIMENTAL) ------------------------------------
# Reads ESP-side sealed-key blob {primary.ctx, secret.pub, secret.priv},
# runs tpm2_load + tpm2_unseal against the system's TPM2 device with
# PCR0+PCR7 policy (firmware + Secure Boot state). Emits the unsealed
# secret on stdout via process substitution, piped to cryptsetup-static
# --key-file=-. Falls through on ANY failure (no chip, broken seal,
# wrong PCRs from firmware update, missing tools, etc.) — never gates
# the boot.
try_tpm2_unlock() {
    [ "$USE_TPM2" = "yes" ] || return 1
    # Load TPM2 modules — bundled in I-D extension to build-fde-initramfs.sh.
    # Failures are non-fatal (modprobe may report missing module on a host
    # without TPM hardware or on initramfs predating I-D — the /dev/tpmrm0
    # check below catches the absence either way).
    for mod in tpm tpm_tis tpm_crb; do
        modprobe "$mod" 2>/dev/null || true
    done
    [ -c /dev/tpmrm0 ] || {
        echo "[fde-init][EXPERIMENTAL TPM2] /dev/tpmrm0 not present — skipping"
        return 1
    }
    if ! command -v tpm2_unseal >/dev/null 2>&1; then
        echo "[fde-init][EXPERIMENTAL TPM2] tpm2-tools-static not in initramfs — skipping"
        return 1
    fi
    if ! mount_esp_readonly; then
        echo "[fde-init][EXPERIMENTAL TPM2] could not mount ESP — skipping"
        return 1
    fi
    tpm2_dir="$ESP_MOUNT/intergen/tpm2"
    for f in primary.ctx secret.pub secret.priv; do
        if [ ! -f "$tpm2_dir/$f" ]; then
            echo "[fde-init][EXPERIMENTAL TPM2] missing $tpm2_dir/$f — skipping"
            return 1
        fi
    done

    echo "[fde-init][EXPERIMENTAL TPM2] attempting unlock via sealed key (PCR0+PCR7)"
    # tpm2_load needs a context output for the loaded object; tpm2_unseal
    # then reads from that loaded object and emits the unsealed bytes.
    if ! tpm2_load -C "$tpm2_dir/primary.ctx" \
                   -u "$tpm2_dir/secret.pub" \
                   -r "$tpm2_dir/secret.priv" \
                   -c /tmp/sealed.ctx >/dev/null 2>&1; then
        echo "[fde-init][EXPERIMENTAL TPM2] tpm2_load failed — falling through"
        return 1
    fi
    # tpm2_unseal needs PCR-policy auth since the seal was created with
    # --policy=<pcr_policy> at install time (disks.py:_tpm2_seal_random_key
    # tpm2_create --policy={PCR_POLICY}). Without -p pcr:..., tpm2_unseal
    # returns policy-check failure on PCR-bound objects regardless of
    # whether the live PCRs actually match. Per tpm2-tools man page
    # canonical PCR-policy unseal example: `tpm2_unseal -c seal.ctx -p
    # pcr:sha256:0,1,2,3` — the -p arg satisfies the policy directly
    # without needing a separate tpm2_startauthsession at unseal time.
    #
    # PCR set MUST match install-time TPM2_SEAL_PCRS constant in
    # installer/backend/disks.py:120 ("sha256:0,7" = PCR0 firmware +
    # PCR7 Secure Boot state).
    if tpm2_unseal -c /tmp/sealed.ctx -p "pcr:sha256:0,7" \
            | cryptsetup open --key-file=- "$CRYPT_DEV" "$CRYPT_NAME" 2>/dev/null; then
        echo "[fde-init][EXPERIMENTAL TPM2] unlock succeeded"
        rm -f /tmp/sealed.ctx 2>/dev/null || true
        return 0
    fi
    echo "[fde-init][EXPERIMENTAL TPM2] tpm2_unseal | cryptsetup failed (PCR drift? broken seal?) — falling through"
    rm -f /tmp/sealed.ctx 2>/dev/null || true
    return 1
}

# ---- FIDO2 unlock attempt (EXPERIMENTAL) -----------------------------------
# Reads ESP-side {cred_id, stored_nonce}, waits up to 30s for a USB
# FIDO2 token to enumerate, runs fido2-assert with hmac-secret extension
# using the stored nonce. The HMAC output is the LUKS keyslot key
# (added at install time by D-001/I-E fido2_enroll_token). Operator
# must TOUCH the token within the assertion timeout (token vendor's
# user-presence timer; typically 30-60s). Falls through on any failure.
try_fido2_unlock() {
    [ "$USE_FIDO2" = "yes" ] || return 1
    # Load USB-HID modules — bundled in I-D extension to
    # build-fde-initramfs.sh. Without these, FIDO2 USB tokens don't
    # enumerate as /dev/hidraw* devices.
    for mod in usbhid hid_generic; do
        modprobe "$mod" 2>/dev/null || true
    done
    if ! command -v fido2-assert >/dev/null 2>&1; then
        echo "[fde-init][EXPERIMENTAL FIDO2] fido2-tools-static not in initramfs — skipping"
        return 1
    fi
    if ! mount_esp_readonly; then
        echo "[fde-init][EXPERIMENTAL FIDO2] could not mount ESP — skipping"
        return 1
    fi
    fido2_dir="$ESP_MOUNT/intergen/fido2"
    for f in cred_id stored_nonce; do
        if [ ! -f "$fido2_dir/$f" ]; then
            echo "[fde-init][EXPERIMENTAL FIDO2] missing $fido2_dir/$f — skipping"
            return 1
        fi
    done

    echo "[fde-init][EXPERIMENTAL FIDO2] attempting unlock — plug your security token + touch when it blinks"
    # Wait up to 30s for a FIDO2 token /dev/hidraw* to appear (the
    # libfido2 enumerate path watches for HID class FIDO devices).
    i=0
    token_dev=""
    while [ "$i" -lt 30 ]; do
        token_dev=$(fido2-token -L 2>/dev/null | head -1 | awk '{print $1}')
        [ -n "$token_dev" ] && break
        sleep 1
        i=$((i + 1))
    done
    if [ -z "$token_dev" ]; then
        echo "[fde-init][EXPERIMENTAL FIDO2] no FIDO2 token detected within 30s — falling through"
        return 1
    fi

    # fido2-assert hmac-secret call. Per libfido2 fido2-assert(1) man page
    # (Yubico/libfido2 main man/fido2-assert.1), input format with -h /
    # --hmac-secret is 4 stdin lines:
    #   1. client data hash (base64 blob)
    #   2. relying party id (UTF-8 string)
    #   3. credential id (base64 blob)
    #   4. hmac salt (base64 blob)
    # Output format is 7 lines, NO field prefixes, all base64 (except
    # line 2 = RP id UTF-8). Line 6 (1-indexed) is the hmac secret
    # (base64 blob).
    #
    # Earlier implementation had THREE defects: missing --hmac-secret
    # flag (extension never invoked); stdin was raw nonce file (single
    # line, wrong shape); output parsed via `awk /^hmac-secret/` +
    # `xxd -r -p` (no such prefix exists + output is base64 not hex).
    # Result: FIDO2 unlock fell through silently at every boot. Fixed
    # per windows-docs-coordinator 2026-05-19T01:35:56Z FDE self-audit
    # + verbatim libfido2 man-page re-fetch.
    cdh_b64=$(head -c 32 /dev/urandom | base64 | tr -d '\n')
    cred_id_b64=$(base64 -w 0 < "$fido2_dir/cred_id")
    nonce_b64=$(base64 -w 0 < "$fido2_dir/stored_nonce")
    if [ -z "$cdh_b64" ] || [ -z "$cred_id_b64" ] || [ -z "$nonce_b64" ]; then
        echo "[fde-init][EXPERIMENTAL FIDO2] failed to build fido2-assert stdin (empty cdh/cred_id/nonce) — falling through"
        return 1
    fi

    fido2_out=$(printf '%s\n%s\n%s\n%s\n' \
            "$cdh_b64" "intergenos" "$cred_id_b64" "$nonce_b64" \
        | fido2-assert -G --hmac-secret -h "$token_dev" 2>/dev/null) || {
        echo "[fde-init][EXPERIMENTAL FIDO2] fido2-assert returned non-zero (token-not-touched? wrong token? firmware changed?) — falling through"
        return 1
    }

    # Output line 6 (0-indexed via sed: line 6) = base64 hmac secret.
    # Defensive: verify output has at least 6 lines + that line 6 is
    # non-empty before piping to base64 -d → cryptsetup.
    hmac_b64=$(printf '%s\n' "$fido2_out" | sed -n '6p')
    if [ -z "$hmac_b64" ]; then
        line_count=$(printf '%s\n' "$fido2_out" | wc -l)
        echo "[fde-init][EXPERIMENTAL FIDO2] fido2-assert output line 6 empty (got $line_count lines; expected >=6 with --hmac-secret) — falling through"
        return 1
    fi

    # base64 -d + cryptsetup open in a single pipeline so cryptsetup
    # never sees an empty stream (which would be a non-failure no-op
    # before triggering the passphrase fallback).
    if printf '%s' "$hmac_b64" | base64 -d 2>/dev/null \
            | cryptsetup open --key-file=- "$CRYPT_DEV" "$CRYPT_NAME" 2>/dev/null; then
        echo "[fde-init][EXPERIMENTAL FIDO2] unlock succeeded"
        return 0
    fi
    echo "[fde-init][EXPERIMENTAL FIDO2] base64 decode or cryptsetup open failed (wrong token? slot mismatch?) — falling through"
    return 1
}

# ---- Unlock attempt chain: TPM2 → FIDO2 → passphrase ----------------------
# D-001 EXPERIMENTAL unlock methods run BEFORE the interactive
# passphrase prompt so successful TPM2/FIDO2 unlocks skip the prompt
# entirely. ANY failure of an EXPERIMENTAL method falls through to the
# next one; passphrase is always the final fallback. Passphrase remains
# the only NON-experimental path + the only path that requires no
# extra hardware/software state on the system.
echo ""
echo "  InterGenOS — encrypted root unlock"
echo ""

UNLOCKED="no"
if try_tpm2_unlock; then
    UNLOCKED="yes"
elif try_fido2_unlock; then
    UNLOCKED="yes"
fi

# Unmount ESP if we mounted it (TPM2 / FIDO2 paths). systemd will
# re-mount it at /boot/efi post-switch_root via fstab.
if mountpoint -q "$ESP_MOUNT" 2>/dev/null; then
    umount "$ESP_MOUNT" 2>/dev/null || true
fi

if [ "$UNLOCKED" = "no" ]; then
    # cryptsetup open prompts on /dev/tty by default; passphrase entry is
    # interactive. Max 3 attempts before falling through to recovery shell.
    attempts=0
    until cryptsetup open "$CRYPT_DEV" "$CRYPT_NAME"; do
        attempts=$((attempts + 1))
        if [ "$attempts" -ge 3 ]; then
            echo "[fde-init] FATAL: 3 failed passphrase attempts."
            echo "[fde-init] Dropping to recovery shell. Type 'cryptsetup open $CRYPT_DEV $CRYPT_NAME' to retry."
            exec /bin/sh
        fi
        echo "[fde-init] Wrong passphrase. $((3 - attempts)) attempts remaining."
    done
fi

# ---- Mount the unlocked root + handoff -------------------------------------
mkdir -p /newroot
if ! mount "/dev/mapper/$CRYPT_NAME" /newroot; then
    echo "[fde-init] FATAL: mount /dev/mapper/$CRYPT_NAME -> /newroot failed"
    exec /bin/sh
fi

mount --move /proc /newroot/proc
mount --move /sys  /newroot/sys
mount --move /dev  /newroot/dev

# Handoff to systemd (or whatever /sbin/init resolves to on the rootfs)
exec switch_root /newroot /sbin/init
