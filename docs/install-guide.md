# InterGenOS Forge Installation Guide

Welcome to InterGenOS! The OS is installed using our custom-built installer known as **Forge**. Forge provides both a text-based user interface (TUI) and a graphical user interface (GUI) to walk you through configuring and installing your new system.

## 1. Booting the Live Media
After flashing the InterGenOS ISO to a USB drive and booting your machine in UEFI mode with Secure Boot enabled, you will be greeted by the live environment.
The live system provides a fully functional desktop where you can test hardware compatibility before committing to an installation.

## 2. Launching Forge
You can launch the Forge installer from the desktop shortcut or by running \orge-installer\ in a terminal.
*Note: The installer requires administrative privileges to perform disk formatting and package installation.*

## 3. Installation Steps

### Welcome & Language Selection
Select your preferred language, keyboard layout, and timezone. Forge will configure the live environment immediately to reflect these choices.

### Disk Partitioning
Forge supports both automated disk wiping and manual partitioning:
- **Erase Disk**: Select a target drive. Forge will automatically create an EFI System Partition (ESP), a swap partition (if requested), and a Btrfs root filesystem.
- **Manual Partitioning**: For advanced users requiring custom layouts. Ensure you create an ESP (FAT32) mounted at \/boot/efi\.
- **Full Disk Encryption**: Regardless of your partitioning choice, you can opt to encrypt your root filesystem using LUKS2. You will be prompted to enter a strong passphrase.

### Bootloader Configuration
InterGenOS uses GRUB2 loaded via a Microsoft-signed shim to maintain the Secure Boot chain of trust. Forge automatically installs the bootloader to your ESP. No manual configuration is required unless you are setting up a complex dual-boot scenario.

### Software Selection
Choose your software tiers:
- **Base/Core**: Essential system utilities (mandatory).
- **Desktop**: The GNOME-based desktop environment.
- **Extra**: Common end-user applications (Firefox, Thunderbird, LibreOffice, etc.).
- **AI Tier**: The InterGen local AI assistant, \llama.cpp\, and required models.

### User Creation
Create your primary user account. This user will automatically be granted \sudo\ access. You will also be prompted to set a root password (or you can choose to disable the root account and rely entirely on \sudo\).

### Summary & Integrity Check
Before any destructive actions occur, Forge displays a summary of your choices.
Once you confirm, the installation begins.
*Integrity Note:* During the install process, Forge validates the cryptographic signatures and content hashes of all packages being installed, ensuring a bit-for-bit match with the official InterGenOS release manifest.

## 4. Post-Installation
When the installation completes, Forge will prompt you to reboot. Remove your installation media.
Upon your first boot into the new system, you may be guided through the Machine Owner Key (MOK) enrollment process if you plan to compile out-of-tree kernel modules (like proprietary graphics drivers). See \docs/mok-enrollment.md\ for details.

Welcome to your new, secure, AI-native operating system!
