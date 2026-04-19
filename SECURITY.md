# Security Policy

InterGenOS takes security seriously. This document describes how to report vulnerabilities and how we respond.

> **Project status:** InterGenOS is in active development pre-1.0 release. This policy applies to all InterGenOS code and infrastructure from the date below onward. We are a small team; if we cannot meet a target, we will communicate openly and directly with the reporter.

## Reporting a vulnerability

**Primary:** Email `security@intergenstudios.com`

**Alternative:** Open a [private security advisory](https://github.com/InterGenJLU/intergenos/security/advisories/new) on GitHub.

Anonymous reports accepted. We will never retaliate against good-faith reporters.

For encrypted submissions, our PGP key fingerprint is published on our [signing key page](https://intergenstudios.com/signing-key) (available post-launch).

---

## Software vulnerability response

### Acknowledgment
- **Within 48 hours** of receipt, we will confirm we have your report and begin triage.

### Fix timelines (from triage)
| Severity | Target |
|---|---|
| **Critical** — remote code execution, authentication bypass, Secure Boot chain break | 14 days |
| **High** — local privilege escalation, cryptographic weakness | 30 days |
| **Medium** — denial of service, information disclosure | 60 days |
| **Low** — defense-in-depth gaps | 90 days or next release |

### Public disclosure
At fix release **or** 90 days from acknowledgment, **whichever comes first**. This is our Project-Zero-style safety net — we will not sit on unresolved vulnerabilities indefinitely.

### Upstream coordination
If the vulnerability is in upstream code (kernel, systemd, glibc, etc.), we coordinate with their embargo. Our users receive the fix on the same timeline as upstream users — never later.

### Advisory content
Published advisories include:
- CVE number (assigned via MITRE)
- CVSS score
- Affected versions
- Mitigation steps
- Patch commit reference
- Timeline (reported / triaged / fixed / disclosed)
- Reporter credit (see below)

---

## Trust-anchor compromise response

A **signing-key compromise is not a bug — it is a break in the foundation of trust.** The standard framework above does not apply; this track is dedicated and aggressive.

### What counts as "confirmed" compromise

A confirmed compromise requires **evidence, not claim**:
- **(a) Direct evidence** — artifact of private-key material exposure, device-tampering indicator, or credential leak, **OR**
- **(b) Anomalous signature** — a valid signature observed in the wild that we did not authorize.

Mere claims route to the standard 48-hour triage and are likely closed without action.

### Response on confirmed compromise

| Timing | Action |
|---|---|
| **Immediate** | Acknowledgment |
| **Within 12 hours** | Revocation published + new keyring package available |
| **Simultaneous with revocation** | Public disclosure — no embargo, no delay |

**Target:** users running `pkm update` within 24 hours of the incident receive the new keyring and revoke trust in the compromised key.

### Advisory content
- Fingerprint of compromised key
- First known compromised signature timestamp (if determinable)
- Downstream artifacts suspected tampered
- Replacement key fingerprint
- Verification instructions
- Incident timeline

### Why we do not embargo trust-anchor compromises

- Convenience is never a reason to delay trust-anchor revocation.
- Our update infrastructure must be trustworthy. A compromised signing key means the update path itself is the attack.
- If there is any doubt about key integrity, we revoke. Re-issuing unnecessarily is preferable to leaving users exposed once.

---

## Reporter credit and recognition

**Accepted reports:** we credit reporters in the advisory and on our Hall of Fame page, unless anonymity is requested.

**Rejected reports:** default to anonymity. If a reporter wishes to be publicly credited for a rejected or duplicate report, they may request it.

**Hall of Fame:** [https://intergenstudios.com/security-hall-of-fame](https://intergenstudios.com/security-hall-of-fame) *(page available post-launch)* lists researchers who have responsibly disclosed vulnerabilities to us.

---

## What we do NOT offer

**No bug bounty program at this time.** We appreciate responsible disclosure and will credit researchers publicly, but we do not pay monetary rewards. This policy may change as the project grows.

---

## Scope

This policy applies to:
- InterGenOS core packages and distribution artifacts
- The `pkm` package manager and repository infrastructure
- Signing keys and related trust infrastructure
- `intergenstudios.com` web properties

Out of scope:
- Upstream projects we package (report to them directly; we will coordinate)
- Third-party software installed by users post-install

---

*Last updated: 2026-04-19*
