#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Generate intergen/data/capability-surface.json by INTROSPECTING the real tools.

The M4 capability gate refuses to deliver a reply that invokes a shipped
first-party tool with a subcommand or flag the tool does not have. Its whole
value rests on the ground truth it checks against being the tool's REAL
interface, so that ground truth is DERIVED here by walking the live parsers and
the live dispatcher — never transcribed by hand.

The artifact's own ``_meta`` has named a generator since it first shipped, but no
generator was in the tree, so the file could only be refreshed by hand. Measured
2026-08-26 against the parser it claims to come from: the shipped artifact was
missing three real pkm subcommands (``vacuum``, ``hook-baseline``,
``record-hook-changes``) and four real global flags (``--root``, ``--wait``,
``--no-wait``, ``--wait-timeout``). A gate reading that artifact calls a REAL
command a fabrication. This script is the fix for the class: run it and the
artifact is the parser.

Sources, one per shipped tool:

  pkm                        ``pkm.cli.build_parser()``            (argparse)
  forge                      ``installer.__main__.build_parser()`` (argparse)
  igos-game-window-density   its module's ``build_parser()``       (argparse)
  intergen                   the dispatch chain in ``intergen/cli.py:main()``
                             (read from the source with ast — the dispatcher IS
                             the interface; there is no parser to ask)
  igos-install-*             the ``verify_paths`` entries of the package recipes
                             that ship them. Shell scripts: the NAME is derived,
                             the argument surface is NOT introspectable, and the
                             artifact says so rather than asserting one.

REFUSES rather than writes a thinner artifact: every extractor below has a floor,
and a floor that is not met exits non-zero with the reason named. A generator
that quietly emitted an empty surface would hand the gate a ground truth that
calls every real command a fabrication, which is the failure this file exists to
make impossible.

Usage:  python3 scripts/gen-capability-surface.py [--check]
        --check  compare against the shipped artifact and exit 1 on any
                 difference, writing nothing (for a gate or a test).
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ARTIFACT = REPO / "intergen" / "data" / "capability-surface.json"


# ── argparse walking ────────────────────────────────────────────────────────

def _flags_of(parser) -> list[dict]:
    """Every option this parser accepts, as {names, choices?}.

    ``-h/--help`` is included: it is a real accepted flag, and leaving it out
    made the gate treat ``pkm install --help`` as an invented flag."""
    out: list[dict] = []
    for action in parser._actions:
        if not action.option_strings:
            continue
        if isinstance(action, argparse._SubParsersAction):
            continue
        spec: dict = {"names": list(action.option_strings)}
        # Whether the flag consumes the token after it. The gate needs this to
        # find the SUBCOMMAND: without it, the value in `pkm --db /var/lib/pkm.db
        # list` reads as the subcommand and a correct command is called invented.
        spec["takes_value"] = action.nargs != 0
        if action.choices:
            spec["choices"] = [str(c) for c in action.choices]
        out.append(spec)
    return out


def _positionals_of(parser) -> list[dict]:
    out: list[dict] = []
    for action in parser._actions:
        if action.option_strings or isinstance(action, argparse._SubParsersAction):
            continue
        out.append({"name": action.dest,
                    "nargs": action.nargs if isinstance(action.nargs, str) else None})
    return out


def _subcommands_of(parser) -> dict[str, dict]:
    """{primary_name: {aliases, positionals, flags, subcommands, help}}.

    argparse records an alias as a separate key in ``choices`` pointing at the
    SAME sub-parser object, so identity is what separates a primary name from
    its aliases. The first name registered is taken as primary, matching the
    order the parser declares them in.

    Nested sub-parsers (``pkm cache clean``) are walked too: a second level that
    the surface did not record would make ``pkm cache clean`` unverifiable."""
    subs: dict[str, dict] = {}
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        by_parser: dict[int, list[str]] = {}
        for name, sub in action.choices.items():
            by_parser.setdefault(id(sub), []).append(name)
        for name, sub in action.choices.items():
            names = by_parser[id(sub)]
            if name != names[0]:
                continue  # an alias; recorded under its primary
            nested = _subcommands_of(sub)
            spec = {
                "aliases": names[1:],
                "positionals": _positionals_of(sub),
                "flags": _flags_of(sub),
                "help": (sub.description or "") if sub.description else "",
            }
            if nested:
                spec["subcommands"] = nested
            subs[name] = spec
    return subs


def _load_module_from_path(path: Path, modname: str):
    spec = importlib.util.spec_from_file_location(modname, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    return mod


# ── per-tool extractors ─────────────────────────────────────────────────────

def surface_pkm() -> dict:
    from pkm.cli import build_parser
    parser = build_parser()
    subs = _subcommands_of(parser)
    if len(subs) < 20:
        raise SystemExit(f"REFUSING: pkm parser yielded only {len(subs)} "
                         "subcommands; the real parser has far more. The walk "
                         "is broken — fix it rather than ship a thin surface.")
    return {
        "version": getattr(__import__("pkm"), "__version__", "unknown"),
        "source": "pkm.cli.build_parser()",
        "introspected": True,
        "requires_subcommand": True,
        "global_flags": _flags_of(parser),
        "positionals": _positionals_of(parser),
        "subcommands": subs,
    }


def surface_forge() -> dict:
    from installer.__main__ import build_parser
    parser = build_parser()
    flags = _flags_of(parser)
    if len(flags) < 3:
        raise SystemExit(f"REFUSING: forge parser yielded only {len(flags)} "
                         "flags; the walk is broken.")
    return {
        "source": "installer.__main__.build_parser()",
        "introspected": True,
        "requires_subcommand": False,
        "global_flags": flags,
        # forge takes NO positional arguments, and recording that is what lets
        # the gate see `forge install` — an invented subcommand for a tool that
        # has none — instead of shrugging it off as an argument.
        "positionals": _positionals_of(parser),
        "subcommands": {},
    }


def surface_game_window_density() -> dict:
    path = REPO / "packages" / "extra" / "steam" / "assets" / "igos-game-window-density.py"
    mod = _load_module_from_path(path, "_igos_gwd_surface")
    parser = mod.build_parser()
    subs = _subcommands_of(parser)
    if not subs:
        raise SystemExit("REFUSING: igos-game-window-density yielded no "
                         "subcommands; the walk is broken.")
    return {
        "source": f"{path.relative_to(REPO)}:build_parser()",
        "introspected": True,
        "requires_subcommand": True,
        "global_flags": _flags_of(parser),
        "positionals": _positionals_of(parser),
        "subcommands": subs,
    }


def _flag_constants(node: ast.AST) -> tuple[list[str], set[str]]:
    """Every option-looking string literal this code TESTS an argument against.

    ``intergen``'s handlers read their own options straight out of the argument
    list — ``if "--raw" in args:``, ``if arg.startswith("--tier="):`` — so the
    literals in those tests are the flags the command really accepts."""
    found: list[str] = []
    valued: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Compare) and len(sub.ops) == 1 \
                and isinstance(sub.ops[0], ast.In) \
                and isinstance(sub.left, ast.Constant) \
                and isinstance(sub.left.value, str) \
                and sub.left.value.startswith("-"):
            found.append(sub.left.value)
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) \
                and sub.func.attr in ("startswith", "index") and sub.args \
                and isinstance(sub.args[0], ast.Constant) \
                and isinstance(sub.args[0].value, str) \
                and sub.args[0].value.startswith("-"):
            found.append(sub.args[0].value.rstrip("="))
            if sub.func.attr == "index":
                # `i = args.index("--tail"); args[i + 1]` — it takes a value.
                valued.add(sub.args[0].value)
        if isinstance(sub, ast.Compare) and isinstance(sub.left, ast.Name) \
                and len(sub.ops) == 1 and isinstance(sub.ops[0], ast.Eq) \
                and isinstance(sub.comparators[0], ast.Constant) \
                and isinstance(sub.comparators[0].value, str) \
                and sub.comparators[0].value.startswith("-"):
            found.append(sub.comparators[0].value)
    return found, valued


def _intergen_commands_from_source() -> tuple[dict[str, list[dict]], list[dict]]:
    """({command: its flags}, global flags) read out of intergen/cli.py.

    ``intergen`` has no argparse parser: ``main()`` lowercases ``sys.argv[1]``
    and walks an if/elif chain of equality and membership tests, handing the
    rest of the line to a ``cmd_*`` handler that reads its own options. That
    chain plus those handlers ARE the interface, so both are what get read —
    with ast, from the real source, so a command or flag added to the CLI cannot
    go missing from the surface without this extractor also going quiet (which
    the floors below turn into a refusal).

    Reading only ``main()`` was not enough and the miss was measured: every
    option of ``intergen last``, ``intergen glass`` and ``intergen tool-log``
    lives in its handler, so a surface built from ``main()`` alone would have
    called the real ``intergen glass --json`` an invented flag."""
    tree = ast.parse((REPO / "intergen" / "cli.py").read_text())
    main = next((n for n in tree.body
                 if isinstance(n, ast.FunctionDef) and n.name == "main"), None)
    if main is None:
        raise SystemExit("REFUSING: intergen/cli.py has no main() to read.")
    handlers = {n.name: n for n in tree.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

    commands: dict[str, list[str]] = {}
    global_flags: list[str] = []

    def branch_commands(test: ast.AST) -> list[str]:
        out: list[str] = []
        for node in ast.walk(test):
            if not (isinstance(node, ast.Compare) and isinstance(node.left, ast.Name)
                    and node.left.id == "command"):
                continue
            for op, comp in zip(node.ops, node.comparators):
                if isinstance(op, ast.Eq) and isinstance(comp, ast.Constant) \
                        and isinstance(comp.value, str):
                    out.append(comp.value)
                elif isinstance(op, ast.In) and isinstance(comp, (ast.Tuple, ast.List)):
                    for elt in comp.elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            out.append(elt.value)
        return out

    def walk_chain(stmts: list[ast.stmt]) -> None:
        for stmt in stmts:
            if not isinstance(stmt, ast.If):
                continue
            names = branch_commands(stmt.test)
            if names:
                # Flags tested inside the branch itself, plus every flag the
                # handler this branch calls tests for.
                flags, valued = _flag_constants(
                    ast.Module(body=stmt.body, type_ignores=[]))
                for node in ast.walk(ast.Module(body=stmt.body, type_ignores=[])):
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                            and node.func.id in handlers:
                        more, more_valued = _flag_constants(handlers[node.func.id])
                        flags += more
                        valued |= more_valued
                for name in names:
                    prev = {f["names"][0]: f for f in commands.get(name, [])}
                    for flag in sorted(set(flags)):
                        prev[flag] = {"names": [flag],
                                      "takes_value": flag in valued}
                    commands[name] = [prev[k] for k in sorted(prev)]
            walk_chain(stmt.orelse)

    walk_chain(main.body)
    global_names, global_valued = _flag_constants(ast.Module(
        body=[s for s in main.body if not isinstance(s, ast.If)], type_ignores=[]))
    global_flags = [{"names": [f], "takes_value": f in global_valued}
                    for f in sorted(set(global_names))]

    if len(commands) < 10:
        raise SystemExit(
            f"REFUSING: read only {len(commands)} commands out of "
            "intergen/cli.py:main(). The dispatcher's shape changed and this "
            "extractor no longer sees it — fix the extractor. Shipping the "
            "short list would make the gate call real commands fabrications.")
    return commands, global_flags


def surface_intergen() -> dict:
    commands, global_flags = _intergen_commands_from_source()
    # The help forms are dispatched as commands but read as flags by anyone
    # typing them; both are accepted, so both are recorded where they belong.
    helpish = {"--help", "-h"}
    subs = {name: {"aliases": [], "positionals": [], "flags": flags, "help": ""}
            for name, flags in sorted(commands.items()) if name not in helpish}
    if not any(sub["flags"] for sub in subs.values()):
        raise SystemExit(
            "REFUSING: no intergen subcommand carries a flag. The handlers "
            "declare several (`last --raw`, `glass --json`); reading none means "
            "the extractor is blind and the gate would call them fabrications.")
    return {
        "source": "intergen/cli.py:main() dispatch chain + its cmd_* handlers "
                  "(read with ast)",
        "introspected": True,
        "requires_subcommand": True,
        "global_flags": global_flags + [{"names": [f], "takes_value": False}
                                        for f in sorted(helpish)],
        # intergen's first argument is always its command, never a positional.
        "positionals": [],
        "subcommands": subs,
    }


def surface_igos_installers() -> dict[str, dict]:
    """The shipped ``igos-*`` commands, from the recipes' verify_paths.

    A recipe's ``verify_paths`` is the list of files the package must have
    installed for the install to be accepted, so it is the authority on which
    ``igos-*`` commands exist on a built machine.

    These are shell scripts. Their NAME is derived and checkable; their argument
    surface is not introspectable, and the artifact records
    ``introspected: false`` instead of asserting one. The gate reads that as
    "this tool's arguments cannot be checked" — an honest unverifiable, never a
    silent pass and never a false accusation."""
    out: dict[str, dict] = {}
    for recipe in sorted((REPO / "packages").glob("*/*/package.yml")):
        for line in recipe.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line.startswith("- /usr/bin/igos-"):
                continue
            name = line.split("- ", 1)[1].strip().rsplit("/", 1)[-1]
            if not name or name.endswith("*"):
                continue
            out[name] = {
                "source": f"{recipe.relative_to(REPO)}:verify_paths",
                "introspected": False,
                "requires_subcommand": False,
                "global_flags": [],
                # Not recorded as "none": this tool is not introspected, so what
                # it accepts is unknown, and an empty list would read as proof
                # that it accepts nothing.
                "positionals": None,
                "subcommands": {},
            }
    if len(out) < 10:
        raise SystemExit(f"REFUSING: found only {len(out)} igos-* commands in "
                         "the recipes; the scan is broken.")
    return out


# ── artifact assembly ───────────────────────────────────────────────────────

def build_surface() -> dict:
    sys.path.insert(0, str(REPO))
    pkm = surface_pkm()
    tools: dict[str, dict] = {
        "forge": surface_forge(),
        "intergen": surface_intergen(),
    }
    igos = surface_igos_installers()
    # The one igos-* command with a real parser is introspected like any other,
    # overriding the name-only entry the recipe scan produced for it.
    gwd = surface_game_window_density()
    for name in list(igos):
        if name == "igos-game-window-density":
            igos[name] = gwd
    tools.update(igos)

    # The intergen TOOL registry block (a different surface: the assistant's own
    # tools, not a command line) is CARRIED FORWARD from the shipped artifact,
    # not re-derived here. This generator covers the command-line surfaces the
    # M4 command gate reads; reproducing the tool block's exact shape was not
    # proven against its original generator, so it is preserved rather than
    # rewritten from a guess.
    previous = json.loads(ARTIFACT.read_text()) if ARTIFACT.exists() else {}
    intergen_tools = previous.get("intergen_tools", {})
    if not intergen_tools.get("tools"):
        raise SystemExit("REFUSING: the shipped artifact has no intergen_tools "
                         "block to carry forward, and this generator does not "
                         "derive one. Restore the artifact first.")

    return {
        "_meta": {
            "purpose": "M4 capability ground-truth surface — the REAL parser / "
                       "dispatcher surface a capability claim is checked "
                       "against. A reply that invokes a first-party tool with a "
                       "subcommand or flag absent from this file is a "
                       "fabrication and is not delivered as written.",
            "generated_by": "scripts/gen-capability-surface.py",
            "regeneration": "python3 scripts/gen-capability-surface.py "
                            "(introspection only; no hand-transcription). "
                            "--check compares without writing.",
            "carried_forward": ["intergen_tools"],
            "note": "pkm holds the package manager's surface (unchanged shape, "
                    "kept for its existing readers). tools holds every other "
                    "shipped first-party command. A tool with "
                    "introspected: false has a derivable NAME but no derivable "
                    "argument surface — arguments passed to it are UNVERIFIABLE, "
                    "which is reported honestly rather than guessed either way.",
        },
        "pkm": pkm,
        "tools": tools,
        "intergen_tools": intergen_tools,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="compare against the shipped artifact; write nothing")
    args = ap.parse_args()

    surface = build_surface()
    rendered = json.dumps(surface, indent=2, sort_keys=False) + "\n"

    if args.check:
        if not ARTIFACT.exists():
            print(f"MISSING: {ARTIFACT}", file=sys.stderr)
            return 1
        if ARTIFACT.read_text() != rendered:
            print(f"STALE: {ARTIFACT} does not match the live tool surfaces. "
                  "Run: python3 scripts/gen-capability-surface.py",
                  file=sys.stderr)
            return 1
        print(f"OK: {ARTIFACT} matches the live tool surfaces.")
        return 0

    ARTIFACT.write_text(rendered)
    n_tools = 1 + len(surface["tools"])
    print(f"WROTE {ARTIFACT} — {n_tools} first-party commands "
          f"({len(surface['pkm']['subcommands'])} pkm subcommands).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
