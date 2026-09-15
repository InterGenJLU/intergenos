# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Database write traces report execution without predicting transaction fate."""

import sqlite3

import pytest

from pkm import database


@pytest.mark.parametrize("operation", ["add_installed", "log_operation"])
@pytest.mark.parametrize("outcome", ["immediate_commit", "outer_commit", "rollback"])
def test_write_trace_stays_true_after_transaction_outcome(tmp_path, monkeypatch,
                                                       operation, outcome):
    events = []
    monkeypatch.setattr(database, "_TRACE_AVAILABLE", True)
    monkeypatch.setattr(database._trace, "trace_event",
                        lambda event, **fields: events.append((event, fields)))
    path = tmp_path / "packages.db"
    db = database.PackageDB(path)
    table = "installed" if operation == "add_installed" else "history"
    try:
        immediate = outcome == "immediate_commit"
        if not immediate:
            db.conn.execute("BEGIN")
        if operation == "add_installed":
            db.add_installed("trace-fixture", "1.0", commit=immediate)
        else:
            db.log_operation("install", "trace-fixture", commit=immediate)

        # The SQL succeeded even when another connection cannot yet see it.
        assert db.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1
        with sqlite3.connect(path) as reader:
            assert reader.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == int(immediate)
        matching = [fields for event, fields in events
                    if event == "pkm_db_write" and fields["operation"] == operation]
        assert len(matching) == 1
        recorded = matching[0].copy()
        if outcome == "rollback":
            db.conn.rollback()
        elif outcome == "outer_commit":
            db.conn.commit()
        with sqlite3.connect(path) as reader:
            assert reader.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == int(outcome != "rollback")

        assert "committed" not in recorded, recorded
        assert recorded["statement_executed"] is True
        assert recorded["pkg"] == "trace-fixture"
    finally:
        db.close()
