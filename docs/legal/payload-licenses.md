# InterGenOS — Payload Licenses (proprietary fetches via helpers and at runtime)

**Audience:** users running `pkm install <helper>` or launching
InterGen with a model download required; developers maintaining
the helper packages; auditors checking compliance.

**Status:** authoritative reference for the EULAs the user accepts
by using each helper or fetched-at-runtime component. Each entry
documents the canonical URL, the last-checked date, the SPDX
`LicenseRef-` identifier used in `package.yml`, and a non-binding
summary of the user-facing key restrictions.

The summaries below are **not** the operative license terms. The
operative terms are the vendor's published EULA as in force at the
time of acceptance, retrieved through the URL given. If the
vendor's URL has moved or the EULA's substance has changed since
the last-checked date, the user is bound by the current version
the vendor has linked through the canonical URL, **not** by the
summary in this file. The InterGenOS project will refresh this
document on a best-effort basis as helpers are exercised or
upstreams revise their terms.

---

## How acceptance works

Each helper package and each InterGen model fetch displays a
**click-through acceptance prompt** before any network download or
post-install step. The prompt shows:

- The vendor and product
- The canonical URL of the EULA
- A non-binding summary of the headline restrictions (from this
  file)
- Two buttons: **Accept and continue** / **Cancel** (in the GUI),
  or `y` / `n` prompts in the TUI / CLI

If the user declines, the helper exits without installing. If the
user accepts, the helper writes an acceptance record to
`/var/lib/intergen/legal/<helper>-<version>-accepted.json`
containing the timestamp, the helper version, and a SHA256 of the
canonical URL at the time of acceptance. Re-running the same
helper at the same version skips re-prompting; an upgrade to a new
helper version (which may correspond to a vendor EULA update) re-
prompts.

---

## Index

| LicenseRef | Product | Helper | Last-checked |
|---|---|---|---|
| `LicenseRef-Google-Chrome-ToS` | Google Chrome | `packages/extra/chrome` | 2026-05-18 |
| `LicenseRef-Microsoft-Edge-EULA` | Microsoft Edge | `packages/extra/edge` | 2026-05-18 |
| `LicenseRef-Microsoft-VSCode-Software-License` | Visual Studio Code | `packages/extra/vscode` | 2026-05-18 |
| `LicenseRef-Spotify-ToS` | Spotify Desktop | `packages/extra/spotify` | 2026-05-18 |
| `LicenseRef-Discord-ToS` | Discord | `packages/extra/discord` | 2026-05-18 |
| `LicenseRef-Brave-EULA` | Brave Browser | `packages/extra/brave` | 2026-05-18 |
| `LicenseRef-Anthropic-Commercial-Terms` | Claude Code CLI | `packages/extra/claude-code` | 2026-05-18 |
| `LicenseRef-NVIDIA-CUDA-EULA` | NVIDIA CUDA Toolkit | `packages/compute/cuda-toolkit` | 2026-08-04 |
| `LicenseRef-Tongyi-Qianwen` | Qwen3.5 (LLM model weights) — **superseded, see below** | `packages/ai/intergen` (model fetch) | 2026-07-02 |

---

## LicenseRef-Google-Chrome-ToS

**Vendor:** Google LLC
**Product:** Google Chrome (Linux x86_64)
**Canonical URL:** `https://www.google.com/intl/en/chrome/terms/`
**Distribution endpoint:** `https://dl.google.com/linux/direct/google-chrome-stable_current_x86_64.rpm`

**Key restrictions (non-binding summary):**

- The Chrome binary is licensed for use under Google Chrome
  Terms of Service. Source code (Chromium) is available
  separately under BSD-style licenses; the binary in
  `google-chrome-stable` includes proprietary additions (Widevine
  CDM, Adobe Flash sandbox interop on older versions, Google-
  branding) that are not redistributable as part of the user's
  fork.
- Use is permitted for personal and commercial purposes by the
  installing user; redistribution of the binary by the user is
  not permitted.
- Google's privacy policies apply to use of Chrome.
- Auto-update behavior fetches from Google's servers; InterGenOS
  does not gate or proxy these.

**Reference:** Google's Chrome ToS at the canonical URL above is
the controlling text.

---

## LicenseRef-Microsoft-Edge-EULA

**Vendor:** Microsoft Corporation
**Product:** Microsoft Edge for Linux
**Canonical URL:** `https://www.microsoft.com/en-us/legal/terms-of-use`
(general); product-specific EULA accessed within the application

**Distribution endpoint:** `https://packages.microsoft.com/repos/edge/`

**Key restrictions (non-binding summary):**

- The Edge binary is licensed for personal or business use by
  the installing user under Microsoft's standard end-user
  license terms.
- Redistribution of the binary is restricted to Microsoft-
  authorized channels.
- Edge fetches from Microsoft servers for updates; InterGenOS
  does not gate.
- Microsoft's privacy and telemetry behavior applies; refer to
  Edge's first-run settings.

---

## LicenseRef-Microsoft-VSCode-Software-License

**Vendor:** Microsoft Corporation
**Product:** Visual Studio Code (the "VS Code" Microsoft-branded
build; distinct from the open-source Code-OSS upstream)
**Canonical URL:** `https://code.visualstudio.com/license`

**Distribution endpoint:** `https://code.visualstudio.com/sha/download?build=stable&os=linux-x64`

**Key restrictions (non-binding summary):**

- The Microsoft-branded VS Code binary is licensed under the
  **VS Code Software License**, not under the MIT license that
  governs the open-source Code-OSS upstream. These are
  distinguishable products:
  - Code-OSS is MIT-licensed and freely buildable from the
    upstream source; it draws extensions from the Open VSX
    Registry rather than Microsoft's Marketplace.
  - `packages/extra/vscode` fetches the Microsoft-
    branded VS Code, which adds proprietary components and a
    different license.
- The Microsoft binary may include telemetry; users can disable
  it in settings.
- Marketplace access (extensions) requires acceptance of
  Microsoft Marketplace terms separately.

**Note:** The helper exists for users who specifically need
Marketplace extensions, which Microsoft restricts to the branded
build. The MIT-licensed Code-OSS build covers the same editing
experience for everyone else, sourcing extensions from Open VSX.

---

## LicenseRef-Spotify-ToS

**Vendor:** Spotify Technology S.A.
**Product:** Spotify Desktop Client (Linux)
**Canonical URL:** `https://www.spotify.com/legal/end-user-agreement/`

**Distribution endpoint:** `http://repository.spotify.com/pool/non-free/s/spotify-client/`

**Key restrictions (non-binding summary):**

- A Spotify account (free or paid) is required to use the
  client; the binary is not useful without account terms
  accepted.
- The Spotify EULA includes restrictions on reverse
  engineering, redistribution, and use in commercial
  background-music settings without separate licensing.
- Spotify's privacy and data-collection policies apply.

---

## LicenseRef-Discord-ToS

**Vendor:** Discord Inc.
**Product:** Discord Desktop (Linux)
**Canonical URL:** `https://discord.com/terms`

**Distribution endpoint:** `https://discord.com/api/download?platform=linux&format=tar.gz`

**Key restrictions (non-binding summary):**

- A Discord account is required; account terms apply.
- Discord's EULA includes standard restrictions (no reverse
  engineering, no redistribution).
- Discord's privacy policies apply; the client is
  network-attached and exchanges voice/text/screen data with
  Discord's servers.

---

## LicenseRef-Brave-EULA

**Vendor:** Brave Software, Inc.
**Product:** Brave Browser (Linux x86_64)
**Canonical URL:** `https://brave.com/terms-of-use/`

**Distribution endpoint:** `https://brave-browser-apt-release.s3.brave.com/`

**Key restrictions (non-binding summary):**

- Brave is open source (MPL-2.0) at the source level, but the
  binary distribution from brave.com includes proprietary
  additions (Brave Rewards, Brave Wallet integrations) governed
  by additional terms.
- Use of Brave Rewards or Brave Wallet requires separate terms
  acceptance within the app.
- Brave's privacy posture is favorable relative to other Chromium
  forks, but is not zero (some opt-out telemetry).

---

## LicenseRef-Anthropic-Commercial-Terms

**Vendor:** Anthropic PBC
**Product:** Claude Code (CLI tool — the Anthropic-distributed
binary, not source available)
**Canonical URL:** `https://www.anthropic.com/legal/commercial-terms`

**Distribution endpoint:** `https://github.com/anthropics/claude-code/releases/`

**Key restrictions (non-binding summary):**

- Claude Code is licensed under Anthropic's Commercial Terms
  for use with an Anthropic API key.
- The user must have a paid Anthropic API account or operate
  under Anthropic's free-tier limits (subject to change).
- Use is for the licensed user; redistribution is not permitted.
- Anthropic's data-handling for API requests is governed by their
  Usage Policies and Privacy Policy, accessed through the
  canonical URL.

---

## LicenseRef-NVIDIA-CUDA-EULA

**Vendor:** NVIDIA Corporation
**Product:** NVIDIA CUDA Toolkit (Linux x86_64 runfile installer)
**Canonical URL:** `https://docs.nvidia.com/cuda/eula/index.html`

**Distribution endpoint:** `https://developer.download.nvidia.com/compute/cuda/13.3.1/local_installers/` — the versioned path the helper downloads from. The unversioned parent directory is not browsable; NVIDIA's index of released versions is at `https://developer.nvidia.com/cuda-toolkit-archive`.

**Pinned version:** 13.3.1 (`cuda_13.3.1_610.43.02_linux.run`).
SHA256 `9f98ec1f6c950401041d3f1308e221f0d5db8771a8e10569001b64caaee31a92`,
recorded in `packages/compute/cuda-toolkit/helper/igos-install-cuda-toolkit`.
The helper verifies this checksum before unpacking anything and refuses the
install on a mismatch.

**Where the licence text comes from.** Not from a URL we hope stays
alive: the agreement ships inside the runfile as `EULA.txt`, and the
helper writes that exact file to
`/var/lib/intergen/legal/cuda-toolkit-<version>-EULA.txt` and to
`/opt/cuda/EULA.txt`. The text a user agreed to is on their own disk.

**Key restrictions (non-binding summary; read against the 13.3.1
`EULA.txt` shipped in the pinned runfile):**

- §2.1 licenses the toolkit for developing applications **for use on
  systems with NVIDIA GPUs**.
- §2.2 limits redistribution to the components listed in Attachment A.
  Attachment A is a list of **runtime libraries** — on Linux
  `libcudart.so`, `libcublas.so`, `libcublasLt.so`, `libcufft.so`,
  `libcusparse.so` and their static twins, among others — and permits
  distributing them **"with applications developed by you"**.
- **`nvcc`, the CUDA compiler, is not in Attachment A.** It is not
  redistributable. This is why InterGenOS does not carry the toolkit on
  its mirror and fetches it on the user's machine instead.
- §2.3 permits redistribution of the Linux-only portions only if the
  object code files are unmodified apart from decompression.
- §1.5 forbids using the toolkit in a way that would subject it to an
  open source licence — including any licence requiring it to be
  distributable at no charge.
- §1.6 states the toolkit is not certified for safety-critical systems
  (avionics, medical, automotive, life support).

**What InterGenOS relies on.** Nothing in Attachment A. InterGenOS
publishes no NVIDIA binaries at all, not even the ones Attachment A
would permit: `packages/compute/llama-cpp-cuda` contains only its own
compiled output from MIT-licensed llama.cpp source, and resolves
`libcudart`, `libcublas` and `libcublasLt` dynamically at run time from
the `/opt/cuda` install this helper creates. The CUDA driver library
`libcuda.so.1` comes from the `nvidia` driver package, not from here.
Declining to exercise a redistribution right we arguably have is
deliberate: it keeps the mirror free of vendor binaries and keeps the
question of what we may republish from ever arising.

---

## LicenseRef-Tongyi-Qianwen — SUPERSEDED

> **This identifier is no longer the declared license for the Qwen3.5
> weights InterGenOS fetches.** Decided 2026-07-02: the signed model
> manifest (`intergen/data/models-manifest.json`, verified against the
> release master key) declares **`Apache-2.0`** for every Qwen3.5 entry —
> the 2B, the 9B, and the 35B-A3B. That signed field is the sole authority
> the runtime consults: `intergen/model_manager.py` reads the acceptance
> gate from it and treats permissive licenses as auto-accepted, so a Qwen
> model download presents **no first-launch acceptance prompt** and the
> obligations described below — the use-restriction list, the
> monthly-active-user scale gate, and the attribution requirement — are not
> asserted against InterGenOS users by the shipped configuration.
>
> The section is retained rather than deleted because it is the record of
> the prior disposition and of what a differently-licensed weight would
> require. **If the license of a fetched model ever changes, the change is
> made in the signed manifest first**; this document follows it.
> The paragraphs below describe the Tongyi Qianwen License as it was
> summarised at the earlier disposition and are not a statement of the
> terms currently in force for the shipped weights.

**Vendor:** Alibaba Cloud
**Product:** Qwen3.5 model weights (2B / 9B / 35B-A3B
quantizations, in GGUF format)
**Canonical URL:** `https://github.com/QwenLM/Qwen3.5/blob/main/LICENSE`
(or `https://huggingface.co/Qwen/Qwen3.5-*/blob/main/LICENSE`)

**Distribution endpoint:** HuggingFace
`https://huggingface.co/unsloth/Qwen3.5-*-GGUF/resolve/main/`

**Key restrictions (non-binding summary):**

The Tongyi Qianwen License is a **source-available, non-FOSS**
license with the following key provisions:

- **Use case restrictions:** The license imposes a use-restrictions
  list including (but not limited to): military uses, surveillance
  applications, generation of CSAM, generation of content that
  incites violence or discrimination, and other restricted use
  cases enumerated in the license.

- **Scale gate:** Use by services with more than 100 million
  monthly active users requires a separate commercial license
  from Alibaba; InterGenOS is well below this threshold today
  but downstream redistributors and service operators must
  evaluate independently.

- **Attribution requirement:** Derivative products that use the
  model output in user-facing contexts must display **"Powered
  by Qwen"** attribution where the model is the primary inference
  engine. InterGen displays this attribution in the assistant's
  UI per Tongyi Qianwen License § 4.

- **Output ownership:** Per the license, the user owns the
  output of their inference; InterGenOS does not assert any
  rights in user output.

- **Modification:** Modifying the weights for redistribution is
  permitted under license, subject to clearly marking the
  modification and the corresponding obligations.

**InterGenOS handling:**

- The full Tongyi Qianwen License text is bundled to
  `/usr/share/licenses/intergen/MODELS/qwen-3.5-LICENSE` at
  intergen package install.
- The InterGen first-launch acceptance gate displays the
  license summary above + the canonical URL + the user-restrictions
  list, requiring explicit acceptance before the model download
  begins.
- The acceptance record is stored at
  `~/.local/share/intergen/legal/qwen-3.5-accepted.json`
  (per-user, not system-wide, because the model is per-user data).
- The "Powered by Qwen" attribution is rendered by
  `intergen --version`, which names the Qwen-family model present
  on the machine and the license it is used under. It is printed only when
  such a model is on the machine: a Tier-1 box serves
  InternVL3.5-2B, and the attribution would not be a true statement
  about what powers it there.
- Decided 2026-08-27: an earlier revision of this entry also stated
  that the InterGen conversation view and the firstboot greeter
  render the attribution. Neither does — the string appears in
  neither surface — so the sentence above is corrected to describe
  only the surface that renders it. The section 4 attribution
  obligation for those two surfaces is open and unmet.

---

## How this file is maintained

- **On creation of a new helper:** the helper author adds a
  `LicenseRef-<short-name>` entry here, sets `payload_license:` in
  the helper's `package.yml`, and updates the [Index](#index)
  table.
- **When a vendor updates their EULA:** the entry's last-checked
  date is refreshed and the summary is reviewed for material
  changes. If a summary's accuracy is in doubt, the entry is
  updated and the helper's version is bumped so accepted-record
  files become stale and the next install re-prompts.
- **When a vendor changes their distribution endpoint:** the entry
  is updated; this is not a license change.
- **When a vendor goes out of business or is sanctioned:** the
  helper is moved to a deprecated state and the entry is
  preserved here for the record.

---

## Cross-references

- [`docs/governance/license-policy.md`](../governance/license-policy.md) —
  the policy this document operationalizes for helper packages.
- [`SOURCES.md`](../../SOURCES.md) — source-availability commitment;
  payload licenses do not affect InterGenOS-side source
  availability of the helper script itself.
- [`PRIVACY.md`](../../PRIVACY.md) — the privacy posture of
  InterGenOS itself; payload software has its own privacy posture
  per the vendor.
- [`docs/legal/PATENTS.md`](PATENTS.md) — patent posture (independent
  of payload-license question, but related for AAC/codec helpers).

---

## Provenance

This file was authored 2026-05-18 as part of the InterGenOS v1.0
legal-readiness sprint. It implements the documentation surface
required by `docs/governance/license-policy.md` § 6 and closes
the documentation portion of audit findings **P-006** (High:
helper packages mislabel proprietary downstream as GPL-3) and
**P-016** (Security: Qwen weights with no acceptance gate).

The mechanical implementation work — adding the
`payload_license:` field to each helper's `package.yml`, the
acceptance-gate code path in the helper install flow, the model-
acceptance gate in `intergen/model_manager.py`, and the
"Powered by Qwen" UI attribution — is tracked separately as part
of this sprint's package-fix pass.

**License of this document.** This `payload-licenses.md` is
licensed **CC0-1.0**.

— InterGenJLU
