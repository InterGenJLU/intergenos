# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Fail-closed coverage for the package-manager execution inventory."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "scripts/lib/pkm_execution_inventory.py"
INVENTORY_PATH = REPO_ROOT / "config/pkm-execution-inventory.tsv"
PROCESS_PATH = REPO_ROOT / "config/pkm-process-calls.tsv"
SHAPES_PATH = REPO_ROOT / "config/pkm-execution-shapes.tsv"
SAFE_LAUNCH_PATH = REPO_ROOT / "config/pkm-safe-python-launchers.tsv"


def _load_module():
    spec = importlib.util.spec_from_file_location("pkm_execution_inventory_test", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


inventory = _load_module()


def _write_tsv(path: Path, columns, rows):
    values = ["\t".join(columns)]
    values.extend("\t".join(row[column] for column in columns) for row in rows)
    path.write_text("\n".join(values) + "\n")


def _inventory_row(key="canonical:demo"):
    row = {column: "reviewed" for column in inventory.INVENTORY_COLUMNS}
    row.update({
        "key": key,
        "legacy_id": "none",
        "kind": "canonical",
        "owner": "demo",
        "source": "pkm/hooks.py",
        "selector": "demo",
        "target": "/usr/bin/demo",
        "fingerprint": "a" * 64,
        "shape": "demo",
    })
    return row


def test_real_tree_matches_the_committed_contract():
    issues, surfaces, calls, shapes, safe_launchers = inventory.check_tree(
        REPO_ROOT, INVENTORY_PATH, PROCESS_PATH, SHAPES_PATH, SAFE_LAUNCH_PATH
    )
    assert issues == []
    assert surfaces == 185
    assert calls > 0
    assert shapes == 57
    assert safe_launchers == 5


def test_semantic_noop_classifier_has_both_mutation_controls():
    assert inventory.is_semantic_noop("set -e\n:\nreturn 0")
    assert not inventory.is_semantic_noop("set -e\n/usr/bin/false")
    assert not inventory.is_semantic_noop("probe=$(/usr/bin/false)")
    assert not inventory.is_semantic_noop("probe=`/usr/bin/false`")


def test_empty_inventory_is_an_error(tmp_path):
    path = tmp_path / "empty.tsv"
    path.write_text("\t".join(inventory.INVENTORY_COLUMNS) + "\n")
    with pytest.raises(inventory.InventoryError, match="zero contract rows"):
        inventory.load_inventory(path)


def test_malformed_inventory_header_is_an_error(tmp_path):
    path = tmp_path / "malformed.tsv"
    path.write_text("key\tkind\ncanonical:demo\tcanonical\n")
    with pytest.raises(inventory.InventoryError, match="columns"):
        inventory.load_inventory(path)


def test_duplicate_inventory_key_is_an_error(tmp_path):
    path = tmp_path / "duplicate.tsv"
    row = _inventory_row()
    _write_tsv(path, inventory.INVENTORY_COLUMNS, [row, row])
    with pytest.raises(inventory.InventoryError, match="duplicates key"):
        inventory.load_inventory(path)


def test_unknown_inventory_kind_is_an_error(tmp_path):
    path = tmp_path / "unknown.tsv"
    row = _inventory_row()
    row["kind"] = "unreviewed"
    _write_tsv(path, inventory.INVENTORY_COLUMNS, [row])
    with pytest.raises(inventory.InventoryError, match="unknown inventory kind"):
        inventory.load_inventory(path)


def test_alias_process_call_is_discovered(tmp_path):
    pkm_dir = tmp_path / "pkm"
    pkm_dir.mkdir()
    (pkm_dir / "alias.py").write_text(
        "import subprocess as child_process\n"
        "def launch():\n"
        "    return child_process.run(['/usr/bin/true'])\n"
    )
    calls = inventory.scan_python_process_calls(pkm_dir)
    assert len(calls) == 1
    call = next(iter(calls.values()))
    assert call.module == "pkm/alias.py"
    assert call.canonical_api == "subprocess.run"
    assert call.command_builder == "['/usr/bin/true']"


def test_fabricated_process_edge_is_named_and_refused(tmp_path):
    scratch = tmp_path / "pkm"
    shutil.copytree(REPO_ROOT / "pkm", scratch)
    (scratch / "fabricated.py").write_text(
        "import subprocess\n"
        "def launch():\n"
        "    return subprocess.run(['/usr/bin/false'])\n"
    )
    expected = inventory.load_process_contract(PROCESS_PATH)
    actual = inventory.scan_python_process_calls(scratch)
    semantic = set(inventory.load_inventory(INVENTORY_PATH))
    issues = inventory.compare_process_calls(expected, actual, semantic)
    assert any(
        "pkm/fabricated.py:launch" in issue and "subprocess.run" in issue
        for issue in issues
    )


def test_fabricated_process_edge_fails_the_full_checker(tmp_path):
    scratch = tmp_path / "tree"
    scratch.mkdir()

    def link_or_copy(source, destination):
        try:
            os.link(source, destination)
        except OSError:
            shutil.copy2(source, destination)

    for name in ("assets", "config", "igos-build", "packages", "pkm", "scripts"):
        shutil.copytree(
            REPO_ROOT / name,
            scratch / name,
            copy_function=link_or_copy,
            symlinks=True,
        )
    (scratch / "pkm/fabricated.py").write_text(
        "import subprocess as child_process\n"
        "def launch():\n"
        "    return child_process.run(['/usr/bin/false'])\n"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-P",
            str(REPO_ROOT / "scripts/check-pkm-execution-inventory.py"),
            "--root",
            str(scratch),
            "--inventory",
            str(scratch / "config/pkm-execution-inventory.tsv"),
            "--process-calls",
            str(scratch / "config/pkm-process-calls.tsv"),
            "--shapes",
            str(scratch / "config/pkm-execution-shapes.tsv"),
            "--safe-launchers",
            str(scratch / "config/pkm-safe-python-launchers.tsv"),
        ],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 1
    assert "pkm/fabricated.py:launch" in result.stdout
    assert "subprocess.run" in result.stdout


def test_configured_only_and_tree_only_surfaces_both_fail():
    expected = inventory.load_inventory(INVENTORY_PATH)
    actual = inventory.census_tree(REPO_ROOT)
    shapes = inventory.load_shapes(SHAPES_PATH)

    missing_key = next(iter(sorted(actual)))
    without_live = dict(actual)
    without_live.pop(missing_key)
    missing = inventory.compare_surfaces(expected, without_live, shapes)
    assert any(f"configured surface missing from tree: {missing_key}" in issue for issue in missing)

    extra = dict(actual)
    extra["canonical:fabricated"] = inventory.Surface(
        key="canonical:fabricated",
        kind="canonical",
        owner="fabricated",
        source="pkm/fabricated.py",
        selector="fabricated",
        target="/usr/bin/false",
        fingerprint="b" * 64,
    )
    unexpected = inventory.compare_surfaces(expected, extra, shapes)
    assert any("unlisted tree surface: canonical:fabricated" in issue for issue in unexpected)


def test_body_change_with_the_same_selector_fails_fingerprint():
    expected = inventory.load_inventory(INVENTORY_PATH)
    actual = inventory.census_tree(REPO_ROOT)
    shapes = inventory.load_shapes(SHAPES_PATH)
    key = next(key for key in sorted(actual) if key.startswith("archive:"))
    changed = dict(actual)
    changed[key] = replace(actual[key], fingerprint="0" * 64)
    issues = inventory.compare_surfaces(expected, changed, shapes)
    assert any(f"surface {key} fingerprint changed" in issue for issue in issues)


def test_archive_row_with_unknown_shape_fails():
    expected = inventory.load_inventory(INVENTORY_PATH)
    actual = inventory.census_tree(REPO_ROOT)
    key = next(key for key in sorted(expected) if key.startswith("archive:"))
    changed = {name: dict(row) for name, row in expected.items()}
    changed[key]["shape"] = "not-a-reviewed-shape"
    issues = inventory.compare_surfaces(changed, actual, inventory.load_shapes(SHAPES_PATH))
    assert any("unknown archive shape" in issue for issue in issues)


def test_declared_eula_helper_without_a_source_is_an_error(tmp_path):
    manifest = tmp_path / "packages/extra/demo/package.yml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("name: demo\neula_helper: demo-eula\n")
    with pytest.raises(inventory.InventoryError, match="resolves to 0 source files"):
        inventory._scan_eula_helpers(tmp_path, {})


def test_shell_true_process_call_is_refused(tmp_path):
    pkm_dir = tmp_path / "pkm"
    pkm_dir.mkdir()
    (pkm_dir / "shell.py").write_text(
        "import subprocess\n"
        "subprocess.run('/usr/bin/true', shell=True)\n"
    )
    with pytest.raises(inventory.InventoryError, match="shell=True"):
        inventory.scan_python_process_calls(pkm_dir)


def test_safe_launcher_without_safe_path_mode_is_refused():
    expected = inventory.load_safe_launch_contract(SAFE_LAUNCH_PATH)
    actual = inventory.scan_safe_launchers(REPO_ROOT)
    key = "safe-launch:pkm:package"
    changed = dict(actual)
    changed[key] = replace(
        actual[key], expected_prefix='exec /usr/bin/python3 -m pkm "$@"'
    )
    issues = inventory.compare_safe_launchers(expected, changed)
    assert any("does not enable Python safe-path mode" in issue for issue in issues)


def test_pre_push_runs_gate_on_candidate_blobs_and_cleans_first():
    hook = (REPO_ROOT / ".githooks/pre-push").read_text()
    for relative in (
        "scripts/check-pkm-execution-inventory.py",
        "scripts/lib/pkm_execution_inventory.py",
        "config/pkm-execution-inventory.tsv",
        "config/pkm-execution-shapes.tsv",
        "config/pkm-process-calls.tsv",
        "config/pkm-safe-python-launchers.tsv",
    ):
        assert relative in hook
    assert 'git ls-tree "$RELCHECK_TARGET"' in hook
    assert '"$PY_BIN" -P' in hook
    cleanup = hook.index("cleanup_relcheck_worktree\n    trap - EXIT HUP INT TERM")
    verdict = hook.index('if [ "$PKM_INV_RC" -eq 1 ]')
    assert cleanup < verdict
