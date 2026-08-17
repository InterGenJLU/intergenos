# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The name-lookup failure classifier.

What is being protected: a machine whose network works and whose name server
does not must never be told to connect to a network. Before this module both
failures produced one verdict and one instruction, and the instruction was
right for only one of them.

Every failure path here is produced by INJECTION — a resolver callable that
raises the exact error a broken name server produces, and a routing table
supplied as text. Nothing in this file touches the network of the machine it
runs on, so the tests behave identically on a machine with a perfect
connection and on one with none.
"""

from __future__ import annotations

import errno
import socket
import unittest

from intergen import net_diagnostics as nd


# Routing tables as the kernel prints them. Column layouts copied from a real
# machine so the parser is exercised against the format it will actually meet.
ROUTE_WITH_DEFAULT = (
    "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
    "eno2\t00000000\t0101A8C0\t0003\t0\t0\t100\t00000000\t0\t0\t0\n"
    "eno2\t0001A8C0\t00000000\t0001\t0\t0\t100\t00FFFFFF\t0\t0\t0\n"
)
ROUTE_WITHOUT_DEFAULT = (
    "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
    "eno2\t0001A8C0\t00000000\t0001\t0\t0\t100\t00FFFFFF\t0\t0\t0\n"
)
ROUTE_LOOPBACK_DEFAULT_ONLY = (
    "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
    "lo\t00000000\t00000000\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"
)
ROUTE_DEFAULT_NOT_UP = (
    "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
    "eno2\t00000000\t0101A8C0\t0002\t0\t0\t100\t00000000\t0\t0\t0\n"
)

IPV6_WITH_DEFAULT = (
    "00000000000000000000000000000000 00 "
    "00000000000000000000000000000000 00 "
    "fe80000000000000021122fffe334455 00000400 00000001 00000000 00000003 eno2\n"
)
IPV6_WITHOUT_DEFAULT = (
    "fe800000000000000000000000000000 40 "
    "00000000000000000000000000000000 00 "
    "00000000000000000000000000000000 00000100 00000000 00000000 00000001 eno2\n"
)


def _reader(ipv4=ROUTE_WITHOUT_DEFAULT, ipv6=IPV6_WITHOUT_DEFAULT):
    """A routing-table reader that serves the supplied text."""
    def read(path):
        if path.endswith("ipv6_route"):
            return ipv6
        return ipv4
    return read


def _gaierror(code=None):
    """The error a name lookup raises when the name server does not answer."""
    if code is None:
        code = socket.EAI_AGAIN
    return socket.gaierror(code, "Temporary failure in name resolution")


class TestClassifyException(unittest.TestCase):
    def test_eai_again_is_name_resolution(self):
        # The exact signature of a machine whose name server is not answering.
        self.assertEqual(nd.classify_exception(_gaierror(socket.EAI_AGAIN)),
                         nd.NAME_RESOLUTION)

    def test_eai_noname_is_name_resolution(self):
        self.assertEqual(nd.classify_exception(_gaierror(socket.EAI_NONAME)),
                         nd.NAME_RESOLUTION)

    def test_eai_fail_is_name_resolution(self):
        self.assertEqual(nd.classify_exception(_gaierror(socket.EAI_FAIL)),
                         nd.NAME_RESOLUTION)

    def test_gaierror_that_is_not_a_lookup_failure_is_unknown(self):
        # A bad service name is a fault in the call, not in the network.
        # Calling it "your name server is broken" would send the user to fix
        # something that is not wrong.
        self.assertEqual(nd.classify_exception(_gaierror(socket.EAI_SERVICE)),
                         nd.UNKNOWN)

    def test_network_unreachable_is_no_route(self):
        exc = OSError(errno.ENETUNREACH, "Network is unreachable")
        self.assertEqual(nd.classify_exception(exc), nd.NO_ROUTE)

    def test_host_unreachable_is_no_route(self):
        exc = OSError(errno.EHOSTUNREACH, "No route to host")
        self.assertEqual(nd.classify_exception(exc), nd.NO_ROUTE)

    def test_connection_refused_is_unknown(self):
        exc = ConnectionRefusedError(errno.ECONNREFUSED, "refused")
        self.assertEqual(nd.classify_exception(exc), nd.UNKNOWN)

    def test_none_is_unknown(self):
        self.assertEqual(nd.classify_exception(None), nd.UNKNOWN)

    def test_urlerror_wrapper_is_unwrapped(self):
        # The form the model download actually sees: urllib wraps the real
        # error in .reason. A classifier that only read the outermost
        # exception would call every download failure unknown.
        import urllib.error

        wrapped = urllib.error.URLError(_gaierror())
        self.assertEqual(nd.classify_exception(wrapped), nd.NAME_RESOLUTION)

    def test_chained_cause_is_unwrapped(self):
        inner = _gaierror()
        outer = RuntimeError("download failed")
        outer.__cause__ = inner
        self.assertEqual(nd.classify_exception(outer), nd.NAME_RESOLUTION)

    def test_self_referential_chain_terminates(self):
        # A malformed chain must not hang the classifier — this runs during a
        # failure, which is the worst moment to lock up.
        exc = RuntimeError("loop")
        exc.__cause__ = exc
        self.assertEqual(nd.classify_exception(exc), nd.UNKNOWN)


class TestHasDefaultRoute(unittest.TestCase):
    def test_ipv4_default_route_found(self):
        self.assertTrue(nd.has_default_route(_reader(ipv4=ROUTE_WITH_DEFAULT)))

    def test_no_default_route(self):
        self.assertFalse(nd.has_default_route(_reader()))

    def test_loopback_default_does_not_count(self):
        # A machine that can only talk to itself is not on a network.
        self.assertFalse(nd.has_default_route(
            _reader(ipv4=ROUTE_LOOPBACK_DEFAULT_ONLY)))

    def test_default_route_that_is_down_does_not_count(self):
        self.assertFalse(nd.has_default_route(
            _reader(ipv4=ROUTE_DEFAULT_NOT_UP)))

    def test_ipv6_only_default_route_counts(self):
        self.assertTrue(nd.has_default_route(
            _reader(ipv4=ROUTE_WITHOUT_DEFAULT, ipv6=IPV6_WITH_DEFAULT)))

    def test_unreadable_tables_report_no_route(self):
        # False routes the caller to "you are not on a network", which is the
        # behaviour that existed before any of this — an unreadable routing
        # table can never make the guidance worse than it already was.
        def explode(path):
            raise OSError("nope")

        self.assertFalse(nd.has_default_route(explode))

    def test_garbage_tables_report_no_route(self):
        self.assertFalse(nd.has_default_route(lambda path: "not a table\n@@@\n"))


class _Probe:
    """Assembles the injected resolver/connector pair for probe_hosts."""

    def __init__(self, resolves=None, connects=False, resolve_error=None):
        self.resolves = resolves
        self.connects = connects
        self.resolve_error = resolve_error
        self.resolved_hosts = []
        self.connected = []

    def resolve(self, host, port):
        self.resolved_hosts.append(host)
        if self.resolve_error is not None:
            raise self.resolve_error
        return self.resolves if self.resolves is not None else [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "",
             ("192.0.2.1", port))
        ]

    def connect(self, family, socktype, proto, sockaddr, timeout):
        self.connected.append(sockaddr)
        return self.connects


class TestProbeHosts(unittest.TestCase):
    def test_reachable_when_a_source_answers(self):
        probe = _Probe(connects=True)
        result = nd.probe_hosts(("example.invalid",), resolve=probe.resolve,
                                connect=probe.connect,
                                route_check=lambda: True)
        self.assertTrue(result.reachable)
        self.assertEqual(result.cause, nd.REACHABLE)

    def test_stops_at_the_first_source_that_answers(self):
        probe = _Probe(connects=True)
        nd.probe_hosts(("first.invalid", "second.invalid"),
                       resolve=probe.resolve, connect=probe.connect,
                       route_check=lambda: True)
        self.assertEqual(probe.resolved_hosts, ["first.invalid"])

    def test_lookup_failure_with_a_route_is_name_resolution(self):
        # THE case this module exists for: connected, and the name server is
        # not answering. Telling this user to join a network would be telling
        # them to join the one they are already on.
        probe = _Probe(resolve_error=_gaierror())
        result = nd.probe_hosts(("example.invalid",), resolve=probe.resolve,
                                connect=probe.connect,
                                route_check=lambda: True)
        self.assertFalse(result.reachable)
        self.assertEqual(result.cause, nd.NAME_RESOLUTION)
        self.assertTrue(result.has_route)
        self.assertFalse(result.resolved_any)

    def test_lookup_failure_without_a_route_is_no_link(self):
        # Genuinely not on a network. Same exception as the case above — the
        # routing table is the only thing that tells them apart.
        probe = _Probe(resolve_error=_gaierror())
        result = nd.probe_hosts(("example.invalid",), resolve=probe.resolve,
                                connect=probe.connect,
                                route_check=lambda: False)
        self.assertFalse(result.reachable)
        self.assertEqual(result.cause, nd.NO_LINK)

    def test_names_resolve_but_nothing_connects_is_no_route(self):
        probe = _Probe(connects=False)
        result = nd.probe_hosts(("example.invalid",), resolve=probe.resolve,
                                connect=probe.connect,
                                route_check=lambda: True)
        self.assertFalse(result.reachable)
        self.assertEqual(result.cause, nd.NO_ROUTE)
        self.assertTrue(result.resolved_any)

    def test_route_is_not_consulted_when_names_resolve(self):
        # The routing table only matters for interpreting a lookup failure.
        def explode():
            raise AssertionError("route check must not run here")

        probe = _Probe(connects=False)
        result = nd.probe_hosts(("example.invalid",), resolve=probe.resolve,
                                connect=probe.connect, route_check=explode)
        self.assertEqual(result.cause, nd.NO_ROUTE)

    def test_resolver_returning_nothing_counts_as_a_lookup_failure(self):
        # A resolver that answers with an empty list produced no address. That
        # is the lookup failing; moving on with no record of it would leave
        # the cause as "unknown" for a machine whose cause is knowable.
        probe = _Probe(resolves=[])
        result = nd.probe_hosts(("example.invalid",), resolve=probe.resolve,
                                connect=probe.connect,
                                route_check=lambda: True)
        self.assertEqual(result.cause, nd.NAME_RESOLUTION)

    def test_non_lookup_resolver_error_is_unknown(self):
        probe = _Probe(resolve_error=OSError(errno.EACCES, "denied"))
        result = nd.probe_hosts(("example.invalid",), resolve=probe.resolve,
                                connect=probe.connect,
                                route_check=lambda: True)
        self.assertEqual(result.cause, nd.UNKNOWN)

    def test_second_host_is_tried_after_the_first_fails_to_connect(self):
        probe = _Probe(connects=False)
        nd.probe_hosts(("first.invalid", "second.invalid"),
                       resolve=probe.resolve, connect=probe.connect,
                       route_check=lambda: True)
        self.assertEqual(probe.resolved_hosts,
                         ["first.invalid", "second.invalid"])


class TestGuidance(unittest.TestCase):
    """The words. These assertions are the point of the whole change: the
    wrong instruction must be impossible to produce for the right cause."""

    def test_name_resolution_never_says_connect_or_wifi(self):
        text = (nd.cause_headline(nd.NAME_RESOLUTION) + " "
                + nd.cause_detail(nd.NAME_RESOLUTION)).lower()
        self.assertNotIn("connect to wifi", text)
        self.assertNotIn("join wifi", text)
        self.assertIn("name server", text)

    def test_name_resolution_says_the_connection_is_working(self):
        text = nd.cause_detail(nd.NAME_RESOLUTION).lower()
        self.assertIn("connection itself is working", text)

    def test_no_link_does_say_to_connect(self):
        text = nd.cause_detail(nd.NO_LINK).lower()
        self.assertIn("connect to a network", text)

    def test_no_route_does_not_blame_the_name_server(self):
        text = nd.cause_detail(nd.NO_ROUTE).lower()
        self.assertIn("name server is working", text)

    def test_every_cause_has_words(self):
        for cause in (nd.REACHABLE, nd.NO_LINK, nd.NAME_RESOLUTION,
                      nd.NO_ROUTE, nd.UNKNOWN):
            self.assertTrue(nd.cause_headline(cause))

    def test_an_unknown_cause_string_falls_back_rather_than_raising(self):
        self.assertEqual(nd.cause_headline("not-a-cause"),
                         nd.cause_headline(nd.UNKNOWN))
        self.assertEqual(nd.cause_detail("not-a-cause"),
                         nd.cause_detail(nd.UNKNOWN))

    def test_only_name_resolution_points_at_the_page(self):
        self.assertTrue(nd.cause_is_name_resolution(nd.NAME_RESOLUTION))
        for cause in (nd.REACHABLE, nd.NO_LINK, nd.NO_ROUTE, nd.UNKNOWN):
            self.assertFalse(nd.cause_is_name_resolution(cause))


if __name__ == "__main__":
    unittest.main()
