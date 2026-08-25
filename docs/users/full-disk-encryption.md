# Full Disk Encryption on InterGenOS

This guide explains how InterGenOS encrypts your installed system at rest, what you choose at install time, what the boot prompt looks like, and how to recover when something goes wrong.

It is written for users who want to understand the encryption model: what is being protected, what is not, and where the boundaries are. For the boot-chain signing story that runs *around* the encrypted volume, see [Secure Boot and MOK](secure-boot-and-mok.md). LUKS-at-install ships in v1.0, and the encryption-unlock initramfs is bundled into the same signed UKI as the kernel.

> **Status note (decided 2026-08-11):** the two EXPERIMENTAL unlock methods
> described below — TPM2-sealed unlock and FIDO2-token unlock — are **not
> offered by the installer in this release**. A functionality review
> (2026-08-08) found defects in both: FIDO2 enrollment can abort an install
> that opts in, and the TPM2 sealed key's parent context does not survive a
> reboot, so automatic unlock could never outlast the first power cycle.
> Passphrase encryption is unaffected and fully supported. The sections below
> are retained as design documentation for when the corrected methods return
> after dedicated hardware verification.

## The 30-second version

- Full disk encryption is **opt-in**, not default. The Forge installer asks; you choose.
- The format is **LUKS2** with a passphrase. TPM2-sealed unlock and FIDO2-token unlock are designed as **EXPERIMENTAL** sub-options that compose with the passphrase, but they are **not offered in this release** (see the status note above); the passphrase slot is always the unconditional fallback.
- Forge passes the passphrase to `cryptsetup` over standard input and does not intentionally persist or log it. LUKS stores encrypted keyslot material and key-derivation metadata, not the passphrase itself. Mutable buffers are scrubbed on a best-effort basis; immutable Python string copies cannot be guaranteed erased immediately. There is no InterGenOS-side recovery-key escrow.
- The encrypted volume holds the entire root filesystem. The ESP (the small boot partition) is not encrypted because firmware must read it before the operating system runs. When Secure Boot is enabled it authenticates signed EFI binaries and UKIs; it does not authenticate every file on the ESP.
- At boot, a small InterGenOS-branded prompt asks for your passphrase. Three attempts, then a recovery shell.
- If you lose every credential for every usable keyslot, your data is **gone**. This is by design. There is no project-held universal or recovery key.

## Why encrypt

InterGenOS treats your machine's data as yours. Encryption-at-rest protects the data on the disk from anyone who has the disk but does not have you — a stolen laptop, a discarded SSD, a seized device, a borrowed loaner. It does not protect the data from someone who has both the disk and you (and your passphrase), and it does not protect a running system once you have unlocked it.

The trade-off is small. You type a passphrase once per boot. In exchange, the contents of the disk are an unreadable cryptographic blob to anyone without your passphrase, including us. We made the choice to leave encryption opt-in rather than default so that users who do not want the boot prompt are not forced into it — but we recommend it for any portable device.

## The encryption model

InterGenOS uses **LUKS2** (Linux Unified Key Setup, version 2) as the on-disk encryption format. LUKS2 is the standard Linux full-disk-encryption format; the tooling is `cryptsetup`, the algorithm defaults are modern (AES-256 in XTS mode), and the key-derivation function is `argon2id` (memory-hard, resistant to GPU/ASIC bruteforce).

A LUKS2 volume has:

- **A header**, at the start of the encrypted partition, that holds the encryption metadata, cipher choice, and encrypted volume-key material. LUKS2 supports up to 32 **key slots**, subject to keyslot-area and key-size limits.
- **Key slots**, each independently holding an encrypted wrapping of the volume key protected by one passphrase (or by a TPM-sealed key, or by a FIDO2-derived key). You can have several unlock methods active at once; deleting one slot does not affect the others.
- **The encrypted payload** — your root filesystem.

Your passphrase is run through the key-derivation function to unlock a keyslot; the passphrase itself is not stored in the header. Unlocking loads the volume key into the kernel's dm-crypt path while the mapping is active.

## The Forge install flow

When you run Forge, the TUI presents **Full-disk encryption (LUKS)** and the GUI currently presents **Full-disk encryption (EXPERIMENTAL)**. Both offer **Encrypt the root filesystem with LUKS2**. If you enable it:

1. **Forge prompts for a passphrase** with a confirmation field. Both frontends enforce a non-empty value and a confirm-field match. The GUI surfaces a live strength label as you type; the TUI surfaces the same guidance once you submit. The guidance is a *soft* warning: 8 characters is the floor below which the warning fires (with the explanation "short passphrases fall to dictionary attack quickly even with argon2id KDF cost"), and 12 characters with at least two character classes is the recommended baseline. You can accept a passphrase that fires a soft warning — the installer asks you to confirm, but does not block. You can paste, but most users type — a passphrase you cannot recall under stress is not a passphrase, it is a paperweight.
2. **Forge formats the target partition with LUKS2** using `cryptsetup luksFormat`. Forge forces argon2id with a 1 GiB memory cost, 4 iterations, and 4 threads. It does not pass `--cipher` or `--key-size`; the pinned cryptsetup 2.8.4 currently supplies its compiled-in AES-XTS default. The forced KDF parameters sit between RFC 9106's first-recommended (t=1, m=2 GiB) and second-recommended (t=3, m=64 MiB) profiles.
3. **Forge writes `/etc/crypttab`** on the target system so the boot-time FDE initramfs knows what to unlock. The entry is named `cryptroot` and references the partition by `UUID=`.
4. **Forge writes `/etc/fstab`** with the unlocked ext4 filesystem's UUID as the root mount source. It falls back to `/dev/mapper/cryptroot` only when that UUID cannot be read.
5. **The kernel post-install hook bundles the FDE initramfs into the UKI.** Because of the composition with Secure Boot (see [below](#composition-with-secure-boot)), the unlock prompt lives inside the same signed envelope as the kernel.

The passphrase is sent to `cryptsetup` over standard input, never as a command-line argument. Forge clears its stored value and scrubs mutable buffers on success and failure paths, but Python cannot guarantee immediate erasure of every immutable in-memory string copy. Forge does not intentionally persist the passphrase; the LUKS header stores derived, encrypted keyslot material rather than the passphrase text.

The retained design includes TPM2- and FIDO2-backed keyslots alongside the passphrase. Their controls are hidden in this release; the sections below describe the disabled design, not choices the current installer displays.

### EXPERIMENTAL — TPM2-sealed unlock

If you tick **Unlock with TPM2 (EXPERIMENTAL)**, Forge seals a fresh 32-byte random key against the current measured-boot state, adds that key as an additional LUKS slot, and writes the sealed-blob triplet `{primary.ctx, secret.pub, secret.priv}` to the ESP under `/intergen/tpm2/`. The seal is bound to a PCR0 + PCR7 policy: **PCR0** tracks the firmware (BIOS/UEFI code), and **PCR7** tracks Secure Boot policy state (the db/dbx/KEK/MOK enrollment status). On normal boots the system unlocks automatically without prompting for a passphrase.

This is flagged EXPERIMENTAL for v1.0 because the failure modes are subtle. A firmware update changes PCR0; an MS-signed kernel update that touches your shim/MOK state can change PCR7; a Secure Boot reconfiguration changes PCR7. Any of these invalidates the seal, and the boot falls through to the next configured method (FIDO2, if enrolled) and ultimately to the passphrase prompt. The user-experience cost of *thinking* you are TPM-unlocked and then suddenly being prompted is real, so we are holding this back from a general recommendation until it has more field validation.

The sealed blob written to the ESP is not a secret: without the specific TPM that performed the seal (and with the same PCR0/PCR7 state), the blob is useless. You can copy it for backup if you want, but it cannot be unsealed on any other machine. The passphrase slot remains untouched; if the TPM rejects the unseal, the boot prompt asks for the passphrase exactly as in the passphrase-only flow.

### EXPERIMENTAL — FIDO2-token unlock

If you tick **Unlock with FIDO2 token (EXPERIMENTAL)**, Forge enrolls a credential on a FIDO2 security token (YubiKey, Solo, Nitrokey, etc.) plugged in during the install. Enrollment runs in two interactive steps:

1. **Generate credential**: `fido2-cred -M` creates a new credential on the token. You will be prompted to touch the token when it blinks.
2. **Derive unlock key**: a fresh 32-byte random nonce is generated, `fido2-assert -G --hmac-secret` is invoked with that nonce as salt, and the token returns an HMAC output bound to the credential and the nonce. You will be prompted to touch the token a second time. The HMAC is added as an additional LUKS slot.

Forge writes `{cred_id, stored_nonce}` to the ESP under `/intergen/fido2/`. Neither file is a secret: without the physical token, the credential ID and the salt are useless. The relying-party ID is `intergenos`. At boot, the same `fido2-assert` call against the same nonce yields the same HMAC bytes, which unlock the slot.

This is flagged EXPERIMENTAL for the same family of reasons as the TPM2 path: a lost token, a firmware update on the token that rotates the underlying credential storage, or a token swap all invalidate the slot. The passphrase slot stays in the LUKS header for fallback.

## The boot-time flow

When an encrypted InterGenOS system boots, the path looks like this:

```
   Firmware (UEFI)
        │
        │  Secure Boot signature verification on the UKI
        ▼
   Signed UKI                       (kernel + FDE initramfs + cmdline,
        │                            bundled and signed by your local MOK)
        │
        ▼
   Kernel hands off to /init
        │
        ▼
   InterGenOS — encrypted root unlock
        │
        │  read /etc/crypttab options (field 4) for "tpm2" / "fido2"
        │  mount ESP read-only at /esp (by IGOS_ESP label, fallback scan)
        ▼
   Try TPM2 unlock (if enrolled + /dev/tpmrm0 present)
        │  tpm2_load + tpm2_unseal against PCR0+PCR7 policy
        │  → pipe unsealed bytes to cryptsetup-static --key-file=-
        │  on any failure: fall through
        ▼
   Try FIDO2 unlock (if enrolled + tools available)
        │  wait up to 30s for the token to enumerate
        │  fido2-assert -G --hmac-secret against stored nonce
        │  → pipe HMAC to cryptsetup-static --key-file=-
        │  on any failure: fall through
        ▼
   Passphrase prompt (always the final fallback)
   Enter passphrase: _              (three attempts; the prompt lives in
        │                            installer/init/fde-init.sh and runs
        │  cryptsetup open           inside the signed UKI envelope)
        ▼
   /dev/mapper/cryptroot mounted
        │
        │  switch_root
        ▼
   systemd PID 1 — normal boot continues
```

If neither TPM2 nor FIDO2 was enrolled, the chain skips straight to the passphrase prompt — the unlock path is identical to a passphrase-only install. The prompt you see in that case is plain text:

```
  InterGenOS — encrypted root unlock

Enter passphrase for /dev/<resolved-partition>:
```

Three wrong attempts drops you into a recovery shell with `cryptsetup` available. From the recovery shell you can retry the unlock manually, inspect `/etc/crypttab`, or reboot.

The FDE initramfs is tiny — a static `cryptsetup` binary, a static `busybox`, the `dm_crypt` and `ext4` kernel modules, and the storage drivers needed to see your disk. When TPM2 or FIDO2 unlock is enrolled, the initramfs also carries the static `tpm2-tools` / `fido2-tools` binaries and the corresponding kernel modules (`tpm`/`tpm_tis`/`tpm_crb` for TPM2; `usbhid`/`hid_generic` for FIDO2). It does no logging to disk, no telemetry, no network — its only job is to attempt the unlock chain, mount the root filesystem, and hand off to systemd.

### Debugging the unlock chain

The fde-init script emits journal-grep-friendly prefixes when running EXPERIMENTAL methods. From a recovery shell or post-boot, you can correlate each attempt by searching for the prefix:

| Prefix | What it means |
|---|---|
| `[fde-init][EXPERIMENTAL TPM2]` | TPM2 unlock attempt — outcomes include `skipping` (no `/dev/tpmrm0`, no tools, or no sealed blob on ESP), `attempting unlock via sealed key (PCR0+PCR7)`, `tpm2_load failed`, `tpm2_unseal | cryptsetup failed (PCR drift? broken seal?)`, or `unlock succeeded`. |
| `[fde-init][EXPERIMENTAL FIDO2]` | FIDO2 unlock attempt — outcomes include `skipping` (no tools or no metadata), `attempting unlock — plug your security token + touch when it blinks`, `no FIDO2 token detected within 30s`, a specific diagnostic from the fido2-assert + base64 + cryptsetup pipeline (4 possible variants: subprocess non-zero / output-line-6 empty / base64-decode-or-cryptsetup-open failed / stdin-build failed — see `installer/init/fde-init.sh` for the exact text), or `unlock succeeded`. |
| `[fde-init]` (no `EXPERIMENTAL` suffix) | Passphrase prompt path, the recovery-shell drop, and root-mount errors. |

These messages go to the console during early boot. The current initramfs does not persist them into the post-boot systemd journal.

## Composition with Secure Boot

The encrypted-root and signed-boot stories are designed to compose. They do not interact at runtime except through the FDE initramfs being part of the signed UKI.

A UKI is a single signed file that bundles the kernel, the initramfs, and the kernel command-line. On a non-encrypted install, the UKI's bundled initramfs is minimal or empty — the kernel-builtin storage drivers, `PARTUUID=` rootspec, and `rootwait` are sufficient. On an encrypted install, the UKI's bundled initramfs **is** the FDE initramfs: the same `fde-init.sh` script that prompts you for the passphrase is part of the same signed envelope as the kernel.

The practical consequences:

- **Where Secure Boot enforcement is on and your MOK is enrolled**, an attacker cannot substitute a fake unlock prompt that captures your passphrase: the prompt code is inside the signed UKI, and firmware refuses to load a tampered or resigned one. This protection depends on firmware enforcement, which is optional and ships off by default on current target hardware — with enforcement off, nothing in the firmware rejects a substituted UKI, so an attacker with write access to the EFI system partition can install one that captures the passphrase. Enrolling your MOK and turning Secure Boot on is what closes that path; see [Secure Boot and MOK](secure-boot-and-mok.md).
- A kernel upgrade rebuilds the UKI with the new kernel and a freshly generated FDE initramfs, signed with your machine's MOK. The unlock experience is intended to remain the same.
- On an encrypted R001.2 install, Forge withholds stock bare-vmlinuz entries while `/boot/initramfs.img` is only the placeholder; the MOK-signed UKI is then the only generated InterGenOS entry. Do not assume a UKI signing failure leaves a bootable fallback. Use live media to repair the installation and rebuild the UKI.

For the full signed-boot model, see [Secure Boot and MOK](secure-boot-and-mok.md), particularly the "Composition with LUKS encryption" section there.

## What is and is not protected

**Encrypted at rest:**

- The entire root filesystem, including `/home`, `/var`, `/etc`, `/root`, swap (if you put it on the encrypted volume), and any other partition you place inside the LUKS container.
- Anything written to disk by any program once the system is running and the volume is unlocked.

**Not encrypted:**

- The **ESP** (the small `/boot/efi` partition). The firmware reads this before any operating system runs, so it cannot be encrypted. Its integrity is protected by Secure Boot signature verification on the UKI, not by encryption.
- The **LUKS header** itself is on disk and visible. It tells an observer that the partition is LUKS-encrypted and what cipher is in use. It does not reveal anything about the payload.
- **A running system**. Once you have entered your passphrase and the volume is unlocked, the master key lives in kernel memory and the filesystem is readable to any process with the right permissions. Encryption-at-rest does not replace the operating system's process isolation, user permissions, or AppArmor confinement.

If your threat model includes someone with physical access to your machine *while it is running*, full disk encryption is not the control you are looking for — you want screen lock, suspend-to-RAM with discard-of-keys (not yet a default on InterGenOS), or a powered-off machine.

## Recovery

Most of the time you will never think about any of this. When something goes wrong, you have several recovery paths.

### "I forgot my passphrase"

If no usable keyslot credential remains, your data is gone. We are sorry. LUKS has a volume key, but InterGenOS does not hold it or escrow a recovery credential; there is no back door or service we can offer that will recover it. This is intentional — a recovery channel that we could use is a channel that an attacker could use.

If this happens, boot a live ISO and reinstall. **Back up early and often** is the only mitigation.

### "I want to add a second passphrase"

LUKS2 supports up to 32 key slots, subject to keyslot-area and key-size limits. From a running system, as root:

```sh
cryptsetup luksAddKey /dev/disk/by-uuid/<uuid>
```

You will be prompted for an existing passphrase (to unwrap the master key) and then for the new one. The new passphrase will work on the next boot.

### "I want to remove a key slot"

```sh
cryptsetup luksRemoveKey /dev/disk/by-uuid/<uuid>
```

You will be prompted for the passphrase belonging to the slot you want to remove. The other slots are untouched.

### "I want to back up the LUKS header"

The LUKS header is a small file at the start of the encrypted partition. If it is corrupted (disk-level damage at exactly the wrong offset, or accidental `dd` to the wrong device), the payload becomes unrecoverable even with the correct passphrase, because the encryption metadata lives in the header.

Back it up to a separate medium:

```sh
cryptsetup luksHeaderBackup /dev/disk/by-uuid/<uuid> \
    --header-backup-file /path/to/external/header.bin
```

Store the backup somewhere offline. Anyone with the backup file *and* your passphrase can decrypt your disk; treat it accordingly.

To restore:

```sh
cryptsetup luksHeaderRestore /dev/disk/by-uuid/<uuid> \
    --header-backup-file /path/to/external/header.bin
```

### "A kernel upgrade did not leave a bootable UKI"

R001.2 suppresses the stock bare-vmlinuz entries on an encrypted install when `/boot/initramfs.img` is only the placeholder. Boot live media, unlock and mount the root volume, inspect `/var/log/intergen-kernel-postinstall.log`, and rebuild the signed UKI. A regenerated bare fallback is not claimed bootable until it has passed a real cold boot.

### "Boot drops me to the FDE recovery shell"

Three failed passphrase attempts (or a missing `/etc/crypttab`, or a missing LUKS volume) drop you to a small `busybox` shell with `cryptsetup` available. From there you can:

- Inspect available devices with `cat /proc/partitions` and `blkid`; this initramfs has no udev-created `/dev/disk/by-*` links.
- Retry the unlock and handoff with the applets the initramfs carries:

  ```sh
  cryptsetup open /dev/<partition> cryptroot
  mkdir -p /newroot
  mount /dev/mapper/cryptroot /newroot
  mount --move /proc /newroot/proc
  mount --move /sys /newroot/sys
  mount --move /dev /newroot/dev
  exec switch_root /newroot /sbin/init
  ```
- Reboot with `/bin/busybox reboot -f`; no standalone `reboot` applet link is installed.

The recovery shell has no network access and no logs. It is intentionally minimal.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Boot prompt asks for passphrase but every attempt fails | Wrong passphrase, or keyboard layout differs at boot from inside the OS | Confirm the layout. The FDE initramfs uses the US-QWERTY layout by default; if your passphrase contains layout-sensitive characters, type the QWERTY equivalents. |
| Boot drops straight to the FDE recovery shell with "no LUKS volume specified" | `/etc/crypttab` was not written, or the install did not complete the encryption stage | From the recovery shell, inspect `/etc/crypttab` (`cat /etc/crypttab`). If empty, the install did not enable encryption; boot a live ISO and reinstall. |
| Boot reports that the LUKS volume could not be resolved after 30 seconds | Storage did not enumerate, a required storage driver is absent, or the configured UUID/path is wrong | Run `cat /proc/partitions` and `blkid`, then retry `cryptsetup open` with a concrete `/dev/...` partition. |
| TPM2-sealed unlock falls through to passphrase | PCR0 or PCR7 has changed (firmware update, Secure Boot reconfiguration, shim/MOK update) | Enter the passphrase to boot. The early-boot console carries the specific outcome; the current initramfs does not persist it to the post-boot journal. |
| FIDO2 unlock does not detect the token | Token not plugged in early enough, or token battery dead | The FDE initramfs waits up to 30 seconds for the device to enumerate. Plug the token in before boot. For battery-dead tokens, fall back to the passphrase. The early-boot console carries the outcome. |
| `cryptsetup luksAddKey` says "No key available with this passphrase" | The passphrase you entered does not match any existing slot | Try other passphrases you have set. If none work, you are in the [forgot-passphrase](#i-forgot-my-passphrase) case. |
| The ESP filled up on a kernel upgrade and the new UKI was not written | UKI generation logs ESP-full and skips; the previous kernel's UKI remains the default | There is no version-suffixed kernel package to remove. The installed keep-two helper normally prunes superseded files. After verified old artifacts are safely removed, run `sudo pkm reinstall linux-kernel` and inspect `/var/log/intergen-kernel-postinstall.log`. |

## Further reading

- [Secure Boot and MOK](secure-boot-and-mok.md) — the signed-boot story that wraps the encrypted-unlock story.
- [Security Defaults](security-defaults.md) — the at-a-glance summary of every default protection InterGenOS enforces.
- [Getting Started](../getting-started.md) — install walkthrough that references the encryption opt-in step in context.
- [Security Policy](../../SECURITY.md) — how to report a vulnerability in any part of the encryption path.
