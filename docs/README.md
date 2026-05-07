# InterGenOS Documentation

InterGenOS is an AI-integrated Linux distribution built entirely from source. This directory contains the project's working documentation, governance records, and research notes.

If you're new to the project, start here. Otherwise, jump to the section relevant to your role.

---

## For everyone

- [VISION.md](VISION.md) — What InterGenOS is and why it exists.
- [getting-started.md](getting-started.md) — How to clone, build, and run.

## For contributors

- [contributor-guide.md](contributor-guide.md) — Onboarding, conventions, repo workflow. *(In progress; folded into the documentation lane.)*
- [components/](components/) — Per-component design docs:
  - `pkm.md` — Package manager
  - `forge.md` — Installer
  - `intergen.md` — AI assistant runtime
- [architecture.md](architecture.md) — Repository structure and build pipeline overview.

## For reviewers + auditors

- [shim-review-submission.md](shim-review-submission.md) — Submission to the rhboot/shim-review process.
- [grub2-cve-audit.md](grub2-cve-audit.md) — CVE audit against the GRUB2 fork.
- [signing-key.md](signing-key.md) — Canonical signing-key fingerprints.
- [signing-procedure.md](signing-procedure.md) — Operational runbook for release signing.
- [research/audit/](research/audit/) — Codebase audit deliverables and consensus reviews.
- [research/security/](research/security/) — Security research and advisories.

## For security researchers

- [security/](security/) — Security advisories.
- [signing-key.md](signing-key.md) — Verify release signatures.
- [grub2-cve-audit.md](grub2-cve-audit.md) — Reviewer-facing CVE audit.
- [ephemeral-module-signing.md](ephemeral-module-signing.md) — Novel kernel-module-signing writeup.

## For governance

- [governance/](governance/) — Succession, role policies.
- [governance/succession.md](governance/succession.md) — Public role policy.

## For research / archaeology

- [research/](research/) — Topical research subdirectories (AI integration, build systems, package management, theming, hardware tests, virtualization, etc.). Curated subset of the project's home-drive research collection.

---

## Conventions

- Markdown files use H1 for the document title and H2-H4 for sections.
- Date-stamped research filenames use `YYYY-MM-DD` suffix (e.g., `meson_curations_2026-04-01.md`).
- Internal vocabulary (project rules, agent abbreviations) does not appear in published documentation here. Use plain technical terms instead.

For the freshest "what is the project doing this week" picture, the project's runtime status lives outside this directory in the project's session-state files. Documentation here is intended to be reasonably durable.
