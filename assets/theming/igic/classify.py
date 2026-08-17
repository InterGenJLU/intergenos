#!/usr/bin/env python3
"""Default-by-class classifier — coverage as a COMPUTED property of the catalog.

Two questions a new package must answer before it earns (or is denied) a mark:

  1. Is it ALREADY covered?
     Keyed on the WIRED .desktop-id stems the theme actually emits — every unit's
     out_stem() out of gather_units() — NEVER on a normalized package name. The
     package-name path silently mis-scored file-roller / seahorse as uncovered: their
     launchers wire org.gnome.FileRoller / org.gnome.seahorse.Application, and no
     name-normalization bridges the bare package name to that reverse-DNS .desktop id.
     Coverage is checked stem-to-stem so that class of miscount cannot recur; the
     regression battery below pins it (file-roller / seahorse / sysprof).

  2. If uncovered, what TREATMENT does its CLASS call for?
     A structural class (name-pattern + tier + install-path flags) maps through
     CLASS_TREATMENT to one register: flat-first-party | gradient | flat-category | none.
     The default is the FLOOR — a branded third-party app is lifted from flat-category to
     gradient only when a recognizable own icon is sourced (the two-register rule); a
     background-only daemon with no launcher stays none. Coverage stays measured-demand:
     an uncovered wanted package is a finding for a follow-on wave, never a build failure,
     so this module is a triage/coverage oracle and does NOT gate the compile path.

Deterministic: pure functions over the catalog + a package record; no wall-clock, no RNG.
Self-contained: the coverage index is derived live from gather_units(), so the regression
battery runs with no external package tree. `python3 classify.py` prints the class->treatment
table + a coverage self-check and exits non-zero if any regression assertion fails.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import igic_core as ic


# ---------------------------------------------------------------------------
# 1. Coverage — computed from the catalog's WIRED .desktop-id stems
# ---------------------------------------------------------------------------
def wired_stem_index() -> set[str]:
    """The coverage oracle: every .desktop-id stem the theme emits, derived live from
    gather_units(). A single/family-expanded unit contributes out_stem(recipe); a state
    unit contributes its <id>-symbolic stem. This is what GTK resolves an Icon= key
    against, so membership here IS coverage — no name-normalization, no guessing."""
    palette = ic.load_palette()
    units, _family_failures = ic.gather_units(palette)
    wired: set[str] = set()
    for u in units:
        if u["kind"] == "state":
            wired.add(f"{u['state_id']}-symbolic")
        else:
            wired.add(ic.out_stem(u["recipe"]))
    return wired


def is_covered(desktop_id: str, wired: set[str]) -> bool:
    """Stem-to-stem: is this exact .desktop Icon= key already an emitted theme file?
    The whole point of the fix — the argument is the WIRED id (org.gnome.FileRoller),
    never a normalized package name (file-roller)."""
    return desktop_id in wired


def coverage_status(pkg: dict, wired: set[str]) -> str:
    """'covered' | 'uncovered'. Keyed on the package's WIRED .desktop id when known
    (pkg['desktop_id']), falling back to the bare name only when a package ships no
    launcher — in which case a name miss is truthful, not a normalization artifact."""
    key = pkg.get("desktop_id") or pkg["name"]
    return "covered" if is_covered(key, wired) else "uncovered"


# The flawed path, kept ONLY so the regression battery can prove it fails where stem-keying
# succeeds. Normalizes a package name and looks it up in a normalized copy of the wired set —
# exactly the bridge that dropped file-roller/seahorse. Never call this for real coverage.
def _norm(s: str) -> str:
    s = s.lower()
    s = re.sub(r"^org\.[a-z0-9]+\.", "", s)                       # org.gnome.Calculator -> calculator
    s = re.sub(r"^(gnome|gtk|xfce4?)-", "", s)                    # gnome-calculator -> calculator
    s = re.sub(r"-(browser|client|setup|gtk|gui|bin|desktop)$", "", s)
    return s


def _name_normalized_covered(name: str, wired: set[str]) -> bool:
    normalized_wired = {_norm(w) for w in wired}
    return _norm(name) in normalized_wired


# ---------------------------------------------------------------------------
# 2. Structural class — name-pattern + tier + install-path flags (the IC-035 taxonomy)
# ---------------------------------------------------------------------------
# Ported verbatim in intent from the ruled whole-catalog classifier: eight mutually
# exclusive structural classes, first match wins in this order.
_FIRST = re.compile(r"^(intergen|forge|pkm|igos|no-overview|welcome)", re.I)
_META = re.compile(r"(-pass[0-9]$|-bootstrap$|^lib32-|-static$|-headers$|-stage[0-9]$)", re.I)
_LANGMOD = re.compile(r"^(perl|python[0-9]?|py3?|ruby|lua|node|php|tcl)-|-perl$|^rubygem-", re.I)
_FONT = re.compile(r"(^font|-font|-fonts$|^ttf-|^otf-|^noto|-icon-theme$|firmware)", re.I)
_LIB = re.compile(r"^lib|(-devel$|-libs$)", re.I)
_DAEMON = re.compile(
    r"(-d$|daemon|-server$|^cronie$|^chrony|^avahi|^bluez|^wireplumber|^polkit|"
    r"^udisks|^upower|^rtkit|^accountsservice)", re.I)

CLASSES = ("first-party", "meta-infra", "language-module", "font-data-firmware",
           "library", "daemon-service", "app-or-cli", "other-support")


def structural_class(pkg: dict) -> str:
    """One of CLASSES. `pkg`: {name, tier?, desc?, bin?, lib?, desktop?}. First-party is
    authorship (our tiers/prefixes); the rest are surface/shape from the name and the
    install-path flags a package manager already records."""
    name = pkg["name"]
    tier = pkg.get("tier", "")
    desc = (pkg.get("desc") or "").lower()
    if tier == "ai" or _FIRST.match(name):
        return "first-party"
    if _META.search(name):
        return "meta-infra"
    if _LANGMOD.match(name):
        return "language-module"
    if _FONT.search(name):
        return "font-data-firmware"
    if _LIB.match(name) or (pkg.get("lib") and not pkg.get("bin") and not pkg.get("desktop")):
        return "library"
    if _DAEMON.search(name) or (
            not pkg.get("bin") and ("daemon" in desc or " server" in desc or "service" in desc)):
        return "daemon-service"
    if pkg.get("bin") or pkg.get("desktop"):
        return "app-or-cli"
    return "other-support"


# ---------------------------------------------------------------------------
# 3. Class -> treatment (the two-register rule + the IC-035 taxonomy, for the record)
# ---------------------------------------------------------------------------
# Registers a mark can live in:
#   flat-first-party  InterGenOS's OWN marks, flat line-art (intergen-apps / the OS mark)
#   gradient          a third-party app's OWN recognizable icon, luminance-keyed gradient blue
#   flat-category     user-facing surface, no brand to recolor -> flat line-art function glyph
#                     (covers BOTH the system-surface marks, a distinct glyph each, and the
#                      generic tools, a shared category glyph by function — one register, the
#                      glyph selection differs)
#   none              no user-facing launcher -> below the render bar, no mark
CLASS_TREATMENT = {
    "first-party":        "flat-first-party",
    "app-or-cli":         "gradient|flat-category",   # gradient if an own icon exists, else flat-category
    "daemon-service":     "flat-category|none",       # flat-category if it presents a launcher, else none
    "library":            "none",
    "meta-infra":         "none",
    "language-module":    "none",
    "font-data-firmware": "none",
    "other-support":      "none",
}


def default_treatment(pkg: dict) -> str:
    """The concrete register for one package. Resolves the two conditional classes:
    app-or-cli lifts to `gradient` when a recognizable own icon is available
    (pkg['brand_mark'] truthy — an authoring signal, set once a source is sourced+gate-2
    scanned), otherwise sits at its `flat-category` floor; a daemon-service earns
    `flat-category` only when it ships a launcher (pkg['desktop']), else `none`."""
    cls = structural_class(pkg)
    if cls == "app-or-cli":
        return "gradient" if pkg.get("brand_mark") else "flat-category"
    if cls == "daemon-service":
        return "flat-category" if pkg.get("desktop") else "none"
    return CLASS_TREATMENT[cls].split("|")[0]


# ---------------------------------------------------------------------------
# Regression battery — the file-roller/seahorse miscount cannot recur
# ---------------------------------------------------------------------------
# `divergent` marks the packages whose WIRED .desktop id genuinely diverges from the bare
# package name — the exact shape that mis-scored under name-normalization: file-roller ->
# org.gnome.FileRoller, seahorse -> org.gnome.seahorse.Application. For these the flawed path
# MUST still miss (that miss is what stem-keying repairs). sysprof is NOT divergent — its name
# normalizes cleanly back to its stem (org.gnome.Sysprof -> sysprof), so it only demonstrates
# coverage, never the miss. Every entry is covered when checked stem-to-stem.
_REGRESSION = [
    {"name": "file-roller", "desktop_id": "org.gnome.FileRoller",           "divergent": True},
    {"name": "seahorse",    "desktop_id": "org.gnome.seahorse.Application", "divergent": True},
    {"name": "sysprof",     "desktop_id": "org.gnome.Sysprof",             "divergent": False},
]


def selftest() -> list[str]:
    """Return a list of failures (empty == pass). Pins the coverage fix and the table."""
    fails: list[str] = []
    wired = wired_stem_index()

    for pkg in _REGRESSION:
        did, name = pkg["desktop_id"], pkg["name"]
        # (a) stem-keyed coverage HITS — the wired .desktop id is an emitted theme file
        if not is_covered(did, wired):
            fails.append(f"coverage: wired stem {did!r} ({name}) not found in the catalog — "
                         f"regression entry must be covered")
        # (b) for a genuinely divergent name the name-normalized path MUST still MISS —
        #     that miss is precisely what stem-keying repairs (the load-bearing property)
        if pkg["divergent"] and _name_normalized_covered(name, wired):
            fails.append(f"coverage: name-normalized lookup unexpectedly matched divergent "
                         f"{name!r}; the regression only holds while the flawed path misses it")
        # (c) coverage_status keys on the wired id, not the name
        if coverage_status(pkg, wired) != "covered":
            fails.append(f"coverage_status({name!r}) != covered")

    # (d) every structural class resolves to a treatment
    for cls in CLASSES:
        if cls not in CLASS_TREATMENT:
            fails.append(f"class {cls!r} has no CLASS_TREATMENT entry")

    # (e) the treatment resolver honors the two conditional splits
    if default_treatment({"name": "examplecli", "bin": True, "brand_mark": True}) != "gradient":
        fails.append("default_treatment: branded app-or-cli must resolve to gradient")
    if default_treatment({"name": "examplecli", "bin": True}) != "flat-category":
        fails.append("default_treatment: unbranded app-or-cli must resolve to flat-category")
    if default_treatment({"name": "somesvc", "desc": "background daemon"}) != "none":
        fails.append("default_treatment: launcher-less daemon-service must resolve to none")
    if default_treatment({"name": "libfoo"}) != "none":
        fails.append("default_treatment: library must resolve to none")
    if default_treatment({"name": "intergen-face", "tier": "ai"}) != "flat-first-party":
        fails.append("default_treatment: first-party must resolve to flat-first-party")

    return fails


def _print_report() -> int:
    wired = wired_stem_index()
    print("IGIC default-by-class classifier")
    print(f"catalog wired-stem index: {len(wired)} emitted .desktop-id stems (coverage oracle)\n")
    print("class -> default treatment")
    for cls in CLASSES:
        print(f"  {cls:20s} -> {CLASS_TREATMENT[cls]}")
    print("\nregression pair (wired .desktop id != package name):")
    for pkg in _REGRESSION:
        st = coverage_status(pkg, wired)
        nm = "MISS" if not _name_normalized_covered(pkg["name"], wired) else "hit"
        print(f"  {pkg['name']:12s} wired={pkg['desktop_id']:34s} stem-keyed={st}  "
              f"name-normalized={nm}")
    fails = selftest()
    if fails:
        print("\nSELFTEST FAIL:")
        for m in fails:
            print(f"  - {m}")
        return 1
    print("\nSELFTEST PASS")
    return 0


if __name__ == "__main__":
    sys.exit(_print_report())
