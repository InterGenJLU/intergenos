#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Fail-closed validate-phase gate: every file a generated package's install
step takes from its extracted source tree must be a member of that package's
generated tarball.

WHY THIS EXISTS
---------------
A generated source tarball is produced at build time from in-tree assets, with
no committed sha256 pin — the generator is trusted to stage what the recipe
consumes. Nothing checked that the two agreed.

On 2026-07-30 they stopped agreeing. intergen-welcome's do_install had installed
org.intergenos.Wiki.svg since release 19, but the generator never staged the
file into the tarball. Every build from a freshly generated tarball failed at
`install: cannot stat 'org.intergenos.Wiki.svg'` — the package could not build
at all — for four days, while its release note claimed the mark ships. Nothing
in the tree could catch the class, because the recipe and the generator are
separate files that no check read together.

This gate reads them together. It lists what the tarball actually contains,
works out what the install step actually consumes, and refuses the build when
the second is not a subset of the first.

WHAT IT CHECKS
--------------
For every package whose package.yml declares a `generated: true` source:

  1. the generated tarball exists in the sources directory;
  2. its members are enumerated and the leading path component is dropped,
     because the builder extracts with `tar --strip-components=1`
     (igos-build/builder.py:586,605 and scripts/pkg-functions.sh:210,224), so a
     member stored as `iw-pkg/foo` is what the recipe sees as `foo`;
  3. the recipe's install function is parsed for the source-tree paths it
     consumes;
  4. any consumed path that is not a member is a HALT, named by package and by
     path.

WHY IT ENUMERATES BY `generated: true` AND NOT BY FILENAME
----------------------------------------------------------
The set is taken from the package recipes, not from a glob over the sources
directory. Two of the nine tarballs this repository's asset generator produces —
bibata-cursor-theme and catppuccin-mocha-blue — do not begin with "intergen", so
any intergen*-shaped glob silently covers seven of nine and reads as complete.
Coverage that shrinks silently is the failure mode this gate exists to prevent,
so membership in the checked set is a property a recipe declares, and a package
that stops declaring it disappears from the build entirely rather than quietly
from this gate.

WHEN IT CANNOT DETERMINE THE ANSWER, IT FAILS
---------------------------------------------
An install step is shell. This parser understands a deliberately small subset of
it (below). Anything outside that subset — an unrecognised command, a variable
it cannot resolve, a sourced helper it cannot read — makes the consumed set
unknown for that package, and an unknown consumed set is reported as
"could not determine" and FAILS.

That is the honest posture and it is the expensive lesson of pkm verify (landed
2026-08-03 at public dev 330ff4261), where a check that could not read a file
counted it as verified: a check that cannot check must say so. A silent skip
here would be worse than no gate, because the build would read as covered.

The subset is meant to grow deliberately. Widening it is a code change with a
test, not a shrug.

"THE GENERATOR PRODUCED NOTHING" IS NOT "THE GENERATOR COULD NOT BE READ"
------------------------------------------------------------------------
One generator legitimately produces no tarball outside a release build.
intergenos-wiki's source is the rendered mdBook tree, which is staged into
build/wiki-book from a separate repository at release time and is not carried in
git; without it the generator SKIPs by design rather than fabricating content.
Every by-hand firing of this gate therefore reported that package as "could not
determine" and exited 1 — a halt that was correct under the old model and wrong
about the situation, because nothing had failed to parse: the input legitimately
did not exist yet at that call site. A known-noise failure that everyone reads
around is exactly how a real failure eventually hides, so the gate learns the
distinction instead of the reader carrying it.

A package states the condition itself, in its recipe:

    release_staged_source: "build/wiki-book — <why the generator produces
                            nothing without it>"

The value is a non-empty string, and the gate quotes it back in its output, so
the class can never be claimed without saying what is staged and why. It is
DECLARED, never inferred: no filename shape, no directory guess, nothing that
could widen silently to a package that did not ask for it. A typo'd key is not a
quiet return to the old behaviour either — the recipe parser rejects unknown
top-level keys (igos-build/parser.py KNOWN_FIELDS), so a misspelled declaration
fails the build loudly at parse time.

What the declaration changes is EXACTLY ONE verdict — an absent tarball becomes
its own named state, reported separately from clean and from could-not-determine
and counted in the summary, rather than a halt. What it does not change:

  * a declared package whose tarball IS present is checked in full, like any
    other package — a present input is never skipped;
  * the recipe is parsed even when the tarball is absent, so a recipe this gate
    cannot read still HALTS;
  * an UNDECLARED absent tarball still halts as could-not-determine;
  * a malformed declaration is a setup error, not a pass.

Nothing is masked by the softer verdict. An absent generated tarball at a real
build is caught loudly downstream at the point it matters: the builder refuses
to build a package whose declared source is not on disk
(igos-build/builder.py `extract_source`, "Source not found"). This gate declining
to halt on it does not let such a build proceed.

Exit 0 clean, 1 on violations, 2 on usage/setup errors — including the case where
every generated package's tarball is release-staged and absent, because a gate
that verified nothing must not print PASS.
"""

from __future__ import annotations

import argparse
import re
import shlex
import sys
import tarfile
import time
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - environment defect, not logic
    print("FATAL: pyyaml required (pip install pyyaml)", file=sys.stderr)
    sys.exit(2)

REPO_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Commands that appear inside an install function and can never consume a file
# from the extracted source tree. Anything NOT listed here makes the package
# undeterminable, which fails. Add to this list deliberately, with a test.
#
# Shell keywords are included because the parser walks physical lines rather
# than building a syntax tree: `if`, `for`, `done` and friends arrive here as
# ordinary leading tokens.
# ---------------------------------------------------------------------------
NON_CONSUMING = {
    # shell keywords / builtins
    "if", "then", "else", "elif", "fi", "for", "in", "do", "done", "while",
    "until", "case", "esac", "function", "return", "exit", "set", "local",
    "continue", "break",
    "export", "unset", "shift", "true", "false", ":", "test", "[", "[[",
    "echo", "printf", "read", "eval",
    # filesystem operations whose operands are destinations under DESTDIR, or
    # which create rather than consume
    "mkdir", "rmdir", "rm", "chmod", "chown", "chgrp", "touch", "ln",
    "find", "xargs",
    # content generators (their payload is a heredoc, which is stripped before
    # parsing — see _strip_heredocs)
    "cat", "tee",
    # post-install style tooling invoked against the staged tree
    "glib-compile-schemas", "gtk-update-icon-cache", "update-desktop-database",
    "update-mime-database", "gdk-pixbuf-query-loaders", "ldconfig",
    "desktop-file-install", "desktop-file-validate", "install-info",
    "python3", "python", "sed", "grep", "awk", "sort", "uniq", "head", "tail",
    "ls", "command", "pushd", "popd", "cd",
}

# Commands whose non-flag operands, except the last, are read from the
# extracted source tree.
CONSUMING = {"install", "cp", "mv"}

# Path tokens that mean "the whole extracted tree", not one member.
WHOLE_TREE = {".", "./", "./.", "*", "./*", "./ *"}

# package.yml variable substitution. Mirrors igos_build.parser._resolve_variables;
# if that set drifts, audit both consumers (the same warning verify-sources
# carries at scripts/build-intergenos.sh).
_VAR_RE = re.compile(r"\$\{(\w+)\}")


class Undeterminable(Exception):
    """The consumed set cannot be established for this package."""


# ---------------------------------------------------------------------------
# package.yml reading
# ---------------------------------------------------------------------------

def _resolve_variables(text: str, name: str, version: str) -> str:
    """Expand the ${...} placeholders package.yml URLs are allowed to carry."""
    parts = version.split(".")
    table = {
        "name": name,
        "version": version,
        "version_major": parts[0] if parts else version,
        "version_major_minor": ".".join(parts[:2]) if len(parts) >= 2 else version,
        "version_patch": parts[2] if len(parts) >= 3 else "",
    }

    def sub(match: re.Match) -> str:
        key = match.group(1)
        if key not in table:
            raise Undeterminable(
                f"package.yml uses ${{{key}}}, which this gate does not know how "
                f"to expand; teach _resolve_variables or drop the placeholder"
            )
        return table[key]

    return _VAR_RE.sub(sub, text)


def find_generated_packages(packages_dir: Path) -> list[dict]:
    """Every package declaring a `generated: true` source, with its tarball name."""
    found = []
    for recipe in sorted(packages_dir.glob("*/*/package.yml")):
        text = recipe.read_text(encoding="utf-8")
        # Cheap substring pre-filter before the YAML parse. A recipe that does
        # not contain the word "generated" anywhere cannot declare
        # `generated: true`, so parsing it would only cost time — and it costs
        # a lot: parsing all 1138 recipes takes about a second, while reading
        # them as text takes about ten milliseconds, and 63 contain the word at
        # all. The filter is on the bare word rather than the full `generated:
        # true` so that odd spacing, a quoted value, or a commented-out
        # declaration still reaches the real parser and is judged there.
        if "generated" not in text:
            continue
        try:
            data = yaml.safe_load(text) or {}
        except yaml.YAMLError as e:
            raise Undeterminable(f"{recipe}: unreadable YAML: {e}") from e

        sources = data.get("source") or []
        if isinstance(sources, dict):
            sources = [sources]
        generated = [s for s in sources
                     if isinstance(s, dict) and s.get("generated") is True]
        if not generated:
            continue

        name = str(data.get("name") or recipe.parent.name)
        version = str(data.get("version") or "")

        # The release-time-staged declaration. Strict like the recipe parser's
        # security booleans: a value that is not a non-empty string is a
        # declaration error, never a soft "close enough to true". The string
        # itself is quoted back in the gate's output, which is why an empty one
        # is refused — a class that changes a verdict has to say why it applies.
        declaration = data.get("release_staged_source")
        if declaration is not None:
            if not isinstance(declaration, str) or not declaration.strip():
                raise Undeterminable(
                    f"{recipe}: release_staged_source must be a non-empty "
                    f"string naming the input that is staged at release time "
                    f"and why the generator produces no tarball without it "
                    f"(got {type(declaration).__name__} {declaration!r})")
            declaration = declaration.strip()

        if len(generated) > 1:
            # One generated tarball per package is the whole shape of the
            # extraction step; more than one has no defined extract layout.
            raise Undeterminable(
                f"{recipe}: {len(generated)} generated sources declared; this "
                f"gate models one generated tarball per package"
            )

        url = str(generated[0].get("url") or "")
        if not url:
            raise Undeterminable(f"{recipe}: generated source has no url")
        tarball = _resolve_variables(url, name, version).rsplit("/", 1)[-1]

        found.append({
            "name": name,
            "version": version,
            "recipe": recipe,
            "build_sh": recipe.parent / "build.sh",
            "install_func": str(data.get("install_func") or "do_install"),
            "tarball": tarball,
            "release_staged_source": declaration,
        })
    return found


# ---------------------------------------------------------------------------
# tarball reading
# ---------------------------------------------------------------------------

def tarball_members(path: Path) -> set[str]:
    """Member paths as the recipe sees them after --strip-components=1.

    tar drops the FIRST path component of each stored name. A member stored as
    `./Bibata-Modern-Classic/cursors/x` has components ('.', 'Bibata-...',
    'cursors', 'x') and lands at `Bibata-Modern-Classic/cursors/x`; one stored
    as `iw-pkg/foo` lands at `foo`. The leading `.` is a real component to tar,
    so it is dropped the same way and never special-cased here.
    """
    seen: set[str] = set()
    try:
        with tarfile.open(path, "r:*") as tf:
            for member in tf.getmembers():
                parts = member.name.split("/")
                if len(parts) <= 1:
                    # The stripped-away top-level entry itself; contributes no
                    # visible path.
                    continue
                stripped = "/".join(parts[1:]).rstrip("/")
                if stripped:
                    seen.add(stripped)
    except (tarfile.TarError, OSError) as e:
        raise Undeterminable(f"cannot read tarball {path.name}: {e}") from e
    return seen


# ---------------------------------------------------------------------------
# install-function parsing
# ---------------------------------------------------------------------------

def _strip_heredocs(lines: list[str]) -> list[str]:
    """Drop heredoc payloads. Their content is generated, never consumed."""
    out: list[str] = []
    terminator: str | None = None
    strip_tabs = False
    for line in lines:
        if terminator is not None:
            candidate = line.strip() if strip_tabs else line.rstrip("\n")
            if candidate.strip() == terminator:
                terminator = None
            continue
        match = re.search(r"<<(-?)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2", line)
        if match:
            strip_tabs = match.group(1) == "-"
            terminator = match.group(3)
            # Keep the command line itself (minus the heredoc operator) so its
            # redirect target is still visible to the parser.
            out.append(line[:match.start()])
            continue
        out.append(line)
    if terminator is not None:
        raise Undeterminable(f"unterminated heredoc (expected {terminator!r})")
    return out


def _extract_function(text: str, func: str) -> list[str]:
    """The body lines of `func()`, by brace depth."""
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if re.match(rf"^\s*{re.escape(func)}\s*\(\s*\)\s*\{{", line):
            start = i
            break
    if start is None:
        raise Undeterminable(f"no {func}() found in recipe")

    depth = 0
    body: list[str] = []
    for line in lines[start:]:
        # Brace counting ignores braces inside ${...}, which are parameter
        # expansions rather than block delimiters.
        bare = re.sub(r"\$\{[^}]*\}", "", line)
        opened = bare.count("{")
        closed = bare.count("}")
        if depth > 0 or opened:
            body.append(line)
        depth += opened - closed
        if depth <= 0 and body:
            break
    if depth > 0:
        raise Undeterminable(f"{func}() body is not brace-balanced")
    return body[1:-1] if len(body) >= 2 else []


def _join_continuations(lines: list[str]) -> list[str]:
    out: list[str] = []
    buffer = ""
    for line in lines:
        stripped = line.rstrip()
        if stripped.endswith("\\"):
            buffer += stripped[:-1] + " "
            continue
        out.append(buffer + stripped)
        buffer = ""
    if buffer:
        out.append(buffer)
    return out


def _strip_comment(line: str) -> str:
    """Remove a trailing comment, respecting quotes."""
    out = []
    quote: str | None = None
    prev = ""
    for ch in line:
        if quote:
            out.append(ch)
            if ch == quote and prev != "\\":
                quote = None
        elif ch in ("'", '"'):
            quote = ch
            out.append(ch)
        elif ch == "#" and (not out or out[-1].isspace()):
            break
        else:
            out.append(ch)
        prev = ch
    return "".join(out)


def _split_commands(line: str) -> list[str]:
    """Split a line on `;`, `&&` and `||`, ignoring separators inside quotes.

    A naive regex split breaks on the `;` inside a quoted diagnostic such as
    `echo "... at extract root; tarball layout changed"`, which then fails to
    tokenise and reports a parse failure against a recipe that is perfectly
    well formed.
    """
    segments: list[str] = []
    current: list[str] = []
    quote: str | None = None
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            current.append(ch)
            if ch == quote and (i == 0 or line[i - 1] != "\\"):
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            current.append(ch)
            i += 1
            continue
        if ch == ";":
            segments.append("".join(current))
            current = []
            i += 1
            continue
        if line.startswith("&&", i) or line.startswith("||", i):
            segments.append("".join(current))
            current = []
            i += 2
            continue
        current.append(ch)
        i += 1
    if quote:
        raise Undeterminable(f"unbalanced quote in {line!r}")
    segments.append("".join(current))
    return segments


def _drop_redirects(tokens: list[str]) -> list[str]:
    """Remove redirection operators and their targets."""
    out: list[str] = []
    skip_next = False
    for token in tokens:
        if skip_next:
            skip_next = False
            continue
        if re.fullmatch(r"\d*(>>|>|<|>&|&>)", token):
            skip_next = True
            continue
        if re.match(r"^\d*(>>|>|<)", token) and len(token) > 1:
            # Attached form: >file
            continue
        out.append(token)
    return out


def _is_directory_only_install(tokens: list[str]) -> bool:
    """True for `install -d` / `-dm755` forms, which create and consume nothing.

    Lowercase `d` means "create directories". Uppercase `D` means "create the
    destination's parents" and still takes a source, so the two must not be
    folded together.
    """
    for token in tokens:
        if not token.startswith("-") or token.startswith("--"):
            continue
        letters = re.match(r"^-([A-Za-z]*)", token)
        if letters and "d" in letters.group(1):
            return True
    return False


def _expand(token: str, symbols: dict[str, list[str]]) -> list[str]:
    """Expand ${VAR}, $VAR and ${VAR:-default} against known assignments.

    `${VAR:-default}` is resolved to its default when VAR is not a local
    assignment, because the environment cannot be read at parse time and the
    default is what the recipe guarantees. Recipes use that form to locate the
    recipe directory (`${IGOS_PACKAGE_DIR:-/mnt/intergenos/packages/...}`),
    whose default is absolute and therefore outside the extracted tree.
    """
    results = [token]
    for _ in range(8):  # bounded: assignments can reference other assignments
        next_results: list[str] = []
        changed = False
        for value in results:
            match = re.search(r"\$\{(\w+):[-=]([^}]*)\}|\$\{(\w+)\}|\$(\w+)",
                              value)
            if not match:
                next_results.append(value)
                continue
            if match.group(1) is not None:
                key, default = match.group(1), match.group(2)
                candidates = symbols.get(key) or [default]
            else:
                key = match.group(3) or match.group(4)
                if key not in symbols:
                    next_results.append(value)
                    continue
                candidates = symbols[key]
            changed = True
            for candidate in candidates:
                next_results.append(
                    value[:match.start()] + candidate + value[match.end():])
        results = next_results
        if not changed:
            break
    return results


def consumed_paths(build_sh: Path, install_func: str) -> tuple[set[str], set[str], bool]:
    """Source-tree paths the install function consumes.

    Returns (required, optional, consumes_whole_tree). Raises Undeterminable
    when any part of the function is outside the understood subset.
    """
    if not build_sh.is_file():
        raise Undeterminable(f"no build.sh at {build_sh}")

    body = _extract_function(build_sh.read_text(encoding="utf-8"), install_func)
    body = _strip_heredocs(body)
    body = _join_continuations(body)

    symbols: dict[str, list[str]] = {}
    required: set[str] = set()
    optional: set[str] = set()
    whole_tree = False

    # Guard stack: each entry is the set of paths an enclosing `if` proved
    # present. A copy guarded by `[ -d previews ]` is optional — the recipe
    # already handles its absence, so its absence from the tarball is not a
    # broken build.
    guard_stack: list[set[str]] = []
    guarded_now: set[str] = set()

    for raw in body:
        line = _strip_comment(raw).strip()
        if not line:
            continue

        # Split on separators the parser treats as command boundaries. `&&`
        # and `||` chain commands; `;` ends them.
        for segment in _split_commands(line):
            segment = segment.strip()
            if not segment:
                continue

            if segment.startswith("if ") or segment.startswith("elif "):
                guard_stack.append(set(guarded_now))
                guarded_now = guarded_now | _positive_existence_tests(segment)
                continue
            if segment == "fi":
                guarded_now = guard_stack.pop() if guard_stack else set()
                continue
            if segment in ("then", "else", "do", "done"):
                continue

            try:
                tokens = shlex.split(segment, posix=True)
            except ValueError as e:
                raise Undeterminable(f"unparseable line {segment!r}: {e}") from e
            if not tokens:
                continue
            tokens = _drop_redirects(tokens)
            if not tokens:
                continue

            # `for VAR in a b c` — record the values the body may use. The list
            # may contain globs (`"$dir"/*.patch`); those are carried through
            # as-is, because a glob is checked against the member set rather
            # than resolved here.
            if tokens[0] == "for" and len(tokens) >= 3 and tokens[2] == "in":
                values: list[str] = []
                for token in tokens[3:]:
                    if token in ("do", ";"):
                        continue
                    for candidate in _expand(token, symbols):
                        if re.search(r"\$\{?\w", candidate):
                            raise Undeterminable(
                                f"for-loop list has an unresolvable value "
                                f"{candidate!r}: {segment!r}")
                        values.append(candidate)
                symbols[tokens[1]] = values
                continue

            # Assignments, with or without `local`.
            assign = tokens[1:] if tokens[0] == "local" else tokens
            if assign and re.fullmatch(r"\w+=.*", assign[0]):
                key, _, value = assign[0].partition("=")
                symbols[key] = _expand(value, symbols)
                continue
            if tokens[0] == "local":
                # `local x` with no value — declares nothing this gate needs.
                continue

            command = tokens[0]

            if command in ("source", "."):
                raise Undeterminable(
                    f"{install_func} sources a helper ({segment!r}); this gate "
                    f"does not follow sourced files, so the consumed set is "
                    f"unknown")

            if command in NON_CONSUMING:
                continue

            if command == "patch":
                # `patch -i FILE` reads FILE and rewrites the extracted tree.
                # The file itself is a consumed path like any other; recipes
                # take it from the recipe directory (absolute, out of scope),
                # but one taken from the tarball must be a member.
                sources = []
                for i, token in enumerate(tokens):
                    if token in ("-i", "--input") and i + 1 < len(tokens):
                        sources.append(tokens[i + 1])
                    elif token.startswith("--input="):
                        sources.append(token.split("=", 1)[1])
                operands = sources + ["(no destination)"]
            elif command not in CONSUMING:
                raise Undeterminable(
                    f"unrecognised command {command!r} in {install_func} "
                    f"(line: {segment!r})")
            else:
                operands = [t for t in tokens[1:] if not t.startswith("-")]
                if command == "install" and _is_directory_only_install(tokens):
                    continue
                if len(operands) < 2:
                    raise Undeterminable(
                        f"{command} with fewer than two operands: {segment!r}")

            for operand in operands[:-1]:
                for candidate in _expand(operand, symbols):
                    if "${" in candidate or re.search(r"\$\w", candidate):
                        raise Undeterminable(
                            f"cannot resolve {candidate!r} in {segment!r}")
                    if "DESTDIR" in operand:
                        continue
                    if candidate.startswith("/"):
                        # An absolute path is read from the build host or the
                        # chroot, not from the extracted tarball. Out of scope
                        # by construction.
                        continue
                    normal = candidate.rstrip("/")
                    if candidate in WHOLE_TREE or normal in ("", ".", "./."):
                        whole_tree = True
                        continue
                    normal = re.sub(r"^\./", "", normal)
                    if normal.endswith("/."):
                        normal = normal[:-2]
                    if not normal or normal == ".":
                        whole_tree = True
                        continue
                    if _is_guarded(normal, guarded_now):
                        optional.add(normal)
                    else:
                        required.add(normal)

    return required, optional, whole_tree


def _positive_existence_tests(segment: str) -> set[str]:
    """Paths an `if` condition proves present (`[ -d x ]`, `[ -f x ]`).

    A negated test (`[ ! -d x ]`) proves nothing about the happy path — those
    guards exist to abort the build — so they are deliberately not collected.
    """
    if re.search(r"\[\s*!", segment):
        return set()
    found = set()
    for match in re.finditer(r"-[def]\s+(\S+)", segment):
        value = match.group(1).strip('"\'')
        value = re.sub(r"^\./", "", value).rstrip("/")
        if value and "$" not in value:
            found.add(value)
    return found


def _is_guarded(path: str, guards: set[str]) -> bool:
    """True when `path`, or a directory containing it, was proved present."""
    for guard in guards:
        if path == guard or path.startswith(guard.rstrip("/") + "/"):
            return True
    return False


def _member_present(path: str, members: set[str]) -> bool:
    """A consumed path is present if it is a member, a directory holding
    members, or — when it carries a glob — matched by at least one member."""
    if path in members:
        return True
    prefix = path.rstrip("/") + "/"
    if any(m.startswith(prefix) for m in members):
        return True
    if any(ch in path for ch in "*?["):
        import fnmatch
        return any(fnmatch.fnmatch(m, path) for m in members)
    return False


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def check_package(pkg: dict, sources_dir: Path) -> dict:
    """One package's verdict. Never raises; Undeterminable becomes a verdict."""
    result = {"name": pkg["name"], "tarball": pkg["tarball"],
              "missing": [], "undeterminable": None, "checked": 0,
              "release_staged_absent": None, "unverified": 0}
    tarball_path = sources_dir / pkg["tarball"]
    if not tarball_path.is_file():
        declaration = pkg.get("release_staged_source")
        if not declaration:
            result["undeterminable"] = (
                f"generated tarball {pkg['tarball']} is absent from "
                f"{sources_dir} — the generator must run before this gate")
            return result
        # Declared release-staged. The recipe is still parsed here, even though
        # there is no member set to check it against: a recipe this gate cannot
        # read is a halt in every other case and must not become reachable-only-
        # at-release-time by declaring this class. Only the absent INPUT is
        # excused; an unreadable install step is not.
        try:
            required, _optional, _whole_tree = consumed_paths(
                pkg["build_sh"], pkg["install_func"])
        except Undeterminable as e:
            result["undeterminable"] = str(e)
            return result
        result["release_staged_absent"] = declaration
        result["unverified"] = len(required)
        return result
    try:
        members = tarball_members(tarball_path)
        required, optional, whole_tree = consumed_paths(
            pkg["build_sh"], pkg["install_func"])
    except Undeterminable as e:
        result["undeterminable"] = str(e)
        return result

    if whole_tree and not members:
        result["missing"].append(
            "(whole extracted tree) — the recipe copies the tree wholesale and "
            "the tarball has no members")

    for path in sorted(required):
        if not _member_present(path, members):
            result["missing"].append(path)
    result["checked"] = len(required)
    result["optional"] = sorted(optional)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Assert every file a generated package installs from its "
                    "extracted source tree is a member of its generated tarball")
    parser.add_argument("--packages-dir", default=str(REPO_ROOT / "packages"),
                        help="root of the package recipes")
    parser.add_argument("--sources-dir", default=str(REPO_ROOT / "build" / "sources"),
                        help="directory the generators write tarballs into")
    args = parser.parse_args()

    packages_dir = Path(args.packages_dir)
    sources_dir = Path(args.sources_dir)
    if not packages_dir.is_dir():
        print(f"[tarball-membership] SETUP ERROR: no packages dir at "
              f"{packages_dir}", file=sys.stderr)
        return 2
    if not sources_dir.is_dir():
        print(f"[tarball-membership] SETUP ERROR: no sources dir at "
              f"{sources_dir}", file=sys.stderr)
        return 2

    started = time.monotonic()
    try:
        packages = find_generated_packages(packages_dir)
    except Undeterminable as e:
        print(f"[tarball-membership] SETUP ERROR: {e}", file=sys.stderr)
        return 2

    if not packages:
        print("[tarball-membership] SETUP ERROR: no package declares a "
              "generated source; this gate would check nothing", file=sys.stderr)
        return 2

    results = [check_package(p, sources_dir) for p in packages]
    elapsed = time.monotonic() - started

    broken = [r for r in results if r["missing"]]
    unknown = [r for r in results if r["undeterminable"]]
    staged = [r for r in results if r["release_staged_absent"]]
    clean = len(results) - len(broken) - len(unknown) - len(staged)

    def report_staged(stream) -> None:
        """Name every release-staged package that went unverified, and why.

        Printed on the passing path as well as the halting one: a package this
        gate did not check is reported in both, or the softer verdict would be
        the silent skip the gate exists to refuse.
        """
        for r in staged:
            print(f"  {r['name']} — RELEASE-STAGED SOURCE ABSENT: "
                  f"{r['tarball']} is not in {sources_dir}, and its recipe "
                  f"declares: {r['release_staged_absent']}", file=stream)
            print(f"    {r['unverified']} consumed path(s) parsed cleanly and "
                  f"stay UNVERIFIED until a build stages that input.",
                  file=stream)

    if not broken and not unknown:
        total = sum(r["checked"] for r in results)
        if not clean:
            # Every generated package is release-staged and absent, so this run
            # compared nothing against anything. The gate already refuses to
            # print a verdict when no package declares a generated source at
            # all; a run that checked none of them is the same statement.
            print(f"[tarball-membership] SETUP ERROR: all {len(results)} "
                  f"generated package(s) declare a release-staged source and "
                  f"none of their tarballs are present in {sources_dir}, so "
                  f"this gate verified nothing", file=sys.stderr)
            report_staged(sys.stderr)
            return 2
        if staged:
            print(f"[tarball-membership] PASS: {len(results)} generated "
                  f"package(s), {clean} verified against their tarballs "
                  f"({total} consumed path(s)); {len(staged)} NOT VERIFIED — "
                  f"release-staged source absent ({elapsed:.2f}s)")
            report_staged(sys.stdout)
            return 0
        print(f"[tarball-membership] PASS: {len(results)} generated package(s), "
              f"{total} consumed path(s), all present in their tarballs "
              f"({elapsed:.2f}s)")
        return 0

    print(f"[tarball-membership] HALT: {len(broken)} package(s) install files "
          f"their tarball does not carry; {len(unknown)} package(s) could not "
          f"be determined; {len(staged)} not verified (release-staged source "
          f"absent); {clean} clean ({elapsed:.2f}s)", file=sys.stderr)

    for r in broken:
        print(f"  {r['name']} — installs {len(r['missing'])} path(s) absent "
              f"from {r['tarball']}:", file=sys.stderr)
        for path in r["missing"]:
            print(f"    {path}", file=sys.stderr)

    for r in unknown:
        print(f"  {r['name']} — COULD NOT DETERMINE: {r['undeterminable']}",
              file=sys.stderr)

    report_staged(sys.stderr)

    print("", file=sys.stderr)
    if broken:
        print("  A generated tarball is built from in-tree assets by a "
              "generator script; the recipe installs from the extracted tree. "
              "When the generator stops staging a file the recipe still "
              "installs, the package cannot build at all — and it cannot be "
              "caught anywhere else, because the two files are only wrong "
              "together. Stage the named path in the generator, or stop "
              "installing it in the recipe.", file=sys.stderr)
    if unknown:
        print("  A package this gate cannot parse is a FAILURE, not a skip: an "
              "unknown consumed set means the build would read as covered "
              "while nothing was checked. Either simplify the install step, or "
              "widen this gate's understood subset deliberately, with a test.",
              file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
