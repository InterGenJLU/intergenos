# InterGenOS — Patent Posture

**Audience:** users, downstream redistributors, and any party
distributing InterGenOS in a jurisdiction where software patents
or audio/video codec patents are enforced.

**Status:** advisory + disposition record. Nothing in this document
is legal advice or a representation that any specific use is
non-infringing. Consult counsel for use cases that depend on a
specific patent disposition.

---

## 1. Why this notice exists

Software patents are not uniformly recognized worldwide. The United
States allows them broadly; the European Union recognizes them in
limited contexts; many other jurisdictions either reject them
outright or have narrow scope. Audio/video codec patents are
recognized in most major jurisdictions and are actively licensed
by patent pools (MPEG-LA, MC-IF, Via-LA, Sisvel, HEVC Advance).

InterGenOS ships a broad set of multimedia, cryptographic, and
systems software. Some of those components have features that **may
be subject to patent claims** depending on:

- The jurisdiction the user (or redistributor) operates in
- The intended use (personal, commercial, encoding, decoding,
  distribution, hardware integration)
- The patent's status (active, expired, encumbered by FRAND
  obligations, abandoned)

This document is the project's transparent disposition record:
what we know, what we ship, what we don't ship by default, and what
you should think about if you operate at scale in a patent-
enforcing jurisdiction.

---

## 2. The project's stance

InterGenOS adopts the **defensive transparency** stance shared by
most upstream Linux distributions:

- We **do not** make patent-licensing decisions on behalf of users.
- We **do not** ship components in the default install that are
  known to require patent licensing for ordinary distribution
  (FDK-AAC is the canonical example — see § 4.1).
- We **do** ship components that have patent exposure but where the
  upstream license, jurisdictional practice, or active license
  pool makes ordinary distribution low-risk for the user (H.264,
  H.265 decoding, etc. — see § 4.2).
- We **do** ship opt-in helper packages for users who explicitly
  want patent-encumbered functionality and accept the disposition
  applicable to them (`ffmpeg-nonfree-helper`).
- We **publish** what we know so users and redistributors can make
  their own informed decisions.

This stance does not waive any rights InterGenJLU may have under
defensive patent provisions in licenses we receive code under
(Apache-2.0 § 3, GPL-3.0 §§ 10–11, BSD-2-Clause-Patent, etc.).

---

## 3. Patent-related license clauses we benefit from

The codebase received from upstreams generally carries patent
grants beneficial to InterGenOS and its users:

- **Apache-2.0 § 3** grants an irrevocable patent license from
  each contributor for any patent claim "necessarily infringed by
  their contribution alone or by combination of their contribution
  with the Work."

- **GPL-3.0 §§ 10–11** include an automatic patent license from
  upstream conveyors and prohibit downstream distributors from
  imposing further patent restrictions. Among the strongest
  patent-protection clauses in any FOSS license.

- **BSD-2-Clause-Patent** (used by some recent permissive
  upstreams) extends the standard BSD-2-Clause with an explicit
  patent grant.

- **Mozilla Public License v2.0 § 2.1** provides patent licenses
  scoped to contributions.

These clauses provide meaningful protection for ordinary
distribution and use. They do **not** override third-party patent
claims (claims by parties who are not contributors to the code).

---

## 4. Specific component dispositions

### 4.1 AAC encoding (FDK-AAC, libfdk-aac)

**Component:** `packages/desktop/fdk-aac` — Fraunhofer FDK AAC
codec library. Implements AAC-LC, HE-AAC, HE-AACv2 encoding and
decoding.

**Patent exposure:** Active patent licensing required for
commercial AAC encoders/decoders meeting certain thresholds.
Fraunhofer's own license (the "FDK AAC license") permits source
and binary redistribution but explicitly **does not** grant
patent licenses; users and redistributors must obtain those
separately if their jurisdiction and use case require it.

**Distro practice:** Fedora and Debian explicitly omit FDK-AAC
from their main repositories. RPM Fusion / Debian non-free are
the typical opt-in channels.

**InterGenOS disposition:**

- The `packages/desktop/fdk-aac` package is **in the tree** as a
  source dependency but is **not part of the default ffmpeg
  build**.
- `packages/desktop/ffmpeg/build.sh` is configured **without**
  `--enable-nonfree` and **without** `--enable-libfdk-aac` in
  the default ISO. Default ffmpeg uses the in-tree AAC encoder
  (`--enable-encoder=aac`), which provides functional AAC support
  without the FDK linkage.
- Users who specifically want FDK-AAC quality install the
  **`ffmpeg-nonfree-helper`** package via `pkm install
  ffmpeg-nonfree-helper`. The helper:
  - Surfaces this PATENTS notice for explicit acceptance
  - Re-builds ffmpeg with `--enable-nonfree --enable-libfdk-aac`
    against the user's installed system
  - Records the user's acceptance in `/var/lib/intergen/legal/`
    so re-installation does not silently re-prompt

**For redistributors:** Distributing the default InterGenOS ISO
does not implicate the FDK-AAC patent posture (FDK-AAC is not
linked into the ffmpeg binary). Distributing an installed system
where `ffmpeg-nonfree-helper` has been used implicates whatever
patent dispositions apply in the redistribution channel.

### 4.2 H.264 / H.265 (libavcodec)

**Components:** the H.264 (AVC) and H.265 (HEVC) decoders shipped
as part of `packages/desktop/ffmpeg`'s libavcodec.

**Patent exposure:** Active in MPEG-LA / Via-LA / HEVC Advance
licensing pools. Most consumer hardware (CPUs, GPUs, mobile SoCs)
ships with hardware decoder licenses already covered by the
manufacturer.

**Distro practice:** Fedora ships software decoders for H.264/H.265
in modern releases, having determined that defensive omissions did
not meaningfully reduce patent exposure for ordinary use. Debian
ships in `main` (decoders) and `non-free-firmware` (some encoder
acceleration).

**InterGenOS disposition:** **Shipped in default.** Decoders only;
no commercial-grade encoders are linked into default ffmpeg. Users
encoding H.264/H.265 commercially at scale should evaluate their
specific patent posture; ordinary personal encoding from camera
footage is generally low-risk under current jurisdictional
enforcement.

### 4.3 MP3 (LAME)

**Component:** `packages/desktop/lame` (MP3 encoder) and
`packages/desktop/mpg123` (MP3 decoder).

**Patent exposure:** The principal MP3 patents expired worldwide
between 2012 and 2017. The Fraunhofer MP3 licensing program ended
in 2017. **MP3 is no longer patent-encumbered.**

**InterGenOS disposition:** **Shipped in default**, no special
handling required. Listed here only because the patent posture is
frequently misunderstood.

### 4.4 DVD-CSS (libdvdcss)

**Component:** Not currently in the tree; mentioned for
completeness.

**Patent exposure:** DVD-CSS contains decryption logic whose
distribution is subject to the U.S. Digital Millennium Copyright
Act § 1201 anti-circumvention provision and equivalent EU rules
(EUCD).

**Distro practice:** Universally omitted from main repos. VideoLAN
operates a separate distribution for their `libdvdcss` package
that ships under defensive jurisdiction.

**InterGenOS disposition:** **Not in the tree.** Users who need
DVD playback obtain `libdvdcss` from VideoLAN's project
distribution under the disposition VideoLAN applies. A future
opt-in helper may exist if user demand justifies.

### 4.5 Intel and AMD CPU microcode

**Components:** `packages/core/intel-ucode`, AMD ucode bundled in
`packages/core/linux-firmware`.

**Patent exposure:** None applicable to distribution. Microcode
is vendor-licensed for redistribution under terms set by Intel and
AMD respectively. No third-party patent claims apply to
distribution of these blobs (the blobs themselves embody Intel/AMD
trade secrets and patents, not third-party patents).

**InterGenOS disposition:** **Shipped in default.** Vendor
licenses bundled to `/usr/share/licenses/<package>/`.

### 4.6 Secure Boot / shim / Microsoft UEFI CA-signed binaries

**Components:** `packages/core/shim-signed` (Fedora-signed shim
during the bootstrap period; pending replacement by InterGenOS-
owned MS-signed shim via the rhboot/shim-review process).

**Patent exposure:** None applicable to ordinary distribution. The
Microsoft UEFI CA Certificate Agreement governs use of the
Microsoft signature, not distribution under patents.

**InterGenOS disposition:** **Shipped in default**, with
permission documented per
[`docs/governance/fedora-shim-redistribution.md`](../governance/fedora-shim-redistribution.md).
The Microsoft signature is a trademark/contract concern, not a
patent concern.

### 4.7 ECC cryptography (curve25519, P-256, P-384, etc.)

**Components:** Bundled in `openssl`, `gnutls`, `libgcrypt`, `nss`,
`nettle`, the Linux kernel crypto subsystem.

**Patent exposure:** P-256 and P-384 (NIST curves) had patent
concerns historically; widely-licensed today through Certicom (now
BlackBerry) and the relevant patents have largely expired.
Curve25519 is patent-unencumbered by design (Bernstein).

**InterGenOS disposition:** **Shipped in default**, no special
handling.

### 4.8 OpenSSL / GnuTLS license combinations

**Components:** OpenSSL ≥ 3.0 is Apache-2.0 licensed (Apache-2.0
§ 3 patent grant applies). GnuTLS is LGPL-2.1+. Both are
distributed under licenses that are GPL-compatible and carry
the patent grants in §3.

**InterGenOS disposition:** **Shipped in default**, no special
handling. The historical OpenSSL-1.x license incompatibility
with GPL was resolved by OpenSSL's relicense to Apache-2.0 in
version 3.0.

---

## 5. What this document is not

- **Not legal advice.** Patent law is jurisdiction-specific and
  fact-specific. This document is descriptive of the project's
  disposition; it is not a representation that any specific use is
  non-infringing.

- **Not an exhaustive inventory.** Many shipped packages
  potentially intersect with patents in ways we have not
  individually catalogued. The dispositions above cover the major
  known intersections.

- **Not a covenant not to sue.** InterGenJLU makes no patent
  claims of its own and the contributor patent grants in the
  licenses we receive code under apply to that code. We have
  not attempted to extract a separate covenant from any party.

- **Not a hardware compatibility statement.** Hardware vendors'
  patent licenses on their own products (mainly DRM / HDCP /
  similar) are between those vendors and their licensees;
  InterGenOS does not modify those relationships.

---

## 6. Downstream redistributor guidance

If you redistribute InterGenOS — particularly in a way that
generates revenue, ships preinstalled on hardware, or serves a
commercial customer base — you should:

1. **Review the §4 dispositions** for your jurisdiction.
2. **Decide whether to include the opt-in patent-encumbered
   helpers** (`ffmpeg-nonfree-helper` etc.). The default ISO
   does not include them; you may strip them or pre-install them
   in your distribution channel, with the patent disposition
   appropriate to your case.
3. **Document your own patent posture** in your distribution
   channel's equivalent of this document. The InterGenOS
   disposition does not transitively apply to your distribution.
4. **Honor the trademark and export policies** (
   [`TRADEMARK.md`](../../TRADEMARK.md),
   [`EXPORT-NOTICE.md`](../../EXPORT-NOTICE.md)) which are
   independent of patent considerations.
5. **Consult counsel** if your redistribution is at meaningful
   scale or in a jurisdiction with active patent enforcement
   against codec distribution.

---

## 7. Cross-references

- [`SOURCES.md`](../../SOURCES.md) — source-availability
  commitment (independent of patent disposition).
- [`docs/governance/license-policy.md`](../governance/license-policy.md)
  — internal license discipline, including patent-encumbered
  components.
- [`TRADEMARK.md`](../../TRADEMARK.md) — brand policy.
- [`EXPORT-NOTICE.md`](../../EXPORT-NOTICE.md) — export controls.
- [`CREDITS`](../../CREDITS) — third-party attribution.

External:
- MPEG-LA: `https://www.mpegla.com`
- HEVC Advance / Via-LA: `https://www.via-la.com`
- USPTO patent database: `https://ppubs.uspto.gov`
- EPO Espacenet: `https://worldwide.espacenet.com`

---

## 8. Provenance

This document was authored 2026-05-18 as part of the InterGenOS
v1.0 legal-readiness sprint, closing audit finding **P-013** (Low:
no PATENT-NOTICE for codec/format packages beyond fdk-aac) and
covering the patent-disposition aspect of **P-003** (Critical:
FDK-AAC redistribution restrictions ignored) from the 2026-05-18
comprehensive state audit.

**License of this document.** This `PATENTS.md` is licensed
**CC0-1.0** (public domain dedication).

— InterGenJLU
