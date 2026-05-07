# Install-Time Integrity Verification — Design Doc

**Date:** 2026-05-07 | **Status:** APPROVED — project lead ratified 2026-05-07 (all 5 §10 questions answered, locked in)
**Filters applied:** anti-supply-chain security objective ✓ | Prime Directive (user controls) ✓
**Estimated v1.0 scope:** ~10 hours implementation + audit pass for un-pinned YAMLs

---

## 1. Problem

Mythos-class adversaries can compromise the supply chain at superhuman scale. Yesterday's Shai-Hulud SAP NPM worm IR was a current-events reminder. We assume:

- Upstream source tarballs may be poisoned between when WE pinned a SHA and when a user re-pulls them
- Our own `.igos.tar.gz` archives may be tampered between build and install (compromised mirror, MITM on USB-write, malicious install media)
- A user installing InterGenOS today should be able to **verify cryptographically** that what they're about to put on their disk matches what we built

No major distro does halt-on-mismatch *during* install. `rpm -V` and `pacman -Qkk` are post-install audit tools; NixOS sidesteps via content-addressed storage. That gap is a feature for us — supply-chain compromise is exactly what this OS is built to defend against.

## 2. Decision (owner-ratified 2026-05-07 ~04:50 CDT)

**Soft-warn-with-explicit-typed-override + per-mismatch acknowledgment + tamper-visible audit log.**

Per Prime Directive: hard-reject would push power users *out* of our verification model (comment out the check, custom-build the installer). Soft-warn keeps them inside the model where their override is recorded. Legitimate cases — testing a patched package, intentional local rebuild, custom variant — must stay non-painful.

## 3. Architecture overview

```
BUILD-TIME                              INSTALL-TIME
─────────────────────                   ─────────────────────────────
1. Each package YAML pins               1. Forge orchestrator's NEW
   upstream-source sha256                  PHASE_VERIFY runs FIRST
   (gate in build-intergenos.sh)           (before partition/mount/etc)

2. After all .igos.tar.gz               2. Reads embedded signed manifest
   archives are built, generate            from install media
   intergenos-archive-manifest.txt
   (sha256 of every .igos.tar.gz)       3. SHA-256 every .igos.tar.gz
                                            in archive_dir, compare to
3. Sign manifest with release key           manifest entry

4. Embed signed manifest +              4. On mismatch:
   release-key public part in              a. Display hard-coded warning
   install media (Pattern B Live-USB)      b. Require typed-phrase ack
                                              per mismatched package
                                           c. Append signed entry to
                                              hash-chained audit log
                                           d. Continue OR abort

5. (Future) Surface override                5. After all mismatches
   summary in install-complete                handled, proceed to
   screen if any occurred                     existing PHASE_PARTITION
```

## 4. Manifest format

Filename: `intergenos-archive-manifest.txt`
Format: BSD-style sha256sum + signature footer:

```
# InterGenOS archive integrity manifest
# Build: v1.0-dev1
# Built: 2026-07-15T14:30:00Z
# Built-on: igos-build (Ubuntu 24.04.2)
# Manifest-version: 1
SHA256 (toolchain/glibc-2.40-1.igos.tar.gz) = a1b2c3...
SHA256 (toolchain/binutils-2.43-1.igos.tar.gz) = d4e5f6...
[... 688 entries total ...]
SHA256 (extra/code-helper-1.96-1.igos.tar.gz) = z9y8x7...
# End of manifest.

-----BEGIN PGP SIGNATURE-----
[... signature by master key + countersigned by [S1] release key ...]
-----END PGP SIGNATURE-----
```

**Why BSD-style not GNU-style sha256sum:**
- BSD `SHA256 (path) = hash` is unambiguous when paths contain spaces
- Existing `sha256sum -c` accepts both formats; ours opens the door to additional algos later (sha512, blake3) without format change

**Why two signatures (master + S1):**
- Master signs the *release identity*; S1 signs *this specific build*. Compromise of either alone doesn't validate.
- Same chain we already use for signing kernel/grub artifacts (per `docs/signing-procedure.md`).

**Why embedded in install media, not fetched at install time:**
- Live-USB ceremony per Pattern B Decision 1 = no network requirement
- Manifest fetch over network would itself need a trust anchor → infinite regress
- Manifest is produced before install media is signed; same signing ceremony covers both

## 5. Build-time gate (`scripts/build-intergenos.sh` integration)

### 5.0 Schema decision — `source:` vs `build_artifacts:` (resolved 2026-05-07 ~05:50 CDT)

Audit-pass found 5/720 entries with placeholder SHAs (`placeholder-vendor`, `placeholder-lock`) on `local://` URLs in 4 Rust packages (cargo-c, cbindgen, librsvg, rust-bindgen). These are locally-generated cargo vendor tarballs + Cargo.lock files — NOT upstream sources, so they don't fit the "pinned-by-author" semantics of `source:`.

**Schema extension:** introduce `build_artifacts:` as a new top-level key parallel to `source:`.

```yaml
source:                                   # MUST be upstream-fetchable; mandatory sha256
- url: https://github.com/.../cargo-c-${version}.tar.gz
  sha256: 9bdf7c10b44466a7c01dc4ed152da5031793cca9e0c8009d73223a32522cf2c3

build_artifacts:                          # locally-generated; SHA computed at build-time
- name: cargo-c-${version}-vendor.tar.xz
  generated_by: cargo-vendor              # hint to verify-sources / build.sh
- name: cargo-c-${version}-Cargo.lock
  generated_by: cargo-vendor
```

**Why two keys, not one with a discriminator:**
- `source:` must always be pinned by the YAML author; gate is unconditional. Clean audit story.
- `build_artifacts:` SHA is unknowable until build time; gate happens at the *manifest* phase, not `verify-sources`.
- New contributors can read the YAML and immediately understand which entries they're responsible for pinning.
- Avoids `placeholder-*` literals committed to git, which look like un-pinned sources but aren't.

**Migration:** 4 YAMLs change shape; build.sh in those packages may reference vendor.tar.xz path via `build_artifacts` lookup instead of `source[N]`. Migration lands as part of Step 3 (`verify-sources` build phase).

### 5.1 New phase: `verify-sources`
Runs early (after `validate`, before `setup`). For every package YAML in `packages/<tier>/<name>/package.yml`:
- Parse YAML for `source:` list (only)
- If any `source:` list entry is missing `sha256` → **HARD FAIL** the build with list of un-pinned YAMLs
- For each pinned source: download (or use cached), compute sha256, compare
- On mismatch: **HARD FAIL** with package name + expected vs actual (no override at build time — fix the YAML or the source)
- `build_artifacts:` entries are **NOT** processed here — they're handled by the `manifest` phase after build produces them.

### 5.2 New phase: `manifest`
Runs after `image` (the existing terminal phase that produces the install ISO). For every `.igos.tar.gz` produced during the build (regardless of whether its source was `source:` or `build_artifacts:`):
- Compute sha256
- Append to `intergenos-archive-manifest.txt` in BSD format
- Sign manifest with release key (master + S1)
- Place signed manifest into the ISO at `/install/intergenos-archive-manifest.txt`
- Place the release-key public component at `/install/intergenos-release-key.asc` (also signed by master, so it's self-validating against the master fingerprint published at `docs/signing-key.md`)

The `manifest` phase produces the unified install-time integrity surface — every archive is in the signed manifest, regardless of how its source was acquired. Install-time `PHASE_VERIFY` consumes this manifest only; it does not need to know the difference between `source:` and `build_artifacts:`.

## 6. Install-time verify phase

### 6.1 New phase: `PHASE_VERIFY` in `installer/backend/install.py`

Insertion point: BEFORE `PHASE_PARTITION`. Updated `PHASE_ORDER`:
```python
PHASE_ORDER = [
    PHASE_VALIDATE,
    PHASE_VERIFY,        # ← NEW
    PHASE_PARTITION,
    # ... rest unchanged
]
```

Rationale for placement: nothing has been written to disk yet at this point. If verify fails and user aborts, the target system is untouched.

### 6.2 New module: `installer/backend/integrity.py`

Public API:
```python
def verify_archives(archive_dir, manifest_path, public_key_path,
                   warning_callback, ack_callback, audit_log_path) -> VerifyResult
```

Behavior:
1. Load `manifest_path`, verify GPG signature against `public_key_path`. On signature failure → **NON-OVERRIDABLE HARD FAIL** (signature failure = manifest itself is compromised; not a per-package override case).
2. Walk `archive_dir`, sha256 every `.igos.tar.gz` found.
3. Cross-reference against manifest entries. Any of three states per archive:
   - Match → silent pass
   - Missing-from-manifest → mismatch (treated as hash mismatch, overridable)
   - Hash mismatch → mismatch (overridable)
4. For each mismatch: invoke `warning_callback(name, expected, actual)`, then `ack_callback(name) → bool`. If `ack_callback` returns False → ABORT install. If True → write audit-log entry + continue.
5. Return `VerifyResult(success, mismatches_overridden, mismatches_aborted)`.

### 6.3 Hard-coded warning text

In `installer/backend/integrity.py` as a module-level constant (NOT in the manifest):

```python
INTEGRITY_WARNING_TEMPLATE = """
═════════════════════════════════════════════════════════════════════════
  ⚠  INTEGRITY MISMATCH DETECTED
═════════════════════════════════════════════════════════════════════════

  Package:   {package}
  Expected:  {expected_sha256}
  Found:     {actual_sha256}

  THIS IS WRONG.  The package archive on this install media does not
  match the cryptographically-signed manifest published with this
  InterGenOS release.  This means one of:

    • The install media was tampered with after we signed it.
    • The archive was corrupted in transit (USB write error, network).
    • You are intentionally running a custom-built or modified package.

  We STRONGLY recommend you DO NOT PROCEED.  Instead:

    1. Re-write the install media from a trusted source.
    2. Email security@intergenstudios.com with this warning text and
       a description of where you obtained the install media.
    3. Wait for guidance before retrying.

  Cross-check the release signing key fingerprint independently
  before trusting any artifact you download from us:

      Master:  5597 A3E0 587B 2530 06D0  DD7B 8C50 8261 8208 3050

  This fingerprint is published canonically at
  https://intergenstudios.com/signing-key  AND at the project's
  GitHub repository docs/signing-key.md.  Both copies must match.
  If they do not match, you are looking at a compromised source.

  However, this is YOUR machine.  If you intentionally created this
  mismatch (e.g. testing a patched package), you may proceed at your
  own risk by acknowledging this warning explicitly.

  This override will be recorded in:
      /var/log/igos-integrity-override.log

  To proceed despite this mismatch, type EXACTLY (case-sensitive):

      OVERRIDE_HASH_MISMATCH_{package_normalized}

  To abort the install, type anything else (or press Ctrl+C).

═════════════════════════════════════════════════════════════════════════
"""
```

`{package_normalized}` = the package name with non-alphanumerics replaced by underscores (so e.g. `gtk+3` becomes `gtk_3` in the override phrase). Avoids shell-interpretation issues during type-in.

### 6.4 Per-mismatch acknowledgment rule

Implemented in `ack_callback`. For each mismatch:
- Print warning text with `{package}` filled in
- `input()` to read user's typed response
- Compare with `f"OVERRIDE_HASH_MISMATCH_{normalize(package)}"` exactly (case-sensitive, whitespace-trimmed)
- Match → return True (override granted)
- Anything else → return False (abort)

**No bulk-override.** If 5 archives mismatch, user types 5 separate phrases. This is the dangerous case — 5 mismatches looks like full-manifest swap by an attacker — and we deliberately make it tedious. A user who legitimately swapped 5 archives knows what they're doing and can type 5 phrases.

**Re-ack on retry.** If an install fails mid-flight after some overrides were granted (e.g. partition phase fails, user retries from start), the next install attempt re-runs PHASE_VERIFY and requires the user to re-type each override phrase fresh. The audit log captures BOTH attempts so the chain shows the user re-asserted intent. State-of-truth is the explicit user act at *this* install attempt, not a remembered consent from a prior attempt. (Per Prime Directive: never assume consent from a prior session — always require fresh expression of user agency.)

### 6.5 Audit log format (`/var/log/igos-integrity-override.log`)

Hash-chained append-only journal. Format (one entry per line, JSON):

```jsonl
{"v":1,"prev":"GENESIS","ts":"2026-05-07T13:42:11Z","event":"verify_started","manifest_sha256":"...","entry_sha256":"a1b2..."}
{"v":1,"prev":"a1b2...","ts":"2026-05-07T13:42:14Z","event":"override","package":"gnome-shell","expected":"...","actual":"...","entry_sha256":"c3d4..."}
{"v":1,"prev":"c3d4...","ts":"2026-05-07T13:42:18Z","event":"override","package":"firefox","expected":"...","actual":"...","entry_sha256":"e5f6..."}
{"v":1,"prev":"e5f6...","ts":"2026-05-07T13:43:02Z","event":"verify_completed","overrides":2,"aborted":0,"entry_sha256":"7890..."}
```

**Hash chain:** `entry_sha256 = sha256(JSON-without-entry_sha256-field || prev)`. Silent deletion of any entry breaks the chain at the next entry → tamper-visible.

**Genesis entry:** when log doesn't exist, first entry has `prev: "GENESIS"`. Subsequent installs append their own `verify_started` chained off the previous install's terminal entry.

**Survives onto target:** during `PHASE_CLEANUP`, copy `/var/log/igos-integrity-override.log` from the install environment into `{target}/var/log/igos-integrity-override.log`. So the user has a record on their installed system of what they accepted.

### 6.5.1 GUI parity — typed-phrase entry must defeat copy-paste

The TUI uses a normal `input()` prompt; the user types the phrase character-by-character with no shortcut. The GUI must enforce the same property:

- Text-entry widget is a `Gtk.Entry` (single-line) explicitly configured with:
  - `Gtk.Entry.set_input_hints(NO_SPELLCHECK | NO_EMOJI)` to keep IME / autocomplete out of the path
  - **Paste disabled.** Override `paste-clipboard` signal handler to ignore the paste event. Right-click "Paste" menu item likewise suppressed via custom `populate-popup` handler.
  - `Gtk.Entry.set_input_purpose(FREE_FORM)` (avoid PASSWORD purpose — we want the typed phrase visible so the user reads what they're committing to)
- Submit button enabled only when the entered text matches the expected phrase exactly (live-validate on `changed` signal). This gives sighted users feedback without leaking which characters are wrong (the button just stays grey until full match).
- After submit, the audit-log entry records `entry_method: "gui_typed"` (vs `"tui_typed"` for the CLI path) — useful for forensics later if we discover a paste-disable bypass on some platform.

Rationale: the typed phrase is the gating affordance against scripted bypass. Allowing paste defeats the entire point — a script can shove the override phrase into the clipboard and trigger paste programmatically.

### 6.6 Install-complete summary surface

In TUI's `run_declarative` and GUI's progress-page-complete handler:
- If `result.integrity_overrides > 0`:
  - Print/show: *"⚠ {N} integrity overrides occurred during install. Review /var/log/igos-integrity-override.log for details."*
  - This appears on the same screen as "install complete" — user can't dismiss without seeing it.

## 7. Audit pass for un-pinned YAMLs

Before any of the above ships, we need to know what state the existing 688 package YAMLs are in. Audit pass:

```bash
# scripts/audit-yaml-source-pinning.sh
for yaml in packages/*/*/*.yaml; do
  if ! yq -e '.source.sha256' "$yaml" >/dev/null 2>&1; then
    echo "UNPINNED: $yaml"
  fi
done
```

Expected: most LFS-tier packages already pin (LFS book convention). Likely-unpinned: extra/ tier (some pull from git tags), some BLFS packages.

For each un-pinned YAML:
- Compute sha256 of the upstream source we currently use
- Add `source.sha256:` to the YAML
- Commit per-tier batches (don't bulk-commit 688 YAMLs in one shot — too noisy for review)

This work is parallelizable; can be split by tier across implementers.

## 8. Threat-model coverage

| Threat | Coverage |
|---|---|
| Compromised mirror serving tampered .igos.tar.gz | ✓ Manifest sha mismatch detected |
| MITM during USB-write of install media | ✓ Manifest sig fails (master key not on attacker's keychain) |
| Tampered manifest with valid-looking content | ✓ Signature verification on manifest before any sha-compare |
| Attacker replaces both manifest + key on install media | ✗ No coverage — Pattern B Live-USB ceremony assumes signed media is trustworthy at root. This is what shim/Secure Boot covers. |
| User accidentally proceeds without reading | Mitigated: typed-phrase forces them to read package name |
| Malicious script automates override | Mitigated: typed phrase includes per-package name; not a flag |
| Silent post-install log tampering | Mitigated: hash chain breaks visibly |
| Upstream source poisoning between our SHA pin and user's re-pull | ✓ Build-time gate fails the build |

**Not covered (intentionally, separate lane):**
- Runtime tampering of installed files (covered by `pkm verify` periodic / boot-time check — separate v1.x feature)
- Boot-time integrity (covered by Secure Boot + signed kernel/grub — already in scope)
- Build-environment compromise (covered by reproducible-builds work — v1.x lane)

## 9. Test plan

### 9.1 Unit tests (`installer/tests/test_integrity.py`)
- Manifest signature validation: valid sig passes, bad sig hard-fails (no override path)
- SHA-mismatch detection: synthetic mismatched archive triggers warning_callback
- Acknowledgment phrase: correct phrase grants override; any other input aborts
- Per-mismatch isolation: 3 mismatches require 3 separate acks; one wrong abort all
- Audit log: hash chain holds across multiple entries; deleted middle entry breaks chain
- Genesis entry: log creation when absent
- Target log copy: cleanup phase places log at target's /var/log

### 9.2 Integration tests
- Full install dry-run with tampered archive → orchestrator halts at PHASE_VERIFY
- Full install dry-run with valid archives → orchestrator proceeds normally
- Full install dry-run with override path exercised → install completes; audit log present on target

### 9.3 Build-time tests
- `audit-yaml-source-pinning.sh` finds known-unpinned fixture YAML
- `verify-sources` phase fails build when SHA mismatch in test fixture
- `manifest` phase produces valid signed manifest with all archive entries

## 10. Open questions — RESOLVED 2026-05-07 ~05:00 CDT

1. **Email destination:** `security@intergenstudios.com` — **CONFIRMED EXISTS + preferred listing.** Use it as-is in the warning text and user-facing docs. (Owner-direct.)
2. **Master fingerprint in warning text:** **YES.** Hard-coded into `INTEGRITY_WARNING_TEMPLATE` along with a pointer to canonical publication locations (`intergenstudios.com/signing-key` + `docs/signing-key.md` in repo). Both must match — gives the user an independent cross-check that doesn't depend on any single channel.
3. **Re-ack on retry:** **YES.** Each install attempt re-runs PHASE_VERIFY and requires fresh typed-phrase per mismatch. Audit log captures every attempt so the chain shows re-asserted intent. State-of-truth is the user act at *this* attempt — never assume consent from a prior session.
4. **GUI parity (paste-disable):** **YES.** GUI uses `Gtk.Entry` with paste-clipboard signal suppressed + right-click paste menu item suppressed + live-validation submit button. Audit log records `entry_method: gui_typed | tui_typed` for later forensics. (Coordinated with the Phase 6 GUI Path A scope; lands as a follow-on after Phase 6 ships, not inline.)
5. **v1.0 ship gate or v1.x:** **v1.0 SHIP GATE.** Anti-supply-chain integrity is a top-priority security objective and trumps schedule. Land all 7 implementation steps from §12 before bootable-ISO smoke test. Adds ~10 hr of implementation effort over the week.

## 11. Estimated implementation effort

Assuming Path A (incremental, parallel with the Phase 6 GUI work):

| Component | Effort |
|---|---|
| `installer/backend/integrity.py` (new) | 2 hr |
| `installer/tests/test_integrity.py` (new) | 1.5 hr |
| Wire `PHASE_VERIFY` into `install.py` orchestrator | 30 min |
| TUI hook (warning + typed-phrase prompt + audit-log display) | 45 min |
| GUI hook (coordinate with Phase 6 GUI work; same callback contract) | 45 min |
| `scripts/audit-yaml-source-pinning.sh` + audit pass results | 30 min |
| `verify-sources` build phase | 1 hr |
| `manifest` build phase + signing-procedure update | 1.5 hr |
| Hard-coded warning text + UX review | 30 min |
| Documentation: `docs/integrity-verification.md` (user-facing) | 1 hr |
| Pin-up un-pinned YAMLs (variable; depends on audit-pass result count) | 2-8 hr |
| **Subtotal (excluding YAML pin-up)** | **~10 hr** |

YAML pin-up can run as parallel work — per-tier chunks dispatched to multiple implementers once audit-pass output is in hand.

## 12. Recommendation

**Greenlight v1.0 inclusion. Land in this order:**

1. Audit pass (find un-pinned YAMLs) — owner-approved go-ahead, ~30 min
2. `installer/backend/integrity.py` + tests — fundamental primitive
3. Build-time `verify-sources` phase + YAML pin-up — parallel work, multiple implementers
4. `manifest` build phase + sign integration — gates release artifact production
5. Wire `PHASE_VERIFY` into orchestrator — ties install path to manifest
6. TUI + GUI hooks — coordinate with the Phase 6 GUI scope
7. End-to-end test on build VM (the smoke test the project lead is currently holding)

Steps 2-5 can land before the Phase 6 GUI ships — they're independent file surfaces. Step 6 lands once the Phase 6 GUI is on master.

**Net impact on installer timeline:** none. This is parallel work to the Phase 6 GUI effort. Adds ~10 hr of implementation effort over the next week before bootable-ISO smoke test.

---

**END OF DOC**
