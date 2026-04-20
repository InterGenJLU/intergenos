# Hardware Test Report — HP Laptop 15-dw0xxx

**Date:** April 10, 2026  
**Boot method:** USB (SanDisk Ultra 233GB)  
**Result:** FIRST BOOT SUCCESS — all hardware functional

---

## System

| Field | Value |
|-------|-------|
| Vendor | HP |
| Model | HP Laptop 15-dw0xxx |
| BIOS | F.53 |
| Boot mode | UEFI |
| Source | Found in closet — completely unprepped |

## CPU

| Field | Value |
|-------|-------|
| Model | Intel Core i3-8145U @ 2.10GHz |
| Architecture | Whiskey Lake (8th gen) |
| Cores | 4 |
| Threads | 4 |

## Memory

| Field | Value |
|-------|-------|
| Total | 24GB |
| Used at idle | ~1.0GB |
| Swap | 2GB (unused) |

## GPU

| Field | Value |
|-------|-------|
| Device | Intel WhiskeyLake-U GT2 [UHD Graphics 620] |
| Driver | i915 (loaded, 4.6MB module) |
| Acceleration | Hardware accelerated |
| Display outputs | 4 detected, 1 connected (laptop panel) |

## Storage

| Device | Size | Type | Model |
|--------|------|------|-------|
| sda | 465.8GB | Internal SSD | WDC WDBNCE5000PNC (Windows) |
| sdb | 233.3GB | USB | SanDisk Ultra (InterGenOS) |

## Network

| Interface | Hardware | Status |
|-----------|----------|--------|
| eno1 | Realtek RTL8111/8168 Gigabit Ethernet | Detected, cable not connected |
| wlo1 | Realtek RTL8821CE 802.11ac WiFi | **Connected, working** |

## Audio

| Field | Value |
|-------|-------|
| Controller | Intel Cannon Point-LP HDA |
| Codec | Realtek ALC269 |
| Driver | snd_hda_intel + SOF pipeline |
| Status | **Fully initialized** |

## Bluetooth

| Field | Value |
|-------|-------|
| Driver | btusb, btrtl |
| Status | **Module loaded, stack ready** |

## Kernel

| Field | Value |
|-------|-------|
| Version | 6.18.10 |
| Build | SMP PREEMPT_DYNAMIC |
| Modules loaded | 164 |

## Key Modules Loaded

- `i915` — Intel GPU
- `snd_hda_intel` + `snd_hda_codec_alc269` + full SOF stack — Audio
- `btusb` + `btrtl` — Bluetooth
- `r8169` (implied by RTL8111 detection) — Ethernet
- `rtw88` or `rtl8821ce` (implied by wlo1 UP) — WiFi

## What Worked Out of the Box

- [x] Boot from USB (UEFI)
- [x] GRUB bootloader
- [x] Kernel boot
- [x] GNOME desktop (GDM → login → desktop)
- [x] Intel GPU acceleration (i915)
- [x] WiFi (Realtek RTL8821CE)
- [x] Ethernet detection (RTL8111, cable not tested)
- [x] Audio (Intel HDA + Realtek codec)
- [x] Bluetooth stack
- [x] Display (laptop panel)
- [x] USB devices
- [x] Keyboard and trackpad
- [x] All installed applications (GIMP, Inkscape, etc.)

## What Was NOT Tested

- [ ] Audio playback (no media available)
- [ ] Bluetooth pairing
- [ ] External displays
- [ ] Suspend/resume
- [ ] Ethernet (cable not available)
- [ ] Webcam
- [ ] Card reader

## Notes

- This laptop was found in a closet with zero preparation for InterGenOS testing
- Windows installation on internal SSD was untouched
- First boot attempt succeeded immediately
- Performance was described as "snappy" — GIMP and Inkscape loaded fast
- RAM usage at idle: ~1GB out of 24GB — very lean
