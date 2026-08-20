#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# network-diagnostics meta-package — no source, no build.
#
# Installs one file: a README that says what the set contains, what each tool
# answers, and which privileges each needs. The dependency list in package.yml
# is what actually pulls the tools in.

do_install() {
    set -e
    install -dm755 "${DESTDIR}/usr/share/doc/network-diagnostics"
    cat > "${DESTDIR}/usr/share/doc/network-diagnostics/README" <<'READMEEOF'
network-diagnostics meta-package — InterGenOS
=============================================

Installing this package installs a set of tools for diagnosing network
problems. Each one answers a different question, and the useful skill is
knowing which question you are asking.

  mtr       Where along the path is traffic being lost or delayed?
            Sends a continuous sequence of probes and reports loss and
            latency per hop, so a problem at hop 7 is visible as a
            problem at hop 7 rather than as "the destination is slow".
            Needs raw-socket privilege; the packaged build isolates that
            in a separate mtr-packet helper rather than running the whole
            interface privileged.

  tcpdump   What is actually on the wire?
            Captures packets from an interface, or reads a saved capture,
            and prints them decoded. The tool that settles arguments about
            what a client really sent. Needs CAP_NET_RAW to capture; it
            can drop privileges afterwards with -Z.

  iperf3    How much bandwidth does this path really deliver?
            Runs a client against an iperf3 server and measures achieved
            throughput, which is what separates a slow network from a slow
            application. Needs a cooperating server at the other end.

  nmap      What hosts and services are reachable, and what are they?
            Discovers hosts, open ports and service versions. Scanning
            machines you are not responsible for is, in many jurisdictions
            and on most networks, not yours to decide — the tool does not
            know whose network it is on.

  socat     Can I construct exactly this connection by hand?
            Relays data between any two channels: sockets, files, pipes,
            TLS sessions, sub-processes. Used to reproduce a client's
            behaviour precisely when nothing else will.

NOT included:

  ethtool   Ships in the base installation rather than here, because it
            reports and changes the state of the network interface itself.
            That is diagnosis of this machine, and it has to be available
            before the machine can reach a mirror to install anything else.

Each tool is documented in its own manual page: mtr(8), tcpdump(1),
iperf3(1), nmap(1), socat(1).
READMEEOF
}
