# InterGenOS Signing-Key Ceremony — Automation

This directory holds the automation that produces the InterGenOS distro release-signing keys (PGP master + four sign subkeys + EFI X.509 PIV slot 9c vendor cert) inside an air-gapped Tails session.

## Files

| File | Role |
|---|---|
| `bootstrap.sh` | Entry point. Installs the offline-debs bundle, then execs `ceremony.py`. |
| `ceremony.py` | Main automation (~2,200 LOC). Stage-structured, idempotent, resumable via `--from-stage N`. |
| `validate.py` | Ship gate. Five validation sections; the ceremony is "done" only when all pass with 0 failures. |

## Why this is in the public repo

This is a **reference implementation**. It is published for transparency: shim-review reviewers, security researchers, and anyone trusting an InterGenOS-signed release should be able to read exactly how the trust-anchor keys are produced and how the attestation chain is rooted in this code.

For the reviewer-facing summary of what these scripts do and why, see [`docs/ceremony/signing-key-ceremony-procedure.md`](../../docs/ceremony/signing-key-ceremony-procedure.md). For the post-mortem covering the 24+ ratified bug fixes that informed the defensive guards in `ceremony.py`, see [`docs/research/ceremony/lessons-learned-2026-05-05.md`](../../docs/research/ceremony/lessons-learned-2026-05-05.md).

## Maintenance status

**Reference implementation. Not actively maintained between rotations.**

The InterGenOS subkey rotation cadence is two years (next rotation 2028-05-04, or earlier on compromise). This script is only run during a rotation. Issues filed against it between rotations are triaged at the next rotation cycle. Pull requests are held for the v3 refactor sprint (the lessons-learned doc enumerates the v3 backlog).

If you find a security issue in this code, please report it via [`SECURITY.md`](../../SECURITY.md), not as a public issue.

## Do not run this on your own machine

This script is designed for the air-gapped Tails environment described in the procedure doc, with the production Nitrokey 3 hardware tokens. Running it without that environment will, at best, do nothing useful, and may factory-reset Nitrokeys in surprising ways. There is no "demo mode."

If you are studying the script:
- `python3 -m py_compile ceremony.py validate.py` confirms syntactic validity.
- The stage list is at the bottom of `ceremony.py` (`--from-stage` argparse handler).
- `validate.py` reads as the spec for "what does a successfully-completed ceremony look like."

## Development methodology

This automation was developed in a Docker container of Tails on the maintainer workstation, with the four production Nitrokeys connected for live validation. Each stage was iterated, debugged, and validated against real hardware in that container until `validate.py` returned the 0-failures verdict. Only then was the validated script transferred to Drive #2 and run inside the actual air-gapped Tails session.

The dev container minimizes the risk window during the live ceremony in two ways. First, the air-gapped session is not where bugs get discovered — bugs get discovered in the dev container, in advance, where iteration is cheap. Second, the live ceremony is correspondingly short: the air-gap window only needs to cover the actual key-generating run plus validation, not the multi-hour debugging that necessarily attends a first-time procedure.

The development environment is not part of the trust-anchor chain — it never holds production keys, only ephemeral dev-Nitrokey state used to validate the script's logic. The trust-anchor chain begins in the air-gapped Tails session running `ceremony.py` against the production Nitrokeys.

## License

Licensed under the GNU General Public License, version 3 or later (`GPL-3.0-or-later`). See [`LICENSE`](../../LICENSE) at the repo root.
