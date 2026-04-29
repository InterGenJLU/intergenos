# GRUB2 CVE Audit (2026-04-29)

**Package:** `packages/core/grub/`
**Version:** GRUB 2.14
**Audit Scope:** Every GRUB2 CVE fixed between v2.12 and v2.14 release (v2.12..v2.14).
**Verdict:** **COMPLIANT.** GRUB 2.14 incorporates 32 unique CVE fixes identified in the upstream git log since v2.12.

## Our GRUB Build

| Field | Value |
|---|---|
| Package | `packages/core/grub/` |
| Version | **GRUB 2.14** |
| Source | `grub-2.14.tar.xz`, upstream GNU FTP mirror |
| Platforms built | BIOS (`i386-pc`) and EFI (`x86_64-efi`) |
| Source SHA-256 | `bc8d3c73535b8838d8c8e2654d73edc4e6ae8c8acdb45d5df5dc9a1547446d43` |
| Local patches | **None** security-related. One non-security `sed` in `configure()` works around a linker-option bug introduced in 2.14 itself. |

GRUB 2.14 was released on 2026-01-14 ([upstream release announcement](https://lists.gnu.org/archive/html/grub-devel/2026-01/msg00029.html)).

## Audit Table (v2.12..v2.14)

The following 32 CVEs were addressed in the upstream GRUB repository between the v2.12 and v2.14 tags.

| CVE-ID | Severity | Component | Status | Upstream Citation |
|---|---|---|---|---|
| **CVE-2018-1000654** | High | libtasn1 | Fixed in 2.14 | `9a26abbc3` libtasn1: Import libtasn1-4.19.0 |
| **CVE-2023-4001** | Medium | search | Fixed in 2.14 | `ed691c0e0` commands/search: Introduce the --cryptodisk-only argument |
| **CVE-2024-45774** | High (6.7) | jpeg parser | Fixed in 2.14 | `2c34af908` video/readers/jpeg: Do not permit duplicate SOF0 markers in JPEG |
| **CVE-2024-45775** | Medium (5.2) | extcmd | Fixed in 2.14 | `05be856a8` commands/extcmd: Missing check for failed allocation |
| **CVE-2024-45776** | High (6.7) | gettext | Fixed in 2.14 | `09bd6eb58` gettext: Integer overflow leads to heap OOB write or read |
| **CVE-2024-45777** | High (6.7) | gettext | Fixed in 2.14 | `b970a5ed9` gettext: Integer overflow leads to heap OOB write |
| **CVE-2024-45778** | Low (4.1) | bfs fs | Fixed in 2.14 | `26db66050` fs/bfs: Disable under lockdown |
| **CVE-2024-45779** | Low (4.1) | bfs fs | Fixed in 2.14 | `26db66050` fs/bfs: Disable under lockdown |
| **CVE-2024-45780** | High (6.7) | tar fs | Fixed in 2.14 | `0087bc690` fs/tar: Integer overflow leads to heap OOB write |
| **CVE-2024-45781** | High (6.7) | ufs fs | Fixed in 2.14 | `c1a291b01` fs/ufs: Fix a heap OOB write |
| **CVE-2024-45782** | High (6.7) | hfs fs | Fixed in 2.14 | `417547c10` fs/hfs: Fix stack OOB write with grub_strcpy() |
| **CVE-2024-45783** | Medium (4.4) | hfs+ fs | Fixed in 2.14 | `f7c070a2e` fs/hfsplus: Set a grub_errno if mount fails |
| **CVE-2024-49504** | High | cryptodisk | Fixed in 2.14 | `13febd78d` disk/cryptodisk: Require authentication after TPM unlock for CLI access |
| **CVE-2024-56737** | High | hfs fs | Fixed in 2.14 | `417547c10` fs/hfs: Fix stack OOB write with grub_strcpy() |
| **CVE-2025-0622** | Medium (6.4) | gpg/pgp | Fixed in 2.14 | `2123c5bca` commands/pgp: Unregister the "check_signatures" hooks on module unload |
| **CVE-2025-0624** | High (7.5) | network | Fixed in 2.14 | `5eef88152` net: Fix OOB write in grub_net_search_config_file() |
| **CVE-2025-0677** | Medium (6.4) | ufs fs | Fixed in 2.14 | `c4bc55da2` fs: Disable many filesystems under lockdown |
| **CVE-2025-0678** | Medium (6.4) | squash4 fs | Fixed in 2.14 | `84bc0a9a6` fs: Prevent overflows when allocating memory for arrays |
| **CVE-2025-0684** | Medium (6.4) | reiserfs fs | Fixed in 2.14 | `c4bc55da2` fs: Disable many filesystems under lockdown |
| **CVE-2025-0685** | Medium (6.4) | jfs fs | Fixed in 2.14 | `c4bc55da2` fs: Disable many filesystems under lockdown |
| **CVE-2025-0686** | Medium (6.4) | romfs fs | Fixed in 2.14 | `c4bc55da2` fs: Disable many filesystems under lockdown |
| **CVE-2025-0689** | Medium (6.4) | udf fs | Fixed in 2.14 | `c4bc55da2` fs: Disable many filesystems under lockdown |
| **CVE-2025-0690** | Medium (6.1) | read cmd | Fixed in 2.14 | `dad8f5029` commands/read: Fix an integer overflow when supplying more than 2^31 characters |
| **CVE-2025-1118** | Medium (4.4) | dump cmd | Fixed in 2.14 | `34824806a` commands/minicmd: Block the dump command in lockdown mode |
| **CVE-2025-1125** | Medium (6.4) | hfs fs | Fixed in 2.14 | `84bc0a9a6` fs: Prevent overflows when allocating memory for arrays |
| **CVE-2025-4382** | Medium (6.4) | rescue | Fixed in 2.14 | `c448f511e` kern/rescue_reader: Block the rescue mode until the CLI authentication |
| **CVE-2025-54770** | Low (4.1) | net | Fixed in 2.14 | `10e58a14d` net/net: Unregister net_set_vlan command on unload |
| **CVE-2025-54771** | Medium (5.2) | file | Fixed in 2.14 | `c4fb4cbc9` kern/file: Call grub_dl_unref() after fs->fs_close() |
| **CVE-2025-61661** | High (6.7) | usb | Fixed in 2.14 | `549a9cc37` commands/usbtest: Use correct string length field |
| **CVE-2025-61662** | Low (4.1) | gettext | Fixed in 2.14 | `8ed78fd9f` gettext/gettext: Unregister gettext command on module unload |
| **CVE-2025-61663** | Low (4.1) | normal | Fixed in 2.14 | `05d3698b8` normal/main: Unregister commands on module unload |
| **CVE-2025-61664** | Low (4.1) | normal | Fixed in 2.14 | `05d3698b8` normal/main: Unregister commands on module unload |

*CVSS scores and qualitative bands sourced from [GRUB2 vulnerabilities - 2025/02/18 security patch series](https://lists.gnu.org/archive/html/grub-devel/2025-02/msg00024.html).*

## Historic CVE Clusters (Inherited Fixes)

As GRUB 2.14 is downstream of 2.12, it inherits the following historical security fixes mandatory for shim-review:

| Cluster | Key CVEs | Status |
|---|---|---|
| **BootHole (2020)** | CVE-2020-10713, CVE-2020-14372 | Fixed in 2.14 |
| **Secure Boot Bypass (2021)** | CVE-2021-3418, CVE-2021-3695 | Fixed in 2.14 |
| **Network/Shim-lock (2022)** | CVE-2022-28733 | Fixed in 2.14 |
| **NTFS Cluster (2023)** | CVE-2023-4692 | Fixed in 2.14 |

## Verification Procedure

Reviewers can independently verify the audit by running the following commands on the upstream GRUB repository:

1. **Extract unique CVE IDs:**
   ```bash
   git clone https://git.savannah.gnu.org/git/grub.git
   git -C grub log grub-2.12..grub-2.14 --grep="CVE-" --format="%B" | grep -oE "CVE-[0-9]{4}-[0-9]+" | sort -u
   ```
   *Expected result: 32 unique CVE identifiers.*

2. **Verify specific fix commit:**
   ```bash
   git -C grub log grub-2.12..grub-2.14 --grep="<CVE-ID>" --format="%H %s"
   ```

## Post-2.14 Monitoring

As of **2026-04-29**, an automated search confirms zero new CVE assignments for GRUB2 post-v2.14 (released 2026-01-14). 

**Verification query (git):**
```bash
# Check for any commits mentioning CVE in the subject or body since 2.14 release
git log --since="2026-01-14" --grep="CVE" --format="%H %s"
```
*Result: (empty output)*

**Verification query (mailing list):**
Searched [GNU grub-devel archives](https://lists.gnu.org/archive/cgi-bin/namazu.cgi?query=CVE&idxname=grub-devel&max=100&result=normal&sort=date:late) for "CVE" mentions since Jan 2026. Findings:
- Jan-Feb 2026: Discussions related to the 2.14 release and backported maintenance.
- March 2026: Post-release bug fixes (e.g., `mmap` integer overflow) without CVE assignment.
- April 2026: No new CVE disclosures.

**Notable Non-CVE Security-Adjacent Commits Post-2.14:**
- `170221b35` mmap/mmap: Fix integer overflow in binary search (Prevents infinite loop lockup via crafted `badram` command; mitigated by lockdown).

## Residual Risk

- **Possible CVEs assigned between 2.14 release (2026-01-14) and this audit (2026-04-21)** that have not yet reached public tracking. Mitigation: cross-check with Red Hat, Ubuntu, Fedora, and SUSE GRUB2 security pages immediately before PR-open.
- **Non-CVE security-relevant findings** (static-analysis Coverity fixes bundled into 2.14 without CVE assignment). Covered implicitly by our 2.14 baseline but not individually enumerated here.

## Maintenance Commitment

For the lifetime of the shim-review submission, any new GRUB2 CVE affecting our shipped version triggers:

1. Upstream patch review within 48 hours of public disclosure.
2. Backport to our 2.14 package (or version bump if 2.14 remains the latest branch).
3. New release artifact with SBAT generation incremented as required.
4. This audit updated with the new CVE entry and coverage status.

See [SECURITY.md](../SECURITY.md) for the full disclosure policy (48h acknowledgment, 14-day fix target for CRITICAL severity which includes Secure Boot chain breaks).

## References

- [GRUB 2.14 release announcement](https://lists.gnu.org/archive/html/grub-devel/2026-01/msg00029.html) - GNU grub-devel mailing list, 2026-01-14
- [GRUB2 vulnerabilities - 2025/02/18 patch set](https://lists.gnu.org/archive/html/grub-devel/2025-02/msg00024.html) - GNU grub-devel mailing list
- [GRUB2 vulnerabilities - 2021/03/02 patch bundle](https://lists.gnu.org/archive/html/grub-devel/2021-03/msg00007.html) - GNU grub-devel mailing list
- [GRUB2 NTFS driver vulnerabilities - 2023/10/03](https://lists.gnu.org/archive/html/grub-devel/2023-10/msg00028.html) - GNU grub-devel mailing list
- [Red Hat: BootHole Vulnerability (CVE-2020-10713)](https://access.redhat.com/security/vulnerabilities/grub2bootloader)
- [Red Hat: RHSB-2021-003 - ACPI Secure Boot bypass](https://access.redhat.com/security/vulnerabilities/RHSB-2021-003)
- [Ubuntu Security Team: GRUB2 Secure Boot Bypass 2021](https://wiki.ubuntu.com/SecurityTeam/KnowledgeBase/GRUB2SecureBootBypass2021)
- [CVE Details - GNU GRUB2 vulnerability list](https://www.cvedetails.com/vulnerability-list/vendor_id-72/product_id-32736/GNU-Grub2.html)
