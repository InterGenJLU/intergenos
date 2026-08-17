# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Integrity gate for the pkm audit findings registry.

This is the programmatic backbone of the audit: it asserts the registry is
well-formed and — crucially — that no finding can be marked `fixed`/`verified`
without naming a regression_test that actually exists. A 'verified' claim with
no test behind it is exactly the unverified-assertion-as-fact the security-first
mandate forbids; this test makes that impossible to commit.
"""
import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

_REGISTRY = Path(__file__).resolve().parent / "findings.yaml"
_REPO_ROOT = Path(__file__).resolve().parents[3]

_REQUIRED_KEYS = {
    "id", "title", "module", "locations", "lens", "severity", "tier",
    "status", "owner", "batch", "regression_test", "evidence", "fix",
}
_LENSES = {"security", "user_control", "correctness", "ux"}
_SEVERITIES = {"critical", "high", "medium", "low"}
_TIERS = {"trust", "understand", "harden"}
_STATUSES = {"open", "in_progress", "fixed", "verified"}
_ID_RE = re.compile(r"^PKM-A\d{2}$")


def _findings():
    with _REGISTRY.open() as fh:
        doc = yaml.safe_load(fh)
    return doc.get("findings", []) or []


def test_registry_loads_and_is_nonempty():
    findings = _findings()
    assert findings, "findings.yaml has no findings"


def test_every_finding_well_formed():
    for f in _findings():
        missing = _REQUIRED_KEYS - set(f)
        assert not missing, f"{f.get('id', '<no id>')} missing keys: {missing}"
        assert _ID_RE.match(f["id"]), f"bad id format: {f['id']!r}"
        assert f["lens"] in _LENSES, f"{f['id']} bad lens {f['lens']!r}"
        assert f["severity"] in _SEVERITIES, f"{f['id']} bad severity {f['severity']!r}"
        assert f["tier"] in _TIERS, f"{f['id']} bad tier {f['tier']!r}"
        assert f["status"] in _STATUSES, f"{f['id']} bad status {f['status']!r}"
        assert isinstance(f["locations"], list) and f["locations"], f"{f['id']} needs locations"


def test_ids_unique():
    ids = [f["id"] for f in _findings()]
    dupes = {i for i in ids if ids.count(i) > 1}
    assert not dupes, f"duplicate finding ids: {dupes}"


def test_fixed_or_verified_findings_name_a_real_test():
    """A finding may only claim fixed/verified if it names a regression_test
    whose file exists. The check gate, not the prose."""
    for f in _findings():
        if f["status"] in ("fixed", "verified"):
            rt = f.get("regression_test")
            assert rt, f"{f['id']} is {f['status']} but has no regression_test"
            test_path = rt.split("::", 1)[0]
            assert (_REPO_ROOT / test_path).exists(), (
                f"{f['id']} names regression_test {test_path!r} which does not exist"
            )
