# Secure Boot and MOK on InterGenOS

This guide explains how InterGenOS uses Secure Boot, what the Machine Owner Key (MOK) is for, and what you can expect during install, first boot, and kernel upgrades.

It is written for users who want to understand the boot-chain security model — what is being verified, who signs what, and what happens when something goes wrong. It is not a developer reference; for the signing-ceremony side, see [03 — Automating release signing](../operations/03-automating-signing.md).

## The 30-second version

- InterGenOS requires UEFI Secure Boot. We do not ship a path that disables it.
- The boot chain is: Microsoft CA → Fedora-signed shim → InterGenOS GRUB → InterGenOS kernel (or Unified Kernel Image).
- Every kernel the system boots is signed. Old kernels and recovery kernels too.
- The **release signing key never leaves our hardware**. We sign the live ISO + the install-mode images on our offline workstation; that key never touches your machine.
- When you install InterGenOS to disk, the Forge installer generates a per-machine **Machine Owner Key (MOK)** that lives only on your machine.
- Every kernel you install after the original ISO is rebuilt into a Unified Kernel Image and signed with your own MOK. The InterGenOS release key is never asked to sign anything you produce locally.
- If a kernel signing step fails, GRUB still offers a known-good fallback so you never end up with a system that can't boot.

## Why Secure Boot

The InterGenOS security alignment doctrine treats Secure Boot as mandatory, not optional. The boot path is the most privileged code on any machine — if an attacker can substitute a kernel before the OS finishes starting, every other defense the OS provides is moot. Mandatory verified boot closes that class of attack.

Concretely, we want:

- **Verified boot from firmware**: every executable handed control during boot has its signature checked against a key the firmware (or shim) trusts.
- **No unsigned kernel modules**: a signed kernel that loads unsigned out-of-tree modules at runtime is no better than an unsigned kernel.
- **Reproducible answers** to "what just booted?": every signature in the chain is verifiable after the fact.

Many distributions ship Secure Boot as opt-in and disable it for unsigned third-party packages. InterGenOS does not. The trade-off is that out-of-tree modules (proprietary drivers, custom kernels) require a one-time MOK enrollment step; the benefit is that nothing unsigned ever runs during boot.

## The boot chain

```
   Firmware (UEFI)
        │
        │  trusts Microsoft 3rd-party CA
        ▼
   Fedora-signed shim                  (we piggyback Fedora's shim
        │                               for v1.0; a parallel
        │  trusts InterGenOS vendor     submission produces our own
        │  cert (loaded as MOK)         MS-signed shim for later)
        ▼
   InterGenOS GRUB                     (signed by our HQ key for the
        │                               live ISO + install media,
        │  enforces signature           OR by your local MOK for the
        │  verification on UKIs         GRUB that GRUB writes to your
        ▼                               disk during install)
   InterGenOS UKI                      (vmlinuz + initramfs +
   (or kernel + initramfs)              cmdline, bundled and signed
        │                               as a single Authenticode
        │  Linux kernel handoff         binary by systemd-stub)
        ▼
   InterGenOS userspace
```

A Unified Kernel Image (UKI) is a single signed file that bundles the kernel, the initramfs, and the kernel command-line. Signing the UKI envelope signs all three at once; nothing inside can be swapped without breaking the signature.

The live ISO and install media use UKIs signed by our release key (in our offline signing workstation). Once you install to disk, every kernel you install or upgrade is rebuilt into a UKI on your machine and signed with your machine's MOK.

## What is a MOK?

A Machine Owner Key is a per-machine signing key, generated on your own machine, that the firmware trusts because you enrolled it via MokManager at first boot.

It exists for two reasons:

1. **Your machine signs the kernels you install.** When you install a new kernel (`pkm install linux-kernel-X.Y.Z`), InterGenOS rebuilds the UKI and signs it with your MOK. The InterGenOS release key never sees the kernels you install — it only signs the live ISO + install media that ship from us.
2. **You can trust your own third-party drivers.** If you build out-of-tree modules (e.g., proprietary GPU drivers via DKMS), they can be signed by your MOK and load on a Secure Boot system without disabling enforcement.

The MOK is yours. It lives at `/var/lib/intergen/mok/` on the installed system. If you reinstall, Forge generates a fresh MOK; if you migrate to a new machine, you generate a new MOK there.

## The Forge install flow

When you run the Forge installer (TUI or GUI), the bootloader stage does the following without asking you any further questions:

1. **Generates a per-machine MOK keypair** (RSA-4096 or Ed25519) at `/var/lib/intergen/mok/mok.key` + `/var/lib/intergen/mok/mok.der`.
2. **Generates an enrollment password** (12 characters; random) and stores it for the first-boot enrollment step.
3. **Stages the MOK for enrollment** so MokManager picks it up on the next reboot.
4. **Installs `ukify` and `sbsigntool`** into the target system so the linux-kernel package's post-install hook can build and sign UKIs at kernel install or upgrade time.
5. **Installs an initial UKI** built from the kernel the installer just dropped on the system, signed with the freshly-generated MOK.
6. **Configures a recovery boot entry** that loads the bare vmlinuz with no UKI envelope, as a fallback path if a UKI ever fails to sign or boot.

Forge shows you the enrollment password on the install-complete screen and prints it to the install log at `/var/log/intergen-install.log`. **Write it down before you reboot.** You will need it once, during MokManager enrollment at first boot.

If you forget the password between install and reboot, you can regenerate the MOK from a live ISO before the first successful boot — see [Recovery](#recovery) below.

## First-boot MOK enrollment

On the first boot after install, the firmware notices that there is a pending MOK enrollment request and runs **MokManager** before continuing the boot. MokManager is a small blue-text-on-black-background utility that walks you through three screens:

1. **"Perform MOK management"** — press any key to start.
2. **"Enroll MOK"** — review the certificate that is about to be enrolled. The certificate subject will read `CN=InterGenOS MOK (<machine hostname>)`. Confirm.
3. **"Enter password"** — type the enrollment password Forge gave you. The password is single-use; once enrollment completes, you will not be prompted for it again.

After enrollment, MokManager exits and the system boots into InterGenOS normally. From that point on, the firmware trusts your MOK; UKIs signed with that key load without further prompts.

If you do not enroll the MOK at first boot (for example, you reboot during MokManager and skip the enrollment), the system will still boot using the InterGenOS-release-signed UKI that shipped with the installer image. But the next kernel install or upgrade — which produces a UKI signed with your MOK — will not be trusted by the firmware, and Secure Boot will refuse to load it. You will fall through to the recovery boot entry until you enroll the MOK.

## Kernel install and upgrade

When you install or upgrade a kernel via `pkm install linux-kernel-X.Y.Z` (or any package whose post-install hook touches the kernel), the linux-kernel package's `post_install` hook does the following on your machine, with no key material from InterGenOS HQ:

1. Reads the kernel, the standard initramfs (and an additional FDE initramfs if your system is LUKS-encrypted — see below), and the canonical command-line for your system.
2. Runs `ukify build` to bundle them into a single UKI in the systemd-stub envelope.
3. Runs `sbsign --key /var/lib/intergen/mok/mok.key --cert /var/lib/intergen/mok/mok.der` to sign the UKI.
4. Writes the signed UKI to `/boot/efi/EFI/InterGenOS/igos-<version>.efi` (or the GRUB-compatible path your boot configuration uses).
5. Updates the GRUB menu so the new kernel is the default boot entry.
6. Retains a configurable number of old kernels (default: 2) and their UKIs as fallback entries.

The InterGenOS PIV slot 9c key — the one we use for release signing in our offline workstation — is never asked. It physically does not exist on your machine.

If signing fails (key file corrupt, ESP full, etc.), the linux-kernel post-install hook falls back to the GRUB-loads-vmlinuz path: the kernel and initramfs are written out separately, and GRUB loads them directly with the same signature verification semantics (since GRUB is itself signed and enforces `check_signatures=enforce`). You do not end up with a system that has a half-installed kernel.

## ESP sizing

Because every kernel you install becomes a signed UKI in `/boot/efi`, the ESP needs enough headroom for several generations of kernel. A typical UKI is 80–150 MB depending on the initramfs payload. Forge enforces a minimum ESP size of about 500 MB during partitioning to leave room for at least three kernels.

If your ESP fills up, kernel install will fail. The linux-kernel post-install hook prints a clear message; you can clean up old kernels with `pkm remove linux-kernel-<old-version>` to free space.

## Composition with LUKS encryption

If you chose the encrypted-install option (see the [LUKS scope ratification](../owner-directives.md) — D-001), Forge installs a small FDE-only initramfs alongside the kernel: busybox plus cryptsetup, just enough to prompt for your LUKS passphrase and unlock the root volume before the kernel hands off to the system's userspace.

That FDE initramfs is bundled into the same UKI as the kernel. The UKI signature covers it, just as it covers the kernel and the command-line. There is one signature; verifying the UKI signature verifies the entire boot path including the LUKS unlock prompt.

If you opt for TPM2-sealed unlock (an experimental v1.0 feature), the same UKI envelope holds the additional bits that talk to your TPM. The unlock path is still inside the signed envelope; the TPM is not a way to skip Secure Boot verification.

For non-encrypted installs, the UKI's bundled initramfs is minimal — typically only CPU microcode — because all storage and filesystem drivers are built into the kernel. The bootloader does not need an initramfs to find the root volume.

## Recovery

Most of the time you will never think about any of this. When something goes wrong, you have several recovery paths.

### "I forgot the MOK enrollment password"

Boot a live InterGenOS ISO. Mount the install target's root and ESP. Run:

```sh
# (Operator commands — actual paths will be in a recovery runbook
# referenced from this section once the runbook lands.)
intergenos-recovery regenerate-mok --target /mnt/target
```

This generates a fresh MOK on the target, queues it for enrollment at next boot, and prints a fresh enrollment password. Your existing UKIs (signed with the old MOK) will not validate against the new MOK until the next kernel install or upgrade, but the GRUB-loads-vmlinuz fallback continues to work; the system will boot using the recovery entry while you wait for the next kernel upgrade to rebuild UKIs with the new MOK.

### "MokManager rejected my password"

You may have transposed two digits. MokManager allows three attempts, then reboots. You can try again, or use the password-reset path above.

### "Secure Boot is refusing my new kernel"

This usually means the MOK was not enrolled (or was un-enrolled) but the kernel post-install hook signed a UKI with it. Boot the recovery entry (load the bare vmlinuz directly via GRUB), then either re-enroll the MOK or regenerate it per the password-reset path.

### "I want to run an unsigned kernel for testing"

Don't. The doctrine here is non-negotiable: every kernel that boots is signed. Build your kernel, sign it with your MOK using `sbsign`, and install it via the same pkm flow as everything else. If you need to test bare unsigned kernels, do it in a VM where Secure Boot is off — not on a production install.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Boot stops at MokManager every time | MOK enrollment never completed; firmware re-prompts each boot | Complete enrollment; see [First-boot MOK enrollment](#first-boot-mok-enrollment) above. |
| New kernel installs but won't boot, falls through to recovery | UKI signed with a MOK the firmware doesn't trust | Re-enroll the MOK, or re-run the kernel post-install hook after enrollment. |
| GRUB menu shows only the recovery entry, no UKI entries | UKI signing has been failing silently | Inspect `/var/log/intergen-kernel-postinstall.log` for sign errors. ESP-full and missing MOK key file are the two common causes. |
| `ukify` is missing on the installed system | Forge skipped the UKI tooling install (older install, or install with a different boot path) | `pkm install ukify sbsigntool` and re-run the linux-kernel post-install hook for the current kernel. |
| Secure Boot toggle in firmware is greyed out | Some firmware (especially OEM laptops) makes Secure Boot read-only outside Setup Mode | See your hardware vendor's documentation for entering Setup Mode. InterGenOS does not require Setup Mode for normal operation; the standard MOK enrollment path is sufficient. |

## Further reading

- [Security Defaults](security-defaults.md) — the at-a-glance summary of every default protection InterGenOS enforces.
- [Owner Directives](../owner-directives.md) — the canonical ratifications behind this boot chain (D-002 Fedora-piggyback shim, D-005 user-MOK UKI parity, D-001 LUKS-at-install).
- [03 — Automating release signing](../operations/03-automating-signing.md) — the operator-side documentation for how the live ISO + install media get signed (the upstream side of the boot chain described here).
- [Getting Started](../getting-started.md) — install walkthrough that references the MOK enrollment step in context.
- [Security Policy](../../SECURITY.md) — how to report a vulnerability in any part of this boot chain.
