"""InterGenOS Installer — Text User Interface (ncurses).

Phase 1 installer UI. Works over SSH, serial console, or bare TTY.
Guides the user through: disk selection → configuration → install → done.
"""

import curses
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from installer.backend import disks, packages, config, bootloader, hooks, users


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
        header_text = f" InterGenOS Installer — {title}"
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
        self.message(3, "Welcome to the InterGenOS Installer", color=1)
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
        """Disk selection screen."""
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

        row += 2
        efi = disks.is_efi()
        mode = "EFI" if efi else "BIOS"
        self.message(row, f"Boot mode: {mode}", color=2)
        row += 1
        self.message(row, f"WARNING: ALL data on {self.selected_disk.path} will be erased!", color=3)
        row += 1
        if not self.yes_no(row, "Proceed with partitioning?", default=False):
            return False

        return True

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

        if not self.yes_no(15, "Begin installation?", default=False):
            return False

        return True

    def screen_install(self):
        """Installation progress screen."""
        self.clear()
        self.header("Installing")

        row = 3

        # Step 1: Partition
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

        # Step 7: Bootloader
        self.message(row, "Installing GRUB bootloader...", color=4)
        self.stdscr.refresh()
        try:
            bootloader.install_grub(self.target, self.selected_disk.path, self.partitions)
            self.message(row, "Installing GRUB bootloader... done", color=2)
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

        self.message(3, "InterGenOS has been installed successfully!", color=2)
        self.message(5, "You can now remove the installation media and reboot.")
        self.message(7, f"  Hostname: {self.hostname}")
        if self.username:
            self.message(8, f"  User:     {self.username}")
        self.message(9, f"  SSH:      Enabled (port 22)")
        self.message(11, "On first boot:")
        self.message(12, "  - Log in as root or your user account")
        self.message(13, "  - Run 'sudo igos-install-chrome' for Google Chrome")
        self.message(14, "  - Run 'sudo igos-install-vscode' for VS Code")
        self.message(15, "  - Run 'igos-install-claude-code' for Claude Code")
        self.message(17, '"A system you understand, can modify, and can trust."', color=1)
        self.wait_key(20, "Press any key to exit the installer.")


def run_installer(archive_dir, packages_dir=None):
    """Entry point — launch the TUI installer."""
    def _main(stdscr):
        tui = InstallerTUI(stdscr, archive_dir, packages_dir)
        tui.run()

    curses.wrapper(_main)
