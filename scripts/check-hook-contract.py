#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""check-hook-contract.py — lifecycle hooks are maintenance-only (fail-closed).

A recipe's lifecycle functions now travel inside the signed archive and run on
the target at install time. That makes them a delivery mechanism, and a
delivery mechanism outside the signed payload is one the archive's signature,
its manifest, and every downstream integrity gate cannot see. A post_install
that writes a file into /usr has placed content on the system that no manifest
declares, no checksum covers, and no verify can check.

Decided 2026-07-30: a lifecycle hook is MAINTENANCE-ONLY. What it may do:

  * enablement — systemctl enable/preset, alternatives, service activation
  * cache / database refresh — ldconfig, depmod, install-info, fc-cache,
    update-mime-database, glib-compile-schemas, gtk-update-icon-cache, mandb
  * machine-unique generation — host keys, machine-id, per-machine state
    under /var and /run, which cannot be shipped in an archive because it is
    unique to the machine
  * attribute restoration on paths the package already owns — chown, chmod,
    setcap on payload the archive delivered

What it may NOT do is WRITE PAYLOAD. Anything a package needs to place under
/usr, /opt or /boot, or any file it needs to create under /etc, belongs in
do_install where the builder stages it, the manifest records it, the archive
carries it signed, and pkm owns it. Moving such a line into do_install is
always the remedy; weakening this gate never is.

DETECTED (against the lifecycle-function text that hookseal actually seals,
comments stripped, so prose about copying files never false-positives):

  1. PAYLOAD WRITE   — install / cp / mv / ln targeting /usr, /opt or /boot.
  2. PAYLOAD REDIRECT— a `>` or `>>` redirect, or a tee, into /usr /opt /boot.
  3. ETC CREATION    — the same forms targeting a path under /etc. A `-d`
                       form is reported as a directory creation, because a
                       finding that mislabels what it found is a finding the
                       reader has to re-derive.
  4. IN-PLACE EDIT   — sed -i / perl -pi on a path under /usr /opt /boot /etc,
                       which rewrites shipped payload out from under the
                       checksum the manifest recorded for it.

HEREDOC BODIES ARE NOT SCANNED. A hook that writes a config file with
`cat > /etc/foo << "EOF"` is flagged on the redirect itself; the lines of the
document it writes are DATA, and scanning them would report a config file's
contents as if they were commands the hook runs. The terminator is tracked so
the scan resumes at the right place — dropping to end-of-function instead
would silently stop checking after the first heredoc.

EVERY SEALED EVENT IS GATED, not only post_install. The ruling names
post_install because that is where the tree's violations live, but the seal seam
seals all six lifecycle events and each one executes on the target, so gating one
would leave five delivery surfaces unchecked. Measured against the tree as
committed: enforcing all six finds exactly the same 63 lines in the same 25
recipes that post_install alone finds, so the wider scope costs nothing today and
catches the pre_install that writes payload tomorrow.

The gate reads the SAME function text the seal seam extracts
(igos-build/hookseal.locate_function), not an approximation of it: a gate that
located lifecycle functions its own way would be checking text that is not
what ships.

Exit codes:
  0  no lifecycle function writes payload.
  1  one or more do (offenders named with recipe, event, line and the line).
  2  arguments invalid / packages tree not found / a recipe could not be read.

KNOWN LIMITS, stated rather than implied. The scan is static and per-command:
a write through a relative path after a `cd`, a path assembled at runtime from
a variable the gate cannot resolve, or a write performed by a helper script the
hook invokes are all invisible to it. It is a regression gate over the shape
recipes actually use, not a proof of absence — the ownership recorder
(pkm/hookrecord.py) is what observes a write nobody predicted.

Usage:
  python3 scripts/check-hook-contract.py [--packages <dir>] [--verbose]
                                         [--events post_install,...]
"""
import argparse
import importlib.util
import re
import shlex
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_hookseal():
    """Import igos-build/hookseal.py (hyphenated dir → load by file path)."""
    path = _REPO_ROOT / "igos-build" / "hookseal.py"
    spec = importlib.util.spec_from_file_location("igos_build_hookseal", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# A build.sh line is a comment when its first non-whitespace char is '#'.
_COMMENT_RE = re.compile(r"^\s*#")

# The payload trees. A package's content lives here and gets there through
# do_install, the manifest and the signed archive — never through a hook.
_PAYLOAD_TREES = r"(?:usr|opt|boot)"

# A path landing in a payload tree, however it is spelled: absolute, or under
# a root variable the hook env provides ($PKM_PACKAGE_ROOT/usr/...).
_PAYLOAD_PATH = rf"/{_PAYLOAD_TREES}/"
# An /etc path that names a FILE (has a segment after etc/). `/etc/` on its
# own is a directory reference, not a creation.
_ETC_PATH = r"/etc/\S"

# Copy/link/edit verbs, matched only in COMMAND POSITION — start of the
# logical line, or straight after a separator (`;`, `&&`, `||`, `|`, `(`) or
# a `sudo`/`time`-style prefix. Matching the bare word anywhere on the line
# reads an English sentence as a command: a recipe echoing "(chroot install
# context) … /etc/apparmor.d/" was reported as an install into /etc by the
# first version of this gate. The trailing lookaround keeps `install-info` —
# a canonical cache refresh whose name merely starts with a verb — from
# reading as an `install`.
_CMD_POS = r"(?:^|[;&|(]|&&|\|\||\bsudo\b|\btime\b|\bthen\b|\bdo\b|\belse\b)\s*"
_COPY_VERBS = ("install", "cp", "mv", "ln")
_COPY_RE = re.compile(
    rf"{_CMD_POS}(?P<verb>{'|'.join(_COPY_VERBS)})(?![-\w])(?P<args>[^\n]*)")
# A redirect into a path, and a `tee` whose argument is a path.
_REDIRECT_RE = re.compile(r"(?<![0-9<>])>>?\s*[\"']?(?P<target>[^\s\"';|&]+)")
_TEE_RE = re.compile(
    rf"{_CMD_POS}tee(?![-\w])(?P<args>[^\n]*)")
# In-place editors: sed -i / perl -pi. The file is the last argument.
_INPLACE_RE = re.compile(
    rf"{_CMD_POS}(?P<verb>sed|perl)(?![-\w])(?P<args>[^\n]*)")
_INPLACE_FLAG_RE = re.compile(r"(?:^|\s)-[A-Za-z]*i(?:[A-Za-z]*)?(?:\s|$|=)")
# mkdir, and `install -d`, create a directory rather than a file.
_MKDIR_RE = re.compile(rf"{_CMD_POS}mkdir(?![-\w])(?P<args>[^\n]*)")

# Tokens that end the command's own argument list.
_ARG_STOP = ("|", "||", "&&", ";", "&", "2>", ">", ">>", "<")

# A heredoc opener: `<< EOF`, `<<-'EOF'`, `<< "EOF"`. The captured word is the
# terminator that ends the document.
_HEREDOC_RE = re.compile(r"<<-?\s*[\"']?([A-Za-z_][A-Za-z0-9_]*)[\"']?")

# Paths under /etc whose CONTENT is a property of the installing machine and
# therefore cannot exist in an archive built on another one. The ruled contract
# already permits machine-unique generation; this list is that clause made
# specific, because the detector keys on path shape and cannot read intent.
#
# NAMED, EVIDENCE-TRACED, AND MINIMAL — the same discipline the verify-class
# registries in pkm/database.py carry. Each entry cites the recipe line it
# exists for; extend only on the same evidence, never to make a tree green.
# The exemption is safe rather than a hole precisely because the hook-output
# recorder (pkm/hookrecord.py) registers what the hook creates to the owning
# package: the path is exempt from SHIPPING in the archive, not from being
# owned. An exempted line is reported, not silently dropped.
MACHINE_UNIQUE_ETC = {
    # glibc-core post_install: `ln -sfv /usr/share/zoneinfo/$tz /etc/localtime`
    # — the symlink target is the installing machine's timezone, chosen at
    # install time. No archive built elsewhere can carry the right one.
    "/etc/localtime": "timezone selection, chosen on the installing machine",
}


def _machine_unique(path):
    """The exemption reason for a machine-unique /etc path, else None."""
    stripped = re.sub(r"^\$\{?\w+\}?", "", path)
    return MACHINE_UNIQUE_ETC.get(stripped)


def _is_payload(path):
    return bool(re.match(r"(?:\$\{?\w+\}?)?/(?:usr|opt|boot)/", path))


def _is_etc(path):
    """An /etc path naming something inside /etc (not the directory itself)."""
    return bool(re.match(r"(?:\$\{?\w+\}?)?/etc/\S", path))


def _has_flag(args, letter):
    """A short flag present on its own or bundled (`-d`, `-vdm755`)."""
    for tok in args.split():
        if tok.startswith("--") or not tok.startswith("-"):
            continue
        if letter in tok[1:].split("=")[0]:
            return True
    return False


def _targets(args, all_operands=False):
    """Destination operand(s) of a copy/link command.

    `cp SRC… DEST`, `install SRC… DEST` and `ln TARGET LINK` all put the
    thing being WRITTEN last; `install -d` writes every operand. Checking
    every path on the line instead would flag `cp /usr/share/foo/template
    /var/lib/foo/conf` — a shipped template copied into machine state, which
    is exactly the maintenance a hook is FOR.
    """
    try:
        tokens = shlex.split(args, comments=False, posix=True)
    except ValueError:
        # Unbalanced quoting (a fragment of a larger construct). A whitespace
        # split sees more than nothing, and seeing less is the direction that
        # loses a violation.
        tokens = args.split()
    operands = []
    for tok in tokens:
        if tok in _ARG_STOP or any(
                tok.startswith(s) for s in (">", ">>", "2>", "<", "|", "&")):
            break
        if tok.startswith("-"):
            continue
        operands.append(tok.strip("\"'"))
    if not operands:
        return []
    return operands if all_operands else [operands[-1]]


def _logical_lines(body):
    """(offset_of_first_line, joined_text) for every scannable command.

    Comments, blank lines and heredoc BODIES are skipped — a heredoc body is
    the data a hook writes, not commands it runs, and scanning it reports a
    config file's own contents as hook behaviour. Backslash continuations are
    JOINED, because the destination of a wrapped `ln -sfv <target> \\` sits on
    the next physical line and a per-line scan would judge the command on its
    source operand alone.
    """
    lines = body.splitlines()
    i = 0
    terminator = None
    while i < len(lines):
        ln = lines[i]
        if terminator is not None:
            if ln.strip() == terminator:
                terminator = None
            i += 1
            continue
        if _COMMENT_RE.match(ln) or not ln.strip():
            i += 1
            continue
        start = i
        joined = ln
        while joined.rstrip().endswith("\\") and i + 1 < len(lines):
            i += 1
            joined = joined.rstrip()[:-1] + " " + lines[i].strip()
        yield start, joined
        m = _HEREDOC_RE.search(joined)
        if m:
            terminator = m.group(1)
        i += 1


def classify(line):
    """(rule_id, reason) for the first contract violation on a command, else None.

    A destination on MACHINE_UNIQUE_ETC yields the "exempt-machine-unique"
    rule id rather than None: the ruled contract permits machine-unique
    generation, and the line is reported under that name instead of being
    dropped where nobody can see the exemption was taken.
    """
    m = _COPY_RE.search(line)
    if m:
        is_dir = m.group("verb") == "install" and _has_flag(m.group("args"), "d")
        for dest in _targets(m.group("args"), all_operands=is_dir):
            if _is_payload(dest):
                return (("payload-dir-create", "creates a directory under "
                         "/usr, /opt or /boot") if is_dir else
                        ("payload-write",
                         "writes payload into /usr, /opt or /boot"))
            if _is_etc(dest):
                why = _machine_unique(dest)
                if why:
                    return ("exempt-machine-unique", why)
                return (("etc-dir-creation", "creates a directory under /etc")
                        if is_dir else
                        ("etc-creation", "creates a file under /etc"))
    m = _MKDIR_RE.search(line)
    if m:
        for dest in _targets(m.group("args"), all_operands=True):
            if _is_payload(dest):
                return ("payload-dir-create",
                        "creates a directory under /usr, /opt or /boot")
            if _is_etc(dest):
                why = _machine_unique(dest)
                if why:
                    return ("exempt-machine-unique", why)
                return ("etc-dir-creation", "creates a directory under /etc")
    for m in _REDIRECT_RE.finditer(line):
        target = m.group("target").strip("\"'")
        if _is_payload(target):
            return ("payload-redirect",
                    "redirects output into /usr, /opt or /boot")
        if _is_etc(target):
            why = _machine_unique(target)
            if why:
                return ("exempt-machine-unique", why)
            return ("etc-creation", "creates a file under /etc")
    m = _TEE_RE.search(line)
    if m:
        for dest in _targets(m.group("args"), all_operands=True):
            if _is_payload(dest):
                return ("payload-redirect",
                        "redirects output into /usr, /opt or /boot")
            if _is_etc(dest):
                why = _machine_unique(dest)
                if why:
                    return ("exempt-machine-unique", why)
                return ("etc-creation", "creates a file under /etc")
    m = _INPLACE_RE.search(line)
    if m and _INPLACE_FLAG_RE.search(" " + m.group("args")):
        for dest in _targets(m.group("args"), all_operands=True):
            if _is_payload(dest) or _is_etc(dest):
                why = _machine_unique(dest)
                if why:
                    return ("exempt-machine-unique", why)
                return ("in-place-edit", "rewrites shipped payload in place")
    return None


def violations_in_body(body, first_line):
    """Contract violations in one lifecycle-function body.

    Returns a list of (rule_id, build_sh_line_number, line_text, reason).
    A command is reported once, under the first rule that matches it, so a
    single offending line does not read as four separate findings.
    """
    found = []
    for offset, line in _logical_lines(body):
        hit = classify(line)
        if hit:
            found.append(
                (hit[0], first_line + offset, line.strip(), hit[1]))
    return found


def scan_recipe(build_sh_text, events, hookseal):
    """Contract violations across a recipe's lifecycle functions.

    Returns (violations, unreadable_events). A function whose body cannot be
    extracted is returned rather than skipped: the seal seam fails the build
    on exactly that condition, and a gate that silently passed a recipe it
    could not read would be the mask this whole contract exists to remove.
    """
    violations = []
    unreadable = []
    for event in events:
        try:
            located = hookseal.locate_function(build_sh_text, event)
        except hookseal.SealError as e:
            unreadable.append((event, str(e)))
            continue
        if located is None:
            continue
        body, first_line = located
        for rule_id, line_no, text, reason in violations_in_body(
                body, first_line):
            violations.append((event, rule_id, line_no, text, reason))
    return violations, unreadable


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--packages", default=str(_REPO_ROOT / "packages"),
        help="path to the packages/ tree (default: repo packages/)")
    ap.add_argument(
        "--events", default=",".join(_load_hookseal().LIFECYCLE_EVENTS),
        help="comma-separated lifecycle events to enforce against "
             "(default: every lifecycle event the seal seam seals)")
    ap.add_argument(
        "--verbose", action="store_true",
        help="list every recipe that declares a gated lifecycle function")
    args = ap.parse_args(argv)

    packages_dir = Path(args.packages)
    if not packages_dir.is_dir():
        print(f"error: packages tree not found: {packages_dir}",
              file=sys.stderr)
        return 2

    hookseal = _load_hookseal()
    events = [e.strip() for e in args.events.split(",") if e.strip()]
    unknown = [e for e in events if e not in hookseal.LIFECYCLE_EVENTS]
    if unknown:
        print(f"error: not lifecycle events: {', '.join(unknown)}",
              file=sys.stderr)
        return 2

    scanned = []    # (name, rel, [events declared])
    offenders = []  # (name, rel, event, rule_id, line_no, text, reason)
    exempt = []     # same shape — machine-unique, reported but not failing
    unreadable = []  # (name, rel, event, message)
    for tier_dir in sorted(p for p in packages_dir.iterdir() if p.is_dir()):
        for pkg_dir in sorted(p for p in tier_dir.iterdir() if p.is_dir()):
            build_sh = pkg_dir / "build.sh"
            if not build_sh.is_file():
                continue
            rel = str(pkg_dir.relative_to(packages_dir))
            name = pkg_dir.name
            text = build_sh.read_text(errors="replace")
            declared = [e for e in events
                        if re.search(rf"^(?:function\s+)?{e}\s*(?:\(\s*\))?\s*\{{\s*$",
                                     text, re.M)]
            if declared:
                scanned.append((name, rel, declared))
            found, unread = scan_recipe(text, events, hookseal)
            for event, msg in unread:
                unreadable.append((name, rel, event, msg))
            for event, rule_id, line_no, line_text, reason in found:
                row = (name, rel, event, rule_id, line_no, line_text, reason)
                if rule_id == "exempt-machine-unique":
                    exempt.append(row)
                else:
                    offenders.append(row)

    if args.verbose:
        print(f"Scanned {len(scanned)} recipe(s) declaring "
              f"{'/'.join(events)}:")
        for name, rel, declared in scanned:
            bad = sum(1 for o in offenders if o[1] == rel)
            mark = f"{bad} violation(s)" if bad else "clean"
            print(f"  [{mark}] {name} ({rel}/build.sh) — {', '.join(declared)}")

    rc = 0
    if unreadable:
        print(f"\nERROR: {len(unreadable)} lifecycle function(s) could not be "
              f"read — the seal seam fails the build on this same condition:",
              file=sys.stderr)
        for name, rel, event, msg in unreadable:
            print(f"  - {name} ({rel}/build.sh) {event}(): {msg}",
                  file=sys.stderr)
        rc = 1

    if offenders:
        print(f"\nERROR: {len(offenders)} lifecycle-hook line(s) in "
              f"{len({o[1] for o in offenders})} recipe(s) WRITE payload. A "
              f"lifecycle hook is maintenance-only (decided 2026-07-30):",
              file=sys.stderr)
        for name, rel, event, rule_id, line_no, text, reason in offenders:
            print(f"  - {name} ({rel}/build.sh:{line_no}) {event}() "
                  f"[{rule_id}] {reason}", file=sys.stderr)
            print(f"      {text}", file=sys.stderr)
        print(
            "\nMove each line into do_install, where the builder stages the "
            "content, the manifest records it, the signed archive carries it "
            "and pkm owns it. A hook may enable, refresh a cache or database, "
            "generate machine-unique state, and restore attributes on paths "
            "the package already owns — it may not deliver content.",
            file=sys.stderr)
        rc = 1

    if not scanned:
        # A gate's exit 0 must mean "the whole tree was checked and is clean",
        # never "there was nothing to check" (review finding H4). A packages
        # tree with no lifecycle function at all is a scan that found its
        # inventory empty, which is a condition to report, not to certify.
        print(f"\nERROR: scanned {packages_dir} and found NO recipe declaring "
              f"{'/'.join(events)} — an empty inventory is not a pass.",
              file=sys.stderr)
        return 1

    if exempt:
        # ALWAYS printed, pass or fail. An exemption nobody sees is a hole:
        # the whole point of naming the class is that the next reader can
        # audit the list against the lines it was granted for.
        print(f"\n{len(exempt)} line(s) exempt as machine-unique "
              f"(MACHINE_UNIQUE_ETC — the contract's machine-unique clause; "
              f"ownership is still recorded by pkm/hookrecord.py):")
        for name, rel, event, _rid, line_no, text, reason in exempt:
            print(f"  - {name} ({rel}/build.sh:{line_no}) {event}() — {reason}")
            print(f"      {text}")

    if rc == 0:
        print(f"OK: {len(scanned)} recipe(s) declare {'/'.join(events)}; "
              f"none writes payload (hook contract, decided 2026-07-30).")
    return rc


if __name__ == "__main__":
    sys.exit(main())
