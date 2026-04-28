# InterGenOS Firewall Architecture: nftables Only

**Date:** 2026-04-18
**Status:** Decision documented, kernel + package changes staged (untracked, awaiting owner approval)
**Origin:** Followup from an iptables-on-installed-kernel diagnosis (2026-04-18)

## Decision

InterGenOS uses **nftables exclusively** as its packet filtering framework. The legacy iptables backend (xtables interface) is structurally disabled at the kernel level. The `iptables` userspace CLI ships as a translation shim only — it accepts iptables syntax and produces nftables rules under the hood (the standard `iptables-nft` mode upstream).

## Rationale

### Origin incident
On the HP laptop's installed kernel (6.18.10), `make olddefconfig` resolved `CONFIG_NETFILTER_XTABLES_LEGACY=n` while leaving `CONFIG_NETFILTER_XTABLES=y`. Because the iptables userspace package was built with `--disable-nftables` (legacy backend only), the iptables CLI could not bind to the kernel's xtables interface. Tailscale's iptables-based firewall path failed; tailscaled fell back to nftables and worked correctly.

The fallback path is the better path. Make it the only path.

### Security rationale

- Maintaining two parallel firewall frameworks doubles attack surface for marginal compatibility benefit. Modern tooling (Docker, Podman, Kubernetes, systemd-networkd, Tailscale, firewalld) all support nftables natively.
- Dropping the iptables-legacy backend removes a deprecated code path from the kernel and its corresponding userspace.
- A single packet filtering framework is dramatically easier to audit and reason about. One firewall tool, one answer.

### User-control rationale

- A user troubleshooting their firewall runs into one tool, one syntax, one set of error messages. No "is this iptables-legacy or iptables-nft?" confusion. The system is more understandable.

## Kernel configuration

In `config/kernel/fragments/99-intergenos-overrides.config`:

```
# Explicitly disable legacy xtables interface
# CONFIG_NETFILTER_XTABLES_LEGACY is not set
# Modern netfilter API entry points (kept for nftables-backed compat)
CONFIG_NF_CONNTRACK=y
CONFIG_NF_TABLES=y
CONFIG_IP_NF_IPTABLES=y
CONFIG_IP_TABLES=y
CONFIG_IP_NF_FILTER=y
CONFIG_IP_NF_TARGET_REJECT=y
CONFIG_IP6_NF_IPTABLES=y
CONFIG_IP6_NF_FILTER=y
```

The `IP_NF_*` and `IP6_NF_*` symbols stay built-in because they're the kernel-side API surface that iptables-nft userspace talks to. Without `NETFILTER_XTABLES_LEGACY`, the legacy syscall path is gone — userspace iptables-legacy cannot bind.

## Package configuration

In `packages/desktop/iptables/package.yml`:

```yaml
configure_flags:
- --prefix=/usr
- --enable-nftables
```

This builds the iptables CLI as the iptables-nft compat shim. Users running `iptables -A INPUT -j DROP` get a properly-translated nftables rule under the hood.

The dedicated `nftables` package (in `desktop` tier) provides the native `nft` CLI for users who want to write nftables rules directly.

## Migration / operator notes

- Users with existing iptables rule scripts: rules continue to work because the syntax is identical. Storage backend is now nftables.
- `iptables-save` produces nftables-format output (different from legacy format). Backups taken under InterGenOS are not portable to legacy-backend distributions.
- `iptables-legacy` and `iptables-nft` binaries (which exist under multi-backend builds) are NOT shipped — only the unified `iptables` symlink pointing to the nft backend.
- Direct kernel module names (`ip_tables.ko`, `iptable_filter.ko`, etc.) do not load — they correspond to the disabled legacy backend. This is expected.

## Verified behavior (laptop, 2026-04-18)

- `tailscale status` → clean, no iptables-related warnings
- `firewallmode` in tailscaled logs → `nft-forced`
- `nft list ruleset` → shows tailscale's chains
- iptables binaries quarantined manually as proof-of-concept; canonical build will not ship the legacy backend in the first place.

## Unresolved followups

- [ ] Audit `packages/desktop/` for any package that depends on iptables-legacy specifically (vs. accepting either backend).
- [ ] Update `vision/README` (when it exists) to document the firewall posture for end users.
- [ ] Consider whether `iptables` package belongs in `extra` tier (opt-in) rather than `desktop` (default) — the iptables-nft compat shim is small but adds nothing for users who use `nft` directly. Defer until usage data justifies.
- [ ] Document the `nft` CLI as the preferred interface in InterGen's system-help responses.

## See also

- This change does NOT disable firewall features; it chooses one backend over two. Net capability is preserved.
