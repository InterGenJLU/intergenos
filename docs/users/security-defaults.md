# InterGenOS Security Defaults

InterGenOS is built on a doctrine of security-only alignment: security is not first, it is only. When a security control conflicts with convenience, security wins. For anyone evaluating InterGenOS, this document provides an at-a-glance summary of the concrete protections enforced by default.

## 1. At A Glance

Out of the box, InterGenOS builds a fully signed boot chain — Secure Boot enforcement is optional and ships off by default on current target hardware, so the signatures are present and verifiable, enforced by firmware where you turn it on — plus sealed, dm-verity-protected live and install media (the installed root filesystem is an ordinary writable filesystem you own) and AppArmor mandatory access control, with the two InterGenOS-authored profiles shipping in complain mode on the default image and the mirror's server packages shipping enforce-mode profiles. Every system service is sandboxed using extensive systemd isolation directives. The package mirror uses an end-to-end signed index, and InterGenOS enforces a strict zero-telemetry, zero-analytics, zero-auto-update privacy boundary.

## 2. What's Protected By Default

InterGenOS does not rely on post-installation hardening scripts. The environment is hardened from the moment the system boots.

- **Secure Boot**: The boot chain is signed end to end — anchored by a Microsoft-signed shim (the pre-signed shim from Fedora) that validates the InterGenOS GRUB bootloader, which in turn verifies the Linux kernel and Unified Kernel Images. Unsigned kernel modules are not trusted. Secure Boot enforcement itself is optional and ships off by default on current target hardware: the signatures are present and verifiable, and firmware enforces them once you turn Secure Boot on. If you need out-of-tree modules such as proprietary drivers, the Forge installer walks you through enrolling a Machine Owner Key (MOK) on your first boot. Installed systems regenerate and sign each kernel's UKI with your machine's local MOK at every kernel install or upgrade, so the same boot-time signature verification continues to apply to kernels you install after the original ISO image. The InterGenOS release-signing key never leaves a hardware token under InterGenOS control; only the MOK that Forge generates on your own machine signs the kernels you install. See the [Secure Boot and MOK Guide](secure-boot-and-mok.md) for the full signed-versus-enforced breakdown.
- **Sealed Install Media**: The live ISO's root filesystem is a read-only squashfs protected by dm-verity, so a tampered install medium cannot present itself as genuine — corruption or tampering is caught at boot rather than surfacing as a mysterious runtime failure.
- **AppArmor Mandatory Access Control**: AppArmor is loaded and profiles ship with the packages they confine. On the default image the two InterGenOS-authored profiles (`intergen-mcp` and `pkm`) ship in **complain** mode: a violation is logged and allowed, which is the learning stage before a profile graduates. Graduation to **enforce** happens per profile. The server packages on the mirror (nginx, apache-httpd, caddy, lighttpd, haproxy, memcached, valkey, postgresql, mariadb, influxdb, etcd) each ship an **enforce**-mode profile that takes effect when that server is installed, so a network-facing service that steps outside its profile is denied rather than logged.
- **Aggressive Systemd Hardening**: System services are sandboxed to minimize the blast radius of a potential compromise. Baseline directives applied across system daemons include:
  - NoNewPrivileges=true
  - ProtectSystem=strict
  - ProtectHome=true
  - PrivateTmp=true
  - PrivateDevices=true
  - ProtectKernelTunables=true
  - ProtectKernelModules=true
  - ProtectKernelLogs=true
  - ProtectControlGroups=true
  - ProtectHostname=true
  - ProtectClock=true
  - ProtectProc=invisible
  - RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
  - RestrictNamespaces=true
  - RestrictRealtime=true
  - RestrictSUIDSGID=true
  - LockPersonality=true
  - MemoryDenyWriteExecute=true (Except for packages explicitly requiring JIT, like PostgreSQL's LLVM JIT)
  - RemoveIPC=true
  - SystemCallArchitectures=native
  - SystemCallFilter=@system-service
  - SystemCallFilter=~@privileged @resources @mount @swap @reboot
- **Safe Network Binds**: Any server package shipped by InterGenOS binds exclusively to localhost (127.0.0.1) by default. Services never listen on public interfaces unless you deliberately edit their configuration to allow it.
- **No Default Passwords**: InterGenOS does not ship databases or services with blank or default "admin" passwords. Initial credentials are randomly generated or require manual setup during installation.

## 3. The Signed Binary Mirror

When you install software, you pull from repo.intergenos.org. This mirror is signed end-to-end to prevent tampering.

Every time you run `pkm sync`, your machine cryptographically verifies the `InterGenOS.db` index signature against the offline-generated InterGenOS master release key. When downloading a package, pkm validates the file's SHA-256 hash locally before installation. InterGenOS enforces an index-only signature trust model for the v1.0 release, providing a centralized, verifiable source of truth.

For a deeper look at the repository verification process, read the [Repository Trust Model](../repository-trust.md) and the [Per-Archive Signature Decision](../architecture/per-archive-sig-decision.md).

## 4. The Build Chain

Every package is built from source in an isolated, immutable build VM.

- **Zero-PyPI Methodology**: To protect the supply chain during active attack windows targeting Python packages, InterGenOS sources critical dependencies from verified GitHub release tags rather than relying on PyPI. For an example of this pattern, see the maturin package definition (`packages/core/maturin/`).
- **Reproducible Vendor Pipelines**: Rust and Go packages use a reproducible cargo-vendor (or equivalent) pipeline. Dependencies are fetched, verified, and packaged offline, so upstream ecosystem volatility cannot break the build or inject compromised code during a compile step.
- **Software Bill of Materials (SBOM)**: InterGenOS ships a deterministic SPDX 2.3 JSON SBOM for the Secure Boot shim — the boot chain's root of trust — listing its dependencies and source hashes, readable with ordinary tools.

## 5. What We Don't Do

In InterGenOS, silence is golden.

- **No Telemetry**: InterGenOS collects zero analytics, crash reports, or usage statistics.
- **No Auto-Updates**: Your system will not update software behind your back. Updates happen when you explicitly run `pkm update`.
- **No Opt-Out Privacy**: You do not have to flip toggles in a settings menu to stop your OS from sending data to the cloud. The data never leaves to begin with.
- **No Proprietary Firmware in Core**: The core operating system relies exclusively on open-source drivers and firmware. Proprietary blobs are available if your hardware strictly requires them, but they are never forced on you.

## 6. Further Reading

- Need to set up your system? Read the [Getting Started Guide](../getting-started.md).
- Have general questions? Check the [Frequently Asked Questions](../faq.md).
- Want to verify how we handle vulnerability disclosures? Read our [Security Policy](../../SECURITY.md).
- Interested in a reviewer-focused CVE breakdown? See our [GRUB2 CVE Audit](../grub2-cve-audit.md).
- Curious about the software available? Browse the [Databases on InterGenOS](databases.md) overview.
