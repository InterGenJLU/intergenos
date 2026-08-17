# Getting Started with InterGenOS

Welcome to InterGenOS 1.0. This guide covers how to verify your download, write the installation media, and what to expect during your first boot, as well as how to keep your new system up to date.

## 1. Hardware Requirements

InterGenOS is built for modern 64-bit hardware with the following minimum requirements:

*   **CPU:** x86-64-v2 (Intel Nehalem / AMD Jaguar or newer)
*   **RAM:** 4GB minimum (the floor InterGen's entry AI tier is built for); 8GB+ recommended for a comfortable desktop, 16GB+ to run the larger local-AI tiers
*   **Storage:** 32GB minimum (the installer refuses smaller targets); more gives headroom for kernel updates and packages
*   **Boot:** UEFI, with Secure Boot supported by the firmware. Turn Secure Boot **off** for the install itself and back **on** at the first reboot — see the MOK Enrollment step in section 4.

## 2. Verifying the ISO Image

Before writing the image to a USB drive, you must verify its integrity. InterGenOS uses a strict "Security-Only Alignment" doctrine; verifying your download protects you against man-in-the-middle attacks or corrupted files.

1.  **Download the signing key:** 
    Download the canonical release signing key from [docs/signing-key.md](signing-key.md) or intergenstudios.com/signing-key.
2.  **Import the key:**
    gpg --import intergenos-release-key.asc
3.  **Verify the fingerprint:**
    Ensure the fingerprint matches the master fingerprint exactly:
    5597 A3E0 587B 2530 06D0  DD7B 8C50 8261 8208 3050
4.  **Verify the ISO:**
    gpg --verify intergenos-1.0.iso.sig intergenos-1.0.iso
    You should see a "Good signature" message from the primary key or one of the trusted subkeys ([S1] or [S2]).

## 3. Writing the Installation Media

Once verified, write the ISO to a USB flash drive (8GB or larger).

**On Linux/macOS:**
```bash
# Replace /dev/sdX with your actual USB device. DOUBLE-CHECK THIS.
sudo dd if=intergenos-1.0.iso of=/dev/sdX bs=4M status=progress oflag=sync
```

**On Windows:**
We recommend using [Rufus](https://rufus.ie) or [balenaEtcher](https://balena.io/etcher) in "DD Image" mode.

## 4. Booting and Installation

1.  Insert the USB drive and boot your machine.
2.  Enter your UEFI/BIOS boot menu (often F12, F11, or F8) and select the USB drive.
3.  Turn Secure Boot **off** in UEFI firmware setup before you install. A Microsoft-signed shim anchors the trust chain; GRUB and the kernel's Unified Kernel Image are signed with a Machine Owner Key (MOK) that Forge generates for your machine during install, and your firmware does not trust that MOK yet — so the install runs with Secure Boot disabled and you turn it back on afterwards to enroll. See the MOK Enrollment step below.
4.  The boot menu offers **Install InterGenOS (Graphical)** and **Install InterGenOS (Text)** — Forge ships both a graphical installer and a text (TUI) installer; choose either.
5.  Follow the prompts to partition your disk, set your hostname, and create your user account.
6.  **MOK Enrollment:** In the installer's **Secure Boot enrollment** section, set a one-time MOK enrollment password. You choose it; Forge never generates, displays, or logs one, and leaving the field empty skips enrollment. Then, on the first reboot after Forge finishes, **turn Secure Boot back on** in UEFI firmware setup — that re-enable is what triggers the firmware's MokManager, which prompts you for the password you set and completes enrollment. This is what lets the shim (currently InterGenOS uses Fedora's pre-signed shim while its own Microsoft-signed shim is in review) trust the MOK-signed GRUB and kernel. It also covers any out-of-tree kernel modules (DKMS, e.g. proprietary GPU drivers) you install later. The full walkthrough is in [docs/mok-enrollment.md](mok-enrollment.md).

*(For details on Forge's installer architecture, see the [Forge component reference](components/forge.md)).*

## 5. First Boot

After the installation completes, remove the USB drive and reboot.

If you set a MOK enrollment password, re-enter UEFI firmware setup on this reboot and turn Secure Boot back **on** before InterGenOS starts. MokManager runs on that boot and asks for the password you set; enter it, choose Reboot, and enrollment is done — once only.

On your very first boot, you log in with the credentials you chose during the Forge install. There is no separate first-boot password prompt — Forge collected the username and password from you during install, root is locked (`sudo` from your user account is the only path to root), and SSH host keys are generated on first boot.

## 6. Keeping Your System Secure and Up To Date

Once you're at your desktop, you will use the pkm package manager to pull updates from our canonical mirror at https://repo.intergenos.org/x86_64/current/.

Your first step should be to run:
```bash
sudo pkm sync
```
This simple command automatically fetches the InterGenOS.db index, verifies its cryptographic signature against the bundled release key, and securely refreshes your local package metadata so you can begin installing software.

Then apply any available upgrades with:
```bash
sudo pkm upgrade --all
```
This upgrades your installed packages to the newest signed versions from the refreshed index (`pkm sync` only refreshes the index; `pkm upgrade` is what applies updates). A bare `pkm upgrade` with no package names and no `--all` refuses to run rather than risk silently mass-modifying the system — pass `--all` for a full upgrade, or name specific packages (e.g. `sudo pkm upgrade firefox`) to upgrade only those.

For details on the cryptographic verification your machine performs during every pkm sync, see the [Repository Trust Model](repository-trust.md).

## 7. Next Steps

*   **InterGen AI Assistant:** To understand the AI assistant runtime, see the [InterGen component reference](components/intergen.md).
*   **Package Management:** To learn how to install software via our secure package manager, read the pkm(1) man page or the [Package Management user guide](users/package-management.md).
*   **FAQ:** Check the [Frequently Asked Questions](faq.md) for common issues and answers.
*   **Contribute:** If you want to help develop the OS, see the [Contributor Guide](contributor-guide.md).
