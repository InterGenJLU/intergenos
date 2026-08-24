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

Where the property is about ORDER rather than reference — did a frame reach the
client before the routing work started — a code object cannot answer, because
order across two functions is not something ``co_names`` records. Those readers
parse the module into a syntax tree instead. That is the same discipline, not an
exception to it: what is forbidden is matching TEXT, and a syntax tree is no more
text than a code object is. A reader of either kind that cannot characterise the
shape in front of it raises :class:`ShapeNotRecognised` rather than returning an
answer, because "I could not tell" and "the property is absent" are different
facts and only one of them is a defect in the software being measured.
"""

from __future__ import annotations

import ast
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

# ── Reading the turn path: did anything reach the client before routing? ──────

class ShapeNotRecognised(Exception):
    """The reader could not find the construct it measures ordering against.

    Raised rather than returned. A gate that received ``[]`` from a reader that
    never located the routing call would report "no acknowledgement is sent",
    which is a statement about the software; the true statement is about the
    reader. The two must not be able to look the same to whoever reads the
    failure.
    """


#: What the server calls to put a frame on the socket.
_SEND_METHODS = ("send_json", "send_str", "send")

#: A frame type carrying a failure is not an acknowledgement: a turn that is
#: going to succeed slowly never produces one, so counting it would let a gate
#: pass on exactly the turns this property exists to protect.
_ERROR_MARKERS = ("error", "fail", "denied", "refused")


def _is_router_route(node: ast.AST) -> bool:
    """True for ``self._router.route`` however it is spelled."""
    return (isinstance(node, ast.Attribute) and node.attr == "route"
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "_router"
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "self")


def routing_call_sites(module_source: str) -> list[tuple[str, int]]:
    """``(function name, line)`` for every site that hands a turn to the router.

    BOTH shapes count, and that is the point of this reader. R001.1 called
    ``self._router.route(...)`` directly, so the routing site was the callee.
    The current tree offloads it — the router's ``route`` is handed to an
    executor as an ARGUMENT and never appears in callee position at all. A
    reader that recognised only the first shape would find nothing on a system
    that routes perfectly well, and the gate above it would report the
    acknowledgement as missing, blaming the software for the reader's blindness.
    """
    tree = ast.parse(module_source)
    # Innermost definition wins: a nested helper's name is the useful one, and
    # walk order is not nesting order, so the containing span is compared rather
    # than relying on which definition happened to be visited last.
    spans = [(fn.lineno, fn.end_lineno or fn.lineno, fn.name)
             for fn in ast.walk(tree)
             if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))]

    def owner_of(line: int) -> str:
        containing = [(start, end, name) for start, end, name in spans
                      if start <= line <= end]
        if not containing:
            return "<module>"
        return min(containing, key=lambda s: s[1] - s[0])[2]

    sites: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        hit = _is_router_route(node.func) or any(
            _is_router_route(a) for a in list(node.args) +
            [kw.value for kw in node.keywords])
        if hit:
            sites.append((owner_of(node.lineno), node.lineno))
    return sorted(set(sites))


def _frame_sends(node: ast.AST) -> list[tuple[int, str]]:
    """``(line, type)`` for every frame this subtree puts on the socket.

    The LINE OF THE SEND ITSELF, never the line of a statement containing it. A
    ``try:`` or a ``with`` header sits above its own body, so a reader that
    filtered compound statements by their header line would count a frame sent
    after the routing call as one sent before it — the gate would then report an
    acknowledgement that the user never receives in time. Measured while writing
    this reader: filtering by statement line reported the R001.1 module as
    acknowledging turns, because the function header precedes everything in the
    function including the response frame sent at the end.
    """
    sends: list[tuple[int, str]] = []
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        func = sub.func
        if not (isinstance(func, ast.Attribute) and func.attr in _SEND_METHODS):
            continue
        for arg in sub.args:
            if not isinstance(arg, ast.Dict):
                continue
            for key, value in zip(arg.keys, arg.values):
                if (isinstance(key, ast.Constant) and key.value == "type"
                        and isinstance(value, ast.Constant)
                        and isinstance(value.value, str)):
                    sends.append((sub.lineno, value.value))
    return sends


def acknowledgement_before_routing(module_source: str) -> list[str]:
    """Frame types the server sends the client BEFORE the routing work starts.

    Two placements are read, because the shipped code has used both. The frame
    may be sent inside the function that routes, above the routing call; or it
    may be sent by a function that then calls the one that routes, which is
    where the current tree puts it. Error frames are excluded by
    :data:`_ERROR_MARKERS`.

    :raises ShapeNotRecognised: when no routing site can be found at all.
    """
    sites = routing_call_sites(module_source)
    if not sites:
        raise ShapeNotRecognised(
            "no call handing a turn to the router was found in this module, so "
            "there is no routing work to order an acknowledgement against")

    tree = ast.parse(module_source)
    routing_functions = {name for name, _line in sites}
    first_route_line = {}
    for name, line in sites:
        first_route_line[name] = min(line, first_route_line.get(name, line))

    acknowledgements: list[str] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        cutoffs: list[int] = []
        # (a) the acknowledgement sits above the routing call in the same function
        if fn.name in routing_functions:
            cutoffs.append(first_route_line[fn.name])
        # (b) the acknowledgement sits above a call INTO the function that routes,
        #     which is where the current tree puts it
        calls_in = [
            sub.lineno for sub in ast.walk(fn)
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
            and sub.func.attr in routing_functions
            and isinstance(sub.func.value, ast.Name) and sub.func.value.id == "self"
        ]
        if calls_in:
            cutoffs.append(min(calls_in))
        if not cutoffs:
            continue

        cutoff = min(cutoffs)
        for line, frame_type in _frame_sends(fn):
            if line < cutoff:
                acknowledgements.append(frame_type)

    return sorted({t for t in acknowledgements
                   if not any(m in t.lower() for m in _ERROR_MARKERS)})


def client_dispatches(app_js: str, frame_type: str) -> bool:
    """True when the shipped browser has a dispatch arm for *frame_type*.

    An acknowledgement the client does not read is not an acknowledgement: the
    server's frame would leave the browser's failsafe armed exactly as if it had
    never been sent, so both halves are asserted rather than the server's alone.
    """
    return bool(re.search(r"case\s*['\"]" + re.escape(frame_type) + r"['\"]\s*:",
                          app_js))
