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

PARSED, NOT GREPPED. The policy is read as XML and each action is judged on its
own elements, so a comment or a different action cannot satisfy the check. The
allowlist is read from the registry's syntax tree; nothing is imported.

Usage: check-privileged-runner-policy.py --policy FILE --registry FILE [--runner PATH]
Exit 0 = every action bound to the runner passes; 1 = violations, each named;
2 = an input could not be read or parsed.
"""

import argparse
import ast
import sys
import xml.etree.ElementTree as ElementTree

RUNNER = "/usr/bin/intergen-privileged-runner"
EXEC_PATH = "org.freedesktop.policykit.exec.path"
EXEC_ARGV1 = "org.freedesktop.policykit.exec.argv1"
DEFAULTS = ("allow_any", "allow_inactive", "allow_active")
REQUIRED = "auth_admin"


def privileged_tools(registry_path):
    """The _PRIVILEGED_TOOLS allowlist, read from the registry's syntax tree."""
    with open(registry_path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), registry_path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        else:
            continue
        if not (isinstance(target, ast.Name) and target.id == "_PRIVILEGED_TOOLS"):
            continue
        if (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
                and value.func.id == "frozenset" and len(value.args) == 1
                and isinstance(value.args[0], ast.Set)
                and all(isinstance(e, ast.Constant) and isinstance(e.value, str)
                        for e in value.args[0].elts)):
            return {e.value for e in value.args[0].elts}
        raise ValueError(f"{registry_path}: _PRIVILEGED_TOOLS is not a frozenset of string literals")
    raise ValueError(f"{registry_path}: _PRIVILEGED_TOOLS is not assigned")


def check(policy_path, registry_path, runner=RUNNER):
    """Return (bound action ids, violations, allowlist); raise on unreadable input."""
    tools = privileged_tools(registry_path)
    root = ElementTree.parse(policy_path).getroot()
    bound, violations, argv1s = [], [], []
    for action in root.iter("action"):
        annotations = {}
        for annotate in action.findall("annotate"):
            annotations.setdefault(annotate.get("key"), []).append((annotate.text or "").strip())
        paths = annotations.get(EXEC_PATH, [])
        if runner not in paths:
            continue
        action_id = action.get("id")
        bound.append(action_id)
        if len(paths) != 1:
            violations.append(f"{action_id}: {len(paths)} exec.path annotations; exactly one is read by pkexec")
        defaults = action.find("defaults")
        for key in DEFAULTS:
            element = defaults.find(key) if defaults is not None else None
            value = (element.text or "").strip() if element is not None else None
            if value != REQUIRED:
                violations.append(f"{action_id}: {key} is {value!r}, not {REQUIRED!r} "
                                  f"(one administrator authentication per call, no keep)")
        names = annotations.get(EXEC_ARGV1, [])
        if len(names) != 1:
            violations.append(f"{action_id}: {len(names)} exec.argv1 annotations; exactly one is required, "
                              f"or the action matches any first argument")
        else:
            argv1s.append(names[0])
    if not bound:
        violations.append(f"no action is bound to {runner}: nothing was checked")
    repeated = sorted({name for name in argv1s if argv1s.count(name) > 1})
    if repeated:
        violations.append(f"exec.argv1 named by more than one action: {repeated}")
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
    parser.add_argument("--registry", required=True)
    parser.add_argument("--runner", default=RUNNER)
    args = parser.parse_args(argv)
    try:
        bound, violations, tools = check(args.policy, args.registry, args.runner)
    except (OSError, SyntaxError, ValueError, ElementTree.ParseError) as exc:
        print(f"CANNOT CHECK: {exc}")
        return 2
    for violation in violations:
        print(f"VIOLATION: {violation}")
    if violations:
        return 1
    print(f"PASS: {len(bound)} action(s) bound to {args.runner} ({', '.join(bound)}), each {REQUIRED} "
          f"in {', '.join(DEFAULTS)} (no keep) with its own exec.argv1; the argv1 set equals "
          f"_PRIVILEGED_TOOLS ({', '.join(sorted(tools))})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
