# Shim-review Submission Comms Plan — InterGenOS shim-x64-20260522

**Author:** chris-windows-code-claude (WC)
**Drafted:** 2026-05-11
**Branch:** `chris-windows-code-claude/shim-review-pr-prep`
**Target submission:** `https://github.com/InterGenJLU/shim-review/tree/intergenos-shim-x64-20260515` (branch name retained for backward-compat; submission opens against rhboot/shim-review around 2026-05-22)
**Companion doc:** `docs/shim-review-submission.md` (the 39-question populated submission body — this comms plan governs the conversation that doc enters)
**Basis:** Precedent walk of 5 recent merged rhboot/shim-review issues — source data + per-issue analysis saved to `C:/Users/ccork/research-intergenos/shim-review-threads/` (issues #433 ZeronsoftN / #479 10ZiG / #490 Miray / #488 opsi / #481 Blancco).

---

## Nomenclature note

`rhboot/shim-review` uses **GitHub Issues** for submission/review, NOT pull requests. Submissions open as an Issue with the populated 39-question template as the Issue body. The "PR" language in many places across InterGenOS internal docs is a misnomer; this comms plan uses "Issue" / "submission Issue" / "submission" throughout. (A separate F-finding in the WC triage queue tracks whether `docs/shim-review-submission.md` should sweep "PR-open" → "Issue-open" or accept the convention drift.)

---

## Timeline reality-check

### Precedent data

| Submission | Days open→merge | Reviewer rounds | First-engagement | Patches |
|---|---|---|---|---|
| #433 ZeronsoftN | 589 (outlier — upstream patch dep) | 1 substantive + pings | ~12 min | yes |
| #479 10ZiG | 257 | 6 | 11h | none |
| #490 Miray | 91 | 4 | 13 days | none |
| #488 opsi | 96 | 3 | 25 days | minimal |
| #481 Blancco | 109 (re-submission) | 1 | 5.5 days | none |
| **Mean (ex outlier)** | **138** | **3.5** | **bimodal** | — |
| **Fastest** | **91** | — | — | — |

### Implication for InterGenOS

- **PR-open target:** 2026-05-22 (current per SPOC slip ratification)
- **MS 2011 UEFI CA expiration:** 2026-06-27 (36 days post-open)
- **Earliest plausible merge** (best precedent, Miray 91 days): **2026-08-21**
- **Mean-precedent merge** (138 days): **2026-10-07**
- **Outlier-tail merge** (10ZiG 257 days): **2027-02-03**

**Hard structural finding:** merging the InterGenOS shim-review submission **before the 2026-06-27 MS 2011 CA expiration is structurally implausible** under any realistic-pace precedent. Even the fastest comparable submission (Miray at 91 days) overshoots the deadline by ~2 months. This has a direct consequence for Q5 of the submission doc, which currently asserts:

> "Our own shim-review submission, opened before the 2026-06-27 cert-transition deadline, will receive dual-signed (2011 + 2023 CA) binaries from Microsoft for maximum hardware compatibility — a strict improvement over the Fedora-piggyback posture."

This claim is **factually optimistic** given precedent — by the time the submission reasonably merges and Microsoft processes the signing, the 2011 CA will have ceased dual-signing. The InterGenOS shim will be **2023-CA-signed only**. A follow-on finding (tracked in the WC queue as **F16**) flags this for owner consideration: either rephrase Q5 to acknowledge the realistic dual-sign-vs-2023-only split, or accept the optimism with a fallback narrative ready.

### What InterGenOS can do to land at the fastest-precedent end

Three structural advantages tilt InterGenOS toward the 91-day Miray pace rather than the 257-day 10ZiG pace:

1. **No vendor patches** (Q10 attests zero code patches on shim) — eliminates the upstream-patch-review cycle that drove ZeronsoftN to 589 days.
2. **Dockerfile-driven cross-host reproducibility evidence already on master** (Q22 native-Linux SHA table, Q23 logs in fork) — reviewers will re-run and confirm rather than ask for evidence.
3. **Front-loaded answers to the 6 recurring reviewer asks** (see next section) — eliminates the round-trip on the questions most-frequently raised.

---

## Top 6 recurring reviewer asks (5/5 across precedent)

These are the asks that surfaced in **every** examined submission. InterGenOS's submission must pre-empt them in its Issue body / submission doc.

| # | Ask | InterGenOS preempt status |
|---|---|---|
| 1 | **NX bit / NX_COMPAT** — disabled? why? evidence? | Q11 documents `readelf -lW shimx64.efi` shows `GNU_STACK RW` (not `RWE`); NX-bit hardware-enforced under `CONFIG_X86_64=y` |
| 2 | **Build reproducibility via Dockerfile + SHA256** — reviewer will re-run | Q22 cross-host SHA table on master + `scripts/verify-b2-reproducibility.sh` 9-check harness; fork has logs/build_2026-05-06T08-42Z.log + logs/verify-b2-reproducibility-cross-host-2026-05-06.log |
| 3 | **Ephemeral kernel-module signing keys** — `CONFIG_MODULE_SIG_KEY` unset and per-build regenerated? | Q19 is the longest answer in the doc (305 lines of context); preempted in detail. Reviewers WILL grill this; we're ready. |
| 4 | **GRUB module list justification** — every non-standard module needs rationale | Q30 module table + `scripts/build-grub-standalone.sh` MODULES array (canonical source); each category has rationale column |
| 5 | **SBAT entries + version increments** — version drift across binary/README is caught | Q14 SBAT table + `scripts/check-sbat-generations.sh` precheck + `tests/sbat/test_check_sbat_generations.sh` PASS — README/binary drift risk mitigated structurally |
| 6 | **CA-cert constraints + key storage** — CA:TRUE + critical, hardware-token storage, validity | Q26 documents the Tails 7.7 ceremony, Nitrokey-3-NFC distribution (S1-S4 across 2 maintainers + 4 locations), 2-year-expiry rotation strategy, PIV-slot-9c on-card generation |

**All 6 are answered in detail in `docs/shim-review-submission.md` on master.** No surprise lurks here barring a reviewer questioning a SPECIFIC sub-detail.

---

## Section 1 — PR description (Issue body)

The Issue body opened against rhboot/shim-review is the rendered `README.md` from `InterGenJLU/shim-review` branch `intergenos-shim-x64-20260515`. This README MUST mirror `docs/shim-review-submission.md` on master with **zero drift** at submission moment. Fork-side README is currently 56 KB / 638 lines — needs a `docs/shim-review-submission.md` → fork `README.md` sync at submission time, then **no further main-repo edits** to the source doc until the submission Issue closes (to prevent README/submission drift, which 10ZiG hit hard).

**Issue title format** (per precedent shape):

```
Review request for InterGenOS shim 16.1 (x86_64)
```

Title-shape rationale: matches Miray ("Review request for Miray Software shim 16.1") + opsi ("Review request for opsi-shim 16.1"). NOT "InterGenOS shim-review submission" or "Please review InterGenOS shim." Reviewers grep titles by `Review request for <vendor> shim <version>`.

**Body opening (above the 39 questions):**

```markdown
Hello shim-review maintainers,

InterGenOS is a Linux-from-Source-derived distribution preparing its
first MS-signed shim submission, transitioning from a Fedora-piggyback
bootstrap to its own signed boot chain.

Build: shim upstream rhboot/shim @ tag 16.1 commit afc4955.
Vendor cert: CN=InterGenOS Secure Boot CA (RSA-4096, on-card generation
on Nitrokey 3 NFC PIV slot 9c during 2026-05-05 ceremony).
Reproducibility: Dockerfile + cross-host native-Linux verification
(2 independent witness hosts produced byte-identical SHAs).

Source repo (main project): https://github.com/InterGenJLU/intergenos
Submission branch: https://github.com/InterGenJLU/shim-review/tree/intergenos-shim-x64-20260515
Build instructions: docker/shim-build/Dockerfile in main repo; copies
                   into submission branch's root.

Notable design choices that may invite reviewer questions and are
documented in detail below:
- Ephemeral kernel-module signing keys (Q19)
- Two-secondary-contact custody architecture across 4 Nitrokeys (Q26)
- Lockdown auto-trigger via CONFIG_LOCK_DOWN_IN_EFI_SECURE_BOOT (Q17)

Cross-sign status: Founder PGP cross-signed with secondary contact
[Ethan Bambock] in Phase 1 of his onboarding (2026-05-11 ceremony,
post-submission-prep). Community cross-signer outreach in progress
(target: PR-open-1d to PR-open-7d).

39 question answers follow.

---
```

This opening matches precedent shape (Miray + opsi both used short context-setting paragraphs before the question table). Length: 250-300 words. Long enough to context-set, short enough to not duplicate Q1-Q3.

---

## Section 2 — Reviewer-Q-response cadence

### Discipline

- **Acknowledge every reviewer comment within 24 hours** of when it lands (precedent shows reviewers expect 24-48h reply-cycles once engaged).
- **Substantive answers within 48-72 hours** — even if the answer is "I'm researching X, will respond by [date]." The Miray submission's 91-day pace came from tight reply-cycles, not from reviewers being slow.
- **Never leave a reviewer thread open for >5 days without an update,** even if the update is "still researching, ETA [date]." Silence reads as abandoned.

### Channels

- **Primary:** GitHub Issue comments on the rhboot/shim-review Issue itself.
- **Secondary (if reviewer raises depth question):** PR against `InterGenJLU/shim-review intergenos-shim-x64-20260515` to update the submission README / supporting files. When a PR lands in the fork, **comment on the rhboot/shim-review Issue with a link** so reviewers see the update.
- **Tertiary:** Email via `security@intergenstudios.com` for confidential / vulnerability-disclosure topics. For shim-review the bar is high — only for issues that genuinely need to go off the public record (e.g., a not-yet-disclosed vulnerability surfaced during review).

### Branch-update workflow when reviewers ask for changes

```
1. Reviewer comment lands on rhboot/shim-review Issue #NNN.
2. Acknowledge within 24h ("Thanks, looking into this — will respond
   within 48h").
3. If a docs change is needed:
   a. Make the change on chris-windows-code-claude/shim-review-pr-prep
      (or a successor branch in main repo).
   b. Cherry-pick to InterGenJLU/shim-review intergenos-shim-x64-20260515
      branch.
   c. Update logs/ in the fork if reproducibility evidence changes.
   d. Re-comment on rhboot/shim-review Issue: "Updated [section] —
      <link to fork commit>. The change does [X] because [Y].
      Reviewer-runnable verification: <command>."
4. If a code change is needed in main repo (e.g., kernel-config tweak):
   a. Standard branch-and-PR workflow against main repo master.
   b. Once merged, sync to fork README + bump SHIM_COMMIT_SHA / sbat
      generation if relevant.
   c. Re-comment on Issue with the cross-link.
5. Mark the reviewer's comment thread "resolved" only when the
   reviewer themselves indicates satisfaction. Never preemptively.
```

### What NOT to do

- **Don't argue with reviewers.** If a reviewer asks for change X, deliver X or explain why X isn't applicable — don't push back on the validity of the question. Precedent (Miray miray_memobj thread) shows reviewers will dig until they understand; arguing extends the cycle.
- **Don't batch multiple unrelated changes** into one comment. Each change-thread gets its own comment for cleaner review-tracking.
- **Don't edit the original Issue body** post-opening. Updates go in comments + fork PR links. (rhboot/shim-review reviewers grep the original body; mutating it loses the trail.)

---

## Section 3 — Fedora-shim-maintainers advisor thread

### When to invoke

Precedent: Fedora-team engagement (`@vathpela`, `@steve-mcintyre`) was invoked **only when vendor patches required upstream review** (ZeronsoftN at #433 — added 10 weeks to the cycle). InterGenOS has **zero vendor patches** (Q10) — therefore Fedora-team engagement is **NOT structurally required** for the review itself.

### What is appropriate

A short, scoped advisor outreach **before** the Issue opens — not to seek formal sponsorship (we are not pursuing the Fedora-sponsorship path), but to:

1. Sanity-check the submission doc structure against current rhboot/shim-review template-version expectations
2. Flag the ephemeral-module-signing design (Q19) as a novel-for-the-process pattern and ask whether it warrants additional preemptive narrative
3. Confirm the dual-CA vs 2023-only signature expectation given submission timing post-CA-expiration (F16 above)

### Format

**Subject:** `Pre-submission sanity check — InterGenOS shim-x64 (first submission, no vendor patches)`

**To:** `shim-maintainers@fedoraproject.org` (per Q39 references + sponsorship analysis)

**Body (target ≤ 200 words):**

```
Hi shim-maintainers,

InterGenOS is preparing its first shim-review submission, target open
~2026-05-22 at rhboot/shim-review#NNN. Brief sanity-check ask:

(1) Submission doc is 39 questions populated; mirrored to fork
README at InterGenJLU/shim-review:intergenos-shim-x64-20260515.
Does the doc structure look right against the current template?

(2) We use ephemeral per-build kernel-module signing keys
(CONFIG_MODULE_SIG_KEY unset, regenerated per build, embedded
pubkey in UKI). This is novel relative to the major-distro
long-lived-module-key pattern. Q19 documents the design in
detail. Any preemptive framing you'd recommend so this doesn't
read as a misconfiguration on first review pass?

(3) Submission opens ~2026-05-22; mean-precedent merge ~138 days
puts realistic completion mid-October, post-2026-06-27 MS 2011 CA
expiration. We've planned for 2023-CA-only signing. Any reason to
revisit?

We're not seeking formal sponsorship — operating community-reviewed
(Path B). Just checking we haven't missed structural prerequisites.

Thanks.
— Christopher Cork, InterGenOS
```

### Timing

- **Send: PR-open-3d to PR-open-7d** (after submission branch state is final, before Issue opens).
- **Expected reply window:** none guaranteed. Fedora-team is volunteer-time; treat lack of response as neutral. Do NOT block submission opening on a reply.

### If a substantive reply lands

Incorporate the advice into the submission before opening the Issue, if any. Reference the advisory exchange in the Issue body's opening paragraph: "Pre-submission advisory consultation with Fedora shim-maintainers confirmed [X]."

---

## Section 4 — Cross-sign coordination

### Goal

Per Q2 + Q39 of the submission doc, two cross-signing operations must be complete before Issue open:

1. **Founder ↔ Secondary maintainer mutual cross-sig** (Christopher Cork ↔ Ethan Bambock).
2. **At least one community-recognized cross-signer** signs the founder's key (preferably via established community keysigning event or remote keysigning session).

### Operation 1 — Internal cross-sig

**Trigger:** Ethan Phase-1 PGP key generation completes (today's ceremony 2026-05-11 per memory calendar; produces Ethan's master key fingerprint).

**Workflow:**

```
1. Ethan generates Phase-1 PGP key on Tails-amnesic boot
   (ceremony today 2026-05-11). Key is RSA-4096, 2-year-expiry sub.
2. Ethan exports pubkey ASCII-armored; transmits to founder
   over secure channel (Signal or air-gapped USB).
3. Founder verifies key fingerprint matches Ethan-stated value
   via independent confirmation (in-person verbal, or secondary
   secure channel).
4. Founder signs Ethan's key on the founder's air-gapped Tails
   master keyring; produces signed pubkey.
5. Ethan signs the founder's key reciprocally (same workflow,
   inverted).
6. Both upload cross-signed pubkeys to keys.openpgp.org +
   keyserver.ubuntu.com (email-verified addresses).
7. Founder updates docs/shim-review-submission.md Q7 with
   Ethan's fingerprint (closes F1 + F2 + F10 from the WC
   triage REPORT).
8. Founder commits cross-sig artifacts to the submission fork
   under pgp/ directory:
   - intergenos-primary-pubkey.asc (founder)
   - intergenos-secondary-pubkey.asc (Ethan)
   Verify both have the cross-sig signature visible via
   `gpg --check-sigs <fingerprint>`.
```

**Closure signal:** Q7 fingerprint populated + both pubkeys on
keys.openpgp.org + visible cross-sigs.

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
cross-signing the InterGenOS founder PGP key (5597A3E0587B2530...
RSA-4096; on keys.openpgp.org).

The submission doc is at:
https://github.com/InterGenJLU/shim-review/tree/intergenos-shim-x64-20260515

I'm happy to do this via:
- A short remote keysigning session (video confirmation +
  fingerprint exchange)
- An in-person keysigning if you're at $event between $date and $date
- A standard out-of-band pubkey-exchange + verification process if
  you prefer

If now isn't the right time / wrong fit, no worries — I'm pinging
a few people in parallel and any one cross-sig closes the loop.

Thanks for considering.
— Christopher Cork, InterGenOS
```

**Timing:**

- Outreach starts **PR-open - 14d** (2026-05-08, today's date is past this).
- Realistic: outreach NOW, accept that community-cross-sig may land **post-Issue-open**. Issue opens with founder + Ethan cross-sig confirmed; community cross-sig follows.
- Update Q39 to reflect actual cross-sig status at Issue-open moment; no synthesis assertion of "will be cross-signed" unless we have a committed cross-signer.

**Closure signal:** ≥1 community member's signature visible on founder's pubkey via `gpg --check-sigs` against the canonical key.

---

## Pre-open checklist

Before opening the Issue at rhboot/shim-review, ALL of these must be true:

- [ ] `docs/shim-review-submission.md` and `InterGenJLU/shim-review:intergenos-shim-x64-20260515/README.md` are byte-identical (or strictly text-equivalent — any drift caught + closed)
- [ ] All 39 questions populated (no `__TBD__` markers — F1 + F2 + F10 closed via Operation 1 above)
- [ ] All open footer-checklist items closed (per WC REPORT 2026-05-11 — F3/F5/F8/F14/F15a closed in `d203e53f`; F11.a verified via fork-side audit; F11/F12/F13 close cascade)
- [ ] PR-open date references all read `2026-05-22` (F14 closure verified)
- [ ] Submission fork has: Dockerfile, vendor_cert.{der,pem}, sbat.intergenos.csv, shimx64.efi, SHIM_COMMIT_SHA, logs/build_*.log, logs/verify-b2-reproducibility*.log, pjones.asc, pgp/ tree with founder + secondary pubkeys (cross-signed)
- [ ] ≥2 peer-review contributions delivered + linked in Q38 (F9.b execution per this plan's Section 2)
- [ ] Fedora advisor outreach sent (Section 3) — reply optional, not blocking
- [ ] Community cross-signer engaged (Operation 2) — sig optional pre-open, target post-open
- [ ] Founder ↔ Ethan cross-sig complete + on keys.openpgp.org (Operation 1)
- [ ] SBOM artifact at `docs/sboms/intergenos-shim-x64-20260515.spdx.json` (PGP-signed-detached) — separate WC lane deliverable
- [ ] B2 reproducibility re-verify against current master tip (Item 3 of WC lane; post-Build-#8)
- [ ] Pre-submission tree-walk: `find docker/ scripts/ tests/ docs/research/installer/ docs/research/shim_review/ -type f -mtime -7` — review any last-week changes for unintended drift before opening

---

## Tracking findings beyond F15

| F# | Origin | Status | Notes |
|---|---|---|---|
| F1 | WC REPORT 2026-05-11 | OPEN — external dep | Closes with Operation 1 of this plan (Ethan Phase-1) |
| F2 | WC REPORT 2026-05-11 | OPEN — external dep | Closes with Operation 1 |
| F3 | WC REPORT 2026-05-11 | CLOSED `d203e53f` | grub2-cve-audit.md sha256 inline |
| F5 | WC REPORT 2026-05-11 | CLOSED `d203e53f` | Q28 past-tense conditional |
| F8 | WC REPORT 2026-05-11 | CLOSED `d203e53f` | Q36 assertion-of-Q30 |
| F9 | WC REPORT 2026-05-11 | SPLIT — (a) CLOSED `d203e53f` date, (b) OPEN peer-review work | Section 2 cadence governs F9.b execution |
| F10 | WC REPORT 2026-05-11 | OPEN — external dep | = F1 mirror |
| F11 | WC REPORT 2026-05-11 | F11.a VERIFIED PASS today | Both Q23 logs exist; F11 cascade-closes once F11.a sub-finding (Q23 filename mismatch — `verify-b2-reproducibility.log` doc vs `verify-b2-reproducibility-cross-host-2026-05-06.log` actual) resolved |
| F11.a (sub) | F11.a fork audit 2026-05-11 | NEW FINDING | Q23 references `logs/verify-b2-reproducibility.log` — actual fork filename is `logs/verify-b2-reproducibility-cross-host-2026-05-06.log` (~5 char `cross-host-2026-05-06` suffix delta). Owner pick: doc-side rename to match fork, or fork-side rename to match doc, or accept divergence with a footnote |
| F12 | WC REPORT 2026-05-11 | OPEN | = F9.b dependency |
| F13 | WC REPORT 2026-05-11 | OPEN — meta | Closes when F1-F12 close |
| F14 | WC REPORT 2026-05-11 | CLOSED `d203e53f` | PR-open date sweep |
| F15(a) | WC REPORT 2026-05-11 | CLOSED `d203e53f` | Convention preamble tightened |
| **F16** | **THIS PLAN — Timeline analysis** | **NEW FINDING** | **Q5 dual-signed-shim claim is factually optimistic given precedent merge times. Mean-precedent merge ~138 days from 2026-05-22 = ~2026-10-07, past the 2026-06-27 MS 2011 CA expiration. Realistic outcome: 2023-CA-signed-only shim. Owner pick: rephrase Q5 with realistic dual-vs-single posture, OR keep claim with fallback narrative, OR push Issue-open earlier (which has its own readiness implications)** |

---

## Methodology note

Precedent walk methodology:
- Source: 5 most-recent merged rhboot/shim-review issues filtered for small-distro / specialty-vendor + first-time-or-resubmission cases (Vanilla OS / NixOS / GhostBSD were the SPOC-suggested targets but have NO merged shim-review issues in the repo — substituted with comparable-scope vendors).
- Per-issue extracted: days-to-merge, reviewer rounds, time-to-first-engagement, top reviewer asks, send-back patterns, shim-maintainer engagement (Fedora-team ping pattern), cross-sign / MS-signed-return patterns, explicit cross-references.
- Synthesized: top-6 recurring asks (5/5), shim-maintainer engagement criteria, timeline implications.
- Source data preserved: `C:/Users/ccork/research-intergenos/shim-review-threads/{433,479,490,488,481}-{issue,comments}.json` + human dump.

Per SPOC's Q5 ratification 2026-05-11T19:59:38Z (precedent-mirror approach confirmed) and the audit-hardening REPORT-first discipline, this plan is delivered as a singleton process doc for owner-routed review. No technical claims in the submission doc are modified by this plan — F16 surfaces a Q5 reframing question but does not unilaterally edit it.
