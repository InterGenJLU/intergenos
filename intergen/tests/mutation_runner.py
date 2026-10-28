# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Run a trusted test command against independent Python mutants in scratch.

Usage: python intergen/tests/mutation_runner.py --root fixture --file subject.py
       --mutations mutations.json -- python check.py

Worked example: subject.py contains ``def answer(): return 42`` and check.py
contains ``from subject import answer; assert answer() == 42``. mutations.json
is ``[{"id":"empty-answer", "kind":"return_none", "function":"answer"}]``.
The baseline passes, that mutant is KILLED, and the final verdict is PASS.
Adding an untested function and a mutation for it produces SURVIVED and FAIL.

Mutation kinds (line numbers are one-based in the original file):
* regex_alternative: line of a string literal, literal = the exact alternative
  text to remove. It must occur in exactly one alternation on that line. Pipes
  inside groups, escaped pipes and character classes are distinguished.
* invert_condition: line of an if, while, conditional expression or assert;
  wrap its condition in ``not (...)``. Ambiguous lines are refused.
* return_none: function = its qualified name, e.g. ``Widget.answer``; replace
  the complete body with ``return None``. A no-op replacement is refused.

The root is snapshotted once, excluding .git, caches and .pyc files. The
baseline and each mutant run in a new copy of that snapshot. Internal symlinks
are rebased; external or broken links are refused. Absolute command arguments
under root are rebased, and PYTHONPATH points at the copy. Commands use argv,
never a shell, and stdin is closed. This is copy isolation, not an OS sandbox:
use trusted tests that access project files relative to their cwd or __file__.
An absolute path embedded inside code or an environment variable is not rebased.

Only exit 1 from a mutant's test command counts as KILLED, after a passing
baseline. Exit 0 is SURVIVED; timeouts, signals, other exits and setup errors are
ERROR. The command must follow this exit-code contract (ordinary pytest and
unittest test failures do). Syntactically invalid or unapplied mutations are
errors, never evidence of test sensitivity. A nonempty list with every mutant
killed is the only green verdict. CLI exits: PASS=0, FAIL=1, ERROR=2. Full command
output is JSON-escaped on OUTPUT lines; the final VERDICT line is unambiguous.
"""

from __future__ import annotations

import argparse
import ast
import difflib
import io
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import tokenize


def _validate(mutation):
    fields = {"regex_alternative": {"line", "literal"},
              "invert_condition": {"line"}, "return_none": {"function"}}
    if (not isinstance(mutation, dict) or not isinstance(mutation.get("kind"), str)
            or mutation["kind"] not in fields):
        raise ValueError("unknown mutation kind")
    kind = mutation["kind"]
    if set(mutation) != {"id", "kind"} | fields[kind]:
        raise ValueError(f"{kind} requires exactly {sorted(fields[kind])}")
    if not isinstance(mutation["id"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", mutation["id"]):
        raise ValueError("mutation id must be a nonempty identifier")
    if "line" in mutation and (type(mutation["line"]) is not int or mutation["line"] < 1):
        raise ValueError("line must be a positive integer")
    for field in ("literal", "function"):
        if field in mutation and (not isinstance(mutation[field], str) or not mutation[field]):
            raise ValueError(f"{field} must be a nonempty string")


def _regex_deletions(pattern, literal):
    """Find exact branches; return source intervals that include one separator."""
    re.compile(pattern)
    # Each stack entry is [current branch start, completed branch intervals].
    stack = [[0, []]]
    groups = []
    i = 0
    in_class = False
    while i < len(pattern):
        char = pattern[i]
        if char == "\\":
            i += 2
            continue
        if in_class:
            if char == "]":
                in_class = False
        elif char == "[":
            in_class = True
            # A leading ] (also after ^) is a literal member of the class.
            j = i + 1 + (pattern[i+1:i+2] == "^")
            if pattern[j:j+1] == "]":
                i = j
        elif char == "(":
            if pattern.startswith("(?#", i):
                end = pattern.find(")", i + 3)
                i = end + 1
                continue
            header = re.match(r"\(\?(?:P<[^>]+>|[:=!]|<[=!]|[aiLmsux-]+:)", pattern[i:])
            start = i + (len(header.group()) if header else 1)
            stack.append([start, []])
            i = start
            continue
        elif char == "|":
            stack[-1][1].append((stack[-1][0], i))
            stack[-1][0] = i + 1
        elif char == ")":
            if len(stack) == 1:
                raise ValueError("unsupported regex grouping")
            start, branches = stack.pop()
            groups.append(branches + [(start, i)])
        i += 1
    if len(stack) != 1 or in_class:
        raise ValueError("unsupported regex grouping")
    start, branches = stack.pop()
    groups.append(branches + [(start, len(pattern))])
    edits = []
    for branches in groups:
        if len(branches) < 2:
            continue
        for position, (start, end) in enumerate(branches):
            if pattern[start:end] == literal:
                edits.append((start - 1, end) if position else (start, end + 1))
    return edits


def _span(source, node):
    lines = source.encode("utf-8").splitlines(keepends=True)
    return (sum(map(len, lines[:node.lineno-1])) + node.col_offset,
            sum(map(len, lines[:node.end_lineno-1])) + node.end_col_offset)


def _functions(node, prefix=""):
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            name = prefix + child.name
            if not isinstance(child, ast.ClassDef):
                yield name, child
            yield from _functions(child, name + ".")
        else:
            yield from _functions(child, prefix)


def mutate(source, mutation):
    """Produce one compiling, nonempty source edit against the original text."""
    _validate(mutation)
    tree = ast.parse(source)
    data = source.encode("utf-8")
    edits = []
    kind = mutation["kind"]
    if kind == "regex_alternative":
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.lineno == mutation["line"]:
                for start, end in _regex_deletions(node.value, mutation["literal"]):
                    pattern = node.value[:start] + node.value[end:]
                    re.compile(pattern)
                    edits.append((*_span(source, node), repr(pattern)))
    elif kind == "invert_condition":
        for node in ast.walk(tree):
            if isinstance(node, (ast.If, ast.While, ast.IfExp, ast.Assert)) and node.lineno == mutation["line"]:
                start, end = _span(source, node.test)
                edits.append((start, end, "not (" + data[start:end].decode("utf-8") + ")"))
    else:
        for name, node in _functions(tree):
            if name == mutation["function"]:
                start = _span(source, node.body[0])[0]
                end = _span(source, node.body[-1])[1]
                edits.append((start, end, "return None"))
    if len(edits) != 1:
        raise ValueError(f"{mutation['id']}: expected exactly one mutation site, found {len(edits)}")
    start, end, replacement = edits[0]
    mutant = (data[:start] + replacement.encode("utf-8") + data[end:]).decode("utf-8")
    if mutant == source:
        raise ValueError(f"{mutation['id']}: mutation makes no change")
    compile(mutant, "<mutant>", "exec")
    return mutant


def _copy(source, destination):
    shutil.copytree(source, destination, symlinks=True,
                    ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc", ".pytest_cache"))
    for directory, dirs, files in os.walk(destination, followlinks=False):
        for name in dirs + files:
            link = Path(directory) / name
            if not link.is_symlink():
                continue
            original = source / link.relative_to(destination)
            try:
                relative = original.resolve(strict=True).relative_to(source)
            except (OSError, RuntimeError, ValueError) as exc:
                raise ValueError(f"symlink does not resolve within source: {original}") from exc
            target = destination / relative
            if not target.exists():
                raise ValueError(f"symlink points to an excluded path: {original}")
            link.unlink()
            link.symlink_to(os.path.relpath(target, link.parent))


def _command(command, root, scratch):
    rebased = []
    for argument in command:
        prefix, separator, value = argument.partition("=")
        path = Path(value if separator and prefix.startswith("-") else argument)
        if path.is_absolute() and path.is_relative_to(root):
            replacement = str(scratch / path.relative_to(root))
            argument = prefix + "=" + replacement if separator and prefix.startswith("-") else replacement
        rebased.append(argument)
    return rebased


def _execute(command, scratch, timeout):
    result = {"command": command, "cwd": str(scratch), "returncode": None, "output": ""}
    env = dict(os.environ, PYTHONPATH=str(scratch), PYTHONDONTWRITEBYTECODE="1")
    try:
        process = subprocess.Popen(command, cwd=scratch, env=env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   start_new_session=True)
    except OSError as exc:
        result["error"] = str(exc)
        return result
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # The process group belongs to this invocation, including test children.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        output, _ = process.communicate()
        result["error"] = f"timeout after {timeout:g} seconds"
    result.update(returncode=process.returncode, output=output.decode("utf-8", errors="replace"))
    return result


def run_mutations(root, target, mutations, command, *, timeout=60):
    """Return a baseline and mutant report; invalid input raises ValueError."""
    root = Path(root).resolve(strict=True)
    target = Path(target)
    target = (root / target).resolve(strict=True)
    if not root.is_dir() or not target.is_file() or not target.is_relative_to(root):
        raise ValueError("target must be a file within the source root")
    if (not isinstance(command, (tuple, list)) or not command
            or any(not isinstance(arg, str) or not arg or "\0" in arg for arg in command)):
        raise ValueError("command must be a nonempty argv list")
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout) or timeout <= 0):
        raise ValueError("timeout must be a positive finite number")
    if not isinstance(mutations, list) or not mutations:
        raise ValueError("a nonempty mutation list is required")
    for mutation in mutations:
        _validate(mutation)
    if len({m["id"] for m in mutations}) != len(mutations):
        raise ValueError("mutation ids must be unique")
    if Path(tempfile.gettempdir()).resolve().is_relative_to(root):
        raise ValueError("temporary directory must be outside source root")
    relative = target.relative_to(root)
    with tempfile.TemporaryDirectory(prefix="python-mutants-") as temporary:
        work = Path(temporary)
        snapshot = work / "snapshot"
        _copy(root, snapshot)
        original = (snapshot / relative).read_bytes()
        encoding, _ = tokenize.detect_encoding(io.BytesIO(original).readline)
        source = original.decode(encoding)
        mutants = [mutate(source, mutation) for mutation in mutations]
        baseline_dir = work / "baseline"
        _copy(snapshot, baseline_dir)
        baseline = _execute(_command(command, root, baseline_dir), baseline_dir, timeout)
        report = {"baseline": baseline, "mutants": [], "verdict": "ERROR"}
        if baseline["returncode"] != 0 or "error" in baseline:
            return report
        for index, (mutation, mutant) in enumerate(zip(mutations, mutants)):
            scratch = work / f"mutant-{index}"
            _copy(snapshot, scratch)
            (scratch / relative).write_bytes(mutant.encode(encoding))
            result = _execute(_command(command, root, scratch), scratch, timeout)
            status = ("ERROR" if "error" in result or result["returncode"] not in (0, 1)
                      else "KILLED" if result["returncode"] == 1 else "SURVIVED")
            difference = difflib.unified_diff(
                source.splitlines(keepends=True), mutant.splitlines(keepends=True),
                fromfile=f"a/{relative}", tofile=f"b/{relative}")
            result.update(id=mutation["id"], status=status, diff="".join(
                line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
                for line in difference))
            report["mutants"].append(result)
        statuses = {row["status"] for row in report["mutants"]}
        report["verdict"] = "ERROR" if "ERROR" in statuses else "FAIL" if "SURVIVED" in statuses else "PASS"
        return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--file", required=True, type=Path)
    parser.add_argument("--mutations", required=True, type=Path)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    try:
        mutations = json.loads(args.mutations.read_text(encoding="utf-8"))
        report = run_mutations(args.root, args.file, mutations, command, timeout=args.timeout)
    except (OSError, ValueError, SyntaxError, UnicodeError, re.error) as exc:
        print(f"ERROR {exc}")
        print("VERDICT ERROR")
        return 2
    for label, result in [("BASELINE", report["baseline"]), *[(f"{r['status']} {r['id']}", r) for r in report["mutants"]]]:
        print(f"{label} exit={result['returncode']}")
        print(f"COMMAND {shlex.join(result['command'])} (cwd={result['cwd']})")
        if result.get("diff"):
            print(result["diff"], end="" if result["diff"].endswith("\n") else "\n")
        print("OUTPUT " + json.dumps(result["output"]))
        if "error" in result:
            print("ERROR " + json.dumps(result["error"]))
    counts = {status: sum(row["status"] == status for row in report["mutants"])
              for status in ("KILLED", "SURVIVED", "ERROR")}
    print(f"VERDICT {report['verdict']} killed={counts['KILLED']} survived={counts['SURVIVED']} errors={counts['ERROR']}")
    return {"PASS": 0, "FAIL": 1, "ERROR": 2}[report["verdict"]]


if __name__ == "__main__":
    raise SystemExit(main())
