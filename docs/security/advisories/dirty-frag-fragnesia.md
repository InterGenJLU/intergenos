# Dirty Frag + Fragnesia — InterGenOS Security Advisory

**Advisory ID:** IGOS-SA-2026-002
**Issued:** 2026-05-18
**Severity:** High (local privilege escalation to root)
**InterGenOS versions affected:** All builds shipping kernel 6.18.10 before commit `<this-commit>`
**Status:** Mitigated by in-tree kernel patches + defense-in-depth module blacklist. Mitigation applies on next kernel rebuild + ISO rebuild.

---

## Summary

Three upstream Linux kernel CVEs disclosed between May 7 and May 13, 2026 — collectively the "Dirty Frag" cluster — affect every shipped InterGenOS build using kernel 6.18.10 (the version we have pinned and built since pre-release work began). All three are local privilege escalation to root via in-place decryption paths in the IPsec ESP and rxrpc receive code.

InterGenOS has had zero pre-installed users facing public exposure during this window (operator + soon Ethan via builder), but the kernel image has been vulnerable as built. This advisory documents the cluster and the fix landed at commit `<this-commit>`.

## The cluster

| CVE | Subsystem | Mainline commit | Disclosed | Found by |
|---|---|---|---|---|
| **CVE-2026-43284** ("Dirty Frag" / xfrm-ESP) | `xfrm: esp` — IPv4/IPv6 ESP receive path | `f4c50a4034e6` | 2026-05-07 | Hyunwoo Kim + Kuan-Ting Chen |
| **CVE-2026-43500** ("Dirty Frag" / rxrpc) | `rxrpc` socket layer (OpenAFS protocol) | `aa54b1d27fe0` | 2026-05-08 (PoC released before mainline merge on 2026-05-10) | Hyunwoo Kim |
| **CVE-2026-46300** ("Fragnesia") | `net/core/skbuff.c` — `skb_try_coalesce()` | `f84eca581739` | 2026-05-13 | William Bowling (V12 Security / Zellic) |

### Root cause (shared across all three)

The Linux socket-buffer (`sk_buff`) data path uses a flag `SKBFL_SHARED_FRAG` to mark socket buffers whose paged fragments are externally owned — e.g., pages from a pipe attached via `MSG_SPLICE_PAGES`, or page-cache-backed pages from `sendfile()`. Later code paths that may modify packet data in-place are supposed to check this marker and make a private copy first.

The cluster of CVEs is the result of three independent code paths failing to either set or propagate that marker correctly. When the marker is missing, the IPsec ESP receive path performs **in-place AES-GCM decryption directly on page-cache pages**. An unprivileged process holding a reference to those pages — including pages backing files like `/usr/bin/su` or `/etc/passwd` — can observe the decrypted plaintext written into them, gaining a kernel-mediated write primitive into arbitrary page-cache content. From there, root is trivial.

The three CVEs are sequential discoveries in the same fragile area:

- CVE-2026-43284 — IPv4/IPv6 UDP datagram-append code didn't set `SKBFL_SHARED_FRAG` when splicing pages into UDP skbs
- CVE-2026-43500 — rxrpc handler only made the private copy when `skb_cloned()` was true; it didn't handle the case of an uncloned skb that still carries externally-owned paged fragments
- CVE-2026-46300 — `skb_try_coalesce()` transferred paged fragments between skbs but failed to propagate the `SKBFL_SHARED_FRAG` flag, leaving the resulting skb in a state the ESP receive path mis-trusted

Public exploit code exists for all three, particularly Fragnesia (William Bowling published a working PoC concurrent with disclosure).

## InterGenOS exposure

- **Kernel version:** 6.18.10 (released February 11, 2026 — over three months before the first CVE in this cluster)
- **Pre-fix patches in tree:** only `CVE-2026-31431-copy-fail.patch` (algif_aead, April 29, 2026)
- **Pre-fix verdict:** **fully vulnerable** to all three Dirty Frag CVEs

No InterGenOS ISO has been shipped publicly yet (still in development); the operator is currently the only user. Builder access by Ethan was pending greenlight at advisory issuance. **No public-user impact from this window.**

## Mitigation landed at commit `<this-commit>`

### 1. In-tree kernel patches

Three new patches in `packages/core/linux-kernel/patches/`:

- `CVE-2026-43284-xfrm-esp-shared-frags.patch` (mainline commit `f4c50a4034e6`, applies cleanly to 6.18.10)
- `CVE-2026-43500-rxrpc-shared-frags.patch` (**InterGenOS-specific 6.18.10 backport** — see note below)
- `CVE-2026-46300-fragnesia-skbuff-coalesce.patch` (mainline commit `f84eca581739`, applies cleanly to 6.18.10)

**CVE-2026-43500 disposition (revised 2026-05-18 evening).** The upstream mainline commit `aa54b1d27fe0` modifies `net/rxrpc/call_event.c` and `net/rxrpc/conn_event.c`. Those file locations correspond to a refactor introduced by upstream commit `d0d5c0cd1e71` ("rxrpc: Use skb_unshare() rather than skb_cow_data()") which post-dates 6.18.10 — in 6.18.10's source those files contain zero `skb_cloned` / `skb_unshare` references and the vulnerable code path the mainline patch modifies simply does not exist there. The structurally equivalent vulnerable site in 6.18.10 lives in `net/rxrpc/io_thread.c` (`rxrpc_input_packet()`, DATA case) and in the RESPONSE-packet code path that flows through `rxrpc_process_event()` → `security->verify_response()` → `rxkad_decrypt_response()` in-place. The shipped patch is an InterGenOS-authored custom backport targeting `io_thread.c` — widening the unshare-or-copy gate in both the DATA case and (new) the server-side RESPONSE case to call `skb_copy()` whenever `skb_has_frag_list()` or `skb_has_shared_frag()` is true, regardless of `skb_cloned()` status. Patch provenance, full reasoning, and the disposition for replacing it with upstream stable's 6.18.y backport (when issued) are documented in the patch file header itself.

Patch kernel version pin: 6.18.10 is the InterGenOS v1.0 kernel; the operator pin is binding. Backport rather than kernel bump is the v1.0 path.

Applied automatically by `packages/core/linux-kernel/build.sh` on kernel build. **Mitigation requires a kernel rebuild** — the patches are not active on existing built artifacts until the kernel is rebuilt and the resulting `vmlinuz` + UKI replace what's currently on disk.

### 2. Defense-in-depth module blacklist

Shipped to `/etc/modprobe.d/igos-dirty-frag-mitigation.conf` via `scripts/chroot-config-ch9.sh`. Refuses to load the `esp4`, `esp6`, and `rxrpc` modules at all:

```
install esp4 /bin/false
install esp6 /bin/false
install rxrpc /bin/false
```

**Rationale:** Fragnesia is itself proof that the original Dirty Frag patches did not cover the related code path. That's the failure mode defense-in-depth pays for — if a "Fragnesia-2" CVE drops next week in the same area before we ship a fresh kernel, the blacklist blocks the modules from loading regardless of patch state.

The decision to ship the blacklist by default was filtered through the InterGenOS Prime Directive ("user in control") and Holy Grail ("security is only") rules:

- User control preserved — the file is plainly visible, user-editable. Any user needing IPsec ESP or OpenAFS can `rm` the file or `modprobe esp4` manually.
- Modern consumer VPNs (WireGuard, OpenVPN) do not use the `esp4`/`esp6` modules. `rxrpc` is OpenAFS-only — extremely niche on desktop Linux. Blacklisting these by default breaks essentially no consumer-facing workflow on v1.0.
- For the current user base (operator + Ethan), the trade-off is convenience for hypothetical-future-IPsec users vs security for actual users. Under the filter, security wins.

### Opt-out

Users who actually depend on IPsec ESP (traditional enterprise/site-to-site VPN) or OpenAFS:

```sh
# Remove the blacklist (requires root)
sudo rm /etc/modprobe.d/igos-dirty-frag-mitigation.conf

# Or selectively unblock just one module by commenting its line
sudo sed -i 's|^install esp4|#install esp4|' /etc/modprobe.d/igos-dirty-frag-mitigation.conf
sudo modprobe esp4

# After modifying the blacklist, reload module dependency cache
sudo depmod -a
```

The kernel patches above remain in effect even with the blacklist removed — opt-out loses the defense-in-depth layer but keeps the primary fix.

## Composition with other security work

- **CVE-2026-31431 (copy-fail)** — pre-existing patch in tree (April 29, 2026). Unrelated to this cluster but the same upstream-patch pattern.
- **D-007** (SSH + root + credentials posture) — orthogonal. D-007 closes remote-rootable paths via SSH credential exposure; this advisory closes local-PE-to-root paths via kernel data structures.
- **E-001 microcode kernel-config gap** — fixed across two commits. Initial fix (`ed09604d`) added `CONFIG_MICROCODE_INTEL=y` + `CONFIG_MICROCODE_AMD=y` + `CONFIG_MICROCODE_LATE_LOADING=y` to `config/kernel/fragments/99-intergenos-overrides.config`. **Post-rebuild discovery (IGOSC, 2026-05-18 19:29Z):** kernel 6.18 Kconfig refactored away the separate `MICROCODE_INTEL`/`MICROCODE_AMD` symbols — they no longer exist as independent options. `MICROCODE` now `depends on CPU_SUP_AMD || CPU_SUP_INTEL`. `olddefconfig` silently dropped the obsolete vendor flags. Microcode loading actually became live through the inherited baseline `CPU_SUP_*` defaults, not through the explicit symbols we set. **Corrective fix:** `99-intergenos-overrides.config` now explicitly declares `CONFIG_CPU_SUP_INTEL=y` + `CONFIG_CPU_SUP_AMD=y` + `CONFIG_MICROCODE_LATE_LOADING=y` as the 6.18-correct chain. IGOSC verified post-reboot: `dmesg` shows `microcode: Updated early from: 0x000000a8 -> Current revision: 0x000000c6` — microcode actually reaching the CPU for the first time in InterGenOS history. SRBDS + GDS mitigations now active via microcode (not just kernel-side software workarounds).
- **F-005 AMD microcode early-load** — fixed in the same commit. `scripts/create-image.sh` now generates `/boot/amd-ucode.img` and patches grub.cfg with the AMD initrd line (mirror of the existing Intel path). `linux-firmware` already shipped the AMD microcode blobs at `/lib/firmware/amd-ucode/`; only the early-load image assembly was missing.

## Required action for InterGenOS builds going forward

Any future ISO build picks up the patches automatically — `linux-kernel`'s `build.sh` applies every `*.patch` in alphabetical order from `packages/core/linux-kernel/patches/` at configure time. **No manual step needed** beyond rebuilding the kernel (and the UKI, since the kernel image changed).

## Sources

- [AlmaLinux: Dirty Frag (CVE-2026-43284, CVE-2026-43500) Patches Released](https://almalinux.org/blog/2026-05-07-dirty-frag/)
- [AlmaLinux: Fragnesia (CVE-2026-46300) Patches Released](https://almalinux.org/blog/2026-05-13-fragnesia-cve-2026-46300/)
- [Red Hat RHSB-2026-003: Networking subsystem Privilege Escalation (Dirty Frag)](https://access.redhat.com/security/vulnerabilities/RHSB-2026-003)
- [Microsoft Security Blog: Active attack — Dirty Frag Linux vulnerability expands post-compromise risk (May 8, 2026)](https://www.microsoft.com/en-us/security/blog/2026/05/08/active-attack-dirty-frag-linux-vulnerability-expands-post-compromise-risk/)
- [Help Net Security: Fragnesia: New Linux kernel LPE bug was spawned by Dirty Frag patch (May 14, 2026)](https://www.helpnetsecurity.com/2026/05/14/fragnesia-cve-2026-46300-linux-lpe-vulnerability/)
- [Tenable: Dirty Frag FAQ](https://www.tenable.com/blog/dirty-frag-cve-2026-43284-cve-2026-43500-frequently-asked-questions-linux-kernel-lpe)
- [TuxCare: Fragnesia is a new Linux kernel LPE in the Dirty Frag family](https://tuxcare.com/blog/fragnesia-cve-2026-46300-is-a-new-linux-kernel-lpe/)
- [V12 Security PoCs (Fragnesia)](https://github.com/v12-security/pocs/tree/main/fragnesia-5db89c99566fc)
- [netdev mailing list: PATCH net - net: skbuff: preserve shared-frag marker during coalescing](https://lists.openwall.net/netdev/2026/05/13/79)
