# InterGenOS Security Defaults

InterGenOS is built on a doctrine of security-only alignment: security is not first, it is only. When a security control conflicts with convenience, security wins. For anyone evaluating InterGenOS, this document provides an at-a-glance summary of the concrete protections enforced by default.

## 1. At A Glance

Out of the box, InterGenOS builds a fully signed boot chain — Secure Boot enforcement is optional and ships off by default on current target hardware, so the signatures are present and verifiable, enforced by firmware where you turn it on — plus dm-verity block verification for the live and install media (the installed root filesystem is an ordinary writable filesystem you own) and AppArmor mandatory access control. The two InterGenOS-authored profiles ship in complain mode on the default image and the mirror's server packages ship enforce-mode profiles. Systemd isolation is service-specific: many daemon units carry the full baseline, while services such as SSH and the installer backend document functionality-required exceptions. The package mirror uses an end-to-end signed index, and InterGenOS applies a zero-telemetry, zero-analytics, zero-unattended-update default.

## 2. What's Protected By Default

InterGenOS does not rely on post-installation hardening scripts. The environment is hardened from the moment the system boots.

- **Secure Boot**: The boot chain is signed end to end — anchored by a Microsoft-signed shim (the pre-signed shim from Fedora) that validates the InterGenOS GRUB bootloader, which in turn verifies the Linux kernel and Unified Kernel Images. Unsigned kernel modules are not trusted. Secure Boot enforcement itself is optional and ships off by default on current target hardware: the signatures are present and verifiable, and firmware enforces them once you turn Secure Boot on. If you need out-of-tree modules such as proprietary drivers, the Forge installer walks you through enrolling a Machine Owner Key (MOK) on your first boot. Installed systems regenerate and sign each kernel's UKI with your machine's local MOK at every kernel install or upgrade, so the same boot-time signature verification continues to apply to kernels you install after the original ISO image. The InterGenOS release-signing key never leaves a hardware token under InterGenOS control; only the MOK that Forge generates on your own machine signs the kernels you install. See the [Secure Boot and MOK Guide](secure-boot-and-mok.md) for the full signed-versus-enforced breakdown.
- **Block-verified Install Media**: The live ISO's root filesystem is a read-only squashfs protected by dm-verity, which checks its blocks against the root hash embedded in the UKI. This detects corruption in every mode; it authenticates the medium against an attacker who can replace both the image and UKI only when Secure Boot enforces the UKI signature, or when you independently verify the signed media before boot.
- **AppArmor Mandatory Access Control**: AppArmor is loaded and profiles ship with the packages they confine. On the default image the two InterGenOS-authored profiles (`intergen-mcp` and `pkm`) ship in **complain** mode: a violation is logged and allowed, which is the learning stage before a profile graduates. Graduation to **enforce** happens per profile. The server packages on the mirror (nginx, apache-httpd, caddy, lighttpd, haproxy, memcached, valkey, postgresql, mariadb, influxdb, etcd) each ship an **enforce**-mode profile that takes effect when that server is installed, so a network-facing service that steps outside its profile is denied rather than logged.
- **Service-specific Systemd Hardening**: Hardened daemon units commonly use directives such as the following. Not every directive applies to every service; each unit's file is the authority for its effective sandbox.
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
- **Kernel Hardening Floor**: A shipped sysctl drop-in (`/usr/lib/sysctl.d/60-intergenos-hardening.conf`) sets values the kernel otherwise leaves loose: kernel addresses are hidden from unprivileged readers (`kernel.kptr_restrict = 1`), address-space layout randomisation covers the heap as well as the stack and libraries (`kernel.randomize_va_space = 2`, paired with a kernel built without `CONFIG_COMPAT_BRK` so the value is honoured), the reverse path filter is strict on every interface (a packet whose source could not be routed back the way it came is dropped), ICMP redirects are neither accepted nor sent, and packets with impossible source addresses are logged. These are a floor: a host with asymmetric routing sets the loose reverse-path value in `/etc/sysctl.d/`, which sorts after the shipped file and wins. Simultaneous multithreading (SMT) is left on: the kernel carries the mitigations for the known SMT-class flaws, the workloads on the machine are your own, and halving the processor is a cost you choose with `nosmt` or `mitigations=auto,nosmt` on the kernel command line. Values already hardened by the kernel configuration (restricted `dmesg`, Yama ptrace scope, unprivileged BPF disabled, protected links and FIFOs) are unchanged. The installed-system gate tier reads every value back from the running kernel.
- **Remote Login Posture**: The SSH server is off unless you turn it on during the install, and when it does run it runs with a stated posture rather than upstream's defaults: root cannot log in, password authentication is off in favour of keys, an idle session is dropped after ten minutes, three failed attempts end the connection, and a login that has not completed in thirty seconds is closed. **Forwarding is off in both directions** — a person who logs in cannot open tunnels through your machine to anything it can reach (`AllowTcpForwarding no`), and your machine cannot reach back into the key agent on the computer they connected from (`AllowAgentForwarding no`). If you want forwarding, turn it on knowingly: put `AllowTcpForwarding yes` in a file such as `/etc/ssh/sshd_config.d/90-local.conf`, which sorts after the shipped settings, and restart the service. `sshd -T` prints what the server actually decided, which is the answer to trust rather than any file.
- **Keys You Were Shown**: The installer accepts one SSH public key per line, including the whole contents of an existing `authorized_keys` file, and it checks every line rather than the first. Each key it accepts is shown back to you with its type, its comment and its SHA-256 fingerprint, and only the keys you tick are written — compare a fingerprint against `ssh-keygen -lf` on the machine the key came from before you keep it. A line it cannot use is named with the reason (a private key, an options prefix, a truncated paste, a certificate, the same key twice) instead of being dropped quietly, and the install does not continue until you have dealt with it. If no usable key is installed, password authentication is left on and the install says so, rather than shipping a server nobody can log in to.
- **Safe Database Binds**: The database-server defaults described in [Databases](databases.md) bind to loopback (`127.0.0.1` and, where configured, `::1`). Other server packages follow their own configuration and are generally disabled until explicitly enabled.
- **No Shared Administrator Passwords**: Credential setup is service-specific; no archive-baked administrator password shared by every installation is shipped. Some loopback-only caches deliberately start without authentication and must not be exposed until you configure it.

## 3. The Signed Binary Mirror

When you install software, you pull from repo.intergenos.org. This mirror is signed end-to-end to prevent tampering.

Every time you run `pkm sync`, your machine cryptographically verifies the `InterGenOS.db` index signature against the offline-generated InterGenOS master release key. When downloading a package, pkm validates the file's SHA-256 hash locally before installation. InterGenOS enforces an index-only signature trust model for the v1.0 release, providing a centralized, verifiable source of truth.

For a deeper look at the repository verification process, read the [Repository Trust Model](../repository-trust.md) and the [Per-Archive Signature Decision](../architecture/per-archive-sig-decision.md).

## 4. The Build Chain

Every package is built from source in an isolated build VM. The only binary inputs are the Microsoft-signed shim and vendor firmware and microcode, each pinned by hash and named as such.

- **Zero-PyPI Methodology**: To protect the supply chain during active attack windows targeting Python packages, InterGenOS sources critical dependencies from verified GitHub release tags rather than relying on PyPI. For an example of this pattern, see the maturin package definition (`packages/core/maturin/`).
- **Reproducible Vendor Pipelines**: Rust and Go packages use a reproducible cargo-vendor (or equivalent) pipeline. Dependencies are fetched, verified, and packaged offline, so upstream ecosystem volatility cannot break the build or inject compromised code during a compile step.
- **Software Bill of Materials (SBOM)**: Each release publishes a deterministic SPDX 2.3 JSON SBOM for the ISO's exact package set. The source tree also contains a separate SBOM for the prospective InterGenOS shim-review build; that document does not describe the Fedora-signed shim binary the current release packages.

## 5. What We Don't Do

In InterGenOS, silence is golden.

- **No Telemetry**: InterGenOS collects zero analytics, crash reports, or usage statistics.
- **No Auto-Updates**: Your system will not update software behind your back. `pkm update` and `pkm sync` refresh the index; software changes only when you explicitly run `sudo pkm upgrade --all` or name packages to upgrade.
- **No Automatic Data Upload**: No telemetry, analytics, or crash reports are sent automatically. User-invoked web search and optional cloud escalation do send the disclosed request to the selected external service.
- **No Passphrase On Your Machine's Signing Key**: the machine owner key that signs your kernels lives at `/var/lib/intergen/mok/mok.key`, readable only by the administrator and stored without a passphrase, because kernel and driver updates sign with it when nobody is at the machine. On an encrypted install it is protected by your disk encryption; on an unencrypted install, someone who takes the disk can read it. The full trade, the alternative and what would change it are in [Secure Boot and MOK](secure-boot-and-mok.md).
- **Open Drivers, Required Vendor Firmware**: Core uses open-source kernel drivers, while `linux-firmware`, CPU microcode, and SOF carry redistributable vendor binaries needed to operate common hardware. Proprietary driver packages such as NVIDIA remain explicit opt-ins.

## 6. Further Reading

- Need to set up your system? Read the [Getting Started Guide](../getting-started.md).
- Have general questions? Check the [Frequently Asked Questions](../faq.md).
- Want to verify how we handle vulnerability disclosures? Read our [Security Policy](../../SECURITY.md).
- Interested in a reviewer-focused CVE breakdown? See our [GRUB2 CVE Audit](../grub2-cve-audit.md).
- Curious about the software available? Browse the [Databases on InterGenOS](databases.md) overview.
