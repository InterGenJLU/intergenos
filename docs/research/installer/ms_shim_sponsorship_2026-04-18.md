# InterGenOS Secure Boot — MS UEFI CA Shim Signing via shim-review

**Prepared:** 2026-04-18 | **Target:** Own Microsoft-signed shim within ~3 months | **Status:** research draft, untracked; feeds D1-7b (Option A start-date) decision
**Context:** InterGenOS is a solo-dev LFS-derived distro, currently piggybacking on Fedora's pre-signed shim for Monday 2026-04-20 launch (D1-7 Option B). This doc scopes the post-Monday parallel track to replace that with our own MS-signed shim.

---

## Executive summary — actionable findings

- **Sponsorship is not formally required.** `github.com/rhboot/shim-review` accepts direct submissions from any entity shipping an open-source boot chain. Fedora's shim-maintainers (`shim-maintainers@fedoraproject.org`) serve as advisors, not legal sponsors. We can submit directly.
- **Path B (shim-review PR → Red Hat batches to Microsoft) is the realistic path.** Path A (direct Partner Center with EV cert) is overkill for solo-dev and requires the October 2025 annual security audit rule (OCP SAFE, $10k-$50k) unless all chained bootloaders are open source — which for us they are, so audit requirement is waived via shim-review acceptance.
- **HARD DEADLINE: submit before June 27, 2026.** This is when the Microsoft 2011 UEFI CA expires. Submissions accepted after that date get only 2023-CA signatures, which older firmware will not trust. Hitting the window means dual-signed (2011 + 2023) shim binaries with max hardware compatibility.
- **Timeline:** 10-14 weeks realistic end-to-end. 2-3 weeks prep, 2-3 months shim-review iteration, 1-3 weeks Microsoft signing turnaround. Starting within 2 weeks of Monday puts the PR-open date around 2026-05-15, well inside the June 27 window.
- **Zero direct monetary cost** if we stay fully open-source. Red Hat absorbs Partner Center / EV-cert / submission fees. Incidentals: ~$80 for Nitrokey/YubiKey (already in D1 plan).
- **Biggest review blocker: ephemeral per-build kernel-module signing is novel.** Reviewers will push back unless we document clearly in the README that (a) the ephemeral key never signs a bootloader, (b) it's reaped when the kernel build completes, (c) it's distinct from the vendor cert embedded in shim.
- **TWO PGP security contacts, cross-signed, required.** **Primary: Chris (christopher).** **Secondary: Ethan Bambock** (peer-constrained 2nd, confirmed 2026-04-20 per `docs/succession/ethan_onboarding/README.md`). Ethan's PGP keypair generation is in Phase 1 of his onboarding checklist; fingerprint publication + cross-signing with Chris's key will update this doc in a follow-up commit once available.

---

## 1. Microsoft UEFI CA signing workflow

Two paths exist:

- **Path A (direct Partner Center):** requires EV code-signing cert (~$300-500/yr), Azure AD tenant, Hardware Dev Center onboarding, AND as of 2025-10-20 an annual independent security audit (Scope-1 OCP SAFE program, $10k-$50k) unless your boot chain is fully open-source. Red Hat, Canonical, SUSE use this.
- **Path B (shim-review):** submit PR to `github.com/rhboot/shim-review`, pass peer review, receive "accepted" label. Red Hat then bundles accepted shims and submits to Microsoft on behalf of the distro. Microsoft returns signed binaries — **two per submission** (one 2011-CA, one 2023-CA) through June 27, 2026; 2023-only after that. Used by Debian, Rocky, openSUSE Tumbleweed, Pop!_OS, Navix, and others.

**Path B is the only realistic option for InterGenOS** as a solo-dev open-source distro. The shim-review PR itself satisfies Microsoft's security-audit requirement via the open-source-only policy.

Ref: Microsoft Partner Center blog, 2023 cert transition post.

---

## 2. Sponsorship requirements

A formal sponsor is **not required** by shim-review policy (issue #512). In practice:

- Two security contacts with published PGP keys — ideally cross-signed by each other AND by "reasonably well-known members of the Linux community." This is the de-facto sponsorship mechanism: a known community member vouching for your keys via signature.
- Fedora's shim-maintainers routinely advise new submitters; they do not issue formal sponsorships but can answer questions and sign keys. Contact: Peter Jones + the `shim-maintainers@fedoraproject.org` list.
- openSUSE (`security-team@suse.de`): signs its own keys, does not publicly sponsor externals.
- Canonical and Red Hat: no formal sponsorship of externals.

**For InterGenOS:** engage Fedora shim-maintainers as advisors (advisory email target 2026-05-02); Chris + Ethan generate PGP keypairs, cross-sign each other's keys, and get at least one cross-signed by a known community member at a local Linux event or via remote keysigning session.

---

## 3. shim-review submission anatomy

A submission is a fork of `rhboot/shim-review` with a branch/tag named like `intergenos-shim-x64-20260515`, containing a completed `README.md` answering **39 template questions**, plus artifacts:

- `shim.efi` (binary to be signed)
- Full build logs (buildroot, patches, compile, archiving)
- `Dockerfile` that reproduces the binary byte-for-byte when `docker build .` runs
- Public cert embedded in shim (the distro vendor cert)
- Any shim or GRUB2 patches
- SHA256 of the final binary
- SBAT metadata dump (`objcopy --dump-section .sbat=/dev/stdout`)
- Hashes for any binaries allow-listed via `vendor_db`

**Key template questions** (abridged — full list in repo README):

- Legal entity verification + product description
- Justification for global signing (why can't you reuse someone else's?)
- Two security contacts with PGP fingerprints + published on keyservers
- Confirmation binaries derive from **shim 16.1 release tarball**
- Every GRUB2 CVE through Feb 2025 patched
- SBAT generation ≥ 5
- Kernel lockdown commits applied (three specific upstream commits) and enforced under Secure Boot
- NX-bit enforcement
- **Vendor-cert key-management strategy** (where our D1 v2 Nitrokey/YubiKey + offline Tails root + ephemeral-per-build module story lives)
- **Ephemeral kernel-module key usage** (how ephemeral, reaped when, never reused)
- Old-shim-hash strategy (vendor_dbx)
- Have you reviewed other people's submissions? (Yes, at least 1-2)

**Typical reject reasons:**
- Missing or malformed SBAT entries
- Dockerfile that doesn't rebuild byte-identical binary
- PGP keys not on keyservers or unsigned
- Any GRUB2 CVE not patched
- Cert previously used to sign vulnerable binaries
- Submitter has never reviewed others' PRs (peer-review queue de-prioritization)

---

## 4. Commitments Microsoft wants

**Best-effort commitments stated in the README — not a separate legally-binding contract.**

- **Revocation response:** able to produce new shim + coordinate dbx publication within ~90 days of coordinated CVE disclosure.
- **Security reporting policy:** public disclosure contact + PGP key, documented embargo. Our combined D1-8 disclosure policy (48h ack / 14-30-60-90d fix by severity / 90d max embargo / immediate trust-anchor compromise) fits cleanly here.
- **Reproducible build:** the Dockerfile requirement makes this mandatory.
- **Update cadence for the shim:** no hard SLA, but expectation is staying on current shim major version (16.x today).
- **Governance continuity:** no formal succession plan required, but two security contacts is mandatory so single-person-leaving doesn't orphan the shim.

None are legally binding. Public representations in the PR; breaking them results in community reputation damage and potential dbx revocation.

---

## 5. What Microsoft sees

During signing, Microsoft sees: the shim binary (vendor cert embedded — public by design); Partner Center submission metadata Red Hat forwards on our behalf (distro name, version, CA chain); hash of what they're signing.

They do **NOT** see the shim-review README, source tree, or build logs — all public on GitHub.

**Privacy for solo-dev:**
- Vendor cert CN: `CN=InterGenOS Secure Boot CA` (not personal name)
- Security contact: role address `security@intergenstudios.com` with PGP pointing at role key
- Founder's name WILL appear in PGP signatures + public PR discussion. Unavoidable but minimizable.

---

## 6. Timeline realism

- **Best case** (Navix 2024, single-person RHEL-derivative): ~6 weeks end-to-end.
- **Typical** (Debian 13 trixie, Rocky, openSUSE Tumbleweed 2024-2025): 2-3 months PR-open to "accepted", then 1-3 weeks Microsoft signing.
- **Worst case** (first-time submitter with multiple SBAT/CVE/Dockerfile revisions): 4-6 months.

**Hidden hard deadline: June 27, 2026.** Between now (Apr 2026) and June 27, Microsoft dual-signs. After June 27, only 2023-CA. Older firmware that only trusts 2011 CA will not boot a 2023-only-signed shim. Targeting the window pre-June 27 gets us dual-signed binaries and maximum hardware compatibility.

Ref: `github.com/rhboot/shim/issues/679`.

### Empirical note (2026-04-18, claude-main 23:11 UTC)

Fedora's current `shim-x64-16.1-2.x86_64.rpm` shipped with **MS 2011 CA signature ONLY** — not dual-signed with 2023 CA. Verified via `sbverify --list shimx64.efi`:
- Signature 1 of 1
- Issuer: Microsoft Corporation UEFI CA 2011
- No 2023-CA signature present on this specific NVR

**Implication for Monday piggyback:** fine. 2011 CA is trusted by virtually every UEFI firmware shipped since ~2012. Our users' firmware will trust the 2011-signed shim.

**Implication for our own MS-signed shim (Option A):** unchanged. Our shim-review submission (before 2026-06-27) will still receive dual-signed binaries from Microsoft regardless of what Fedora currently ships. Our independence from the piggyback is the whole point of Option A.

**Possible explanation for Fedora's single-sig posture:** 2023-CA dual-signing may require a newer shim-review submission round that Fedora has not yet completed. Worth monitoring — if Fedora eventually ships a dual-signed shim 16.x-N, we may want to refresh our vendored copy.

**Verified artifact (pre-upload):** sha256 `1662629916cf2322ff8e6333439643bca63e6e6327d2e6089bf66cb7a8c5dc4b`, size 489,859 bytes, NVR `shim-x64-16.1-2` (release 2, no fcXX suffix — main's package.yml commit c50d559 corrected the NVR pattern).

---

## 7. Costs

**Zero direct $ via Path B (shim-review).** Red Hat absorbs Partner Center/EV/submission fees.

Incidentals (mostly already planned):
- Nitrokey 3 NFC or YubiKey 5 NFC: ~$80 (already in D1 v2)
- Offline signing station: retired laptop (already have)
- PGP keysigning event: $0-$200 (virtual free; in-person = travel)
- **Optional independent audit:** only if shim chainloads proprietary code. **We avoid by staying fully open-source.** If triggered, $10k-$50k.
- **EV cert** (~$300-500/yr): only needed for Path A. Skip.

---

## 8. Revocation mechanics

1. CVE reported (90-day private embargo typical).
2. Distro + shim maintainers coordinate: new shim + SBAT generation bump.
3. Microsoft notified via Partner Center pipeline; Microsoft issues signed dbx update listing vulnerable shim hash.
4. Update propagates via three channels:
   - Windows Update (dual-boot machines)
   - LVFS / `fwupd` (Linux machines)
   - UEFI Forum revocation list (OEM firmware updates)
5. On InterGenOS: `fwupd` applies dbx on next run.

Known ecosystem weakness (documented by Binarly): Windows Update is the dominant dbx distribution channel, Linux-only distros inherit Microsoft's publishing schedule.

If OUR shim is compromised: email shim-review maintainers + Microsoft UEFI signing contact, dbx entries added at next batch. **D1-8 trust-anchor policy's 6-hour response window should account for this: our revocation request is fast, Microsoft's dbx publish is not, so we also directly push keyring updates to users via pkm — they get the new key before Microsoft gets the old one into dbx.**

---

## 9. Concrete plan for InterGenOS

**Step 1 — Weeks 1-2: Advisory outreach.**
Email `shim-maintainers@fedoraproject.org` from `security@intergenstudios.com`. Subject: "InterGenOS shim-review submission prep — guidance request." Body: one-paragraph intro, one paragraph on build-from-source posture, one paragraph listing specific questions (SBAT generation numbering for new-vendor distro, ephemeral module-signing key posture, Tails-based root-cert custody concerns). Do NOT ask them to sponsor. Ask for guidance only.

**Step 2 — Weeks 2-4: Prepare artifacts.**
- Fork `github.com/rhboot/shim-review` → `github.com/InterGenJLU/shim-review`
- Build shim 16.1 with our vendor cert embedded
- Write `Dockerfile` that reproduces byte-for-byte
- Generate two PGP keys (Chris + Ethan); publish to `keys.openpgp.org` with role-UID hardening per D1-6; cross-sign each other's keys
- Draft `README.md` answering all 39 questions — reference the v2 D1 custody doc in Q26 (key management) + D1-8 disclosure policy in the security-contact section
- Peer-review at least 2 open shim-review PRs (community contribution gate)

**Step 3 — Week 4-6: Open PR.**
Tag `intergenos-shim-x64-20260515` (date matches target submission), push, file issue at `https://github.com/rhboot/shim-review/issues/new` linking the tag.

**Step 4 — Weeks 6-14: Iterate.**
Expect 3-5 rounds of reviewer comments. Respond within 48 hours. Never argue; if a reviewer asks for a change, make it. Common asks: SBAT tweaks, Dockerfile reproducibility fixes, stronger language around ephemeral-key reaping.

**Step 5 — Week 14+: Signing and release.**
"Accepted" label applied → Red Hat batches to Microsoft → signed binaries return in 1-3 weeks (two copies, 2011 + 2023 CAs if before June 27 2026) → publish via pkm.

---

## 10. Red flags / deal-breakers

- **Any proprietary code in the boot chain.** InterGenOS is GPL-3.0 end-to-end — fine.
- **Secondary security contact requirement.** Now satisfied — Ethan Bambock confirmed 2026-04-20 as peer-constrained 2nd. PGP keypair generation is in his onboarding Phase 1; complete before the 2026-05-15 shim-review PR target.
- **Vendor cert previously used to sign any vulnerable binary.** Ours is fresh — fine.
- **Unclear ephemeral-key story.** Novel; reviewers will dig. Document preemptively.
- **No peer-review contribution.** Submitters who haven't reviewed others get queued behind those who have.
- **Missing CVE patches.** Every GRUB2 CVE through Feb 2025 must be applied. No exceptions.
- **Non-reproducible Dockerfile.** Reviewers run it; if output hash doesn't match, PR stalls.
- **Weak governance-continuity story.** Mitigate via named secondary contact + public commitment to orderly-hand-off-or-dbx-revoke if Chris becomes unavailable.

---

## Action checklist

- [ ] Chris drafts outreach email to `shim-maintainers@fedoraproject.org` — send this week
- [x] **Secondary security contact identified + confirmed.** Ethan Bambock (2026-04-20). Role-framing + onboarding in `docs/succession/ethan_onboarding/`.
- [ ] Ethan generates his PGP keypair (Phase 1 of onboarding checklist). Fingerprint updates this doc in follow-up commit.
- [ ] Generate PGP keys for `security@intergenstudios.com` role + personal (Chris); publish to `keys.openpgp.org`; cross-sign with Ethan's key.
- [ ] Arrange cross-signing (keysigning event or trusted community member)
- [ ] Fork `rhboot/shim-review` to `InterGenJLU/shim-review`
- [ ] Build shim 16.1 with InterGenOS vendor cert, wrap in reproducible Dockerfile
- [ ] Apply all GRUB2 CVE patches through Feb 2025, verify SBAT generation ≥ 5
- [ ] Draft README.md answering all 39 template questions
- [ ] Write explicit "ephemeral module-signing key" section referencing v2 D1 custody doc
- [ ] Peer-review ≥ 2 open shim-review PRs before submitting ours
- [ ] **Target PR-open date: before June 27, 2026** (dual-CA window)
- [ ] Establish `fwupd` integration plan so dbx updates reach InterGenOS users automatically
- [ ] Keep R27-style AI-assistant test coverage green through all of the above

---

## Feeds D1-7b (Chris decision)

Original D1-7b question: Option A MS-signed shim start-date.

**Data for Chris:**
- Start within 2 weeks of Monday (~2026-05-04) → PR-open by ~2026-05-15 → fits June 27 window comfortably.
- Delay beyond ~2026-05-15 start → PR-open ~June → possibly miss the June 27 dual-CA window → only 2023-CA signature → subset of older firmware won't boot.
- Delay beyond June 27 → guaranteed 2023-only → older firmware incompatible. Not catastrophic (most modern firmware trusts 2023 CA too) but a brand/compatibility hit.

**Recommendation:** start Step 1 (advisory email) within 2 weeks of Monday launch. Step 2 artifacts prep in parallel with Monday-install stabilization work.

**Blocker retired 2026-04-20:** secondary security contact confirmed (Ethan Bambock, peer-constrained 2nd). Onboarding scaffold at `docs/succession/ethan_onboarding/` tracks the Phase 1 PGP keypair generation milestone; the 2026-05-15 shim-review PR target leaves adequate runway.

---

## Sources

- shim-review repo: https://github.com/rhboot/shim-review
- shim-review README (39 template questions): https://github.com/rhboot/shim-review/blob/main/README.md
- Open-source-only policy: https://github.com/rhboot/shim-review/issues/512
- Fedora historical shim-signing procedure: https://pjones.fedorapeople.org/shim-signing-procedure.html
- Microsoft 2023 UEFI cert transition: https://techcommunity.microsoft.com/blog/hardware-dev-center/signing-with-the-new-2023-microsoft-uefi-certificates-what-submitters-need-to-kn/4455787
- Microsoft UEFI signing requirements updated: https://techcommunity.microsoft.com/blog/hardware-dev-center/updated-microsoft-uefi-signing-requirements/1062916
- 2026 CA expiration context: https://github.com/rhboot/shim/issues/679, https://access.redhat.com/articles/7128933
- Matthew Garrett on small distros: https://mjg59.dreamwidth.org/17542.html
- Binarly dbx / revocation analysis: https://www.binarly.io/blog/from-trust-to-trouble-the-supply-chain-implications-of-a-broken-dbx
