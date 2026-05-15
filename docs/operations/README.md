# InterGenOS — Operations Runbook

Authoritative operational documentation for the InterGenOS build / sign / test / ship lifecycle. Authored in-tree first; the canonical fleet copy lives on the project VPS (upload is a separate manual step performed by the maintainer).

## Audience

Maintainers and contributors operating the build pipeline. These docs assume working familiarity with libvirt, KVM, bash, Python, and the basics of Linux From Scratch — but no prior InterGenOS-specific knowledge. Each doc starts from "you have a workstation" and walks forward.

## How to read

The docs are numbered in topical order, not strict procedural order. A first-time operator going zero-to-bootable would read 01 → 07 (set up + snapshot the build VM) → 02 (the master orchestrator) → 03 (signing) → 04 → 05 (squashfs + ISO assembly) → 06 (test VM evaluation) → 08 (when adding a package). 09 and 10 are reference material — read once for calibration, return when an architectural question surfaces.

## Topics

| # | Title | Purpose |
|---|---|---|
| [01](01-build-vm-setup.md) | Setting up and validating a build VM | Provision the libvirt-managed Ubuntu 24.04 build VM with cloud-init + virtiofs + apt-timer isolation |
| [02](02-running-the-builder.md) | Running the builder | `scripts/build-intergenos.sh` flags, the 17-phase canonical order, surgical-rebuild invocations, graceful-halt mechanics |
| [03](03-automating-signing.md) | Automating release signing | `scripts/sign-release.sh` workflow against the hardware token (GPG + PIV slot 9c) on the offline signing workstation |
| [04](04-generating-squashfs.md) | Generating the live-ISO squashfs | `scripts/build-squashfs.sh` five-step flow + the step-4.5 pre-squashfs audit gate (Rule 20 enforcement) |
| [05](05-creating-iso.md) | Creating the bootable ISO | `scripts/build-iso.sh` six-phase assembly of the hybrid UEFI+BIOS ISO from signed inputs; the trust-gap closure for the squashfs |
| [06](06-test-vm-and-evaluation.md) | Test VM with the ISO + evaluation | virt-install of an OVMF-Secure-Boot test VM; smoke harness invocation; journalctl grep patterns |
| [07](07-golden-builder-snapshot.md) | Snapshotting a "Golden Builder" | `virsh snapshot-create-as` flow with pre-snapshot validation; when to roll a new golden |
| [08](08-adding-packages.md) | Adding a package to the build | `packages/<tier>/<name>/` layout, Rule 20 verify_paths authoring, builder reachability via static-list vs Python tier-driver |
| [09](09-cost-of-deferral.md) | Cost of deferring missing/broken/unfinished work | Case studies on the operational cost of "track and move on" vs "fix now" — calibration document, not a runbook |
| [10](10-recommendations.md) | Recommendations + what's missing | Synthesis after authoring 1-9: gaps identified, runbooks that should exist, infra that would help |

## Conventions

Every doc follows the same six-section structure:

1. **Goal** — what the procedure accomplishes
2. **Prerequisites** — required state / credentials / access
3. **Step-by-step procedure** — actual commands, file paths, runtime expectations
4. **Validation** — how to confirm the step worked
5. **Common failures + troubleshooting** — what goes wrong and how to recover
6. **Cross-references** — links to related docs in this set and to canonical script source files

Code blocks show literal commands or file content. Inline code references files and paths. Tables enumerate per-symptom failure modes and their fixes. **Every command in these runbooks has been verified against the current master tip** per Rule 21 — aspirational content is forbidden. If a procedure has a gap (e.g., a missing helper script), the gap is explicitly called out in the doc and tracked in topic 10.

## Related canonical references

- [`docs/build-development-rulebook.md`](../build-development-rulebook.md) — Rules 1-21 governing the build flow. Section 2 is the halt-handler decision tree.
- [`docs/governance/succession.md`](../governance/succession.md) — public-facing maintainer policy
- [`docs/research/installer/`](../research/installer/) — the design-decision history behind the live-session / Forge installer architecture. Read for context; **note** the FINAL doc is dated 2026-04-10 and has known drift vs current implementation in several areas (see internal stub-hunt audit notes for the matrix).

## Status

This runbook set was authored 2026-05-15 as a single batch per the fleet-documentation dispatch. Per topic-10 recommendations, the next-meaningful-batch of operational-infrastructure work includes:

- A public-facing operational-notes mirror at `docs/operational-notes/` (R2)
- `scripts/check-aspirational-stubs.py` for continuous Rule 21 gating (R3)
- `scripts/build-vm-seed.sh` for cloud-init seed automation (R1)

None of these are blockers; they're the natural next layer of operational-doc maturity once 1-10 has been read against by the team.
