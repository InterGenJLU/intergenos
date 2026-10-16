# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Execute the real shell hook functions over disposable inputs."""
import os
from pathlib import Path
import re
import shlex
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


def function(file, name):
    text = (ROOT / 'scripts' / file).read_text()
    match = re.search(rf'^{name}\(\) \{{\n.*?^\}}$', text, re.M | re.S)
    assert match, f'{file} must define {name}'
    return match.group()


def run(tmp_path, body, env=None):
    script = tmp_path / 'fixture.sh'
    script.write_text('set -eu\nset -o pipefail\n' + body)
    return subprocess.run(['/usr/bin/bash', str(script)], cwd=tmp_path,
                          env={**os.environ, **(env or {})}, text=True,
                          capture_output=True, timeout=15)


def shell_vars(**values):
    return ''.join(f'{key}={shlex.quote(str(value))}\n' for key, value in values.items())


@pytest.mark.parametrize('driver,builder', [
    ('chroot-build-ch8.sh', 'build_ch8_package'),
    ('chroot-build-core-extra.sh', 'build_core_package'),
    ('chroot-build-base.sh', 'build_base_package'),
])
@pytest.mark.parametrize('verdict', [0, 1])
def test_transcript_hook_precedes_install_and_halts(driver, builder, verdict, tmp_path):
    package = tmp_path / 'sample'; package.mkdir()
    (package / 'build.sh').write_text('configure() { :; }\nbuild() { :; }\ncheck() { :; }\n')
    logs = tmp_path / 'logs'; logs.mkdir()
    body = shell_vars(IGOS_PACKAGES=tmp_path, IGOS_LOGS=logs, IGOS_SOURCES=tmp_path,
                      IGOS_TRACE_LIB_LOADED=0, PKG_PHASE_RC=0, VERDICT=verdict)
    body += r'''
log() { printf '%s\n' "$*"; }
get_package_sha256() { printf 'fixture\n'; }
verify_source_checksum() { :; }
extract_source() { :; }
apply_package_patches() { :; }
lib32_env_scrub() { :; }
source_profile_d() { :; }
pkg_run_phase() { PKG_PHASE_RC=0; printf 'PHASE %s\n' "$1"; }
pkg_trace_finish() { :; }
get_package_release() { printf '1\n'; }
pkg_configure_drift() { printf 'GATE %s\n' "$1"; return "$VERDICT"; }
pkg_install() { printf 'INSTALLED\n'; }
rm() { :; }
mkdir() { :; }
cd() { :; }
'''
    body += function(driver, builder) + f'\n{builder} sample sample 1 fixture.tar.gz fixture\n'
    result = run(tmp_path, body)
    output = result.stdout + result.stderr
    assert 'GATE transcript' in output, output
    assert output.index('PHASE check') < output.index('GATE transcript')
    if verdict:
        assert result.returncode != 0, output
        assert 'INSTALLED' not in output
    else:
        assert result.returncode == 0, output
        assert output.index('GATE transcript') < output.index('INSTALLED')


@pytest.mark.parametrize('verdict', [0, 1])
def test_archive_hook_precedes_deploy_and_registration(verdict, tmp_path):
    body = shell_vars(IGOS_TRACE_LIB_LOADED=0, PKG_CONFIGURE_DRIFT_REQUIRED=1,
                      IGOS_PKG_ARCHIVES=tmp_path, IGOS_PKG_DB=tmp_path, VERDICT=verdict)
    body += r'''
pkg_log() { :; }
pkg_init() { :; }
pkg_stage() { :; }
pkg_manifest() { printf marker > "$IGOS_PKG_DB/$1-$2"; }
pkg_archive() { printf archive > "$IGOS_PKG_ARCHIVES/$1-$2-$3.igos.tar.gz"; printf 'SEALED\n'; }
pkg_configure_drift() { printf 'GATE %s %s\n' "$1" "$3"; return "$VERDICT"; }
pkg_deploy() { printf 'DEPLOYED\n'; }
pkg_run_pkm_single_flight() { printf 'REGISTERED\n'; }
pkg_retire_superseded_archives() { :; }
pkg_cleanup() { :; }
pkm() { :; }
'''
    body += function('pkg-functions.sh', 'pkg_rebaseline_config_files') + '\n'
    body += function('pkg-functions.sh', 'pkg_install') + '\npkg_install sample 1 fixture 2\n'
    result = run(tmp_path, body)
    output = result.stdout + result.stderr
    assert 'GATE archive' in output, output
    assert 'sample-1-2.igos.tar.gz' in output
    assert output.index('SEALED') < output.index('GATE archive')
    if verdict:
        assert result.returncode != 0
        assert 'DEPLOYED' not in output
        assert 'REGISTERED' not in output
        assert not (tmp_path / 'sample-1').exists()
        assert not (tmp_path / 'sample-1-2.igos.tar.gz').exists()
        # The refused build leaves the store as the Python builder's failure
        # path does: the completion marker gone, the archive beside the
        # store's archives as <archive>.failed (the one shape build-squashfs
        # Step 4.6 sweeps), and no directory of its own inside the store —
        # five such directories rode the lineage into the R001.3 mint and
        # the ownership gate refused the image over them (2026-10-16).
        assert (tmp_path / 'sample-1-2.igos.tar.gz.failed').read_text() == 'archive'
        assert list(tmp_path.glob('.configure-drift-failed.*')) == []
        assert 'kept as' in output and 'sample-1-2.igos.tar.gz.failed' in output
    else:
        assert result.returncode == 0, output
        assert output.index('GATE archive') < output.index('DEPLOYED') < output.index('REGISTERED')


def _manifest_with(entries):
    return 'PACKAGE NAME: sample-1\nPACKAGE VERSION: 1\nFILE LIST:\n' + ''.join(e + '\n' for e in entries)


@pytest.mark.parametrize('entries,expected', [
    # Every hashed /etc regular file is re-baselined, in manifest order; a
    # symbolic link (no hash), a directory and a non-/etc file are not.
    (['etc/', 'etc/demo.conf sha256:' + 'a' * 64, 'etc/link', 'etc/sub/',
      'etc/sub/two.conf sha256:' + 'b' * 64, 'usr/bin/demo sha256:' + 'c' * 64],
     ['/etc/demo.conf', '/etc/sub/two.conf']),
    # A path with a space keeps it whole.
    (['etc/with space.conf sha256:' + 'd' * 64], ['/etc/with space.conf']),
    # No hashed /etc entry: no pkm call at all.
    (['usr/bin/demo sha256:' + 'c' * 64, 'etc/'], []),
])
def test_install_rebaselines_the_etc_files_it_registered(entries, expected, tmp_path):
    body = shell_vars(IGOS_TRACE_LIB_LOADED=0, PKG_CONFIGURE_DRIFT_REQUIRED=0,
                      IGOS_PKG_ARCHIVES=tmp_path, IGOS_PKG_DB=tmp_path,
                      MANIFEST_BODY=_manifest_with(entries))
    body += r'''
pkg_log() { printf '%s\n' "$*"; }
pkg_error() { printf '%s\n' "$*" >&2; }
pkg_init() { :; }
pkg_stage() { :; }
pkg_manifest() { printf '%s' "$MANIFEST_BODY" > "$IGOS_PKG_DB/$1-$2"; }
pkg_archive() { printf archive > "$IGOS_PKG_ARCHIVES/$1-$2-$3.igos.tar.gz"; }
pkg_deploy() { printf 'DEPLOYED\n'; }
pkg_run_pkm_single_flight() {
    printf 'PKM'; printf ' [%s]' "$@"; printf '\n'
    if [ "$1" = refresh-baseline ]; then shift; for p in "$@"; do printf '  refreshed baseline for %s -> stock...\n' "$p"; done; fi
}
pkg_retire_superseded_archives() { :; }
pkg_cleanup() { :; }
pkm() { :; }
'''
    body += function('pkg-functions.sh', 'pkg_rebaseline_config_files') + '\n'
    body += function('pkg-functions.sh', 'pkg_install') + '\npkg_install sample 1 fixture 2\n'
    result = run(tmp_path, body)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    calls = [line for line in output.splitlines() if line.startswith('PKM')]
    assert calls[0] == 'PKM [import]', output
    if expected:
        assert calls[1:] == ['PKM [refresh-baseline]' + ''.join(f' [{p}]' for p in expected)], output
        assert output.index('DEPLOYED') < output.index('PKM [import]') < output.index('PKM [refresh-baseline]')
    else:
        assert calls == ['PKM [import]'], output


@pytest.mark.parametrize('runner', [
    # The real runner returns 0 whatever pkm did and relays pkm's lines: a
    # path pkm does not track is read from those lines, not from a code.
    r'''pkg_run_pkm_single_flight() { if [ "$1" = refresh-baseline ]; then printf '  /etc/demo.conf: not tracked as a config file\n'; fi; return 0; }''',
    # A runner that fails outright fails the install too.
    r'''pkg_run_pkm_single_flight() { if [ "$1" = refresh-baseline ]; then printf 'REFUSED\n'; return 1; fi; }''',
])
def test_a_failed_rebaseline_fails_the_install(runner, tmp_path):
    body = shell_vars(IGOS_TRACE_LIB_LOADED=0, PKG_CONFIGURE_DRIFT_REQUIRED=0,
                      IGOS_PKG_ARCHIVES=tmp_path, IGOS_PKG_DB=tmp_path,
                      MANIFEST_BODY=_manifest_with(['etc/demo.conf sha256:' + 'a' * 64]))
    body += r'''
pkg_log() { printf '%s\n' "$*"; }
pkg_error() { printf '%s\n' "$*" >&2; }
pkg_init() { :; }
pkg_stage() { :; }
pkg_manifest() { printf '%s' "$MANIFEST_BODY" > "$IGOS_PKG_DB/$1-$2"; }
pkg_archive() { :; }
pkg_deploy() { :; }
''' + runner + r'''
pkg_retire_superseded_archives() { printf 'RETIRED\n'; }
pkg_cleanup() { :; }
pkm() { :; }
'''
    body += function('pkg-functions.sh', 'pkg_rebaseline_config_files') + '\n'
    body += function('pkg-functions.sh', 'pkg_install') + '\npkg_install sample 1 fixture 2\n'
    result = run(tmp_path, body)
    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert 'RETIRED' not in output
    assert 'rebaseline: 0 of 1' in output or 'REFUSED' in output


def test_record_mode_writes_paths_without_python(tmp_path):
    recipe = tmp_path / 'package.yml'; recipe.write_text('name: sample\n')
    log = tmp_path / 'sample-ch8-20260101-000000.log'; log.write_text('checking for x... yes\n')
    body = shell_vars(PKG_TEMPLATE_DIR=tmp_path, IGOS_CONFIGURE_DRIFT_MODE='record',
                      IGOS_CONFIGURE_DRIFT_RECORD=tmp_path / 'records')
    body += 'python3() { printf "UNEXPECTED PYTHON\\n"; return 42; }\n'
    body += function('pkg-functions.sh', 'pkg_configure_drift')
    body += '\npkg_configure_drift transcript sample ' + shlex.quote(str(log)) + '\n'
    result = run(tmp_path, body)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'UNEXPECTED' not in result.stdout
    assert (tmp_path / 'records/sample.tsv').read_text() == f'transcript\tsample\t{log}\t{recipe}\n'


def test_enforce_missing_interpreter_is_red(tmp_path):
    body = shell_vars(PKG_TEMPLATE_DIR=tmp_path, IGOS_CONFIGURE_DRIFT_MODE='enforce')
    body += function('pkg-functions.sh', 'pkg_configure_drift')
    body += '\nPATH=/absent\npkg_configure_drift transcript sample /absent\n'
    result = run(tmp_path, body)
    assert result.returncode != 0
    assert 'RED configure-drift: python3 is absent' in result.stderr


@pytest.mark.parametrize('checkpoint,start,populated,expected', [
    ('true', '', False, 'record'), ('true', 'core', True, 'enforce'),
    ('false', 'base', True, 'enforce'), ('true', '', True, 'refuse'),
])
def test_mode_is_fresh_record_or_resume_enforce(checkpoint, start, populated, expected, tmp_path):
    (tmp_path / 'root').mkdir()
    archive_dir = tmp_path / 'root/var/lib/igos/archives'
    if populated:
        archive_dir.mkdir(parents=True)
        (archive_dir / 'sample-1.igos.tar.gz').write_bytes(b'populated')
    (tmp_path / 'build').mkdir()
    (tmp_path / 'build/configure-drift-reference.json').write_text('fixture reference')
    body = shell_vars(CHECKPOINT=checkpoint, START_AT=start, IGOS=tmp_path / 'root',
                      SCRIPTS=tmp_path / 'scripts', IGOS_TRACE_RUNID='fixture')
    body += 'log() { printf "%s\\n" "$*"; }\npython3() { printf "VALIDATE %s\\n" "$*"; }\n'
    body += function('build-intergenos.sh', 'configure_drift_setup')
    body += '\nconfigure_drift_setup\nprintf "MODE %s\\n" "$IGOS_CONFIGURE_DRIFT_MODE"\n'
    result = run(tmp_path, body)
    output = result.stdout + result.stderr
    if expected == 'refuse':
        assert result.returncode != 0
        assert 'requires an empty root' in output
    else:
        assert result.returncode == 0, output
        assert f'MODE {expected}' in output
        assert ('VALIDATE' in output) == (expected == 'enforce')
        if expected == 'enforce':
            assert (tmp_path / 'root/mnt/intergenos/build/configure-drift-reference.json').read_text() == 'fixture reference'


def test_reference_validation_failure_halts_setup(tmp_path):
    body = shell_vars(CHECKPOINT='false', START_AT='core', IGOS=tmp_path,
                      SCRIPTS=tmp_path / 'scripts', IGOS_TRACE_RUNID='fixture')
    body += 'log() { :; }\npython3() { return 2; }\n'
    body += function('build-intergenos.sh', 'configure_drift_setup')
    result = run(tmp_path, body + '\nconfigure_drift_setup\nprintf CONTINUED\n')
    assert result.returncode == 2
    assert 'CONTINUED' not in result.stdout


def test_populated_root_without_archive_bank_cannot_record(tmp_path):
    (tmp_path / 'root/usr').mkdir(parents=True)
    body = shell_vars(CHECKPOINT='true', START_AT='', IGOS=tmp_path / 'root',
                      SCRIPTS=tmp_path / 'scripts', IGOS_TRACE_RUNID='fixture')
    body += 'log() { printf "%s\\n" "$*"; }\npython3() { return 0; }\n'
    body += function('build-intergenos.sh', 'configure_drift_setup')
    result = run(tmp_path, body + '\nconfigure_drift_setup\nprintf CONTINUED\n')
    assert result.returncode != 0
    assert 'requires an empty root' in result.stdout
    assert 'CONTINUED' not in result.stdout


def test_chroot_entry_forwards_only_named_gate_variables(tmp_path):
    text = (ROOT / 'scripts/chroot-enter.sh').read_text()
    entry = text[text.index('chroot "$IGOS" /usr/bin/env -i'):]
    body = shell_vars(IGOS=tmp_path / 'root', TERM='dumb', HOST_TZ='UTC', JOBS=1,
                      CHROOT_CMD='/bin/bash fixture.sh', IGOS_CONFIGURE_DRIFT_MODE='record',
                      IGOS_CONFIGURE_DRIFT_INDEX='/reference with spaces/index.json',
                      IGOS_CONFIGURE_DRIFT_RECORD='/records', UNRELATED_VALUE='not forwarded')
    body += 'chroot() { printf "%s\\n" "$@"; }\n' + entry
    result = run(tmp_path, body)
    assert result.returncode == 0, result.stderr
    args = result.stdout.splitlines()
    assert 'IGOS_CONFIGURE_DRIFT_MODE=record' in args
    assert 'IGOS_CONFIGURE_DRIFT_INDEX=/reference with spaces/index.json' in args
    assert 'IGOS_CONFIGURE_DRIFT_RECORD=/records' in args
    assert not any('UNRELATED_VALUE' in arg for arg in args)


def test_host_records_a_candidate_after_each_selected_tier(tmp_path):
    body = shell_vars(IGOS_CONFIGURE_DRIFT_MODE='record', IGOS=tmp_path / 'root',
                      SCRIPTS=tmp_path / 'repo/scripts', IGOS_CONFIGURE_DRIFT_RECORD='/records', IGOS_TRACE_RUNID='fixture')
    body += 'python3() { printf "%s\\n" "$@"; }\n'
    body += function('build-intergenos.sh', 'configure_drift_record_tier')
    for phase in ['core', 'core-extra', 'base', 'desktop', 'extra', 'compute', 'ai']:
        body += '\nconfigure_drift_record_tier ' + phase
    result = run(tmp_path, body)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines().count('record-index') == 7
    assert result.stdout.splitlines().count(str(tmp_path / 'root/records')) == 7
    assert str(tmp_path / 'repo/build/configure-drift-reference.json') in result.stdout


def test_failed_root_inspection_cannot_select_record_mode(tmp_path):
    (tmp_path / 'root').mkdir()
    body = shell_vars(CHECKPOINT='true', START_AT='', IGOS=tmp_path / 'root',
                      SCRIPTS=tmp_path / 'scripts', IGOS_TRACE_RUNID='fixture')
    body += 'log() { printf "%s\\n" "$*"; }\nfind() { return 2; }\n'
    body += function('build-intergenos.sh', 'configure_drift_setup')
    result = run(tmp_path, body + '\nconfigure_drift_setup\nprintf CONTINUED\n')
    assert result.returncode != 0
    assert 'could not verify the empty root' in result.stdout
    assert 'CONTINUED' not in result.stdout
