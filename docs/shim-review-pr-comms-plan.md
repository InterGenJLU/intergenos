# Shim-review Submission Comms Plan — InterGenOS shim-x64

**Drafted:** 2026-05-11
**Target submission:** `https://github.com/InterGenJLU/shim-review/tree/intergenos-shim-x64-20260515`
**Companion doc:** `docs/shim-review-submission.md` — the 39-question populated submission body. This comms plan governs the conversation that body enters at rhboot/shim-review.
**Basis:** Precedent walk of 5 recent merged rhboot/shim-review submissions (Issues #433, #479, #481, #488, #490). Source data preserved in maintainer's local working set.

---

## Nomenclature note

`rhboot/shim-review` uses **GitHub Issues** for submission/review, not pull requests. Submissions open as an Issue with the populated 39-question template as the Issue body. The "PR" language in many places across earlier InterGenOS internal drafts is a misnomer; this comms plan uses "Issue" / "submission Issue" / "submission" throughout. The submission doc's existing "PR-open" wording is a separate sweep candidate.

---

## Timeline reality-check

### Precedent data

| Submission | Days open→merge | Reviewer rounds | First-engagement | Vendor patches |
|---|---|---|---|---|
| #433 ZeronsoftN | 589 (outlier — upstream patch dep) | 1 substantive + pings | ~12 min | yes |
| #479 10ZiG | 257 | 6 | 11h | none |
| #490 Miray | 91 | 4 | 13 days | none |
| #488 opsi | 96 | 3 | 25 days | minimal |
| #481 Blancco | 109 (re-submission) | 1 | 5.5 days | none |
| **Mean (ex outlier)** | **138** | **3.5** | **bimodal** | — |
| **Fastest** | **91** | — | — | — |

### Implication for InterGenOS

- **Target Issue-open:** 2026-05-22
- **MS 2011 UEFI CA expiration:** 2026-06-27 (36 days post-open)
- **Earliest plausible merge** (best precedent, Miray 91 days): **2026-08-21**
- **Mean-precedent merge** (138 days): **2026-10-07**
- **Long-tail merge** (10ZiG 257 days): **2027-02-03**

**Open question for owner — Q5 dual-CA claim.** The submission doc Q5 currently asserts:

> "Our own shim-review submission, opened before the 2026-06-27 cert-transition deadline, will receive dual-signed (2011 + 2023 CA) binaries from Microsoft for maximum hardware compatibility — a strict improvement over the Fedora-piggyback posture."

Given precedent, the realistic merge date is **mid-October at the mean, late-August at the fastest** — both well past the 2026-06-27 expiration. Microsoft stops dual-signing once the 2011 CA expires; post-expiration signings are 2023-CA-only. The Q5 dual-signed claim is therefore factually optimistic. Three paths for owner consideration:

1. Rephrase Q5 to acknowledge realistic dual-vs-2023-only split (most honest, slight rhetoric loss).
2. Keep claim with a fallback narrative ("we have a 2023-CA-only fallback if dual-signing window closes").
3. Compress the pre-open work to push Issue-open earlier (high cost, may sacrifice readiness — submitting incomplete hurts more than missing dual-sign).

No edit applied; awaiting owner ratification on path.

### What InterGenOS can do to land at the fastest-precedent end

Three structural advantages tilt InterGenOS toward the 91-day Miray pace rather than the 257-day 10ZiG pace:

1. **No vendor patches** (Q10 attests zero code patches on shim) — eliminates the upstream-patch-review cycle that drove ZeronsoftN to 589 days.
2. **Dockerfile-driven cross-host reproducibility evidence already on master** (Q22 native-Linux SHA table, Q23 logs in fork) — reviewers will re-run and confirm rather than ask for evidence.
3. **Front-loaded answers to the 6 recurring reviewer asks** (see next section) — eliminates the round-trip on the questions most-frequently raised.

---

## Top 6 recurring reviewer asks (5/5 across precedent)

These surfaced in every examined submission. The InterGenOS submission must pre-empt them in its Issue body.

| # | Ask | InterGenOS preempt status |
|---|---|---|
| 1 | **NX bit / NX_COMPAT** — disabled? why? evidence? | Q11 documents `readelf -lW shimx64.efi` shows `GNU_STACK RW` (not `RWE`); NX-bit hardware-enforced under `CONFIG_X86_64=y` |
| 2 | **Build reproducibility via Dockerfile + SHA256** — reviewer will re-run | Q22 cross-host SHA table + `scripts/verify-b2-reproducibility.sh` 9-check harness; fork has `logs/build_2026-05-06T08-42Z.log` + `logs/verify-b2-reproducibility-cross-host-2026-05-06.log` |
| 3 | **Ephemeral kernel-module signing keys** — `CONFIG_MODULE_SIG_KEY` unset and per-build regenerated? | Q19 is the longest answer in the doc; preempted in detail. Reviewers will grill this — we're ready. |
| 4 | **GRUB module list justification** — every non-standard module needs rationale | Q30 module table + `scripts/build-grub-standalone.sh` MODULES array; each category has rationale column |
| 5 | **SBAT entries + version increments** — version drift across binary/README is caught | Q14 SBAT table + `scripts/check-sbat-generations.sh` precheck + `tests/sbat/test_check_sbat_generations.sh` PASS — README/binary drift risk mitigated structurally |
| 6 | **CA-cert constraints + key storage** — CA:TRUE + critical, hardware-token storage, validity | Q26 documents the Tails 7.7 ceremony, Nitrokey-3-NFC distribution (S1-S4 across 2 maintainers + 4 locations), 2-year-expiry rotation strategy, PIV-slot-9c on-card generation |

All six are answered in detail in the submission doc on master.

**Second open question for owner — Q23 filename mismatch.** Q23 of the submission doc references `logs/verify-b2-reproducibility.log`; the actual file in the `InterGenJLU/shim-review intergenos-shim-x64-20260515` branch is `logs/verify-b2-reproducibility-cross-host-2026-05-06.log` (date + cross-host suffix appended). Three resolution paths:

1. Update the submission doc to reference the actual fork filename.
2. Rename the fork-side file to match the doc's reference.
3. Accept the divergence with an inline footnote (minor; reviewers will likely tolerate).

Lean: path 1 (smaller and more accurate). Owner pick.

---

## Section 1 — Submission Issue body

The Issue body opened against rhboot/shim-review is the rendered `README.md` from the InterGenJLU/shim-review submission branch. The fork-side `README.md` must mirror `docs/shim-review-submission.md` on master with **zero drift** at submission moment. The fork-side README is currently ~56 KB / 638 lines — a final `docs/shim-review-submission.md` → fork `README.md` sync is required at submission time, then **no further main-repo edits** to the source doc until the Issue closes (to prevent README/submission drift, which #479 hit hard).

**Issue title format** (matches precedent shape from #488 / #490):

```
Review request for InterGenOS shim 16.1 (x86_64)
```

NOT "InterGenOS shim-review submission" or "Please review InterGenOS shim." Reviewers grep titles by `Review request for <vendor> shim <version>`.

**Body opening (above the 39 questions):**

```markdown
Hello shim-review maintainers,

InterGenOS is a Linux-from-Source-derived distribution preparing its
first MS-signed shim submission, transitioning from a Fedora-piggyback
bootstrap to its own signed boot chain.

Build: shim upstream rhboot/shim @ tag 16.1 commit afc4955.
Vendor cert: CN=InterGenOS Secure Boot CA (RSA-4096, on-card
generation on Nitrokey 3 NFC PIV slot 9c during 2026-05-05 ceremony).
Reproducibility: Dockerfile + cross-host native-Linux verification
(2 independent witness hosts produced byte-identical SHAs).

Source repo (main project): https://github.com/InterGenJLU/intergenos
Submission branch:
  https://github.com/InterGenJLU/shim-review/tree/intergenos-shim-x64-20260515
Build instructions: docker/shim-build/Dockerfile in main repo; copies
                   into submission branch's root.

Notable design choices that may invite reviewer questions and are
documented in detail below:
- Ephemeral kernel-module signing keys (Q19)
- Two-secondary-contact custody architecture across 4 Nitrokeys (Q26)
- Lockdown auto-trigger via CONFIG_LOCK_DOWN_IN_EFI_SECURE_BOOT (Q17)

Cross-sign status: Founder PGP cross-signed with secondary contact
Ethan Bambock in Phase 1 of his onboarding (2026-05-11 ceremony,
post-submission-prep). Community cross-signer outreach in progress
(target: Issue-open-1d to Issue-open-7d).

39 question answers follow.

---
```

This shape matches #488 and #490 precedent: short context-setting paragraphs before the question table. Length target: 250-300 words. Long enough to context-set, short enough to not duplicate Q1-Q3.

---

## Section 2 — Reviewer-Q-response cadence

### Discipline

- **Acknowledge every reviewer comment within 24 hours** of when it lands. Precedent shows reviewers expect 24-48h reply-cycles once engaged.
- **Substantive answers within 48-72 hours** — even if the answer is "I'm researching X, will respond by [date]." The Miray submission's 91-day pace came from tight reply-cycles, not from reviewers being slow.
- **Never leave a reviewer thread open for more than 5 days without an update**, even if the update is "still researching, ETA [date]." Silence reads as abandoned.

### Channels

- **Primary:** GitHub Issue comments on the rhboot/shim-review Issue itself.
- **Secondary (if reviewer raises depth question):** PR against `InterGenJLU/shim-review:intergenos-shim-x64-20260515` to update the submission README / supporting files. When a PR lands in the fork, comment on the rhboot/shim-review Issue with a link so reviewers see the update.
- **Tertiary:** Email via `security@intergenstudios.com` for confidential / vulnerability-disclosure topics only. Bar is high — only for issues that need to go off the public record (e.g., a not-yet-disclosed vulnerability surfaced during review).

### Branch-update workflow when reviewers ask for changes

```
1. Reviewer comment lands on rhboot/shim-review Issue #NNN.
2. Acknowledge within 24h ("Thanks, looking into this — will respond
   within 48h").
3. If a docs change is needed:
   a. Make the change on the main-repo PR-prep branch.
   b. Cherry-pick to InterGenJLU/shim-review:intergenos-shim-x64-20260515
      branch.
   c. Update logs/ in the fork if reproducibility evidence changes.
   d. Re-comment on rhboot/shim-review Issue: "Updated [section] —
      <link to fork commit>. The change does [X] because [Y].
      Reviewer-runnable verification: <command>."
4. If a code change is needed in main repo (e.g., kernel-config tweak):
   a. Standard branch-and-PR workflow against main-repo master.
   b. Once merged, sync to fork README + bump SHIM_COMMIT_SHA / sbat
      generation if relevant.
   c. Re-comment on Issue with the cross-link.
5. Mark a reviewer's comment thread "resolved" only when the reviewer
   themselves indicates satisfaction. Never preemptively.
```

### What NOT to do

- **Don't argue with reviewers.** If a reviewer asks for change X, deliver X or explain why X isn't applicable — don't push back on the validity of the question. Precedent (Miray miray_memobj thread) shows reviewers will dig until they understand; arguing extends the cycle.
- **Don't batch multiple unrelated changes** into one comment. Each change-thread gets its own comment for cleaner review-tracking.
- **Don't edit the original Issue body** post-opening. Updates go in comments + fork PR links. rhboot/shim-review reviewers grep the original body; mutating it loses the trail.

---

## Section 3 — Fedora-shim-maintainers advisor thread

### When to invoke

Precedent: Fedora-team engagement (`@vathpela`, `@steve-mcintyre`) was invoked **only when vendor patches required upstream review** (ZeronsoftN at #433 — added ~10 weeks to the cycle). InterGenOS has **zero vendor patches** (Q10). Therefore Fedora-team engagement is **not structurally required** for the review itself.

### What is appropriate

A short, scoped advisor outreach **before** the Issue opens — not to seek formal sponsorship (InterGenOS does not pursue the Fedora-sponsorship path), but to:

1. Sanity-check the submission doc structure against current rhboot/shim-review template-version expectations
2. Flag the ephemeral-module-signing design (Q19) as a novel-for-the-process pattern and ask whether it warrants additional preemptive narrative
3. Confirm the dual-CA-vs-2023-only signature expectation given submission timing post-CA-expiration

### Format

**Subject:** `Pre-submission sanity check — InterGenOS shim-x64 (first submission, no vendor patches)`

**To:** `shim-maintainers@fedoraproject.org`

**Body (target ≤ 200 words):**

```
Hi shim-maintainers,

InterGenOS is preparing its first shim-review submission, target open
~2026-05-22 at rhboot/shim-review#NNN. Brief sanity-check ask:

(1) Submission doc is 39 questions populated; mirrored to fork README
at InterGenJLU/shim-review:intergenos-shim-x64-20260515. Does the doc
structure look right against the current template?

(2) We use ephemeral per-build kernel-module signing keys
(CONFIG_MODULE_SIG_KEY unset, regenerated per build, embedded pubkey
in UKI). This is novel relative to the major-distro long-lived-
module-key pattern. Q19 documents the design in detail. Any
preemptive framing you'd recommend so this doesn't read as a
misconfiguration on first review pass?

(3) Submission opens ~2026-05-22; mean-precedent merge ~138 days puts
realistic completion mid-October, past the 2026-06-27 MS 2011 CA
expiration. We've planned for 2023-CA-only signing. Any reason to
revisit?

We're not seeking formal sponsorship — operating community-reviewed
(Path B). Just checking we haven't missed structural prerequisites.

Thanks.
— Christopher Cork, InterGenOS
```

### Timing

- **Send: Issue-open-3d to Issue-open-7d** (after submission branch state is final, before the Issue opens).
- **Expected reply window:** none guaranteed. Fedora-team is volunteer-time; treat lack of response as neutral. Do NOT block submission opening on a reply.

### If a substantive reply lands

Incorporate the advice into the submission before opening the Issue. Reference the advisory exchange in the Issue body's opening paragraph: "Pre-submission advisory consultation with Fedora shim-maintainers confirmed [X]."

---

## Section 4 — Cross-sign coordination

### Goal

Per Q2 + Q39 of the submission doc, two cross-signing operations must be complete before Issue open:

1. **Founder ↔ Secondary maintainer mutual cross-sig** (Christopher Cork ↔ Ethan Bambock).
2. **At least one community-recognized cross-signer** signs the founder's key (preferably via established community keysigning event or remote keysigning session).

### Operation 1 — Internal cross-sig

**Trigger:** Ethan Phase-1 PGP key generation completes (today's ceremony 2026-05-11; produces Ethan's master key fingerprint).

**Workflow:**

```
1. Ethan generates Phase-1 PGP key on Tails-amnesic boot
   (ceremony today 2026-05-11). Key is RSA-4096, 2-year-expiry sub.
2. Ethan exports pubkey ASCII-armored; transmits to founder over
   secure channel (Signal or air-gapped USB).
3. Founder verifies key fingerprint matches Ethan-stated value via
   independent confirmation (in-person verbal, or secondary secure
   channel).
4. Founder signs Ethan's key on the founder's air-gapped Tails
   master keyring; produces signed pubkey.
5. Ethan signs the founder's key reciprocally (same workflow,
   inverted).
6. Both upload cross-signed pubkeys to keys.openpgp.org +
   keyserver.ubuntu.com (email-verified addresses).
7. Founder updates docs/shim-review-submission.md Q7 with Ethan's
   fingerprint.
8. Founder commits cross-sig artifacts to the submission fork under
   pgp/ directory:
   - intergenos-primary-pubkey.asc (founder)
   - intergenos-secondary-pubkey.asc (Ethan)
   Verify both have the cross-sig signature visible via
   `gpg --check-sigs <fingerprint>`.
```

**Closure signal:** Q7 fingerprint populated + both pubkeys on keys.openpgp.org + visible cross-sigs.

### Operation 2 — Community cross-signer

**Approach (merit-first per Q39):**

The community-cross-signer requirement is the de-facto "two security contacts cross-signed by a known community member" sponsorship-shape documented in rhboot/shim-review issue #512 + analyzed in `docs/research/installer/ms_shim_sponsorship_2026-04-18.md` §2.

**Target pool (in priority order):**

1. **Active rhboot/shim-review reviewers** — anyone in {steve-mcintyre, vathpela, rosslagerwall, lorddoskias, SherifNagy, NeilHanlon, jclab-joseph}. These are observable from the precedent walk — they're active in shim-review reviews currently. A cross-sign from one of these is the strongest signal.

2. **Linux Foundation / Debian / Arch / Fedora active contributors** with public keys visible on keyservers. Less specific to shim-review but still satisfies the "recognized community member" standard.

3. **Local keysigning event participants** if a hardware-key-signing event is reachable during the comms window.

**Outreach format (target Option 1):**

```
Subject: InterGenOS shim-review submission — community cross-sign request

Hi [reviewer],

InterGenOS is preparing its first shim-review submission targeting
2026-05-22 at rhboot/shim-review. Per the community-cross-signer
de-facto standard, I'm reaching out to ask if you'd consider
cross-signing the InterGenOS founder PGP key
(5597A3E0587B2530... RSA-4096; on keys.openpgp.org).

The submission doc is at:
https://github.com/InterGenJLU/shim-review/tree/intergenos-shim-x64-20260515

I'm happy to do this via:
- A short remote keysigning session (video confirmation + fingerprint
  exchange)
- An in-person keysigning if you're at $event between $date and $date
- A standard out-of-band pubkey-exchange + verification process if
  you prefer

If now isn't the right time / wrong fit, no worries — I'm pinging a
few people in parallel and any one cross-sig closes the loop.

Thanks for considering.
— Christopher Cork, InterGenOS
```

**Timing:**

- Outreach starts **Issue-open - 14d** (2026-05-08, already past).
- Realistic: outreach NOW, accept that community-cross-sig may land **post-Issue-open**. Issue opens with founder + Ethan cross-sig confirmed; community cross-sig follows.
- Update Q39 to reflect actual cross-sig status at Issue-open moment; no synthesis assertion of "will be cross-signed" unless we have a committed cross-signer.

**Closure signal:** ≥1 community member's signature visible on founder's pubkey via `gpg --check-sigs` against the canonical key.

---

## Pre-open checklist

Before opening the Issue at rhboot/shim-review, all of these must be true:

- [ ] `docs/shim-review-submission.md` and `InterGenJLU/shim-review:intergenos-shim-x64-20260515/README.md` are byte-identical (or strictly text-equivalent — any drift caught and closed)
- [ ] All 39 questions populated (no `__TBD__` markers — Q7 Ethan PGP fingerprint closed via Operation 1 above)
- [ ] All open footer-checklist items closed in the submission doc
- [ ] Issue-open date references all read `2026-05-22` (verified — already swept)
- [ ] Submission fork has: Dockerfile, vendor_cert.{der,pem}, sbat.intergenos.csv, shimx64.efi, SHIM_COMMIT_SHA, `logs/build_*.log`, `logs/verify-b2-reproducibility*.log`, pjones.asc, `pgp/` tree with founder + secondary pubkeys (cross-signed)
- [ ] ≥2 peer-review contributions delivered + linked in Q38 (per Section 2 cadence)
- [ ] Fedora advisor outreach sent (Section 3) — reply optional, not blocking
- [ ] Community cross-signer engaged (Operation 2) — signature optional pre-open, target post-open
- [ ] Founder ↔ Ethan cross-sig complete + on keys.openpgp.org (Operation 1)
- [ ] SBOM artifact at `docs/sboms/intergenos-shim-x64-20260515.spdx.json` (PGP-signed-detached) — separate deliverable in the InterGenOS shim-prep stream
- [ ] B2 reproducibility re-verify against current master tip (separate deliverable; post-Build-#8)
- [ ] Pre-submission tree-walk: `find docker/ scripts/ tests/ docs/research/installer/ docs/research/shim_review/ -type f -mtime -7` — review any last-week changes for unintended drift before opening
- [ ] Two open questions surfaced above (Q5 dual-CA timeline, Q23 filename mismatch) ratified by owner

---

## Methodology note

Precedent walk methodology:
- Source: 5 most-recent merged rhboot/shim-review issues filtered for small-distro / specialty-vendor + first-time-or-resubmission cases (Vanilla OS / NixOS / GhostBSD were initial target suggestions but have no merged shim-review issues in the repo — substituted with comparable-scope vendors).
- Per-issue extracted: days-to-merge, reviewer rounds, time-to-first-engagement, top reviewer asks, send-back patterns, shim-maintainer engagement pattern, cross-sign / MS-signed-return patterns, explicit cross-references.
- Synthesized: top-6 recurring asks (5/5), shim-maintainer engagement criteria, timeline implications.

Per the Q5 ratification 2026-05-11 (precedent-mirror approach confirmed) and the maintainer's audit-hardening REPORT-first discipline, this plan is delivered as a singleton process doc for owner-routed review. No technical claims in the submission doc are modified by this plan — the two open questions surfaced above are flagged for owner pick, not auto-applied.
