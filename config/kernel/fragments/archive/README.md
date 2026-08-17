# Kernel Config Fragment Archive

Previous-iteration kernel config fragments preserved here for audit trail.

## Active fragments

The active fragments live in the parent directory (`config/kernel/fragments/`):

- `00-universal-baseline.config` — cross-distro convergence baseline (intersection of Ubuntu / Arch / Fedora / Debian / openSUSE defaults).
- `99-intergenos-overrides.config` — InterGenOS-specific hardening overrides; applied last so they take precedence.

The two-fragment system is concatenated by `packages/core/linux-kernel/build.sh` to produce the final `.config` for the kernel build.

## Archive policy

A fragment graduates from active to archive when:

1. It is superseded by a newer iteration that captures the same constraints.
2. The kernel-config research that produced it has been re-run and a new baseline has replaced it.
3. The owner explicitly retires the fragment for clarity (e.g., a fragment that was active during early development but is no longer referenced).

Files here are read-only artifacts: useful for understanding *why* the active fragments look the way they do, and for reproducing an older build state. They are not loaded by the kernel build pipeline.

## Naming

Archive fragments retain their original numeric prefix and date-stamped suffix when present. The original filenames are preserved so cross-references in research docs continue to work.

If you want to retire an active fragment, move it here rather than deleting it. Cross-reference its retirement reason in the relevant research doc under `docs/research/kernel/`.
