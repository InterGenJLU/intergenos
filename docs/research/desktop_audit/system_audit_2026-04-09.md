# InterGenOS System Audit — April 9, 2026

## Source
Full audit of running InterGenOS system on HP laptop (NVMe boot, kernel 6.18.10).
Examined dmesg, journal, systemd units, hardware drivers, desktop environment.

## Hardware

- CPU: Intel Core i5-1035G1 (Ice Lake, 4c/8t)
- GPU: Intel i915 (device 8a56), DMC firmware loaded
- NVMe: SK hynix BC511 256GB — boot drive
- WiFi: RTW88-8821CE, firmware v24.11.0
- Audio: HDA Intel PCH / ALC236 (speaker, headphone, internal mic)
- BT: Bluetooth service running
- Webcam: HP TrueVision HD Camera (V4L2)

## Issues Found and Resolved

| Issue | Root Cause | Fix |
|-------|-----------|-----|
| Auth loop (GDM) | / owned by christopher instead of root | chown root:root / |
| Audio silent | WirePlumber race — starts before PipeWire registers factories | ExecStartPre=/bin/sleep 1 |
| XDG dirs missing | Not created during image install | xdg-user-dirs-update |
| mDNS conflict | Avahi + systemd-resolved both running mDNS | MulticastDNS=no in resolved |
| Document portal crash | fusermount3 missing setuid bit | chmod u+s /usr/bin/fusermount3 |
| No swap | Not configured | 2GB swapfile, persisted in fstab |
| No firewall | ip_tables not compiled, no nft binary | Rebuilt kernel + built nftables |
| Old microcode | No intel-ucode firmware | Built iucode_tool, installed microcode |
| Kernel initramfs needed | CONFIG_BLK_DEV_NVME=m | Rebuilt with =y, boots without initramfs |

## BIOS Issues (HP firmware bugs — unfixable, harmless)

- ACPI AE_ALREADY_EXISTS: USB hub ports declared twice (HS01-HS10, SS01-SS06)
- ACPI WMID/WQBE: HP WMI buffer overrun
- ACPI UBTC.RUCC: Thunderbolt USB-C symbol missing
- nvme0 missing SUBNQN field (SK hynix firmware quirk)
- efifb: Ignoring BGRT (HP boot logo malformed)

## Remaining Low-Priority Issues

- gnome-shell screencast JS error (screen recording broken)
- bolt daemon missing (Thunderbolt — optional)
- dbus supplementary groups warning (cosmetic)

## setuid Pattern

Three issues (sudo, fusermount3, pkexec) traced to tar stripping setuid during image creation.
create-image.sh now fixes all setuid binaries post-copy.

## Services Running (clean boot)

23 services, 0 failed units:
accounts-daemon, avahi-daemon, bluetooth, colord, dbus, gdm, NetworkManager,
polkit, power-profiles-daemon, rtkit-daemon, sshd, systemd-journald,
systemd-logind, systemd-oomd, systemd-resolved, systemd-timesyncd,
systemd-udevd, systemd-userdbd, udisks2, upower, wpa_supplicant
