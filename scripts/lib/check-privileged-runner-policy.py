#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""check-privileged-runner-policy.py — one administrator authentication per privileged call.

Shared by scripts/check-d008-compliance.sh (Gate G, the source tree) and
scripts/check-d008-runtime.sh (Gate A, the assembled chroot), so the two gates
cannot disagree about what they check.

THE CHECK. Every <action> in the policy whose org.freedesktop.policykit.exec.path
annotation names the privileged runner must

  * require auth_admin in all three defaults (allow_any, allow_inactive,
    allow_active): an administrator's password for each call, with no keep;
  * carry exactly one org.freedesktop.policykit.exec.argv1 annotation: pkexec
    picks the action from the program and its first argument, and an action
    bound to the runner without one would match any first argument;

and the argv1 values must be distinct and equal, as a set, to the
_PRIVILEGED_TOOLS allowlist read from tool_registry.py, so each privileged tool
has exactly one action and no action names a tool the dispatcher does not run.
At least one action must be bound to the runner: a policy that binds nothing to
it is not a pass.

WHY NO KEEP. pkexec asks PolicyKit about its parent process, and every runner is
started as a transient unit of the person's systemd user manager, so every
runner has the same parent. auth_admin_keep stores a successful authentication
for that parent for five minutes, and any process of the account can ask the
manager to start another runner inside that window.

READ AS POLKIT READS IT. The values judged are the ones PolicyKit enforces, so
the policy is read the way polkit 127 reads it (polkitbackendactionpool.c,
_start, _cdata and _end; polkitimplicitauthorization.c): the same expat parser,
the same element states, a default's text taken exactly (no trimming) and
assigned each time it is met, an annotation's value replaced each time its key
is met, an unknown element skipped with everything inside it, and the file's
reading stopped where PolicyKit stops reading it. PolicyKit keeps the LAST value
of a repeated element, so a file in which any element PolicyKit reads once per
action or per file (the action itself, <defaults>, each <allow_*>, each
<annotate> key, <vendor>, <vendor_url>, <icon_name>, each language of
<description> and <message>) appears twice reads differently to PolicyKit than
to anyone reading its first; such a file is refused, naming the action and the
element. So is a file PolicyKit would stop reading. A comment or a different
action still cannot satisfy the check.

READ AS THE DISPATCHER HOLDS IT. The dispatcher imports _PRIVILEGED_TOOLS, so it
holds the value Python bound last. The allowlist is read from the registry's
syntax tree (nothing is imported) and only from a single unconditional
module-level definition: a reassignment, a definition inside a function or a
branch, or any other binding of the name (an import, a del, a global
declaration, a string naming it, a call such as exec, globals or setattr that
binds names at run time) is refused, naming each line, and so is a registry
that binds frozenset itself.

Usage: check-privileged-runner-policy.py --policy FILE --registry FILE [--runner PATH]
Exit 0 = every action bound to the runner passes; 1 = violations, each named;
2 = an input could not be read or parsed.

       check-privileged-runner-policy.py --exec-paths --policy FILE
prints each action's id and exec.path, tab-separated, as PolicyKit reads them
(Gate F's reading); exit 1 with the violations instead when PolicyKit would
stop reading the file or an element it reads once appears twice.
"""

import argparse
import ast
import sys
from xml.parsers import expat

RUNNER = "/usr/bin/intergen-privileged-runner"
EXEC_PATH = "org.freedesktop.policykit.exec.path"
EXEC_ARGV1 = "org.freedesktop.policykit.exec.argv1"
DEFAULTS = ("allow_any", "allow_inactive", "allow_active")
REQUIRED = "auth_admin"

#: The default values polkit_implicit_authorization_from_string accepts, compared
#: exactly; any other text makes PolicyKit stop reading the file.
IMPLICIT_AUTHORIZATIONS = ("no", "auth_self", "auth_admin", "auth_self_keep",
                           "auth_admin_keep", "yes")

#: The value an action's defaults hold before any <allow_*> is read.
NOT_AUTHORIZED = "no"

#: The parser's depth limit (PARSER_MAX_DEPTH).
MAX_DEPTH = 32

(NONE, UNKNOWN, POLICY_CONFIG, POLICY_VENDOR, POLICY_VENDOR_URL, POLICY_ICON_NAME,
 ACTION, ACTION_DESCRIPTION, ACTION_MESSAGE, ACTION_VENDOR, ACTION_VENDOR_URL,
 ACTION_ICON_NAME, IN_DEFAULTS, ALLOW_ANY, ALLOW_INACTIVE, ALLOW_ACTIVE,
 ANNOTATE) = range(17)

_ALLOW_STATES = {"allow_any": ALLOW_ANY, "allow_inactive": ALLOW_INACTIVE,
                 "allow_active": ALLOW_ACTIVE}
_ALLOW_KEYS = {state: key for key, state in _ALLOW_STATES.items()}
_POLICY_LEVEL = {"vendor": POLICY_VENDOR, "vendor_url": POLICY_VENDOR_URL,
                 "icon_name": POLICY_ICON_NAME}
_ACTION_LEVEL = {"vendor": ACTION_VENDOR, "vendor_url": ACTION_VENDOR_URL,
                 "icon_name": ACTION_ICON_NAME}


class Action:
    """One action as PolicyKit holds it after reading the file."""

    def __init__(self, action_id, line):
        self.id = action_id
        self.line = line
        self.defaults = {key: NOT_AUTHORIZED for key in DEFAULTS}
        self.annotations = {}
        #: The untranslated <description> and <message> as PolicyKit keeps them:
        #: the last piece of text it was handed for each (a line break ends a piece).
        self.description = None
        self.message = None
        #: Element name (with its key or language) -> the lines it starts on.
        self.elements = {}


class PolicyReading:
    """A policy file as polkit 127 reads it: its actions, every element it
    reads once that appears more than once, and where it stops reading."""

    def __init__(self):
        #: Action id -> Action, the last action of each id, as PolicyKit keeps them.
        self.actions = {}
        #: (action id or None for the file, element, lines) for every repeat.
        self.repeats = []
        #: Why and where PolicyKit stops reading the file, or None.
        self.stopped = None
        self._ids = {}
        self._file_elements = {}


class _Stop(Exception):
    """PolicyKit's parser stops here (XML_StopParser)."""


def _note(table, name, line):
    table.setdefault(name, []).append(line)


def read_policy(path):
    """The policy at `path` as polkit 127 reads it (see READ AS POLKIT READS IT).

    Raises OSError when the file cannot be read and expat.ExpatError when it is
    not well-formed XML, which PolicyKit cannot read either.
    """
    with open(path, "rb") as fh:
        data = fh.read()
    reading = PolicyReading()
    parser = expat.ParserCreate()
    parser.ordered_attributes = True
    parser.buffer_text = False
    stack = []
    state = [NONE]
    current = [None]
    key = [None]
    untranslated = [False]

    def stop(why):
        reading.stopped = f"line {parser.CurrentLineNumber}: {why}"
        raise _Stop()

    def start(el, attrs):
        if len(stack) >= MAX_DEPTH:
            stop("the elements nest deeper than the parser's limit")
        line = parser.CurrentLineNumber
        here, new = state[0], NONE
        if here == NONE:
            if el == "policyconfig":
                new = POLICY_CONFIG
        elif here == POLICY_CONFIG:
            if el == "action":
                if len(attrs) != 2 or attrs[0] != "id":
                    stop("an <action> whose one attribute is not its id")
                new = ACTION
                current[0] = Action(attrs[1], line)
                _note(reading._ids, attrs[1], line)
            elif el in _POLICY_LEVEL and not attrs:
                new = _POLICY_LEVEL[el]
                _note(reading._file_elements, el, line)
        elif here == ACTION:
            action = current[0]
            if el == "defaults":
                new = IN_DEFAULTS
                _note(action.elements, "defaults", line)
            elif el in ("description", "message"):
                lang = attrs[1] if len(attrs) == 2 and attrs[0] == "xml:lang" else None
                new = ACTION_DESCRIPTION if el == "description" else ACTION_MESSAGE
                untranslated[0] = lang is None
                _note(action.elements, f'{el} xml:lang="{lang}"' if lang else el, line)
            elif el in _ACTION_LEVEL and not attrs:
                new = _ACTION_LEVEL[el]
                _note(action.elements, el, line)
            elif el == "annotate":
                if len(attrs) != 2 or attrs[0] != "key":
                    stop("an <annotate> whose one attribute is not its key")
                new = ANNOTATE
                key[0] = attrs[1]
                _note(action.elements, f'annotate key="{attrs[1]}"', line)
        elif here == IN_DEFAULTS:
            if el in _ALLOW_STATES:
                new = _ALLOW_STATES[el]
                _note(current[0].elements, el, line)
        if new == NONE:
            new = UNKNOWN
        stack.append(new)
        state[0] = new

    def cdata(text):
        here = state[0]
        if here in (POLICY_ICON_NAME, ACTION_ICON_NAME):
            if text.endswith((".png", ".jpg")) or "/" in text:
                stop(f"the icon name {text!r} is not valid")
        elif here in _ALLOW_KEYS:
            if text not in IMPLICIT_AUTHORIZATIONS:
                stop(f"{text!r} in <{_ALLOW_KEYS[here]}> is not a default PolicyKit reads")
            current[0].defaults[_ALLOW_KEYS[here]] = text
        elif here == ANNOTATE:
            current[0].annotations[key[0]] = text
        elif here == ACTION_DESCRIPTION and untranslated[0]:
            current[0].description = text
        elif here == ACTION_MESSAGE and untranslated[0]:
            current[0].message = text

    def end(el):
        if state[0] == ACTION:
            action = current[0]
            reading.actions.pop(action.id, None)
            reading.actions[action.id] = action
        stack.pop()
        state[0] = stack[-1] if stack else NONE

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = cdata
    try:
        parser.Parse(data, True)
    except _Stop:
        pass
    for action_id, lines in reading._ids.items():
        if len(lines) > 1:
            reading.repeats.append((action_id, "action", lines))
    for element, lines in reading._file_elements.items():
        if len(lines) > 1:
            reading.repeats.append((None, element, lines))
    for action in reading.actions.values():
        for element, lines in action.elements.items():
            if len(lines) > 1:
                reading.repeats.append((action.id, element, lines))
    return reading


_TYPE_PARAMS = tuple(getattr(ast, kind) for kind in ("TypeVar", "ParamSpec", "TypeVarTuple")
                     if hasattr(ast, kind))
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda, ast.ListComp,
           ast.SetComp, ast.DictComp, ast.GeneratorExp)


def _how_bound(node, name):
    """How `node` binds, rebinds or unbinds `name`, or None when it does not."""
    if isinstance(node, ast.Name) and node.id == name:
        if isinstance(node.ctx, ast.Del):
            return "deleted"
        if isinstance(node.ctx, ast.Store):
            return "assigned"
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == name:
        return "defined by a def or class statement"
    elif isinstance(node, (ast.Global, ast.Nonlocal)) and name in node.names:
        return "declared global or nonlocal, so a function can rebind it"
    elif isinstance(node, ast.alias):
        if node.name == "*":
            return "possibly bound by a star import"
        if (node.asname or node.name.partition(".")[0]) == name:
            return "bound by an import"
    elif isinstance(node, ast.arg) and node.arg == name:
        return "a parameter of that name"
    elif isinstance(node, ast.ExceptHandler) and node.name == name:
        return "bound by an except clause"
    elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name == name:
        return "bound by a match pattern"
    elif isinstance(node, ast.MatchMapping) and node.rest == name:
        return "bound by a match pattern"
    elif isinstance(node, _TYPE_PARAMS) and node.name == name:
        return "a type parameter of that name"
    elif isinstance(node, ast.Constant) and node.value == name:
        return "named in a string, which can bind it at run time (globals(), setattr)"
    return None


#: Builtins that bind a module's names by a value computed at run time, which a
#: syntax tree cannot show.
_DYNAMIC_BINDERS = ("exec", "eval", "compile", "globals", "vars", "locals", "setattr", "delattr",
                    "__import__")


def _dynamic_binder(node):
    """The builtin a call names when it is one of _DYNAMIC_BINDERS, else None."""
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Name):
        called = func.id
    elif (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
            and func.value.id == "builtins"):
        called = func.attr
    else:
        return None
    return called if called in _DYNAMIC_BINDERS else None


def _bindings(tree, parents, name):
    """Every place in `tree` that can bind, rebind or unbind `name`: (node, words)."""
    found = []
    for node in ast.walk(tree):
        how = _how_bound(node, name)
        called = _dynamic_binder(node)
        if called:
            how = f"possibly bound by {called}(), which binds names the syntax tree does not show"
        if how is None:
            continue
        where, up = "at module level", parents.get(node)
        while up is not None and up is not tree:
            if isinstance(up, _SCOPES):
                where = "inside a function, class, lambda or comprehension"
                break
            if isinstance(up, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With,
                               ast.AsyncWith, ast.Match)) or type(up).__name__ == "TryStar":
                where = "inside an if, for, while, try, with or match block"
            up = parents.get(up)
        found.append((node, f"line {getattr(node, 'lineno', '?')}, {where}: {how}"))
    return sorted(found, key=lambda item: (getattr(item[0], "lineno", 0), item[1]))


def read_registry(registry_path):
    """The registry's syntax tree and each node's parent; nothing is imported."""
    with open(registry_path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), registry_path)
    return tree, {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}


def registry_definition(registry_path, tree, parents, name):
    """The value `name` holds once Python has run the registry module, read from
    its syntax tree: (the value's node or None, refusals).

    The dispatcher imports the name, so it holds the value Python bound LAST.
    The syntax tree shows that value only when the module binds the name exactly
    once, unconditionally, at module level: a reassignment, a definition inside
    a function or a branch, or any other binding (an import, a del, a global
    declaration, a string that names it, a call that binds names at run time)
    is refused, naming each line.
    """
    bindings = _bindings(tree, parents, name)
    if not bindings:
        raise ValueError(f"{registry_path}: {name} is not assigned")
    if len(bindings) == 1:
        target = bindings[0][0]
        statement = parents.get(target)
        if parents.get(statement) is tree and (
                isinstance(statement, ast.Assign) and len(statement.targets) == 1
                and statement.targets[0] is target
                or isinstance(statement, ast.AnnAssign) and statement.target is target
                and statement.value is not None):
            return statement.value, []
    return None, [f"{registry_path}: {name} is bound {len(bindings)} time(s) "
                  f"({'; '.join(words for _, words in bindings)}); the dispatcher holds the value "
                  f"Python bound last, so {name} is read only from one unconditional "
                  f"module-level definition"]


def privileged_tools(registry_path):
    """The _PRIVILEGED_TOOLS allowlist as the dispatcher holds it, read from the
    registry's syntax tree: (the set or None, refusals)."""
    tree, parents = read_registry(registry_path)
    value, refusals = registry_definition(registry_path, tree, parents, "_PRIVILEGED_TOOLS")
    if value is None:
        return None, refusals
    for _, words in _bindings(tree, parents, "frozenset"):
        refusals.append(f"{registry_path}: frozenset is bound ({words}), so frozenset(...) "
                        f"may not be the builtin")
    if (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
            and value.func.id == "frozenset" and len(value.args) == 1 and not value.keywords
            and isinstance(value.args[0], ast.Set)
            and all(isinstance(e, ast.Constant) and isinstance(e.value, str)
                    for e in value.args[0].elts)):
        return {e.value for e in value.args[0].elts}, refusals
    raise ValueError(f"{registry_path}: _PRIVILEGED_TOOLS is not a frozenset of string literals")


def reading_violations(reading):
    """Where `reading` departs from the file as written: PolicyKit stops reading
    it, or keeps the last of an element that appears more than once."""
    violations = []
    if reading.stopped:
        violations.append(f"PolicyKit stops reading this file at {reading.stopped}, so it is "
                          f"not read as written and the actions after that point do not exist")
    for action_id, element, lines in reading.repeats:
        where = action_id if action_id is not None else "the file"
        violations.append(f"{where}: <{element}> appears {len(lines)} times (lines "
                          f"{', '.join(map(str, lines))}); PolicyKit keeps the last, so the "
                          f"file reads differently to it than as written")
    return violations


def check(policy_path, registry_path, runner=RUNNER):
    """Return (bound action ids, violations, allowlist); raise on unreadable input."""
    tools, violations = privileged_tools(registry_path)
    reading = read_policy(policy_path)
    bound, argv1s = [], []
    violations += reading_violations(reading)
    for action in reading.actions.values():
        if action.annotations.get(EXEC_PATH) != runner:
            continue
        bound.append(action.id)
        for key in DEFAULTS:
            value = action.defaults[key]
            if value != REQUIRED:
                violations.append(f"{action.id}: {key} is {value!r}, not {REQUIRED!r} "
                                  f"(one administrator authentication per call, no keep)")
        if EXEC_ARGV1 not in action.annotations:
            violations.append(f"{action.id}: 0 exec.argv1 annotations; exactly one is required, "
                              f"or the action matches any first argument")
        else:
            argv1s.append(action.annotations[EXEC_ARGV1])
    if not bound:
        violations.append(f"no action is bound to {runner}: nothing was checked")
    repeated = sorted({name for name in argv1s if argv1s.count(name) > 1})
    if repeated:
        violations.append(f"exec.argv1 named by more than one action: {repeated}")
    if tools is not None:
        missing = sorted(tools - set(argv1s))
        extra = sorted(set(argv1s) - tools)
        if missing:
            violations.append(f"privileged tools with no action of their own: {missing}")
        if extra:
            violations.append(f"actions naming a tool outside _PRIVILEGED_TOOLS: {extra}")
    return bound, violations, tools


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--policy", required=True)
    parser.add_argument("--registry")
    parser.add_argument("--runner", default=RUNNER)
    parser.add_argument("--exec-paths", action="store_true",
                        help="print each action's id and exec.path as PolicyKit reads them")
    args = parser.parse_args(argv)
    if args.exec_paths:
        try:
            reading = read_policy(args.policy)
        except (OSError, expat.ExpatError) as exc:
            print(f"CANNOT CHECK: {exc}")
            return 2
        violations = reading_violations(reading)
        for violation in violations:
            print(f"VIOLATION: {violation}")
        if violations:
            return 1
        for action in reading.actions.values():
            if EXEC_PATH in action.annotations:
                print(f"{action.id}\t{action.annotations[EXEC_PATH]}")
        return 0
    if not args.registry:
        parser.error("--registry is required unless --exec-paths is given")
    try:
        bound, violations, tools = check(args.policy, args.registry, args.runner)
    except (OSError, SyntaxError, ValueError, expat.ExpatError) as exc:
        print(f"CANNOT CHECK: {exc}")
        return 2
    for violation in violations:
        print(f"VIOLATION: {violation}")
    if violations:
        return 1
    print(f"PASS: {len(bound)} action(s) bound to {args.runner} ({', '.join(bound)}), each {REQUIRED} "
          f"in {', '.join(DEFAULTS)} (no keep) with its own exec.argv1, every element PolicyKit reads "
          f"once appearing once; the argv1 set equals _PRIVILEGED_TOOLS ({', '.join(sorted(tools))})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
