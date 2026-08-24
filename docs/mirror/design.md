# InterGenOS Public Binary Mirror — v1.0 Design

**Status.** Live and publishing. `repo.intergenos.org/x86_64/current/` serves a
signed `InterGenOS.db` index, per-package `.igos.tar.gz` archives, and the GPL
`sources/` tree, emitted by the build pipeline's publish phase. The remaining
v1.0 work is full package coverage across the archive set.

This document describes the mirror as it exists on disk, on the VPS, and in
the canonical publish script. The fundamental decisions — hostname, cPanel
account, docroot, and SSH coordinates — are settled, and the artifacts that
implement them already exist on the `master` branch. The layout details and
atomic-promote semantics described here match what
[`scripts/publish-repo.sh`](../../scripts/publish-repo.sh) implements.

**Canonical companion artifacts.**

- [`scripts/publish-repo.sh`](../../scripts/publish-repo.sh) — the canonical
  publish workflow. Encodes the hostname, docroot, SSH target, and
  signing-key topology. Wired into
  [`scripts/build-intergenos.sh`](../../scripts/build-intergenos.sh) as the
  publish phase.
- [`pkm/repo.py`](../../pkm/repo.py) — the client-side fetch, GPG-verify, and
  SHA256-verify implementation. Its `DEFAULT_REPOS` dictionary encodes the
  mirror URL.
- [`packages/core/pkm/build.sh`](../../packages/core/pkm/build.sh) — the
  installed-system `/etc/pkm/repos.conf` writer. Encodes the mirror URL.

---

## 1 — Hostname, docroot, and access

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

**DNS.** `repo.intergenos.org` resolves to the mirror host, TTL 7199s,
cPanel-as-authoritative on `origin.intergenstudios.com`. The address itself
is deliberately not written here: it is a routable address for a machine
this document's readers reach by name, so the name is what the document
should carry. Resolve it if you need the literal.

**TLS.** Let's Encrypt R12 cert via cPanel AutoSSL.
`CN = www.repo.intergenos.org`, notAfter Aug 9 2026, auto-renewing.
TLS is transport-only — see §3 for the trust model.

**Publish access.** SSH-key authentication as the `intergenos` cPanel user:

```
ssh -p 2200 intergenos@origin.intergenstudios.com
```

A dedicated `ed25519` publish key is installed in
`~intergenos/.ssh/authorized_keys`. The publish script
([`scripts/publish-repo.sh`](../../scripts/publish-repo.sh)) writes
directly into the docroot under this account, with no intermediate staging
account, no VPS-root step, and no cPanel UI intervention.

**Provenance.** DNS, TLS, SSH access, the docroot layout, and the
placeholder index were stood up on 2026-05-11. `pkm/repo.py`'s
`DEFAULT_REPOS` already encodes the hostname on the `master` branch, and
[`docs/repository-trust.md`](../repository-trust.md) references it as the
integrity-chain endpoint.

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
the staging directory, which stays where rsync placed it; the swap is a
single inode update. A directory rename would also be atomic on a single
ext4 filesystem, but symlink-swap composes cleanly with the
rsync → stage → promote workflow without requiring the staging directory
to land at its final path before promotion. The promote step in
[`scripts/publish-repo.sh`](../../scripts/publish-repo.sh) implements this
pattern.

**Per-archive `.sig` files: a later defense-in-depth augmentation.** The
v1.0 mirror uses a signed index only;
[`docs/architecture/per-archive-sig-decision.md`](../architecture/per-archive-sig-decision.md)
records this decision and lists per-archive signatures as a future
defense-in-depth augmentation with documented trigger conditions. The
v1.0 publish script emits no `.tar.gz.sig` files.

**`_previous/` retention.** The promote step in
[`scripts/publish-repo.sh`](../../scripts/publish-repo.sh) prunes `_previous/`
immediately after the symlink swap, retaining the most recent
`--keep-previous N` generations (**default 1**). There is no server-side cron
job involved — retention is part of the publish transaction, so a publish can
never leave the volume in an unpruned state. Each retained generation costs
roughly a full unshared copy of the source tree, which is why the default is
low; `--keep-previous 0` disables retention entirely. The prune acts only
inside `_previous/`, only on entries matching the archive-snapshot naming
shape, and never on whatever `current/` resolves to.

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

The master GPG key is held offline, never touches the VPS, and never
signs release indexes directly. Subkey [S1] (on hardware token NK#1) is
the primary release signer; [S2] (on hardware token NK#2) is the backup.
This topology is documented in
[`docs/signing-key.md`](../signing-key.md).

**TLS is transport-only.** TLS provides encryption and opportunistic
authentication but is not the integrity boundary. A successful MitM
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
It runs on the build VM (or any host that has the build's `.igos.tar.gz`
archives and the release signing key available via the hardware token)
and is wired into [`scripts/build-intergenos.sh`](../../scripts/build-intergenos.sh)
as the publish phase.

**Steps:**

1. **Pre-checks.** The master GPG keyring is available; the release
   subkey is present ([S1] on NK#1 by default, or [S2] on NK#2 via
   `--gpg-key NK2`); SSH access to
   `intergenos@origin.intergenstudios.com:2200` works; and the archives
   directory exists and is non-empty.
2. **Release-monotonicity gate.** Every staged archive whose bytes differ
   from the live `current/` entry must be strictly newer in
   `(version, release)`. A same-version republish that did not bump
   `release:` would be invisible to `pkm upgrade` on every installed
   system while silently overwriting bytes clients already trust, so the
   publish aborts before the signing ceremony rather than shipping it.
   Skipped under `--skip-sign`, which reuses an already-vetted index.
3. **Generate index.** Calls `pkm.repo.generate_index(<archives_dir>)`
   to produce `InterGenOS.db` (gzipped JSON in the format
   `pkm/repo.py` parses).
4. **Sign index.** `gpg --detach-sign --armor --output InterGenOS.db.sig
   --local-user <SUBKEY_FP> InterGenOS.db`. The hardware token prompts
   for its PIN and touch confirmation.
5. **Capacity preflight.** Projects new-versus-hardlinkable bytes against
   the remote's free space and fails **closed** if the post-publish free
   space would drop below `--min-free-pct` (default 25%). This turns a
   step that used to be a human pre-check into an enforced gate;
   `--accept-capacity-risk` is the explicit override.
6. **Rsync staged tree to VPS, incrementally.** Stages into a per-publish
   `_staging-<TS>/` directory directly under
   `/home/intergenos/repo/x86_64/` on the VPS, with no intermediate
   account and no root step. The transfer is content-addressed and
   hardlinks against every snapshot already on the volume — `current/`,
   each `_previous/` generation, and any leftover staging directory — so
   only genuinely new bytes cross the wire.
7. **Atomic promote.** `ln -sfn` + `mv -T` to swap the `current/`
   symlink to the new staging directory, then archive the prior target
   to `_previous/` and prune that directory to the retention limit (§2).
8. **Transparency-log append.** The signed index is appended to the
   append-only transparency log. Fail-closed by default;
   `--skip-transparency` is an emergency override.

**No httpd restart needed.** Apache serves files directly from disk; the
symlink update is observed on the next request.

**Status of the v1.0 wiring.** All of it has landed and been exercised
end to end: the build pipeline emits per-package `.igos.tar.gz` archives,
`publish-repo.sh` has been run against real build archive sets including
the hardware-token signing prompt, and the mirror has been published and
client-validated with `pkm sync` against the live URL. The remaining v1.0
work is package-coverage breadth across the archive set, not wiring.

---

## 5 — Client-side config

The OS ships `/etc/pkm/repos.conf` via
[`packages/core/pkm/build.sh`](../../packages/core/pkm/build.sh):

```ini
[intergenos-current]
url = https://repo.intergenos.org/x86_64/current/
enabled = true
# gpg_verify = true — optional; verification is mandatory, only true is accepted
```

The runtime `pkm/repo.py` `DEFAULT_REPOS` dictionary encodes the same
URL, so a missing or unreadable `/etc/pkm/repos.conf` falls back to a
functional default rather than a broken one.

Repo-index signature verification is **mandatory** and unconditionally on
(the first signed publish has long since landed). `gpg_verify` may be
omitted (default on) or set `true`; an explicit `gpg_verify = false` is
**refused** at config load (PKM-A21) rather than silently verifying anyway
and contradicting the user — a security knob that silently does nothing is
worse than no knob.

**Planned config schema.** The v1.x roadmap includes a per-repo
signing-key-fingerprint field, an opt-in `testing/` channel alongside
`current/`, and an optional TLS-SPKI pin as defense-in-depth. None of
these ship in v1.0.

---

## 6 — Security posture

Security is not first. It is only. The mirror design reflects that posture:

- **No third-party CAs in the integrity boundary.** TLS uses Let's
  Encrypt because the only alternative — shipping a project-owned CA
  trust anchor in the OS for a CA that signs our own TLS certificate —
  buys nothing, since TLS is transport-only. The integrity boundary is
  the GPG signing topology, anchored on hardware-token subkeys certified
  by an offline-held master.
- **No standing third-party access to the publish path.** The publish
  workflow uses a dedicated `ed25519` key installed under the
  `intergenos` cPanel account. There are no webhooks, no CI runners with
  mirror-write access, and no SaaS in the loop. The signing material
  stays on hardware tokens.
- **Verifiable by the user.** Every archive's path, every SHA256, and the
  index signature itself are reproducible by hand with `curl`, `gpg`, and
  `sha256sum` against the published master public key. There are no
  opaque steps.
- **Published from the canonical source, not from an installed system.**
  The publish script takes archives from the build-output directory — the
  canonical producer — not from `/var/lib/igos/archives/` on an installed
  machine.

---

## 7 — Cross-references

- **Hostname and infrastructure** were established on 2026-05-11: DNS,
  the Let's Encrypt certificate, SSH access, and the docroot.
- **Companion docs:** [`docs/signing-key.md`](../signing-key.md)
  (signing topology),
  [`docs/architecture/per-archive-sig-decision.md`](../architecture/per-archive-sig-decision.md)
  (the signed-index-only decision),
  [`docs/repository-trust.md`](../repository-trust.md)
  (integrity-chain endpoint), and
  [`docs/users/security-defaults.md`](../users/security-defaults.md)
  (user-facing trust posture).
