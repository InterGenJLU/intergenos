# Hardware Test Report — HP Laptop 17-ak0xx

**Date:** April 10, 2026  
**Boot method:** USB (SanDisk Ultra 233GB)  
**Result:** FIRST BOOT SUCCESS — all hardware functional

---

## System

| Field | Value |
|-------|-------|
| Vendor | HP |
| Model | HP Laptop 17-ak0xx |
| Boot mode | UEFI |
| Source | Found in closet — second unprepped hardware test |

## CPU

| Field | Value |
|-------|-------|
| Model | AMD A12-9720P RADEON R7, 12 COMPUTE CORES 4C+8G |
| Architecture | Bristol Ridge (AMD 7th gen APU) |
| Cores | 4 |

## Memory

| Field | Value |
|-------|-------|
| Total | 16GB |
| Used at idle | ~963MB |

## GPU

| Field | Value |
|-------|-------|
| Device | AMD/ATI Wani [Radeon R5/R6/R7 Graphics] |
| Driver | amdgpu (open source) |
| Type | APU integrated graphics |

## Storage

| Device | Size | Type | Model |
|--------|------|------|-------|
| sda | 465.8GB | Internal SSD | PNY CS900 500GB (Ubuntu LVM) |
| sdb | 233.3GB | USB | SanDisk Ultra (InterGenOS) |
| sr0 | — | DVD-RW | HP DVDRW DA8AESH |

## Network

| Interface | Hardware | Status |
|-----------|----------|--------|
| eno1 | Realtek RTL8111/8168 Gigabit Ethernet | Detected |
| wlo1 | Realtek RTL8188EE 802.11n WiFi | **Connected, working** |

## Audio

| Field | Value |
|-------|-------|
| Controller 1 | AMD/ATI Kabini HDMI/DP Audio |
| Controller 2 | AMD Family 15h Audio Controller |
| Cards | HDA ATI HDMI + HD-Audio Generic |
| Status | **Fully initialized** |

## Kernel

| Field | Value |
|-------|-------|
| Version | 6.18.10 |
| Modules loaded | 81 |

## What Worked Out of the Box

- [x] Boot from USB (UEFI)
- [x] GRUB bootloader
- [x] Kernel boot
- [x] GNOME desktop
- [x] AMD GPU (amdgpu driver)
- [x] WiFi (Realtek RTL8188EE)
- [x] Audio detection (AMD HDA)
- [x] USB devices
- [x] Keyboard and trackpad
- [x] DVD drive detected

## Significance

This is the SECOND bare metal test. First test was Intel CPU + Intel GPU (HP Laptop 15-dw0xxx). This test is AMD CPU + AMD GPU (HP Laptop 17-ak0xx). Both architectures boot and work first try with zero configuration. WiFi works on both despite different Realtek chipsets (RTL8821CE vs RTL8188EE).
