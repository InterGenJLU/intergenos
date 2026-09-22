# Secure Boot and MOK on InterGenOS

This guide explains how InterGenOS uses Secure Boot, what the Machine Owner Key (MOK) is for, and what you can expect during install, first boot, and kernel upgrades.

It is written for users who want to understand the boot-chain security model: what is being verified, who signs what, and what happens when something goes wrong. It is not a developer reference. For the signing-ceremony side, see [03 — Automating release signing](../operations/03-automating-signing.md).

## The 30-second version

- InterGenOS ships a Microsoft-signed shim and signs the installed GRUB and UKIs it creates. Forge also signs the bare kernel images present during installation; later kernel upgrades MOK-sign the UKI, not the new bare vmlinuz. Being precise about what that *buys* you matters more than a reassuring headline, so this page is exact about what is **signed** versus what is **enforced**.
- **On the current target hardware, UEFI Secure Boot is left disabled by default, and no Machine Owner Key (MOK) is enrolled.** The signatures are present and verifiable, but firmware does not enforce them on a default install today. Secure Boot is *opportunistic*: the chain is ready for hardware where you choose to turn it on.
- The boot chain is: Microsoft CA → Fedora-signed shim → InterGenOS GRUB → InterGenOS kernel (or Unified Kernel Image).
- With Secure Boot enabled, the verified path accepts only images trusted through shim and the enrolled MOK. With Secure Boot disabled, firmware does not reject an unsigned kernel selected manually.
- The **release signing key never leaves our hardware**. We sign the live ISO and the install-mode images on our offline workstation; that key never touches your machine.
- When you install InterGenOS to disk, the Forge installer generates a per-machine **Machine Owner Key (MOK)** that lives only on your machine.
- Every kernel you install after the original ISO is rebuilt into a Unified Kernel Image and signed with your own MOK. The InterGenOS release key is never asked to sign anything you produce locally.
- If UKI generation or signing fails, a previous UKI may remain, but a bootable fallback is not guaranteed. Encrypted installs whose `/boot/initramfs.img` is only the placeholder suppress the stock bare-kernel entries.

## What is signed, and what is enforced

Security is not first. It is only — and "only" includes being honest about the current enforcement posture rather than implying a guarantee the shipped configuration does not yet provide. Two things are easy to conflate:

- **Signed** — the artifact carries a cryptographic signature you (or the firmware) can verify. InterGenOS ships an MS-signed shim and signs installed GRUB and kernel UKIs.
- **Enforced** — the firmware refuses to run anything whose signature does not validate. Enforcement requires UEFI Secure Boot to be *on* and the relevant key (Microsoft's CA via shim, plus your MOK) to be trusted.

On a default install on currently supported hardware, the chain is signed but **not** firmware-enforced, because Secure Boot is disabled and no MOK is enrolled. What that gives you today:

- **Verifiable install media.** The live ISO and install images are signed offline; you can verify them before you ever install.
- **A block-verified live image.** dm-verity checks squashfs blocks against the root hash embedded in the UKI. This detects corruption in every mode; it authenticates the medium against tampering only when Secure Boot enforces the UKI signature.
- **A signing path that is ready to enforce.** Because every installed kernel is already built into a signed UKI, enabling Secure Boot on capable hardware (and enrolling the MOK) turns the existing signatures into enforced ones without re-architecting anything.

What it does **not** give you on a default install is a hardware-rooted guarantee that only signed code boots — that is what enabling Secure Boot adds. The boot path is the most privileged code on any machine: if an attacker can substitute a kernel before the OS finishes starting, every other defense the OS provides is moot. The signed chain is what makes closing that gap a firmware-toggle away rather than a re-architecture.

## The boot chain

```
   Firmware (UEFI)
        │
        │  trusts Microsoft 3rd-party CA   (only checked when Secure Boot is ON)
        ▼
   Fedora-signed shim                  (we piggyback Fedora's shim
        │                               for v1.0; a parallel
        │  trusts InterGenOS vendor     submission produces our own
        │  cert (loaded as MOK)         MS-signed shim for later)
        ▼
   InterGenOS GRUB                     (signed by the release key for
        │                               the live ISO and install media,
        │  enforces signature           or by your local MOK for the
        │  verification on UKIs         GRUB written to your disk
        ▼                               during install)
   InterGenOS UKI                      (vmlinuz + initramfs +
   (or kernel + initramfs)              cmdline, bundled and signed
        │                               as a single Authenticode
        │  Linux kernel handoff         binary by systemd-stub)
        ▼
   InterGenOS userspace
```

A Unified Kernel Image (UKI) is a single signed file that bundles the kernel, the initramfs, and the kernel command-line. Signing the UKI envelope signs all three at once; nothing inside can be swapped without breaking the signature. The chain above is enforced from the firmware downward only when Secure Boot is enabled; with it disabled (the default on the current fleet), the same artifacts are present but the firmware does not gate on them.

The live ISO and install media use UKIs signed by our release subkey (held on a hardware token at our offline signing workstation). Once you install to disk, every kernel you install or upgrade is rebuilt into a UKI on your machine and signed with your machine's MOK.

## What is a MOK?

A Machine Owner Key is a per-machine signing key generated on your machine. MokManager enrolls its public certificate for shim, and the kernel imports it into the secondary trusted keyring.

It exists for two reasons:

1. **Your machine signs the kernels you install.** Whenever pkm installs, reinstalls, or upgrades the `linux-kernel` package, InterGenOS rebuilds the UKI and signs it with your MOK. The InterGenOS release key never sees the kernels you install; it only signs the live ISO and install media that ship from us.
2. **You can trust your own third-party drivers.** If you build out-of-tree modules (e.g., proprietary GPU drivers via DKMS), they can be signed by your MOK and load on a Secure Boot system without disabling enforcement.

The MOK is yours. It lives at `/var/lib/intergen/mok/` on the installed system. If you reinstall, Forge generates a fresh MOK; if you migrate to a new machine, you generate a new MOK there.

**Reinstalling does not remove the certificates earlier installs enrolled, and they do not expire out of the way.** The certificate carries a hundred-year validity, and — this is the part worth knowing — **nothing verifies those dates**: shim disables the time check when it verifies a signature and accepts an expired certificate by design, because there is no trustworthy clock before the system starts, and the kernel does not check them either when it verifies a module signature. So a machine that has been reinstalled a few times trusts one key per install, including keys whose private half is gone with the disk it was made on, and it will keep trusting them until someone removes them.

Removing them is the only way that list gets shorter, and it is deliberately a decision you make rather than something that happens to you:

- **During an install**, Forge shows you the keys this system already trusts that belong to earlier installs — each with its fingerprint and the date it was created — and offers to retire them. Keeping them all is the default and is always available; whatever you choose, the firmware asks you to confirm it at the same prompt that confirms the new key.
- **If you asked for a retirement and it did not happen**, the Welcomer's first page says so at your next login and prints the command that asks again. The firmware's prompt waits about ten seconds; when it is missed the request is dropped and nothing is removed, so the machine still trusts exactly what it trusted before — the page exists so that state is not silent.
- **On every machine, at every login**, the Welcomer's first page states what the firmware trusts: how many certificates carrying this project's machine owner name are enrolled, whether this machine's own is among them (matched by fingerprint against the copy of its certificate on this disk), which of the others somebody chose to keep during an install and which nobody has ever been asked about, and the command pair above for retiring the rest. It reads only what you may read — `mokutil`'s listings, the public copy of the certificate, and `/etc/intergenos/mok-prior-keys`, the world-readable record the install writes of what was decided about earlier keys. A count it cannot read is stated as unreadable, never as zero, and a machine whose own certificate has no readable copy is told the membership could not be checked rather than that its key is absent.
- **At any other time**, `mokutil --export` writes every enrolled certificate to a file in the current directory and `mokutil --delete <file>` queues one for removal, confirmed at the same firmware prompt. Check which file is the current machine's key first: its fingerprint matches `sha1sum` of `/var/lib/intergen/mok/mok.der` — and that is the comparison to use, because the firmware's listing prints SHA-1 and a fingerprint computed any other way will not match it.

## The Forge install flow

**Install with Secure Boot turned OFF.** The installed system's boot chain is signed with a MOK your firmware does not trust yet, so the supported order is: disable Secure Boot in UEFI setup before installing, set the enrollment password in Forge, then re-enable Secure Boot on the first reboot — that re-enable is what triggers enrollment. The three steps in full are in the [MOK enrollment runbook](../mok-enrollment.md).

Forge asks you for exactly one thing on this subject — the enrollment password. With that in hand, the bootloader stage does the following without asking you any further questions:

1. **Generates a per-machine MOK keypair** (RSA-2048, matching the kernel module-signing default) at `/var/lib/intergen/mok/mok.key` (private key), `/var/lib/intergen/mok/mok.crt` (PEM-format X.509 cert — `ukify` signs the UKI with it, and `sbsign` signs the EFI binaries and kernel images with it), and `/var/lib/intergen/mok/mok.der` (DER-format X.509 cert, the binary form MokManager wants for enrollment).
2. **Prompts you to set an enrollment password** in the **Secure Boot enrollment** section of the installer (a password you choose; printable-ASCII, 8–256 characters — leave it blank to skip MOK enrollment) and stages it for the first-boot enrollment step.
3. **Stages the MOK for enrollment** so MokManager picks it up on the next reboot.
4. **Ensures the UKI tooling is present** — `ukify`, an ordinary installed package that ships with the systemd tooling, so the linux-kernel package's post-install hook can build and sign UKIs at kernel install or upgrade time.
5. **Installs an initial UKI** built from the kernel the installer just dropped on the system, signed with the freshly-generated MOK.
6. **Configures bare-vmlinuz entries only when they have a usable initramfs.** On an encrypted install with only the placeholder `/boot/initramfs.img`, R001.2 withholds those entries and pins the UKI by name.

The password is the one **you chose** during install — Forge never generates one, never displays it back, and never writes it to any log. Remember it (or note it somewhere safe before you reboot); you will need it once, during MokManager enrollment at first boot.

If you forget the password before enrolling, you can re-stage enrollment with a new one (`mokutil --import`), or reinstall for a fresh MOK — see [Recovery](#recovery) below.

## First-boot MOK enrollment

MOK enrollment only matters when you intend to run with Secure Boot **enabled**. If you leave Secure Boot off, the firmware never gates on the MOK and the MokManager step never runs — the enrollment simply stays queued, which is not a failure.

**Turning Secure Boot back on is what triggers enrollment.** After Forge finishes, enter UEFI firmware setup on that first reboot and re-enable Secure Boot (the setting you turned off before installing), then save and boot. Only with Secure Boot on does the firmware load shim, and MokManager is part of shim.

On that boot, shim notices the pending MOK enrollment request and runs **MokManager** before continuing. MokManager is a small blue-text-on-black-background utility that walks you through four screens (captured below from a real enrollment):

1. **"Perform MOK management"** — press any key to start, then choose **Enroll MOK**. **MokManager waits about 10 seconds for that key press**, and the prompt can pass unseen while a monitor is still waking up. If it does, the queued request is gone and the boot stops at a "Verification failed" menu — see [I missed the MokManager prompt](#i-missed-the-mokmanager-prompt) below; it is recoverable from that menu without another machine.

   ![MokManager "Perform MOK management" menu with Enroll MOK highlighted](images/mok-1-enroll-panel.png)

2. **"Enroll MOK"** — review the certificate that is about to be enrolled. The certificate subject will read `CN=InterGenOS Machine Owner Key`. Confirm.

   ![MokManager Enroll MOK screen offering View key 0 and Continue](images/mok-2-view-panel.png)

3. **"Enroll the key(s)?"** — answer **Yes**, then type the enrollment password you set during install at the **"Enter password"** prompt. The password is single-use; once enrollment completes, you will not be prompted for it again.

   ![MokManager confirmation prompt "Enroll the key(s)?" with Yes](images/mok-3-confirm-panel.png)

4. **Reboot** — the menu returns with your key enrolled; choose **Reboot** to continue.

   ![MokManager menu after successful enrollment, ready to Reboot](images/mok-4-reboot-panel.png)

After enrollment, MokManager exits and the system boots into InterGenOS normally. From that point on, shim trusts your MOK; UKIs signed with that key load without further prompts.

If Secure Boot is enabled and you skip the pending enrollment, shim cannot validate the installed MOK-signed GRUB and UKI chain. Complete enrollment or turn Secure Boot off; Forge does not stage a release-signed installed-system UKI fallback. With Secure Boot disabled (the default), enrollment is not required and the locally signed UKIs load regardless.

## Kernel install and upgrade

When pkm installs, reinstalls, or upgrades `linux-kernel` (for example, `sudo pkm reinstall linux-kernel` to rerun the current package), its `post_install` hook does the following on your machine, with no key material from the InterGenOS release infrastructure:

1. Reads the kernel, the standard initramfs (and an additional FDE initramfs if your system is LUKS-encrypted — see below), and the canonical command-line for your system.
2. Runs `ukify` to bundle them into a single UKI in the systemd-stub envelope.
3. Signs that UKI with your machine's MOK. `ukify` does the signing itself, in the same invocation, from `/var/lib/intergen/mok/mok.key` and the PEM certificate `/var/lib/intergen/mok/mok.crt`; the `.der` form generated alongside is for MokManager enrollment only, never for signing. If no MOK key pair is present the UKI is built unsigned and the hook records that in its log.
4. Writes the signed UKI to `/boot/efi/EFI/Linux/intergenos-<kernel-version>.efi`.
5. Updates the GRUB menu so the new kernel is the default boot entry.
6. Runs a keep-two retention helper. An older UKI is exposed as a fallback only while its matching module tree remains usable; otherwise it is quarantined.

The InterGenOS PIV slot 9c key, used for release signing on our offline workstation, is never asked. It physically does not exist on your machine.

If UKI generation or signing fails, the hook records the failure and may leave the previous UKI selected. Plain installs may retain a usable bare-kernel entry; encrypted installs with only the placeholder initramfs do not. The failure does not prove that the next boot is usable.

## Where the kernel command line lives, and how to add a parameter

On a system that boots a UKI, the kernel command line is **not** a line in a bootloader configuration file. It is a section inside the signed image itself (`.cmdline`), put there when the image is built. Editing GRUB's configuration on disk will not change what the kernel boots with, and neither will editing anything else after the image exists: the command line is part of what the signature covers, which is the point of bundling it.

The supported way to add a parameter is to give the next image build a fragment to include:

1. Write the parameters into a file under `/etc/kernel/cmdline.d/`, ending in `.conf` — for example `/etc/kernel/cmdline.d/50-my-parameter.conf`. Comment lines beginning with `#` and blank lines are ignored, so the file can explain itself.
2. Rebuild the image: `sudo pkm reinstall linux-kernel`. The same rebuild happens by itself the next time a kernel package is installed or upgraded.
3. Reboot, then check `cat /proc/cmdline`. That is the kernel's own answer, and it is the one to trust.

What the rebuild does, on your machine and with your key: the hook reads the base command line (`/etc/kernel/cmdline`, or the running one if that file is absent), appends every fragment in `/etc/kernel/cmdline.d/` in sorted filename order, bundles kernel, initramfs and command line into one image with `ukify`, and signs it with your machine's own MOK from `/var/lib/intergen/mok/`. No release key is involved, as described above. `/var/log/intergen-kernel-postinstall.log` names each fragment it merged and what the fragment contributed, so a parameter that did not take can be traced to the step where it was lost.

Two things worth knowing before you rely on a parameter:

- **Sorted order is the whole of the ordering rule.** `40-` comes before `50-`. Packages that need a boot parameter ship their own fragment here rather than editing a shared file, so `ls /etc/kernel/cmdline.d/` is a complete list of what the system adds to your command line.
- **Some fragments are written by the installer for your machine in particular**, not shipped by a package: `40-sd-reader-port-power.conf` is written only on a machine whose PCI inventory lists the Genesys Logic GL9755 SD host controller, whose card slot does not work unless the kernel leaves that PCIe port's power management alone. Every such fragment says inside itself what it sets, why, what it costs and how to undo it, and deleting it plus a rebuild is always the way back.
- **With Secure Boot enabled, the kernel runs in integrity lockdown and refuses some module parameters** — specifically the ones a driver marks as hardware parameters, such as a physical memory address. The parameter is accepted on the command line, the module simply does not take it, and the boot log records `Lockdown: unsafe module parameters is restricted`. If a parameter appears in `/proc/cmdline` but plainly had no effect, that log line is the first place to look. (Measured 2026-09-16 on this project's hardware, which is why it is written down here.)

## ESP sizing

Because every kernel you install becomes a signed UKI in `/boot/efi`, the ESP needs enough headroom for several generations of kernel. A typical UKI is 80–150 MB depending on the initramfs payload. Forge creates a fixed **1 GiB** EFI System Partition during partitioning, which leaves room for several kernel generations plus their fallbacks.

If your ESP fills up, kernel install will fail. There is no version-suffixed kernel package to remove. The installed keep-two helper normally prunes superseded files; after verified old artifacts are safely removed, run `sudo pkm reinstall linux-kernel` and inspect `/var/log/intergen-kernel-postinstall.log`.

## Composition with LUKS encryption

If you chose the encrypted-install option, Forge installs a small full-disk-encryption initramfs alongside the kernel: busybox plus cryptsetup, just enough to prompt for your LUKS passphrase and unlock the root volume before the kernel hands off to the system's userspace.

That FDE initramfs is bundled into the same UKI as the kernel. The UKI signature covers it, just as it covers the kernel and the command-line. There is one signature; verifying the UKI signature verifies the entire boot path including the LUKS unlock prompt.

If you opt for TPM2-sealed unlock (an experimental feature not offered by the installer in this release — see [Full Disk Encryption](full-disk-encryption.md)), the same UKI envelope holds the additional bits that talk to your TPM. The unlock path is still inside the signed envelope; the TPM is not a way to skip Secure Boot verification.

For non-encrypted installs, the UKI's bundled initramfs is minimal — typically only CPU microcode — because all storage and filesystem drivers are built into the kernel. The bootloader does not need an initramfs to find the root volume.

## The signing key lives on the disk, and you hold its passphrase

**What is stored, and where.** Your machine owner key is a pair. The certificate — `/var/lib/intergen/mok/mok.crt` and its DER form `mok.der` — is public and is what you enrol into your firmware. The private half is `/var/lib/intergen/mok/mok.key`: an RSA-2048 key, readable only by the administrator (mode 0600, in a directory only the administrator can open), and **encrypted with a passphrase you set during the install**.

**When you are asked for it.** Each time this machine signs something with that key. In practice that is a kernel update and a graphics-driver rebuild — the two operations that produce something your firmware has to accept. The prompt appears at the console and in a desktop session; it says what is being signed, and it allows three attempts.

**What happens if you do not give it.** Nothing is signed, and nothing pretends otherwise. The package manager says the new kernel is `NOT BOOTABLE UNTIL SIGNED`, the EFI system partition keeps the previous release's signed boot image, that image stays the boot-menu target, and the transaction is recorded as incomplete. Your machine still boots — on the kernel it was already booting. When you have the passphrase in hand, finish the job at the console:

```bash
sudo pkm reinstall linux-kernel
```

A graphics-driver rebuild that could not sign says the same thing about its modules, which the kernel would otherwise refuse to load.

**Why it has a passphrase.** Decided 2026-09-17, reversing the arrangement earlier releases shipped. Without one, every signing step ran unattended as the administrator, which meant any program running as root could sign a boot image your firmware trusts — with nobody at the gate. Secure Boot then stopped an attacker who did not have root, and no one who did. Unattended kernel updates and a boot chain that resists root cannot both hold, and the signing authority on your own machine is yours.

The cost is real and is stated rather than hidden: one passphrase prompt per kernel or driver update. Keep the passphrase where you can find it. A passphrase you cannot produce is a machine that stops taking kernel updates until you can — the machine keeps booting and keeps working, but it will not sign a new kernel.

**Machines installed before this change.** They hold a key with no passphrase on it. Nothing is done to them behind your back: at the next kernel or driver update you are asked to set a passphrase, the key is rewritten encrypted in place, its previous bytes are destroyed, and the result is read back before anything signs with it. If you decline, the key is left exactly as it was and nothing is signed — you can set it at the next update instead. The first page you see when you log in says which state your machine is in, and you can read the same fact yourself:

```bash
cat /etc/intergenos/mok-key-protection
```

That file is a record of what the key's state was when it was last set, written by the installer or by the signing step; the key itself stays readable only by the administrator.

**What someone who takes the disk gains.** If you chose the encrypted install, the key sits on the LUKS-encrypted root volume, so someone who removes the disk and has no disk passphrase gets ciphertext. If you chose an unencrypted install, someone who has the disk can read the key file — but it is now encrypted under your signing passphrase, so having the file is not the same as being able to sign with it. Their remaining route is guessing that passphrase, which is why the installer refuses to let it be your disk passphrase and asks for at least eight characters.

Note what the key does **not** give anyone. It is yours alone, generated on your machine at install time. It signs nothing outside it, it is not a project key, and it gives no access to any other machine.

**What the alternative would cost.** The arrangement that removes the key from the disk entirely is a key held in a hardware token, a smart card, or sealed to a TPM. Each means the signing material is unavailable when the token is not present, which turns a kernel update into a failure or an unsigned kernel. With a passphrase on the on-disk key, the person is the thing that has to be present, and a person who is not present gets a refusal that leaves the machine bootable — which is the failure this release prefers.

**What would change this.** If kernels arrived already signed by a key your firmware trusts, your machine would never have to sign anything itself and would never need to ask. That is not how this release works, so the arrangement stands and is documented here rather than left implicit.


## Recovery

Most of the time you will never think about any of this. When something goes wrong, you have several recovery paths.

### "I missed the MokManager prompt"

The MokManager window is short (about 10 seconds), and on some machines the firmware's display is not awake yet when it appears. When it passes unseen, shim drops the queued request and stops at a menu titled **"Verification failed"** that offers *Continue boot*, *Enroll key from disk* and *Enroll hash from disk*. Recovery takes one minute and needs no password:

1. Choose **Enroll key from disk**.
2. Pick the boot partition (the small FAT volume, usually the only one offered).
3. Open **EFI**, then **InterGenOS**, and pick **mok.der** — the installer staged your machine's certificate there for exactly this.
4. Choose **Continue**, answer **Yes**, then **Reboot**.

Do not use *Enroll hash from disk*: it pins today's boot loader bytes, and the next kernel update would fail to boot.

Once you are logged in, the Welcomer's first page tells you whether the key is enrolled; it shows the same steps whenever the key is staged but not enrolled, and stays silent otherwise. It also tells you when a retirement of an earlier install's key that you asked for during the install has not happened, and prints the command that asks again; it stays silent when every key you asked about is gone. The certificate's public copy is at `/etc/intergenos/mok.der`; `mokutil --list-enrolled` lists what the firmware holds.

### "I forgot the MOK enrollment password"

On a default install (Secure Boot off), this has no effect on booting: enrollment is not required and your locally signed UKIs load regardless. The enrollment password only matters when you intend to enable Secure Boot.

The MOK material lives under `/var/lib/intergen/mok/`. There is no separate recovery wrapper command — UKI signing is handled entirely by the kernel package's post-install hook. To start fresh, reinstalling with Forge generates a new MOK and lets you set a new enrollment password; a subsequent kernel install or upgrade rebuilds the UKIs with it.

### "MokManager rejected my password"

You may have mistyped a character — firmware text prompts are often US-QWERTY regardless of the layout you chose at install. MokManager allows three attempts, then reboots. You can try again, or reinstall to get a fresh MOK and set a new password as described above.

### "Secure Boot is refusing my new kernel"

This applies only with Secure Boot enabled. It usually means the MOK was not enrolled (or was un-enrolled) but the kernel post-install hook signed a UKI with it. Re-enter MokManager and enroll the current MOK. Do not rely on a bare-vmlinuz recovery entry on an encrypted install; it may be intentionally absent.

### "I want to run an unsigned kernel for testing"

On a default install (Secure Boot off) nothing stops you, but the supported path is to build your kernel, sign it with your MOK using `sbsign`, and install it through the package manager like everything else — so the machinery stays consistent for the day you enable Secure Boot. If you need to test bare unsigned kernels with Secure Boot on, do it in a VM where Secure Boot is off, not on a production install.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Boot stops at MokManager every time | MOK enrollment never completed; firmware re-prompts each boot | Complete enrollment; see [First-boot MOK enrollment](#first-boot-mok-enrollment) above. |
| Boot stops at "Verification failed" with no *Enroll MOK* entry | The MokManager window passed unseen and the queued request was dropped | *Enroll key from disk* → EFI → InterGenOS → mok.der; see [I missed the MokManager prompt](#i-missed-the-mokmanager-prompt). |
| A new UKI will not boot under Secure Boot | The UKI is signed with a MOK shim does not trust | Enroll the current MOK, or rebuild the UKI after enrollment. |
| GRUB shows no usable InterGenOS UKI entry | UKI generation or signing failed | Inspect `/var/log/intergen-kernel-postinstall.log`. ESP-full and missing MOK material are two causes; do not assume a bare entry can unlock an encrypted root. |
| `ukify` is missing on the installed system | The package providing it was removed | Reinstall the `systemd` tooling, which provides `ukify`, with `sudo pkm reinstall systemd`, then run `sudo pkm reinstall linux-kernel`. On an encrypted install, missing `ukify` does not guarantee a usable bare-kernel fallback. |
| `sbsign` is missing on the installed system | `sbsigntool` was removed | Run `sudo pkm reinstall sbsigntool`. It is what signs EFI binaries and kernel images with your MOK — the installer uses it, and so does the NVIDIA module-signing hook. |
| Secure Boot toggle in firmware is greyed out | Some firmware (especially OEM laptops) makes Secure Boot read-only outside Setup Mode | See your hardware vendor's documentation for entering Setup Mode. InterGenOS runs fine with Secure Boot off (the default); enabling it is optional. |

## Further reading

- [Security Defaults](security-defaults.md) — the at-a-glance summary of every default protection InterGenOS enforces.
- Three design decisions shape the boot chain described here: the live ISO ships Fedora's pre-signed shim for v1.0, installed systems sign every per-kernel UKI with the user's own MOK, and encrypted installs fold the LUKS unlock into the signed UKI.
- [03 — Automating release signing](../operations/03-automating-signing.md) — the documentation for how the live ISO and install media get signed (the upstream side of the boot chain described here).
- [Getting Started](../getting-started.md) — install walkthrough that references the MOK enrollment step in context.
- [Security Policy](../../SECURITY.md) — how to report a vulnerability in any part of this boot chain.
