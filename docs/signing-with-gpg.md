# Signing files with OpenPGP — `scripts/sign-with-gpg.sh`

Universal helper for producing detached OpenPGP signatures (`.asc` sidecar
files) over arbitrary InterGenOS artifacts using the project master signing
key. Pairs with the smartcard-setup library at
`scripts/lib-gpg-card-setup.sh`.

## When to use

Use `sign-with-gpg.sh` for any **non-PE-binary** signing target: pinned
manifests (e.g. `intergen/data/models-manifest.json.asc`), release tarballs,
pkm repo indexes, JSON / YAML / text configs, etc. The output is an armored
detached `.asc` sidecar that lives alongside the signed file.

Use `scripts/sign-release.sh` / `sign-bootloader.sh` / `sign-kernel-uki.sh`
/ `sign-grub.sh` / `sign-shim.sh` for **UEFI Secure Boot PE/COFF binaries**.
Those use `sbsign --engine pkcs11` against the X.509 cert on NK#1 PIV slot
9c — a completely different cryptographic interface from this script. See
[docs/signing-procedure.md](signing-procedure.md) for the sbsign side.

## Quick start

```sh
# 1. Validate without signing
bash scripts/sign-with-gpg.sh --file path/to/file --dry-run

# 2. Real sign (PIN + Nitrokey touch required)
bash scripts/sign-with-gpg.sh --file path/to/file

# 3. Sig lands at path/to/file.asc (default --out)
# 4. Anyone with the project master pubkey can verify:
gpg --verify path/to/file.asc path/to/file
```

The script does not invoke any `git` operations. What happens with the file
+ sig after signing is your decision — stage in `git`, copy elsewhere,
ship to a peer, etc.

## CLI reference

```
sign-with-gpg.sh --file <path> [--sha256 <hex>] [--key <fingerprint>]
                 [--out <path>] [--dry-run] [--debug] [--help]
```

| Flag | Required? | Default | Purpose |
|------|-----------|---------|---------|
| `--file <path>` | yes | — | File to sign |
| `--sha256 <hex>` | no | (skip check) | Expected SHA256; pre-flight refuses if file's actual sha256 doesn't match. Useful for cross-host byte-fidelity sanity (e.g. you `scp`'d the file in and want to confirm it arrived intact). |
| `--key <fingerprint>` | no | `5597A3E0587B253006D0DD7B8C50826182083050` (project master) | 40-hex-char OpenPGP fingerprint of the signing key |
| `--out <path>` | no | `<file>.asc` | Sig output path |
| `--dry-run` | no | off | Run pre-flight + library setup-init in DRY mode; no signing |
| `--debug` | no | off | Verbose tracing to `~/tmp/sign-with-gpg.debug.log` |
| `--help` / `-h` | no | — | Print usage and exit |

## Workflow phases

The script runs through 6 numbered phases (banner output makes them visible
in real time):

- **Phase 0/5 — Pre-flight.** Confirms the library is at the expected
  sibling path, the target file exists + is readable, and (if `--sha256`
  given) the file's actual hash matches.
- **Phase 1/5 — GPG smartcard setup.** Sources `lib-gpg-card-setup.sh`
  and calls `gpg_card_setup_init` + `gpg_card_verify_key`. Idempotent;
  see [Smartcard setup the library handles](#smartcard-setup-the-library-handles)
  below for what runs on a fresh host.
- **Phase 2/5 — Sign.** Prints a "PIN ENTRY REQUIRED" preamble naming
  exactly which PIN the operator should enter, then invokes
  `gpg --detach-sign --armor --local-user <fpr> --output <out> <file>`.
  The Nitrokey will blink — touch over/near the shield symbol at the top
  of the key to authorize the signing operation. **Failure to touch in
  time causes the signing to timeout; the script must then be re-run.**
- **Phase 3/5 — Verify.** Runs `gpg --verify --status-fd=1` and parses
  the machine-readable `VALIDSIG` status line to confirm the **primary
  key fingerprint** matches `--key`. (Human-readable `gpg --verify` only
  shows the signing-subkey fingerprint; the primary fingerprint is
  available only via the `--status-fd` output.)
- **Phase 4/5 — Summary.** Prints sig path + sig size + sig sha256 +
  signing key fingerprint + debug log path.
- **Phase 5/5 — Complete.** Exit 0.

## Smartcard setup the library handles

The library is **polite-house-guest mode**: temporary modifications during
the signing run only, originals restored on exit so the host state is
unchanged after the script completes (clean exit, error exit, or signal
interrupt).

1. **Host class detection.** `pcscd` present in `$PATH` → Class A (pcscd
   activation + canonical `scdaemon.conf`). `pcscd` absent → Class B
   (skip pcscd ops; scdaemon's built-in CCID driver is doing the work).
2. **EXIT trap registered.** Before any config swap, the library registers
   an EXIT/INT/TERM trap that restores all backed-up configs and kills
   `gpg-agent` so the restored state takes effect on the next gpg call.
3. **`gpg-agent.conf` swap-in.** If an existing `~/.gnupg/gpg-agent.conf`
   is present, it's renamed to `~/.gnupg/gpg-agent.conf.sign-with-gpg-backup`.
   A temporary config containing only `pinentry-program /usr/bin/pinentry-tty`
   is written in its place. This avoids the terminal-mode garbling that
   `pinentry-curses` (the system default on many distros) can leave behind.
   At script exit, the backup is restored.
4. **Class A only: `scdaemon.conf` swap-in.** Same pattern — existing
   config (if any) moved to `~/.gnupg/scdaemon.conf.sign-with-gpg-backup`,
   canonical 4-line config (`disable-ccid` + `pcsc-shared` + `debug-level
   guru` + `log-file ~/tmp/scdaemon.log`) written in its place to avoid
   the scdaemon↔pcscd USB-interface conflict from the 2026-04-30 ceremony.
   Restored at exit.
5. **Public-key import (permanent).** If the signing-key fingerprint is
   not in the local pubring, fetches it from `keys.openpgp.org` via
   `gpg --keyserver hkps://keys.openpgp.org --recv-keys <fpr>`. NOT
   restored — the pubkey stays in the keyring (required for future
   verification operations; additive change with no operator data loss).
6. **Ownertrust ultimate (permanent).** Sets ownertrust on the imported
   key to `ultimate` via `gpg --import-ownertrust`. NOT restored —
   ownertrust persists in the keyring database (required for `gpg --verify`
   to treat sigs as fully trusted without warnings).
7. **Secret-key stub (permanent).** Runs `gpg --card-status` so gpg
   discovers the on-card private key + creates the secret-key stub
   linking it to the imported pubkey. NOT restored — the stub persists
   (required for future signing operations).

Steps 1-4 are transient (swap + restore). Steps 5-7 are keyring state
that's additive + needed for any future signing or verification op; the
library doesn't undo them.

### Stale-backup recovery

If the script crashes mid-run (or is SIGKILL'd), a `.sign-with-gpg-backup`
file may be left behind. The next invocation detects this:

- If the current config matches what the library would have written,
  the library auto-recovers (restores the backup, then proceeds normally).
- If the current config looks like operator-modified content rather than
  library-generated content, the library bails with an explicit message
  asking the operator to reconcile manually before re-running. (This
  guards against losing operator-authored changes that landed between
  the previous crashed run and the current one.)

## Common examples

### Sign a pinned manifest

```sh
bash scripts/sign-with-gpg.sh \
    --file intergen/data/models-manifest.json \
    --sha256 a7869c9a2d64d12bbb349bd24588ee02e7a61f1542458487297609c4bba74c36
# -> produces intergen/data/models-manifest.json.asc
```

### Sign a release tarball

```sh
bash scripts/sign-with-gpg.sh \
    --file build/intergenos-1.0.iso
# -> produces build/intergenos-1.0.iso.asc
```

### Sign with a different key

```sh
bash scripts/sign-with-gpg.sh \
    --file path/to/file \
    --key <40-hex-fingerprint-of-other-key>
```

### Validate first (dry-run)

```sh
bash scripts/sign-with-gpg.sh \
    --file path/to/file \
    --dry-run
# Runs pre-flight + library setup-init in DRY mode. No signing happens.
# Re-run without --dry-run to actually sign.
```

### Verbose tracing for diagnostics

```sh
bash scripts/sign-with-gpg.sh \
    --file path/to/file \
    --debug
# Verbose log lands at ~/tmp/sign-with-gpg.debug.log
# scdaemon log (Class A only) lands at ~/tmp/scdaemon.log
```

## Troubleshooting

### "Failed to fetch pubkey ... from keys.openpgp.org"

Network connectivity to `keys.openpgp.org` is required for first-time
setup on a fresh host. Workarounds:

- Confirm DNS + outbound HTTPS to `keys.openpgp.org` works
  (`curl -s https://keys.openpgp.org/ -o /dev/null && echo ok`)
- Manually import the pubkey from an existing host:
  `gpg --export <fpr>` on the source host, transfer the binary blob,
  `gpg --import < blob` on the target host. Then re-run the script.

### "Sig primary-key fingerprint mismatch"

The card produced a sig but the primary fingerprint at the end of the
`VALIDSIG` status line doesn't match `--key`. Means: the card you're
signing with isn't the one whose primary fingerprint matches `--key`.
Plug in the right card (or pass the correct `--key`).

### Terminal looks weird / cursor garbled after PIN entry

Most likely cause: `pinentry-curses` left the terminal in alternate-screen
mode. **The library should prevent this** by swapping in a temporary
`~/.gnupg/gpg-agent.conf` with `pinentry-program /usr/bin/pinentry-tty` for
the duration of every signing run. If you still see garbling:

- Confirm `/usr/bin/pinentry-tty` is installed (`ls -la /usr/bin/pinentry-tty`);
  if absent, install your distro's `pinentry-tty` package.
- Run `reset` to recover the current terminal.
- Check that the trap fired correctly: `ls -la ~/.gnupg/*.sign-with-gpg-backup`
  should be empty after a successful run. If a `.sign-with-gpg-backup` file
  is still there, the trap didn't restore — see "Stale-backup recovery"
  above.

### "gpg --card-status FAILED"

The smartcard isn't plugged in, OR the smartcard stack (pcscd or
scdaemon's built-in CCID) isn't talking to the hardware. Manual diagnose:

```sh
gpg --card-status     # should print card info
# If "No such device": replug the card, then retry
# If pcscd is installed: sudo systemctl status pcscd
# If pcscd is not installed: scdaemon should be using its built-in CCID
#   driver; no action needed beyond replug
```

### "Refusing to overwrite stale sig at ..."

Not a current failure mode — the script always removes any pre-existing
`.asc` at the output path before signing. If you want to preserve an old
sig, copy it elsewhere before re-running.

## Cross-references

- [docs/signing-procedure.md](signing-procedure.md) — the sbsign workflow
  for UEFI Secure Boot PE/COFF binaries. Different cryptographic stack
  (X.509 / PKCS#11 on NK#1 PIV slot 9c), different signing tool, different
  output format (signature embedded in the PE binary, not a sidecar).
- [docs/signing-key.md](signing-key.md) — canonical fingerprint
  publication + hardware token assignments.
- [scripts/lib-gpg-card-setup.sh](../scripts/lib-gpg-card-setup.sh) —
  sourceable library; the smartcard-setup logic this script uses.
- [scripts/sign-with-gpg.sh](../scripts/sign-with-gpg.sh) — the script
  itself; source is the canonical reference for behavior.

## Future signing scenarios

The script is target-agnostic by design. Anything that needs an OpenPGP
detached signature uses the same tool with different `--file` / `--out`
args. No new scripts needed per ceremony. Per-ceremony specifics (which
file, which sha256, which key) live in the operator's shell history or
in a ceremony-specific runbook, not in the universal signing tool.
