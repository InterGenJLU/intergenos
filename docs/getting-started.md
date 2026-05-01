# Getting Started with InterGenOS

Welcome to InterGenOS 1.0-dev. This guide covers how to verify your download, write the installation media, and what to expect during your first boot.

## 1. Hardware Requirements

InterGenOS is built for modern 64-bit hardware with the following minimum requirements:

*   **CPU:** x86-64-v2 (Intel Nehalem / AMD Jaguar or newer)
*   **RAM:** 8GB minimum (16GB recommended for local AI tiers)
*   **Storage:** 64GB NVMe or SSD
*   **Boot:** UEFI with Secure Boot enabled

## 2. Verifying the ISO Image

Before writing the image to a USB drive, you must verify its integrity. InterGenOS uses a strict "Security-Only Alignment" doctrine; verifying your download protects you against man-in-the-middle attacks or corrupted files.

1.  **Download the signing key:** 
    Download the canonical release signing key from [docs/signing-key.md](signing-key.md) or `intergenstudios.com/signing-key`.
2.  **Import the key:**
    `gpg --import intergenos-release-key.asc`
3.  **Verify the fingerprint:**
    Ensure the fingerprint matches the master fingerprint listed in `docs/signing-key.md`:
    `46DD 1029 F98F D453 1D44  99C3 A2AF 3A36 C5CE F2C3`
4.  **Verify the ISO:**
    `gpg --verify intergenos-1.0-dev.iso.sig intergenos-1.0-dev.iso`
    You should see a "Good signature" message from the primary key or one of the trusted subkeys ([S1] or [S2]).

## 3. Writing the Installation Media

Once verified, write the ISO to a USB flash drive (8GB or larger).

**On Linux/macOS:**
```bash
# Replace /dev/sdX with your actual USB device. DOUBLE-CHECK THIS.
sudo dd if=intergenos-1.0-dev.iso of=/dev/sdX bs=4M status=progress oflag=sync
```

**On Windows:**
We recommend using [Rufus](https://rufus.ie) or [balenaEtcher](https://balena.io/etcher) in "DD Image" mode.

## 4. Booting and Installation

1.  Insert the USB drive and boot your machine.
2.  Enter your UEFI/BIOS boot menu (often F12, F11, or F8) and select the USB drive.
3.  Secure Boot should be **enabled**. InterGenOS uses a Microsoft-signed shim to anchor the trust chain through to the InterGenOS-signed bootloader and kernel; no manual key enrollment required for in-tree modules.
4.  The system will boot into the **Forge Installer** TUI (Text User Interface).
5.  Follow the prompts to partition your disk, set your hostname, and create your user account.
6.  **MOK Enrollment:** During installation, Forge will prompt you to enroll the InterGenOS Machine Owner Key (MOK). This is required if you plan to use DKMS or build out-of-tree kernel modules. You must accept this enrollment and follow the on-screen instructions.

*(For a detailed walkthrough of the installation process, see [docs/install-guide.md](install-guide.md) - coming soon).*

## 5. First Boot

After the installation completes, remove the USB drive and reboot.

On your very first boot, the system will start the **First-Boot Greeter**. This is a security measure designed to ensure you securely set your initial passwords before the system brings up any network interfaces or background services.

For details on what to expect, read the [First-Boot Greeter reference](first-boot-greeter.md).

## 6. Next Steps

*   **InterGen AI Assistant:** To learn how to use the built-in AI, see the [InterGen User Guide](intergen-user-guide.md) (coming soon).
*   **Package Management:** To learn how to install software via our secure package manager, read the `pkm(1)` man page or the [PKM Guide](pkm-guide.md) (coming soon).
*   **FAQ:** Check the [Frequently Asked Questions](faq.md) (coming soon) for common issues and answers.
