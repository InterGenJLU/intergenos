# 03 — Automating release signing

**Audience:** maintainers who hold the signing token and need to sign a release-grade artifact set after a clean build.

## Scope (what this doc covers vs. what it doesn't)

This doc covers **release-grade artifact signing** — the artifacts that ship in the public ISO + index. It does NOT cover:

- **Shim signing** — InterGenOS ships Fedora's pre-signed shim (Fedora-piggyback path ratified day-0 per `docs/owner-directives.md` D-002). We do not sign our own shim for v1.0 ship; the parallel `rhboot/shim-review` PR submission produces our own MS-signed shim for later releases.
- **Installed-system per-kernel UKI signing** — Installed systems regenerate + sign UKIs at kernel install/upgrade time using the **user's local MOK key**, not the release PIV slot 9c (per `docs/owner-directives.md` D-005, UKI parity Option A). That flow runs inside `packages/core/linux-kernel`'s post_install hook on the user's machine; the InterGenOS PIV slot 9c key NEVER leaves HQ.
- **Module signing** — Runs inside the kernel build with an ephemeral per-build key that never touches the hardware token. See topic 02 for kernel-build context.

## Goal

Sign three classes of release artifact, on an offline signing workstation, with hardware-backed keys:

1. **pkm repo index** (`InterGenOS.db`) — GPG detached signature with the release subkey (release-grade PGP).
2. **Kernel UKI binaries** (`igos-live.efi`, `igos-install-gui.efi`, `igos-install-tui.efi`) — Authenticode signatures via `sbsign` against the vendor X.509 cert backed by the PIV applet, slot 9c. The 3-UKI scope is canonical (live ISO + install-gui + install-tui) — this matches the live ISO output of `scripts/build-iso.sh`. Earlier versions of `sign-release.sh` silently skipped the install-gui + install-tui UKIs due to an incomplete glob; this regression class is closed by the post-sign count assertion described in step 4 below.
3. **GRUB EFI binary** (`grubx64.efi`) — Authenticode signature with the same PIV key.

The entry point is `scripts/sign-release.sh`. The script accepts staged unsigned artifacts and emits signed artifacts plus detached signatures to a clean output directory.

## Composes with directives

This ceremony composes with these binding ratifications at `docs/owner-directives.md`:

- **D-002** — Fedora-piggyback shim is the v1.0 ship path. We do not sign our own shim during this ceremony (see Scope).
- **D-005** — Installed-system per-kernel UKIs are signed by the user's local MOK at the user's machine, NOT here. This ceremony signs the live ISO + install-* UKIs that ship in the public ISO.
- **D-007** — SSH/root posture is enforced as a Class A ship-gate by `scripts/check-d007-compliance.sh` wired into `scripts/build-iso.sh phase_image`. That gate runs at ISO build time, separate from this release-signing ceremony. The signed artifacts produced here are consumed by the ISO build that runs the D-007 gate.

## Prerequisites

- An **offline signing workstation** — never the same physical machine as the build VM host. Air-gapped where practical; at minimum, browser closed + non-essential processes terminated for the duration of the ceremony (this is a ceremony-grade operation, not a casual scripted run).
- A hardware token with:
  - GPG release subkey loaded
  - PIV applet, slot 9c populated with the X.509 cert backing `grubx64.efi` + UKI Authenticode signatures
- Token PIN unlocked (`gpg --card-status` succeeds, `pkcs11-tool --list-objects` returns the PIV cert).
- `gpg`, `sbsigntool` (provides `sbsign`), `pkcs11-tool` (from `opensc`), `scd` (scdaemon) installed on the signing workstation.
- The vendor X.509 cert PEM-encoded at `/etc/intergenos/signing/vendor-cert.pem` (pre-positioned, NOT transported with each artifact bundle — protects against substitution).
- A staged unsigned artifacts directory containing some or all of:
  - `InterGenOS.db` (pkm repo index)
  - `igos-live.efi`, `igos-install-gui.efi`, `igos-install-tui.efi` (UKI variants from `scripts/build-uki.sh`)
  - `grubx64.efi` (unsigned standalone from `scripts/build-grub-standalone.sh`)
- A scdaemon configuration that allows the PIV applet to coexist with the GPG card — see "scdaemon configuration" below; the canonical shape is documented in the project's scdaemon-conf operational note.

## scdaemon configuration

The GPG-card + PKCS#11-PIV split on the same token requires scdaemon to be told which reader handles which applet. The canonical shape lives at `~/.gnupg/scdaemon.conf` and includes a `reader-port` line plus a `disable-ccid` line (so OpenSC can drive the PIV applet via PC/SC while GPG drives the OpenPGP applet via the same reader). Without this config, the PIV `pkcs11-tool --login` call and the `gpg --detach-sign` call race for the same applet and one of them fails non-deterministically. Reference the operational note for the verbatim file contents.

## Manual ceremony is permanently off the table

Every signing pass goes through `scripts/sign-release.sh`. Manual step-by-step sbsign / gpg invocations are not supported and not permitted — the script encodes the full sequence (token presence check, vendor cert match, key-material validation, per-artifact sign, output-directory layout) and any deviation introduces ceremony drift. If something needs to change, change the script (in a reviewed commit) and re-run; never copy-paste a one-off invocation.

## Step-by-step procedure

### 1. Stage the artifacts on the signing workstation

Transport the unsigned artifacts from the build VM to the signing workstation by whatever low-trust mechanism the operational threat model allows (USB stick wiped before and after; scp over a known-trusted LAN segment; rsync over the build VPN). The cert at `/etc/intergenos/signing/vendor-cert.pem` is pre-positioned and is NOT part of the artifact transport.

Conventional staging path:

```
/home/<user>/signing/staged/<release-tag>/
├── InterGenOS.db
├── igos-live.efi
├── igos-install-gui.efi
├── igos-install-tui.efi
└── grubx64.efi
```

### 2. Confirm token + key material before signing

```sh
# OpenPGP side — release subkey present + reachable
gpg --card-status
# Look for `Signature key ...:` with the expected key ID

# PIV side — vendor cert present + reachable
pkcs11-tool --list-objects --type cert
# Look for the InterGenOS vendor cert by CKA_LABEL or CKA_ID

# Vendor cert matches what sign-release.sh expects
openssl x509 -in /etc/intergenos/signing/vendor-cert.pem -noout -subject -issuer -fingerprint
# Confirm the fingerprint matches the build's embedded shim vendor cert
```

If any of these fail, **stop**. Do not proceed with a partial ceremony. Resolve the configuration drift first.

### 3. Set the signing env vars

```sh
export INTERGENOS_GPG_KEY_ID=<release-subkey-fingerprint>
export INTERGENOS_PKCS11_URI='pkcs11:object=InterGenOS%20SB;type=private'
export INTERGENOS_VENDOR_CERT=/etc/intergenos/signing/vendor-cert.pem
```

The PKCS#11 URI matches the cert's CKA_LABEL — adjust if your token uses a different label.

### 4. Run sign-release.sh

```sh
scripts/sign-release.sh \
    --artifacts /home/<user>/signing/staged/<release-tag>/ \
    --output /home/<user>/signing/signed/<release-tag>/ \
    --strict
```

`--strict` requires every artifact class in the staged directory to be present; absent files fail the ceremony rather than silently skipping. Use `--strict` for releases, drop it for incremental signing of subset-rebuilds.

The script:

1. Confirms token presence + PKCS#11 enumeration succeed.
2. Confirms `--vendor-cert` X.509 matches the PKCS#11 key (avoids signing with the wrong key against the right cert — a class of error that previously cost about a day of debugging).
3. Enumerates input UKIs by the canonical glob set (`*.uki.efi`, `igos-live.efi`, `igos-install-gui.efi`, `igos-install-tui.efi`, future `igos-install-*.efi` variants). Records the input count.
4. For each artifact:
   - **InterGenOS.db** → `gpg --detach-sign --armor --local-user $GPG_KEY_ID InterGenOS.db` → emits `InterGenOS.db.asc` (the detached ASCII-armored signature).
   - **UKI binaries** → `sbsign --engine pkcs11 --key "$INTERGENOS_PKCS11_URI" --cert "$VENDOR_CERT" --output <out>.efi <in>.efi` → emits the signed UKI. UKI shape is preserved through the sign operation (signature lands in the existing PE32+ certificate-table; `.linux`/`.initrd`/`.cmdline`/etc. sections untouched).
   - **grubx64.efi** → same sbsign invocation → emits the signed GRUB binary.
5. **Post-sign count assertion** — verifies `signed_uki_count == input_uki_count`. Catches the regression class where a new UKI variant is added (e.g., a future `igos-recovery.efi`) but the signing glob isn't extended; the assertion fires and the ceremony aborts rather than shipping with unsigned UKIs that the build-iso phase would then refuse-or-warn on.
6. Validates each signed binary with `sbverify --cert "$VENDOR_CERT"` and aborts if any verify-step fails.
7. Writes a per-artifact log to `${OUTPUT}/sign.log` recording timestamp, input SHA, output SHA, signer identity.

### 5. Verify the signed artifacts before transport

```sh
cd /home/<user>/signing/signed/<release-tag>/
for f in *.efi; do
    sbverify --cert /etc/intergenos/signing/vendor-cert.pem "$f" \
        && echo "OK: $f"
done

gpg --verify InterGenOS.db.asc InterGenOS.db && echo "OK: InterGenOS.db"
```

All artifacts must verify before they leave the signing workstation. If any fail, **do not transport** — investigate first.

### 6. Transport the signed bundle to the build VM

Same path as the unsigned-transport mechanism, in reverse. The signed bundle is what `scripts/build-iso.sh` consumes as its SHIM/GRUB/UKI env vars (topic 05).

## Validation

- `sign-release.sh` exits 0.
- `sbverify` succeeds against every `.efi` output.
- `gpg --verify` succeeds for the InterGenOS.db detached signature.
- `${OUTPUT}/sign.log` shows expected timestamps, signer identity, and non-empty input/output SHAs for each artifact.

## Common failures + troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `gpg --card-status` returns "No such device" | Token unplugged or pcscd not running | Plug token, `systemctl restart pcscd`, retry |
| `pkcs11-tool --list-objects` empty | scdaemon holding the reader and not yielding to OpenSC | adjust `~/.gnupg/scdaemon.conf` per the operational note; restart scdaemon |
| `sbsign` errors with "Could not load key" | PKCS#11 URI doesn't resolve to a private key on the inserted card | re-check `pkcs11-tool --list-objects --type privkey` and the URI's `object=` value |
| `sbverify` fails on a freshly-signed binary | Vendor cert in `--cert` doesn't match the PKCS#11 key used to sign | the `--vendor-cert` and PKCS#11 key MUST be the same key-pair; resync the cert file |
| sign-release.sh aborts with "vendor cert SHA mismatch" | The X.509 at `--vendor-cert` doesn't match the cert embedded in the build's shim | rebuild shim with the correct vendor cert OR re-position the correct vendor cert on the signing workstation |
| Detached `.asc` signature opens as a 0-byte file | GPG didn't actually sign (silent failure mode under some scdaemon races) | rerun, confirm the file is non-empty before transport |

## Cross-references

- Topic 02: How to run the builder — produces the unsigned UKIs + GRUB binary
- Topic 05: How to create an ISO — consumes the signed outputs
- `scripts/sign-release.sh` — canonical reference
- `scripts/build-uki.sh` — produces the UKI envelope that gets signed
- `scripts/build-grub-standalone.sh` — produces the unsigned grubx64.efi
- `scripts/check-d007-compliance.sh` — Class A ship-gate at ISO build time per D-007; consumes the artifacts this ceremony produces
- `docs/owner-directives.md` — D-002 (Fedora-piggyback shim), D-005 (installed-system UKI parity, user-MOK signing), D-007 (SSH/root/credentials posture)
- The scdaemon-conf and no-manual-ceremony-steps operational notes — referenced above; read them before any signing pass
