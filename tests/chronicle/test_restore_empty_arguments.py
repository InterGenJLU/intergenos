# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Empty restore arguments are refused before any restore or escalation."""

import subprocess
import sys
from pathlib import Path

import pytest

from chronicle import api, config, engine, escalate


@pytest.fixture
def saved_point(tmp_path):
    document = tmp_path / "document"
    document.write_text("saved content\n")
    store = tmp_path / "store"
    conf = tmp_path / "chronicle.conf"
    conf.write_text("# Isolated restore test.\n")
    backend = engine.Engine(local_root=store, config=config.Config())
    version = backend.capture(
        "restore-point", scope={"paths": [str(document)], "packages": ["sample"]},
        reason="empty argument test",
    )["version_id"]
    return backend, version, document, store, conf


@pytest.mark.parametrize("json_mode", [False, True])
@pytest.mark.parametrize("mixed", [False, True])
def test_real_cli_rejects_empty_paths(saved_point, json_mode, mixed):
    _, version, document, store, conf = saved_point
    entry = Path(__file__).resolve().parents[2] / "assets/intergenos-backup/chronicle-cli"
    result = subprocess.run(
        [sys.executable, str(entry), "--local-root", str(store), "--config", str(conf),
         "--socket", str(store / "absent.sock"), "restore", "restore-point", version,
         *([str(document)] if mixed else []), "", "--dry-run",
         *(["--json"] if json_mode else [])],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0, result.stdout
    assert "empty" in result.stderr.lower()
    assert document.read_text() == "saved content\n"


@pytest.mark.parametrize("verb", ["restore-plan", "restore"])
def test_api_refuses_empty_path_before_escalation_or_partial_restore(saved_point, monkeypatch, verb):
    backend, version, document, *_ = saved_point
    document.write_text("current content\n")
    monkeypatch.setattr(escalate, "has_cap_chown", lambda: False)
    calls = []

    def unexpected_escalation(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("invalid paths reached service escalation")

    monkeypatch.setattr(escalate, "run_restore_via_unit", unexpected_escalation)
    result = api.dispatch(backend, {"verb": verb, "args": {
        "layer": "restore-point", "version_id": version, "paths": [str(document), ""],
    }})
    assert result["ok"] is False
    assert "empty" in result["error"].lower()
    assert calls == []
    assert document.read_text() == "current content\n"
