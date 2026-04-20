# Code Review Request #8: PyYAML Bootstrap in Chroot Build Environment

**Project:** InterGenOS — Linux distribution built entirely from source
**Author:** InterGenJLU / InterGen Studios
**Date:** April 7, 2026
**Reviewer(s):** External LLM review requested
**Scope:** Python dependency bootstrap logic across 3 chroot build scripts
**Priority:** High — this code path has caused 3 separate build failures

---

## Context

I am requesting a thorough review of the PyYAML bootstrap logic used in the InterGenOS build pipeline. This specific code path has failed three times during development, each time for a different reason, and I would like an independent assessment of the current implementation's correctness and robustness.

InterGenOS is a Linux distribution built entirely from source following Linux From Scratch (LFS 13.0) and Beyond LFS (BLFS 13.0). The entire build runs inside an isolated chroot on an Ubuntu 24.04 KVM virtual machine. **The chroot has no internet access** — all source tarballs are pre-staged to `/sources`.

The build system uses a Python orchestrator (`igos-build`) that reads YAML package templates to determine build order, dependency resolution, and build flags. This orchestrator requires PyYAML to function. PyYAML is not part of the LFS core build — it must be bootstrapped into the chroot before any build phase that invokes the Python-based builder.

## The Problem

Python 3.14.3 is built in LFS Chapter 8 (the core system). setuptools 82.0.0 is also built in Chapter 8 via pip. After Chapter 8 completes, subsequent build phases (desktop, extra) need to use the Python-based `igos-build` tool, which requires PyYAML.

PyYAML is not available via a simple `import` after the core build — it must be installed from a local source tarball using pip, inside a chroot, with no network access. Python 3.14 removed `distutils` from the standard library (PEP 632), so setuptools must provide a compatibility shim.

## Failure History

This bootstrap has failed **three times** during development, each for a different reason:

### Failure 1: SIGPIPE death from `setup.py install | tail -5`

**What happened:** The original approach used `python3 setup.py install | tail -5` under `set -o pipefail`. When `tail` closed its end of the pipe before `setup.py` finished writing, `setup.py` received SIGPIPE and died. Under `pipefail`, this non-zero exit killed the entire build.

**Root cause:** Piping a long-running install command to `tail` is unsafe under `set -o pipefail`.

### Failure 2: `setup.py install` deprecated, fragile

**What happened:** Changed to `python3 setup.py install || true`, which worked but masked real errors. Also, `setup.py install` is deprecated in modern Python and doesn't integrate with pip's package tracking.

**Root cause:** `setup.py install` is the wrong tool for modern Python. The upstream-supported method is `ensurepip` + `pip install`.

### Failure 3: `Cannot import 'setuptools.build_meta'` in pip's isolated build environment

**What happened:** Switched to `python3 -m ensurepip --upgrade` followed by `pip3 install --no-index --find-links=/sources PyYAML`. pip created an **isolated build environment** for PyYAML and tried to install setuptools into that isolated env from the source tarball. Inside the isolated env, `setuptools.build_meta` could not be imported.

**Root cause:** pip's build isolation creates a temporary virtualenv and tries to install build dependencies into it. With `--no-index`, it can only find packages in `--find-links`, but the isolated environment's package installation failed silently. The fix was adding `--no-build-isolation` to use the already-installed system setuptools.

### Failure 3b: Double prefix — files installed to `/usr/usr/lib/python3.14/site-packages/`

**What happened:** After fixing build isolation, pip installed PyYAML successfully (reported "Successfully installed PyYAML-6.0.3") but `import yaml` failed with `ModuleNotFoundError`. Investigation revealed the files were installed to `/usr/usr/lib/python3.14/site-packages/yaml` — a doubled `/usr` prefix.

**Root cause:** pip's prefix detection inside the chroot doubled the `/usr` prefix. Python reports `sys.prefix = /usr`, and pip apparently prepended another `/usr` when computing the installation target. The fix was adding `--target="$SITE"` (where `SITE` is the output of `python3 -c "import site; print(site.getsitepackages()[0])"`) to force pip to install to the exact correct path.

## Current Implementation

The bootstrap logic is duplicated across three scripts that run inside the chroot. Each script checks for PyYAML at startup and installs it if missing.

### Python's pip configuration (installed in Chapter 8)

This is `/etc/pip.conf`, installed by the Python package build script during LFS Chapter 8. It may be relevant to the prefix doubling issue:

```ini
[install]
root = /usr
compile = no

[global]
root-user-action = ignore
disable-pip-version-check = true
break-system-packages = true

[freeze]
user = false
user-site = false
```

### setuptools installation (LFS Chapter 8)

setuptools is built and installed as an LFS Chapter 8 package. Note the use of `--root="$DESTDIR"`:

```bash
#!/bin/bash
# Setuptools 82.0.0
# LFS 13.0 Section 8.57
#
# DESTDIR exception: pip uses --root instead of DESTDIR.

configure() {
    : # No configure step
}

build() {
    pip3 wheel -w dist --no-cache-dir --no-build-isolation --no-deps $PWD
}

do_install() {
    pip3 install --no-index --no-user --root="$DESTDIR" --no-deps --find-links dist setuptools
}
```

### Script 1: `chroot-build-desktop.sh` (desktop tier — 337 packages)

```bash
#!/bin/bash
# InterGenOS Desktop Build — 337 packages for GNOME on Wayland
# Runs INSIDE the chroot after core, config, core-extra, and kernel complete.
#
# Handles all prerequisites automatically:
#   1. Installs PyYAML for the Python builder
#   2. Builds base-tier dependencies needed by desktop packages
#   3. Runs igos-build with --skip-built for safe restarts
#
# Usage:
#   sudo bash /mnt/intergenos/scripts/chroot-enter.sh \
#        /mnt/intergenos/scripts/chroot-build-desktop.sh

set +h
set -e
set -o pipefail
umask 022

IGOS_SOURCES=/sources
IGOS_LOGS=/mnt/intergenos/build/logs
IGOS_JOBS=$(nproc)

mkdir -p "$IGOS_LOGS"

DESKTOP_LOG="$IGOS_LOGS/desktop-build-$(date '+%Y%m%d-%H%M%S').log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$DESKTOP_LOG"
}

log ""
log "============================================"
log "  InterGenOS Desktop Build"
log "  337 packages for GNOME on Wayland"
log "  Start: $(date)"
log "  Cores: ${IGOS_JOBS}"
log "============================================"
log ""

# ============================================================================
# Step 1: Ensure PyYAML is available for igos-build
# ============================================================================

log "--- Checking Python dependencies for igos-build ---"

if python3 -c "import yaml" 2>/dev/null; then
    log "  PyYAML: already installed"
else
    log "  PyYAML: not found — installing..."

    # Python 3.14 includes ensurepip — the upstream-supported way to
    # bootstrap pip into a fresh installation. Once pip exists, it
    # installs setuptools and PyYAML properly from local tarballs.
    # No deprecated setup.py, no file copies, no swallowed errors.
    log "  Bootstrapping pip via ensurepip..."
    python3 -m ensurepip --upgrade
    log "  pip: $(pip3 --version)"

    # Determine correct site-packages path
    SITE=$(python3 -c "import site; print(site.getsitepackages()[0])")

    log "  Installing setuptools from local tarball..."
    pip3 install --no-index --find-links="${IGOS_SOURCES}" \
        --no-cache-dir --no-user --target="$SITE" setuptools

    # Ensure distutils compatibility shim is active (Python 3.12+ removed
    # distutils from stdlib; setuptools provides it via _distutils_hack)
    if [ ! -f "$SITE/distutils-precedence.pth" ]; then
        echo "import _distutils_hack; _distutils_hack.add_shim()" > "$SITE/distutils-precedence.pth"
        log "  distutils shim: activated"
    fi

    log "  Installing PyYAML from local tarball..."
    pip3 install --no-index --find-links="${IGOS_SOURCES}" \
        --no-cache-dir --no-user --no-build-isolation --target="$SITE" PyYAML

    if ! python3 -c "import yaml" 2>/dev/null; then
        log "ERROR: Failed to install PyYAML — igos-build cannot run"
        exit 1
    fi
fi

log "  Python: $(python3 --version 2>&1)"
log "  PyYAML: $(python3 -c 'import yaml; print(yaml.__version__)')"
```

### Script 2: `chroot-build-tier.sh` (unified tier builder)

```bash
#!/bin/bash
# ==========================================================================
# InterGenOS Unified Tier Builder
#
# Runs INSIDE the chroot. Bootstraps PyYAML into the temporary Python
# (from LFS Ch. 7), then invokes the Python builder for any tier.
#
# Replaces the per-tier bash build scripts (chroot-build-ch8.sh,
# chroot-build-core-extra.sh, chroot-build-base.sh, chroot-build-desktop.sh)
# with a single entry point. One builder, one set of templates.
#
# Usage:
#   bash /mnt/intergenos/scripts/chroot-build-tier.sh --tier core
#   bash /mnt/intergenos/scripts/chroot-build-tier.sh --tier base
#   bash /mnt/intergenos/scripts/chroot-build-tier.sh --tier desktop
#
# The Python builder handles dependency resolution, build ordering,
# DESTDIR staging, manifest tracking, and skip-built logic.
# ==========================================================================

set +h
set -e
umask 022

IGOS_SOURCES=/sources
IGOS_LOGS=/mnt/intergenos/build/logs
TIER=""

# --------------------------------------------------------------------------
# Parse arguments
# --------------------------------------------------------------------------

while [[ $# -gt 0 ]]; do
    case "$1" in
        --tier)
            TIER="$2"
            shift 2
            ;;
        *)
            echo "Unknown argument: $1"
            echo "Usage: $0 --tier <core|base|desktop>"
            exit 1
            ;;
    esac
done

if [ -z "$TIER" ]; then
    echo "ERROR: --tier argument is required"
    echo "Usage: $0 --tier <core|base|desktop>"
    exit 1
fi

mkdir -p "$IGOS_LOGS"

TIER_LOG="${IGOS_LOGS}/${TIER}-build-$(date '+%Y%m%d-%H%M%S').log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$TIER_LOG"
}

log ""
log "============================================"
log "  InterGenOS Tier Build: ${TIER}"
log "  Start: $(date)"
log "  Cores: $(nproc)"
log "============================================"
log ""

# ==========================================================================
# Step 1: Ensure PyYAML is available for the Python builder
# ==========================================================================

log "--- Checking Python dependencies for igos-build ---"

if python3 -c "import yaml" 2>/dev/null; then
    log "  PyYAML: already installed"
else
    log "  PyYAML: not found — installing..."

    # Bootstrap pip via ensurepip (Python 3.14 upstream method),
    # then install setuptools + PyYAML from local tarballs.
    if ! pip3 --version 2>/dev/null; then
        log "  Bootstrapping pip via ensurepip..."
        python3 -m ensurepip --upgrade
        log "  pip: $(pip3 --version)"
    fi

    # Determine correct site-packages path
    SITE=$(python3 -c "import site; print(site.getsitepackages()[0])")

    if ! python3 -c "import setuptools" 2>/dev/null; then
        log "  Installing setuptools from local tarball..."
        pip3 install --no-index --find-links="${IGOS_SOURCES}" \
            --no-cache-dir --no-user --target="$SITE" setuptools
    fi

    # Ensure distutils compatibility shim is active
    if [ ! -f "$SITE/distutils-precedence.pth" ]; then
        echo "import _distutils_hack; _distutils_hack.add_shim()" > "$SITE/distutils-precedence.pth"
        log "  distutils shim: activated"
    fi

    log "  Installing PyYAML from local tarball..."
    pip3 install --no-index --find-links="${IGOS_SOURCES}" \
        --no-cache-dir --no-user --no-build-isolation --target="$SITE" PyYAML

    if ! python3 -c "import yaml" 2>/dev/null; then
        log "ERROR: Failed to install PyYAML — igos-build cannot run"
        exit 1
    fi
fi

# Verify
if ! python3 -c "import yaml; print(f'PyYAML {yaml.__version__}')" 2>/dev/null; then
    log "ERROR: PyYAML import test failed"
    exit 1
fi

log "  Python: $(python3 --version 2>&1)"
log "  PyYAML: $(python3 -c 'import yaml; print(yaml.__version__)')"
```

### Script 3: `chroot-build-extra.sh` (extra tier — user applications)

```bash
#!/bin/bash
# InterGenOS Extra Tier Build — User-facing applications
# Runs INSIDE the chroot after desktop tier completes.
#
# Uses igos-build (Python builder) for dependency resolution and build
# ordering. Packages in this tier are optional — the desktop works without them.
#
# Usage:
#   sudo bash /mnt/intergenos/scripts/chroot-enter.sh \
#        /mnt/intergenos/scripts/chroot-build-extra.sh

set +h
set -e
umask 022

IGOS_SOURCES=/sources
IGOS_LOGS=/mnt/intergenos/build/logs
IGOS_JOBS=$(nproc)

mkdir -p "$IGOS_LOGS"

EXTRA_LOG="$IGOS_LOGS/extra-build-$(date '+%Y%m%d-%H%M%S').log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$EXTRA_LOG"
}

log ""
log "============================================"
log "  InterGenOS Extra Tier Build"
log "  User-facing applications"
log "  Start: $(date)"
log "  Cores: ${IGOS_JOBS}"
log "============================================"
log ""

# ============================================================================
# Step 1: Ensure PyYAML is available for igos-build
# ============================================================================

if ! python3 -c "import yaml" 2>/dev/null; then
    log "  PyYAML not found — installing via ensurepip + pip..."

    if ! pip3 --version 2>/dev/null; then
        python3 -m ensurepip --upgrade
        log "  pip: $(pip3 --version)"
    fi

    # Determine correct site-packages path
    SITE=$(python3 -c "import site; print(site.getsitepackages()[0])")

    if ! python3 -c "import setuptools" 2>/dev/null; then
        pip3 install --no-index --find-links="${IGOS_SOURCES}" \
            --no-cache-dir --no-user --target="$SITE" setuptools
    fi

    # Ensure distutils compatibility shim is active
    if [ ! -f "$SITE/distutils-precedence.pth" ]; then
        echo "import _distutils_hack; _distutils_hack.add_shim()" > "$SITE/distutils-precedence.pth"
    fi

    pip3 install --no-index --find-links="${IGOS_SOURCES}" \
        --no-cache-dir --no-user --no-build-isolation --target="$SITE" PyYAML

    if ! python3 -c "import yaml" 2>/dev/null; then
        log "ERROR: Failed to install PyYAML"
        exit 1
    fi
    log "  PyYAML: installed"
fi
```

## Environment Details

- **Python version:** 3.14.3 (LFS 13.0)
- **setuptools version:** 82.0.0
- **PyYAML version:** 6.0.3
- **pip version:** bundled with Python 3.14.3 via ensurepip
- **Build environment:** Isolated chroot, no internet access
- **Source tarballs available:** `setuptools-82.0.0.tar.gz`, `pyyaml-6.0.3.tar.gz` (pre-staged to `/sources`)
- **Shell:** bash 5.3, `set -e` active, `set -o pipefail` active in desktop script
- **`/etc/pip.conf`** is present with `root = /usr` (see above)

## Specific Questions

1. **Is the `--target="$SITE"` approach the correct fix for the double-prefix problem?** Or is the root cause actually the `root = /usr` setting in `/etc/pip.conf` interacting badly with pip's internal prefix computation inside a chroot? Would removing `root = /usr` from pip.conf and using `--root /` instead be more correct?

2. **Is `--no-build-isolation` safe here?** We use it because pip's isolated build environment can't install setuptools from a local tarball without network access. But does skipping build isolation introduce risks for PyYAML's C extension compilation (it uses libyaml if available)?

3. **Should the distutils compatibility shim (`distutils-precedence.pth`) still be necessary?** setuptools 82.0.0 was already installed in Chapter 8 — does it not automatically provide the distutils shim? Are we fighting a problem that the Ch. 8 setuptools install already solved?

4. **Code duplication:** The bootstrap logic is copy-pasted across three scripts with minor variations. Should this be extracted to a shared function (e.g., a `bootstrap-pyyaml.sh` that all three scripts source)? What's the best practice for sharing shell functions across scripts that run inside a chroot?

5. **Is there a simpler approach entirely?** For example:
   - Could PyYAML be built and installed as a proper LFS Chapter 8 package (alongside setuptools and wheel) so it's always available?
   - Could we use `pip3 install --no-index --find-links=/sources --no-build-isolation --no-deps PyYAML` (adding `--no-deps` since PyYAML has no Python dependencies)?
   - Could we bypass pip entirely and install PyYAML's pure-Python fallback manually (copy `yaml/` directory into site-packages)?

6. **Are there any other Python 3.14-specific pitfalls** we should be aware of for running pip inside a chroot? PEP 668 (externally-managed-environment), PEP 632 (distutils removal), and the `EXTERNALLY-MANAGED` marker file are all in play.

7. **Any recommended refactors** to make this bootstrap more robust against future Python version changes?

## What Success Looks Like

After the bootstrap completes, the following must be true inside the chroot:

```bash
python3 -c "import yaml; print(yaml.__version__)"   # → 6.0.3
python3 -c "import setuptools; print(setuptools.__version__)"  # → 82.0.0
python3 -c "import distutils"  # → no error (via setuptools shim)
```

And the Python-based build system must be able to:

```bash
cd /mnt/intergenos
python3 -m igos_build --tier desktop --skip-built  # → parses YAML templates, resolves deps, builds packages
```

Thank you for your time and expertise.
