# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Restore previews and results keep each filesystem path on one quoted line."""

from types import SimpleNamespace

import pytest

from chronicle import api, cli, config, engine, escalate


class InProcessBackend:
    def __init__(self, backend):
        self.engine = backend

    def call(self, verb, **args):
        response = api.dispatch(self.engine, {"verb": verb, "args": args})
        assert response["ok"], response
        return response["result"]


@pytest.mark.parametrize("name", ["two\nlines", "name\x1b[2K", "before\rafter",
                                 "trailing space ", "quote'and\\slash", "café"])
def test_all_restore_plan_and_result_paths_are_quoted(tmp_path, monkeypatch, capsys, name):
    backend = engine.Engine(local_root=tmp_path / "store", config=config.Config())
    directory = tmp_path / ("directory-" + name)
    directory.mkdir()
    existing = tmp_path / ("existing-" + name)
    missing = tmp_path / ("missing-" + name)
    absent = tmp_path / ("absent-" + name)
    existing.write_text("saved\n")
    missing.write_text("saved\n")
    version = backend.capture("restore-point", scope={
        "paths": list(map(str, [directory, existing, missing])), "packages": ["sample"],
    })["version_id"]
    missing.unlink()
    args = SimpleNamespace(layer="restore-point", version=version,
                           paths=list(map(str, [directory, existing, missing, absent])),
                           mode="replace-confirm", json=False, dry_run=True, yes=True)
    adapter = InProcessBackend(backend)
    cli.cmd_restore(adapter, args, cli.Reporter())
    preview = capsys.readouterr()
    assert preview.err == ""
    assert preview.out.splitlines() == [
        f"Restore plan for {version} (mode: replace-confirm):",
        f"  restore directory {str(directory)!r} (nothing is stored beneath it)",
        f"  OVERWRITE (with confirmation) {str(existing)!r}",
        f"  restore {str(missing)!r}",
        f"  SKIP {str(absent)!r} — not in this version",
        "Dry run — nothing was changed.",
    ]
    # All restore writes stay in the fixture and retain the invoking uid/gid.
    monkeypatch.setattr(escalate, "has_cap_chown", lambda: True)
    monkeypatch.setattr(escalate, "run_restore_via_unit",
                        lambda *a, **kw: pytest.fail("unexpected service escalation"))
    args.mode = "beside"
    args.dry_run = False
    assert cli.cmd_restore(adapter, args, cli.Reporter()) == 1  # absent path
    applied = capsys.readouterr()
    for path in [directory, existing, missing]:
        destination = str(path) + f".chronicle-restored-{version}"
        assert f"  restored {str(path)!r} -> {destination!r}" in applied.out.splitlines()
    assert applied.err.splitlines() == [f"ERROR:   {str(absent)!r}: not in this version"]
