# InterGenOS — Corresponding Source Availability

InterGenOS ships hundreds of binary packages built from source-available
upstream projects. Many of those projects carry copyleft licenses
(GPL-2.0, GPL-3.0, LGPL-2.1, LGPL-3.0, MPL-2.0, AGPL-3.0, EPL-1.0)
that obligate the distributor to provide the corresponding source code
on terms the recipient can rely on.

This document is the InterGenOS project's binding commitment to that
obligation. It satisfies the source-availability requirements of the
GNU General Public License v2 §3, v3 §6, and equivalent terms in the
LGPL, AGPL, MPL, and EPL.

**The same commitment applies regardless of how you received InterGenOS**
— direct download of a release ISO, a copy from a third party, an
installed system you inherited, or any future redistribution channel.

---

## 1. Two-channel commitment

InterGenOS satisfies its source-availability obligations through two
independent channels. **Either channel is sufficient** under the
applicable licenses; both are offered so the recipient may choose.

### Channel A — Network access (GPL v3 §6d, GPL v2 §3 equivalent)

The corresponding source for every binary InterGenOS publishes is
available at no charge from the project's public source mirror:

```
https://repo.intergenos.org/x86_64/current/sources/
```

The mirror is operated by the InterGenOS project. The hostname,
infrastructure, and access topology are documented at
[`docs/mirror/design.md`](docs/mirror/design.md). The mirror is
designed to remain available alongside, and for at least as long
as, the corresponding binary distribution under `current/`.

Source archives are named to match their binary counterparts:

```
binary archive:  <name>-<version>-<release>.igos.tar.gz
source archive:  <name>-<version>-<release>.igos.src.tar.gz
```

Each source archive contains everything needed to reproduce the binary
build (see §3 below).

### Channel B — Written offer (GPL v2 §3, GPL v3 §6b)

If you cannot use the network mirror — for any reason, including
intermittent access, mirror downtime, jurisdictional restrictions, or
simply preference — InterGenJLU hereby makes the following offer,
valid for **three (3) years** from the date you received this copy of
InterGenOS:

> **InterGenJLU offers to provide, to any third party in possession
> of a copy of any InterGenOS release, a complete machine-readable
> copy of the corresponding source for any binary contained in that
> release, on a physical storage medium customarily used for software
> interchange, for a price no more than the cost of physically
> performing the source distribution.**

To request source under this offer, write to:

```
InterGenJLU
Attn: GPL Source Request
[postal address available on request via legal@intergenos.org]
```

or email **legal@intergenos.org** with the subject line
`GPL Source Request` and include:

1. The release identifier of the InterGenOS copy you received
   (visible in `/etc/intergenos-release` on an installed system, or
   in the ISO filename, e.g. `intergenos-1.0-stable.iso`)
2. The package names whose source you are requesting, OR the keyword
   `ALL` to request the complete source set for that release
3. A return postal address if you want physical media (otherwise we
   may direct you to a download link)

We will acknowledge receipt within 10 business days and dispatch the
source within a reasonable period thereafter. We may charge no more
than the actual cost of media, packaging, and shipping; we will quote
that cost in our acknowledgment and require confirmation before
shipping.

---

## 2. What you receive

For every package that contains software whose license requires
source distribution, "corresponding source" under InterGenOS means
**all of the following**:

1. **The upstream source tarball** as published by the upstream
   project, in unmodified form. Filename and SHA256 hash match the
   `source:` and `sha256:` fields in the package's `package.yml`.

2. **Every patch** the InterGenOS build applied to that upstream
   source, from `packages/<tier>/<name>/patches/*.patch`. Patches
   are applied in the same order the build applies them
   (alphabetical, via `for p in patches/*.patch; do patch -p1 < $p; done`).

3. **The build script** `packages/<tier>/<name>/build.sh` that
   InterGenOS used to configure, compile, and install the package.
   This script encodes the build flags, link options, install
   layout, and post-install steps under which the binary was
   produced. It is part of the corresponding source under
   GPL v3 §1 ("the source code for all modules it contains, plus any
   associated interface definition files, plus the scripts used to
   control compilation and installation of the executable").

4. **The package metadata** `packages/<tier>/<name>/package.yml`,
   which declares dependencies, license, and source verification
   data.

5. **Any kernel configuration fragments, profile snippets, or
   sidecar artifacts** the build composes into the final binary
   (e.g., `config/kernel/fragments/*.config` for the kernel package).

For packages whose license does not require source distribution
(MIT, BSD without advertising clauses, Public Domain, ISC, Zlib, etc.),
InterGenOS still publishes the same artifact set under the same
naming convention — we treat source availability as a uniform policy
across the package tree rather than a per-license carve-out. You
will never need to consult per-package license terms to determine
whether source is available; it always is.

---

## 3. Source archive layout

Each `<name>-<version>-<release>.igos.src.tar.gz` is a flat tarball
containing the directory `<name>-<version>-<release>/` with:

```
<name>-<version>-<release>/
├── <upstream-source-tarball>          # Unmodified upstream, original filename
├── <upstream-source-tarball>.sha256   # Hash file
├── patches/
│   └── *.patch                        # InterGenOS-applied patches
├── build.sh                           # InterGenOS build script
├── package.yml                        # InterGenOS package metadata
└── README.SOURCES                     # Build reproduction instructions
```

`README.SOURCES` documents the steps needed to reproduce the
corresponding binary from the source archive, using the same
toolchain version InterGenOS used. The toolchain itself is also
distributed as InterGenOS packages and is itself source-available
under this same policy.

---

## 4. Provenance and verification

Every InterGenOS source archive's SHA256 hash is recorded in the
signed package index that pkm uses to verify binary archives:

```
https://repo.intergenos.org/x86_64/current/InterGenOS.db
https://repo.intergenos.org/x86_64/current/InterGenOS.db.sig
```

Both source and binary archive hashes appear in the same signed
index. The signing topology is documented at
[`docs/signing-key.md`](docs/signing-key.md) (anchored on hardware-
token release subkeys certified by an offline-held master).

To verify a source archive:

```sh
# 1. Fetch the signed index
curl -O https://repo.intergenos.org/x86_64/current/InterGenOS.db
curl -O https://repo.intergenos.org/x86_64/current/InterGenOS.db.sig

# 2. Verify the index signature against the master pubkey
gpg --verify InterGenOS.db.sig InterGenOS.db

# 3. Fetch a source archive
curl -O https://repo.intergenos.org/x86_64/current/sources/<name>-<version>-<release>.igos.src.tar.gz

# 4. Verify SHA256 against the index entry
gunzip -c InterGenOS.db | jq -r '.packages."<name>".source_sha256'
sha256sum <name>-<version>-<release>.igos.src.tar.gz
```

The upstream tarball contained inside the archive can be cross-verified
against its own SHA256, which `package.yml` records and `pkm` enforces
at install time.

---

## 5. Scope and special cases

**Toolchain.** The InterGenOS toolchain (gcc, glibc, binutils, the
LFS-Ch5/6/7 cross-build chain) is source-available under the same
mechanism as every other package. Source is in the same `sources/`
directory; the build scripts under `packages/toolchain/` are part of
the source-availability guarantee.

**Kernel.** The Linux kernel package is source-available with its
configuration fragments. The fragments at
`config/kernel/fragments/*.config` are composed into the final
`.config` at build time and are part of the corresponding source.

**Microcode.** Intel microcode (`packages/core/intel-ucode`) and AMD
microcode are vendor-supplied binary blobs distributed under
proprietary licenses that permit redistribution but not modification.
The upstream binary is itself the "source" the licenses recognize.
We redistribute the unmodified upstream archive and the upstream
license text alongside it.

**Vendor firmware.** The same applies to other vendor-supplied
firmware blobs (in `linux-firmware`, etc.): we redistribute the
upstream archive and its license text; modification is not
permitted by the upstream license and InterGenOS does not modify.

**Fetched-at-runtime payloads.** Some `packages/extra/*-helper/`
packages fetch proprietary upstream binaries at install time on
the user's machine (Chrome, Edge, VS Code, Spotify, Discord,
Brave, Claude Code CLI). The helper script itself is GPL-3 and
its source is in our mirror. The fetched payload is the upstream
vendor's, distributed under their EULA, not redistributed by
InterGenOS. See [`docs/governance/license-policy.md`](docs/governance/license-policy.md)
for the helper-payload license posture.

**Bundled font, icon, sound, and theme assets** ship under the
licenses declared in `CREDITS`. Their source/font files are
distributed in their as-shipped form; that form is the source.

**Fetched-at-runtime LLM models** (under `packages/ai/`) ship under
their model authors' licenses (e.g. Qwen3.5 under the Tongyi
Qianwen License). Model weights are downloaded from the model
author's distribution. The InterGenOS code that performs the
download is GPL-3 and source-available; the model weights are
not InterGenOS-modified and not redistributed by InterGenOS.

---

## 6. Coverage commitment over time

InterGenOS commits to keeping source available for each release for
**no less than three (3) years** after that release is superseded by
a newer stable release. In practice:

- The active `current/` symlink on the mirror always reflects the
  most recent stable release; sources are co-located with binaries.
- The previous five (5) release snapshots are retained under
  `_previous/` on the mirror with both binaries and sources.
- For releases older than the retained snapshots, the §6b written
  offer remains in force for the full three-year window from the
  date you received your copy.

If the InterGenOS project mirror is ever decommissioned, taken
permanently offline, or transferred to another operator, the project
will publish a migration notice with the new source-availability
mechanism, and the §6b written offer remains in force.

---

## 7. Compliance posture for downstream redistributors

If you redistribute InterGenOS — burning an ISO for someone, running
a mirror, hosting downloads, forking the project — **you inherit the
same source-availability obligations the original GPL licenses
impose**. You can satisfy them by:

1. **Pointing to this commitment**: forwarding the recipient to this
   `SOURCES.md` and to `https://repo.intergenos.org/x86_64/current/sources/`,
   provided the mirror remains operational. GPL v3 §6e explicitly
   permits this for non-commercial conveyance; GPL v3 §6d permits
   it for any conveyance where the source is offered at the same
   place as the binary; GPL v2 §3(c) permits it for non-commercial
   conveyance where you received the binary with the written §6b
   offer.

2. **Making your own offer**: providing your own §6b written offer
   valid for three years from the date of your redistribution.

3. **Bundling the source directly**: shipping source archives
   alongside binary archives in your redistribution channel.

InterGenOS recommends Option 1 (pointing to the project mirror) for
non-commercial redistribution, and Option 3 (bundling source) for
commercial redistribution where you cannot rely on the project mirror's
continued availability under your distribution timeline.

Note that the project's trademark policy ([`TRADEMARK.md`](TRADEMARK.md))
limits use of the InterGenOS name and brand assets for derivative
distributions. Source availability and trademark use are independent
matters; one does not imply the other.

---

## 8. Interim window: pre-first-publish

The InterGenOS public mirror infrastructure is ratified and operational
(DNS, TLS, SSH access, docroot layout — landed 2026-05-11; see
[`docs/mirror/design.md`](docs/mirror/design.md)). The first end-to-end
publish exercising the source-archive pipeline is the final v1.0
ship-blocker on the publish path.

During this interim window — between the date this commitment is
published and the date the first source archive lands at
`https://repo.intergenos.org/x86_64/current/sources/` — **the §6b
written offer is the load-bearing source-availability channel**. Any
InterGenOS copy that left the project's possession during this
window carries the §6b offer above as its enforceable source
guarantee.

The interim window will be closed by a `[publish: first-source]`
commit when the mirror's `sources/` tree is live. From that commit
onward, both channels are operational; the §6b offer remains in force
as a permanent fallback regardless of mirror availability.

---

## 9. Contact, escalation, and edge cases

For routine source requests, use `legal@intergenos.org` with subject
`GPL Source Request` per §1 Channel B above.

For compliance questions, license-conflict reports, or claims that
InterGenOS is failing to meet a source-availability obligation:

- **First**: file a GitHub issue at
  [github.com/InterGenJLU/intergenos/issues](https://github.com/InterGenJLU/intergenos/issues)
  with the label `legal: source-availability`. This is the fastest
  path to acknowledgment.

- **Second** (if you require a private channel): email
  `legal@intergenos.org` with subject `Source Compliance —
  <package or release>`. We will acknowledge within 10 business days.

If you have not received a substantive response within 30 days of
your initial outreach via either channel, the relevant upstream
license remedies are not waived by silence on our part; you may
proceed under the terms of the applicable license.

---

## 10. Cross-references

- [`LICENSE`](LICENSE) — root license (GPL-3.0-or-later) for
  InterGenOS-authored code (build system, tools, templates,
  scripts, the pkm package manager, the Forge installer,
  the InterGen assistant wrapper, package metadata, and build
  scripts).
- [`CREDITS`](CREDITS) — third-party attribution for bundled
  components, themes, extensions, fonts, sounds, wallpapers, and
  icons.
- [`docs/mirror/design.md`](docs/mirror/design.md) — public binary
  mirror design, hostname/docroot/SSH/TLS infrastructure, signing
  topology.
- [`docs/signing-key.md`](docs/signing-key.md) — release-signing
  key topology and master fingerprint.
- [`docs/governance/license-policy.md`](docs/governance/license-policy.md)
  — internal license accept/reject inventory, AGPL exposure
  posture, helper-payload policy, SPDX header policy.
- [`TRADEMARK.md`](TRADEMARK.md) — trademark policy (separate from
  source-availability; brand assets are carved out of the GPL-3
  default license).
- [`EXPORT-NOTICE.md`](EXPORT-NOTICE.md) — US BIS / EU dual-use
  classification for the cryptographic components shipped.

---

## 11. Provenance

This document was authored 2026-05-18 as part of the InterGenOS v1.0
legal-readiness sprint, closing audit finding **P-001** (Holy-Grail
class: no GPL source-availability mechanism on shipped ISO) from the
2026-05-18 comprehensive state audit.

**License of this document.** This `SOURCES.md` is licensed
**CC0-1.0** (public domain dedication) so downstream redistributors
may copy, adapt, or reuse its text without restriction.

— InterGenJLU
