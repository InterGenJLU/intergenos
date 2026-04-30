# InterGenOS AppArmor Profile Set

This package provides the AppArmor Mandatory Access Control (MAC) profiles for InterGenOS. It implements Option A from the 2026-04-29 fleet consensus vote.

## Posture: Complain by Default

In alignment with the Prime Directive ("user in control of their own machine"), InterGenOS ships all AppArmor profiles in **complain mode (learning mode)** by default.

This posture provides a graceful rollout: it logs policy violations to the journal (`/var/log/audit/audit.log` or `dmesg`) without blocking execution. This allows us to validate the profiles against real-world workloads and edge cases without breaking user systems.

As confidence builds, profiles will graduate to `enforce` mode per-profile in future releases.

## Substrate

The foundational profiles in this package are derived from the Debian 12 (Bookworm) `apparmor` and `apparmor-profiles-extra` packages (version 3.0.8). We rely on upstream distribution hygiene for standard services (sshd, cups, NetworkManager, browsers, etc.) rather than rolling our own.

## InterGenOS-Specific Profiles

We ship custom, minimal profiles for InterGenOS-specific services that lack upstream Debian profiles. These are housed in `packages/desktop/apparmor/profiles/`:

- `usr.bin.intergen-mcp`: The local AI assistant daemon (runs as user service).
- `usr.bin.pkm`: The InterGenOS package manager (runs privileged at install time).
- `usr.bin.forge`: The Forge Secure Boot installer and MOK enrollment flow.
- `usr.libexec.intergenos.first-boot-greeter`: The first-boot prompt ensuring zero default credentials.

## Disabling Profiles (User Control)

To disable a specific profile, symlink it into the `disable/` directory and reload AppArmor:

```bash
sudo ln -s /etc/apparmor.d/usr.bin.intergen-mcp /etc/apparmor.d/disable/
sudo apparmor_parser -R /etc/apparmor.d/usr.bin.intergen-mcp
```

Alternatively, standard tooling can be used if installed:
```bash
sudo aa-disable /usr/bin/intergen-mcp
```

To globally disable AppArmor (not recommended), append `apparmor=0` to your GRUB boot parameters.
