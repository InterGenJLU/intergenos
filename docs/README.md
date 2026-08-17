# InterGenOS Documentation

InterGenOS is an AI-integrated Linux distribution built entirely from source. This directory contains the project's working documentation, governance records, and research notes.

If you're new to the project, start here. Otherwise, jump to the section relevant to your role.

---

## For everyone

- [VISION.md](VISION.md) — What InterGenOS is and why it exists.
- [getting-started.md](getting-started.md) — How to clone, build, and run.

## For contributors

- [contributor-guide.md](contributor-guide.md) — Onboarding, conventions, repo workflow, and the pre-push gates.
- [operations/](operations/) — The numbered lifecycle guides, 01 through 11: build-VM setup, running the builder, signing automation, squashfs, ISO creation, test-VM evaluation, the golden-builder snapshot, adding packages, iteration methodology, resume builds, and resolving validation gates.
- [package-tiers.md](package-tiers.md) — Canonical tier definitions and the strict build order.
- [DEVELOPMENT-FRAMEWORK.md](DEVELOPMENT-FRAMEWORK.md) — How the project is developed and why the process is shaped that way.
- [components/](components/) — Per-component design docs:
  - `pkm.md` — Package manager
  - `forge.md` — Installer
  - `intergen.md` — AI assistant runtime
- [architecture.md](architecture.md) — Repository structure and build pipeline overview.

## For users

- [users/](users/) — End-user guides: the desktop experience, package management, full-disk encryption, Secure Boot and MOK, security defaults, databases, and the assistant.
- [faq.md](faq.md) — Common questions.
- [mok-enrollment.md](mok-enrollment.md) — Enrolling your Machine Owner Key.

## For reviewers + auditors

- [shim-review-submission.md](shim-review-submission.md) — Submission to the rhboot/shim-review process.
- [grub2-cve-audit.md](grub2-cve-audit.md) — CVE audit against the GRUB2 fork.
- [signing-key.md](signing-key.md) — Canonical signing-key fingerprints.
- [signing-procedure.md](signing-procedure.md) — Operational runbook for release signing.
- [research/security/](research/security/) — Security research and advisories.
- [legal/](legal/) — Payload licences and patent posture.
- [sboms/](sboms/) — Software bills of materials.
- [ceremony/](ceremony/) — The signing-key ceremony procedure.

## For security researchers

- [security/](security/) — Security advisories.
- [signing-key.md](signing-key.md) — Verify release signatures.
- [grub2-cve-audit.md](grub2-cve-audit.md) — Reviewer-facing CVE audit.
- [ephemeral-module-signing.md](ephemeral-module-signing.md) — Novel kernel-module-signing writeup.

## For governance

- [governance/](governance/) — Role policy, licence policy, and redistribution posture.
- [governance/succession.md](governance/succession.md) — Public role policy.
- [governance/license-policy.md](governance/license-policy.md) — How licences are classified and what that means for what ships.
- [mirror/](mirror/) — Binary-mirror design.

## For research / archaeology

- [research/](research/) — Topical research subdirectories (AI integration, build systems, package management, theming, hardware tests, virtualization, and more). These are **dated records of the analysis as it stood**, not current-state documentation: where one has been overtaken it carries a banner saying so, and it is not rewritten to agree with the present. Read them for how a decision was reached.

---

## Conventions

- Markdown files use H1 for the document title and H2-H4 for sections.
- Date-stamped research filenames use `YYYY-MM-DD` suffix (e.g., `meson_curations_2026-04-01.md`).
- Internal vocabulary (project rules, agent abbreviations) does not appear in published documentation here. Use plain technical terms instead.

Documentation here is intended to be reasonably durable. Week-to-week project status is not tracked in this directory; the [CHANGELOG](../CHANGELOG.md) and the release notes are the public record of what changed.
