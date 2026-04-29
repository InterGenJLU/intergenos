---
title: Fedora shim-maintainers advisory email — draft
status: DRAFT
audience: shim-maintainers@fedoraproject.org
target_send_date: 2026-05-02
prerequisites:
  - 2026-04-30 ceremony complete (vendor cert + security@ PGP key generated)
  - PGP key published to keys.openpgp.org
  - docs/signing-key.md fingerprint page live
authors: chris-ubuntu-code-claude (SPOC, draft)
review: chris-windows-code-claude + chris-intergenos-code-claude before send
---

# Fedora shim-maintainers advisory email — draft

This is the draft of the **first contact** outreach email to
`shim-maintainers@fedoraproject.org` per Step 1 of the
[shim-review timeline](../installer/ms_shim_sponsorship_2026-04-18.md#9-concrete-plan-for-intergenos)
(target send Saturday 2026-05-02, post-ceremony, well ahead of the
2026-05-15 PR-open target).

The goal is **guidance, not sponsorship.** Fedora's shim-maintainers
formally advise; they do not legally sponsor third-party shim
submissions. Three concrete questions reflect the InterGenOS posture
items most likely to surface review concerns.

---

## Email body (sendable form)

**From:** security@intergenstudios.com
**To:** shim-maintainers@fedoraproject.org
**Subject:** InterGenOS shim-review submission prep — guidance request

> Hello shim-maintainers,
>
> We are preparing a shim-review submission for InterGenOS, a new
> source-built Linux distribution targeting a 2026-05-15 PR-open against
> `github.com/rhboot/shim-review`. Before we open the PR, we would
> appreciate guidance on three posture items where the established
> review pattern is not obviously settled for new-vendor distros built
> entirely from source.
>
> InterGenOS is built from source via Linux From Scratch 13.0 with a
> custom kernel configuration and a deterministic Docker-based build
> for the shim itself. The full source tree is published at
> `github.com/InterGenJLU/intergenos` under GPL-3.0-or-later. Our shim
> binary is built from `rhboot/shim` v16.1 with an InterGenOS-specific
> vendor cert embedded; the build is reproducible byte-for-byte from a
> pinned `debian:bookworm-slim` base image with a fixed
> `SOURCE_DATE_EPOCH` and `--recurse-submodules --shallow-submodules`
> clone. Vendor cert custody is offline — Nitrokey 3 NFC tokens, a
> Tails-based air-gapped key-generation ceremony, and a four-card
> distribution (daily / home-safe / safety-deposit-box / spare).
>
> Our specific questions:
>
> 1. **SBAT generation numbering for a new-vendor distro.** Our shim
>    inherits SBAT generation 5 from upstream rhboot/shim. We do not
>    ship a vendor-specific SBAT entry below the upstream level; rather
>    we add an `intergenos` component entry at generation 1 alongside
>    the upstream entries. Is there a precedent or guidance for what
>    starting generation number a new-vendor distro should claim, or do
>    reviewers expect generation 1 universally for first-submission
>    shims?
>
> 2. **Ephemeral per-build kernel-module-signing key posture.** Our
>    kernel build generates a fresh module-signing key per build,
>    signs all in-tree modules, and reaps the private key when the
>    build completes. The matching public key is embedded in the kernel
>    via `CONFIG_SYSTEM_TRUSTED_KEYS` only for that build. The vendor
>    cert that signs shim is distinct and never participates in module
>    signing. We document this clearly in our `README.md` to forestall
>    the obvious "where does the module-signing key live" question, but
>    we have not seen a comparable posture in other reviewed shims and
>    would value a sanity-check before opening the PR.
>
> 3. **Tails-based offline-root-cert custody.** The vendor cert and the
>    PGP signing keys for our security contacts are generated on a Tails
>    live-USB host with hardware-token (Nitrokey 3 NFC) custody, never
>    on a persistent disk. Subkeys derive from a primary key that
>    remains offline at all times after the ceremony. The ceremony
>    procedure is documented at
>    `docs/signing-procedure.md` in our repo. Is this consistent with
>    review expectations, or are there known concerns we should pre-empt
>    in the README?
>
> No reply is expected on a strict timeline. We are happy to wait for a
> response before opening the PR; if you would prefer to provide
> guidance via the shim-review issue tracker after we open, that works
> equally well for us.
>
> Our public signing key fingerprint will be published at
> `https://intergenstudios.com/signing-key` and at
> `keys.openpgp.org` once the ceremony completes (target: 2026-05-01).
> This message will be PGP-signed from that key.
>
> Thank you for the work you do reviewing shims for the broader
> ecosystem. We have studied the existing review record and know
> reviewers are volunteering scarce time; we will arrive with a complete
> 39-question template and a buildable Dockerfile to keep the review
> efficient.
>
> Best regards,
> The InterGenOS maintainers
> `security@intergenstudios.com`
> PGP fingerprint: __TBD post-ceremony 2026-04-30__

---

## Notes for reviewers (laptop / windows-claude)

**Tone choices:**
- "guidance, not sponsorship" framing is explicit per the sponsorship doc — Fedora maintainers do not sponsor, they advise. Asking for sponsorship would be off-target.
- "PR-open target 2026-05-15" sets expectations transparently. They can decide whether to engage pre-PR or wait for the PR itself.
- Three questions only. More than three reads as a fishing expedition; fewer than three understates the actual unknowns.

**Question selection rationale:**
- Q1 (SBAT generation) is a concrete reviewer-facing decision we have to make. Asking establishes that we have read the SBAT spec and have a specific question, not a "how does shim work" question.
- Q2 (ephemeral module-signing) is the highest-novelty item per the sponsorship doc's "biggest review blocker" call. Surfacing it pre-PR signals we know it is novel and want to validate the approach.
- Q3 (Tails + Nitrokey custody) is the operational-hygiene item most likely to draw "is this enough?" questions. Inviting feedback on it pre-PR is cheaper than fielding it during review.

**What is NOT in the email (deliberate):**
- The ceremony date (2026-04-30) — irrelevant to maintainers; would over-share operational detail.
- Any reference to fleet-internal coordination tooling, agent IDs, MCP, or the multi-agent build process.
- Names of individual maintainers or any personal identifiers — the role address signs off as "The InterGenOS maintainers."
- Apology language or excessive deference. Asking for guidance does not require "I know you are busy" language; the closing paragraph already acknowledges scarce reviewer time.

**Pre-send checklist:**

- [ ] Owner reads + edits as needed.
- [ ] PGP fingerprint placeholder replaced with actual fingerprint post-ceremony 2026-04-30.
- [ ] `https://intergenstudios.com/signing-key` page published before send.
- [ ] Email is PGP-signed from the new security@ key.
- [ ] Send from `security@intergenstudios.com` (configured as alias-to-personal-mailbox or dedicated mailbox per owner preference).
- [ ] No file attachments. Maintainers can read at the repo URL if they want artifacts.
- [ ] Saturday morning EU-friendly send window (sometime between 09:00-13:00 UTC) maximizes attention.

**Post-send tracking:**

- Save a copy of the sent message in maintainer's offline log alongside ceremony records.
- If no response by 2026-05-09 (one week), the PR-open target on 2026-05-15 stands; the maintainers can engage in the PR thread instead.
- A response, regardless of content, becomes input to README.md content (Q26 key management, security-contact section) before PR-open.

---

## Status

- **2026-04-29**: Draft created by SPOC. Pending peer-review by laptop and windows-claude before the Saturday send window. Owner-final-edit always.
- **2026-04-30**: Ceremony completes. PGP fingerprint becomes available.
- **2026-05-01**: Fingerprint published; signing-key.md page goes live.
- **2026-05-02 (target)**: Send.
