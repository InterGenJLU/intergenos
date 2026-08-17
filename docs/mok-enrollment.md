# MOK (Machine Owner Key) Enrollment Runbook

**Audience:** end users installing InterGenOS on Secure Boot hardware, and reviewers verifying our key-management posture.
**Scope:** the full path from a freshly-installed InterGenOS system to a working MOK-enrolled keyring that DKMS / out-of-tree modules can chain against.
**Last updated:** 2026-07-25
**Status:** v1 — gating doc for the 2026-05-14 first-light trigger. Real-hardware validation against the validation-target build's ISO output performed before promotion.

This document is the canonical end-user procedure for MOK enrollment on InterGenOS. Companion docs:
- [docs/ephemeral-module-signing.md](ephemeral-module-signing.md) — how in-tree modules are signed (different key, different lifetime)
- [docs/shim-review-submission.md](shim-review-submission.md) — the full Secure Boot chain context
- [docs/signing-procedure.md](signing-procedure.md) — distro-side release-signing operational procedure
- [installer/backend/mok.py](../installer/backend/mok.py) — the install-time MOK provisioning code

---

## 0. The enrollment procedure, in three steps

MOK enrollment spans your firmware settings and the installer, and the **order matters**.
These three steps are the supported procedure; everything else in this document assumes it.

1. **Before you install, turn Secure Boot OFF in UEFI firmware setup.** The installer
   signs the installed system's boot chain with a Machine Owner Key your firmware does
   not trust yet, so the install itself runs with Secure Boot disabled.

2. **During installation, in Forge, turn on Secure Boot enrollment and set a MOK
   enrollment password.** On the Forge account page, the **Secure Boot enrollment**
   section carries a **MOK enrollment password** field. Entering a password is what turns
   enrollment on; leaving the field empty skips enrollment entirely. **You choose the
   password** — Forge never generates one, never displays one back, and never writes it
   to a log. Remember what you set; you need it in step 3.

3. **On the first reboot after Forge finishes, turn Secure Boot back ON in UEFI firmware
   setup.** Re-enabling Secure Boot is what **triggers** enrollment: the firmware loads
   shim, shim finds the enrollment your install staged, and MokManager asks for the
   password you set in step 2. Entering it enrolls your MOK into the firmware's key store.

Enrollment happens once. After it completes, the machine boots normally with Secure Boot
enforcing, and every subsequent kernel UKI your machine signs with that MOK is trusted.

---

## 1. Overview + threat model

### What a MOK is

A **Machine Owner Key** is an X.509 keypair that lives on *your* machine. The Forge installer generates one when you install InterGenOS, and at first boot you enroll its public half into the UEFI firmware's MOK list. Once enrolled, the kernel trusts modules signed by your MOK's private half.

This matters because InterGenOS ships with `CONFIG_MODULE_SIG_FORCE=y` — modules whose signature doesn't verify against a trusted key are refused. That includes any third-party module DKMS might build on your system (e.g., the NVIDIA proprietary driver, ZFS, VirtualBox host modules). Without a MOK, those modules cannot load.

### Which key is which

InterGenOS deals with four distinct keys. Confusing them is the most common source of MOK-enrollment user error.

| # | Key | Whose | Used for | Where it lives |
|---|---|---|---|---|
| 1 | Distro GPG | InterGenOS | Signs the package repo index + releases | Hardware tokens (NK#1/NK#2); never on your machine |
| 2 | Distro EFI X.509 | InterGenOS | Signs the **live/repo** `grubx64.efi` (the ISO + mirror build-ceremony binary) via `sbsign` — NOT the installed grub (re-signed per-machine with your MOK) and NOT vmlinuz (MOK-signed) | Hardware token PIV slot 9c; never on your machine |
| 3 | Kernel module-signing X.509 | InterGenOS, per-kernel-build | Signs in-tree `.ko` files for one specific kernel image | Ephemeral; embedded into the kernel it signs, then discarded |
| 4 | **MOK (this document)** | **You** | **Signs DKMS / out-of-tree modules on your machine** | **`/var/lib/intergen/mok/` on your machine, never anywhere else** |

The MOK is *yours*. The InterGenOS project has no copy and cannot recover it. If you wipe `/var/lib/intergen/mok/` you must re-enroll a new MOK.

### What MOK enrollment protects against

- **Unsigned-module loading from a compromised account on your system.** Even with root, an attacker cannot load arbitrary kernel modules — they would need a key your kernel trusts.
- **Pre-boot tampering.** The MOK is enrolled into UEFI firmware variables; a physically-present attacker with shim password access can enroll a different MOK, but cannot enroll one silently.
- **Module substitution from network sources.** Modules pulled from third-party repos must be signed by your MOK to load.

### What MOK enrollment does NOT protect against

- **A physically-present attacker who can boot from another medium.** They can read the MOK private half from `/var/lib/intergen/mok/mok.key` if the disk is not encrypted at rest.
- **Firmware-level attacker.** A bug in UEFI implementation, or an SMM-level rootkit, sits below the MOK trust boundary.
- **Compromised in-tree kernel.** If a vulnerability lets attacker code run in kernel context, module signing is bypassed by definition.
- **Targeted social engineering of the enrollment flow.** A user who blindly enrolls a third party's MOK along with their own loses the boundary entirely.

The MOK is the boundary between "anyone with root can load arbitrary kernel code" (no MOK) and "kernel code must be signed by a key the firmware was told to trust" (MOK enrolled). It is not a substitute for full-disk encryption, and it is not a substitute for hardware-rooted Secure Boot.

---

## 2. The boot chain being enrolled into

InterGenOS uses a UKI-parity boot architecture for installed systems per requirement D-005 (installed-system boot architecture, UKI parity signed by the user's MOK). The user's machine-local MOK signs Unified Kernel Images (UKIs) at kernel install + upgrade time; the InterGenOS distro EFI X.509 PIV slot 9c key stays at HQ and never leaves it. The trust-chain at HEAD:

```
UEFI firmware (verifies against its built-in cert store + MOK list)
        │
        ▼
shim-x64.efi  ──── signed by Microsoft 2011/2023 UEFI CA
        │           Embeds InterGenOS vendor cert (CN=InterGenOS Secure Boot CA)
        │
        ▼
grubx64.efi   ──── signed by YOUR MOK private half (re-signed at install time)
        │           Verified by shim against your enrolled MOK list
        │           Forge rebuilds grub with SBAT and re-signs it per-machine
        │           (installer/backend/bootloader.py _install_signed_efi_chain).
        │           On the LIVE ISO / mirror this same grub is distro-PIV-signed
        │           (slot 9c, HQ-only) and shim-verified against the vendor cert.
        │
        ▼
UKI (primary) ──── signed by YOUR MOK private half (this document)
        │           Verified by shim against your enrolled MOK list
        │           Built by `ukify` at kernel install/upgrade per D-005;
        │           bundles vmlinuz + initramfs + cmdline + microcode in
        │           a single PE envelope. linux-kernel/hooks/post-install.sh
        │           is the canonical signing path.
        │
        │  (Recovery fallback path if UKI signing fails — also user-MOK-signed)
        ▼
vmlinuz       ──── signed by YOUR MOK private half (recovery only)
        │           Verified by shim against your enrolled MOK list
        │           Authored by installer/backend/bootloader.py at install time
        │           as the grub-loads-vmlinuz recovery fallback per D-005
        │           recovery semantics.
        │
        ▼
in-tree .ko   ──── signed by ephemeral per-build kernel module key
        │           Verified by kernel against .builtin_trusted_keys
        │
        ▼
DKMS .ko      ──── signed by YOUR MOK private half (this document)
                    Verified by kernel against .secondary_trusted_keys
                    (enabled by CONFIG_SECONDARY_TRUSTED_KEYRING=y)
```

On an installed system the machine-local MOK signs **four** layers in the chain: the **installed GRUB binary**, the UKI (primary), the recovery vmlinuz (fallback), and DKMS modules. The InterGenOS distro EFI X.509 PIV slot 9c key signs only the **live/repo GRUB** — the build-ceremony binary shipped on the ISO and the mirror — and never touches an installed machine. At install time Forge rebuilds GRUB with SBAT and re-signs it with the per-machine MOK (`installer/backend/bootloader.py`, `_install_signed_efi_chain`), so the installed GRUB carries the user's own MOK signature rather than the distro PIV signature. The distro's PIV private key stays at HQ and is never present on a user's system.

MOK enrollment is what populates `.secondary_trusted_keys` with your MOK's public half. Until enrollment completes, only modules whose pubkey matches `.builtin_trusted_keys` (i.e., the in-tree set built with the running kernel) will load.

> **Note (history):** Prior versions of this document described vmlinuz as InterGenOS-distro-EFI-X.509-signed. That framing pre-dated D-005 (2026-05-18) which ratified UKI parity for installed systems with user-MOK signing. The InterGenOS PIV slot 9c key stays at HQ; only the **live/repo GRUB** (the ISO/mirror build-ceremony binary) carries that signature — the **installed GRUB is re-signed per-machine with the user MOK** at install time, so the installed GRUB, vmlinuz, UKI, and DKMS modules all sign with the user's machine-local MOK. Audit row B-047 closure 2026-05-22 records the vmlinuz refresh; the live-vs-installed GRUB distinction was added 2026-06-27 (code-verified against the `sign_efi_binary` call at `installer/backend/bootloader.py:679-686`; RESOLUTION PROPOSED, operator declares).

For a deeper treatment of the ephemeral kernel-module key (#3) and why it is distinct from the MOK, see [docs/ephemeral-module-signing.md](ephemeral-module-signing.md).

---

## 3. Pre-install state — what InterGenOS ships today

As of this writing InterGenOS uses Fedora's MS-signed shim binary (`shim-x64-16.1-2`) for the pre-boot chain. Our own MS-signed shim is in flight — see [docs/shim-review-submission.md](shim-review-submission.md) and its target 2026-05-22 PR-open against `rhboot/shim-review`.

**The MOK enrollment flow described here works identically against both the Fedora-piggyback shim and our own forthcoming MS-signed shim.** The shim binary changes; the MOK enrollment path does not. The shim is just the carrier — what matters at MOK time is that *some* MS-signed shim is loaded, exposing the standard `MokManager` interface from `mmx64.efi`.

A reviewer or end user verifying the loaded shim at boot can run:

```bash
sbverify --list /boot/efi/EFI/InterGenOS/shimx64.efi
```

and expect either MS 2011 CA or MS 2023 CA signatures (or both, post-shim-review acceptance). Both are accepted by UEFI firmware shipped from 2012 onward.

---

## 4. Install-time MOK provisioning

The Forge installer (`installer/backend/mok.py`) handles install-time MOK setup. The one thing it asks of you is the enrollment password (§0 step 2) — everything else below happens on its own. End users do not need to manually run any of the commands in this section; they are documented here so you can verify what the installer did and so reviewers can audit the procedure.

### What the installer does

1. **Generate keypair** ([mok.py:generate_mok_keypair](../installer/backend/mok.py#L54)):
   - RSA-2048 X.509 self-signed cert, valid 100 years (the lifetime of your machine is the only thing that matters; key rotation requires a new enrollment cycle and is rarely done in practice).
   - Subject is `CN=InterGenOS Machine Owner Key` by default. The installer allows you to override the CN with a label of your choice (e.g., `CN=Christopher's laptop MOK`), constrained to a safe-character whitelist to prevent shell injection.
   - Files written under `/var/lib/intergen/mok/` on your installed system, with mode 0700 on the directory:
     - `mok.key` — RSA private key, PEM, **mode 0600**
     - `mok.crt` — self-signed cert, PEM, mode 0644
     - `mok.der` — same cert in DER format, mode 0644 (required by `mokutil`)

2. **Set the enrollment password** (validated by [_validators.py:validate_mok_password](../installer/backend/_validators.py)):
   - **You choose it, during install** — Forge prompts you for it in the **Secure Boot enrollment** section (GUI) or at the **Secure Boot MOK password** prompt (TUI). This is §0 step 2.
   - Printable-ASCII, 8–256 characters. The installer enforces that range so the value pipes cleanly to `mokutil` at staging time; pick something you can retype accurately at a firmware-text prompt, where the keyboard layout may be US-QWERTY regardless of your locale.
   - **Forge does not generate it, does not display it back, and does not write it to any log.** Remember the password you chose — you type it once, at MokManager (§0 step 3, §5 below).
   - Leaving the field empty skips MOK enrollment entirely.

3. **Queue MOK for enrollment** ([mok.py:queue_mok_enrollment](../installer/backend/mok.py#L150)):
   - Invokes `mokutil --import /var/lib/intergen/mok/mok.der` inside the install chroot.
   - The chroot has `/sys/firmware/efi/efivars` bind-mounted (see [installer/backend/hooks.py:mount_efivars](../installer/backend/hooks.py)), which is what allows `mokutil` to write the staged enrollment into EFI variables.
   - The password is piped to `mokutil` on stdin (it requires two confirmations); this is why the installer enforces printable-ASCII-only passwords — embedded control chars would split the stdin reads.

After install, the MOK is *queued* but *not yet enrolled*. Enrollment completes on the first boot after you re-enable Secure Boot in firmware setup (§0 step 3) — that re-enable is what puts shim in the boot path and lets MokManager run.

### Verifying what the installer wrote

After install, before first reboot, you can verify the MOK files are in place. The simplest verification is on the next boot's pre-MokManager prompt itself, but if you want to audit beforehand:

```bash
ls -la /var/lib/intergen/mok/
# Expect:
#   drwx------ ... mok/                    (mode 0700)
#   -rw------- ... mok.key                 (mode 0600 — your private key)
#   -rw-r--r-- ... mok.crt                 (PEM cert)
#   -rw-r--r-- ... mok.der                 (DER cert — what mokutil enrolled)

# Cert details
openssl x509 -in /var/lib/intergen/mok/mok.crt -noout -text | head -20

# Verify mokutil sees the staged enrollment
mokutil --list-new
# Expect the cert subject line "CN=InterGenOS Machine Owner Key" (or your custom CN)
```

If `mokutil --list-new` shows nothing but `/var/lib/intergen/mok/mok.der` exists, the cert was generated but not queued. Re-queue:

```bash
mokutil --import /var/lib/intergen/mok/mok.der
# You will be prompted for a NEW enrollment password (the original is gone).
# Pick one and write it down — you cannot recover this either.
```

---

## 5. First-boot MokManager walkthrough

This is the step that requires you to act. It happens once, on the first boot after install.

### First: re-enable Secure Boot (this is the trigger)

Forge finishes and offers to reboot. **Before InterGenOS starts, enter UEFI firmware setup
and turn Secure Boot back ON** (the setting you disabled in §0 step 1), then save and exit.
Firmware setup is typically reached with F2, F10, Del, or Esc during POST — the key varies
by vendor.

Re-enabling Secure Boot is what makes enrollment happen. With Secure Boot off the firmware
does not load shim, and MokManager is part of shim — so the enrollment your install staged
simply sits queued and the machine boots straight into InterGenOS. That is not a failure;
it just means the trigger has not been pulled yet. Re-enable Secure Boot and reboot.

### What you will see

On that first boot with Secure Boot re-enabled, **before** the InterGenOS boot menu appears, the firmware-loaded shim detects the pending MOK enrollment and surfaces the MokManager interface. The exact appearance varies by hardware vendor.

> **If the machine instead bootloops with "MOK Manager not found":** shim looks for
> `mmx64.efi` only in the directory it was launched from. Images built before
> 2026-07-16 staged MokManager beside the `EFI/InterGenOS/` shim but not the
> `EFI/BOOT/` fallback shim (nor on the live medium at all), and some firmware
> takes the fallback path on default boot — with an enrollment pending, that shim
> hard-fails and the machine loops. Workaround on such images: open the firmware
> boot menu (often F11) and select the **InterGenOS** entry explicitly, which
> launches the shim whose directory carries MokManager. Current images stage
> `mmx64.efi` beside every shim instance, on both the live medium and the
> installed ESP, so any launch path can run enrollment.

**On most hardware:** a blue or black text-mode screen with white text, similar to:

```
                            Shim UEFI key management

                       Press any key to perform MOK management

                                  (10 seconds)
```

**You have ~10 seconds** to press a key. If you miss the prompt, the system continues to boot to InterGenOS, but the MOK remains un-enrolled. You can re-trigger the prompt by running `mokutil --import` again from inside InterGenOS and rebooting.

### The MokManager menu

After you press a key, you see:

```
                            Perform MOK management

                       Continue boot
                    ►  Enroll MOK
                       Enroll key from disk
                       Enroll hash from disk
                       Reset MOK
                       Change MOK password
                       MOK options
                       Reboot
```

Navigate to **"Enroll MOK"** with arrow keys and press **Enter**.

### Reviewing the staged MOK

```
                            View key 0

                       Subject: CN=InterGenOS Machine Owner Key
                       Issuer:  CN=InterGenOS Machine Owner Key
                       SHA256:  <40-char hash>
                       Valid from: <date>
                       Valid to:   <date + 100 years>

                       Continue
                    ►  View key 0
```

This is the cert that was generated on your machine at install time. Confirm the **Subject** matches what the installer reported (the default is `CN=InterGenOS Machine Owner Key`; if you customized the CN during install, it will reflect your label).

Press **Enter** on "View key 0" once if you want to see the full details, then **Esc** to go back. Then arrow to "Continue" and press **Enter**.

### Entering the password

```
                            Enroll the key(s)?

                       [Y]es     [N]o
```

Press **Y**.

```
                            Password:
                            _
```

Type the **enrollment password you set during install** (§0 step 2). (Not your user login password. Not your root password. The one you chose specifically for MOK enrollment.) Characters do not echo. Press **Enter**.

```
                            Perform MOK management

                       Continue boot
                       Enroll MOK
                       ...
                    ►  Reboot
```

Arrow to **"Reboot"** and press **Enter**. The system reboots into InterGenOS proper.

### What just happened cryptographically

When you confirmed enrollment, MokManager:
1. Verified the password you typed matches the hash the installer queued.
2. Wrote the cert into the `MokListRT` UEFI runtime variable, which the kernel reads at startup to populate `.secondary_trusted_keys`.
3. Cleared the staging variable (`MokNew`), so the cert is no longer pending enrollment.

The next boot reads the MOK list into kernel keyring before any modules load. From here forward, any DKMS-built module signed with your MOK's private half (kept at `/var/lib/intergen/mok/mok.key`) will load.

---

## 6. Post-enrollment validation

After the post-MokManager reboot, log in and run the validation commands below. A post-install smoke-test framework lives at [installer/smoke/](../installer/smoke/) (entrypoint `smoke-test.sh`); the install-time wiring that drops it onto the target as `/usr/bin/intergenos-smoke-test` is in progress.

### Confirm Secure Boot is on and shim is loaded

```bash
mokutil --sb-state
# SecureBoot enabled

bootctl status 2>/dev/null | grep -E "Secure Boot|Setup Mode|Vendor"
# Secure Boot: enabled
# Setup Mode:  user
```

### Confirm your MOK is enrolled

```bash
mokutil --list-enrolled
# Should include an entry for CN=InterGenOS Machine Owner Key (or your custom CN).
# Plus one or more vendor keys (Microsoft + InterGenOS vendor cert + Fedora cert
# if you're on the piggyback shim).
```

### Confirm the kernel keyring picked it up

```bash
sudo keyctl list %:.secondary_trusted_keys
# Should list one or more keys including your MOK cert.
# The CN in the listing should match what mokutil --list-enrolled shows.
```

If `.secondary_trusted_keys` is empty but `mokutil --list-enrolled` shows your MOK, you are running a kernel built without `CONFIG_SECONDARY_TRUSTED_KEYRING=y`. This is a packaging bug; report it at [github.com/InterGenJLU/intergenos/issues](https://github.com/InterGenJLU/intergenos/issues).

### Confirm signed-module enforcement

```bash
cat /proc/sys/kernel/module_sig_enforce
# 1   ← enforcement on

cat /sys/kernel/security/lockdown
# [integrity]   ← or [confidentiality], either is correct

# Try to load an unsigned module (will fail; this is what we want):
sudo modprobe test_user_copy 2>&1 | head
# expect: "Key was rejected by service" or "Required key not available"
```

### End-to-end DKMS chain test

If you've installed a DKMS-built package (NVIDIA, ZFS, VirtualBox host modules), the test is whether `modprobe <name>` loads it.

```bash
sudo modprobe nvidia 2>&1 | head
# No error → DKMS module signed by your MOK loaded successfully.
# "Key was rejected" → either DKMS didn't sign with your MOK, or your MOK
#                      isn't enrolled. Check `mokutil --list-enrolled` first.
```

To audit which key signed a loaded module:

```bash
sudo modinfo nvidia | grep -E "^signer|^sig_key"
# signer:  InterGenOS Machine Owner Key  ← your MOK by CN
# sig_key: <hex fingerprint>
```

Compare the `sig_key` fingerprint against your MOK cert:

```bash
openssl x509 -in /var/lib/intergen/mok/mok.crt -noout -fingerprint -sha1
# SHA1 Fingerprint=<hex>   ← should match sig_key (or a sub-fingerprint)
```

---

## 7. Failure modes + recovery

### "I missed the 10-second MokManager prompt"

The system booted to InterGenOS with the MOK still queued. Log in and re-trigger:

```bash
sudo mokutil --import /var/lib/intergen/mok/mok.der
# Pick a new enrollment password when prompted, write it down,
# then reboot to retry MokManager.
sudo reboot
```

### "I typed the wrong password at MokManager"

MokManager allows three attempts before it aborts. If you abort:
- The enrollment is **not** cancelled — it remains queued.
- The next reboot will surface the MokManager prompt again.

If you've forgotten the install-time password entirely:

```bash
# Cancel the original queued enrollment
sudo mokutil --revoke-import

# Re-queue with a new password
sudo mokutil --import /var/lib/intergen/mok/mok.der
sudo reboot
```

### "MokManager never appeared"

Three common causes:

**(a) Secure Boot is still disabled in firmware — by far the most common case.** This is §0 step 3 not yet done: the install correctly ran with Secure Boot off, and it was never turned back on. Without Secure Boot the firmware does not load shim, and MokManager is part of shim, so nothing prompts and the machine boots straight into InterGenOS with the enrollment still queued. Reboot into firmware setup (typically F2/F10/Del/Esc depending on vendor), **enable Secure Boot**, save, and boot again — MokManager appears on that boot.

**(b) The system is in "Setup Mode" with no PK enrolled.** This is a state where Secure Boot is on but no platform key is present, leaving the firmware in a permissive boot state where MokManager is bypassed. Check via:

```bash
sudo bootctl status | grep -E "Setup Mode"
# Setup Mode: user   ← good
# Setup Mode: setup  ← problem
```

Recovery: in firmware setup, find "Restore factory keys" or "Reset Secure Boot keys" and enable. This re-enrolls the PK and exits setup mode.

**(c) Hardware vendor's firmware skips the MokManager prompt under Secure Boot user-mode.** Reported on some Lenovo ThinkBook and HP ProBook models; **not reproduced on the hardware this runbook has been validated against**, so treat it as a vendor-specific report rather than a documented InterGenOS behavior. The reported workaround is to enter firmware setup, disable Secure Boot, boot once so the enrollment is surfaced, enroll, then re-enable Secure Boot. Note that this contradicts cause (a) above — with Secure Boot off, shim is normally not loaded at all — so it only applies to firmware that still routes through shim with Secure Boot disabled. Try the (a) path first.

### "Vendor-specific BIOS variants"

| Vendor | MokManager UI behavior | Quirks |
|---|---|---|
| Lenovo ThinkPad / IdeaPad | Standard text-mode, ENTER/arrows | Reliable; oldest ThinkPads pre-2015 may need BIOS update for shim 16.x |
| HP ProBook / EliteBook | Standard text-mode | Some models present MokManager at next boot only if Fast Boot is disabled |
| Dell Latitude / XPS | Standard text-mode | Reliable |
| ASUS ROG / ZenBook | Text-mode, F-key navigation | Some require F10 to confirm enrollment instead of Enter |
| MSI gaming series | Standard text-mode | Reliable |
| Acer Aspire / Predator | Standard text-mode | Some models need "Secure Boot Mode: Custom" rather than "Standard" |
| Apple Mac (Intel) | Not supported | Mac firmware does not implement MokManager prompts |

### "I see 'invalid signature' on a DKMS module after enrollment"

Three causes in descending order of likelihood:

**(a) DKMS didn't sign the module.** DKMS module signing is configured per-distribution; InterGenOS configures it to sign with your MOK by default, but if you upgraded from a pre-MOK installer, the config may be missing. Check:

```bash
grep -E "^MOK|^DKMS_SIGN|^sign_tool" /etc/dkms/framework.conf
# Should reference /var/lib/intergen/mok/mok.key + mok.der
```

**(b) DKMS signed with a different key than the one you enrolled.** Happens if `/var/lib/intergen/mok/` was regenerated after install (e.g., manual `openssl req` run). Match the fingerprints:

```bash
modinfo <module> | grep sig_key
openssl x509 -in /var/lib/intergen/mok/mok.crt -noout -fingerprint
# Should match. If not, re-enroll the current cert via mokutil --import.
```

**(c) The MOK was enrolled but the kernel hasn't picked it up.** Reboot. (`.secondary_trusted_keys` is populated at kernel init; it does not hot-add MOKs after boot.)

### "I want to revoke a MOK"

If your machine changes hands, or you want to invalidate your current MOK and start fresh:

```bash
# Queue the current MOK for revocation
sudo mokutil --revoke <fingerprint>
sudo reboot
# At next MokManager prompt, confirm revocation with your enrollment password.
```

Or wipe the local state entirely (you will need to re-generate and re-enroll):

```bash
sudo rm -rf /var/lib/intergen/mok/
# Re-run the installer's MOK setup step from a recovery boot, or invoke
# the same logic directly from a Python shell with installer/backend/mok.py:
#   from backend.mok import generate_mok_keypair
#   generate_mok_keypair("/")
```

---

## 8. Real-hardware test plan

This section is the procedure used to validate the runbook against a real machine. It is what will be run against the validation-target build's ISO output as the gating evidence for the 2026-05-14 first-light trigger.

### Test environment

- **Hardware:** HP 14-dq laptop running InterGenOS 1.0-dev (the test laptop). Pre-existing MOK from prior install present at `/var/lib/intergen/mok/`. Secure Boot enabled in firmware.
- **Build artifact under test:** the validation-target build's ISO (path TBD when build completes). Current target: Build #9 (master `0cadd8c`); the Build #N-of-the-moment is what gets validated.
- **Witness host:** a separate witness host — receives copies of validation logs via rsync.

### Test procedure

1. **Pre-flight baseline — capture from the validation-target build's live-ISO env, NOT from the installed host.**

   The test host's installed OS is a dev-state InterGenOS 1.0-dev build that
   pre-dates `mokutil`/`sbverify`/`sbsign` landing in the package tree. The
   validation-target live-ISO env ships these tools as part of the standard image,
   so baseline capture runs from the live env.

   - `dd` the validation-target ISO to USB; boot the laptop from the USB.
   - At the live shell, run and capture each:
     - `mokutil --list-enrolled` — baseline enrolled-key set (firmware cert store
       + any preexisting MOKs from prior installs on this hardware).
     - `mokutil --sb-state` — must report `SecureBoot enabled`.
     - `bootctl status | grep -E 'Secure Boot|Setup Mode|Vendor'` — must show
       `Setup Mode: user`.
   - Write outputs to `/run/mok-validation/baseline-<timestamp>.log` (live env is
     tmpfs). Before reboot/install, rsync the log to the witness host per the
     "Witness + reproducibility" subsection below so the baseline survives the
     live-env teardown.

2. **Boot the validation-target ISO from USB:**
   - dd the ISO to a USB stick.
   - **Media check (Secure Boot ON):** boot from USB once with Secure Boot enabled, to prove the shipped media chain verifies.
   - **Pass criterion 1:** ISO boots without disabling Secure Boot. Failure here means shim or the kernel signing chain is broken.
   - Then **turn Secure Boot OFF** in firmware setup before installing (§0 step 1) and boot the USB again. The install runs with Secure Boot disabled.

3. **Run Forge installer through MOK provisioning step:**
   - Target a spare partition (do not overwrite the current InterGenOS install on the laptop's primary disk).
   - In the **Secure Boot enrollment** section, **set** an enrollment password and note what you set (§0 step 2).
   - **Pass criterion 2:** `/var/lib/intergen/mok/{mok.key, mok.crt, mok.der}` present in installed target with correct modes (0600 on the key).
   - **Pass criterion 3:** `mokutil --list-new` (run against the install chroot before reboot) shows the staged cert.

4. **First boot — re-enable Secure Boot, then MokManager:**
   - **Enter firmware setup and turn Secure Boot back ON** (§0 step 3), save, exit.
   - Boot, selecting the newly-installed partition.
   - **Pass criterion 4:** MokManager prompt appears within 10s of POST, on the first boot after the Secure Boot re-enable.
   - Navigate Enroll MOK → review cert → confirm → enter the password set in step 3 → reboot.
   - **Pass criterion 5:** Reboot completes to InterGenOS login without errors, with Secure Boot enforcing.

5. **Post-enrollment validation:**
   - `mokutil --list-enrolled` includes the new MOK CN.
   - `sudo keyctl list %:.secondary_trusted_keys` shows the MOK pubkey.
   - `cat /proc/sys/kernel/module_sig_enforce` returns `1`.
   - Run the smoke-test framework at `installer/smoke/smoke-test.sh` — all signing-chain checks must PASS or WARN, none FAIL.
   - **Pass criterion 6:** all of the above.

6. **DKMS chain end-to-end (optional, if a DKMS module is available in the test build):**
   - Install a DKMS-built module (test harness ships a no-op `intergenos-test-mok` DKMS module for this purpose).
   - `sudo modprobe intergenos_test_mok` loads without error.
   - `modinfo intergenos_test_mok | grep signer` shows the MOK CN.
   - **Pass criterion 7:** module loads + signer matches MOK CN.

### Pass/fail criteria summary

The runbook is **validated for the first-light trigger** when criteria 1-6 pass on the validation-target build's ISO output. Criterion 7 is a stronger gate that we will run when a DKMS test module is available; not blocking on its own.

Failure at any of criteria 1-3 indicates a build / installer bug; failure at 4-6 indicates a documentation gap in this runbook (real-hardware step diverges from what's documented). In both cases, the runbook does not promote past v1 until the gap closes.

### Witness + reproducibility

Validation runs produce logs written to `~/tmp/mok-validation/run-<timestamp>/`. After each run, contents are rsync'd to a separate witness host at `~/intergenos/validation/mok-enrollment/run-<timestamp>/` for independent review.

---

## References

- [docs/ephemeral-module-signing.md](ephemeral-module-signing.md) — the in-tree module-signing key (separate from MOK)
- [docs/shim-review-submission.md](shim-review-submission.md) — pre-boot chain submission to `rhboot/shim-review`
- [docs/signing-key.md](signing-key.md) — InterGenOS's two long-lived distro keys
- [installer/backend/mok.py](../installer/backend/mok.py) — install-time MOK provisioning
- [installer/smoke/](../installer/smoke/) — post-install smoke-test framework (signing-chain checks)
- [Linux kernel module signing facility](https://www.kernel.org/doc/html/latest/admin-guide/module-signing.html) — upstream reference
- [shim project (rhboot/shim)](https://github.com/rhboot/shim) — the bootloader hand-off implementation
- [Microsoft UEFI Signing Service](https://techcommunity.microsoft.com/blog/hardware-dev-center/updated-microsoft-uefi-signing-requirements/1062916) — the trust root for the shim's MS signature
