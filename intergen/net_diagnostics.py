# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Why a download source could not be reached — told apart, not lumped together.

The model download needs two separate things to work: a route off this machine,
and a name server that answers. Before this module both failures produced the
same verdict — "offline" — and both produced the same instruction: connect to
WiFi. For the user whose route is fine and whose name server is not, that
instruction is not merely unhelpful, it is confidently wrong: they are told to
join a network they are already on, and nothing they can do from there will fix
anything.

So every reachability question is answered with a CAUSE, and the cause is
derived from two independent facts:

  * does a default route exist (read from the kernel routing tables — no
    traffic, no name lookup, no waiting), and
  * did the name lookup for the download hosts succeed.

From those two, exactly one of four causes:

  ``REACHABLE``       a source answered on :443.
  ``NO_LINK``         name lookup failed and there is no default route. The
                      machine genuinely is not on a network yet. This is the
                      only case where "join a network" is the right advice.
  ``NAME_RESOLUTION`` name lookup failed but a default route exists. The
                      machine IS on a network; the name server it was given is
                      not answering. Advice: choose a different name server.
  ``NO_ROUTE``        names resolved but no source accepted a connection. The
                      name server works; something between here and the
                      download hosts does not.
  ``UNKNOWN``         the probe could not reach a conclusion.

Both callers use this one module so they cannot disagree about what a name
lookup failure looks like or what to tell the user about it:

  * ``intergen.setup`` imports it directly (same package).
  * The Welcomer, which is a separate GTK application and deliberately does not
    import the intergen package, receives a copy of THIS FILE in its own
    package (staged by scripts/build-intergenos-source-tarballs.sh into
    /usr/libexec/intergen-welcome/) and loads it from beside itself. One source
    file in the tree, so a correction lands in both places or in neither.

Nothing here imports anything from intergen — that is what makes the second
consumer possible. Standard library only.

Every probe entry point takes injectable resolver / connector / route-reader
callables. That is how the failure paths are tested: a test supplies a resolver
that raises the exact ``socket.gaierror`` a broken name server produces, rather
than breaking name resolution on the machine running the tests.
"""

from __future__ import annotations

import errno
import socket
from typing import Callable, Iterable, NamedTuple

__all__ = [
    "REACHABLE",
    "NO_LINK",
    "NAME_RESOLUTION",
    "NO_ROUTE",
    "UNKNOWN",
    "MODEL_SOURCE_HOSTS",
    "ProbeResult",
    "classify_exception",
    "has_default_route",
    "probe_hosts",
    "cause_headline",
    "cause_detail",
    "cause_is_name_resolution",
]


REACHABLE = "reachable"
NO_LINK = "no-link"
NAME_RESOLUTION = "name-resolution"
NO_ROUTE = "no-route"
UNKNOWN = "unknown"

#: The hosts the model download actually uses, in the order it uses them
#: (mirror first, vendor second). Callers that can derive the hosts from the
#: live download URLs pass their own list; this is the fallback and the value
#: the Welcomer uses, since it cannot ask the model manager.
MODEL_SOURCE_HOSTS = ("repo.intergenos.org", "huggingface.co")

# getaddrinfo error codes that mean "the name lookup itself failed". EAI_AGAIN
# is the one a machine with no answering name server produces — it is the
# `[Errno -3] Temporary failure in name resolution` string. EAI_NONAME and
# EAI_FAIL come back from a name server that answers with a refusal or a
# non-answer, which for these fixed, known-good hostnames is equally a broken
# lookup. EAI_NODATA does not exist on every platform, so it is looked up
# rather than referenced directly.
_RESOLUTION_ERRNOS = {
    getattr(socket, name)
    for name in ("EAI_AGAIN", "EAI_NONAME", "EAI_FAIL", "EAI_NODATA")
    if hasattr(socket, name)
}

# errno values from a connect() that mean the packet had nowhere to go.
_NO_ROUTE_ERRNOS = {
    getattr(errno, name)
    for name in ("ENETUNREACH", "EHOSTUNREACH", "ENETDOWN", "ENONET")
    if hasattr(errno, name)
}

_IPV4_ROUTE_TABLE = "/proc/net/route"
_IPV6_ROUTE_TABLE = "/proc/net/ipv6_route"

# Routing-table flag bit for "this route is up" (RTF_UP). A default route entry
# that is not up does not count as being on a network.
_RTF_UP = 0x0001


class ProbeResult(NamedTuple):
    """The outcome of a reachability probe.

    ``cause`` is one of the module constants and is the whole point: it is what
    the caller shows the user. ``reachable`` is the plain boolean the previous
    code returned, kept so a caller that only needs the yes/no does not have to
    compare strings. ``resolved_any`` and ``has_route`` are the two underlying
    facts, carried so a caller (or a test) can see WHY the cause was chosen
    rather than having to trust it.
    """

    reachable: bool
    cause: str
    resolved_any: bool
    has_route: bool


def classify_exception(exc: BaseException | None) -> str:
    """The cause an exception raised by a network attempt points at.

    Returns ``NAME_RESOLUTION``, ``NO_ROUTE`` or ``UNKNOWN``. Never raises.

    Unwraps wrappers as it goes: ``urllib.error.URLError`` carries the real
    error in ``.reason``, and that is the form the model download sees, so a
    classifier that only looked at the outermost exception would call every
    download failure unknown. ``__cause__`` and ``__context__`` are followed
    for the same reason.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))

        if isinstance(current, socket.gaierror):
            code = current.args[0] if current.args else None
            if code in _RESOLUTION_ERRNOS:
                return NAME_RESOLUTION
            # Some gaierror codes are not lookup failures at all — a bad
            # service name or an unsupported address family is a fault in the
            # call, not in the network. Calling those "your name server is
            # broken" would send the user to fix something that is not wrong,
            # so they fall through to UNKNOWN.
            return UNKNOWN

        if isinstance(current, OSError) and current.errno in _NO_ROUTE_ERRNOS:
            return NO_ROUTE

        nxt = getattr(current, "reason", None)
        if not isinstance(nxt, BaseException):
            nxt = current.__cause__ or current.__context__
        current = nxt if isinstance(nxt, BaseException) else None

    return UNKNOWN


def _default_route_in_ipv4(text: str) -> bool:
    for line in text.splitlines()[1:]:          # first line is the header
        fields = line.split()
        if len(fields) < 4:
            continue
        iface, destination, _gateway, flags = fields[0], fields[1], fields[2], fields[3]
        if iface == "lo":
            continue
        if destination != "00000000":
            continue
        try:
            if int(flags, 16) & _RTF_UP:
                return True
        except ValueError:
            continue
    return False


def _default_route_in_ipv6(text: str) -> bool:
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 10:
            continue
        destination, prefix_len, iface = fields[0], fields[1], fields[9]
        if iface == "lo":
            continue
        if destination != "0" * 32 or int(prefix_len, 16) != 0:
            continue
        return True
    return False


def has_default_route(read_text: Callable[[str], str] | None = None) -> bool:
    """True if the kernel holds a usable default route on a real interface.

    Read straight out of the routing tables: no packet is sent and no name is
    looked up, so this answers even when the network is exactly as broken as
    the caller is trying to describe. The loopback interface is excluded — a
    machine that can only talk to itself is not on a network.

    A default route is not proof the internet is reachable. It is proof the
    machine has been given a way off itself, which is precisely the fact that
    separates "you are not connected yet" from "you are connected and your
    name lookups are failing".

    ``read_text`` is the file reader, injectable so tests can present routing
    tables without touching the machine's own network. Any read failure yields
    False, and False routes the caller to the "not on a network" message —
    which is the pre-existing behaviour, so an unreadable routing table can
    never make the guidance worse than it already was.
    """
    if read_text is None:
        def read_text(path: str) -> str:  # noqa: E306 — local default
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                return handle.read()

    for path, parser in ((_IPV4_ROUTE_TABLE, _default_route_in_ipv4),
                         (_IPV6_ROUTE_TABLE, _default_route_in_ipv6)):
        try:
            if parser(read_text(path)):
                return True
        except (OSError, ValueError):
            continue
    return False


def probe_hosts(
    hosts: Iterable[str] = MODEL_SOURCE_HOSTS,
    port: int = 443,
    timeout: float = 4.0,
    *,
    resolve: Callable[[str, int], list] | None = None,
    connect: Callable[[tuple, int, int, tuple, float], bool] | None = None,
    route_check: Callable[[], bool] | None = None,
) -> ProbeResult:
    """Try to reach ``hosts`` and report WHY if it did not work.

    Resolves each host, then TCP-connects to the first address that answers.
    Stops at the first success. The two failure facts — did any name resolve,
    is there a default route — decide the cause.

    The route is read only when it is needed to interpret a lookup failure, so
    a healthy machine pays nothing for it.

    ``resolve``, ``connect`` and ``route_check`` are injection points for
    tests: a test drives the broken-name-server path by supplying a resolver
    that raises ``socket.gaierror``, never by breaking name resolution on the
    machine running the test.
    """
    if resolve is None:
        def resolve(host: str, port_: int) -> list:  # noqa: E306
            return socket.getaddrinfo(host, port_, proto=socket.IPPROTO_TCP)

    if connect is None:
        def connect(family, socktype, proto, sockaddr, timeout_) -> bool:  # noqa: E306
            sock = socket.socket(family, socktype, proto)
            sock.settimeout(timeout_)
            try:
                sock.connect(sockaddr)
                return True
            except OSError:
                return False
            finally:
                sock.close()

    if route_check is None:
        route_check = has_default_route

    resolved_any = False
    saw_resolution_failure = False

    for host in hosts:
        try:
            infos = resolve(host, port)
        except OSError as exc:
            if classify_exception(exc) == NAME_RESOLUTION:
                saw_resolution_failure = True
            continue
        if not infos:
            # A resolver that returns nothing without raising is a lookup that
            # produced no answer; treat it as the lookup failing rather than
            # silently moving on with no record of it.
            saw_resolution_failure = True
            continue
        resolved_any = True
        for family, socktype, proto, _canonical, sockaddr in infos:
            try:
                if connect(family, socktype, proto, sockaddr, timeout):
                    return ProbeResult(True, REACHABLE, True, True)
            except OSError:
                continue

    if resolved_any:
        # Names resolve; nothing accepted a connection. The name server is
        # working, so this is not the page's problem to fix.
        return ProbeResult(False, NO_ROUTE, True, True)

    has_route = bool(route_check())
    if saw_resolution_failure:
        cause = NAME_RESOLUTION if has_route else NO_LINK
    else:
        cause = UNKNOWN
    return ProbeResult(False, cause, False, has_route)


def cause_is_name_resolution(cause: str) -> bool:
    """True when the name-lookup page is the thing that can fix this cause."""
    return cause == NAME_RESOLUTION


# The words. They live here rather than at each call site so the command line
# and the Welcomer say the same thing about the same machine state, and so a
# wording correction is one edit. Each is written to name the user's situation
# rather than a component: "this machine cannot look up website names", not
# "getaddrinfo returned EAI_AGAIN".
_HEADLINES = {
    REACHABLE: "The download sources are reachable.",
    NO_LINK: "This machine is not connected to a network yet.",
    NAME_RESOLUTION: "This machine is connected, but it cannot look up "
                     "website names.",
    NO_ROUTE: "Website names are being looked up, but the download sources "
              "cannot be reached.",
    UNKNOWN: "The download sources could not be reached.",
}

_DETAILS = {
    REACHABLE: "",
    NO_LINK: (
        "There is no network connection on this machine — no wired cable and "
        "no wireless network joined. Connect to a network, then try again."
    ),
    NAME_RESOLUTION: (
        "The network connection itself is working. What is failing is the "
        "step that turns a name such as repo.intergenos.org into an address "
        "to connect to. That step is done by a name server, and the one this "
        "network handed out is not answering. Joining a different wireless "
        "network will not help, because the connection is not the problem. "
        "Choosing a different name server will."
    ),
    NO_ROUTE: (
        "Names are being looked up correctly, so the name server is working. "
        "Something between this machine and the download sources is refusing "
        "the connection — a firewall on this network, or the sources being "
        "temporarily unavailable. Trying again later is usually the answer."
    ),
    UNKNOWN: (
        "The reason could not be determined. Check that this machine is "
        "connected to a network, then try again."
    ),
}


def cause_headline(cause: str) -> str:
    """One sentence naming what is wrong, for a title or a status line."""
    return _HEADLINES.get(cause, _HEADLINES[UNKNOWN])


def cause_detail(cause: str) -> str:
    """The explanation under the headline. Empty when nothing is wrong."""
    return _DETAILS.get(cause, _DETAILS[UNKNOWN])
