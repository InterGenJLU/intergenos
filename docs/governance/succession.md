# Maintainer Succession & Governance

**Last updated:** 2026-04-21

This document states InterGenOS's public-facing maintainer policy. Operational specifics — access configurations, credential procedures, infrastructure details — are kept in private maintainer documentation.

## Current Roles

| Role | Contact | Scope |
|---|---|---|
| **Primary maintainer** | Christopher Cork (GitHub: `InterGenJLU`) | Full project ownership: code, infrastructure, external representation, release signing |
| **Secondary maintainer (peer-constrained)** | Ethan Bambock | Co-contribution under GitHub-Member access; 2nd PGP contact for vulnerability disclosure; onboarded 2026-04 |

## Role Framing

InterGenOS is a peer-maintainer project. Differences in access scope between roles reflect the current division of work, not hierarchy or trust level. The secondary role expands over time as the project and the collaboration grow.

Branch protection on `master` applies to everyone, including the primary maintainer: PR + 1 review is required for merges regardless of role.

## What Is (and Isn't) Public

- **Public:** this governance document, vulnerability disclosure policy ([SECURITY.md](../../SECURITY.md)), technical research docs under [docs/research/](../research/), and the codebase itself.
- **Not public:** internal maintainer documentation — specific access configurations, credential procedures, and infrastructure details. This is shared privately with maintainers during onboarding, which is standard practice for operational infrastructure documentation.

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
