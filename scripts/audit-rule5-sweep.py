#!/usr/bin/env python3
"""Rule 5 sweep — for each package with bundled-lib-extract-unclear flagged
in its audit JSON, check whether the build.sh actually extracts the vendor
directory or whether it's benign (e.g., the dir is just a meson wrap fallback
that meson handles internally, or the dir contains no buildable code).

Classification:
  - HANDLED: build.sh has an explicit `tar xf` for a multi-source entry
  - MESON-SUBPROJECTS-WRAP: dir is `subprojects/` and contains only `.wrap`
    files (meson handles via fallback URLs — benign)
  - MESON-SUBPROJECTS-VENDORED: dir contains vendored source trees we need
    to verify build.sh handles
  - BENIGN-CONTRIB: contrib/ dir contains tests/examples/doc-only material
  - NEEDS-MANUAL-REVIEW: can't classify automatically

Writes /tmp/rule5-sweep-results.tsv for owner review."""
import json, subprocess, re
from pathlib import Path

REPO = Path("/mnt/intergenos")
AUDITS = REPO / "build" / "audits"
SOURCES = REPO / "build" / "sources"


def classify(name: str, audit: dict) -> tuple[str, str]:
    """Returns (category, note)."""
    bundled = audit.get("bundled_libs") or []
    if not bundled:
        return ("NO-BUNDLED-LIBS", "")
    tarball = audit.get("source_tarball")
    if not tarball:
        return ("NEEDS-MANUAL-REVIEW", "no tarball on disk")
    tball_path = SOURCES / tarball
    if not tball_path.exists():
        return ("NEEDS-MANUAL-REVIEW", "tarball file missing")

    # Find build.sh
    pkg_dir = REPO / audit["package_dir"]
    build_sh = pkg_dir / "build.sh"
    bsh_text = build_sh.read_text() if build_sh.exists() else ""

    # Check 1: explicit tar xf in build.sh
    has_explicit_extract = bool(re.search(r"\btar\s+x[a-z]*\s+[^\n]*\.(?:tar|tgz|zip)",
                                          bsh_text, re.IGNORECASE))

    # Check 2: multi-source declaration
    yml_path = pkg_dir / "package.yml"
    has_multi_source = False
    if yml_path.exists():
        import yaml as Y
        try:
            d = Y.safe_load(yml_path.read_text())
            srcs = d.get("source") or []
            has_multi_source = isinstance(srcs, list) and len(srcs) > 1
        except Exception:
            pass

    # Check 3: subprojects-wrap-only
    if "subprojects" in bundled:
        try:
            paths = subprocess.run(
                ["tar", "-tf", str(tball_path)],
                capture_output=True, text=True, timeout=30,
            ).stdout.splitlines()
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
                # Vendored source trees inside subprojects/
                return ("MESON-SUBPROJECTS-VENDORED",
                        f"subprojects/ contains vendored trees: {sorted(non_wrap_dirs)[:3]}")
        except Exception as e:
            return ("NEEDS-MANUAL-REVIEW", f"tar inspect error: {e}")

    # Check 4: explicit extract present
    if has_explicit_extract or has_multi_source:
        return ("HANDLED", "explicit tar xf in build.sh OR multi-source")

    # Check 5: pure-contrib/test dir (typical for autotools projects)
    if bundled == ["contrib"] and not has_multi_source:
        return ("BENIGN-CONTRIB-LIKELY",
                "contrib/ dir alone, usually tests/examples/doc-only")
    if bundled == ["third_party"] or bundled == ["third-party"]:
        # Heavy bundling — needs careful look
        return ("NEEDS-MANUAL-REVIEW",
                f"third_party/ bundling without explicit extract or multi-source")
    if bundled == ["external"]:
        return ("NEEDS-MANUAL-REVIEW",
                "external/ bundling without explicit extract or multi-source")
    if bundled == ["vendor"]:
        return ("NEEDS-MANUAL-REVIEW",
                "vendor/ bundling — Go/Rust style; verify build.sh handles")

    return ("NEEDS-MANUAL-REVIEW", f"multi-bundled: {bundled}")


def main():
    out_path = Path("/tmp/rule5-sweep-results.tsv")
    cats: dict[str, int] = {}
    rows: list[tuple[str, str, str, str]] = []
    for jp in sorted(AUDITS.glob("*.json")):
        audit = json.loads(jp.read_text())
        bundled = audit.get("bundled_libs") or []
        if not bundled:
            continue
        cat, note = classify(audit["name"], audit)
        cats[cat] = cats.get(cat, 0) + 1
        rows.append((audit["name"], cat, ",".join(bundled), note))

    with out_path.open("w") as f:
        f.write("name\tcategory\tbundled\tnote\n")
        for r in sorted(rows):
            f.write("\t".join(r) + "\n")

    print(f"Wrote {out_path} ({len(rows)} rows)")
    print()
    print("Breakdown:")
    for cat, n in sorted(cats.items(), key=lambda x: -x[1]):
        print(f"  {cat:30s} {n:4d}")


if __name__ == "__main__":
    main()
