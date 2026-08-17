# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Seal a recipe's lifecycle functions into the archive as .scripts/<event>.sh.

A recipe's ``post_install()`` runs at BUILD time, in the build chroot. On a
target installed from archives it never ran at all, so anything the recipe
expected to happen at install time — enablement, a cache refresh, a
machine-unique file — simply did not. pkm already has the execution mechanism:
``pkm/hooks.py`` fires ``.scripts/<event>.sh`` out of the extracted archive
between the canonical pre and post hook layers. Nothing was ever putting those
scripts INTO archives, so exactly one package hand-rolls its own. This is the
missing seam, and it is deliberately generic so that package can converge onto
it instead of staying bespoke.

WHY THE FUNCTION IS EXTRACTED RATHER THAN THE SCRIPT SOURCED. The obvious
implementation — ship build.sh and have the hook ``source build.sh &&
post_install`` — is what the installer's hook phase does today, and it is
context-fragile: a build.sh with top-level code runs that code at source time,
on a machine that is not the build host. A recipe that reads a build-tree path
at the top level exits non-zero before its function is ever called. Extracting
the function body means the sealed script carries the recipe's intent and none
of its build-time preamble.

There is a second, sharper reason, and it is the ordering trap in the builder:
``post_install`` runs AFTER the manifest and archive are sealed. Capturing what
the hook DID during the build would capture build-chroot side effects — files
belonging to a filesystem that is not the target's. The seal must therefore be
a purely textual read of the recipe, which is what this module does. It never
executes anything.

EXTRACTION IS FAIL-CLOSED. The body is taken from the opening ``event() {`` line
to the matching column-0 ``}`` — the shape every recipe in this tree is written
in, and the same shape the installer's own hook scanner keys on. Every extracted
body is then reassembled and syntax-checked with ``bash -n``, and the emitted
script is syntax-checked again. A function whose closer cannot be located, or
whose extracted body does not parse, raises instead of sealing a truncated
script: a hook that silently runs half of what the recipe said is worse than one
that was never sealed, because it will appear to have succeeded.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Mirrors pkm/hooks.py LIFECYCLE_EVENTS. Kept as a literal rather than imported
# so this module stays usable from the bash lane inside the build chroot, where
# the pkm package may not be importable yet. The parity is asserted by a test —
# a divergence must fail loudly rather than seal a script pkm will never fire.
LIFECYCLE_EVENTS = (
    "pre_install", "post_install",
    "pre_upgrade", "post_upgrade",
    "pre_remove", "post_remove",
)

SCRIPTS_DIR = ".scripts"

# `name() {`, `name () {`, or `function name {` — with the brace on the same
# line, which is how every recipe in this tree declares a function. A recipe
# that declares one some other way will simply not match, and not sealing is
# the safe direction: the package behaves exactly as it does today.
_OPENER = re.compile(
    r"^(?:function\s+)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?:\(\s*\))?\s*\{\s*$")


class SealError(RuntimeError):
    """Extraction or validation failed — the caller must fail the build."""


def _bash_available() -> bool:
    return shutil.which("bash") is not None


def _syntax_ok(text: str) -> bool:
    """True when bash accepts `text` as a parseable script.

    When bash is unavailable the check cannot be performed, and this returns
    True rather than blocking a seal on a missing tool. That is a deliberate
    narrowing: the caller still fails closed on a missing closer, which is the
    truncation case this guards against; bash absence would otherwise make the
    build fail for a reason that has nothing to do with the recipe.
    """
    if not _bash_available():
        return True
    try:
        r = subprocess.run(["bash", "-n"], input=text, text=True,
                           capture_output=True, timeout=30)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return True


def extract_function(build_sh_text: str, event: str) -> str | None:
    """Return the body of `event`'s function, or None when it is not declared.

    The body is returned verbatim, without the enclosing braces and without
    dedenting: the recipe's own indentation is preserved so a sealed script
    reads like the recipe it came from.

    Raises SealError when the function opens but its column-0 closer cannot be
    found, or when the extracted body does not parse as the function body. Both
    mean the extraction is untrustworthy, and a truncated hook must never ship.
    """
    located = locate_function(build_sh_text, event)
    return None if located is None else located[0]


def locate_function(build_sh_text: str, event: str):
    """Return (body, first_body_line_number) for `event`, or None.

    Same extraction as extract_function, plus the 1-indexed build.sh line the
    body starts on. The line number is what lets a caller name the offending
    line in the recipe rather than in a detached copy of it — the hook-contract
    gate reports against build.sh, and a gate that located lifecycle functions
    its own way could check text the sealer never seals.
    """
    if event not in LIFECYCLE_EVENTS:
        raise ValueError(f"unknown lifecycle event: {event}")

    lines = build_sh_text.splitlines()
    start = None
    for i, line in enumerate(lines):
        m = _OPENER.match(line)
        if m and m.group("name") == event:
            start = i
            break
    if start is None:
        return None

    end = None
    for j in range(start + 1, len(lines)):
        if lines[j] == "}" or lines[j].rstrip() == "}":
            if lines[j].startswith("}"):
                end = j
                break
    if end is None:
        raise SealError(
            f"{event}() opens at line {start + 1} but no column-0 '}}' closes "
            f"it — refusing to seal a truncated hook")

    body = "\n".join(lines[start + 1:end])

    # Reassembling and parsing is what catches a WRONG closer: if the real
    # function contained a column-0 '}' inside a heredoc, the body cut short
    # there will almost never parse as a complete function.
    if not _syntax_ok(f"{event}() {{\n{body}\n}}\n"):
        raise SealError(
            f"{event}() body extracted from lines "
            f"{start + 2}-{end} does not parse — refusing to seal it")
    return body, start + 2


def render_script(event: str, body: str, name: str, version: str) -> str:
    """Wrap an extracted body as a standalone lifecycle script.

    `set -e` matches how pkm runs these (bash -e) and how the recipes' own
    functions open. The provenance header is for the person who finds this file
    inside an archive and needs to know it was generated, not hand-written.

    THE BODY IS RE-WRAPPED IN A FUNCTION, WHICH IS NOT COSMETIC. The body was
    written inside `event() { ... }`, so it may legally use constructs that only
    exist in a function — `return` and `local` being the common ones. Pasted at
    top level those become run-time errors: bash refuses a bare `return` outside
    a function with "return: can only `return' from a function or sourced
    script" and exits 2. `packages/core/intergenos-base-files` is written that
    way — a documented stub whose entire body is `return 0` — so its sealed hook
    failed on every archive install until this wrapper existed.

    `bash -n` does not see this class at all: a top-level `return` is a valid
    parse and an invalid execution, so the _syntax_ok() checks around this
    function passed the broken script through. Restoring the function context is
    what makes the check meaningful again, because now there is no construct the
    recipe could legally use that the sealed script cannot.

    The body itself is still copied verbatim — the wrapper only adds lines
    around it. Reviewing the recipe therefore remains enough to know what the
    sealed hook does.

    "$@" is forwarded so a caller that passes arguments keeps them, and the
    invocation is the script's last command so its exit status becomes the
    script's exit status: a hook that fails still fails.
    """
    fn = f"__hookseal_{event}"
    return (
        f"#!/bin/bash\n"
        f"# Generated by igos-build/hookseal.py from the {name} recipe's\n"
        f"# {event}() function at build time. Do not edit inside the archive —\n"
        f"# edit the recipe and rebuild.\n"
        f"# package: {name}-{version}\n"
        f"#\n"
        f"# The body runs inside a function because that is the context the\n"
        f"# recipe wrote it in: `return` and `local` are legal there and are\n"
        f"# run-time errors at top level.\n"
        f"set -e\n"
        f"{fn}() {{\n"
        f"{body}\n"
        f"}}\n"
        f"{fn} \"$@\"\n"
    )


def seal_into_staging(staging_dir, build_sh, name, version,
                      events=LIFECYCLE_EVENTS) -> list[str]:
    """Write <staging_dir>/.scripts/<event>.sh for each declared lifecycle fn.

    Returns the sealed event names (empty when the recipe declares none, which
    is the overwhelmingly common case and is a silent no-op).

    An existing .scripts/<event>.sh is left ALONE and reported as sealed. A
    recipe that hand-writes its own script has said something more specific than
    its function did, and overwriting that from a generic extraction would be
    this seam quietly changing a package's behaviour on the way past.
    """
    staging_dir = Path(staging_dir)
    build_sh = Path(build_sh)
    if not build_sh.is_file():
        return []
    text = build_sh.read_text(errors="surrogateescape")

    sealed: list[str] = []
    for event in events:
        target = staging_dir / SCRIPTS_DIR / f"{event}.sh"
        if target.is_file():
            sealed.append(event)
            continue
        body = extract_function(text, event)
        if body is None:
            continue
        script = render_script(event, body, name, version)
        if not _syntax_ok(script):
            raise SealError(
                f"the sealed {event}.sh for {name}-{version} does not parse — "
                f"refusing to ship it")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(script)
        target.chmod(0o755)
        sealed.append(event)
    return sealed


def main(argv=None) -> int:
    """CLI for the bash build lane, which cannot import this module.

    Mirrors how scripts/gen-pkginfo.py is invoked from pkg_archive: staging dir
    in, sealed scripts out, non-zero exit on a refusal so the build stops.
    """
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--staging", required=True, help="package staging directory")
    ap.add_argument("--build-sh", required=True, help="the recipe's build.sh")
    ap.add_argument("--name", required=True)
    ap.add_argument("--version", required=True)
    args = ap.parse_args(argv)

    try:
        sealed = seal_into_staging(args.staging, args.build_sh,
                                   args.name, args.version)
    except SealError as e:
        print(f"[hookseal] REFUSED for {args.name}-{args.version}: {e}",
              file=sys.stderr)
        return 1
    if sealed:
        print(f"[hookseal] sealed {', '.join(sealed)} for "
              f"{args.name}-{args.version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
