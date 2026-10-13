# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The Python builder halts before install or deployment on gate refusal."""
import importlib
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from .factories import make_package

builder = importlib.import_module('igos-build.builder')


def executor(tmp_path):
    result = builder.BuildExecutor(tmp_path / 'work', tmp_path / 'logs',
                                   tmp_path / 'sources', tmp_path / 'patches', tmp_path / 'system')
    result.tracked = True
    result.pkg_archives = tmp_path / 'archives'
    result.pkg_staging = tmp_path / 'staging'
    result.pkg_db = tmp_path / 'db'
    for path in (result.pkg_archives, result.pkg_staging, result.pkg_db):
        path.mkdir()
    return result


@pytest.mark.parametrize('refused', ['transcript', 'archive', None])
def test_both_signals_order_and_fatality(tmp_path, monkeypatch, refused):
    ex = executor(tmp_path)
    template = tmp_path / 'packages/core/sample/package.yml'
    template.parent.mkdir(parents=True); template.write_text('name: sample\n')
    pkg = make_package(name='sample', template_path=template)
    events = []
    style = SimpleNamespace(
        all_phases=lambda pkg: [SimpleNamespace(name=name, commands=[name]) for name in ('configure', 'build', 'check', 'install')],
        post_install=lambda pkg: SimpleNamespace(commands=[]))
    monkeypatch.setattr(builder, 'get_style', lambda name: style)
    monkeypatch.setattr(ex, 'build_env', lambda pkg: {})
    monkeypatch.setattr(ex, 'extract_source', lambda pkg, work: work)
    monkeypatch.setattr(ex, 'run_command', lambda command, env, cwd: events.append(command) or 0)
    monkeypatch.setattr(ex, 'pkg_configure_drift', lambda pkg, signal: events.append('gate:' + signal) or signal != refused, raising=False)
    for method in ['overlay_package_files', 'bundle_license', 'run_validation', 'pkg_elf_audit',
                   'pkg_time64_audit', 'pkg_manifest', 'pkg_archive', 'pkg_deploy', 'pkg_verify',
                   'pkg_register_pkm_db', '_force_root_ownership', '_auto_derive_verify_paths_from_staging',
                   '_retire_superseded_archives', '_remove_failed_tracking_artifacts']:
        monkeypatch.setattr(ex, method, lambda *args, method=method, **kwargs: events.append(method) or True)
    ok = ex.build_package(pkg)
    assert ok == (refused is None), events
    assert events.index('check') < events.index('gate:transcript')
    if refused == 'transcript':
        assert 'install' not in events
        assert 'pkg_archive' not in events
    else:
        assert events.index('gate:transcript') < events.index('install')
        assert events.index('pkg_archive') < events.index('gate:archive')
        if refused == 'archive':
            assert 'pkg_deploy' not in events
            assert 'pkg_register_pkm_db' not in events
        else:
            assert events.index('gate:archive') < events.index('pkg_deploy')
    if refused:
        assert '_remove_failed_tracking_artifacts' in events


def test_gate_subprocess_failure_is_fatal_and_reported(tmp_path, monkeypatch):
    ex = executor(tmp_path)
    template = tmp_path / 'packages/core/sample/package.yml'
    template.parent.mkdir(parents=True); template.write_text('name: sample\n')
    scripts = tmp_path / 'scripts'; scripts.mkdir()
    (scripts / 'check-configure-drift.py').write_text('import sys\nprint("fixture refused")\nsys.exit(1)\n')
    pkg = make_package(name='sample', template_path=template)
    ex.logger.start_package(pkg.name, pkg.version, pkg.build_style)
    monkeypatch.setenv('IGOS_CONFIGURE_DRIFT_MODE', 'enforce')
    assert not ex.pkg_configure_drift(pkg, 'transcript')
    text = Path(ex.logger._log_path).read_text()
    assert 'fixture refused' in text
    assert 'exit 1' in text
    ex.logger.end_package(False)
