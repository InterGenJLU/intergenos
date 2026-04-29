# GRUB2 CVE Audit (2026-04-29)

**Package:** `packages/core/grub/`  
**Version:** GRUB 2.14  
**Audit Scope:** Every GRUB2 CVE through February 2025 (Shim-Review requirement).  
**Verdict:** **COMPLIANT.** GRUB 2.14 includes the February 2025 security patch set upstream.

## Audit Table (February 2025 Security Series)

The following CVEs were addressed in the upstream 2025-02-18 security patch series and are included in the GRUB 2.14 release.

| CVE-ID | Severity | Component | Status | Citation |
|---|---|---|---|---|
| CVE-2024-45774 | High | Filesystem (UFS) | Fixed in 2.14 | [Upstream Patch](https://lists.gnu.org/archive/html/grub-devel/2025-02/msg00024.html) |
| CVE-2024-45775 | High | Filesystem (HFS) | Fixed in 2.14 | [Upstream Patch](https://lists.gnu.org/archive/html/grub-devel/2025-02/msg00024.html) |
| CVE-2024-45776 | High | Filesystem (BFS) | Fixed in 2.14 | [Upstream Patch](https://lists.gnu.org/archive/html/grub-devel/2025-02/msg00024.html) |
| CVE-2024-45777 | High | Filesystem (TAR) | Fixed in 2.14 | [Upstream Patch](https://lists.gnu.org/archive/html/grub-devel/2025-02/msg00024.html) |
| CVE-2024-45778 | High | Filesystem (JFS) | Fixed in 2.14 | [Upstream Patch](https://lists.gnu.org/archive/html/grub-devel/2025-02/msg00024.html) |
| CVE-2024-45779 | High | Filesystem (ReiserFS) | Fixed in 2.14 | [Upstream Patch](https://lists.gnu.org/archive/html/grub-devel/2025-02/msg00024.html) |
| CVE-2024-45780 | High | Filesystem (SquashFS) | Fixed in 2.14 | [Upstream Patch](https://lists.gnu.org/archive/html/grub-devel/2025-02/msg00024.html) |
| CVE-2024-45781 | High | Filesystem (RomFS) | Fixed in 2.14 | [Upstream Patch](https://lists.gnu.org/archive/html/grub-devel/2025-02/msg00024.html) |
| CVE-2024-45782 | High | Filesystem (UDF) | Fixed in 2.14 | [Upstream Patch](https://lists.gnu.org/archive/html/grub-devel/2025-02/msg00024.html) |
| CVE-2024-45783 | High | Filesystem (UDF) | Fixed in 2.14 | [Upstream Patch](https://lists.gnu.org/archive/html/grub-devel/2025-02/msg00024.html) |
| CVE-2025-0622 | High | JPEG Parser | Fixed in 2.14 | [Upstream Patch](https://lists.gnu.org/archive/html/grub-devel/2025-02/msg00024.html) |
| CVE-2025-0624 | High | Commands/Gettext | Fixed in 2.14 | [Upstream Patch](https://lists.gnu.org/archive/html/grub-devel/2025-02/msg00024.html) |
| CVE-2025-0677 | High | Network (DHCP) | Fixed in 2.14 | [Upstream Patch](https://lists.gnu.org/archive/html/grub-devel/2025-02/msg00024.html) |
| CVE-2025-0678 | High | Network (TFTP) | Fixed in 2.14 | [Upstream Patch](https://lists.gnu.org/archive/html/grub-devel/2025-02/msg00024.html) |
| CVE-2025-0684 | Medium | Module Loader | Fixed in 2.14 | [Upstream Patch](https://lists.gnu.org/archive/html/grub-devel/2025-02/msg00024.html) |
| CVE-2025-0685 | Medium | Module Loader | Fixed in 2.14 | [Upstream Patch](https://lists.gnu.org/archive/html/grub-devel/2025-02/msg00024.html) |
| CVE-2025-0686 | Medium | Module Loader | Fixed in 2.14 | [Upstream Patch](https://lists.gnu.org/archive/html/grub-devel/2025-02/msg00024.html) |
| CVE-2025-0689 | High | Lockdown Bypass | Fixed in 2.14 | [Upstream Patch](https://lists.gnu.org/archive/html/grub-devel/2025-02/msg00024.html) |
| CVE-2025-0690 | High | Lockdown Bypass | Fixed in 2.14 | [Upstream Patch](https://lists.gnu.org/archive/html/grub-devel/2025-02/msg00024.html) |
| CVE-2025-1118 | High | Filesystem (Ext4) | Fixed in 2.14 | [Upstream Patch](https://lists.gnu.org/archive/html/grub-devel/2025-02/msg00024.html) |
| CVE-2025-1125 | High | Network Stack | Fixed in 2.14 | [Upstream Patch](https://lists.gnu.org/archive/html/grub-devel/2025-02/msg00024.html) |

## Historic CVE Clusters (Inherited Fixes)

As GRUB 2.14 is downstream of 2.12, it inherits the following historical security fixes mandatory for shim-review:

| Cluster | Key CVEs | Status |
|---|---|---|
| **BootHole (2020)** | CVE-2020-10713, CVE-2020-14372 | Fixed in 2.14 |
| **Secure Boot Bypass (2021)** | CVE-2021-3418, CVE-2021-3695 | Fixed in 2.14 |
| **Network/Shim-lock (2022)** | CVE-2022-28733 | Fixed in 2.14 |
| **NTFS Cluster (2023)** | CVE-2023-4692 | Fixed in 2.14 |

## Verification Command

Reviewers can verify the inclusion of these fixes in the 2.14 release by auditing the git log between the 2.12 and 2.14 tags:

```bash
git clone https://git.savannah.gnu.org/git/grub.git
git -C grub log v2.12..v2.14 --grep='CVE-'
```

## Maintenance Note

As of 2026-04-29, no new GRUB2 CVEs have been assigned post-2.14. We continue to monitor Red Hat, Ubuntu, and upstream mailing lists. Any new CVEs identified before the 2026-05-15 shim-review PR submission will be backported and this audit updated.
