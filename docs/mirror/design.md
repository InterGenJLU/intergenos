# InterGenOS Package Mirror — v1.0 Design

**Status.** Design draft, awaiting owner ratification before any VPS-side
file lands. Authored against master `47329170`. The companion
artifacts are `scripts/mirror-publish.sh` (publish workflow) and
`docs/mirror/apache-userdata-snippet.conf` (cPanel-blessed vhost
include).

**Prerequisites that already exist.** `pkm/repo.py` already implements
the **client side** in full: index fetch + GPG verify + SHA256 verify
on download + chain-of-trust comments. The server-side gap is what
this design closes — what the VPS serves, where the publish workflow
lives, and how each new build's archives reach `https://<mirror>/.../`.

---

## 1 — Hostname and path

```
Mirror docroot (server-side):  /home/intergen/public_html/mirror/
Mirror URL (client-side):       https://intergenstudios.com/mirror/
```

The VPS (`origin.intergenstudios.com`) is KnownHost-managed cPanel/WHM
on a Linux 3.10 OpenVZ container. The cPanel account that owns the
`intergenstudios.com` document root is `intergen` (not `christopher` —
fleet-user `christopher` has read access via `~/agent-rules` etc. but
no write access to the `intergen` account's docroot). Mirror files
live under the existing `intergenstudios.com` vhost as a path-mounted
location; no DNS work, no new vhost.

**Rejected alternatives.**

- `https://repo.intergenos.org/` (currently in `pkm/repo.py:78` as the
  `DEFAULT_REPOS` URL). Would require a new domain registration + DNS
  setup + a fresh TLS cert. Adds infra risk for no functional gain
  over a path-mount on the existing vhost. **Action:**
  `pkm/repo.py:78` updates to point at `intergenstudios.com/mirror/`
  as part of landing this design.

- `https://mirror.intergenstudios.com/`. Requires a cPanel subdomain
  setup + an LE cert SAN expansion. Cleaner namespacing but more
  moving parts vs. path-mount.

---

## 2 — Layout under the mirror docroot

```
/home/intergen/public_html/mirror/
├── x86_64/
│   ├── current/                       # Atomic-promoted live snapshot
│   │   ├── InterGenOS.db              # Gzipped JSON index (pkm/repo.py format)
│   │   ├── InterGenOS.db.sig          # GPG detached signature
│   │   ├── <name>-<version>-<release>.igos.tar.gz
│   │   ├── <name>-<version>-<release>.igos.tar.gz.sig    # Optional per-archive sig
│   │   └── ...
│   ├── _staging-<UTC_ISO_TS>/         # Per-publish staging — promoted via mv
│   │   └── ...
│   └── _previous/                     # Last-known-good snapshots
│       ├── snapshot-<UTC_ISO_TS>/
│       └── ...
└── pubkey.asc                         # Master signing pubkey (FP 5597A3E0...)
```

**Why `current/` is a directory not a symlink.** A directory rename is
atomic on a single ext4 filesystem; a symlink swap is also atomic but
the swap window can race with mid-request reads on Apache + sendfile.
Renaming `current/` → `_previous/snapshot-<TS>/` and then renaming
`_staging-<TS>/` → `current/` is two atomic syscalls; clients in
flight either complete the prior snapshot or restart against the new
one. No partial-state visibility.

**Why per-archive `.sig` files in addition to the signed index.**
Defense in depth. The signed index is the primary integrity boundary;
the per-archive sigs let a paranoid client (or an out-of-band audit
script) verify any single archive without trusting the index. The
`pkm/repo.py` client only checks the index sig + per-package SHA256;
per-archive sigs are inert at install time but useful for
reproducibility checks and post-incident forensics.

---

## 3 — Trust model

**Integrity boundary: the GPG signature on `InterGenOS.db`.**

```
1. /etc/pkm/trusted.gpg ships with the OS (in the live ISO and the
   installed system). Contains the master pubkey:
     Fingerprint: 5597A3E0 587B2530 06D0DD7B 8C508261 82083050
     UID:         InterGenOS Project Signing Key (primary)
                  <intergenos-primary@intergenstudios.com>
2. Client downloads InterGenOS.db + InterGenOS.db.sig.
3. Client verifies the signature against /etc/pkm/trusted.gpg.
4. If valid: the index is authentic. Every per-package SHA256 in the
   index is now trusted.
5. Client downloads an individual archive, verifies SHA256 against
   the trusted index.
6. Install proceeds with verified bits.
```

**TLS is transport-only.** TLS provides encryption + opportunistic
authentication but is NOT the integrity boundary. A successful MitM
with a valid Let's Encrypt cert for `intergenstudios.com` cannot
forge the GPG signature on the index (master key lives offline on
NK#1 PIV slot 9c, never touches the VPS). The MitM can deny service
but cannot install untrusted bits.

**Cert pinning posture.** Not required for v1.0. Optionally
specifiable in the client config (see §6) as defense-in-depth. The
existing cPanel-managed Let's Encrypt cert rotates every ~60d
(R10→R11→R12 etc.); pinning the LE intermediate is fragile across
rotations, pinning the leaf is impossible across rotations. If
cert pinning is desired long-term, the right shape is pinning the
**Public Key Pin (HPKP-style)** of the master GPG public key, which
is already what the trust chain does — so we're back to "TLS is
transport-only."

**Clarification ask flagged for owner:** the dispatch brief mentioned
"the InterGenOS CA cert chain (we already have the cert from the
May-13 signing). Cert-pinned for archive integrity." I find no
record of a May-13 TLS-CA signing event — the May-5 ceremony
produced the master GPG signing key, not a TLS CA. If owner meant
the GPG master key, the design above already uses it. If owner
meant a separately-issued TLS CA cert, please point me at it.

---

## 4 — Publish workflow

A single script — `scripts/mirror-publish.sh` — runs on the
build-VM-or-host that produced the archives. It does NOT have write
access to the live mirror docroot; it stages to a directory owned by
the fleet-user `christopher` on the VPS, prints a one-line atomic
promote command that the VPS-admin agent (with `intergen`-account
write perms via WHM root) runs to flip live.

**Steps:**

1. **Pre-checks.** Master GPG key available (locally or via NK#1
   PIV slot 9c); ssh access to `christopher@origin.intergenstudios.com:2200`;
   archives dir exists and is non-empty.
2. **Generate index.** `python3 -c "from pkm.repo import generate_index;
   generate_index('<archives_dir>')"`. Writes `InterGenOS.db`.
3. **Sign index.** `gpg --detach-sign --armor --output InterGenOS.db.sig
   --local-user 5597A3E0587B253006D0DD7B8C50826182083050 InterGenOS.db`.
   For the v1.0 ceremony-built variant: key lives on NK#1 PIV slot 9c
   so this invocation prompts for the YubiKey PIN + touch.
4. **Stage to VPS.** `rsync -av --delete <local_staging>/
   <fleet-user>@origin.intergenstudios.com:/home/<fleet-user>/mirror-staging/<TS>/`.
   Fleet-user owns `/home/<fleet-user>/mirror-staging/`; the VPS-admin
   agent has read access via being root.
5. **Print promote command.** Echoes the exact two `mv` commands the
   VPS-admin agent runs as `intergen` (via WHM root → su) to
   atomic-promote.

**VPS-admin-side promote (out of script scope, single one-liner):**

```bash
# Run as root on VPS, su to intergen for the moves:
sudo -u intergen bash -c '
  cd /home/intergen/public_html/mirror/x86_64 &&
  mv current _previous/snapshot-2026-05-16T07-00-00Z &&
  mv /home/<fleet-user>/mirror-staging/2026-05-16T07-00-00Z/x86_64 current
'
```

The `_previous/` directory retains the last 5 snapshots; older ones
auto-rotate via a small cron job (out of scope for v1.0 — owner
ratifies retention policy first).

**No httpd restart needed.** Apache serves files directly from disk;
the rename is observed on next request.

---

## 5 — Apache vhost (cPanel-userdata pattern)

The shipped snippet at `docs/mirror/apache-userdata-snippet.conf` is
designed to be dropped into:

```
/etc/apache2/conf.d/userdata/std/2_4/intergen/intergenstudios.com/mirror_pkm.conf
/etc/apache2/conf.d/userdata/ssl/2_4/intergen/intergenstudios.com/mirror_pkm.conf
```

Per the existing `mcp_proxy.conf` + `security_headers.conf` pattern
already in place. After dropping the files, the VPS-admin agent runs:

```
/usr/local/cpanel/scripts/rebuildhttpdconf
systemctl restart httpd
```

(Same procedure documented in `mcp_proxy.conf` headers — survives
EasyApache rebuilds.)

The snippet:

- `<Location /mirror>` serves files from `/home/intergen/public_html/mirror/`
  (cPanel auto-maps `https://intergenstudios.com/<path>` to
  `/home/intergen/public_html/<path>`).
- Disables PHP execution under `/mirror/` (defense in depth — these
  are downloadable archives, never executable web content).
- Sets `Content-Type: application/octet-stream` on `.igos.tar.gz` so
  browsers download rather than try to interpret.
- Sets `Cache-Control: public, max-age=604800` for archives (a week —
  archive contents are content-addressed by name+version+sha so they
  never change in place) and `no-cache` for `InterGenOS.db` (index
  changes on every publish).
- Sets `Header always set Cross-Origin-Resource-Policy: cross-origin`
  so other-origin clients (curl/wget from a freshly installed system)
  can fetch without CORS friction.
- Disables symlink following with `Options -FollowSymLinks` for the
  served tree (the `current/` directory is a regular dir, not a symlink
  — but defense-in-depth against future drift).
- `Header always set Content-Security-Policy "default-src 'none'"`
  (override the existing site-wide CSP that allows 'self' scripts —
  /mirror/ should never serve script content).

Listening is on port 443 only — the `std` (port 80) variant of the
snippet redirects `/mirror/*` → `https://<host>/mirror/*` so a
client that mistakenly fetches over plaintext gets bounced to TLS.

---

## 6 — Client-side config schema (don't ship yet)

The future `/etc/pkm/repos.d/intergenos-mirror.conf` (or a single
`/etc/pkm/repos.conf` file with multiple sections — TBD by owner).
Drafted shape:

```ini
[intergenos]
url = https://intergenstudios.com/mirror/x86_64/current
enabled = true
priority = 100
signing_key_fingerprint = 5597A3E0587B253006D0DD7B8C50826182083050
default_branch = stable
# Optional defense-in-depth pin (cert SPKI sha256, base64-encoded):
# cert_spki_pin = <base64-sha256>
# Optional cert-chain CA bundle (PEM); falls back to system trust if absent:
# ca_bundle = /etc/ssl/certs/ca-bundle.crt
```

Mapped to `pkm/repo.py:76` `DEFAULT_REPOS` dict; the `signing_key_fingerprint`
field is new (must be added — currently the code uses
`GPG_KEYRING` as the trust source; this lets a single keyring host
multiple-repo trust with per-repo enforcement).

**Branches.** `default_branch = stable` is reserved for future use:
once we have a `testing/` snapshot alongside `current/`, the client
picks based on this field. v1.0 ships `current/` only.

---

## 7 — Holy Grail considerations

- **No third-party CAs in the integrity boundary.** The TLS chain
  uses Let's Encrypt because the practical alternative is "ship a
  trust anchor in the OS for a CA that signs our own TLS cert," which
  buys nothing because TLS is transport-only. The integrity boundary
  stays on the GPG master key, which is offline-held and signed in
  ceremony.
- **No standing third-party access.** The publish workflow does not
  install any SSH key, agent, or webhook on the VPS that isn't
  already part of the documented fleet posture. Fleet-user
  `christopher` already has SSH access via `agent-rules`; the
  VPS-admin agent has
  root via the WHM/cPanel admin channel. No new access grants are
  required.
- **Verifiable by the user.** Every archive's path, sha256, and sig
  is visible in `InterGenOS.db`; a user with `gpg`, `curl`, and
  `sha256sum` can reproduce the verification chain by hand. There
  are no opaque steps.
- **Bundle from canonical, not installed.** Per
  `feedback_bundle_from_canonical_not_installed`, the publish script
  takes archives from a build-output directory (the canonical
  source), not from `/usr/lib/igos-archives/` on a developer box.
  The build VM is the canonical producer.

---

## 8 — Open questions for owner ratification

1. **Hostname/path.** Ratify path-mount on `intergenstudios.com/mirror/`?
   Or prefer `mirror.intergenstudios.com/` subdomain (more cPanel setup)?
   Or stand up `repo.intergenos.org/` (new domain)?
2. **The "May-13 CA cert" reference.** Is there a TLS-CA cert I'm
   missing? Or does owner mean the May-5 master GPG key?
3. **Cert pinning.** Required for v1.0 or deferred? My current design
   says deferred (TLS is transport-only).
4. **`_previous/` retention.** How many snapshots back to keep? My
   default is 5; ratify or set a different number.
5. **`/etc/pkm/repos.d/` vs single `repos.conf`.** Drop-in dir
   (multi-file, conventional) or single file (simpler)?
6. **Branches.** Does v1.0 need `testing/` alongside `current/`, or is
   `current/` only sufficient?

---

Last updated: 2026-05-16 against master `47329170`.
