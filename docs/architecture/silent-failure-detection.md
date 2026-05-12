# Silent Build Failure Detection — Design Note

**Motivation:** Apparmor 3.1.7 built silently in Build #9 at ~06:04Z. Its Make.rules shelled out to which awk before which was in PATH; the AWK variable was empty, the library/parser never compiled, but the InterGenOS-specific file-copy portions of do_install() succeeded. pkm tracked apparmor-3.1.7 as installed with a 188KB archive containing only four profile files. The missing libapparmor.so surfaced 9 hours later when systemd-pass2's configure failed on the dependency gap.

## Heuristics

The validator (`scripts/validate-pkm-archive.py`) applies two heuristics per archive:

### 1. Archive size sanity
For packages with a compiled build style (autotools, meson, cmake), archives smaller than a configurable threshold (default 200KB) are flagged. The apparmor-3.1.7 188KB archive would have been caught here.

### 2. Payload directory check  
At least one of `usr/lib`, `usr/lib64`, `usr/bin`, or `usr/sbin` must contain at least one real file (not just empty directories). apparmor's archive had empty directories only — the profile files were not under these paths.

### Custom build style exemption
Packages with `build_style: custom` are exempt from the size sanity check (the build.sh script decides what to install). The payload check still applies.

## How It Would Have Caught Apparmor

| Heuristic | Apparmor-3.1.7 | Result |
|---|---|---|
| Size sanity (200KB min) | 188KB archive | **SUSPECT** — autotools package below 200KB |
| Payload dirs | No files in usr/lib, usr/bin, etc. | **SUSPECT** — only config profiles present |

## Integration

The validator runs alongside existing tools — it does not modify pkm/repo.py, sign-release.sh, generate-repodb.py, or emit-package-archives.py. It reads the archives + manifests independently and produces TSV + JSON reports. CI integration (phase_validate gate) is deferred to post-Build #9 when build-intergenos.sh can be safely edited.

## Limitations

- The size threshold is heuristic — a legitimately small autotools package (e.g., a tiny utility) could false-positive. Tune via config YAML.
- The validator does not compare archive contents against the companion manifest at /var/lib/igos/packages/ — a future enhancement could cross-reference archive file lists against manifest FILE LIST entries.
- Corrupted archives (unreadable tar.gz) are flagged as errors, not suspects.
