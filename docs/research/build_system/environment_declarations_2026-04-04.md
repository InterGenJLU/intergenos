# InterGenOS Build Environment Declarations
## Authoritative Reference: LFS 13.0 → Our Implementation

Each build phase requires specific environment variables set at precisely the right time.
Wrong variables = wrong linking = broken system.

---

## Phase 1: Toolchain Host Environment (LFS Ch. 4.4)

**When:** Before Chapter 5-6, on the HOST system as the build user.

**LFS 13.0 specifies:**
```bash
set +h                              # Disable bash hash
umask 022                           # Standard permissions
LFS=/mnt/lfs                        # Build root
LC_ALL=POSIX                        # Prevent locale interference
LFS_TGT=$(uname -m)-lfs-linux-gnu   # Cross-compilation target triplet
PATH=/usr/bin                        # Start with clean PATH
if [ ! -L /bin ]; then PATH=/bin:$PATH; fi
PATH=$LFS/tools/bin:$PATH           # Cross-tools first in PATH
CONFIG_SITE=$LFS/usr/share/config.site  # Override autoconf cache
export LFS LC_ALL LFS_TGT PATH CONFIG_SITE
```

**Our implementation** (`scripts/toolchain-build.sh`):
```bash
set +h
umask 022
export IGOS=/mnt/igos                       # LFS → IGOS
export IGOS_TARGET=x86_64-igos-linux-gnu    # LFS_TGT → IGOS_TARGET (hardcoded, not $(uname -m))
export IGOS_SOURCES=/mnt/intergenos/build/sources
export IGOS_PATCHES=/mnt/intergenos/build/patches
export IGOS_LOGS=/mnt/intergenos/build/logs
export IGOS_JOBS=$(nproc)
export LC_ALL=POSIX
export PATH=/usr/bin
if [ ! -L /bin ]; then PATH=/bin:$PATH; fi
export PATH=$IGOS/tools/bin:$PATH
export CONFIG_SITE=$IGOS/usr/share/config.site
```

**Called via** (from `build-intergenos.sh`):
```bash
su - "$BUILD_USER" -c "env -i HOME=/home/${BUILD_USER} TERM=${TERM} bash ${SCRIPTS}/toolchain-build.sh"
```
The `env -i` wipes ALL host variables (LFS requirement).

**NOTES:**
- `IGOS_TARGET` is hardcoded as `x86_64-igos-linux-gnu`. LFS uses `$(uname -m)-lfs-linux-gnu`. Our target triplet uses `igos` instead of `lfs` — this is intentional (InterGenOS identity).
- `CONFIG_SITE` is critical — prevents autoconf from caching host system values.
- `LC_ALL=POSIX` prevents locale-dependent behavior during cross-compilation.

---

## Phase 2: Chroot Entry (LFS Ch. 7.4)

**When:** After toolchain, before Chapter 7 tools and all subsequent phases.

**LFS 13.0 specifies:**
```bash
chroot "$LFS" /usr/bin/env -i   \
    HOME=/root                  \
    TERM="$TERM"                \
    PS1='(lfs chroot) \u:\w\$ ' \
    PATH=/usr/bin:/usr/sbin     \
    MAKEFLAGS="-j$(nproc)"      \
    TESTSUITEFLAGS="-j$(nproc)" \
    /bin/bash --login
```

**Our implementation** (`scripts/chroot-enter.sh`):
```bash
chroot "$IGOS" /usr/bin/env -i           \
    HOME=/root                           \
    TERM="$TERM"                         \
    TZ="$HOST_TZ"                        \
    PS1='...(igos-chroot)...'            \
    PATH=/usr/bin:/usr/sbin              \
    MAKEFLAGS="-j${JOBS}"               \
    TESTSUITEFLAGS="-j${JOBS}"           \
    IGOS_JOBS="${JOBS}"                  \
    IGOS_SOURCES=/sources               \
    IGOS_PATCHES=/sources               \
    IGOS_LOGS=/var/log/igos-build       \
    PKG_VERSION=""                       \
    IGOS_START_AT="${IGOS_START_AT:-}"   \
    $CHROOT_CMD
```

**Additions beyond LFS:**
- `TZ` — timezone from host
- `IGOS_JOBS` — our parallelism variable
- `IGOS_SOURCES`, `IGOS_PATCHES`, `IGOS_LOGS` — builder paths
- `PKG_VERSION` — initialized empty, set per-package
- `IGOS_START_AT` — resume support

**CRITICAL:** `env -i` wipes ALL host environment. Only the listed variables survive. This is essential — no host CFLAGS, CXXFLAGS, LD_LIBRARY_PATH, etc. leaking into the chroot.

---

## Phase 3: Chapter 7 Chroot Tools (LFS Ch. 7.5-7.12)

**When:** Inside chroot, building temporary tools (gettext, bison, perl, python, texinfo, util-linux).

**Additional environment** (`scripts/chroot-build.sh`):
```bash
set +h
umask 022
IGOS_SOURCES=/sources
IGOS_PATCHES=/sources
IGOS_LOGS=/var/log/igos-build
IGOS_JOBS=$(nproc)
```

These override/supplement what chroot-enter.sh passed in. The `set +h` is critical here too.

---

## Phase 4: Chapter 8 Core Build (LFS Ch. 8)

**When:** Inside chroot, after Chapter 7 tools are built.

**Previously** (`scripts/archive/chroot-build-ch8.sh`):
```bash
set +h
umask 022
IGOS_SOURCES=/sources
IGOS_PATCHES=/sources
IGOS_LOGS=/var/log/igos-build
IGOS_JOBS=$(nproc)
IGOS_PACKAGES=/mnt/intergenos/packages/core
export IGOS_SOURCES IGOS_PATCHES IGOS_LOGS IGOS_JOBS
```

**Now** (`scripts/chroot-build-tier.sh` → Python builder):
The Python builder (`builder.py:build_env()`) sets:
```python
env = os.environ.copy()                              # Inherits from chroot-enter.sh
env["IGOS"] = str(self.system_root)                  # /
env["IGOS_TARGET"] = pkg.target_triple or self.target_triple
env["IGOS_JOBS"] = str(self.jobs)
env["IGOS_SOURCES"] = str(self.sources_dir)          # /sources
env["IGOS_SOURCES_DIR"] = str(self.sources_dir)      # alias
env["IGOS_PATCHES"] = str(self.patches_dir)
env["PKG_VERSION"] = str(pkg.version)
env["version"] = str(pkg.version)
env["MAKEFLAGS"] = f"-j{self.jobs}"
env["LC_ALL"] = "POSIX"
env["XML_CATALOG_FILES"] = "/etc/xml/catalog"
env["PKG_CONFIG_PATH"] = "/usr/lib/pkgconfig:/usr/lib64/pkgconfig:/usr/share/pkgconfig"
env["PATH"] = f"{self.system_root}/tools/bin:" + env.get("PATH", "")
```

**NOTE:** The Python builder adds `LC_ALL=POSIX` which is correct for builds. It also adds `PKG_CONFIG_PATH` which includes `/usr/lib64/pkgconfig` — this should probably be reviewed given the lib64 issue. After remediation, there should be nothing in `/usr/lib64/`.

**NOTE:** The builder prepends `{system_root}/tools/bin` to PATH. Inside the chroot, system_root is `/`, so this adds `/tools/bin` to PATH. This is correct for early Ch. 8 builds that still need temporary tools.

---

## Environment Flow Summary

```
Host (env -i wipes everything)
  ↓
Toolchain (Ch. 5-6): IGOS, IGOS_TARGET, LC_ALL, PATH, CONFIG_SITE
  ↓
chroot-enter.sh (env -i wipes everything again)
  ↓
Chroot (Ch. 7+): HOME, TERM, TZ, PATH, MAKEFLAGS, IGOS_*
  ↓
chroot-build-tier.sh → Python builder
  ↓
builder.py:build_env() per package: inherits chroot env + adds IGOS_*, PKG_VERSION, etc.
  ↓
build.sh functions: inherits builder env, uses $IGOS_SOURCES, $IGOS_PATCHES, $PKG_VERSION, etc.
```

Two `env -i` barriers ensure no host contamination:
1. `su - $BUILD_USER -c "env -i ..."` for toolchain
2. `chroot ... /usr/bin/env -i ...` for all chroot phases

---

## Variables Available to build.sh Scripts

| Variable | Set By | Available In | Value |
|----------|--------|-------------|-------|
| `IGOS` | builder.py | core/base/desktop | `/` (inside chroot) |
| `IGOS_TARGET` | builder.py | core/base/desktop | `x86_64-igos-linux-gnu` |
| `IGOS_JOBS` | chroot-enter.sh + builder.py | all chroot phases | `$(nproc)` |
| `IGOS_SOURCES` | chroot-enter.sh + builder.py | all chroot phases | `/sources` |
| `IGOS_SOURCES_DIR` | builder.py | core/base/desktop | `/sources` (alias) |
| `IGOS_PATCHES` | chroot-enter.sh + builder.py | all chroot phases | `/sources` or patches dir |
| `PKG_VERSION` | builder.py | core/base/desktop | package version string |
| `version` | builder.py | core/base/desktop | package version string (alias) |
| `MAKEFLAGS` | chroot-enter.sh + builder.py | all chroot phases | `-j$(nproc)` |
| `LC_ALL` | builder.py | core/base/desktop | `POSIX` |
| `PATH` | chroot-enter.sh + builder.py | all chroot phases | `/tools/bin:/usr/bin:/usr/sbin` |
| `DESTDIR` | builder.py | core/base/desktop (tracked) | `/tmp/igos-staging/<pkg>-<ver>` |
| `HOME` | chroot-enter.sh | all chroot phases | `/root` |
| `TERM` | chroot-enter.sh | all chroot phases | inherited from host |

## Potential Issues Identified

1. **PKG_CONFIG_PATH includes /usr/lib64/pkgconfig** — after remediation this path won't exist. Should be `/usr/lib/pkgconfig:/usr/share/pkgconfig` only.
2. **IGOS_PATCHES points to /sources** in chroot-enter.sh but the Python builder has its own patches_dir. Need to verify consistency.
3. **PATH includes /tools/bin** — correct for early Ch. 8 but should be removed after toolchain cleanup at end of Ch. 8. LFS does this cleanup. Need to verify our process does too.
