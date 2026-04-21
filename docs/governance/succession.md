# Maintainer Succession & Governance

**Last updated:** 2026-04-21

This document states InterGenOS's maintainer policy at the level that belongs in public view. Operational specifics (access matrices, credential rotation procedures, device inventories, agent identity maps) are held in private scaffolds shared only with maintainers during onboarding.

## Current Roles

| Role | Contact | Scope |
|---|---|---|
| **Primary maintainer** | Christopher Cork (GitHub: `InterGenJLU`) | Full project ownership: code, infrastructure, external representation, release signing |
| **Secondary maintainer (peer-constrained)** | Ethan Bambock | Co-contribution under GitHub-Member access; 2nd PGP contact for vulnerability disclosure; onboarded 2026-04 |

## Role Framing

We operate a **peer-maintainer-with-constrained-authority** model, not a hierarchy. Access differences between roles reflect blast-radius and infrastructure-cost reasons, not trust. The secondary role expands toward co-primary as the project matures and the succession plan evolves.

Branch protection on `master` applies to everyone, including the primary maintainer: PR + 1 review is required for merges regardless of role.

## What Is (and Isn't) Public

- **Public:** this governance document, vulnerability disclosure policy ([SECURITY.md](../../SECURITY.md)), technical research docs under [docs/research/](../research/), and the codebase itself.
- **Not public:** operational onboarding scaffolds — per-surface access matrices, credential rotation procedures, device inventories, agent identity maps. These are shared privately with maintainers during onboarding.

**Reason:** InterGenOS assumes superhuman-scale vulnerability discovery by adversaries (see the [Security-Only Alignment premise](../../README.md#security-only-alignment) in the project README). Published operational maps of who-has-what-access-where are free reconnaissance. Keeping them private is a direct application of the same Holy Grail reasoning that drives Secure Boot mandatory-by-default, module-signature-enforcement, and the signed-chain installer design.

## Vulnerability Disclosure

Two PGP-enabled security contacts, cross-signed, as of the first shim-signed release:
- Primary: Christopher Cork
- Secondary: Ethan Bambock (PGP fingerprint will be published with the cross-signing milestone)

Reports go to `security@intergenstudios.com`. See [SECURITY.md](../../SECURITY.md) and [/.well-known/security.txt](https://intergenstudios.com/.well-known/security.txt) for the full disclosure policy.

## Credential Rotation

Shared infrastructure credentials are rotated on any of:
- personnel change (addition, removal, or scope change of any maintainer)
- a credential-compromise event (confirmed or strongly suspected)
- an annual cadence, whichever is sooner

Scope and procedures are maintained in private operational docs.

## Amendments

This document is updated in a public commit whenever the role table or policy framing changes. Operational details that affect only private scaffolds are not mirrored here.
