# InterGenOS Package Tiers — Canonical Definitions

**The source of truth for what each `tier:` field in `package.yml` means.**

This document is the operational reference for Build Development Rulebook **Rule 1** ("Tier reflects what a package IS, not what's convenient to make the topo-sort happy"). Every tier assignment in `packages/*/*/package.yml` is defensible against the definitions below. The programmatic checker in `scripts/validate-package-tiers.py` enforces this.

If a package does not fit any tier definition below, the canonical definitions are wrong (extend them) or the package should not ship in v1.0 (move to a planning doc). The package's tier is **never** set to make a build succeed.

---

## Tier ordering — strict total order

```
toolchain  ─►  core  ─►  base  ─►  desktop  ─►  extra
                                          │
                                          └─►  ai
```

Build proceeds left-to-right. `extra` and `ai` are peers (both build on the desktop substrate); they do not depend on each other.

**The cross-tier dependency rule (load-bearing).** Every package's declared `dependencies.build` and `dependencies.host` entries must resolve to a package in the **same tier or an earlier tier**. A `tier:core` package may depend on a `tier:toolchain` or `tier:core` package. It may NOT depend on a `tier:desktop`, `tier:extra`, or `tier:ai` package. The build chroot would simply not have the dependency available when the consumer is built. Backward cross-tier build-deps are a Rule 1 violation **regardless of cascade considerations**.

When a real upstream relationship crosses a tier boundary backward — e.g., `cairo` (would-be `tier:desktop` library) requires `harfbuzz` for shaping, and `harfbuzz` is also `tier:desktop` — the resolution is **never** to demote `cairo` to whatever tier `harfbuzz` lives in (that's Rule 1 violation by cascade-convenience). The resolution is one of:

  1. Place both packages in the tier that fits their nature (often the same tier, then ordering within the tier handles it).
  2. If their nature genuinely puts them in different tiers and the dep is real, the consumer is in the **later** tier; that's the correct shape and no fix is needed.
  3. If they form a true required-edge cycle within their natural tier, author a **bootstrap variant** in that tier (see "Bootstrap variants" below). This is the LFS/BLFS pattern (`glib2-bootstrap → glib2`, `freetype2-pass1 → freetype2`).

---

## LFS Chapter 8 ordering is sacrosanct

This is non-negotiable and tested by the project's history (alternate orderings have broken the build every time we've tried).

**Rule.** The order of packages in LFS Chapter 8 (the final-system core build) follows the LFS book exactly. No reordering, no insertions before a Ch 8 package, no version skips, no sequence changes. This is also Build Development Rulebook **Rule 13**.

**Where additions go.** Any `tier: core` package that is NOT in LFS Ch 8 — including every Python-build-tool, every TLS-chain library beyond LFS Ch 8's set, the package manager, the Secure Boot toolchain, and the language runtimes (Node, Ruby, Rust, etc.) — is appended at the **tail end** of `scripts/chroot-build-core-extra.sh`, which runs immediately after `chroot-build-ch8.sh`. New `tier: core` packages NEVER get inserted between Ch 8 packages.

**Where 2-pass cyclic bootstrap variants go.** If a `tier: core` package — whether in LFS Ch 8 or added after it — has a real upstream-required build dependency that creates a cycle with another `tier: core` package or with a later-tier package, the resolution is a 2-pass variant authored at the **tail end** of `chroot-build-core-extra.sh`:

  1. `<pkg>-pass1` (or `<pkg>-bootstrap`) builds with the cyclic feature disabled.
  2. The cycle-completing dependency builds against pass1.
  3. `<pkg>` (full build) re-builds at the tail end with all features enabled.

The 2-pass pattern is the **standard answer** to cycle-back deps, not an exotic recourse. We use it for:

  - `glib2-bootstrap` → `glib2` (introspection cycle)
  - `freetype2-pass1` → `freetype2` (HarfBuzz cycle)
  - `gst-plugins-base-pass2`, `gdk-pixbuf-pass2`, `libtiff-pass2`, `lame-pass2`, `pyyaml-pass2`, `systemd-pass2`, `linux-kernel-pass2` (see `scripts/migrate-pkm-supersedes.sh`)

When `polkit`, `NetworkManager`, `Node.js`, `Ruby`, `Rust`, or any other `tier: core` addition needs a build-time dependency that is naturally `tier: desktop`, we author the 2-pass at the tail end of `chroot-build-core-extra.sh` to satisfy the cycle. We do NOT demote the consumer to a later tier to avoid the cycle, and we do NOT promote unrelated `tier: desktop` packages to `tier: core` just because the cycle-completing dep happens to live there.

**Why this matters for reproducibility.** A deterministic build order is a prerequisite for byte-identical reproducible builds. Reordering Ch 8 packages or inserting things between them produces different binaries on different days — that's the failure mode the LFS book guards against. Appending to the tail is order-preserving, and 2-pass variants are deterministic (each pass is itself deterministic and the supersedes register documents which pass wins).

---

## The six tiers

### `tier: toolchain` — cross-compilation toolchain

**One-line definition.** The host-side toolchain used to bootstrap the target system. None of these packages ship in the final ISO.

**Includes.** LFS Chapters 5-7: cross-binutils, cross-gcc, target glibc (temp), libstdc++ pass1, target binutils pass2, target gcc pass2, temporary userland (bash, coreutils, diffutils, file, findutils, gawk, grep, gzip, make, patch, perl, python, sed, tar, texinfo, xz, util-linux).

**Excludes.** Anything intended to ship in the final ISO.

**LSB mapping.** None — toolchain doesn't run on the target.

**Build position.** First. Built on the host before the chroot is entered.

**Wired by.** `scripts/toolchain-build.sh` and `scripts/temp-tools-build.sh` (inline pattern; no `run_package` calls).

---

### `tier: core` — foundational system

**One-line definition.** Everything the target system requires to boot, run, manage packages, and self-update. The non-negotiable substrate.

**Definition.** A package belongs in `tier: core` if **any** of the following is true:

  - It is in LFS Chapter 8 (the final-system core build per the LFS book).
  - It is required at boot or by `systemd` to bring the system up.
  - It is part of the Secure Boot / initramfs / bootloader chain (efivar, efitools, sbsigntool, mokutil, shim-signed, busybox-static, kernel).
  - It is the package manager itself (`pkm`) or its hard dependencies.
  - It is a foundational system library (C/C++) that 30%+ of the rest of the distribution depends on (libc, libstdc++, ncurses, readline, zlib, openssl/gnutls, curl, libxml2, sqlite, etc.).
  - It is a build-system tool required to build a substantial fraction of the rest of the distribution (cmake, meson, ninja, pkg-config, autoconf, automake, libtool, bison, flex, gettext, perl, python + python build infrastructure).
  - It is required to satisfy the **LSB Base** ABI for runtime guarantees: glibc, libstdc++, libm, libdl, libz, libcrypt, libpthread, libgcc_s.

**Examples.** kernel, glibc, gcc, binutils, systemd, shadow, bash, coreutils, util-linux, e2fsprogs, kmod, eudev, openssl, gnutls, curl, wget, git, cmake, meson, ninja, pkg-config, perl, python, ruby, rust, llvm, node.js, openssh, sudo, polkit, networkmanager, zlib, ncurses, readline, libxml2, libxslt, sqlite, lua, dbus, glib2, libffi, libidn2, libtasn1, libunistring, p11-kit, nss, nspr, make-ca, libpsl, mitkrb, popt, rpm, gnupg2, lvm2, cryptsetup, btrfs-progs, dosfstools, parted, hatchling, hatch-vcs, hatch-fancy-pypi-readme, setuptools-scm (Python build infrastructure for Python packages we ship).

**On language runtimes (Node.js, Ruby, Rust, Python).** These are `tier: core` because they are foundational language runtimes that many parts of the system can be authored against. They are built **feature-complete** — any build dependency they need from a naturally-later tier is satisfied via 2-pass bootstrap variants authored at the tail end of `chroot-build-core-extra.sh`. We do not strip features to make the dep graph easier.

**On polkit and NetworkManager.** Both are `tier: core` because they implement capabilities the running system requires (session authorization for multi-user operation; network connectivity for self-update and remote management). Servers use `systemd-networkd` (already `tier: core`); desktops use NetworkManager — co-locating both in `tier: core` keeps the network-stack story coherent. Any build dependencies these pull in from naturally-later tiers are resolved via 2-pass at the tail end of `chroot-build-core-extra.sh`.

**Counter-examples — these MUST NOT be in `tier: core`:**

  - LibreOffice (user-facing office suite — `tier: extra`)
  - GIMP, Inkscape, Krita (user-facing creative apps — `tier: extra`)
  - Web browsers (`tier: extra`)
  - GNOME Shell, GNOME applications (`tier: desktop`)
  - GTK, Qt, Wayland, X11 libraries (`tier: desktop`)
  - Mesa, Vulkan loader (`tier: desktop` — graphics stack)
  - CUPS, Ghostscript (`tier: desktop` — LSB Print is a desktop-runtime capability)
  - LLM model files (`tier: ai`)

**LSB mapping.** LSB Base is fully resident in `tier: core`. LSB Languages (Perl, Python) is also resident here.

**Build position.** Second. Built inside the chroot.

**Wired by.** `scripts/chroot-build-ch8.sh` (LFS Ch 8 packages) + `scripts/chroot-build-core-extra.sh` (everything else `tier: core` beyond LFS).

---

### `tier: base` — end-user CLI utilities

**One-line definition.** Quality-of-life CLI tools an experienced Linux user expects on any general-purpose system, but that aren't strictly required for the system to function.

**Definition.** A package belongs in `tier: base` if **all** of the following are true:

  - It is a CLI utility (no GUI dependency).
  - The system can boot and function without it.
  - It is conventionally present on any general-purpose Linux distribution.
  - It is NOT in LFS Ch 8 and NOT a foundational library.

**Examples.** htop, btop, rsync, strace, screen, tmux, file, tree, less (if not in core), nano, vim (if not in core), jq, ripgrep, fd, bat, neofetch, lsof, ncdu, dialog, dialog-derivatives, mc.

**Counter-examples — these MUST NOT be in `tier: base`:**

  - Foundational libraries (those belong in `tier: core`).
  - Anything with a GUI dep (those belong in `tier: desktop` or later).
  - Build-system tools (those belong in `tier: core`).

**LSB mapping.** None directly. LSB does not specify the CLI utility set beyond the POSIX-mandated tools that are already in `tier: core`.

**Build position.** Third.

**Wired by.** `scripts/chroot-build-base.sh` (via `run_package` calls).

---

### `tier: desktop` — graphical environment

**One-line definition.** The GUI substrate: display server, graphics stack, fonts, toolkits, desktop environment, audio, integration services. Everything that makes the box a "desktop" rather than a server.

**Definition.** A package belongs in `tier: desktop` if **any** of the following is true:

  - It is part of the Wayland or X11 protocol implementation (wayland, libX11, xwayland, libxkbcommon, etc.).
  - It is part of the graphics stack (Mesa, libdrm, Vulkan, OpenGL helpers, libepoxy).
  - It is a font / text rendering library (fontconfig, freetype2 pass2, harfbuzz, pango, fribidi, graphite2, cairo, pixman).
  - It is a GUI toolkit or its core dependencies (GTK 3/4, gdk-pixbuf, librsvg, libadwaita, glycin, Qt if/when we ship it).
  - It is a multimedia/audio stack package (GStreamer + plugins, PulseAudio, PipeWire, WirePlumber, ALSA library, libogg, libvorbis, libopus, flac, lame, libsndfile).
  - It is a GNOME shell or core GNOME application/library (GNOME Shell, mutter, gnome-desktop, gnome-keyring, gvfs, gnome-control-center, evince, nautilus, totem, etc.).
  - It is a desktop integration service (polkit, udisks2, geoclue, modemmanager, networkmanager [TBD], avahi, color management, accessibility).
  - It is a desktop-only Python module or library (most Python GUI bindings, desktop-only Python helpers).

**Examples.** wayland, wayland-protocols, xwayland, libX11, libXrandr, libXcursor, libxkbcommon, mesa, libdrm, vulkan-loader, libepoxy, fontconfig, freetype2, harfbuzz, pango, cairo, gtk3, gtk4, gdk-pixbuf, librsvg, libadwaita, gstreamer, pulseaudio, pipewire, wireplumber, gnome-shell, mutter, gnome-desktop, gvfs, evince, cups, ghostscript, udisks2, geoclue, evolution-data-server.

**Counter-examples — these MUST NOT be in `tier: desktop`:**

  - User-facing applications layered on the desktop (those belong in `tier: extra`).
  - Foundational libraries used outside the desktop stack (those belong in `tier: core`).
  - LLM model files or AI runtime (`tier: ai`).

**LSB mapping.** **LSB Desktop**: GTK, Qt, X11 libs, OpenGL. **LSB Print**: CUPS, Ghostscript (yes, print belongs in `tier: desktop`, not `tier: core` — it is a desktop-runtime capability).

**Build position.** Fourth.

**Wired by.** `scripts/chroot-build-desktop.sh` invoking `igos-build.py --tier desktop` (the Python builder walks topo closure of all `tier: desktop` packages).

---

### `tier: extra` — user-facing applications

**One-line definition.** Applications a user installs to do work on top of the desktop. Not part of the desktop itself.

**Definition.** A package belongs in `tier: extra` if **all** of the following are true:

  - It is a complete, user-launchable application (not a library, not an integration service, not a daemon that runs at boot).
  - It is optional to the system's function — uninstalling it does not break the desktop.
  - It is NOT a GNOME-core application that ships with GNOME by default (those are `tier: desktop`).
  - It is NOT part of the AI assistant stack (that's `tier: ai`).

**Examples.** LibreOffice, GIMP, Inkscape, Blender (if shipping), Code-OSS, Node.js (if user-facing — see "Open questions"), browser variants, media players beyond GNOME defaults, IDEs, communication apps.

**Counter-examples — these MUST NOT be in `tier: extra`:**

  - GNOME-core apps (those are `tier: desktop`).
  - Libraries — even libraries used ONLY by `tier: extra` apps. If something is a library, it goes in the tier of its consumer (so libraries used only by `tier: extra` packages are `tier: extra`, but they are not "applications").
  - Anything required at boot.

**LSB mapping.** None directly.

**Build position.** Fifth.

**Wired by.** `scripts/chroot-build-extra.sh` invoking `igos-build.py --tier extra`.

---

### `tier: ai` — local AI assistant

**One-line definition.** InterGen + the local LLM runtime + bundled models.

**Definition.** A package belongs in `tier: ai` if it is part of the InterGen AI assistant stack, the local LLM runtime (`llama.cpp`), or a bundled model artifact.

**Examples.** intergen (the assistant daemon + CLI + MCP tools), llama.cpp, model files (if packaged).

**Counter-examples.**

  - General-purpose Python/Rust/C++ libraries that the AI stack uses but that have non-AI consumers (those belong in their natural tier, usually `tier: core`).
  - GUI components (those go in `tier: desktop`).

**LSB mapping.** None.

**Build position.** Sixth (peer of `tier: extra`).

**Wired by.** `scripts/chroot-build-ai.sh` invoking `igos-build.py --tier ai`.

---

## Bootstrap variants

A small number of foundational packages have **upstream-required circular build dependencies** within their tier. The classic case in `tier: core` is `glib2` requiring `gobject-introspection` for full features, while `gobject-introspection` requires `glib2`. The LFS/BLFS-standard resolution is the **bootstrap variant**:

  1. Author `<package>-bootstrap` (or `<package>-pass1`) as a separate package directory with the same tier as the full package.
  2. Bootstrap variant builds with the circular feature disabled (`glib2-bootstrap`: no introspection).
  3. The downstream dependency builds against the bootstrap.
  4. The full package rebuilds with the dependency now available.

Bootstrap variants:

  - Live in the SAME tier as the full package they bootstrap. They are not "convenience demotions" to an earlier tier.
  - Are named `<base>-bootstrap` or `<base>-pass1` and `<base>` for the full build.
  - Get an entry in `scripts/migrate-pkm-supersedes.sh` so `pkm` knows the full build supersedes the pass1.
  - Are documented in their `package.yml` with a comment explaining the cycle.

This pattern is what real distributions (Arch, Fedora, Debian, Gentoo, BLFS itself) use. We use it too. **It is not a tier-change.**

---

## How to determine a package's tier — decision tree

```
1. Is the package part of LFS Ch 5-7 host toolchain or temp tools?
   → tier: toolchain  STOP

2. Is the package in LFS Ch 8?
   → tier: core  STOP

3. Is the package part of the Secure Boot / initramfs / bootloader chain
   (efivar, efitools, sbsigntool, mokutil, shim-signed, busybox-static, kernel)?
   → tier: core  STOP

4. Is the package the package manager (pkm) or its hard dep?
   → tier: core  STOP

5. Is the package a foundational system library or build-system tool
   (used by many parts of the system, including by non-desktop packages)?
   → tier: core  STOP

6. Is the package required to satisfy LSB Base (glibc, libstdc++, libm, libdl,
   libz, libcrypt, libpthread, libgcc_s)?
   → tier: core  STOP

7. Does the package have any GUI dependency in its build or runtime?
   → continue (skip step 8)
   else
   → Is it a CLI utility that a Linux user expects on any general-purpose system?
     → tier: base  STOP

8. Is the package part of the GUI substrate (display server, graphics, fonts,
   toolkits, desktop env, audio stack, GNOME-core, desktop integration service)?
   → tier: desktop  STOP

9. Is the package part of the AI assistant stack?
   → tier: ai  STOP

10. Is the package a user-facing application layered on the desktop?
    → tier: extra  STOP

11. Otherwise — STOP and surface to maintainer. The definitions need extending
    OR the package does not belong in v1.0 (move to a planning doc).
```

Library packages take the tier of their **consumer**:
  - Library used only by `tier: desktop` packages → `tier: desktop`
  - Library used only by `tier: extra` packages → `tier: extra`
  - Library used by packages in multiple tiers → take the **earliest** tier.

---

## How to add a new package

  1. Run the decision tree above to determine the tier.
  2. Create `packages/<tier>/<name>/package.yml` with `tier: <tier>` set.
  3. Run `python3 scripts/validate-package-tiers.py <name>` — must report OK.
  4. Run `python3 scripts/preflight-tier-coverage.py` — must report PASS.
  5. Wire into the appropriate `scripts/chroot-build-<phase>.sh` per the tier:
     - `tier: core`  → `run_package` line in `chroot-build-ch8.sh` or `chroot-build-core-extra.sh`
     - `tier: base`  → `run_package` line in `chroot-build-base.sh`
     - `tier: desktop` / `tier: extra` / `tier: ai`  → automatic via `igos-build.py --tier`

## How to move a package between tiers

  1. Run the decision tree against the package.
  2. If the result differs from the current `tier:` field, that is the move.
  3. `git mv packages/<old-tier>/<name>/ packages/<new-tier>/<name>/`
  4. Update the `tier:` field in `package.yml`.
  5. Audit all consumers via `git grep` (per Rule 9). If any consumer is in a tier that's now BEFORE the moved package's new tier, that consumer is itself wrong: re-run the decision tree on each consumer.
  6. Update the relevant `chroot-build-<phase>.sh` script — add/remove `run_package` lines.
  7. Re-run preflight + validator. Both must PASS before commit.

## Validation: `scripts/validate-package-tiers.py`

To be authored as part of this layout-correction effort. Behavior:

  - For each `packages/*/*/package.yml`, runs the decision tree and produces a verdict: `OK`, `MOVE→<tier>`, or `UNCLEAR (surface)`.
  - For each package's `dependencies.build` and `dependencies.host`, checks every dep is in same-or-earlier tier; reports any backward cross-tier edges.
  - Outputs a TSV: `name <TAB> current_tier <TAB> verdict <TAB> notes`.
  - Exit 0 if every package is `OK`; exit 1 otherwise.

This is a **reviewable** report — the maintainer signs off on every non-OK row before any move is applied. It is not an auto-fix.

---

## Resolved placements (formerly "open questions") — 2026-05-11 owner-direct

  1. **`polkit`** → `tier: core`. Session authorization is required to run a multi-user system. 2-pass at tail end of `chroot-build-core-extra.sh` if build-deps require it.
  2. **`NetworkManager`** → `tier: core`. Network connectivity is required to self-update. Co-locates with `systemd-networkd` (already `tier: core`) for a coherent network-stack story. 2-pass at tail end if needed.
  3. **`Node.js` / `npm`** → `tier: core`. Foundational language runtime. Built feature-complete; 2-pass at tail end for any naturally-later build-deps.
  4. **`Ruby`, `Rust`** → `tier: core`. Same reasoning as Node.js. Feature-complete; 2-pass as needed.
  5. **`cups` + `ghostscript`** → `tier: desktop`. LSB Print is a desktop-runtime capability; LSB conformance is about presence + ABI, not tier placement. Our tier reflects what the package IS — these ARE desktop-printing infrastructure.
  6. **`linux-kernel`** → `tier: core`. Special build pathway via `phase_kernel` (`scripts/chroot-build-ch10.sh`'s `build_ch10_package` helper). Kernel headers used during Ch 8 chroot setup; full kernel built in `phase_kernel`; modules loaded at desktop runtime. One source of truth (`packages/core/linux-kernel/`), multiple consumers across phases. The preflight already special-cases this.

---

## Reproducibility — first-class design concern

Debian's "Reproducible Builds" mandate (effective March 2026, post-Mythos publication) is the industry signal: every package must produce byte-identical output from the same source, so a third party can cryptographically verify the binary matches the claimed source. With Mythos-class adversaries capable of superhuman vulnerability discovery, reproducible builds are a **security primitive** — they let downstream verifiers detect silent backdoor insertion, supply-chain compromise, or build-environment tampering. This aligns directly with InterGenOS's HOLY GRAIL (security-only alignment).

**What reproducibility means for tier design.**

  - Tier classification must be **deterministic**: every package's tier is derivable from its `package.yml` plus the canonical definitions in this doc. No ad-hoc placements, no "in this case we'll make an exception" without a doc update. `scripts/validate-package-tiers.py` enforces this.
  - Build order must be **deterministic**: LFS Ch 8 order is fixed (per "LFS Chapter 8 ordering is sacrosanct"); tail-end additions are deterministic via the explicit `run_package` sequence in `chroot-build-core-extra.sh`; topo-sort inside the Python builder is stable.
  - Per-package reproducibility primitives will be applied package-by-package across Build #N: `SOURCE_DATE_EPOCH`, deterministic `tar` invocations, `--no-clamp-mtime`, stripped build-path embeddings, locale-independent compilation. The tier model does not block any of these.
  - Bootstrap variants (`-pass1`/`-bootstrap` → full) do not break reproducibility. Each pass is itself deterministic; the `migrate-pkm-supersedes.sh` register documents which pass supersedes which, so the final installed manifest is unambiguous.

**What reproducibility does NOT mean for tier design.**

  - It does not mean every package must hit byte-identical reproducibility today. We adopt the primitives package-by-package; the tier model just must not get in the way.
  - It does not mean we copy Debian's specific implementation choices wholesale. Per `feedback_inspiration_not_copy.md` — other distros inspire us, but we develop our own. We adopt the *principle* (deterministic, verifiable builds) without importing Debian's trust assumptions or tooling architecture.

This section gets revisited as we adopt reproducibility primitives in build scripts.

---

## Maintenance

This document is the source of truth. When the rulebook says "tier reflects what a package IS," it means the definitions above. Editing this file is a maintainer decision and requires updating the validator script accordingly.

Last updated: 2026-05-11 — initial canonical definition.
