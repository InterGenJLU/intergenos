# Ceremony Lessons Learned — 2026-04-30 → 2026-05-05

**Audience:** the future maintainer who has to redo this ceremony — for subkey rotation in 2028, for emergency revocation + re-issue, or for the next project that wants an offline-master + 4-NK signing setup. Read this **before** writing any code.

**Outcome:** Master FP `5597A3E0587B253006D0DD7B8C50826182083050` published on `keys.openpgp.org`, all four NKs hold signing subkeys with UIF=on, PIV vendor cert minted on NK#1, LUKS master backup on Drive #3, base16 paperkey printed and on Drive #2, ethan-pack curated on Drive #3.

**Effort to reach that outcome:** five days, ~1500 lines of `ceremony.py`, 24+ ratified bug fixes, two complete restarts of the cards (factory-reset all four), one near-disaster from a parser bug (Day 5: substring-match against spaced gpg output silently ran `addkey` on every NK on resume — caught before `gpg --armor --export` published the orphaned subkeys).

This document is a record of what worked, what bit us repeatedly, and what the v3 refactor needs to bake in from the start.

---

## 1. Final ceremony state

### Artifacts

| Artifact | Location | Notes |
|---|---|---|
| Master pubkey | `keys.openpgp.org` (FP-indexed + email-verified), `docs/signing-key.asc` | Published 2026-05-05 |
| Master secret | LUKS volume on Drive #3 (`master-backup.luks`, 50 MB) | Owner-only physical custody; LUKS pass on paper |
| Paperkey (base16) | Printed × 2 (home safe + offsite), `paperkey-5597A3E0.txt` was on Drive #2 (shredded post-ceremony) | Use only for absolute-disaster recovery |
| Revocation cert | Inside the LUKS volume alongside the master secret | Plus a paperkey of the master, paperkey of the revocation cert |
| 4× signing subkeys | Keytocard'd to NK#1–#4 slot 1, UIF=on, 2y expiry | Owner pubkey URL = canonical KOO form |
| EFI vendor cert | NK#1 PIV slot 9c, mgmt key rotated to fresh AES-256 | SHA-256 + new mgmt key on owner's paper only |
| Ethan-pack | Drive #3 `ethan-pack/`, copied to Drive #2 for shipment | Pubkey + README + sign.sh + verify.sh |

### Hardware & paper

- 4 × Nitrokey 3 NFC tokens (OpenPGP serials `B9753481`, `43D33126`, `730D5185`, `CC1D07E3`)
- Drive #1 (boot/install media, ships to Ethan)
- Drive #2 (`OFFLINEDEBS`, sanitized post-ceremony, ships to Ethan with offline-debs + scripts + ethan-pack)
- Drive #3 (`CEREMONY`, master-backup.luks + identity-log + vendor cert + ethan-pack source — stays in physical safe with owner)
- Drive #4 (spare; not used in final shipping plan)
- Paper: master FP, master pass, LUKS pass, 16 PINs (4 per NK × 4 NKs), vendor cert SHA-256, new PIV mgmt key

### Tooling final state

`packages/ai/intergen/`-adjacent ceremony bundle at `~/intergenos/research/ceremony/c6-offlinedebs/`:
- `scripts/ceremony.py` — ~1500 lines, full ceremony script (master keygen + Phase 0 + Stages 4–10)
- `scripts/validate.py` — read-only post-ceremony audit, 5 sections
- `scripts/bootstrap.sh` — installs offline-debs + execs ceremony.py
- `scripts/diag-c6.sh` — standalone PIV diagnostic
- `debian13/` — 71 Tails-Python-3.13 .debs (gnupg, opensc-tool, pkcs11-tool, paperkey, pexpect, etc.)
- `wheels/` — 5 pure-Python wheels (pynitrokey, nitrokey, libusb1, nethsm, nkdfu) for the optional `nitropy` CLI

---

## 2. Timeline (compressed)

| Day | What happened |
|---|---|
| 2026-04-30 (Thu) | Original ceremony attempt. Manual interactive approach. Fat-fingering across 16 PINs + multi-step menus produced cascading PIN-counter exhaustion. Script attempts began that night — half-automated `00-prep.sh` through `04-luks-backup.sh`. |
| 2026-05-01 (Fri) | First `ceremony.py` consolidating the shell scripts. Hit pcscd/scdaemon caching issues immediately — keytocard would silently fail with `No such device` between cards. |
| 2026-05-02 (Sat) | Refined the offline-debs bundle. Added `c6_airgap_execution_*.md` + `nk4_prep_instructions_*.md` runbooks. |
| 2026-05-03 (Sun) | More iterations. PIN-order discovered (master_pass FIRST in keytocard inquiry, admin_pin SECOND — confirmed via gpg-agent debug log). Card key-attr forced to rsa4096 before keytocard (default rsa2048 → import fails with misleading "Invalid time" error). |
| 2026-05-04 (Mon) | Half the day on Stage 9 PIV chain (Nitrokey 3's PIV applet has a known PKCS#11 quirk — solved by mimicking diag-c6.sh's exact 4-call sequence). Validated end-to-end with NK#4 dry-run. Real ceremony halted at §0.5 dry-run on offline Tails. Drive #2 shuttled to workstation for diagnosis. |
| 2026-05-05 (Tue) | Fresh restart of the ceremony. Day-3 attempt at recovery had been polluted by a buggy `cleanup_disk_resident_sign_subkeys` that misclassified on-card stubs as orphans and deleted them. Today: factory-reset all 4 NKs at workstation, wiped Drive #3, fresh ceremony from scratch. Then the cascade — `addkey buildup` from a substring-match parser bug; `cleanup_dead_card_refs` with double-read defenses; `validate.py` parser bug giving false catastrophic readings; finally **0 failures** at ~5 PM CDT. |

---

## 3. Bug catalogue

The full chronological catalogue is in the carryovers (`context_carryover_20260505_ceremony_stage7_recovery.md` etc.) — this is the deduplicated lesson list.

### Category A — pcscd/scdaemon stack flakiness

**A1. `gpgconf --kill scdaemon; sleep 1` is not enough between card swaps.** Multiple unplug/replug cycles leave pcscd's reader list stale. The next BLAST spawns a new scdaemon, queries pcscd, gets `pcsc_list_readers failed: no readers available (0x8010002e)`. **Fix:** `revive_pcscd()` = `gpgconf --kill scdaemon` + `sudo systemctl restart pcscd` + sleep — full restart, not just kill. Use it before EVERY card-changing operation, never `kill+sleep`.

**A2. First card-status read after `revive_pcscd` can return partial data.** Symptom: `Serial number: ?` or `Signature key: [none]` even when the card is fine. **Fix (DS-suggested):** double-read with 3s gap; only act on the unhealthy reading if it's confirmed by a second read. Cheap when first read is healthy (no wait penalty).

**A3. scdaemon's CCID-direct mode conflicts with pcscd holding the readers.** Without `disable-ccid` + `pcsc-shared` in `scdaemon.conf`, every card op gets `No such device` indefinitely. **Fix:** `scdaemon.conf` must be in **the ceremony's `GNUPGHOME`** (e.g., `~/ceremony/gnupg-master/scdaemon.conf`), not just `~/.gnupg/scdaemon.conf`. The ceremony's gpg-agent picks up the config from its own home, not the default one.

**A4. `gpgconf --kill scdaemon` operates on the env-defined GNUPGHOME.** If `os.environ["GNUPGHOME"]` isn't set globally before the kill, gpgconf kills the wrong scdaemon (the one for `~/.gnupg`) while the ceremony's stays stuck. **Fix:** set `os.environ["GNUPGHOME"]` once at start of stage_master_keypair (and in resume mode after locating the existing keyring), not just per-subprocess `env=...` — `gpgconf` invocations don't always use the local env dict.

### Category B — gpg `--card-edit` BLAST input

**B1. PIN order in keytocard inquiry: master_pass FIRST, admin_pin SECOND.** Confirmed via gpg-agent debug log. The KEYTOCARD command issues two pinentry inquiries in this order. Every other order gives "Bad PIN" errors that look like the card is broken.

**B2. `addkey` BLAST consumes master_pass via `--passphrase` flag, NOT stdin.** With `--pinentry-mode=loopback`, gpg-agent reads master pass from `--passphrase` / `--passphrase-fd`. Putting master_pass in the BLAST input stream causes silent abort (gpg consumes it as unrecognized command).

**B3. Card key-attr must be set to `rsa4096` BEFORE keytocard.** Default Nitrokey 3 OpenPGP attribute is `rsa2048`; importing an RSA-4096 sub fails with the misleading "KEYTOCARD failed: Invalid time" (mapped from the card's wrong-params SW). Walk all 3 slots through the key-attr menu (3 admin-PIN prompts).

**B4. keytocard `replace-y` when slot is occupied.** If a slot already has a key (e.g., resumed run, or replace-with-fresh), gpg prompts `Replace existing key? (y/N)` BEFORE pinentry. The BLAST input has to include `y` in that position. Detect via `gpg --card-status` Signature key field.

**B5. BLAST input must be sent as a single chunk, not per-line paced.** `p.send("\n".join(lines)+"\n")`, not `p.sendline()` per line. Per-line pacing causes input race where `1` (menu select) gets consumed as PIN attempt, decrementing counter.

**B6. `gpg --card-edit > admin > factory-reset > y > yes > quit` doesn't need admin PIN.** The OpenPGP card spec's TERMINATE + ACTIVATE commands don't require it. Useful: lets us factory-reset cards without knowing the admin PIN (e.g., recovering from a partially-completed prior run).

**B7. `head -2` + `script -qc` hangs if grep produces fewer than 2 lines.** Replaced with `gpg_card_cmd` writing full output to file, then `cat`. No truncation.

**B8. After `addkey`, scdaemon enters a stuck state where `gpg --edit-key keytocard` spawns `selecting card failed: No such device`.** Always `revive_pcscd()` between addkey and keytocard.

### Category C — User PIN / Admin PIN mechanics

**C1. Phase 0 §0.3 can silently fail to rotate User PIN.** Symptom: test-sign with values.txt's User PIN gets "Bad PIN" + no card touch (PIN auth fails before UIF state). **Fix:** defensive User PIN reset via Admin PIN unblock (`gpg --card-edit > admin > passwd > 2 > admin_pin > new_user_pin > new_user_pin > q > quit`). Idempotent. Admin PIN auth is verified correct by virtue of having just succeeded for keytocard + UIF, so the unblock is reliable.

**C2. URL canonical update happens AFTER UIF set.** Phase 0 §0.4 sets a placeholder URL (`https://keys.openpgp.org/vks/v1/by-fingerprint/PLACEHOLDER_REPLACE_POST_C3`). Stage 7 (post-keytocard) overwrites with the canonical `…by-fingerprint/{master_fp}` form. Doing it later means the placeholder catches "what if Phase 0 ran but Stage 4 didn't" half-finished states.

### Category D — Parser bugs (the recurring class)

**D1. `ssb#:` prefix does NOT mark stubs in modern gpg.** Both stubs and disk-resident subs use `ssb:`. The distinguishing field is field 14 of `--with-colons` output (card AID for stubs, empty for disk-resident). Multiple parsers in `ceremony.py` and `validate.py` had this bug latent.

**D2. Non-colon `gpg --list-secret-keys` output is fragile across versions.** "card-no:" vs "Card serial no. =" vs `ssb>` prefix — combinations vary by gpg version and command flags (`--keyid-format LONG` shifts the layout). **Rule:** never substring-match against non-colon output. Always use `--with-colons` + field-index parsing.

**D3. Spaces in the on-card serial display break naive substring matches.** Default `gpg --list-secret-keys` formats card serials as `B975 3481 0000` (with spaces); a substring search for `B97534810000` returns False. The Day-5 cascade was triggered by exactly this — `skip_keytocard` never fired because the substring match was always False, so `addkey` ran on every NK on resume, accumulating disk-resident orphans that would have published as dead keys in Stage 8.

**D4. The `find_disk_resident_sign_subkeys` parser was disabled mid-ceremony for the same reason.** It was deleting legitimate stubs as orphans on Day 3 because of D1/D2. **Rule:** if a parser deletes data, validate its classifier on a known-good sample first, twice, with both reads agreeing. Never trust a single read of a card-status output to authorize a destructive op.

### Category E — Atomicity / failure recovery

**E1. addkey + keytocard is not atomic by default.** If addkey succeeds and keytocard fails, the disk-resident sub becomes an "orphan" (no card-no annotation; never moved to a card). Re-running --from-stage 7 would addkey AGAIN, accumulating one orphan per retry. **Fix:** in `stage_keytocard_one`, capture `new_sub_idx = sub_count_after` after addkey; on keytocard failure, `delkey` that exact index before fataling. Verify sub count drops back to `sub_count_before`. Atomic per-NK guarantee — keyring stays clean across N retries.

**E2. keytocard `replace-y` on an occupied slot leaves a "dead reference" stub.** The original on-card key gets overwritten by the new keypair, but the original stub's `card-no` annotation still references this card's serial. Two stubs end up annotated with the same card serial, but only one matches the on-card keyid. **Fix:** `cleanup_dead_card_refs(card_num, master_fp, master_pass, env)` runs at the start of each per-NK iteration. Reads on-card sig keyid via `gpg --card-status`, walks `--with-colons` output for stubs annotated with this card's serial, deletes those whose keyid doesn't match. Idempotent; defensive double-read prevents acting on partial card-status reads.

**E3. Stage 8 `gpg --armor --export` is unfiltered.** Any sub on the master keyring — including orphans and dead refs — gets published. Ergo: the keyring MUST be in correct state (1 enc + N live stubs, no orphans, no dead refs) before Stage 8 fires. The earlier the validation, the better. v3 should validate keyring shape before Stage 8 unconditionally.

### Category F — Bootloader/firmware mode

**F1. Nitrokey 3 in firmware-bootloader mode shows USB ID `20a0:42dd` instead of `20a0:42b2`.** No smartcard interface is present. **Fix:** `assert_not_bootloader()` early in each per-NK iteration; if in bootloader mode, instruct unplug + 30s wait + replug.

### Category G — Drive / log persistence

**G1. Trace logs default to `CEREMONY = ~/ceremony/` which is Tails RAM.** They vanish on power-off. Owner had to remember to `cp /home/amnesia/ceremony/trace-*.log /media/amnesia/OFFLINEDEBS/` before reboot, every time. **Fix:** in v3, `TRACE_LOG_PATH` should default to `DRIVE2 / "trace-{ts}.log"` (USB-persisted) so we don't lose logs on power-off. Same for scdaemon-ceremony.log + gpg-agent-ceremony.log — log-file paths in the configs should target the USB.

### Category H — Idempotency

**H1. Stage 9 PIV cert generation rotates the management key.** Re-running with the factory mgmt key would fail (mgmt key auth rejected) AND would overwrite the existing valid cert. **Fix:** Stage 9 idempotency check — skip if `intergenos-vendor-cert.pem` already exists on Drive #3.

**H2. Skip-keytocard detection.** If a card already has a sig key AND the master keyring has a stub annotated with this card's serial, skip addkey + keytocard, just re-run the idempotent UIF/URL/PIN-reset/test-sign tail. Required for resume mode to not duplicate work.

---

## 4. Architectural lessons for v3

The current ceremony.py grew organically over five days. The next refactor should bake these in from line 1:

### 4.1 Single execution path

Resume mode (`--from-stage N`) and full run should not be separate code paths with subtle drift. Resume should just "skip stages 1..N-1" and enter the same per-stage code as a full run. Today's resume path has half-rebuilt context (e.g., `pins[n]["serial"] = "?"` placeholder because Phase 0 didn't run) which then forces special-case logic in stages 4–10.

**Fix:** a `CeremonyState` dataclass that gets populated either by Phase 0 (fresh run) or by `load_values()` + keyring read (resume). All stages take the same dataclass.

### 4.2 One pre-op preamble per card touch

Today, each card-touching function has its own `revive_pcscd` + `assert_nk_present` + `assert_not_bootloader` logic, sometimes inline, sometimes not at all. Result: bugs like "Stage 7 missed `assert_nk_present()`" that took an hour to track down.

**Fix:** a single `card_op_preamble(card_num)` helper that does:
1. `revive_pcscd()` (kill scdaemon + restart pcscd + poll for reader enumeration, NOT blind sleep)
2. `assert_nk_present()` (lsusb)
3. `assert_not_bootloader()` (lsusb pattern check)
4. `wait_for_card_ready()` (poll `gpg --card-status` until valid, with timeout)
5. Double-read `gpg --card-status`, require agreement before returning the dict

Every card-touching function calls `card_op_preamble(N)` first. No exceptions. Removes the "did Stage 7 do everything Phase 0 did" failure mode.

### 4.3 Atomic per-NK sub creation

Wrap the addkey + keytocard pair in a context manager that auto-undoes on failure:

```python
with new_sign_subkey(master_fp, master_pass, env) as new_sub_idx:
    keytocard_to_card(new_sub_idx, card_num, master_pass, admin_pin, env)
    # if keytocard raises, the context manager runs delkey(new_sub_idx)
    # and the exception propagates
```

Same primitives as today's Option-2 atomic-undo, but baked into the API so it's impossible to forget.

### 4.4 Validation gates after every state-changing op

Today, some operations have post-validation (keytocard checks Signature key on card), others don't (UIF set isn't validated; PIN reset is validated by counter). Bugs hide in the unvalidated steps.

**Rule:** every state-changing card op has a corresponding read-back assertion. UIF set → read UIF state, confirm. URL set → read URL, confirm. PIN reset → counter-3 check. keytocard → on-card sig keyid matches just-added sub keyid (NOT just "not [none]" — see E1).

### 4.5 All parsers use `--with-colons`

Hard rule. Every gpg-output parser uses `--with-colons` + field-index access. No substring matching against human-readable output anywhere. The Day-5 cascade was D3-class; v3 should make D3-class bugs impossible by construction.

### 4.6 Trace persistence to USB

`TRACE_LOG_PATH = DRIVE2 / f"trace-{ts}.log"`. scdaemon and gpg-agent log-file paths in the ceremony's GNUPGHOME configs target Drive #2 too. Operators don't have to remember `cp` before reboot.

### 4.7 Pubkey export filters by card-binding

Stage 8 exports only the master + encryption sub + signing subs that have a `card-no` annotation matching one of the four expected NK serials. Rejects any orphan or dead ref by construction. Failsafe even if upstream stages somehow leak a bad sub.

(Implementation: `gpg --export-filter keep-uid='@`-pattern' --export-filter drop-subkey='…'` plus a programmatic round-trip — gpg's filter language is awkward for "keep only subs with these card-no annotations" and we may end up exporting then post-processing the armored output.)

### 4.8 "No manual fallback" baked into messaging

Today's ceremony.py has `ask_enter()` calls scattered for confirmations. v3 should treat human-typed commands as a hard failure mode: the only owner inputs allowed are (a) plug/unplug confirmations, (b) `Press Enter to begin`, and (c) **physical card touches** when the card requests UIF. Anything else is a bug.

### 4.9 Validation script alongside the ceremony

`validate.py` was an afterthought today; in v3 it should be a first-class artifact, with tight contract: validate.py output of "0 failures" is the ceremony's ship gate. No "trust the script's `ok()` lines" — the validator independently re-reads the keyring + cards + drives and asserts the invariants. The validator's parser is the canonical parser; ceremony.py uses the same library.

### 4.10 Trace blocks include input AND output

Today's `trace_block` captures gpg subprocess output. v3 should also capture the BLAST input (canonicalized — secrets redacted) so when a future failure happens, the trace is replayable as a diff: `here's what we sent, here's what gpg gave us, here's where they disagreed`. Owner had to read PTY output AND scdaemon log AND gpg-agent log together to diagnose; v3 trace should be self-contained.

---

## 5. Process lessons

### 5.1 Iteration limits

Eighteen fixes accreted across 5 days. The script worked at the end, but each fix added complexity in slightly different ways and the architectural cleanliness eroded. **Rule:** when you're past ~10 fixes on a single script in a single push, stop adding fixes. Refactor from known-good inputs/outputs. Owner's exact words: *"we just iterated too much and the code got sloppy because of it."*

### 5.2 Peer review caught real issues

DS reviewed the in-flight `cleanup_dead_card_refs` function and flagged the transient-card-status-read failure mode that would have made the cleanup delete legitimate stubs. That's a Category E2 disaster avoided by a 200-word peer message. The defensive double-read guards (Section 4.2 above) came out of that review.

**Rule:** for ceremony-class scripts (any code that authoritatively destroys data), get a peer review before the first run, not after the first failure.

### 5.3 Don't edit working scripts mid-operation

Owner ratified this as a power rule (`feedback_no_edits_mid_operation.md`) after I offered to add log-persistence-to-USB to ceremony.py during a paused session. The paused script was working; adding "small improvements" mid-flight is exactly when you introduce regressions in critical-path code. Edits go to the post-op backlog.

### 5.4 Manual fallback is never an option

Owner ratified this as a power rule (`feedback_no_manual_ceremony_steps.md`) after I suggested manual `gpg --card-edit` as a contingency. The fat-finger problem is what the script exists to prevent — a 16-PIN multi-card ceremony with manual typing has a ~100% latent failure rate. Contingencies for ceremony failures must always be: patch + retry, refactor from known-good, or narrower automated wrapper. Never "owner runs each step by hand."

### 5.5 Validate the validator

`validate.py` had the same Category-D parser bug as `find_disk_resident_sign_subkeys` (used `card-no:` substring against non-colon output that actually formats stubs differently across gpg versions). It reported 0-stubs / 4-orphans when the actual keyring had 4-stubs / 0-orphans. The trace evidence saved us — the POST-KEYTOCARD card-status block in the ceremony trace showed the correct stub structure, so we knew validate.py was lying.

**Rule:** if the validator gives a catastrophic-sounding result, cross-check against a different artifact (the ceremony trace, the on-card state, an independent parser) before acting on it.

### 5.6 State-of-truth before external review

When cycling reviewers (DS, Gemini, GP, IGOSC, peer Claudes), give them a Verified-Real / Claimed / Pending state document at the start of the request. Stripped framing: "here's what we know is real, here's what we think but haven't proven, here's what's open." Keeps reviewer attention on the actual question instead of re-deriving context.

---

## 6. ceremony.py v3 backlog

In approximate priority order. Implement on the next subkey rotation (2028-05-04) or sooner if rotation comes earlier (compromise, hardware loss).

1. **Single execution path.** Pass-through `CeremonyState` dataclass; resume just enters at stage N with state pre-populated. (§4.1)
2. **`card_op_preamble()` helper.** revive_pcscd + assert_nk_present + assert_not_bootloader + wait_for_card_ready + double-read confirm. Used by EVERY card-touching function. (§4.2)
3. **Atomic per-NK context manager.** `with new_sign_subkey(...) as idx:` auto-undoes addkey on exception. (§4.3)
4. **Read-back validation after every state change.** UIF set → read UIF, URL set → read URL, keytocard → on-card keyid matches new sub keyid. (§4.4)
5. **All parsers use `--with-colons` + field index.** No substring matching against human-readable gpg output anywhere. (§4.5)
6. **Trace persistence to Drive #2.** `TRACE_LOG_PATH` defaults to USB; scdaemon.conf + gpg-agent.conf log-file paths target USB. (§4.6)
7. **Stage 8 export filter.** Only export master + enc + sign-subs annotated with one of the 4 expected card serials. Reject orphans/dead refs by construction. (§4.7)
8. **`validate.py` as ship gate.** Validator and ceremony share parsing library. Validator output of "0 failures" is the only signal cleared for shipping. (§4.9)
9. **Trace blocks include canonical input.** Replayable diff format. Secrets redacted. (§4.10)
10. **Drop the disabled cleanup functions.** `cleanup_disk_resident_sign_subkeys` and `cleanup_orphan_sign_subkeys` are defined but no longer called. v3 doesn't need them — the atomic per-NK context manager prevents orphan creation in the first place. Delete the dead code.
11. **Owner inputs limited to plug/unplug/touch + Press Enter.** Anything else is a bug. (§4.8)
12. **Pre-flight runs validate.py against an empty keyring + factory cards.** Fail fast if any environment assumption is wrong before the master keypair gets generated.

---

## 7. Things to keep

Not everything needs rewriting. These worked first-time-correct and should survive into v3 unchanged:

- **`piv-reset.sh`** — opensc-tool APDU sequence (3× wrong PIN → 0xFB reset). Bulletproof; works regardless of mgmt key state.
- **`diag-c6.sh`** — standalone PIV diagnostic. Read-only, full instrumentation.
- **bootstrap.sh's offline-debs install pattern** — `dpkg -i`, then verify pexpect importable, then exec ceremony.py. (Note: the bootstrap.sh on the shipped Drive #2 was removed because its `exec ceremony.py` was a footgun for Ethan, but the pattern is sound for owner-side use.)
- **The `_blast()` pexpect primitive** — single-chunk send, captured PTY output + scdaemon log delta, label-tagged for trace. Works for every BLAST in the script.
- **The factory-reset-everything-at-Phase-0-§0.1 design** — recovers from any prior partial state without owner intervention. v3 should keep this, just with cleaner state-modeling.
- **Drive #3 = LUKS master-secret backup.** Three-drive layout (boot, scripts, output) is right. Don't combine.
- **base16 paperkey vs raw.** Default `paperkey --output-type raw` is binary unprintable; `--output-type base16` is hex with line numbers + checksums — actually transcribable on paper.

---

## 8. Pointers for the future maintainer

- The carryover documents in `~/.claude/projects/-mnt-intergenos/memory/context_carryover_*.md` have full-fidelity per-session details. Specifically:
  - `context_carryover_20260505_ceremony_stage7_recovery.md` — Day-3 → Day-5 detailed trace, the substring-match bug, the dead-ref cleanup, validate.py parser fix.
  - Earlier carryovers cover Days 1–4.
- The runbook `~/intergenos/research/ceremony/runbook_v2_validated_2026-05-04.md` (1668 lines) was the v2.3 plan that this ceremony executed against. Keep it as the v3 starting structure.
- The script source as it stood at 0-failures is in two places: the home-drive canonical at `~/intergenos/research/ceremony/c6-offlinedebs/scripts/ceremony.py`, and the Drive #2 copy that got shipped to Ethan. The home-drive copy is authoritative.
- All paper records (master FP, master pass, LUKS pass, 16 PINs, vendor cert SHA, new PIV mgmt key) are owner-only. The succession plan (`docs/governance/succession.md`) covers handoff in the loss-of-owner case.
- `keys.openpgp.org` publication is **irreversible** in any practical sense. The 30-day overlap window during rollover is non-negotiable for end-user `pkm update` clients.

Good luck. This is a real artifact now. Don't take it lightly.
