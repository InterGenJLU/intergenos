# InterGenOS — Export-Control Notice

**Last updated:** 2026-05-18

InterGenOS includes strong cryptographic software as a routine part
of being a secure general-purpose operating system. This document
describes the project's posture with respect to United States export
controls (Export Administration Regulations, 15 C.F.R. parts 730–774)
and equivalent rules in other jurisdictions, and tells you what your
obligations are if you download, redistribute, install, or use
InterGenOS in connection with cross-border activity.

If you only read one section, read [§2 — In one paragraph](#2--in-one-paragraph).

---

## 1. Why this notice exists

The Holy Grail Security Alignment of InterGenOS requires that the
system ship strong cryptography enabled by default — TLS, full disk
encryption, code signing, GPG, secure boot, hardware-token integration
— because user trust in the machine depends on it.

Strong cryptography is regulated technology in most jurisdictions.
The United States classifies the relevant components under Category
5, Part 2 (Information Security) of the Commerce Control List (CCL),
and the European Union, the United Kingdom, Japan, Australia, and
others apply analogous classifications under the Wassenaar
Arrangement framework.

This notice exists to (a) declare InterGenOS's classification, (b)
identify the license exception we rely on, (c) name the jurisdictions
where downloads are not permitted, and (d) explain what downstream
redistributors must do to remain in compliance.

Nothing in this notice is legal advice. If your specific use case
involves export-controlled technology in a complex way (sales to a
listed entity, technology transfer agreements, etc.), consult a
qualified export-control counsel.

---

## 2. In one paragraph

InterGenOS is self-classified under **ECCN 5D002**, distributed under
the **TSU license exception for publicly available encryption source
code** (15 C.F.R. § 740.13(e)) and the **ENC license exception** for
the binary distribution (15 C.F.R. § 740.17), and is **not exported,
re-exported, or transferred to** Cuba, Iran, North Korea, Syria, the
Russian-occupied regions of Ukraine, or to any person or entity on
the U.S. Department of the Treasury's Specially Designated Nationals
(SDN) list or the U.S. Department of Commerce's Entity List.
**Downstream redistributors inherit the same restrictions** and must
honor them in their own distribution channels. **Users in embargoed
jurisdictions must not download InterGenOS.** Downloads are not
geo-blocked at the mirror, but downloading from an embargoed
jurisdiction is a violation of the applicable U.S. sanctions regime
and is the responsibility of the downloader.

The rest of this document is the detail behind that paragraph.

---

## 3. The cryptographic components

InterGenOS ships, in its default desktop and base configurations, the
following packages containing strong cryptography (symmetric key
lengths exceeding 56 bits, asymmetric key lengths exceeding 512 bits,
or elliptic-curve cryptography over fields of size exceeding 112
bits — the thresholds in CCL Category 5 Part 2):

| Package | Role |
|---|---|
| `openssl` | TLS, X.509 certificate handling, general crypto library |
| `gnutls` | TLS alternative, used by GNOME and several core libraries |
| `libgcrypt` | GnuPG's crypto primitives, used by libsystemd-cryptsetup |
| `nss` / `nspr` | Mozilla cryptography stack (Firefox, Chrome, Thunderbird) |
| `nettle` / `hogweed` | Low-level crypto primitives |
| `gpgme` / `gpgmepp` | GnuPG library and C++ bindings |
| `gnupg2` | OpenPGP implementation |
| `cryptsetup` | LUKS full-disk encryption userland |
| `libssh` / `libssh2` | SSH protocol libraries |
| `wolfssl` (if installed) | Embedded TLS |
| `kerberos` / `krb5` | MIT Kerberos network authentication |
| Linux kernel crypto subsystem | dm-crypt, IPsec/XFRM, fscrypt, evm, integrity, dm-verity |
| `systemd` | TPM2 integration, sysext signing, journal sealing |
| `shim` / `grub` / `sbsign` / `sbsigntool` | Secure boot signing chain |
| `pkm` (InterGenOS-authored) | GPG-signed index verification of binary packages |
| `Forge` (InterGenOS-authored) | Calls into `cryptsetup` and `sbsigntool` for installation-time LUKS setup and UKI signing |

The cryptographic components above are commonly available from many
sources; their export status is not unique to InterGenOS. We
redistribute them in their upstream form (subject to the InterGenOS
patch set, which is itself published with the source per
[`SOURCES.md`](SOURCES.md)).

---

## 4. United States classification

### 4.1 ECCN

InterGenOS is self-classified under **Export Control Classification
Number 5D002** ("Information Security Software"), specifically the
encryption commodities and software subject to EI controls in
15 C.F.R. § 742.15.

### 4.2 License Exceptions

The project relies on two license exceptions for export under the
EAR:

- **TSU — "Technology and Software, Unrestricted"** (15 C.F.R.
  § 740.13(e)) — for the *publicly available encryption source
  code* portion of InterGenOS. The full source code for every
  encryption component is published in the open at
  `https://repo.intergenos.org/x86_64/current/sources/` and at
  `github.com/InterGenJLU/intergenos` (and various source-fetch
  upstreams). TSU permits export of publicly available encryption
  source code without a license, subject to the BIS notification
  in §4.3 below.

- **ENC — Encryption Commodities, Software, and Technology**
  (15 C.F.R. § 740.17) — for the *compiled binary* portion of
  InterGenOS distributed as install images (ISO, qcow2, and the
  installed-system tree). ENC permits export of mass-market and
  publicly-available encryption binaries without a license, subject
  to the same notification.

### 4.3 BIS Notification

The U.S. Bureau of Industry and Security (BIS) and the U.S. National
Security Agency (NSA) require a one-time email notification when a
party first exports publicly available encryption software relying
on the TSU exception (15 C.F.R. § 742.15(b)). For mass-market binary
encryption distributed under the ENC exception (15 C.F.R. §
740.17(b)(2)) the same single-event notification is filed.

The notification for InterGenOS was filed to `crypt@bis.doc.gov` and
`enc@nsa.gov` on **[NOTIFICATION DATE — to be backfilled before
first public release].** This entry will be updated to the actual
notification date when the filing is made; the entry's appearance
in the master branch's `EXPORT-NOTICE.md` at the time of an
exported release is the project's record of compliance for that
release. Releases that ship before the notification date are not
publicly distributed.

If you operate a mirror of InterGenOS or otherwise redistribute it
**from the United States**, you may be making your own export under
the EAR. You may rely on the same TSU/ENC exceptions if your
redistribution qualifies; consult § 742.15 directly. The InterGenOS
notification does not satisfy your obligation, only ours.

### 4.4 Embargoed destinations

InterGenOS may not be exported, re-exported, or transferred to:

- **Cuba** (15 C.F.R. § 746.2)
- **Iran** (15 C.F.R. § 746.7)
- **North Korea** (15 C.F.R. § 746.4)
- **Syria** (15 C.F.R. § 746.9)
- **The Crimea, Donetsk, Luhansk, Kherson, and Zaporizhzhia regions
  of Ukraine** (15 C.F.R. § 746.6, OFAC Executive Orders)

InterGenOS also may not be exported, re-exported, or transferred to:

- Any person or entity on the U.S. Department of Commerce **Entity
  List** (15 C.F.R. Part 744, Supp. No. 4)
- Any person or entity on the U.S. Department of the Treasury OFAC
  **Specially Designated Nationals** (SDN) list
- Any person on the U.S. Department of State **Debarred Parties**
  list
- Any person on the Department of Commerce **Denied Persons** list
- Any person on the Department of Commerce **Unverified List** for
  end-use that the EAR conditions on a license

These lists change. The authoritative versions are at
`bis.doc.gov` and `treasury.gov/ofac`. If you are uncertain whether
a specific download or redistribution is permitted, consult those
sources or qualified counsel.

### 4.5 End-use restrictions

InterGenOS may not be exported to any person reasonably believed to
intend to use it for (15 C.F.R. § 744):

- Design, development, production, stockpiling, or use of nuclear,
  chemical, or biological weapons or missile technology, where the
  jurisdiction is on the relevant control list
- Activities related to maritime nuclear propulsion or nuclear
  explosive devices
- Activities supporting a foreign military, intelligence, or
  security-services end-user where the destination is subject to the
  applicable end-user controls

### 4.6 Geo-blocking and the mirror

The InterGenOS public mirror at `repo.intergenos.org` and the source
distribution at `repo.intergenos.org/x86_64/current/sources/` are
not geo-blocked. We do not maintain an IP-address-to-jurisdiction
database, and geo-blocking is not effective against deliberate
circumvention.

**The legal obligation not to download from an embargoed jurisdiction
rests on the downloader.** Connecting from an embargoed jurisdiction
and obtaining the software is a violation of U.S. sanctions, even
if the mirror permits the request. By initiating a download from an
embargoed jurisdiction you make a representation to the contrary
that is, in that case, false.

---

## 5. European Union, United Kingdom, and Wassenaar

The European Union regulates dual-use exports under **Regulation
(EU) 2021/821** (the "Dual-Use Regulation"), which implements the
Wassenaar Arrangement's controls on Information Security commodities
and software. The relevant classifications are EU Annex I 5D002 /
5A002 — substantively parallel to U.S. CCL 5D002 / 5A002.

The Dual-Use Regulation contains a **general exemption** for
"publicly available technology" and for "in the public domain" items
under General Note 3 to Annex I. We rely on this exemption for
publicly-available encryption source code.

For **export of binary InterGenOS images from an EU Member State**
to a non-EU destination, EU General Export Authorisation **EU001**
typically applies (low-risk destinations under standard conditions).
Operators in the EU should consult their national export-control
authority for binding guidance specific to their distribution
channel.

The **United Kingdom** applies the same controls under the **Export
Control Order 2008** with substantively parallel provisions; mass-
market encryption is generally covered by the **OGEL — Cryptographic
Development**.

**EU Sanctions.** The EU maintains its own restrictive measures list
(at `eeas.europa.eu/eeas/eu-sanctions-map`) which generally aligns
with but is not identical to the U.S. OFAC SDN list. EU-based
redistributors should screen against the EU consolidated list as
well.

---

## 6. Other jurisdictions

If you are redistributing InterGenOS from a jurisdiction other than
the United States, the European Union, or the United Kingdom, your
local export controls apply. Most Wassenaar member states implement
substantively parallel rules for publicly-available encryption
source code; consult your national export-control authority.

The project does not maintain a comprehensive global compliance map.
We commit to:

- Making the source publicly available under [`SOURCES.md`](SOURCES.md)
  so the "publicly available" carve-outs in most jurisdictions
  remain applicable.
- Cooperating with reasonable inquiries from national authorities
  to confirm the open-source status of any component.

---

## 7. Downstream redistributors

If you redistribute InterGenOS (mirroring, hosting, burning ISOs,
preinstalling on hardware, etc.), you are making your own export
when your distribution channel crosses a border. **You inherit the
same compliance obligations as the project**, including:

- **Honoring the embargoed-destinations list** (§4.4 above).
- **Honoring the end-use restrictions** (§4.5).
- **Filing your own BIS notification** if you are in the United
  States and your redistribution channel does not qualify under the
  InterGenJLU notification.
- **Avoiding transfer to listed entities** (Entity List, SDN, etc.).

You may not represent that downstream users are exempted from these
restrictions because they "received it from InterGenOS." The
exemptions and exceptions apply to each export event.

---

## 8. Specific component notes

**Microcode (Intel, AMD).** Intel CPU microcode and AMD CPU
microcode are vendor-supplied binary blobs. Their export status is
controlled at the CPU vendor level; they ship to InterGenOS through
the regular Intel and AMD distribution channels. We redistribute
them unmodified.

**Hardware tokens.** InterGenOS supports Nitrokey, YubiKey, and
other hardware-token-based authentication via standard `scdaemon`
/ `pkcs11` integration. The tokens themselves are exported by their
manufacturers under their own classifications. InterGenOS does not
ship token hardware.

**TPM2 integration.** The systemd TPM2 unlock path uses standard
TPM2 software stack components; their export status follows the
standard EAR treatment for TPM-using software.

**FIDO2 / WebAuthn.** The InterGenOS support for FIDO2-based
LUKS unlock (per Owner Directive D-001 EXPERIMENTAL) is built on
publicly-available libfido2 / libcbor stacks.

---

## 9. Compliance commitments

InterGenJLU commits to:

1. **Filing the required notifications** with BIS, NSA, and any
   other authority where the project's distribution profile makes
   them applicable, before the first public release.

2. **Updating this notice** if our classification changes, if a
   new license exception becomes applicable, or if the embargoed-
   destinations list changes substantively.

3. **Screening direct shipments** under the `SOURCES.md` § 6b
   written offer for source media against the SDN, Entity List, and
   embargoed-jurisdictions lists before fulfillment.

4. **Cooperating in good faith** with reasonable government
   inquiries about specific exports.

5. **Not deliberately enabling end-use violations** — for example,
   we will not knowingly redistribute InterGenOS to a party we
   reasonably believe is on a control list or has a prohibited
   end-use.

---

## 10. Reporting concerns

If you believe an InterGenOS distribution has been exported in
violation of U.S. or EU controls, or if you wish to report a
compliance concern, contact:

```
legal@intergenos.org
```

with subject line:

```
Export Compliance — <short description>
```

We will respond within 30 days.

For reporting violations directly to U.S. authorities:
- BIS Export Enforcement: `https://www.bis.doc.gov/enforcement`
- OFAC SDN-list violations: `https://ofac.treasury.gov`

---

## 11. Cross-references

- [`LICENSE`](LICENSE) — GPL-3.0-or-later root license.
- [`SOURCES.md`](SOURCES.md) — source-availability commitment;
  the "publicly available source code" foundation of our TSU
  reliance.
- [`PRIVACY.md`](PRIVACY.md) — privacy notice (includes the
  jurisdictional-data-transfer discussion at §9).
- [`TRADEMARK.md`](TRADEMARK.md) — brand policy.
- [`SECURITY.md`](SECURITY.md) — vulnerability disclosure.
- [`docs/mirror/design.md`](docs/mirror/design.md) — mirror
  infrastructure (the export endpoint).
- [`docs/governance/license-policy.md`](docs/governance/license-policy.md)
  — license accept/reject inventory.

External references:
- 15 C.F.R. Parts 730–774 (EAR): `https://www.bis.doc.gov`
- 15 C.F.R. § 740.13 (TSU): `https://www.ecfr.gov/current/title-15/section-740.13`
- 15 C.F.R. § 740.17 (ENC): `https://www.ecfr.gov/current/title-15/section-740.17`
- 15 C.F.R. § 742.15 (Encryption notification): `https://www.ecfr.gov/current/title-15/section-742.15`
- OFAC sanctions program: `https://ofac.treasury.gov`
- EU dual-use regulation: `https://policy.trade.ec.europa.eu/help-exporters-and-importers/exporting-dual-use-items_en`
- Wassenaar Arrangement: `https://www.wassenaar.org`

---

## 12. Provenance

This notice was authored 2026-05-18 as part of the InterGenOS v1.0
legal-readiness sprint, closing audit finding **P-024** (Medium: no
U.S. BIS / EU dual-use export-control notice despite shipping strong
crypto by default) from the 2026-05-18 comprehensive state audit.

**License of this document.** This `EXPORT-NOTICE.md` is licensed
**CC0-1.0** (public domain dedication) so other projects may adapt
its text. Adapting it does not transfer any compliance obligation;
each redistributor is responsible for its own classification,
notification, and screening.

— InterGenJLU
