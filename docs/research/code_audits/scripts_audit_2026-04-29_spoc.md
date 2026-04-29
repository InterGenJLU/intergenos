---
title: scripts/ code audit — SPOC
author: chris-ubuntu-code-claude (SPOC)
date: 2026-04-29
audit_scope: scripts/build-intergenos.sh, scripts/create-image.sh, scripts/pkg-functions.sh (sampled)
audit_complement: DeepSeek pkm + igos-build (1b3be7e); Gemini-Pro intergen + installer + docs
status: DRAFT — pre-shim-review-PR window
---

# scripts/ code audit — SPOC

Targets the build-host shell scripts not covered by DeepSeek's pkm + igos-build audit (`docs/research/code_audits/pkm_igos_build_audit_2026-04-29.md`) or Gemini-Pro's intergen + installer + docs audit. Total `scripts/` surface: 11543 LOC across 31 files. This first pass samples the highest-risk surfaces: master orchestrator + image-creation + shared package-functions library. `scripts/sign-release.sh` is in DeepSeek's 3rd-batch lane and is excluded here.

Findings ordered by severity. Same RFC-discipline shape as DeepSeek's audit (problem statement / Evidence / Severity / Proposed fix).

---

## CRITICAL

### S1. Default root password is "intergenos" — `scripts/create-image.sh:380-384`

**Problem.** Disk images created without `ROOT_PASSWORD` env var override ship with a known root password literal `intergenos`. The script logs a warning when the default is used, but the image is still produced and bootable.

**Evidence.**

```bash
# create-image.sh:380-384
IMAGE_ROOT_PASSWORD="${ROOT_PASSWORD:-intergenos}"
if [ "$IMAGE_ROOT_PASSWORD" = "intergenos" ]; then
    log "  WARNING: Using default root password — set ROOT_PASSWORD env var for production"
fi
echo "root:${IMAGE_ROOT_PASSWORD}" | chroot "$MOUNT_POINT" chpasswd
```

Per Holy Grail security-only alignment: any image built without explicit `ROOT_PASSWORD=` is a known-credentials image. If a build host's shell history is captured, or the build VM image is shared (including for shim-review reproducibility verification), the password is recoverable trivially. Reviewers running our build to verify reproducibility will land on a system with credentialed root.

**Severity: CRITICAL.** Holy Grail violation — known credentials in any shipped artifact.

**Proposed fix (any of these, owner-decision):**

1. **Generate a random password per build, print to stdout** with a clear "save this — first-boot only" banner. Forge installer's first-boot flow then immediately requires a password change.
2. **Lock root account entirely** (`passwd -l root` post-chpasswd). Rely on the user account in the wheel group with sudo. Aligned with most modern desktop distros.
3. **Force first-boot password change via dedicated greeter pre-GDM** (the in-line comment notes GDM cannot handle `passwd --expire`; a Plymouth-or-tty-stage greeter could).

Path 2 is the simplest match for v1.0 (no user-facing changes; standard distro hygiene). Path 1 is the most reviewer-defensible. Path 3 is the most user-friendly but requires net-new code.

---

### S2. Default user password "intergenos" — `scripts/create-image.sh:390-394`

**Problem.** Same fail-shape as S1 but for the default user account `intergenos`. Image with `IMAGE_USER_PASSWORD` not overridden ships with the literal `intergenos` as the user password. The user is in the `wheel` group with sudo, so a known user password is functionally equivalent to known root credentials.

**Evidence.**

```bash
# create-image.sh:390-394
IMAGE_USER="${IMAGE_USER:-intergenos}"
IMAGE_USER_PASSWORD="${IMAGE_USER_PASSWORD:-intergenos}"
if ! chroot "$MOUNT_POINT" id "$IMAGE_USER" > /dev/null 2>&1; then
    chroot "$MOUNT_POINT" useradd -m -G wheel,video,audio,input -s /bin/bash "$IMAGE_USER"
    echo "${IMAGE_USER}:${IMAGE_USER_PASSWORD}" | chroot "$MOUNT_POINT" chpasswd
```

No log warning at all on the default-user path (compare S1 which at least warns). User is sudo-capable via wheel group.

**Severity: CRITICAL.** Same Holy Grail violation as S1; arguably worse because no warning is emitted.

**Proposed fix.** Identical paths as S1. Whichever path is chosen for root, apply consistently to the default user. If the image ships with no default user (Forge installer creates one at install time), this entire block can disappear — preferred for v1.0 since Forge already has the user-creation flow.

---

## HIGH

### S3. `verify_source_checksum` fails open on empty/placeholder checksums — `scripts/pkg-functions.sh:33-54`

**Problem.** Same fail-open shape that DeepSeek's audit flagged at `pkm/repo.py:327` (H1). Empty checksum, the placeholder string `NEEDS_CHECKSUM`, or any falsy value causes verification to be silently skipped with only a warning log. Build proceeds.

**Evidence.**

```bash
# pkg-functions.sh:33-54
verify_source_checksum() {
    local file="$1"
    local expected="$2"

    if [ -z "$expected" ] || [ "$expected" = "NEEDS_CHECKSUM" ]; then
        echo "[pkg] WARNING: No checksum for $(basename "$file") — skipping verification"
        return 0
    fi
    ...
}
```

Combined with `get_package_sha256` at line 59-62 which silently returns empty string when no sha256 is in the yaml file, a package author can disable checksum verification per-package by omitting the sha256 field. No build-time enforcement.

**Severity: HIGH.** Same shape as the DeepSeek H1 finding in pkm. Build-host trust delegates to package YAMLs; YAMLs without checksums are accepted. A compromised mirror could deliver tampered tarballs that pass verification simply because the corresponding package YAML has no checksum.

**Proposed fix.** Strict-type+length check: must be exactly 64 hex chars; reject empty / `NEEDS_CHECKSUM` / wrong-length / non-hex. Fail loudly. Build halts until owner provides a real checksum or explicitly bypasses with a documented `pkg_unsafe_no_checksum=true` flag in package.yml (which CI then treats as a finding, not a pass).

```bash
verify_source_checksum() {
    local file="$1"
    local expected="$2"

    if [[ ! "$expected" =~ ^[a-f0-9]{64}$ ]]; then
        echo "[pkg] ERROR: $(basename "$file") has no valid sha256 (got: ${expected:0:32}...)"
        return 1
    fi

    local actual
    actual=$(sha256sum "$file" | cut -d' ' -f1)
    ...
}
```

---

### S4. install-theming.sh chroot invocation references a path not in the chrooted view — `scripts/create-image.sh:553`

**Problem.** Build-time bug masquerading as a security issue. After `phase_image` removes `${IGOS}/mnt/intergenos`, the chroot tree no longer contains `/mnt/intergenos/scripts/install-theming.sh`. `create-image.sh` at line 553 then invokes `chroot "$MOUNT_POINT" /bin/bash /mnt/intergenos/scripts/install-theming.sh` against the new image, which doesn't have that path either (tar copy of the chroot inherited the absence).

**Evidence.**

```bash
# build-intergenos.sh:540 (phase_image, runs BEFORE create-image.sh)
rm -rf "${IGOS}/mnt/intergenos"

# create-image.sh:545-553
if [ -d "/mnt/intergenos/assets/theming" ]; then
    log "Installing theming assets..."
    # Re-mount bind mounts for chroot execution
    mount --bind /dev "${MOUNT_POINT}/dev"
    mount --bind /dev/pts "${MOUNT_POINT}/dev/pts"
    mount -t proc proc "${MOUNT_POINT}/proc"
    mount -t sysfs sysfs "${MOUNT_POINT}/sys"

    chroot "$MOUNT_POINT" /bin/bash /mnt/intergenos/scripts/install-theming.sh
```

Note: the four `mount --bind` calls bind `/dev`, `/dev/pts`, `/proc`, `/sys`. Not `/mnt/intergenos`. The condition at line 545 checks the HOST path (which exists), not the chroot path (which doesn't). The chroot invocation would error with "No such file or directory" and `set -euo pipefail` at line 21 would terminate create-image.sh.

**Severity: HIGH (functional).** The build is currently broken at this step, OR there's an out-of-band restoration step I haven't located. Either case warrants a fix.

**Proposed fix.** Add an explicit bind-mount of `/mnt/intergenos` into `${MOUNT_POINT}/mnt/intergenos` before the chroot call (and umount after). Or copy `install-theming.sh` plus its dependencies into the chroot tree before the chroot call. Or re-order: run theming install BEFORE the cleanup in `phase_image` (cleanest — keeps `${IGOS}/mnt/intergenos` available, no extra mounts).

---

### S5. `$TERM` shell-injection via `su -c` interpolation — `scripts/build-intergenos.sh:424,434`

**Problem.** `build-intergenos.sh` constructs a string passed to `su -c` that interpolates `$TERM` from the outer shell. If `$TERM` contains command-substitution syntax (`$(...)` or backticks), the user's shell receiving the `-c` argument re-evaluates the string and executes the substitution.

**Evidence.**

```bash
# build-intergenos.sh:424
su - "$BUILD_USER" -c "env -i HOME=/home/${BUILD_USER} TERM=${TERM} bash ${SCRIPTS}/toolchain-build.sh"
# build-intergenos.sh:434
su - "$BUILD_USER" -c "env -i HOME=/home/${BUILD_USER} TERM=${TERM} bash ${SCRIPTS}/temp-tools-build.sh"
```

The outer bash interpolates `${TERM}` into the string verbatim. If `TERM='$(touch /tmp/pwn)'`, the resulting string passed to su is literally `env -i HOME=... TERM=$(touch /tmp/pwn) bash ...` — and the user's shell, parsing the `-c` argument, executes the substitution.

Vector requires attacker-controlled `$TERM` at script-invocation time. On a properly configured build host this is unlikely, but the build documentation does not warn about environment hygiene before running the orchestrator. If the build host is ever attached to a remote agent (CI runner, docker-with-env-passthrough), this becomes exploitable.

**Severity: HIGH.** Real shell-injection vector via env-var passthrough. Defensive quoting is the right baseline regardless of whether the vector is currently exploitable.

**Proposed fix.** Use `${TERM@Q}` (bash 4.4+ literal-quote expansion) so the user's shell sees a properly single-quoted token:

```bash
su - "$BUILD_USER" -c "env -i HOME=/home/${BUILD_USER} TERM=${TERM@Q} bash ${SCRIPTS}/toolchain-build.sh"
```

Or, simpler: drop `$TERM` passthrough entirely and let the user's login shell pick it up from `/etc/environment` or default to `xterm`. The toolchain build doesn't need a specific TERM value.

**SPOC self-applies fix in same audit cycle** — see commit reference below.

---

## MEDIUM

### S6. Checkpoint restore command in log includes `rm -rf ${IGOS}/*` — `scripts/build-intergenos.sh:189`

**Problem.** The checkpoint save logs a "restore with" hint that begins with `rm -rf ${IGOS}/*`. If a future operator copy-pastes this hint while `IGOS` is unset or different, they could delete the wrong directory. The hint itself is correct in context but unsafe in transit.

**Evidence.**

```bash
# build-intergenos.sh:189
log ">>> Restore with: rm -rf ${IGOS}/* && tar -C ${IGOS} --zstd -xf ${checkpoint}"
```

**Severity: MEDIUM (operator-error).** Not exploitable, but log content suggesting destructive operations should be operator-safe.

**Proposed fix.** Change the hint to use the variable name unexpanded so future operators see what they need to set:

```bash
log ">>> Restore with: IGOS=${IGOS} && rm -rf \"\${IGOS:?}/\"* && tar -C \"\${IGOS}\" --zstd -xf ${checkpoint}"
```

The `${IGOS:?}` parameter expansion fails the command if IGOS is unset/empty.

---

### S7. `phase_image` `rm -rf` lacks defensive guards — `scripts/build-intergenos.sh:540-543`

**Problem.** Three `rm -rf` calls in `phase_image` rely on `set -u` (which would catch unset $IGOS) but don't guard against $IGOS becoming `/`. Defense-in-depth would require an explicit non-root, non-empty check.

**Evidence.**

```bash
# build-intergenos.sh:540-543
rm -rf "${IGOS}/mnt/intergenos"
rm -rf "${IGOS}/sources"
rm -rf "${IGOS}/tmp"/*
mkdir -p "${IGOS}/tmp"
```

If $IGOS were ever set to `/`, the second line would `rm -rf /sources` (probably empty on most hosts but illustrates the pattern). The first line would target the build host's repo. Vector requires intentional misconfiguration but defensive guards are cheap.

**Severity: MEDIUM (defensive).** No active exploit path; pattern hygiene only.

**Proposed fix.** Add a guard at the top of `phase_image`:

```bash
phase_image() {
    [[ -n "${IGOS:-}" && "$IGOS" != "/" && -d "$IGOS" ]] || {
        log "ERROR: \$IGOS is unset, root, or missing — refusing to proceed"
        return 1
    }
    ...
}
```

---

## LOW

### S8. `cp -a` of build infrastructure to chroot lacks integrity check — `scripts/build-intergenos.sh:408-411`

**Problem.** `phase_setup` copies `scripts/`, `packages/`, `igos-build/` from the host repo into the chroot via `cp -a`. No integrity verification — if the host repo is compromised at any earlier point in time, the build inherits the compromise. This is structural for a build-from-source posture, not unique to InterGenOS, but worth surfacing for the shim-review record.

**Severity: LOW (structural).** Reviewer-facing documentation should mention this.

**Proposed fix (documentation, not code).** Add a paragraph to the shim-review README describing the trust boundary at the build host: "build host's filesystem state IS the trust anchor for the build; reproducibility verification implies trusting the inputs at the moment of clone." Reproducibility checks are the mitigation.

---

### S9. Hardcoded `/dev/nbd0` device — `scripts/create-image.sh:26`

**Problem.** Image creation hardcodes `NBD_DEV=/dev/nbd0`. Concurrent builds on the same host would race on this device. Single-build assumption is documented in the script header but not enforced.

**Severity: LOW (operational).** Vector requires concurrent builds — currently disallowed by convention. Defensive-only.

**Proposed fix.** Auto-select an available NBD device:

```bash
NBD_DEV=$(for n in /dev/nbd{0..15}; do
    [ -b "$n" ] && ! qemu-nbd --check "$n" 2>/dev/null && echo "$n" && break
done)
```

---

## Summary

| ID | Severity | File | Status |
|---|---|---|---|
| S1 | CRITICAL | create-image.sh:380 | OWNER-DECISION (default root password) |
| S2 | CRITICAL | create-image.sh:390 | OWNER-DECISION (default user password) |
| S3 | HIGH | pkg-functions.sh:33 | DISPATCH (same shape as DeepSeek H1) |
| S4 | HIGH | create-image.sh:553 | DISPATCH (functional bug) |
| S5 | HIGH | build-intergenos.sh:424,434 | SPOC SELF-APPLIES |
| S6 | MEDIUM | build-intergenos.sh:189 | DEFER (cosmetic) |
| S7 | MEDIUM | build-intergenos.sh:540 | DEFER (defensive) |
| S8 | LOW | build-intergenos.sh:408 | DOC (reviewer-facing rationale) |
| S9 | LOW | create-image.sh:26 | DEFER (operational) |

**Status legend:** OWNER-DECISION = needs policy call; DISPATCH = can be coded without owner; SPOC SELF-APPLIES = fix landing in same audit cycle; DEFER = backlog with severity tag; DOC = no code change.

## Out of scope (this pass)

- `scripts/sign-release.sh` (covered by DeepSeek's 3rd-batch lane SR1/SR2/SR3).
- `scripts/host-check.py`, `scripts/download-sources.py`, `scripts/parse-blfs-book.py`, `scripts/populate-meson-db.py` — Python sources, separate audit pass warranted.
- `scripts/chroot-*.sh` (15 files) — second-pass audit candidate. Initial scan via grep showed no obvious eval / unsafe-curl / chmod-too-permissive patterns, but a targeted read pass would surface anything subtle.
- `scripts/install-theming.sh`, `scripts/download-theming.sh`, `scripts/merge-kernel-config.sh`, `scripts/blfs-query.py`, `scripts/check-public-content.py`, `scripts/apply-dep-audit.py`, `scripts/update-desktop-versions.py` — backlog.
