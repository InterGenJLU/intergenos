# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""How gates 6 and 7 read the shape of the shipped modules.

Both gates decide whether an installed system still keeps one conversation for
every client, or one conversation per conversation. They decide it by reading
what the shipped code IS — which names a handler's compiled code references,
which fields a connection carries, how many consent records reach the
dispatcher — because the alternative, standing up a daemon and two browsers,
is not something a post-install checklist can do.

The readers live here, in one file, for two reasons.

  * A gate whose reader is a private copy inside the gate cannot be proved.
    These are proved both ways by intergen/tests/test_installed_gate_shape_
    detectors.py in the ordinary suite: against a stand-in built to the shape
    R001.1 shipped, every reader must still report the defect; against the
    current tree, every reader must report it absent. A reader that cannot fail
    is not a reader, and that is caught in the source tree rather than silently
    on a machine where this tier is gated off.

  * When the shape moves, one file moves with it. The gates keep asking the
    same question either way.

Compiled code objects, not source text: an earlier gate in this tier went green
by reading source text and matching the wrong expression. A code object carries
the names a function really uses, and comments and strings cannot fake it.
"""

from __future__ import annotations

import re

# The call that ends one conversation. Its name is part of the shipped
# interface: a session handler that does not reference it is not ending the
# conversation it just replaced.
RESET = "reset_conversation_state"

# A per-connection field naming the conversation the model is prompted from.
# "session_history" is deliberately NOT here: in the shape this gate was written
# against, that field was the display and persistence copy while the model read
# a buffer held on the shared router.
_OWNED_CONVERSATION_FIELDS = (
    "conversation", "conversation_state", "conversation_history",
    "model_history", "router", "_router",
)


def names_used(func) -> set[str]:
    """Every name the function's code object references, including nested code."""
    seen: set[str] = set()
    stack = [func.__code__]
    while stack:
        code = stack.pop()
        seen.update(code.co_names)
        for const in code.co_consts:
            if hasattr(const, "co_names"):
                stack.append(const)
    return seen


def handler_resets_the_conversation(handler) -> bool:
    """True when this session handler ends the conversation it is replacing."""
    return RESET in names_used(handler)


def connection_owns_the_model_conversation(ctx_cls) -> bool:
    """True when a connection carries the conversation the model is prompted from."""
    fields = set(getattr(ctx_cls, "__annotations__", {}))
    return any(f in fields for f in _OWNED_CONVERSATION_FIELDS)


def connection_fields(ctx_cls) -> list[str]:
    """The per-connection field names, for a failure message that shows its work."""
    return sorted(getattr(ctx_cls, "__annotations__", {}))


def reset_clears_the_consent_record(reset_source: str) -> bool:
    """True when the conversation reset still clears the consent record.

    A premise check, not a finding: both gates rest on the reset actually
    clearing consent, so if that stops being true the gates must be rewritten
    rather than reporting a pass or a fail about it. Matches the record whether
    the reset reaches it as an attribute of the router (`self._trust_state`, the
    shape R001.1 shipped) or of the conversation (`state.trust_state`), and
    whether it is replaced or cleared in place.
    """
    if re.search(r"trust_state\b", reset_source):
        return True
    # Cleared through the conversation's own clear() rather than field by field.
    return bool(re.search(r"\.clear\(\)", reset_source))


def consent_record_sites(web_text: str, router_text: str) -> tuple[int, int]:
    """(per-connection sites, shared-router sites) handing consent to the dispatcher.

    Two non-zero counts mean two records with different lifetimes: a decision
    taken on one path is invisible on the other.
    """
    per_connection = web_text.count("trust_state=ctx.conversation_trust_state")
    shared = router_text.count("trust_state=self._trust_state")
    return per_connection, shared


# --------------------------------------------------------------------------
# WHO STARTS pkexec — gate 3.
#
# The privilege boundary this reader serves turns on ONE question: does the
# daemon exec the setuid helper AS ITS OWN CHILD, or does it start a transient
# unit that execs it? Under NoNewPrivileges the kernel ignores a setuid bit for
# a child of the daemon, so the first shape cannot reach PolicyKit at all; in
# the second the helper is started by the user manager, which the daemon's own
# NoNewPrivileges does not reach, and the boundary works.
#
# THE GATE USED TO ASK THIS WITH A SUBSTRING: `'"pkexec",' in source`. That
# string is present in BOTH shapes — the second one still names pkexec, just
# after a transient-unit launcher and a `--` separator — so the check could not
# tell a broken boundary from a fixed one and went on reporting the defect
# after the dispatch change corrected it. Measured on 2026-08-24 against both modules at
# once: substring True on the R001.1 module (boundary really broken) and True on
# the tree module (boundary fixed).
#
# So the shape is PARSED, not searched. The argv the dispatch builds is read
# from the syntax tree and its FIRST element is what decides: a string constant
# naming pkexec means the daemon execs it directly; a name that resolves to a
# systemd-run path means a transient unit does. Reading the tree rather than
# importing is deliberate — this reader must be able to characterise a module
# it must NOT execute, including the R001.1 stand-in the true-positive control
# builds.

import ast

_DISPATCH_FUNC = "_dispatch_via_pkexec"
_LAUNCHER_LEAF = "systemd-run"


def _module_string_globals(tree: ast.Module) -> dict[str, str]:
    """Module-level NAME = "literal" bindings, so a name in argv can be resolved."""
    out: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Constant):
            continue
        if not isinstance(node.value.value, str):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                out[target.id] = node.value.value
    return out


def _dispatch_argv_first_element(source: str):
    """The first element of the argv the dispatch hands to subprocess.run.

    Returns ("literal", <str>) or ("name", <identifier>), or None when the
    dispatch or its argv cannot be found — which the caller must treat as
    "cannot characterise", never as "fine".
    """
    tree = ast.parse(source)
    func = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == _DISPATCH_FUNC:
            func = node
            break
    if func is None:
        return None, None

    # The argv is either built into a local list and passed by name, or written
    # inline at the call. Both are read; the inline form is what R001.1 shipped.
    lists: dict[str, ast.List] = {}
    for node in ast.walk(func):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.List):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    lists[target.id] = node.value

    argv_list = None
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        is_run = (isinstance(f, ast.Attribute) and f.attr == "run") or \
                 (isinstance(f, ast.Name) and f.id == "run")
        if not is_run or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.List):
            argv_list = first
        elif isinstance(first, ast.Name) and first.id in lists:
            argv_list = lists[first.id]
        if argv_list is not None:
            break

    if argv_list is None or not argv_list.elts:
        return None, tree
    head = argv_list.elts[0]
    if isinstance(head, ast.Constant) and isinstance(head.value, str):
        return ("literal", head.value), tree
    if isinstance(head, ast.Name):
        return ("name", head.id), tree
    return ("other", ast.dump(head)), tree


def daemon_execs_the_setuid_helper_directly(source: str) -> bool:
    """True when the daemon itself execs pkexec — the shape NoNewPrivileges kills.

    Raises ValueError when the dispatch cannot be characterised at all. A reader
    that cannot measure must say so rather than return a comfortable boolean:
    the whole reason this replaced a substring is that the substring answered
    confidently about a shape it could not see.
    """
    head, tree = _dispatch_argv_first_element(source)
    if head is None:
        raise ValueError(
            f"could not find {_DISPATCH_FUNC}'s argv in the module — the dispatch "
            "path cannot be characterised, so nothing is claimed about the boundary"
        )
    kind, value = head
    if kind == "literal":
        return "pkexec" in value
    if kind == "name":
        resolved = _module_string_globals(tree).get(value, "")
        if resolved.endswith(_LAUNCHER_LEAF) or _LAUNCHER_LEAF in resolved:
            return False
        if "pkexec" in resolved:
            return True
        raise ValueError(
            f"{_DISPATCH_FUNC} execs {value!r}, which resolves to {resolved!r} — "
            "neither the setuid helper nor a transient-unit launcher, so this "
            "reader cannot say which shape is shipped"
        )
    raise ValueError(
        f"{_DISPATCH_FUNC}'s argv begins with an expression this reader does not "
        f"understand ({value}); the shape has moved and the reader must move with it"
    )
