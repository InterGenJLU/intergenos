# Silent Build Failure Detection — Design Note

**Motivation:** AppArmor 3.1.7 produced a silently incomplete build during Build #9. Its `Make.rules` ran `which awk` before `which` was available on `PATH`, so the `AWK` variable resolved to empty, and the library and parser never compiled. The InterGenOS-specific file-copy portions of `do_install()` still succeeded, so pkm recorded `apparmor-3.1.7` as installed — backed by a 188 KB archive containing only four profile files. The missing `libapparmor.so` did not surface until nine hours later, when `systemd-pass2`'s configure step failed on the dependency gap.

A build that exits zero but ships a hollow package is the worst class of failure: it passes every gate and corrupts every downstream consumer. This validator catches that shape before it propagates.

## Heuristics

The validator (`scripts/validate-pkm-archive.py`) applies two heuristics per archive:

### 1. Archive size sanity
For packages with a compiled build style (autotools, meson, cmake), archives smaller than a configurable threshold (default 200 KB) are flagged. The `apparmor-3.1.7` 188 KB archive would have been caught here.

### 2. Payload directory check
At least one of `usr/lib`, `usr/lib64`, `usr/bin`, or `usr/sbin` must contain at least one real file, not just empty directories. AppArmor's archive contained empty directories only; its profile files lived outside these paths.

### Custom build style exemption
Packages with `build_style: custom` are exempt from the size sanity check, since their `build.sh` script decides exactly what to install. The payload check still applies to them.

## How It Would Have Caught AppArmor

| Heuristic | apparmor-3.1.7 | Result |
|---|---|---|
| Size sanity (200 KB min) | 188 KB archive | **SUSPECT** — autotools package below 200 KB |
| Payload dirs | No files in `usr/lib`, `usr/bin`, etc. | **SUSPECT** — only config profiles present |

## Integration

The validator runs alongside existing tooling. It does not modify `pkm/repo.py`, `sign-release.sh`, `generate-repodb.py`, or `emit-package-archives.py`. It reads the archives and their companion manifests independently and produces TSV and JSON reports, and it exits non-zero when any archive is flagged so a build gate can act on the result.

It currently runs as a standalone check. Wiring it into the build's validation gate (`phase_validate`) is planned, so that a hollow archive halts the build at the point of detection rather than surfacing hours later in a downstream configure step.

**The failure class this validator was written for is now also covered at the seal point, by a different mechanism.** The builder carries a fail-closed archive-seal gate (`igos-build/tracker.py`) that runs where both archive flows converge — the DESTDIR-staging path and the filesystem-diff path alike — and, for a package whose declared `verify_paths` must be present, requires each declared path to exist **inside the just-sealed archive**, not merely somewhere on the chroot. That distinction is what makes it complementary to the pre-squashfs `verify_paths` audit: the chroot-level audit passes when a file is present on the chroot for any reason, including having been installed by a neighbouring package, so it structurally cannot see an archive whose tar is wrong. The seal gate can. In practice the two catch different halves of the same class, and neither replaces the size-and-payload heuristics described here, which need no per-package declaration to fire.

## Limitations

- The size threshold is a heuristic. A legitimately small autotools package, such as a tiny utility, can trigger a false positive. Tune the threshold through the config YAML.
- The validator does not yet compare archive contents against the companion manifest at `/var/lib/igos/packages/`. A planned enhancement will cross-reference each archive's file list against the manifest's `FILE LIST` entries.
- Corrupted archives (unreadable `tar.gz`) are flagged as errors rather than suspects.
