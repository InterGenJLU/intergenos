# InterGenOS Security Defaults

InterGenOS is built on a doctrine of "Security-Only Alignment." This means that when a security control conflicts with convenience, security always wins. For anyone evaluating InterGenOS, this document provides an at-a-glance summary of the concrete protections we enforce by default.

## 1. At A Glance

Out of the box, InterGenOS enforces mandatory Secure Boot validation and strict AppArmor confinement across the daemon fleet. Every system service is sandboxed using extensive systemd isolation directives. Our package mirror uses an end-to-end signed index, and we enforce a strict zero-telemetry, zero-analytics, zero-auto-update privacy boundary. 

## 2. What's Protected By Default

InterGenOS does not rely on post-installation hardening scripts. The environment is hardened from the moment the system boots.

- **Secure Boot**: Mandatory. The boot chain is anchored by a Microsoft-signed shim that validates our InterGenOS GRUB bootloader, which subsequently verifies our Linux kernel. We do not trust unsigned kernel modules. If you need out-of-tree modules like proprietary drivers, the Forge installer will walk you through enrolling a Machine Owner Key (MOK) on your first boot.
- **AppArmor in Enforce Mode**: Unlike many distributions that leave mandatory access control in complain mode or disabled for third-party packages, InterGenOS ships AppArmor profiles for the daemon fleet in **enforce** mode by default.
- **Aggressive Systemd Hardening**: We sandbox system services to minimize the blast radius of a potential compromise. Baseline directives applied across our daemon fleet include:
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
  - MemoryDenyWriteExecute=true (Except for packages explicitly requiring JIT, like MongoDB)
  - RemoveIPC=true
  - SystemCallArchitectures=native
  - SystemCallFilter=@system-service
  - SystemCallFilter=~@privileged @resources @mount @swap @reboot
- **Safe Network Binds**: Any server package shipped by InterGenOS binds exclusively to localhost (127.0.0.1) by default. Services will never listen on public interfaces unless you deliberately edit their configuration to allow it.
- **No Default Passwords**: We do not ship databases or services with blank or default "admin" passwords. Initial credentials are randomly generated or require manual setup during installation.

## 3. The Signed Binary Mirror

When you install software, you pull from repo.intergenos.org. This mirror is signed end-to-end to prevent tampering.

Every time you run pkm sync, your machine cryptographically verifies the InterGenOS.db index signature against the offline-generated InterGenOS master release key. When downloading a package, pkm validates the file's SHA-256 hash locally before installation. We enforce an index-only signature trust model for our v1.0 release, ensuring a centralized, undeniable source of truth.

For a deeper dive into our repository verification process, read the [Repository Trust Model](../repository-trust.md) and the [Per-Archive Signature Decision](../architecture/per-archive-sig-decision.md).

## 4. The Build Chain

We build every package from source in an isolated, immutable build VM.

- **Zero-PyPI Methodology**: To protect our supply chain during active attack windows targeting Python packages, we explicitly source critical dependencies from verified GitHub release tags rather than relying on PyPI. For an example of this canonical pattern, see the maturin package definition (packages/core/maturin/).
- **Reproducible Vendor Pipelines**: All Rust and Go packages utilize a reproducible cargo-vendor (or equivalent) pipeline. This means dependencies are fetched, verified, and packaged offline, ensuring upstream ecosystem volatility cannot break our builds or inject compromised code during a compile step.
- **Software Bill of Materials (SBOM)**: InterGenOS emits SPDX 2.3 JSON SBOMs detailing exactly what dependencies and source hashes comprise our critical system binaries.

## 5. What We Don't Do

In InterGenOS, silence is golden. 

- **No Telemetry**: We collect zero analytics, crash reports, or usage statistics.
- **No Auto-Updates**: Your system will not update software behind your back. Updates happen when you explicitly type pkm update.
- **No Opt-Out Privacy**: You do not have to flip toggles in a settings menu to stop your OS from sending data to the cloud. The data never leaves to begin with.
- **No Proprietary Firmware in Core**: The core operating system relies exclusively on open-source drivers and firmware. Proprietary blobs are available if your hardware strictly requires them, but they are never forced on you.

## 6. Further Reading

- Need to set up your system? Read the [Getting Started Guide](../getting-started.md).
- Have general questions? Check the [Frequently Asked Questions](../faq.md).
- Want to verify how we handle vulnerability disclosures? Read our [Security Policy](../../SECURITY.md).
- Interested in a reviewer-focused CVE breakdown? See our [GRUB2 CVE Audit](../grub2-cve-audit.md).
- Curious about the software available? Browse the [Databases on InterGenOS](databases.md) overview.
- Want to read the technical mandate behind the systemd and AppArmor hardening? See the [Database Landing Plan](../architecture/database-landing-plan.md).
