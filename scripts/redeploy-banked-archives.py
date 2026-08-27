#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""redeploy-banked-archives.py — find and heal archive-banked-but-undeployed
packages on a lineage build substrate.

A reverted lineage snapshot can carry a package's current sealed archive in
/var/lib/igos/archives while the package itself is entirely absent from the
chroot filesystem — no text manifest, no database row, no files. The archive
looks healthy from every currency check, the git delta cannot see it, and the
first loud symptom is a mid-tier dependency failure hours into a targeted
build (the class where a whole set of core-tier lib32 packages sat banked but
undeployed and the desktop tier halted on the first consumer). The manual
recovery is proven and documented; this tool turns the sweep + recovery into
one commanded pass.

Classification, per shipped package identity (identity is read from each
archive's own ./.PKGINFO — never the filename):

  DEPLOYED-CURRENT        installed state matches the newest banked archive,
                          and the recipe tree does not run ahead of it. OK.
  BANKED-NOT-DEPLOYED     newest banked archive has NO installed manifest/DB
                          row — the healing target. If the archive is CURRENT
                          against the recipe tree it joins the REDEPLOY set
                          (healed under --apply via the explicit-archive
                          install path + pkm verify); if the tree is ahead it
                          joins the REBUILD set (reported, never rebuilt here).
  DEPLOYED-STALE          installed, but the recipe tree is ahead of the
                          newest banked archive — REBUILD set (report only;
                          the currency gate owns refusal semantics).
  SUPERSEDED-TWIN         an older-version archive beside a newer sibling —
                          reported so the pre-pipeline stale-archive sweep
                          can remove it; never acted on here.
  NO-RECIPE               archive with no recipe resolving to its ship name.
                          Toolchain intermediates (-pass*/-tmp) are named and
                          skipped by design; anything else is a loud finding.

Modes: --report (default, read-only) exits 0 when nothing needs healing or
rebuilding, 2 when findings exist. --apply performs the REDEPLOY set only:
  chroot <root> pkm install <name> --archive <path> --archive-trust loose
  chroot <root> pkm verify <name>
and exits nonzero if any redeploy or verify fails. Run it as root on the
build host (the chroot and its archives are root-owned).
"""

import argparse
import importlib.util
import json
import sqlite3
import subprocess
import sys
import tarfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# pkm's own version comparator — the same ordering pkm upgrade uses.
_spec = importlib.util.spec_from_file_location("pkm_version", REPO_ROOT / "pkm" / "version.py")
_pkm_version = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_pkm_version)
vcompare = _pkm_version.compare

TOOLCHAIN_TWIN_SUFFIXES = ("-pass1", "-pass2", "-tmp")


def log(msg: str) -> None:
    print(f"[redeploy-banked-archives] {msg}")


def read_pkginfo(archive: Path) -> "dict | None":
    """Read ./.PKGINFO from a sealed archive; None when absent/unreadable."""
    try:
        with tarfile.open(archive, "r:gz") as tf:
            for member in ("./.PKGINFO", ".PKGINFO"):
                try:
                    fh = tf.extractfile(member)
                except KeyError:
                    continue
                if fh is None:
                    continue
                info = {}
                for raw in fh.read().decode("utf-8", "replace").splitlines():
                    line = raw.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, _, v = line.partition("=")
                    info[k.strip()] = v.strip()
                return info
    except (tarfile.TarError, OSError):
        return None
    return None


def load_recipes(packages_dir: Path) -> "dict[str, dict]":
    """Map SHIP name -> {version, release, recipe_name, path}. A ships_as
    declaration wins over the recipe's own name for the ship identity."""
    recipes = {}
    for yml in sorted(packages_dir.glob("*/*/package.yml")):
        name = version = ships_as = None
        release = 1
        for raw in yml.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.split("#", 1)[0].rstrip()
            if line.startswith("name:") and name is None:
                name = line.split(":", 1)[1].strip().strip('"\'')
            elif line.startswith("version:") and version is None:
                version = line.split(":", 1)[1].strip().strip('"\'')
            elif line.startswith("release:"):
                val = line.split(":", 1)[1].strip().strip('"\'')
                if val.isdigit():
                    release = int(val)
            elif line.startswith("ships_as:"):
                ships_as = line.split(":", 1)[1].strip().strip('"\'')
        if not name or not version:
            continue
        ship = ships_as or name
        entry = {"version": version, "release": release,
                 "recipe_name": name, "path": str(yml)}
        # A ships_as twin and a same-named real recipe can both map to one
        # ship name; prefer the recipe whose own name IS the ship name.
        if ship not in recipes or name == ship:
            recipes[ship] = entry
    return recipes


# The database pkm itself writes (pkm/database.py DB_PATH). The tool read
# var/lib/pkm/pkm.db until 2026-08-27 — a path nothing writes — so the DB leg
# of the deployment check was silently dead on every substrate and only the
# manifest glob decided.
PKM_DB_REL = "var/lib/igos/pkm.db"


def installed_state(chroot: Path, name: str,
                    version: "str | None" = None) -> "dict | None":
    """Installed (version, release) for a ship name from the chroot pkm DB,
    cross-checked against the text-manifest dir. None = not deployed.

    `version` is the banked archive's version: the manifest for exactly
    `<name>-<version>` counts as deployed whatever the version looks like.
    The digit-led glob is kept for other releases of the same name; it must
    stay digit-led so a sibling's manifest (llama-cpp-hip-b8796) never
    satisfies llama-cpp. Versions that begin with a letter (llama-cpp's
    upstream build numbers, "b8796") were invisible to the glob alone and
    reported BANKED-NOT-DEPLOYED with everything present (R001.2 pre-flight,
    2026-08-27)."""
    db_path = chroot / PKM_DB_REL
    row = None
    if db_path.is_file():
        try:
            with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as db:
                cur = db.execute(
                    "SELECT version, release FROM installed "
                    "WHERE name = ? AND superseded_by IS NULL", (name,))
                row = cur.fetchone()
        except sqlite3.Error:
            row = None
    manifest_dir = chroot / "var/lib/igos/packages"
    manifests: "list[Path]" = []
    if manifest_dir.is_dir():
        if version is not None and (manifest_dir / f"{name}-{version}").exists():
            manifests.append(manifest_dir / f"{name}-{version}")
        manifests += [p for p in manifest_dir.glob(f"{name}-[0-9]*")
                      if p not in manifests]
    if row is None and not manifests:
        return None
    return {"version": row[0] if row else None,
            "release": int(row[1]) if row and row[1] is not None else None,
            "db_row": row is not None,
            "text_manifest": bool(manifests)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--chroot", default="/mnt/igos",
                    help="Chroot root to examine (default /mnt/igos)")
    ap.add_argument("--packages-dir", default="/mnt/intergenos/packages",
                    help="Recipe tree the currency check reads (default /mnt/intergenos/packages)")
    ap.add_argument("--apply", action="store_true",
                    help="Perform the REDEPLOY set (default: report only)")
    ap.add_argument("--json", dest="json_out", default=None,
                    help="Also write the full classification as JSON to this path")
    args = ap.parse_args()

    chroot = Path(args.chroot)
    archives_dir = chroot / "var/lib/igos/archives"
    packages_dir = Path(args.packages_dir)
    if not archives_dir.is_dir():
        fail_msg = f"archives dir absent: {archives_dir} — wrong --chroot?"
        log(f"FAIL: {fail_msg}")
        return 1
    if not packages_dir.is_dir():
        log(f"FAIL: packages dir absent: {packages_dir}")
        return 1

    recipes = load_recipes(packages_dir)
    log(f"recipe tree: {len(recipes)} ship identities from {packages_dir}")

    # Newest banked archive per ship name, identity from .PKGINFO.
    newest: "dict[str, dict]" = {}
    superseded: "list[str]" = []
    unreadable: "list[str]" = []
    all_archives = sorted(archives_dir.glob("*.igos.tar.gz"))
    for arc in all_archives:
        info = read_pkginfo(arc)
        if not info or "pkgname" not in info or "pkgver" not in info:
            unreadable.append(arc.name)
            continue
        name = info["pkgname"]
        ver, rel = info["pkgver"], int(info.get("pkgrel", "1") or 1)
        cur = newest.get(name)
        if cur is None:
            newest[name] = {"archive": arc, "version": ver, "release": rel}
        else:
            c = vcompare((cur['version'], cur['release']), (ver, rel))
            if c < 0:
                superseded.append(cur["archive"].name)
                newest[name] = {"archive": arc, "version": ver, "release": rel}
            else:
                superseded.append(arc.name)

    redeploy, rebuild, twins_skipped, no_recipe = [], [], [], []
    deployed_current = 0
    for name, arc in sorted(newest.items()):
        recipe = recipes.get(name)
        if recipe is None:
            if name.endswith(TOOLCHAIN_TWIN_SUFFIXES):
                twins_skipped.append(name)
            else:
                no_recipe.append(name)
            continue
        tree_vr = f"{recipe['version']}-{recipe['release']}"
        arc_vr = f"{arc['version']}-{arc['release']}"
        tree_ahead = vcompare((arc["version"], arc["release"]),
                              (recipe["version"], recipe["release"])) < 0
        state = installed_state(chroot, name, arc["version"])
        if state is None:
            entry = {"name": name, "archive": arc["archive"].name,
                     "archive_vr": arc_vr, "tree_vr": tree_vr}
            (rebuild if tree_ahead else redeploy).append(entry)
        elif tree_ahead:
            rebuild.append({"name": name, "archive": arc["archive"].name,
                            "archive_vr": arc_vr, "tree_vr": tree_vr,
                            "deployed": True})
        else:
            deployed_current += 1

    log(f"archives examined: {len(all_archives)} · ship identities banked: {len(newest)} "
        f"· deployed-current: {deployed_current}")
    if superseded:
        log(f"SUPERSEDED-TWIN archives (pre-pipeline sweep removes them, not this tool): "
            f"{len(superseded)}: {' '.join(sorted(superseded))}")
    if twins_skipped:
        log(f"toolchain intermediates skipped by design: {len(twins_skipped)}: "
            f"{' '.join(twins_skipped)}")
    for name in no_recipe:
        log(f"FINDING NO-RECIPE: banked archive '{name}' resolves to no recipe ship name")
    for e in redeploy:
        log(f"FINDING BANKED-NOT-DEPLOYED (current, healable): {e['name']} "
            f"{e['archive_vr']} — archive {e['archive']}")
    for e in rebuild:
        kind = "DEPLOYED-STALE" if e.get("deployed") else "BANKED-NOT-DEPLOYED (stale)"
        log(f"FINDING {kind}: {e['name']} archive {e['archive_vr']} vs tree {e['tree_vr']} "
            f"— joins the REBUILD set")
    if unreadable:
        for n in unreadable:
            log(f"FINDING UNREADABLE-PKGINFO: {n}")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps({
            "redeploy": redeploy, "rebuild": rebuild, "no_recipe": no_recipe,
            "superseded": sorted(superseded), "unreadable": unreadable,
            "deployed_current": deployed_current}, indent=2))

    if not args.apply:
        findings = bool(redeploy or rebuild or no_recipe or unreadable)
        log(f"report mode: {'FINDINGS PRESENT (exit 2)' if findings else 'clean (exit 0)'}")
        return 2 if findings else 0

    # --apply: heal the REDEPLOY set via the proven explicit-archive path.
    failures = 0
    for e in redeploy:
        rel_archive = f"/var/lib/igos/archives/{e['archive']}"
        for desc, cmd in (
            ("install", ["chroot", str(chroot), "pkm", "install", e["name"],
                         "--archive", rel_archive, "--archive-trust", "loose"]),
            ("verify", ["chroot", str(chroot), "pkm", "verify", e["name"]]),
        ):
            log(f"APPLY {desc}: {' '.join(cmd)}")
            proc = subprocess.run(cmd, capture_output=True, text=True)
            sys.stdout.write(proc.stdout)
            sys.stderr.write(proc.stderr)
            if proc.returncode != 0:
                log(f"FAIL: {e['name']} {desc} rc={proc.returncode}")
                failures += 1
                break
    healed = len(redeploy) - failures
    log(f"apply complete: {healed}/{len(redeploy)} healed, {failures} failed"
        + (f"; REBUILD set ({len(rebuild)}) untouched — those need rebuilds, not redeploys"
           if rebuild else ""))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
