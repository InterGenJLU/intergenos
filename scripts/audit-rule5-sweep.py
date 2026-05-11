#!/usr/bin/env python3
"""Rule 5 sweep — for each package with bundled-lib-extract-unclear flagged
in its audit JSON, classify how the bundling is handled.

Categories (in priority order, most-benign first):
  INTERNAL-BUILD-SYSTEM    build system (cargo/go/cmake/meson) handles the
                           vendored libs natively; no build.sh intervention
                           required. Benign.
  MESON-SUBPROJECTS-WRAP   subprojects/ contains only .wrap fallbacks,
                           meson resolves via WrapDB. Benign.
  BENIGN-CONTRIB-LIKELY    contrib/ dir is typically docs/tests/examples
                           in autotools projects. Benign.
  HANDLED                  build.sh has explicit `tar xf` for vendor
                           tarballs (Rule 5 multi-source pattern).
  NEEDS-MANUAL-REVIEW      bundling not auto-classifiable; reviewer must
                           verify the build system handles it.

Writes /tmp/rule5-sweep-results.tsv + build/rule5-sweep-results.tsv."""
import json, subprocess, re
from pathlib import Path

REPO = Path("/mnt/intergenos")
AUDITS = REPO / "build" / "audits"
SOURCES = REPO / "build" / "sources"


def classify(name: str, audit: dict) -> tuple[str, str]:
    bundled = audit.get("bundled_libs") or []
    if not bundled:
        return ("NO-BUNDLED-LIBS", "")
    tarball = audit.get("source_tarball")
    if not tarball:
        return ("NEEDS-MANUAL-REVIEW", "no tarball on disk")
    tball_path = SOURCES / tarball
    if not tball_path.exists():
        return ("NEEDS-MANUAL-REVIEW", "tarball file missing")

    build_system = audit.get("build_system", "")
    pkg_dir = REPO / audit["package_dir"]
    build_sh = pkg_dir / "build.sh"
    bsh_text = build_sh.read_text() if build_sh.exists() else ""

    try:
        paths = subprocess.run(
            ["tar", "-tf", str(tball_path)],
            capture_output=True, text=True, timeout=30,
        ).stdout.splitlines()
    except Exception as e:
        return ("NEEDS-MANUAL-REVIEW", f"tar inspect error: {e}")

    has_explicit_extract = bool(re.search(
        r"\btar\s+x[a-z]*\s+[^\n]*\.(?:tar|tgz|zip)",
        bsh_text, re.IGNORECASE,
    ))
    # LibreOffice / large-vendoring pattern: --with-external-tar=...
    # + --disable-fetch-external + --with-system-X flags. The bundled
    # external/ dir is loaded from a separate pre-download location.
    has_external_tar = bool(re.search(
        r"--(?:with-external-tar|disable-fetch-external)\b",
        bsh_text,
    ))
    # Node-style: --shared-X flags tell configure to use system libs
    # instead of the bundled deps/ subtree.
    has_shared_flags = bool(re.search(
        r"--shared-(?:brotli|cares|libuv|openssl|nghttp2|zlib|icu|v8)",
        bsh_text,
    ))

    import yaml as Y
    has_multi_source = False
    try:
        d = Y.safe_load((pkg_dir / "package.yml").read_text())
        srcs = d.get("source") or []
        has_multi_source = isinstance(srcs, list) and len(srcs) > 1
    except Exception:
        pass

    # Cargo (Rust): packages with vendor/ + .cargo/config.toml or vendor/
    # itself imply native vendor mode. cargo build reads vendor/ automatically.
    if build_system == "cargo" or any(p.endswith("/Cargo.toml") for p in paths):
        if "vendor" in bundled or "third_party" in bundled:
            return ("INTERNAL-BUILD-SYSTEM",
                    "cargo native vendor mode — handled by cargo build")

    # Go: vendor/ directory is Go's standard vendoring; `go build` reads it
    # automatically when go.mod is present.
    if any(p.endswith("/go.mod") for p in paths) and "vendor" in bundled:
        return ("INTERNAL-BUILD-SYSTEM",
                "Go native vendor mode — handled by go build")

    # CMake: third_party/ / external/ / 3rdparty / DEPS / vendor dirs are
    # typically referenced via add_subdirectory() at configure time —
    # internally handled.
    CMAKE_VENDOR_NAMES = {"third_party", "third-party", "3rdparty", "external",
                          "External", "deps", "DEPS", "vendor"}
    if build_system == "cmake":
        cmake_root = next(
            (p for p in paths if p.endswith("/CMakeLists.txt")
             and p.count("/") <= 2), None)
        if cmake_root and set(bundled) & CMAKE_VENDOR_NAMES:
            return ("INTERNAL-BUILD-SYSTEM",
                    f"cmake project with {','.join(bundled)} — typically "
                    "add_subdirectory() handled at configure")

    # Autotools projects with third_party/ — usually test fixtures or small
    # header bundles (e.g., x86 asm headers). Mark as benign — the configure
    # script doesn't use them as substitutes for system libs.
    if build_system == "autotools" and set(bundled) & {"third_party", "third-party"}:
        return ("BENIGN-CONTRIB-LIKELY",
                "autotools project + third_party/ — typically test fixtures "
                "or bundled headers, not system-lib substitutes")

    # Meson with 3rdparty/ (non-subprojects convention) — same handling as
    # subprojects vendored: meson references them explicitly or they're
    # dormant.
    if build_system == "meson" and "3rdparty" in bundled:
        return ("INTERNAL-BUILD-SYSTEM",
                "meson project with 3rdparty/ — meson references via "
                "subproject()/explicit imports; dormant trees benign")

    # Python setup.py / pyproject.toml projects with external/ — typically
    # test fixtures or bundled C extensions handled by setuptools.
    if build_system == "python" and "external" in bundled:
        return ("BENIGN-CONTRIB-LIKELY",
                "python project with external/ — typically test fixtures "
                "or build-time C extensions")

    # Meson subprojects/ — wrap-only vs vendored split
    if "subprojects" in bundled:
        sub_paths = [p for p in paths if "/subprojects/" in p]
        wraps = [p for p in sub_paths if p.endswith(".wrap")]
        non_wrap_dirs = set()
        for p in sub_paths:
            m = re.search(r"/subprojects/([^/]+)/[^/]+", p)
            if m and not p.endswith(".wrap"):
                non_wrap_dirs.add(m.group(1))
        if wraps and not non_wrap_dirs:
            return ("MESON-SUBPROJECTS-WRAP",
                    f"{len(wraps)} .wrap fallbacks, no vendored trees — benign")
        if non_wrap_dirs:
            # Vendored subprojects: meson only builds subprojects that the
            # parent meson.build explicitly imports via subproject(). Dormant
            # vendored trees are benign.
            return ("INTERNAL-BUILD-SYSTEM",
                    f"meson vendored subprojects ({', '.join(sorted(non_wrap_dirs)[:3])}) "
                    "— meson uses explicit subproject() refs; dormant trees benign")

    if has_explicit_extract or has_multi_source:
        return ("HANDLED", "explicit tar xf in build.sh OR multi-source")
    if has_external_tar:
        return ("HANDLED",
                "--with-external-tar + --disable-fetch-external + "
                "--with-system-X pattern (LibreOffice-style)")
    if has_shared_flags:
        return ("HANDLED",
                "--shared-X flags route bundled deps/ to system libs "
                "(Node-style)")

    if bundled == ["contrib"]:
        return ("BENIGN-CONTRIB-LIKELY",
                "contrib/ dir alone, typically tests/examples/doc-only")

    # Fall-through: needs human review
    return ("NEEDS-MANUAL-REVIEW",
            f"unclassified: build_system={build_system}, bundled={bundled}")


def main():
    rows: list[tuple[str, str, str, str]] = []
    cats: dict[str, int] = {}
    for jp in sorted(AUDITS.glob("*.json")):
        audit = json.loads(jp.read_text())
        bundled = audit.get("bundled_libs") or []
        if not bundled:
            continue
        cat, note = classify(audit["name"], audit)
        cats[cat] = cats.get(cat, 0) + 1
        rows.append((audit["name"], cat, ",".join(bundled), note))

    out_tmp = Path("/tmp/rule5-sweep-results.tsv")
    out_repo = REPO / "build" / "rule5-sweep-results.tsv"
    for out_path in (out_tmp, out_repo):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w") as f:
            f.write("name\tcategory\tbundled\tnote\n")
            for r in sorted(rows):
                f.write("\t".join(r) + "\n")

    print(f"Wrote {out_repo} ({len(rows)} rows)")
    print()
    print("Breakdown:")
    for cat, n in sorted(cats.items(), key=lambda x: -x[1]):
        print(f"  {cat:30s} {n:4d}")


if __name__ == "__main__":
    main()
