# First-Publish Runbook — repo.intergenos.org Binary Mirror

**Last updated:** 2026-05-12
**Status:** v0.1 — pre-flight draft. **The procedure described here has never been exercised end-to-end.** Treat the first run as a ceremony: dry-run against a staging mirror before pointing `repo.intergenos.org` at real artifacts, walk every step under maintainer eyes, expect to discover gaps.

This runbook covers the operational procedure that takes a completed InterGenOS build through to `https://repo.intergenos.org/x86_64/` serving real artifacts that `pkm sync` can install from. It is the operator-on-laptop companion to the publication tooling that already lives in-tree (`scripts/emit-package-archives.py`, `scripts/generate-repodb.py`, `scripts/sign-release.sh`).

For the user-facing trust model — what users are trusting when they install — see [`docs/repository-trust.md`](../repository-trust.md). For the per-signing-window ceremony mechanics (hardware token handling, touch-policy, signing-session hygiene) see [`docs/signing-procedure.md`](../signing-procedure.md). For the canonical signing-key fingerprints see [`docs/signing-key.md`](../signing-key.md).

## Status Banner

- **First-time mirror provisioning.** The signing key fingerprint and the artifact set this procedure publishes become load-bearing for every future install — anything anyone installs subsequently is verified against the key material established on this run.
- **No `current` directory yet to atomic-swap-against.** The atomic-directory-swap rollback path described in the orchestrator's M1 design assumes a previous `current` to roll back to; the first publish does not have one. The Rollback section covers the empty-mirror-seeding case explicitly.
- **`sign-release.sh` has been unit-tested but not exercised against `/var/lib/igos/archives/` output.** Allocate time on the first run for surfacing wiring gaps between the build orchestrator and the signer.

## Prerequisites

Confirm every item below before starting the publish window. A failure halfway through with an avoidable prerequisite gap is more costly than the up-front check.

### 1. Build artifacts present

- Build has completed without halt or kernel-build error.
- Per-package archives present at `/var/lib/igos/archives/*.igos.tar.gz` inside the build chroot.
- Build orchestrator's archive integrity manifest (`intergenos-archive-manifest.txt`) has been emitted to the same artifacts directory or a sibling staging directory.

### 2. Signing workstation prepared

Per [`docs/signing-procedure.md`](../signing-procedure.md) §"Pre-Sign Discipline":

- Hardware token (Nitrokey 3 NFC #1) physically present on the signing workstation.
- `gpg --card-status` lists the card and its serial.
- `pkcs11-tool --module /usr/lib/opensc-pkcs11.so --list-slots` lists the PIV interface.
- `INTERGENOS_GPG_KEY_ID` and `INTERGENOS_PKCS11_URI` exported in the signing shell, OR pass `--gpg-key-id` and `--pkcs11-uri` to `sign-release.sh` explicitly.
- For a tagged release: `INTERGENOS_GPG_MASTER_KEY_ID` exported as well, so the archive manifest is cosigned by master + [S1].
- Browsers, chat clients, and non-essential background tools closed. Screen sharing, remote assist, and recording disabled. No unexpected USB devices attached. Workstation login session has not been idle-and-reopened since last reboot.

### 3. Signing-key drift cross-check

The canonical signing-key fingerprints are published in **two** load-bearing locations that must agree:

- [`docs/signing-key.md`](../signing-key.md) — the human-readable canonical publication.
- [`pkm/release-keys.json`](../../pkm/release-keys.json) — the machine-readable canonical config consumed by `generate-repodb.py` and `pkm`.

Pre-publish, diff the two by hand and confirm S1 + S2 (also documented as legacy aliases NK#1 + NK#2) fingerprints match byte-for-byte:

```
# Print fingerprints from the human-readable doc
grep -E '^\| (Master fingerprint|Signing subkey \[NK#[12]\])' docs/signing-key.md

# Print fingerprints from the machine-readable config
python3 -c "import json; d=json.load(open('pkm/release-keys.json')); \
    [print(k, v) for k, v in d.items() if 'fingerprint' in str(v).lower() or k in ('s1','s2','nk1','nk2')]"
```

If the two disagree on any fingerprint, **halt**. Resolve the drift in code review against `master` before signing anything; a publish that signs with one fingerprint while announcing another collapses the entire trust chain.

### 4. SSH path to the mirror

- SSH key with push access to `intergenos@origin.intergenstudios.com -p 2200`.
- Test the path before signing anything:

```
ssh -p 2200 intergenos@origin.intergenstudios.com 'whoami && ls -la /home/intergenos/repo/x86_64/'
```

The session should return `intergenos` and list whatever is currently in `/x86_64/`. For the first publish, the directory may be empty or contain only a placeholder index file — that is expected.

### 5. DNS + TLS smoke

```
curl -fsS -o /dev/null -w '%{http_code} %{ssl_verify_result}\n' \
    https://repo.intergenos.org/x86_64/
```

Should return `200 0` (HTTP 200, TLS verification OK) once anything is published, or `403/404 0` (no content, but TLS verifies) on a fresh mirror. A non-zero `ssl_verify_result` means the Let's Encrypt cert has drifted or expired — fix before publishing.

## Mirror Layout

The publish target is structured (not flat):

```
/home/intergenos/repo/x86_64/
    InterGenOS.db
    InterGenOS.db.sig
    packages/
        <pkg>-<version>.igos.tar.gz
        ...
```

The repository index (`InterGenOS.db`) and its detached GPG signature (`InterGenOS.db.sig`) sit at `/x86_64/` root. Per-package archives live in `/x86_64/packages/`. The atomic-swap target is the entire `/x86_64/` directory as a single unit — the orchestrator stages a `/x86_64.new/` sibling, fsyncs, then renames over `/x86_64/`. See [Atomic Promote](#step-6-atomic-promote-on-the-mirror).

**Open question for first run (flag on review):** the layout diagram supplied at runbook commission time included per-archive `<pkg>-<version>.igos.tar.gz.sig` files alongside each archive. The current trust model in [`docs/repository-trust.md`](../repository-trust.md) §1 specifies SHA-256-via-signed-index — per-archive integrity is verified against the sha256 recorded in the cryptographically signed `InterGenOS.db`, not against per-archive PGP signatures. `scripts/sign-release.sh` does not emit per-archive signatures. If per-archive `.sig` files are intended as defence-in-depth alongside the signed index, that is a separate addition to the signer + a trust-model doc update. **Resolve before first publish: confirm whether per-archive `.sig` files are required on the mirror, and if so, dispatch the signer enhancement first.**

## Procedure

### Step 1 — Stage Build #9 archives out of the chroot

Build output sits inside the chroot at `/var/lib/igos/archives/`. Stage it out to a clean host directory so the rest of the procedure operates on a stable artifact set that is decoupled from build-chroot lifecycle:

```
HOST_STAGING="${HOME}/intergenos-first-publish-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$HOST_STAGING/unsigned"
sudo cp -a /mnt/intergenos-chroot/var/lib/igos/archives/*.igos.tar.gz \
           "$HOST_STAGING/unsigned/"
sudo chown -R "$(id -u):$(id -g)" "$HOST_STAGING"
```

Also stage the build-emitted archive integrity manifest if `phase_manifest` of the build orchestrator produced one:

```
sudo cp /mnt/intergenos-chroot/var/lib/igos/intergenos-archive-manifest.txt \
        "$HOST_STAGING/unsigned/"
```

Sanity-count:

```
ls "$HOST_STAGING/unsigned/"*.igos.tar.gz | wc -l
ls -la "$HOST_STAGING/unsigned/intergenos-archive-manifest.txt"
```

### Step 2 — Generate the repository index

`scripts/generate-repodb.py` walks the staged archives, computes per-archive SHA-256, reads each archive's manifest, and emits `InterGenOS.db` in the format that `pkm/repo.py` parses. It also invokes the GPG-signing step against the configured release subkey to produce `InterGenOS.db.sig`.

```
cd /path/to/intergenos-repo-clone
INTERGENOS_GPG_KEY_ID="<S1 fingerprint from pkm/release-keys.json>" \
python3 scripts/generate-repodb.py \
    --archives "$HOST_STAGING/unsigned/" \
    --output   "$HOST_STAGING/unsigned/InterGenOS.db"
```

The script writes `InterGenOS.db` to the output path and `InterGenOS.db.sig` alongside it. Expect one hardware-token touch for the signing step.

Verify roundtrip before continuing:

```
gpg --verify "$HOST_STAGING/unsigned/InterGenOS.db.sig" \
             "$HOST_STAGING/unsigned/InterGenOS.db"
python3 -c "from pkm.repo import parse_index; \
    idx = parse_index(open('$HOST_STAGING/unsigned/InterGenOS.db').read()); \
    print(f'parsed {len(idx)} package entries')"
```

The `gpg --verify` line should report the expected signing subkey fingerprint with "Good signature". The `parse_index` line should report a non-zero entry count matching the archive count from Step 1.

### Step 3 — Sign the release with `sign-release.sh`

`scripts/sign-release.sh` is the single entrypoint for release-window signatures. Pointed at the staged unsigned directory, it produces (when the corresponding inputs are present):

- `InterGenOS.db.sig` — GPG-detached signature on the repository index (S1 subkey). **Already present from Step 2; the signer will re-emit identically.**
- `*.uki.efi` / `igos-live.efi` — Unified Kernel Image signatures (PIV slot 9c via sbsign).
- `grubx64.efi` — GRUB signature (same PIV slot).
- `intergenos-archive-manifest.txt.sig` + `intergenos-release-key.asc` — the install-time integrity manifest signature (and a public-key export for ISO embedding).

```
sudo "$HOME/intergenos-repo-clone/scripts/sign-release.sh" \
    --artifacts "$HOST_STAGING/unsigned" \
    --output    "$HOST_STAGING/signed" \
    --strict
```

`--strict` causes the script to fail rather than skip when an expected artifact is missing. For a first publish where you may not have UKIs or GRUB staged at the same time as repo-index publication, omit `--strict` and accept the skips — but then **re-run** the signer for the missing artifact classes before flipping the mirror live.

Expect one token touch per artifact class. Pre-emit checks inside the signer (SBAT generation precheck, manifest format precheck) may halt the run; address the underlying issue rather than bypassing.

Verify locally before staging to the mirror:

```
bash scripts/check-manifest-signature.sh \
     "$HOST_STAGING/signed/intergenos-archive-manifest.txt" \
     "$HOST_STAGING/signed/intergenos-archive-manifest.txt.sig" \
     "$HOST_STAGING/signed/intergenos-release-key.asc"
```

### Step 4 — Assemble the mirror-layout staging directory

Compose the final on-disk layout locally before any data leaves the workstation. This makes the rsync step a verbatim mirror-image transfer, not a layout decision under push:

```
MIRROR_STAGING="$HOST_STAGING/mirror-layout"
mkdir -p "$MIRROR_STAGING/packages"

cp "$HOST_STAGING/signed/InterGenOS.db"     "$MIRROR_STAGING/"
cp "$HOST_STAGING/signed/InterGenOS.db.sig" "$MIRROR_STAGING/"
cp "$HOST_STAGING/unsigned/"*.igos.tar.gz   "$MIRROR_STAGING/packages/"
```

Confirm the on-disk shape matches the documented mirror layout exactly:

```
find "$MIRROR_STAGING" -maxdepth 2 -type f | sort
```

Expected output: `InterGenOS.db` + `InterGenOS.db.sig` at root, every `<pkg>-<version>.igos.tar.gz` under `packages/`, no extra files.

### Step 5 — Rsync to the mirror staging path

Push to a **staging path** on the mirror first (not directly to `/x86_64/`). Atomic promotion happens server-side in Step 6:

```
rsync -avh --delete -e 'ssh -p 2200' \
    "$MIRROR_STAGING/" \
    intergenos@origin.intergenstudios.com:/home/intergenos/repo/x86_64.new/
```

`--delete` ensures the staging directory on the mirror exactly matches the local staging directory (no leftover state from an interrupted prior attempt). Run a dry-run first if uncertain:

```
rsync -avhn --delete -e 'ssh -p 2200' \
    "$MIRROR_STAGING/" \
    intergenos@origin.intergenstudios.com:/home/intergenos/repo/x86_64.new/
```

After the push, sanity-confirm the mirror staging directory matches local:

```
ssh -p 2200 intergenos@origin.intergenstudios.com \
    'cd /home/intergenos/repo/x86_64.new && \
     find . -maxdepth 2 -type f | sort && \
     sha256sum InterGenOS.db'

# Compare to local
(cd "$MIRROR_STAGING" && find . -maxdepth 2 -type f | sort && sha256sum InterGenOS.db)
```

The two file lists and the index sha256 should match exactly.

### Step 6 — Atomic promote on the mirror

The publish-repo orchestrator's M1 atomic-directory-swap pattern renames `/x86_64.new/` over `/x86_64/` in a single filesystem operation so observers never see a half-state. For an empty-mirror first publish there is no prior `/x86_64/` to displace; the operation reduces to a single `mv` of the staging directory:

```
ssh -p 2200 intergenos@origin.intergenstudios.com bash -s <<'EOF'
set -euo pipefail

cd /home/intergenos/repo

# Pre-promote shape check
if [[ ! -f x86_64.new/InterGenOS.db || ! -f x86_64.new/InterGenOS.db.sig ]]; then
    echo "error: staging dir missing InterGenOS.db or .sig — refusing to promote" >&2
    exit 1
fi

# Atomic promote
if [[ -d x86_64 ]]; then
    # Subsequent publish: rotate previous into x86_64.prev, then promote
    rm -rf x86_64.prev || true
    mv x86_64 x86_64.prev
fi
mv x86_64.new x86_64

# Post-promote shape check
ls -la x86_64/InterGenOS.db x86_64/InterGenOS.db.sig
ls x86_64/packages/ | wc -l
EOF
```

The first publish exits this step with `/home/intergenos/repo/x86_64/` containing the live mirror. Subsequent publishes preserve `/home/intergenos/repo/x86_64.prev/` as the immediate rollback target.

## Validation

After promote, the public mirror serves the published artifacts. Before announcing it as live, exercise the install path on a fresh InterGenOS qcow2 from a host that has never imported the keyring:

```
# Inside the fresh qcow2, no prior pkm state:
pkm sync
pkm install hello   # or any other small package known to be in the index
```

Expected behaviour:

- `pkm sync` fetches `InterGenOS.db` + `InterGenOS.db.sig`, verifies the signature against the local `intergenos-keyring`, and updates the local index cache.
- `pkm install hello` downloads `packages/hello-<version>.igos.tar.gz`, computes its SHA-256, compares against the signed index, and installs only on exact match.
- A `gpg --verify` failure or a sha256 mismatch halts the install (`pkm` fail-closed behaviour documented in [`docs/repository-trust.md`](../repository-trust.md) §5).

Run a negative test before sign-off:

```
# Tamper with one byte of an archive on disk, then attempt install
echo X >> /var/cache/pkm/archives/hello-*.igos.tar.gz
pkm install hello --archive /var/cache/pkm/archives/hello-*.igos.tar.gz \
                  --archive-trust=strict
```

`pkm` must refuse the install with a sha256 mismatch message. Document the exact error string in the launch checklist for support reference.

## Rollback

### Mid-rsync interruption (Step 5 failure)

The mirror staging path `/x86_64.new/` is independent of `/x86_64/`. An interrupted rsync leaves a half-state in `/x86_64.new/`; re-running rsync with `--delete` against the same staging path resyncs to the local source of truth. The live `/x86_64/` (if any) is untouched. **No live-traffic impact.**

### Sign-step failure (Step 2 or 3)

`sign-release.sh` runs all sign operations into a hidden `.signing-$PID/` working subdirectory and only `mv` the contents into `--output` after every step succeeds. A failed sign step leaves the output directory untouched. Re-run the script against the same `--artifacts` and a clean `--output`. The script is idempotent against clean output directories.

### Atomic-swap failure (Step 6)

If the post-promote shape check in Step 6 fails (e.g., `InterGenOS.db` missing), the swap has not yet completed. Roll back by reversing the rename:

```
ssh -p 2200 intergenos@origin.intergenstudios.com bash -s <<'EOF'
set -euo pipefail
cd /home/intergenos/repo
mv x86_64 x86_64.new        # revert the just-promoted dir back to staging
if [[ -d x86_64.prev ]]; then
    mv x86_64.prev x86_64   # restore the prior live mirror
fi
ls -la x86_64/ 2>/dev/null || echo "x86_64/ absent (empty mirror or first publish)"
EOF
```

### Post-promote regression discovered in validation

If `pkm sync` on the fresh qcow2 fails after the atomic promote, the immediate rollback target is `/x86_64.prev/` (subsequent publishes) or an empty mirror (first publish — no `prev`).

For subsequent publishes:

```
ssh -p 2200 intergenos@origin.intergenstudios.com bash -s <<'EOF'
set -euo pipefail
cd /home/intergenos/repo
[[ -d x86_64.prev ]] || { echo "no x86_64.prev to roll back to" >&2; exit 1; }
mv x86_64 x86_64.bad-$(date -u +%Y%m%dT%H%M%SZ)
mv x86_64.prev x86_64
ls -la x86_64/InterGenOS.db x86_64/InterGenOS.db.sig
EOF
```

For the first publish only: take the live mirror offline by renaming `/x86_64/` to `/x86_64.broken-<timestamp>/` until the underlying issue is resolved and a clean re-publish can run. There is no prior good state on the mirror to roll back to.

### DNS-side fallback

If the mirror is unreachable for reasons outside the orchestrator's control (origin host down, network partition, TLS cert expiration), users have no fallback URL — `pkm` is configured against `https://repo.intergenos.org/x86_64/` and that is the only canonical source. The mitigation is to fix the upstream issue rather than to point users at an unverified alternative.

## Cross-references

- [`docs/repository-trust.md`](../repository-trust.md) — user-facing trust model (what `pkm` verifies + how)
- [`docs/signing-procedure.md`](../signing-procedure.md) — per-signing-window ceremony mechanics
- [`docs/signing-key.md`](../signing-key.md) — canonical fingerprint publication
- [`docs/ceremony/signing-key-ceremony-procedure.md`](../ceremony/signing-key-ceremony-procedure.md) — key-generation and rotation procedure
- [`pkm/release-keys.json`](../../pkm/release-keys.json) — canonical machine-readable key id config
- [`scripts/sign-release.sh`](../../scripts/sign-release.sh) — release-window signer
- [`scripts/generate-repodb.py`](../../scripts/generate-repodb.py) — index emitter
- [`scripts/emit-package-archives.py`](../../scripts/emit-package-archives.py) — per-package archive emitter (build-pipeline integration is the open item between this script and Step 1)
- [`scripts/check-manifest-signature.sh`](../../scripts/check-manifest-signature.sh) — full Q14-style manifest precheck
- [`SECURITY.md`](../../SECURITY.md) — trust-anchor compromise response policy

## Appendix A — First-launch-only notes

The procedure above is written to support both the first publish and every subsequent publish. The items below apply **only to the first publish**, and can be ignored on every run thereafter.

### A.1 — Empty-mirror seeding

The atomic-promote in Step 6 has no `/x86_64/` to displace on the first run. The script handles this branch (the `[[ -d x86_64 ]]` guard) but the cost is that there is no `/x86_64.prev/` to roll back to. Plan the first publish during a low-traffic window so a forced rollback can take the form of an offline mirror rather than a flap.

### A.2 — Fresh keyring distribution

The first publish is the first public publication of the `intergenos-keyring` package, and therefore the first opportunity for users to acquire the canonical signing fingerprints from the project. Emphasise to early adopters the cross-publication verification per [`docs/signing-key.md`](../signing-key.md) §"Cross-check the fingerprint" — users should confirm the fingerprint matches in at least three of the listed independent sources before trusting the keyring on their machine.

### A.3 — Build-pipeline emission wiring (the E1.B.5 open piece)

`scripts/emit-package-archives.py` exists on master and emits per-package `.igos.tar.gz` archives correctly when invoked against a populated package-build directory. The open piece between Step 1 of this runbook and the build orchestrator is the wiring that invokes `emit-package-archives.py` automatically at the end of each build, so the operator does not have to stage archives out of the chroot by hand. Until that wiring lands, Step 1 is operator-driven; once it lands, Step 1 reduces to a single `cp` from a host-visible output directory.

### A.4 — Per-archive signature design question

See the "Open question for first run" note in [Mirror Layout](#mirror-layout). The current trust model does not require per-archive PGP signatures; the runbook is written against the as-built tooling. Resolve before the first publish whether per-archive `.sig` files are required as defence-in-depth and dispatch the signer enhancement if so.

## Appendix B — Launch checklist

A condensed, single-page form of the procedure for use on the publish day. Run the full Prerequisites + Procedure + Validation top-to-bottom; this checklist is a final-pass sanity sweep, not a substitute.

- [ ] Build complete; archives at `/var/lib/igos/archives/`; manifest emitted
- [ ] Hardware token plugged + PIN unlocked; `gpg --card-status` lists card; `pkcs11-tool` lists PIV
- [ ] `INTERGENOS_GPG_KEY_ID` + `INTERGENOS_PKCS11_URI` exported; master-cosign env var set if tagged release
- [ ] Signing-key drift cross-check (`docs/signing-key.md` vs `pkm/release-keys.json`) — PASS
- [ ] SSH path to mirror confirmed (`intergenos@origin.intergenstudios.com -p 2200`)
- [ ] DNS + TLS smoke (`curl ...`) — PASS
- [ ] Archives staged out of chroot to `$HOST_STAGING/unsigned/`
- [ ] `generate-repodb.py` produced `InterGenOS.db` + `.sig`; `gpg --verify` PASS; `parse_index` count matches archive count
- [ ] `sign-release.sh --strict` (or non-strict with documented skips) — output verified
- [ ] Mirror-layout staging dir composed; `find` matches expected shape
- [ ] Rsync dry-run reviewed; rsync push completed; remote sha256 matches local
- [ ] Atomic-promote completed; post-promote shape check PASS
- [ ] Fresh qcow2 validation: `pkm sync` + `pkm install` end-to-end PASS
- [ ] Negative test: tampered archive rejected by `pkm install`
- [ ] Mirror announced as live in maintainer channels + `docs/repository-trust.md` updated if needed
