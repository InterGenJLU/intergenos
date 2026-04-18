"""Forge — InterGenOS System Installer — Text User Interface (ncurses).

Phase 1 installer UI. Works over SSH, serial console, or bare TTY.
Guides the user through: disk selection → configuration → install → done.
"""

import curses
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from installer.backend import disks, packages, config, bootloader, hooks, users, mok


class InstallerTUI:
    """Text-based installer interface using curses."""

    def __init__(self, stdscr, archive_dir, packages_dir=None):
        self.stdscr = stdscr
        self.archive_dir = archive_dir
        self.packages_dir = packages_dir

        # User selections
        self.selected_disk = None
        self.partitions = None
        self.hostname = "intergenos"
        self.timezone = "UTC"
        self.locale = "en_US.UTF-8"
        self.keymap = "us"
        self.root_password = ""
        self.username = ""
        self.user_password = ""
        self.selected_groups = ["core", "base", "desktop-gnome"]
        self.target = "/mnt/target"

        # Secure Boot / MOK
        self.mok_password = ""        # one-time enrollment password
        self.mok_keypair = None       # populated during install (mok.generate_mok_keypair result)

        # Install mode (fresh disk wipe vs. alongside existing OS)
        self.install_mode = disks.InstallMode.FRESH
        self.alongside_ntfs = None    # Partition object to shrink (alongside mode)
        self.alongside_free_bytes = 0 # bytes available after shrink

        # Curses setup
        curses.curs_set(0)
        curses.init_pair(1, curses.COLOR_CYAN, curses.COLOR_BLACK)
        curses.init_pair(2, curses.COLOR_GREEN, curses.COLOR_BLACK)
        curses.init_pair(3, curses.COLOR_RED, curses.COLOR_BLACK)
        curses.init_pair(4, curses.COLOR_YELLOW, curses.COLOR_BLACK)
        curses.init_pair(5, curses.COLOR_WHITE, curses.COLOR_BLUE)

    def run(self):
        """Run the installer flow."""
        if not self.screen_welcome():
            return
        if not self.screen_disk():
            return
        if not self.screen_config():
            return
        if not self.screen_groups():
            return
        # MOK setup only on EFI installs (BIOS legacy has no Secure Boot)
        if disks.is_efi():
            if not self.screen_mok_setup():
                return
        if not self.screen_confirm():
            return
        self.screen_install()
        self.screen_done()

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------

    def clear(self):
        self.stdscr.clear()

    def header(self, title):
        """Draw the standard header."""
        h, w = self.stdscr.getmaxyx()
        self.stdscr.attron(curses.color_pair(5))
        self.stdscr.addstr(0, 0, " " * w)
        header_text = f" Forge — {title}"
        self.stdscr.addstr(0, 0, header_text[:w-1])
        self.stdscr.attroff(curses.color_pair(5))

    def message(self, row, text, color=0):
        """Display a message at the given row."""
        h, w = self.stdscr.getmaxyx()
        if color:
            self.stdscr.attron(curses.color_pair(color))
        self.stdscr.addstr(row, 2, text[:w-4])
        if color:
            self.stdscr.attroff(curses.color_pair(color))

    def prompt(self, row, text, default=""):
        """Get text input from the user."""
        curses.curs_set(1)
        curses.echo()
        self.message(row, f"{text} [{default}]: ")
        h, w = self.stdscr.getmaxyx()
        prompt_len = len(f"{text} [{default}]: ") + 2
        self.stdscr.move(row, prompt_len)
        value = self.stdscr.getstr(row, prompt_len, w - prompt_len - 2).decode().strip()
        curses.noecho()
        curses.curs_set(0)
        return value if value else default

    def prompt_password(self, row, text):
        """Get password input (hidden)."""
        curses.curs_set(1)
        curses.noecho()
        self.message(row, f"{text}: ")
        h, w = self.stdscr.getmaxyx()
        prompt_len = len(f"{text}: ") + 2
        self.stdscr.move(row, prompt_len)
        value = self.stdscr.getstr(row, prompt_len, w - prompt_len - 2).decode().strip()
        curses.curs_set(0)
        return value

    def wait_key(self, row, text="Press any key to continue..."):
        self.message(row, text, color=4)
        self.stdscr.refresh()
        self.stdscr.getch()

    def yes_no(self, row, text, default=True):
        """Ask a yes/no question."""
        default_str = "Y/n" if default else "y/N"
        self.message(row, f"{text} [{default_str}]: ")
        self.stdscr.refresh()
        key = self.stdscr.getch()
        if key in (ord('y'), ord('Y')):
            return True
        elif key in (ord('n'), ord('N')):
            return False
        return default

    # ------------------------------------------------------------------
    # Screens
    # ------------------------------------------------------------------

    def screen_welcome(self):
        """Welcome screen — introduce InterGenOS."""
        self.clear()
        self.header("Welcome")
        self.message(3, "Welcome to Forge — the InterGenOS Installer", color=1)
        self.message(5, '"A system you understand, can modify, and can trust."')
        self.message(7, "This installer will guide you through setting up InterGenOS")
        self.message(8, "on your computer. The installation process will:")
        self.message(10, "  1. Partition and format a disk")
        self.message(11, "  2. Install system packages from pre-built archives")
        self.message(12, "  3. Configure your system (hostname, users, network)")
        self.message(13, "  4. Install the GRUB bootloader")
        self.message(15, "Your existing data on the selected disk WILL BE ERASED.", color=3)
        self.message(17, "Press ENTER to continue, or 'q' to quit.")
        self.stdscr.refresh()

        key = self.stdscr.getch()
        return key != ord('q')

    def screen_disk(self):
        """Disk selection screen + install mode chooser."""
        self.clear()
        self.header("Disk Selection")

        available_disks = disks.detect_disks()
        if not available_disks:
            self.message(3, "ERROR: No disks detected!", color=3)
            self.wait_key(5)
            return False

        self.message(3, "Available disks:", color=1)
        row = 5
        for i, disk in enumerate(available_disks):
            label = f"  {i+1}. {disk.path} — {disk.size_human} — {disk.model}"
            if disk.removable:
                label += " [removable]"
            # Annotate disks that contain something (existing OS / data)
            if disk.partitions:
                fstypes = [p.fstype for p in disk.partitions if p.fstype]
                if "ntfs" in fstypes:
                    label += " [contains Windows]"
                elif fstypes:
                    label += f" [contains {', '.join(set(fstypes))}]"
            self.message(row, label)
            row += 1

        row += 1
        choice = self.prompt(row, "Select disk number", "1")
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(available_disks):
                self.selected_disk = available_disks[idx]
            else:
                self.message(row + 2, "Invalid selection", color=3)
                self.wait_key(row + 3)
                return False
        except ValueError:
            self.message(row + 2, "Invalid input", color=3)
            self.wait_key(row + 3)
            return False

        # Detect if this disk has a Windows install we could share with
        existing_esp = disks.detect_existing_esp(self.selected_disk)
        ntfs_part, free_bytes = disks.detect_shrinkable_ntfs(self.selected_disk)
        bitlocker_parts = disks.detect_bitlocker_partitions(self.selected_disk)

        return self._screen_install_mode(existing_esp, ntfs_part, free_bytes, bitlocker_parts)

    def _screen_install_mode(self, existing_esp, ntfs_part, free_bytes, bitlocker_parts):
        """Sub-screen: choose fresh vs. alongside install mode."""
        self.clear()
        self.header("Install Mode")

        efi = disks.is_efi()
        mode = "EFI" if efi else "BIOS"
        self.message(3, f"Selected disk: {self.selected_disk.path} ({self.selected_disk.size_human})", color=1)
        self.message(4, f"Boot mode: {mode}", color=2)

        row = 6

        # BitLocker block: if we found encrypted NTFS, surface clearly first
        if bitlocker_parts:
            self.message(row, "BITLOCKER-ENCRYPTED PARTITION(S) DETECTED:", color=3); row += 1
            for p in bitlocker_parts:
                self.message(row, f"  {p.path} ({p.size_human})"); row += 1
            row += 1
            self.message(row, "Alongside install CANNOT shrink BitLocker volumes safely.", color=3); row += 1
            self.message(row, "Options:", color=1); row += 1
            self.message(row, "  - Boot Windows, disable BitLocker on this drive, re-run Forge"); row += 1
            self.message(row, "  - OR proceed with FRESH install (DESTROYS Windows)", color=3); row += 2
            if not self.yes_no(row, "Proceed with FRESH install (destroys all data)?", default=False):
                return False
            self.install_mode = disks.InstallMode.FRESH
            return True

        if existing_esp and ntfs_part and efi:
            # Both an ESP and a shrinkable NTFS — alongside is possible
            free_human = self._human_size_local(free_bytes)
            self.message(row, "Existing Windows install detected.", color=4); row += 1
            self.message(row, f"  ESP: {existing_esp.path} ({existing_esp.size_human})"); row += 1
            self.message(row, f"  Windows: {ntfs_part.path} ({ntfs_part.size_human})"); row += 1
            self.message(row, f"  Free space available after shrink: {free_human}"); row += 2

            self.message(row, "Choose install mode:", color=1); row += 1
            self.message(row, "  1. ALONGSIDE — keep Windows, shrink it, dual-boot"); row += 1
            self.message(row, "  2. FRESH — erase entire disk, InterGenOS only", color=3); row += 2

            choice = self.prompt(row, "Select mode (1 or 2)", "1")
            if choice.strip() == "2":
                self.install_mode = disks.InstallMode.FRESH
                return self._confirm_fresh_wipe(row + 2)
            else:
                self.install_mode = disks.InstallMode.ALONGSIDE
                self.alongside_ntfs = ntfs_part
                self.alongside_free_bytes = free_bytes
                return self._confirm_alongside(row + 2, ntfs_part, free_bytes)

        elif existing_esp and not ntfs_part:
            # Has an ESP but no shrinkable NTFS — only fresh available
            self.message(row, "Existing EFI partition detected, but no shrinkable", color=4); row += 1
            self.message(row, "NTFS partition found (or not enough free space).", color=4); row += 1
            self.message(row, "Only FRESH install available — entire disk will be erased.", color=3); row += 2
            self.install_mode = disks.InstallMode.FRESH
            return self._confirm_fresh_wipe(row)

        else:
            # Empty disk or no usable existing OS — fresh install only
            self.install_mode = disks.InstallMode.FRESH
            return self._confirm_fresh_wipe(row)

    def _confirm_fresh_wipe(self, row):
        """Final confirmation for fresh-disk install (data loss warning)."""
        self.message(row, f"WARNING: ALL data on {self.selected_disk.path} will be erased!", color=3)
        row += 1
        return self.yes_no(row, "Proceed with FRESH install?", default=False)

    def _confirm_alongside(self, row, ntfs_part, free_bytes):
        """Final confirmation for alongside install (NTFS shrink warning)."""
        free_human = self._human_size_local(free_bytes)
        self.message(row, f"NTFS partition {ntfs_part.path} will be shrunk.", color=3); row += 1
        self.message(row, f"Windows data is preserved; {free_human} freed for InterGenOS.", color=4); row += 1
        self.message(row, "Recommend: back up Windows before proceeding.", color=3); row += 2
        return self.yes_no(row, "Proceed with ALONGSIDE install?", default=False)

    def _human_size_local(self, bytes_val):
        """Local human-size formatter (avoid coupling to disks._human_size)."""
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if bytes_val < 1024:
                return f"{bytes_val:.1f} {unit}"
            bytes_val /= 1024
        return f"{bytes_val:.1f} PB"

    def screen_config(self):
        """System configuration screen."""
        self.clear()
        self.header("System Configuration")

        self.message(3, "Configure your InterGenOS system:", color=1)

        self.hostname = self.prompt(5, "Hostname", self.hostname)
        self.timezone = self.prompt(7, "Timezone", self.timezone)
        self.locale = self.prompt(9, "Locale", self.locale)

        self.message(11, "Root password:", color=1)
        while True:
            self.root_password = self.prompt_password(12, "  Enter password")
            confirm = self.prompt_password(13, "  Confirm password")
            if self.root_password == confirm and self.root_password:
                break
            self.message(14, "Passwords don't match or are empty. Try again.", color=3)
            self.stdscr.refresh()

        self.message(16, "Create a user account:", color=1)
        self.username = self.prompt(17, "  Username", "")
        if self.username:
            while True:
                self.user_password = self.prompt_password(18, "  Enter password")
                confirm = self.prompt_password(19, "  Confirm password")
                if self.user_password == confirm and self.user_password:
                    break
                self.message(20, "Passwords don't match or are empty. Try again.", color=3)
                self.stdscr.refresh()

        return True

    def screen_groups(self):
        """Package group selection screen."""
        self.clear()
        self.header("Package Selection")

        self.message(3, "Select package groups to install:", color=1)

        row = 5
        group_list = list(packages.GROUPS.items())
        for i, (name, info) in enumerate(group_list):
            selected = name in self.selected_groups
            marker = "[X]" if selected else "[ ]"
            req = " (required)" if info["required"] else ""
            self.message(row, f"  {marker} {name:20s} — {info['description']}{req}")
            row += 1

        row += 1
        self.message(row, "Toggle with number keys, ENTER to continue:", color=4)
        self.stdscr.refresh()

        # Simple toggle — for now just accept defaults
        key = self.stdscr.getch()
        return True

    def screen_mok_setup(self):
        """MOK enrollment setup screen (EFI installs only).

        Generates the one-time enrollment password and explains the
        first-boot MokManager flow. The actual keypair generation happens
        during the install step (needs the target chroot mounted).
        """
        self.clear()
        self.header("Secure Boot — Machine Owner Key")

        self.message(3, "InterGenOS uses Secure Boot to protect your system.", color=1)
        self.message(5, "During install, we generate a Machine Owner Key (MOK) unique")
        self.message(6, "to this machine. The public half gets enrolled in your UEFI")
        self.message(7, "firmware so signed kernel modules (e.g. NVIDIA drivers built")
        self.message(8, "via DKMS) can load on your system.")

        self.message(10, "On first boot, you will see a blue MokManager screen:", color=4)
        self.message(11, "  1. Press a key within 10 seconds when prompted")
        self.message(12, "  2. Choose 'Enroll MOK'")
        self.message(13, "  3. Enter the password shown below")
        self.message(14, "  4. Reboot — the MOK is now trusted")

        # Generate the one-time password and display it prominently
        self.mok_password = mok.generate_enrollment_password()

        self.message(16, "ONE-TIME ENROLLMENT PASSWORD (write this down NOW):", color=3)
        self.message(17, f"  {self.mok_password}", color=2)
        self.message(18, "(You only need this once, at the next reboot.)", color=4)

        self.message(20, "Press ENTER when you have written the password down,", color=1)
        self.message(21, "or 'q' to cancel installation.", color=1)
        self.stdscr.refresh()

        while True:
            key = self.stdscr.getch()
            if key == ord('q') or key == ord('Q'):
                return False
            if key in (curses.KEY_ENTER, 10, 13):
                return True

    def screen_confirm(self):
        """Confirmation screen before installation."""
        self.clear()
        self.header("Confirm Installation")

        self.message(3, "Please review your selections:", color=1)
        self.message(5, f"  Disk:      {self.selected_disk.path} ({self.selected_disk.size_human})")
        self.message(6, f"  Boot mode: {'EFI' if disks.is_efi() else 'BIOS'}")
        self.message(7, f"  Hostname:  {self.hostname}")
        self.message(8, f"  Timezone:  {self.timezone}")
        self.message(9, f"  Locale:    {self.locale}")
        self.message(10, f"  User:      {self.username or '(none — root only)'}")
        self.message(11, f"  Groups:    {', '.join(self.selected_groups)}")

        self.message(13, "ALL DATA ON THE SELECTED DISK WILL BE ERASED.", color=3)
        self.message(14, f"  Model: {self.selected_disk.model or 'Unknown'}")

        # Require typing the disk path to confirm — prevents accidental wipe
        self.message(16, f"Type '{self.selected_disk.path}' to confirm, or 'q' to cancel:", color=1)
        curses.echo()
        self.stdscr.move(17, 2)
        typed = self.stdscr.getstr(17, 2, 40).decode("utf-8", errors="replace").strip()
        curses.noecho()

        if typed != self.selected_disk.path:
            self.message(19, "Input did not match. Installation cancelled.", color=3)
            self.wait_key(21)
            return False

        return True

    def screen_install(self):
        """Installation progress screen."""
        self.clear()
        self.header("Installing")

        row = 3

        # Step 1: Partition (branch on install mode)
        if self.install_mode == disks.InstallMode.ALONGSIDE:
            self.message(row, f"Shrinking {self.alongside_ntfs.path}...", color=4)
            self.stdscr.refresh()
            try:
                # Shrink NTFS to its current used size + 5GB safety margin
                # (alongside_free_bytes is what we'll get back)
                new_size = self.alongside_ntfs.size_bytes - self.alongside_free_bytes
                disks.shrink_ntfs(self.alongside_ntfs.path, new_size)
                self.message(row, f"Shrinking {self.alongside_ntfs.path}... done", color=2)
            except Exception as e:
                self.message(row, f"NTFS shrink failed: {e}", color=3)
                self.wait_key(row + 2)
                return
            row += 1

            self.message(row, "Creating InterGenOS partition in freed space...", color=4)
            self.stdscr.refresh()
            try:
                free_start_mb = (
                    (self.alongside_ntfs.size_bytes - self.alongside_free_bytes)
                    // (1024 * 1024)
                )
                self.partitions = disks.partition_disk_alongside(
                    self.selected_disk, self.alongside_ntfs, free_start_mb
                )
                self.message(row, "Creating InterGenOS partition in freed space... done", color=2)
            except Exception as e:
                self.message(row, f"Alongside partitioning failed: {e}", color=3)
                self.wait_key(row + 2)
                return
            row += 1
        else:
            # Fresh wipe + clean GPT layout
            self.message(row, "Partitioning disk...", color=4)
            self.stdscr.refresh()
            try:
                efi = disks.is_efi()
                self.partitions = disks.partition_disk(self.selected_disk.path, efi=efi)
                self.message(row, "Partitioning disk... done", color=2)
            except Exception as e:
                self.message(row, f"Partitioning failed: {e}", color=3)
                self.wait_key(row + 2)
                return
            row += 1

        # Step 2: Mount
        self.message(row, "Mounting filesystems...", color=4)
        self.stdscr.refresh()
        disks.mount_target(self.partitions, self.target)
        self.message(row, "Mounting filesystems... done", color=2)
        row += 1

        # Step 3: Install packages
        self.message(row, "Installing packages...", color=4)
        self.stdscr.refresh()
        progress_row = row + 1

        def progress_cb(current, total, name):
            h, w = self.stdscr.getmaxyx()
            pct = current * 100 // total
            bar_width = 30
            filled = current * bar_width // total
            bar = "█" * filled + "░" * (bar_width - filled)
            text = f"  [{bar}] {pct}% — {name} ({current}/{total})"
            self.stdscr.addstr(progress_row, 2, text[:w-4])
            self.stdscr.clrtoeol()
            self.stdscr.refresh()

        success, fails, failed = packages.install_packages(
            self.target, self.archive_dir, self.selected_groups,
            self.packages_dir, progress_callback=progress_cb
        )
        self.message(row, f"Installing packages... {success} installed, {fails} failed", color=2)
        row = progress_row + 1

        # Step 4: Generate config
        self.message(row, "Generating system configuration...", color=4)
        self.stdscr.refresh()
        config.generate_all(
            self.target, self.partitions,
            hostname=self.hostname, locale=self.locale,
            keymap=self.keymap, timezone=self.timezone
        )
        self.message(row, "Generating system configuration... done", color=2)
        row += 1

        # Step 5: Post-install hooks
        if self.packages_dir:
            self.message(row, "Running post-install hooks...", color=4)
            self.stdscr.refresh()
            hook_count = hooks.run_post_install_hooks(
                self.target, self.packages_dir,
                progress_callback=lambda c, t, n: None
            )
            self.message(row, f"Running post-install hooks... {hook_count} executed", color=2)
            row += 1

        # Step 6: User accounts
        self.message(row, "Setting up user accounts...", color=4)
        self.stdscr.refresh()
        users.set_root_password(self.target, self.root_password)
        if self.username:
            users.create_user(self.target, self.username, self.user_password)
        users.enable_services(self.target)
        self.message(row, "Setting up user accounts... done", color=2)
        row += 1

        # Step 7a: MOK keypair generation + enrollment queue (EFI only)
        if self.partitions.get("efi") and self.mok_password:
            self.message(row, "Generating Machine Owner Key (MOK)...", color=4)
            self.stdscr.refresh()
            try:
                self.mok_keypair = mok.generate_mok_keypair(self.target)
                mok.queue_mok_enrollment(
                    self.target,
                    self.mok_keypair["der_path"],
                    self.mok_password,
                )
                self.message(row, "Generating Machine Owner Key (MOK)... done", color=2)
            except Exception as e:
                self.message(row, f"MOK setup failed: {e}", color=3)
                self.wait_key(row + 2)
                return
            row += 1

        # Step 7b: Bootloader (signed boot chain on EFI, plain GRUB on BIOS)
        self.message(row, "Installing bootloader...", color=4)
        self.stdscr.refresh()
        try:
            bootloader.install_bootloader(
                self.target, self.selected_disk.path, self.partitions,
                mok_keypair=self.mok_keypair,
            )
            self.message(row, "Installing bootloader... done", color=2)
        except Exception as e:
            self.message(row, f"Bootloader installation failed: {e}", color=3)
        row += 1

        # Step 8: Unmount
        self.message(row, "Unmounting filesystems...", color=4)
        self.stdscr.refresh()
        disks.unmount_target(self.target)
        self.message(row, "Unmounting filesystems... done", color=2)
        row += 2

        self.message(row, "Installation complete!", color=2)
        self.wait_key(row + 2)

    def screen_done(self):
        """Completion screen."""
        self.clear()
        self.header("Installation Complete")

        self.message(3, "InterGenOS has been forged successfully!", color=2)
        self.message(5, "You can now remove the installation media and reboot.")
        self.message(7, f"  Hostname: {self.hostname}")
        if self.username:
            self.message(8, f"  User:     {self.username}")
        self.message(9, f"  SSH:      Enabled (port 22)")

        row = 11
        # MOK enrollment reminder if EFI install
        if self.mok_keypair and self.mok_password:
            self.message(row, "AT FIRST BOOT — MOK ENROLLMENT (one time):", color=3)
            row += 1
            self.message(row, "  - Blue MokManager screen appears for 10 seconds")
            row += 1
            self.message(row, "  - Press any key, choose 'Enroll MOK' -> 'Continue'")
            row += 1
            self.message(row, f"  - Enter password: {self.mok_password}", color=2)
            row += 1
            self.message(row, "  - Reboot when MokManager finishes")
            row += 2

        self.message(row, "After login:")
        row += 1
        self.message(row, "  - Run 'sudo igos-install-chrome' for Google Chrome")
        row += 1
        self.message(row, "  - Run 'sudo igos-install-vscode' for VS Code")
        row += 1
        self.message(row, "  - Run 'igos-install-claude-code' for Claude Code")
        row += 2

        self.message(row, '"A system you understand, can modify, and can trust."', color=1)
        self.wait_key(row + 3, "Press any key to exit the installer.")


def run_installer(archive_dir, packages_dir=None, dry_run=False):
    """Entry point — launch the TUI installer."""
    if dry_run:
        disks.set_dry_run(True)

    def _main(stdscr):
        tui = InstallerTUI(stdscr, archive_dir, packages_dir)
        tui.run()

    curses.wrapper(_main)
