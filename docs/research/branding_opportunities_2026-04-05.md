# InterGenOS Branding Opportunities Through the Build Process

## Discovered during Build #2 and Desktop Tier Build — April 5, 2026

### Already Implemented
1. **os-release** — `NAME="InterGenOS"`, `PRETTY_NAME="InterGenOS 1.0-dev (Revival)"`
2. **Hostname** — set to `intergenos` via Ch 9 config
3. **Target triplet** — `x86_64-igos-linux-gnu` woven through every compiled binary
4. **Kernel version suffix** — `vmlinuz-6.18.10-igos`
5. **Shell prompt (PS1)** — Blue brackets, colored username, green path
6. **sshd.service** — `Description=InterGenOS OpenSSH Server`
7. **Package tracking** — `/var/lib/igos/packages/`, `.igos.tar.gz` archives
8. **GRUB** — `GRUB_DISTRIBUTOR="InterGenOS"`

### Opportunities in the Build Process
9. **GRUB splash/theme** — Custom GRUB background and color scheme. Files go in `/boot/grub/themes/intergenos/`. Already researched in theming doc.
10. **Plymouth boot splash** — Animated boot screen. Package in desktop tier (if included). Requires a custom theme in `/usr/share/plymouth/themes/intergenos/`.
11. **GDM greeter background/logo** — Custom login screen. Set via `/etc/dconf/db/gdm.d/` config after GNOME is built.
12. **Default wallpaper** — Replace GNOME default. Can be set via gsettings override in `/usr/share/glib-2.0/schemas/`.
13. **GTK theme/icon theme** — Can provide InterGenOS-branded Adwaita variant or pre-select a theme via gsettings.
14. **/etc/issue** — Login banner for TTY consoles. Simple text file, set during config phase.
15. **/etc/motd** — Message of the day after login. Can include ASCII art.
16. **neofetch/fastfetch config** — Custom ASCII logo for system info display (if these tools are included).
17. **Firefox/browser homepage** — If Firefox is included, default homepage could point to InterGenOS docs/welcome page.
18. **systemd boot messages** — `systemd-boot` or GRUB can display branded boot messages.
19. **Installer UI** — When the custom installer is built, full branding control over the installation experience.
20. **Package manager UI** — When the custom PM is built, branded output format (already started with `[pkg]` prefix).

### Opportunities in Distribution
21. **ISO label** — Volume label for installation media
22. **rEFInd theme** — For EFI boot (when we add EFI support)
23. **Website/docs** — intergenstudios.com already exists
24. **Man page headers** — Custom section for InterGenOS-specific man pages

### Low-Hanging Fruit (Can Do Now)
- `/etc/issue` — simple text, added during Ch 9 config
- `/etc/motd` — simple text, same
- GRUB colors/background — config already supports it
- GDM/wallpaper/theme — gsettings overrides after desktop build
