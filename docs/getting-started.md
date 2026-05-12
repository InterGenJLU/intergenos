# Getting Started with InterGenOS

Welcome to InterGenOS 1.0. This guide covers how to verify your download, write the installation media, and what to expect during your first boot, as well as how to keep your new system up to date.

## 1. Hardware Requirements

InterGenOS is built for modern 64-bit hardware with the following minimum requirements:

*   **CPU:** x86-64-v2 (Intel Nehalem / AMD Jaguar or newer)
*   **RAM:** 8GB minimum (16GB recommended for local AI tiers)
*   **Storage:** 64GB NVMe or SSD
*   **Boot:** UEFI with Secure Boot enabled

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
`ash
# Replace /dev/sdX with your actual USB device. DOUBLE-CHECK THIS.
sudo dd if=intergenos-1.0.iso of=/dev/sdX bs=4M status=progress oflag=sync
`

**On Windows:**
We recommend using [Rufus](https://rufus.ie) or [balenaEtcher](https://balena.io/etcher) in "DD Image" mode.

## 4. Booting and Installation

1.  Insert the USB drive and boot your machine.
2.  Enter your UEFI/BIOS boot menu (often F12, F11, or F8) and select the USB drive.
3.  Secure Boot should be **enabled**. InterGenOS uses a Microsoft-signed shim to anchor the trust chain through to the InterGenOS-signed bootloader and kernel; no manual key enrollment required for in-tree modules.
4.  The system will boot into the **Forge Installer** TUI (Text User Interface).
5.  Follow the prompts to partition your disk, set your hostname, and create your user account.
6.  **MOK Enrollment:** During installation, Forge will prompt you to enroll the InterGenOS Machine Owner Key (MOK). This is required if you plan to use DKMS or build out-of-tree kernel modules. You must accept this enrollment and follow the on-screen instructions.

*(For a detailed walkthrough of the installation process, see the [Install Guide](install-guide.md)).*

## 5. First Boot

After the installation completes, remove the USB drive and reboot.

On your very first boot, the system will start the **First-Boot Greeter**. This is a security measure designed to ensure you securely set your initial passwords before the system brings up any network interfaces or background services.

For details on what to expect, read the [First-Boot Greeter reference](first-boot-greeter.md).

## 6. Keeping Your System Secure and Up To Date

Once you're at your desktop, you will use the pkm package manager to pull updates from our canonical mirror at https://repo.intergenos.org/x86_64/.

Your first step should be to run:
`ash
sudo pkm sync
`
This simple command automatically fetches the InterGenOS.db index, verifies its cryptographic signature against the bundled release key, and securely refreshes your local package metadata so you can begin installing software.

For details on the cryptographic verification your machine performs during every pkm sync, see the [Repository Trust Model](repository-trust.md).

## 7. Next Steps

*   **InterGen AI Assistant:** To learn how to use the built-in AI safely, see the [InterGen User Guide](intergen-user-guide.md).
*   **Package Management:** To learn how to install software via our secure package manager, read the pkm(1) man page or the [PKM Guide](pkm-guide.md).
*   **FAQ:** Check the [Frequently Asked Questions](faq.md) for common issues and answers.
*   **Contribute:** If you want to help develop the OS, see the [Contributor Guide](contributor-guide.md).
