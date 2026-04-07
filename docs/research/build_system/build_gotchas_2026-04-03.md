# Build Gotchas — Lessons Learned

Collected during Sessions 4-6. Review EVERY package build.sh against these
before running a new tier. These are the patterns that break builds.

---

## 1. DESTDIR directory creation

**Problem:** `make install` creates most directories in DESTDIR, but manual
install commands in `do_install()` assume directories exist.

**Symptoms:** `install: cannot create regular file '...': No such file or directory`
or `cp: cannot create directory '...': No such file or directory`

**Fix:** Add `install -v -d -m755 "${DESTDIR}/path"` before any manual
install/cp command that targets a DESTDIR path.

**Packages hit:** curl (doc dir), git (man dir), screen (/etc), exim (/var/mail)

**Prevention:** Before running a new tier, grep every build.sh for manual
install/cp/ln commands that target `${DESTDIR}` and verify the parent directory
is created first.

```bash
grep -n 'install.*DESTDIR\|cp.*DESTDIR\|ln.*DESTDIR\|mkdir.*DESTDIR' packages/<tier>/*/build.sh
```

---

## 2. Deploy: tar not cp (symlink directories)

**Problem:** `cp -a --remove-destination` cannot overwrite a symlink with a
directory. On systemd systems, `/var/run → /run`, `/var/lock → /run/lock`, etc.

**Symptoms:** `cp: cannot overwrite non-directory '/var/run' with directory`

**Fix:** Use tar-based deploy:
```bash
tar -C "${dest}" -cf - . | tar -C / -xf - --no-overwrite-dir
```

**Affected:** pkg-functions.sh (shell runner) AND builder.py (Python builder).
Both have been fixed. Check any new deploy code.

---

## 3. Missing build.sh for autotools packages

**Problem:** The Python builder (igos-build) handles autotools packages
automatically via the style handler. The shell runner (chroot-build-*.sh)
requires an explicit build.sh file.

**Symptoms:** `ERROR: No build.sh found at .../build.sh`

**Fix:** Every package needs a build.sh, even simple autotools ones.

**Prevention:** Before running a tier, check for missing build.sh:
```bash
for d in packages/<tier>/*/; do
    [ -f "$d/build.sh" ] || echo "MISSING: $d"
done
```

---

## 4. Missing source tarballs

**Problem:** Source tarballs aren't in the chroot's `/sources/` directory.
The setup phase copies from `build/sources/` on the host, but new tiers'
sources may not have been downloaded yet.

**Symptoms:** `tar: /sources/<pkg>.tar.xz: Cannot open: No such file or directory`

**Fix:** Run `download-sources.py --tier <tier> --update-checksums` BEFORE
building, then copy into chroot.

**Prevention:** Before running a new tier:
```bash
python3 scripts/download-sources.py --tier <tier> --dry-run
```

---

## 5. Missing patches

**Problem:** Packages declare patches in package.yml but the patch files
aren't downloaded and the build.sh doesn't apply them.

**Symptoms:** Build succeeds but the system has bugs (memory corruption, security issues)

**Fix:** 
1. Download patches to build/sources/
2. Apply in configure(): `patch -Np1 -i "${IGOS_SOURCES}/patch-name.patch"`

**Prevention:** Check for declared patches without application:
```bash
for f in packages/<tier>/*/package.yml; do
    patches=$(grep -A10 "patches:" "$f" | grep "^ *-" | sed 's/^ *- //')
    if [ -n "$patches" ]; then
        dir=$(dirname "$f")
        for p in $patches; do
            grep -q "patch.*$p" "$dir/build.sh" 2>/dev/null || echo "NOT APPLIED: $p in $dir"
        done
    fi
done
```

**Packages hit:** glib2 (memory corruption), nss (standalone build), rsync (security)

---

## 6. Circular dependencies (bootstrap pattern)

**Problem:** Package A needs Package B to build, but Package B needs Package A.

**Symptoms:** Build fails because a required tool/library doesn't exist yet.

**Fix:** Split into bootstrap chain (Void Linux approach):
- `pkg-bootstrap` (without the feature that causes the cycle)
- dependency package (builds against bootstrap)
- `pkg` (full, with feature enabled)

**Packages hit:** glib2 ↔ gobject-introspection, freetype2 ↔ harfbuzz

---

## 7. GCC triplet cleanup

**Problem:** LFS cleanup removes `x86_64-lfs-linux-gnu*` cross-compiler files.
InterGenOS uses `x86_64-igos-linux-gnu` for both cross-compiler AND final system.
Cleaning up igos triplet files deletes the live compiler (cc1, collect2, lto1).

**Symptoms:** `gcc: fatal error: cannot execute 'cc1': No such file or directory`

**Fix:** Don't clean up the igos triplet. The cross-compiler and final system
share the same triplet — there's nothing to clean up.

---

## 8. Meson configure vs setup (program caching)

**Problem:** `meson configure` does NOT re-scan PATH for programs. It uses
cached results from the original `meson setup`. Reconfiguring with a new
option that requires a program (e.g., g-ir-scanner) fails if the program
wasn't found during the original setup.

**Symptoms:** `Program 'xxx' not found` after `meson configure`

**Fix:** Do a fresh `meson setup` in a new build directory instead of
`meson configure` on the existing one.

---

## 9. Hardcoded versions

**Problem:** Version numbers hardcoded in build.sh comments, docdir paths,
Perl lib paths, manpage tarball names, etc.

**Fix:** Use `${PKG_VERSION}` variable (set by the shell runner before
calling build.sh). For Perl paths, use a version-independent path.

**Prevention:**
```bash
grep -n '[0-9]\.[0-9]' packages/<tier>/*/build.sh | grep -v '^.*:#'
```

---

## 10. IGOS_START_AT through chroot

**Problem:** `env -i` in chroot-enter.sh wipes all environment variables.
IGOS_START_AT must be explicitly passed through.

**Fix:** Added `IGOS_START_AT="${IGOS_START_AT:-}"` to chroot-enter.sh env list.

---

## 11. Stripping debug symbols

**Problem:** Stripping live libraries while the system is running can break
the dynamic linker chain. set -e makes failures fatal.

**Fix:** Skip stripping during development. Strip in create-image.sh when
packaging the final distributable image.

---

## Pre-Tier Checklist

Run these before building ANY new tier:

```bash
TIER=desktop  # change as needed

# 1. All packages have build.sh
for d in packages/$TIER/*/; do [ -f "$d/build.sh" ] || echo "MISSING: $d"; done

# 2. All sources downloaded
python3 scripts/download-sources.py --tier $TIER --dry-run

# 3. All patches declared are applied in build.sh
for f in packages/$TIER/*/package.yml; do
    patches=$(grep -A10 "patches:" "$f" | grep "^ *-" | sed 's/^ *- //')
    if [ -n "$patches" ]; then
        dir=$(dirname "$f")
        for p in $patches; do
            grep -q "patch.*$p" "$dir/build.sh" 2>/dev/null || echo "NOT APPLIED: $p in $dir"
        done
    fi
done

# 4. DESTDIR directories created for manual installs
grep -n 'install.*DESTDIR\|cp.*DESTDIR' packages/$TIER/*/build.sh | grep -v 'mkdir\|install -.*d'

# 5. No hardcoded versions in build.sh (excluding comments)
grep -n '[0-9]\.[0-9]' packages/$TIER/*/build.sh | grep -v '^.*:#' | grep -v PKG_VERSION
```
