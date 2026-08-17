#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""time64audit.py — archive-time build-log assertion against time64 ABI
mixing in 32-bit packages (RT-8, GE gate-tooling).

THE HAZARD: the lib32 profile targets the time32 ABI the prebuilt Windows/
Steam game binaries were linked against. An individual upstream package can
opt ITSELF into 64-bit time_t (autoconf/gnulib year-2038 probes add
`-D_TIME_BITS=64` to CFLAGS silently) even though the profile never set it —
producing struct-layout skew in public ABIs (e.g. `struct timespec` through
alsa-lib) against time32 consumers. That is silent memory corruption at play
time, not an error path, which is why the define is asserted-absent from the
build log rather than trusted-absent from the profile.

SEMANTICS (keyed on the recipe's `elf_class:` contract):
  * elf_class "64" (the default): no-op — 64-bit time_t is already the ABI;
    the define is harmless there.
  * elf_class "32": every provided build-log file is scanned for the
    forbidden define forms. ANY hit is a violation naming file + line.
    FAIL-CLOSED: zero readable log files is itself a violation — a gate
    that cannot see the log must halt, never wave through.
  * elf_class "mixed": the audit is waived LOUDLY (same rule as the width
    audit; grub's i386-pc BIOS modules are freestanding boot code with no
    libc time_t ABI). WHO may declare mixed is governed by
    ELF_CLASS_MIXED_ALLOWED in validate-package-tiers.py.
  * TIME64_ABI_PROVIDERS (currently exactly lib32-glibc): the audit is
    waived LOUDLY for the package that IMPLEMENTS the time_t ABI. glibc's
    own build compiles its internal time64 entry points with
    -D_TIME_BITS=64 by design — the hazard this audit exists for (a leaf
    package silently opting its public structs into time64 against time32
    consumers) cannot apply to the C library that provides BOTH ABIs; its
    installed m32 headers still default consumers to time32. Governed
    here, at the single audit chokepoint (GE-01 launch-6 grounding,
    2026-07-03).
  * PREBUILT_VENDOR_32 (currently exactly lib32-nvidia): the audit is
    waived LOUDLY for a 32-bit package whose payload is PREBUILT VENDOR
    BINARIES — its recipe runs NO compiler (extract + ship), so the
    asserted-absent define can never be enabled BY OUR BUILD, and the
    F2-a compile-evidence requirement is unsatisfiable by construction
    (no compile lines exist, not silent-rules suppression). The blob's
    time_t ABI is the vendor's fixed upstream property; what this audit
    protects against (our build opting a 32-bit package into time64)
    cannot occur without a compile step. Additions require the operator's
    declaration (same governance weight as the other sets; origin:
    lib32-nvidia 580.159.04 at the 2026-07-10 9B burn, decided).

FORBIDDEN FORMS (the shapes that reach a compiler, chosen to miss autoconf's
"checking whether ..." probe CHATTER and hit only an ENABLED define):
  * `-D_TIME_BITS=64` / `-D _TIME_BITS=64`   (command-line define, echoed
    compile lines — the decisive gnulib/meson/cmake signal)
  * `#define _TIME_BITS 64`                  (config.h dumps in the log)

The G2 lib32 profile's `-U_TIME_BITS` scrub is the PREVENTION half and lands
with the profile itself (RT-7's cross-file work in the multilib build phase);
this assertion is the ENFORCEMENT half that outlives any profile mistake.

Stdlib-only; importable (the Python builder chokepoint) and a CLI (the bash
pkg_archive chokepoint) — one predicate, both builder chains, the same
layering as elfaudit.py (RT-1).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Iterable, List

# The define forms that reach a compiler. Deliberately NOT a bare
# `_TIME_BITS` substring match: autoconf probe chatter ("checking whether
# _TIME_BITS is needed... no") must not false-halt a clean build.
# Widened per the adversarial re-certification (finding F2-b): any whitespace run after -D, optional
# quoting around the symbol/assignment, parenthesized config-header value.
# The time64-ABI PROVIDER waiver set — see SEMANTICS in the module
# docstring. Exactly the package(s) that IMPLEMENT the dual time_t ABI;
# every lib32 leaf package stays fully audited. Additions require the
# operator's declaration (same governance weight as ELF_CLASS_MIXED_ALLOWED).
TIME64_ABI_PROVIDERS = frozenset({"lib32-glibc"})

# The prebuilt-vendor-binary waiver set — see SEMANTICS in the module
# docstring. Exactly the 32-bit package(s) whose recipes run NO compiler
# (prebuilt vendor payload, extract + ship); every compiled lib32 package
# stays fully audited. Additions require the operator's declaration.
PREBUILT_VENDOR_32 = frozenset({"lib32-nvidia"})

FORBIDDEN = (
    # Optional quoting on the symbol AND on the value (a quoted-value form
    # like ="64" evaded the first widening — re-cert residual 2).
    re.compile(r"-D\s*[\"']?_TIME_BITS[\"']?\s*=\s*[\"']?64"),
    re.compile(r"#\s*define\s+_TIME_BITS\s+\(?\s*64\s*\)?"),
)

# Compile-line VISIBILITY evidence (re-certification finding F2-a): libtool, automake
# silent-rules, and ninja's default all suppress the full compiler
# invocation ("CC pcm.lo" instead of the real command line), so the define
# never reaches the log and a "clean" scan is actually BLIND — the common
# case, not an edge. A clean 32-bit scan therefore additionally requires at
# least one genuine full compiler-invocation line (a compiler token plus a
# -c/-o flag); otherwise the audit REFUSES naming the suppression. The
# prevention half (forcing V=1 / --disable-silent-rules / ninja -v in the
# lib32 profile) rides the G2 profile authoring (RT-7).
COMPILE_EVIDENCE = re.compile(
    r"(?:^|[\s/])(?:gcc|g\+\+|cc|c\+\+|clang|clang\+\+)\b[^\n]*\s-[co]\b")

VALID_EXPECTED = ("64", "32", "mixed")


def waiver_reason(name: str) -> str | None:
    """The ONE membership check both builder chains consult (governed sets).

    Returns the loud-announce reason for a name-governed waiver, or None.
    Lives at the shared-predicate level so the bash CLI chain and the
    Python Builder chokepoint can NEVER drift apart on membership — the
    2026-07-10 lib32-nvidia halt recurred precisely because the first
    waiver landed only in main() while the Python tier calls
    audit_package_logs directly (and the provider waiver had the same
    latent gap: lib32-glibc only ever rides the bash chain).
    """
    if name in TIME64_ABI_PROVIDERS:
        return ("declared time64-ABI provider (glibc compiles its internal "
                "time64 implementation half with -D_TIME_BITS=64 by design; "
                "its m32 headers still default consumers to time32). "
                "Governance: TIME64_ABI_PROVIDERS in this audit.")
    if name in PREBUILT_VENDOR_32:
        return ("declared prebuilt-vendor 32-bit payload (the recipe runs "
                "no compiler: extract + ship, so our build cannot enable "
                "the define and no compile line can ever appear in the "
                "log). Governance: PREBUILT_VENDOR_32 in this audit.")
    return None


def audit_log_file(path: Path) -> tuple:
    """Scan one build log for the forbidden time64 define forms.

    Returns (violations, has_compile_evidence): violations as
    "path:lineno: <line>" strings; has_compile_evidence True when at least
    one full compiler-invocation line is visible in this log (F2-a — a log
    with no visible compile line cannot prove the define absent). An
    unreadable file returns a violation naming it (fail-closed), never an
    empty pass.
    """
    violations: List[str] = []
    evidence = False
    try:
        with open(path, "r", errors="replace") as fh:
            for lineno, line in enumerate(fh, 1):
                if not evidence and COMPILE_EVIDENCE.search(line):
                    evidence = True
                for pat in FORBIDDEN:
                    if pat.search(line):
                        violations.append(
                            f"{path}:{lineno}: {line.rstrip()}")
                        break
    except OSError as e:
        violations.append(
            f"{path}: unreadable at audit time ({e.__class__.__name__}) — "
            f"refusing to assume it is clean")
    return violations, evidence


def audit_package_logs(log_paths: Iterable[Path], expected: str,
                       name: str = "") -> List[str]:
    """The package-level predicate both builder chains run.

    expected == "64"  -> no-op (empty list).
    expected == "mixed" -> no-op here; the CALLER announces the waiver
                           (mirrors elfaudit's mixed contract).
    expected == "32"  -> scan every log; zero readable logs is a violation.
    """
    if expected not in VALID_EXPECTED:
        return [f"invalid expected elf_class '{expected}' "
                f"(valid: {VALID_EXPECTED})"]
    if expected in ("64", "mixed"):
        return []
    # Name-governed waivers resolve HERE, at the shared predicate, so both
    # builder chains agree by construction; callers announce loudly.
    if waiver_reason(name):
        return []

    paths = [Path(p) for p in log_paths]
    if not paths:
        return [f"{name or 'package'}: elf_class=32 but NO build log was "
                f"provided to the time64 audit — a gate that cannot see "
                f"must halt, not wave through"]
    violations: List[str] = []
    readable = 0
    any_evidence = False
    for p in paths:
        v, evidence = audit_log_file(p)
        any_evidence = any_evidence or evidence
        if not v:
            readable += 1
        else:
            # Distinguish "hits" from "unreadable": both are violations,
            # but only genuinely-read files count toward visibility.
            if not any("unreadable at audit time" in x for x in v):
                readable += 1
            violations.extend(v)
    if readable == 0 and not violations:
        violations.append(
            f"{name or 'package'}: elf_class=32 but no build log was "
            f"readable — refusing to assume the time64 define is absent")
    # F2-a (re-certification finding): a clean scan with NO visible full compile line is
    # BLIND, not clean — silent-rules/libtool/ninja suppression hides the
    # very lines the define would appear on. Refuse, naming the fix.
    if not violations and not any_evidence:
        violations.append(
            f"{name or 'package'}: elf_class=32 but no full "
            f"compiler-invocation line is visible in the build log(s) — "
            f"silent-rules/ninja suppression makes this scan blind; force "
            f"verbose compile logging for this package (V=1 / "
            f"--disable-silent-rules / ninja -v) so the audit can see")
    return violations


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="RT-8 archive-time time64 build-log assertion")
    ap.add_argument("--name", default="", help="package name (messages)")
    ap.add_argument("--expected", default="64", choices=VALID_EXPECTED,
                    help="the recipe's elf_class contract")
    ap.add_argument("--log", action="append", default=[],
                    help="build log file (repeatable)")
    args = ap.parse_args(argv)

    label = f" [{args.name}]" if args.name else ""
    if args.expected == "mixed":
        print(f"time64 audit{label}: elf_class=mixed — the time64 log "
              f"assertion is waived by the recipe's explicit declaration "
              f"(freestanding boot code carries no libc time_t ABI)",
              file=sys.stderr)
        return 0
    if args.expected == "64":
        return 0
    if args.name in TIME64_ABI_PROVIDERS:
        print(f"time64 audit{label}: WAIVED — declared time64-ABI provider "
              f"(glibc compiles its internal time64 implementation half "
              f"with -D_TIME_BITS=64 by design; its m32 headers still "
              f"default consumers to time32). Governance: "
              f"TIME64_ABI_PROVIDERS in this audit.", file=sys.stderr)
        return 0
    if args.name in PREBUILT_VENDOR_32:
        print(f"time64 audit{label}: WAIVED — declared prebuilt-vendor "
              f"32-bit payload (the recipe runs no compiler: extract + "
              f"ship, so our build cannot enable the define and no compile "
              f"line can ever appear in the log). Governance: "
              f"PREBUILT_VENDOR_32 in this audit.", file=sys.stderr)
        return 0

    violations = audit_package_logs([Path(p) for p in args.log],
                                    args.expected, args.name)
    if violations:
        print(f"time64 audit{label}: REFUSED — a 32-bit package must never "
              f"enable 64-bit time_t (silent struct-layout skew against "
              f"time32 consumers):", file=sys.stderr)
        for v in violations:
            print(f"  {v}", file=sys.stderr)
        return 1
    print(f"time64 audit{label}: clean ({len(args.log)} log file(s) scanned)",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
