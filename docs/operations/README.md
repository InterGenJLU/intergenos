# InterGenOS — Operations Runbook

Authoritative operational documentation for the InterGenOS build / sign / test / ship lifecycle. Authored in-tree first; the canonical copy lives on the project VPS (upload is a separate manual step performed by the maintainer).

## Audience

Maintainers and contributors operating the build pipeline. These docs assume working familiarity with libvirt, KVM, bash, Python, and the basics of Linux From Scratch, but no prior InterGenOS-specific knowledge. Each doc starts from "you have a workstation" and walks forward.

## How to read

The docs are numbered in topical order, not strict procedural order. A first-time operator going zero-to-bootable reads 01 → 07 (set up and snapshot the build VM) → 02 (the master orchestrator) → 03 (signing) → 04 and 05 (squashfs and ISO assembly) → 06 (test VM evaluation), then turns to 08 when adding a package, 09 when driving a release candidate through the iterate-until-clean-install loop, and 10 when resuming a build from a populated VM without a full from-scratch run.

## Topics

| # | Title | Purpose |
|---|---|---|
| [01](01-build-vm-setup.md) | Setting up and validating a build VM | Provision the libvirt-managed Ubuntu 24.04 build VM with cloud-init + virtiofs + apt-timer isolation |
| [02](02-running-the-builder.md) | Running the builder | `scripts/build-intergenos.sh` flags, the 21-phase canonical order (plus the optional `publish` phase), targeted-rebuild invocations, graceful-halt mechanics |
| [03](03-automating-signing.md) | Automating release signing | `scripts/sign-release.sh` workflow against the hardware token (GPG + PIV slot 9c) on the offline signing workstation |
| [04](04-generating-squashfs.md) | Generating the live-ISO squashfs | `scripts/build-squashfs.sh` step-by-step flow, the mirror-only prune and archive exclusion, the four fail-closed pre-seal gates (binary presence, verify_paths, install-set parseability, file ownership), and the dm-verity hashtree emit |
| [05](05-creating-iso.md) | Creating the bootable ISO | `scripts/build-iso.sh` six-phase assembly of the hybrid UEFI+BIOS ISO from signed inputs; the trust-gap closure for the squashfs |
| [06](06-test-vm-and-evaluation.md) | Test VM with the ISO + evaluation | virt-install of an OVMF-Secure-Boot test VM; smoke harness invocation; journalctl grep patterns |
| [07](07-golden-builder-snapshot.md) | Snapshotting a reference build VM | `virsh snapshot-create-as` flow with pre-snapshot validation; when to capture a new reference snapshot |
| [08](08-adding-packages.md) | Adding a package to the build | `packages/<tier>/<name>/` layout, Rule 20 verify_paths authoring, builder reachability via static-list vs Python tier-driver |
| [09](09-gbc-iteration-methodology.md) | Release-candidate iteration methodology | Candidate-to-release promotion, the `[AUTO]`/`[CHROOT]` layer key, grouping-as-layer, and the principle that the costliest bugs surface at install and first boot. This is the lesson the other topics point at. |
| [10](10-iteration-resume-builds.md) | Resuming a build from a populated VM | `--start-at <phase>` mechanics: what actually rebuilds on a resume, what the chroot sees, and which gates fire when carrying a changed tier forward without a full rebuild |
| [11](11-resolving-validation-gates.md) | Resolving the validate-phase gates | What each pre-compilation gate checks (audit-coverage, tier validation, reconciliation mismatches) and the correct way to clear a failure — real fix vs tier reclassification vs acknowledged divergence |

## Conventions

Every doc follows the same six-section structure:

1. **Goal** — what the procedure accomplishes
2. **Prerequisites** — required state / credentials / access
3. **Step-by-step procedure** — actual commands, file paths, runtime expectations
4. **Validation** — how to confirm the step worked
5. **Common failures + troubleshooting** — what goes wrong and how to recover
6. **Cross-references** — links to related docs in this set and to canonical script source files

Code blocks show literal commands or file content. Inline code references files and paths. Tables enumerate per-symptom failure modes and their fixes. **Every command in these runbooks is verified against the current master tip** per Rule 21: aspirational content is forbidden. If a procedure has a gap (for example a missing helper script), the gap is called out explicitly in the doc itself.

## Related canonical references

- [`docs/governance/succession.md`](../governance/succession.md) — public-facing maintainer policy
- [`docs/research/installer/`](../research/installer/) — the design-decision history behind the live-session / Forge installer architecture. Read for context; **note** the FINAL doc is dated 2026-04-10 and has known drift vs current implementation in several areas.

## Status

This runbook set was authored 2026-05-15 as a single batch and is kept current against the build pipeline. Continuous Rule 21 gating is in place via `scripts/check-aspirational-stubs.py`.

Planned operational-infrastructure additions:

- A public-facing operational-notes mirror at `docs/operational-notes/`
- `scripts/build-vm-seed.sh` for cloud-init seed automation

None of these are blockers; they are the natural next layer of operational-doc maturity.
