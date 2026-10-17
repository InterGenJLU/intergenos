# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Previously curated questions keep their route with deterministic probe data.

The table retains the original row identifiers and route labels. The router
and shipped teaching corpus execute normally on the keyword retrieval path;
only the system probes are replaced. Row 97 replays the networking entry present
in its recorded answer because keyword retrieval differs at that boundary.
BASE_ROUTE records the result of this same fixture at the input revision.
Row 97 is the named correction: its weak local-address candidate is refused
and generation is deferred. Every other row must retain its baseline route.
Live embedding selection and the correctness of the recorded answer are not
measured by this route regression.
"""
import csv
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from intergen.interfaces.types import ToolResult
from intergen.tests.test_compound_howto_routing import router


ROWS = list(csv.DictReader(
    Path(__file__).with_name("curated_route_regressions.tsv").open(), delimiter="\t"))


def test_regression_table_population():
    assert len(ROWS) == 148
    assert len({row["IDX"] for row in ROWS}) == 148


CURATED = {"explain", "keyword", "cache", "direct_answer", "ip_answer", "identity"}


def route_row(router, row):
    router._state_cache = SimpleNamespace(
        matches_state_keyword=lambda q: "kernel" in q.lower(),
        lookup_for_query=lambda q: "6.12-example" if "kernel" in q.lower() else None,
        get_system_map_data=lambda q: None,
    )
    outputs = {
        "hostname": "example",
        "uptime -p": "up 3 hours",
        "df -h --output=size,avail /": "Size Avail\n250G 118G",
        "free -h": "               total used free shared buff/cache available\n"
                   "Mem:           16Gi 4Gi 8Gi 0Gi 4Gi 12Gi\nSwap: 0B 0B 0B",
        "uname -r": "6.12-example",
        "cat /etc/os-release": 'PRETTY_NAME="InterGenOS example"\nNAME="InterGenOS"\nVERSION="example"\nID=intergenos',
        "lscpu": "Architecture: x86_64\nCPU(s): 8\nModel name: Example CPU",
        "nproc": "8",
        "df -h": "Filesystem Size Used Avail Use% Mounted on\n/dev/example 250G 132G 118G 53% /",
        "lspci": "00:02.0 VGA compatible controller: Example Graphics",
    }

    def execute(call, **kwargs):
        command = call.arguments.get("command", "")
        return ToolResult(call_id=call.call_id, name=call.name,
                          content=outputs.get(command, "example probe output"),
                          success=True)

    router._tools.execute = mock.Mock(side_effect=execute)
    if row["IDX"] == "97":
        chosen = next(e for e in router._howto._entries if e.id == "net-status")
        router._howto._embeddings = object()
        router._howto._retrieve_embedding = mock.Mock(return_value=(chosen, 0.757, False))
    return router.route(row["QUERY"], decide_only=row["IDX"] == "97")


@pytest.mark.parametrize("row", ROWS, ids=lambda row: row["IDX"])
def test_recorded_curated_route(router, row, request):
    result = route_row(router, row)
    request.node.user_properties.extend([
        ("row_idx", row["IDX"]), ("captured_route", row["ROUTE"]),
        ("observed_route", result.source), ("used_llm", str(result.used_llm)),
    ])
    assert not result.used_llm
    if row["IDX"] == "97":
        assert result.source == "llm_freeform"
        assert not result.handled
    else:
        assert result.source in CURATED
        assert result.source == row["BASE_ROUTE"], (row["IDX"], result.source, result.text)
