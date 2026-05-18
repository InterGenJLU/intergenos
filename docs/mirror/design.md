# InterGenOS Public Binary Mirror — v1.0 Design

**Status.** Hostname + docroot + TLS + access infrastructure are **live and ratified**.
Build-pipeline emission of per-package archives, signed-index generation,
and first publish are the remaining v1.0-launch work.

This document describes what's on disk + on the VPS + in the canonical
publish script. It is not a proposal awaiting ratification — the
fundamental decisions (which hostname, which cPanel account, which docroot,
which SSH coordinates) were ratified day-0 and the artifacts to back them
exist. Subsequent decisions about layout details and atomic-promote
semantics align with what [`scripts/publish-repo.sh`](../../scripts/publish-repo.sh)
already implements.

**Canonical companion artifacts.**

- [`scripts/publish-repo.sh`](../../scripts/publish-repo.sh) — the canonical
  publish workflow. Codes the ratified hostname, the ratified docroot, the
  ratified SSH target, and the ratified signing-key topology. Wired into
  [`scripts/build-intergenos.sh`](../../scripts/build-intergenos.sh) as the
  publish phase.
- [`pkm/repo.py`](../../pkm/repo.py) — the client-side fetch + GPG verify +
  SHA256 verify implementation. `DEFAULT_REPOS["intergenos"]["url"]` codes
  the ratified hostname.
- [`packages/core/pkm/build.sh`](../../packages/core/pkm/build.sh) — the
  installed-system `/etc/pkm/repos.conf` writer. Codes the ratified hostname.

---

## 1 — Hostname, docroot, and access (RATIFIED 2026-05-11)

```
Mirror docroot (server-side):  /home/intergenos/repo/x86_64/
Mirror URL (client-side):      https://repo.intergenos.org/x86_64/current/
Publish SSH target:            intergenos@origin.intergenstudios.com -p 2200
```

**Domain.** `intergenos.org` is secured at the Registrar for five years
(registered 2026-05-11; expiry beyond v1.0 ship + the full v1.x release
cadence). `repo.intergenos.org` is the public binary mirror's subdomain.

**Underlying VPS.** `origin.intergenstudios.com` — KnownHost-managed
cPanel/WHM container. The `intergenos` cPanel account (distinct from the
`intergen` account that owns the `intergenstudios.com` document root,
and distinct from the `christopher` admin account) owns the
`repo.intergenos.org` subdomain's docroot.

**DNS.** `repo.intergenos.org` → `162.255.162.237`, TTL 7199s,
cPanel-as-authoritative on `origin.intergenstudios.com`.

**TLS.** Let's Encrypt R12 cert via cPanel AutoSSL.
`CN = www.repo.intergenos.org`, notAfter Aug 9 2026, auto-renewing.
TLS is transport-only — see §3 for the trust model.

**Publish access.** SSH-key auth as the `intergenos` cPanel user:

```
ssh -p 2200 intergenos@origin.intergenstudios.com
```

The build-system coordinator's `ed25519` pubkey is installed in
`~intergenos/.ssh/authorized_keys`. The publish script
([`scripts/publish-repo.sh`](../../scripts/publish-repo.sh)) writes
directly into the docroot under this account — no fleet-user staging
hop, no VPS-root step, no cPanel UI intervention.

**Closure record.** DNS, TLS, SSH access, docroot layout, and placeholder
index landed 2026-05-11 (TRACKER §E1.B.1–E1.B.4). The hostname decision
itself was made day-0 (operator-direct) — `pkm/repo.py`'s `DEFAULT_REPOS`
already codes it on master, and `docs/repository-trust.md` references
it as the integrity-chain endpoint.

---

## 2 — Layout under the mirror docroot

```
/home/intergenos/repo/x86_64/
├── current/                              # Symlink → live staging snapshot
├── _staging-<UTC_ISO_TS>/                # Per-publish dir; promoted via symlink swap
│   ├── InterGenOS.db                     # Gzipped JSON index (pkm/repo.py format)
│   ├── InterGenOS.db.sig                 # GPG detached signature
│   └── <name>-<version>-<release>.igos.tar.gz
└── _previous/                            # Archived snapshots
    └── <dir-name>-prev-<UTC_ISO_TS>/
```

**Atomic-promote pattern: symlink-swap.** The `current/` entry is a
**symlink** that points at the most recently published `_staging-<TS>/`
directory. Promotion is a `ln -sfn` + `mv -T` of a temporary symlink
over the existing `current/`, which is a single atomic syscall on
ext4. The old `_staging-<TS>/` directory is moved into `_previous/`
under a `prev-<TS>` suffix only *after* the swap, so clients in flight
either complete reads against the prior target (still present at its
prior path) or restart against the new target. No partial-state
visibility, no 404 window.

**Why symlink-swap and not directory-rename.** The symlink target is
the staging dir, which stays where it was placed by rsync — the swap
is a single inode update. A directory rename would also be atomic on a
single ext4 filesystem, but symlink-swap composes with the rsync→stage
→promote workflow without requiring the staging dir to land at the
final path before promotion. The canonical script
([`scripts/publish-repo.sh`](../../scripts/publish-repo.sh) lines
~155–195) implements this pattern.

**Per-archive `.sig` files: deferred to v1.1+.** The 2026-05-12
multi-vantage RFC (closure commit `d6b3946a`,
[`docs/architecture/per-archive-sig-decision.md`](../architecture/per-archive-sig-decision.md))
ratified signed-index-only for v1.0. Per-archive sigs are listed there
as a v1.1+ defense-in-depth augmentation with documented trigger
conditions. No `.tar.gz.sig` files are emitted by the v1.0 publish
script; this design doc previously listed them in the layout — that
was drift from the per-archive-sig RFC and is corrected here.

**`_previous/` retention.** Five most recent snapshots retained;
older ones auto-rotated by a small VPS-side cron job. Retention policy
is operator-tunable post-v1.0 launch if usage data motivates a change.

---

## 3 — Trust model

**Integrity boundary: the GPG signature on `InterGenOS.db`.**

```
1. /etc/pkm/trusted.gpg ships with the OS (in the live ISO and the
   installed system). Contains the InterGenOS release-signing topology:
     Master FP:    5597A3E0 587B2530 06D0DD7B 8C508261 82083050
                   (certifies subkeys; NEVER signs release artifacts)
     Subkey [S1]:  D7AA641D 81ACD690 C5AD865E 7276E14D D8886BFE
                   (on hardware token NK#1; signs release indexes)
     Subkey [S2]:  81DD223F 9BA9B3F2 AFBFFC5A FA24B042 975F775E
                   (on hardware token NK#2; backup signer)
2. Client downloads InterGenOS.db + InterGenOS.db.sig.
3. Client verifies the signature against /etc/pkm/trusted.gpg
   (signature is from [S1] or [S2], both subkeys certified by the master).
4. If valid: the index is authentic. Every per-package SHA256 in the
   index is now trusted.
5. Client downloads an individual archive, verifies SHA256 against
   the trusted index.
6. Install proceeds with verified bits.
```

The master GPG key lives offline on NK#1 PIV slot 9c, never touches
the VPS, and never signs release indexes directly. Subkey [S1] (on NK#1)
is the primary release signer; [S2] (on NK#2) is the backup. This
topology is ratified at
[`docs/signing-key.md`](../signing-key.md) and the 2026-05-05 ceremony
closure record.

**TLS is transport-only.** TLS provides encryption + opportunistic
authentication but is NOT the integrity boundary. A successful MitM
with a valid Let's Encrypt cert for `repo.intergenos.org` cannot forge
a GPG signature on the index — the signing material is on hardware
tokens, not on the VPS. The MitM can deny service but cannot install
untrusted bits.

**Cert pinning: not required for v1.0.** The integrity-chain anchor is
the GPG master pubkey, shipped in `/etc/pkm/trusted.gpg`. TLS cert
rotation (Let's Encrypt every ~60d) does not affect trust.

---

## 4 — Publish workflow

The canonical publish script is [`scripts/publish-repo.sh`](../../scripts/publish-repo.sh).
It runs on the build-VM (or any host with the build's `.igos.tar.gz`
archives + the release signing key available via the hardware token)
and is wired into [`scripts/build-intergenos.sh`](../../scripts/build-intergenos.sh)
as the publish phase.

**Steps:**

1. **Pre-checks.** Master GPG keyring available; release subkey ([S1]
   on NK#1 by default; [S2] on NK#2 via `--gpg-key S2`); SSH access to
   `intergenos@origin.intergenstudios.com:2200`; archives dir exists
   and is non-empty.
2. **Generate index.** Calls `pkm.repo.generate_index(<archives_dir>)`
   to produce `InterGenOS.db` (gzipped JSON in the format
   `pkm/repo.py` parses).
3. **Sign index.** `gpg --detach-sign --armor --output InterGenOS.db.sig
   --local-user <SUBKEY_FP> InterGenOS.db`. The hardware token prompts
   for PIN + touch on the operator's workstation.
4. **Rsync staged tree to VPS.** Stages into a per-publish
   `_staging-<TS>/` directory directly under
   `/home/intergenos/repo/x86_64/` on the VPS. No fleet-user hop, no
   root step.
5. **Atomic promote.** `ln -sfn` + `mv -T` to swap the `current/`
   symlink to the new staging dir; archive the prior target to
   `_previous/`.

**No httpd restart needed.** Apache serves files directly from disk;
the symlink update is observed on the next request.

**E1.B.5 / E1.B.6 / E1.B.7 (remaining v1.0 work).** Per the project
tracker (§E1.B): (i) the build pipeline must emit per-package
`.igos.tar.gz` archives into `/var/lib/igos/archives/` (E1.B.5);
(ii) the index-gen step in `publish-repo.sh` must be exercised against
a real build's archive set, including the signing prompt path (E1.B.6);
(iii) first publish + end-to-end `pkm sync` smoke test against the live
mirror (E1.B.7). All three are wiring work, not design work.

---

## 5 — Client-side config

The OS ships `/etc/pkm/repos.conf` via
[`packages/core/pkm/build.sh`](../../packages/core/pkm/build.sh):

```ini
[intergenos-current]
url = https://repo.intergenos.org/x86_64/current/
enabled = true
# gpg_verify = true — enable once the signing-key ceremony completes
```

The runtime `pkm/repo.py` `DEFAULT_REPOS` dict codes the same URL,
so a missing or unreadable `/etc/pkm/repos.conf` falls back to a
functional default rather than a broken one.

`gpg_verify = true` flips on once the first signed publish lands —
shipping with verify on against an empty mirror would block the first
`pkm sync` on a fresh install before E1.B.7 completes. The flag is
the v1.0-day-of-publish switch.

**Future config schema (v1.x).** Per-repo signing-key-fingerprint
field, opt-in `testing/` channel alongside `current/`, optional
TLS-SPKI pin as defense-in-depth. None ship in v1.0.

---

## 6 — Holy Grail considerations

- **No third-party CAs in the integrity boundary.** TLS uses Let's
  Encrypt because the practical alternative is shipping a project-owned
  CA trust anchor in the OS for a CA that signs our own TLS cert —
  which buys nothing because TLS is transport-only. The integrity
  boundary is the GPG signing topology, anchored on hardware-token
  subkeys certified by an offline-held master.
- **No standing third-party access to the publish path.** The publish
  workflow uses the build-system coordinator's `ed25519` pubkey
  installed under the `intergenos` cPanel account. No webhooks, no
  CI runners with mirror-write access, no SaaS in the loop. The
  signing material stays on hardware tokens at the operator's
  workstation.
- **Verifiable by the user.** Every archive's path, SHA256, and
  ultimately the index signature are reproducible by hand with `curl`,
  `gpg`, and `sha256sum` against the ratified master pubkey. There
  are no opaque steps.
- **Bundle from canonical, not installed.** The publish script takes
  archives from the build-output directory (the canonical producer),
  not from `/var/lib/igos/archives/` on a developer box.

---

## 7 — Provenance + cross-references

- **Hostname + infra ratified** day-0; closure of E1.B.1–E1.B.4
  landed 2026-05-11. Build-system coordinator handled DNS + LE cert
  + SSH + docroot in a single owner+coordinator session.
- **This document was rewritten 2026-05-18** to replace an earlier
  draft (2026-05-16) that had been authored against a rejected
  hostname (`intergenstudios.com/mirror/` path-mount). The earlier
  draft did not scan the 2026-05-11 ratification or the on-master
  `pkm/repo.py` / `publish-repo.sh` artifacts before authoring;
  rewriting against the ratified state is the corrective sweep
  closing audit findings L-003, L-004, L-005, L-006, L-009, L-011,
  K-012, and remediation-plan item #23.
- **Companion docs:** [`docs/signing-key.md`](../signing-key.md)
  (signing topology), [`docs/architecture/per-archive-sig-decision.md`](../architecture/per-archive-sig-decision.md)
  (signed-index-only ratification, 2026-05-12),
  [`docs/users/security-defaults.md`](../users/security-defaults.md)
  (user-facing trust posture).
