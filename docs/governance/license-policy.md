# InterGenOS — License Policy (internal discipline)

**Audience:** project contributors, coordinators, package maintainers,
auditors, and downstream redistributors who need to know what the
project will and will not ship.

**Status:** binding policy for any package, file, asset, or fetched
payload that enters the master branch or any release artifact.

This document is the source-of-truth for the project's license-
acceptance decisions. It is **not** the user-facing license summary
(see [`README.md`](../../README.md) § License and [`SOURCES.md`](../../SOURCES.md)
for that). It is the **internal-discipline** policy: which licenses
we accept, which we reject, what compound expressions look like,
how we handle borderline cases, and what auditors should check.

---

## 1. Outbound license posture

InterGenOS-authored code (the build system, the pkm package
manager, the Forge installer, the InterGen assistant wrapper, the
scripts, the package templates, the audit and verification
infrastructure) ships under:

```
GPL-3.0-or-later
```

This is the **outbound** license. Everything we publish is at
least this permissive; nothing we publish carries restrictions
that would block a GPL-3 redistributor from exercising their GPL-3
rights.

The outbound license has two practical consequences:

1. **Inbound contributions must be compatible.** A package whose
   license cannot be combined with GPL-3-or-later cannot ship as
   part of InterGenOS.

2. **The outbound license never silently tightens.** If we ever
   need to relicense (we do not anticipate doing so), the
   relicensing decision is explicit, public, and requires
   contributor consent (see [`DCO.md`](../../DCO.md) § 3).

---

## 2. License acceptance — the standard list

A package may ship as part of InterGenOS without further review if
its declared SPDX license expression is one of, or a compound of,
the licenses on the **standard accept list** below. The standard
list is curated to be compatible with the GPL-3 outbound posture.

### 2.1 Permissive

- `MIT`
- `BSD-2-Clause`
- `BSD-3-Clause`
- `BSD-4-Clause` (note: the original-BSD advertising clause; we
  accept but flag for attribution-list inclusion)
- `BSD-2-Clause-Patent`
- `ISC`
- `Apache-2.0`
- `Zlib`
- `bzip2-1.0.6`
- `libpng-2.0` / `FTL` (FreeType License)
- `IJG` / `Libpng` / `Bitstream-Vera` / `OFL-1.1`
- `Public Domain` / `CC0-1.0`
- `Unlicense`

### 2.2 Weak copyleft

- `LGPL-2.0-or-later`
- `LGPL-2.1-or-later`
- `LGPL-3.0-or-later`
- `MPL-2.0`
- `EPL-1.0` / `EPL-2.0`
- `EUPL-1.2`
- `Ruby` (Ruby License, dual GPL-compatible)

### 2.3 Strong copyleft

- `GPL-2.0-or-later`
- `GPL-3.0-or-later`
- `GPL-2.0-only` and `GPL-3.0-only` are **accepted** but flagged
  for compound-license verification: a `GPL-2.0-only` package
  cannot combine with `GPL-3.0-only` code, and we sometimes need
  to choose statically between paths.

### 2.4 Project-specific or component-specific licenses

These are accepted as listed:

- `Artistic-1.0` / `Artistic-2.0`
- `PSF-2.0` (Python Software Foundation License)
- `TCL` (Tcl/Tk License)
- `Vim` (Vim License)
- `PostgreSQL` (PostgreSQL License)
- `ICU` (ICU License)
- `OLDAP-2.8` (OpenLDAP)
- `OASIS` (OASIS license)
- `BSL-1.0` (Boost Software License)
- `SGI-B-2.0` (SGI Free Software License B)
- `ImageMagick` / `libtiff` / `Sleepycat` (Berkeley DB pre-Oracle)

### 2.5 Content-only licenses (assets, not code)

For fonts, sounds, wallpapers, theme assets, icon sets:

- `OFL-1.1` (SIL Open Font License)
- `CC-BY-SA-3.0` / `CC-BY-SA-4.0`
- `CC-BY-3.0` / `CC-BY-4.0`
- `Bitstream-Vera`

### 2.6 The "various redistributable" hedge

The project uses `Various (redistributable)` as a license declaration
for the small number of packages whose upstream is a heterogeneous
collection of files under multiple compatible licenses where
declaring a single SPDX expression would be misleading. Use of this
hedge requires:

- The package ships its upstream LICENSE/COPYING/COPYRIGHT-NOTICE
  file under `/usr/share/licenses/<package>/`.
- The license-bundle aggregation (see § 5) covers the package.
- A note in `package.yml` explains briefly why a single SPDX
  expression was unsuitable.

Examples in the current tree: `man-pages` (collection of authors,
mixed permissive licenses, each man page declares its own).

---

## 3. AGPL exposure policy

**AGPL-3.0-or-later** is on the standard accept list **with
conditions**.

The AGPL § 13 network-interaction source-availability requirement
imposes additional obligations on anyone who modifies the AGPL'd
code and makes it accessible to users over a network. The default
InterGenOS posture preserves AGPL compliance by:

1. **Allowing AGPL packages to ship** in their unmodified upstream
   form. We do not patch AGPL upstreams in ways that trigger § 13
   unless a clear, documented compliance plan accompanies the
   change.

2. **Not wrapping AGPL components as network services** without
   explicit review. The currently-shipped AGPL packages are
   `packages/desktop/ghostscript` and `packages/desktop/mupdf` —
   both user-side tools accessed locally. Neither is exposed over
   a network by any InterGenOS service. **If a future change
   exposes either over D-Bus, a socket, a web API, or any IPC
   that could be considered "network" interaction, the change
   must include an AGPL § 13 compliance mechanism** in the same
   commit (typically a source-link served by the same service to
   each connected user).

3. **Maintaining an explicit AGPL inventory.** § 7 below lists
   every AGPL'd package currently in the tree. Auditors check this
   inventory against `package.yml` declarations on each release
   pass.

A package that is `LGPL` for non-network use and `AGPL` for network
use (some libraries dual-license this way) is accepted under the
LGPL terms; using it in a network context triggers the AGPL
provisions and the conditions above apply.

---

## 4. Rejection — what we do not ship

The following licenses are **not accepted** for any package, asset,
or component that enters the InterGenOS tree:

### 4.1 Hard-incompatible with GPL-3

- `GPL-2.0-only` **combined with** code that must be GPL-3 (the
  combination is impossible; we resolve at integration time)
- Original BSD-with-advertising-clause **combined with** GPL in a
  way that activates the advertising-clause incompatibility
  (rare; we case-by-case verify on integration)

### 4.2 Non-free or restricted

- "Source-available" licenses with non-FOSS restrictions:
  - Server-Side Public License (`SSPL`)
  - Business Source License (`BUSL-1.1`)
  - Confluent Community License (`CCL`)
  - Elastic License (any version other than v3.0 dual with AGPL)
  - Redis Source Available License (`RSAL`)
  - Common Clause-affected licenses
- **"No commercial use"** clauses:
  - `CC-BY-NC-*` for code or shipped binaries
  - `JSON License` (the "shall be used for Good, not Evil" clause)
- **"No derivative"** clauses for any code or shipped binary:
  - `CC-BY-ND-*` (acceptable only for brand assets per
    [`TRADEMARK.md`](../../TRADEMARK.md), not for code)

### 4.3 Patent-encumbered without redistributable status

- `FDK-AAC` license — **rejected** as a shipped-binary default
  due to patent restrictions (audit finding P-003 / P-015). The
  `packages/desktop/fdk-aac` package is allowed in the tree
  **only** to support an opt-in `ffmpeg-nonfree-helper` flow
  where the user explicitly accepts the patent posture; see
  § 6 below and [`docs/legal/PATENTS.md`](../legal/PATENTS.md).

### 4.4 Project-incompatible

- **Proprietary EULAs** for components shipped on the ISO — never
  acceptable for default-installed packages. The closed-source
  helper-payload pattern (see § 6) is the only acceptable path
  for proprietary software, and it is **never** part of the
  default install.

- **LFS / BLFS verbatim text** — the books are CC-BY-NC-SA. We
  *follow* the books' methodology (which is not copyrightable as
  procedural facts) but we **do not redistribute verbatim LFS or
  BLFS prose** in shipped binaries or release ISOs. Methodology
  citation in our `docs/` is fair use; copy-pasted upstream prose
  is not. See [`CREDITS`](../../CREDITS) § LFS / BLFS for the
  attribution and the methodology-vs-verbatim distinction.

---

## 5. License bundling and attribution

Per [`SOURCES.md`](../../SOURCES.md) and audit finding P-004, every
shipped package must contribute its upstream LICENSE, COPYING, or
COPYRIGHT-NOTICE file to `/usr/share/licenses/<package>/` on the
installed system. The aggregation mechanism is:

- Each `package.yml` declares a `license:` SPDX expression that
  matches the package's actual outbound license obligations
  (including bundled deps — see § 8).
- Each package's `build.sh` extracts the upstream LICENSE-like
  file from the upstream source tarball during the install phase
  and copies it to `$DESTDIR/usr/share/licenses/<package>/`.
- For packages with multiple upstream LICENSE files (e.g.,
  upstream + bundled vendor LICENSE), all are bundled under the
  same directory with distinguishing filenames.
- The build system's pre-squashfs audit verifies that every
  shipped binary's package has at least one file under
  `/usr/share/licenses/<package>/`.

Two top-level aggregates are produced from the per-package
material:

- `THIRD-PARTY-NOTICES.md` at repo root — human-readable
  aggregated attribution for every shipped package, generated at
  build time from the per-package `license:` field, the upstream
  LICENSE file, and `CREDITS` content.
- `/usr/share/doc/intergenos/THIRD-PARTY-NOTICES` on the installed
  system — the same content, shipped on disk for offline access.

These aggregates are reproducible — built from `package.yml`
metadata + on-disk LICENSE files, no manual list maintenance.

---

## 6. Helper-payload policy (proprietary downstream)

Some `packages/extra/*-helper/` packages exist to install
proprietary software on user demand (Google Chrome, Microsoft Edge,
Visual Studio Code, Spotify, Discord, Brave, Anthropic Claude Code
CLI). These packages exist because users frequently want them, and
because the alternative — silently leaving users to install
proprietary software via vendor scripts that fly past all our
hardening — is worse for security than a transparent, audited,
opt-in mechanism.

The policy:

- **The helper script itself is GPL-3.** Its `license:` field
  declares `GPL-3.0-or-later`, accurately.
- **The fetched payload is the vendor's.** The helper does not
  embed the vendor binary; it fetches at install time from the
  vendor's official distribution endpoint. InterGenOS does not
  redistribute the vendor's binary.
- **The payload's license is declared separately.** Each helper
  `package.yml` declares a **`payload_license:`** field naming
  the upstream EULA or terms-of-service that the user is accepting
  by running the helper.
- **The user accepts the payload license before fetch.** Each
  helper's first install invocation displays a click-through (in
  the TUI / GUI installer) or text prompt (in the CLI) showing
  the payload license and requiring explicit acceptance before
  the download begins. Re-running the helper does not re-prompt
  unless the upstream version changes.
- **Helpers are never installed by default.** They are not part
  of the base or desktop tier; they are tier:extra and explicitly
  opt-in.
- **Helpers do not enable themselves.** Installing a helper does
  not configure browsers' integrations, telemetry agreements, or
  account sign-ins; those are the upstream binary's first-run
  flows, not InterGenOS's.

The `payload_license:` SPDX-style values currently used:

- `LicenseRef-Google-Chrome-ToS`
- `LicenseRef-Microsoft-Edge-EULA`
- `LicenseRef-Microsoft-VSCode-Software-License`
- `LicenseRef-Spotify-ToS`
- `LicenseRef-Discord-ToS`
- `LicenseRef-Brave-EULA`
- `LicenseRef-Anthropic-Commercial-Terms`

Each `LicenseRef-` value is defined in
[`docs/legal/payload-licenses.md`](../legal/payload-licenses.md)
(authored as part of the P-006 implementation work) with a
canonical URL, last-checked-date, and the relevant clause that
the user is accepting.

---

## 7. Tracked exception inventory

The following packages or assets in the tree carry license
characteristics that require ongoing attention. **Auditors check
this list on every release pass.**

### 7.1 AGPL-3.0-or-later packages

| Package | Path | Wrapped as network service? | Notes |
|---|---|---|---|
| ghostscript | `packages/desktop/ghostscript/` | No | User-side PDF renderer; no IPC exposure |
| mupdf | `packages/desktop/mupdf/` | No | User-side PDF reader; no IPC exposure |

If either of the above is wrapped as a service exposing functionality
over D-Bus, a socket, or any IPC layer, an AGPL § 13 compliance step
is required in the same change.

### 7.2 Patent-encumbered components

| Package | Path | Disposition |
|---|---|---|
| fdk-aac | `packages/desktop/fdk-aac/` | Allowed only as a source dep for opt-in ffmpeg-nonfree-helper; never linked into default ffmpeg |
| ffmpeg | `packages/desktop/ffmpeg/` | Default build **drops** `--enable-nonfree` and `--enable-libfdk-aac`; opt-in nonfree variant ships as `ffmpeg-nonfree-helper` |
| x264 / x265 | (as bundled) | Default ffmpeg drops; opt-in via helper |

See [`docs/legal/PATENTS.md`](../legal/PATENTS.md) for the user-facing
patent posture.

### 7.3 Proprietary-payload helpers (P-006)

| Helper | `payload_license:` |
|---|---|
| `packages/extra/chrome-helper` | `LicenseRef-Google-Chrome-ToS` |
| `packages/extra/edge-helper` | `LicenseRef-Microsoft-Edge-EULA` |
| `packages/extra/vscode-helper` | `LicenseRef-Microsoft-VSCode-Software-License` |
| `packages/extra/spotify-helper` | `LicenseRef-Spotify-ToS` |
| `packages/extra/discord-helper` | `LicenseRef-Discord-ToS` |
| `packages/extra/brave-helper` | `LicenseRef-Brave-EULA` |
| `packages/extra/claude-code-helper` | `LicenseRef-Anthropic-Commercial-Terms` |

### 7.4 Vendor-binary firmware

| Package | License | Modifiable? |
|---|---|---|
| `packages/core/intel-ucode` | Intel-Microcode-License | No (vendor-signed binary blobs; redistribute unmodified) |
| `packages/core/linux-firmware` | "Various (redistributable)" — see /lib/firmware/LICENCE.* | No (vendor blobs) |
| AMD microcode (in `linux-firmware`) | AMD-Linux-Firmware-License | No |

Each must ship its upstream LICENSE text under `/usr/share/licenses/<package>/`.

### 7.5 Fetched-at-runtime model weights (InterGen)

| Component | Source | License | User-acceptance gate |
|---|---|---|---|
| Qwen3.5 GGUF (2B / 9B / 35B-A3B) | HuggingFace `unsloth/Qwen3.5-*-GGUF` | `LicenseRef-Tongyi-Qianwen` | Required at first-launch of InterGen (audit P-016) |
| nomic-embed-text-v1.5 | HuggingFace | Apache-2.0 | Same flow |

Each license must be bundled to
`/usr/share/licenses/intergen/MODELS/<model>-LICENSE` at intergen
package install time. The Tongyi Qianwen License is documented at
[`docs/legal/payload-licenses.md`](../legal/payload-licenses.md).

### 7.6 Redistributed third-party signed binaries

| Component | Path | Notes |
|---|---|---|
| `shim-x64.efi` (Fedora-signed for Microsoft UEFI CA) | `packages/core/shim-signed/` | Repackaged from Fedora RPM. Redistribution permission documented at [`docs/governance/fedora-shim-redistribution.md`](fedora-shim-redistribution.md). Pending replacement by InterGenOS-owned MS-signed shim per shim-review PR. |

### 7.7 Methodology-only inheritance (LFS / BLFS)

| Source | InterGenOS use | Constraint |
|---|---|---|
| LFS 13.0 book | Build methodology + package selection | Verbatim text NOT redistributed in shipped binaries or release ISOs. Methodology citation in `docs/` is fair use. |
| BLFS 13.0 book | Build instructions + dep info | Same constraint as LFS |

---

## 8. Compound SPDX expressions and multi-source packages

A package whose `package.yml` declares multiple `source:` entries
must declare a **compound SPDX expression** that reflects all of
the bundled upstreams' license obligations. Audit finding P-022
caught `packages/extra/mariadb/package.yml` declaring
`"GPL-2.0 AND LGPL-2.1"` while bundling a second source (FMT
library, MIT) — the correct expression is
`"GPL-2.0 AND LGPL-2.1 AND MIT"`.

Policy:

- **Every multi-source package must use SPDX compound syntax**:
  `"LICENSE-A AND LICENSE-B"` for combined obligations,
  `"LICENSE-A OR LICENSE-B"` for licensee choice.
- **A pre-commit / pre-push hook validates** that any
  `package.yml` with more than one `source:` entry has a compound
  expression in its `license:` field, and that each component of
  the compound is on the accept list.
- **Static linkage of vendored crates / packages** is treated as
  bundling for license purposes. Rust crates listed in
  `Cargo.lock`, Go modules in `go.sum`, npm modules in
  `package-lock.json` that are statically linked into a shipped
  binary all attach their license obligations to that binary.
  Audit finding P-021 (cargo-c with 420 vendored crates) is the
  canonical instance. The build-time license-extraction pass (see
  § 5) handles inventory.

---

## 9. SPDX header policy

Every InterGenOS-authored source file ships with a 2-line header:

```
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
```

(or `//` for C-family, `<!--`...`-->` for XML/HTML, etc.). This
applies to:

- `pkm/` (the package manager)
- `installer/` (Forge)
- `intergen/` (the assistant)
- `igos-build/` (the build system)
- `scripts/*.{sh,py}` (the build orchestrator and tools)
- `packages/*/build.sh` (build recipes — `package.yml` is metadata
  and does not need an SPDX comment, since SPDX appears in the
  `license:` field)
- `.github/workflows/*.yml`

A pre-commit / pre-push hook validates that new files in these
paths carry the header. Audit finding P-007 (zero of 228
InterGenOS-authored files have headers) is closed by the bulk
header-addition pass referenced in the audit remediation plan.

Files exempt from the header requirement:
- `.md` documentation files (license declared per-file at the
  bottom under "License of this document")
- `.gitignore`, `.gitattributes`, configuration files without
  comment syntax
- Generated files (with a comment indicating they are generated)

---

## 10. New-package license review checklist

When adding a new package to InterGenOS, the author confirms:

1. ☐ The upstream license is on the standard accept list (§ 2) or
   is reviewed and added to this policy via PR before the package
   merges.
2. ☐ The `package.yml` `license:` field is the correct SPDX
   expression — including a compound expression if the package
   bundles or vendors dependencies (§ 8).
3. ☐ The `build.sh` includes a step that extracts the upstream
   LICENSE file to `$DESTDIR/usr/share/licenses/<package>/`.
4. ☐ For helper packages with proprietary payloads: the
   `payload_license:` field is set, and a click-through acceptance
   gate is implemented in the helper's install path (§ 6).
5. ☐ If the upstream is AGPL: the package is not wrapped as a
   network service, or an AGPL § 13 compliance mechanism is
   included.
6. ☐ If the upstream is patent-encumbered: the disposition is
   documented in `docs/legal/PATENTS.md` and the package is not
   in the default-install tier.
7. ☐ If the package fetches additional artifacts at install or
   runtime: each artifact's license is documented and a user-
   acceptance gate exists where applicable.

A pre-push gate (`scripts/check-license-policy.py`, to be authored
as part of the implementation pass closing this policy's audit
findings) automates the mechanical parts of the checklist.

---

## 11. Cross-references

- [`LICENSE`](../../LICENSE) — root GPL-3.0-or-later license.
- [`SOURCES.md`](../../SOURCES.md) — source-availability commitment.
- [`TRADEMARK.md`](../../TRADEMARK.md) — brand carve-out.
- [`PRIVACY.md`](../../PRIVACY.md) — privacy notice.
- [`EXPORT-NOTICE.md`](../../EXPORT-NOTICE.md) — export-control
  posture.
- [`DCO.md`](../../DCO.md) — contributor sign-off requirement.
- [`CREDITS`](../../CREDITS) — third-party attribution.
- [`docs/legal/PATENTS.md`](../legal/PATENTS.md) — patent posture
  (authored as part of this sprint).
- [`docs/legal/payload-licenses.md`](../legal/payload-licenses.md) —
  detailed payload-license references for the helper pattern
  (P-006 / P-016 follow-on work).
- [`docs/governance/fedora-shim-redistribution.md`](fedora-shim-redistribution.md)
  — Fedora-signed shim redistribution permission (P-009 follow-on).
- [SPDX License List](https://spdx.org/licenses/) — canonical
  SPDX expressions.

---

## 12. Provenance and amendment

This policy was authored 2026-05-18 as part of the InterGenOS v1.0
legal-readiness sprint, closing audit finding **P-005** (High: no
license-compatibility audit for AGPL-3.0 packages — also addressed
here is the broader gap of having no internal license-discipline
doc) from the 2026-05-18 comprehensive state audit.

Amendments to this policy are pull requests to this file. Material
changes (additions to the accept list, changes to the AGPL stance,
new tracked exceptions) require review by the build-system
coordinator and approval by the operator. Clarifications,
typographical corrections, and reorganization are routine and
require only review.

— InterGenJLU
