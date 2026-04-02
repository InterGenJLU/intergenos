# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in InterGenOS's build system, scripts,
or package templates, please report it responsibly.

**Do NOT open a public GitHub issue for security vulnerabilities.**

Instead, use [GitHub's private vulnerability reporting](https://github.com/InterGenJLU/intergenos/security/advisories/new)
to submit a confidential report.

You can expect:
- Acknowledgment within 48 hours
- An assessment of severity and impact
- A fix or mitigation plan

## Scope

This policy covers the InterGenOS build system, orchestrator, package templates,
and build scripts. It does **not** cover vulnerabilities in upstream packages
(GCC, glibc, OpenSSL, etc.) — those should be reported to their respective
upstream projects.

## Package Security

InterGenOS pins to BLFS 13.0 tested versions for initial builds, then upgrades
to latest stable releases. If you notice a package template referencing a version
with a known CVE, please open a regular GitHub issue — that's not a vulnerability
in our code, it's a version bump we need to make.
