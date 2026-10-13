# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Configure answers and sealed archive contents are independent fatal signals."""
import hashlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GATE = ROOT / 'scripts/check-configure-drift.py'


@pytest.fixture
def gate():
    assert GATE.is_file(), 'The configure-drift gate must exist'
    spec = importlib.util.spec_from_file_location('configure_drift_test', GATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('old,new', [
    ('checking for optional.h... no', 'checking for optional.h... (cached) yes'),
    ('Run-time dependency optional found: NO (tried pkgconfig)',
     'Run-time dependency optional found: YES 1.2'),
    ('Library optional found: NO', 'Library optional found: YES'),
    ('Program doxygen found: NO', 'Program doxygen found: YES (/usr/bin/doxygen)'),
    ('checking for cargo... no', 'checking for cargo... cargo'),
    ('Has header "optional.h" : NO', 'Has header "optional.h" : YES'),
    ('-- Could NOT find Optional (missing: OPTIONAL_LIBRARY)',
     '-- Found Optional: /usr/lib/liboptional.so (found version "1.0")'),
])
def test_detection_flips_in_both_directions(gate, old, new):
    for before, after in [(old, new), (new, old)]:
        findings, allowed = gate.compare_transcript('sample', gate.parse_transcript(before),
                                                  gate.parse_transcript(after), [])
        assert len(findings) == 1
        assert not allowed


def test_one_sided_questions_and_versions_are_not_flips(gate):
    old = 'checking for old.h... no\nRun-time dependency optional found: YES 1.0'
    new = 'checking for new.h... yes\nRun-time dependency optional found: YES 2.0'
    assert gate.compare_transcript('sample', gate.parse_transcript(old),
                                   gate.parse_transcript(new), []) == ([], [])


def test_repeated_questions_do_not_hide_a_flip(gate):
    old = 'checking for optional.h... yes\nchecking for optional.h... no'
    new = 'checking for optional.h... no\nchecking for optional.h... no'
    assert len(gate.compare_transcript('sample', gate.parse_transcript(old),
                                      gate.parse_transcript(new), [])[0]) == 1


def test_seed_covers_tinfo_but_not_other_libraries(gate):
    allow = gate.load_allowlist(ROOT / 'config/configure-drift-allowlist.txt')
    old = 'checking for tgetent in -ltinfo... no\nchecking which library has the termcap functions... using libcurses'
    new = 'checking for tgetent in -ltinfo... yes\nchecking which library has the termcap functions... using libtinfo'
    findings, allowed = gate.compare_transcript('bash', gate.parse_transcript(old),
                                               gate.parse_transcript(new), allow)
    assert not findings
    assert len(allowed) == 2
    assert gate.compare_transcript('vim', {'for gtk': ['no']}, {'for gtk': ['yes']}, allow)[0]


def test_allowlist_requires_reason_and_valid_fields(gate, tmp_path):
    path = tmp_path / 'allowlist'
    for body in ['sample\ttranscript: for x :: no -> yes\t\n', 'sample\tinvalid selector\treason\n']:
        path.write_text(body)
        with pytest.raises(ValueError):
            gate.load_allowlist(path)


def snapshots():
    before = dict(name='sample', version='1', release='1', needed=['libc.so.6'],
                  members=['usr/bin/sample'], provides=[])
    after = {**before, 'release': '2'}
    return before, after


def test_undeclared_or_unknown_gained_library_is_red(gate):
    before, after = snapshots()
    after['needed'] = ['libc.so.6', 'liboptional.so.1']
    assert gate.compare_archive(before, after, {'liboptional.so.1': ['optional']}, set())
    assert gate.compare_archive(before, after, {}, {'optional'})
    assert not gate.compare_archive(before, after, {'liboptional.so.1': ['optional']}, {'optional'})


def test_member_changes_need_a_version_change_not_a_release_change(gate):
    before, after = snapshots()
    for members in [[], ['usr/bin/sample', 'usr/bin/gui']]:
        after['members'] = members
        assert gate.compare_archive(before, after, {}, set())
        after['version'] = '2'
        assert not gate.compare_archive(before, after, {}, set())
        after['version'] = '1'


def make_archive(path, members):
    with tarfile.open(path, 'w:gz') as tf:
        data = b'pkgname=sample\npkgver=1\npkgrel=2\n'
        info = tarfile.TarInfo('./.PKGINFO'); info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
        for name, data in members:
            info = tarfile.TarInfo(name); info.size = len(data)
            tf.addfile(info, io.BytesIO(data))


def test_archive_reads_real_elf_and_members_without_extracting(gate, tmp_path):
    archive = tmp_path / 'sample.tar.gz'
    make_archive(archive, [('usr/bin/sample', Path('/usr/bin/true').read_bytes()),
                           ('usr/share/doc/readme', b'example')])
    result = gate.archive_snapshot(archive)
    assert result['name'] == 'sample'
    assert result['version'] == '1'
    assert result['release'] == '2'
    assert result['needed']
    assert result['members'] == ['usr/bin/sample']
    assert result['archive_sha256'] == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert not (tmp_path / 'usr').exists()


def test_archive_refuses_malformed_elf_and_unsafe_member(gate, tmp_path):
    archive = tmp_path / 'sample.tar.gz'
    for members in [[('usr/bin/sample', b'\x7fELFbroken')], [('../outside', b'bad')]]:
        make_archive(archive, members)
        with pytest.raises(ValueError):
            gate.archive_snapshot(archive)


def test_index_pin_refuses_changed_bytes(gate, tmp_path):
    index = tmp_path / 'reference.json'; pin = tmp_path / 'reference.sha256'
    index.write_text('{}\n'); pin.write_text('0' * 64 + '\n')
    with pytest.raises(ValueError, match='hash'):
        gate.load_index(index, pin)


def test_missing_reference_or_log_is_not_green(tmp_path):
    result = subprocess.run([sys.executable, '-I', str(GATE), 'check',
                             '--signal', 'transcript', '--package', 'sample',
                             '--recipe', str(tmp_path / 'package.yml'),
                             '--log', str(tmp_path / 'absent')], capture_output=True, text=True)
    assert result.returncode != 0
    assert 'ERROR' in result.stdout + result.stderr


def test_interleaving_stops_pair_attribution(gate):
    answers, coverage = gate.read_transcript('checking for stable... yes\nchecking for a... checking for b... yes\nchecking for later... no')
    assert answers == {'for stable': ['yes']}
    assert coverage == {'kind': 'interleaved-configure-output', 'line': 2}
    findings, _ = gate.compare_transcript('sample', {'for x': ['yes', 'no']},
                                          {'for x': ['no', 'yes']}, [])
    assert not findings


def test_exact_unreadable_allowance_keeps_other_members_enforced(gate):
    record = {'unreadable_members': [{'member': 'usr/lib/test/bad', 'error': 'invalid ELF'}]}
    entries = [('sample', 'archive-unreadable', 'usr/lib/test/bad', 'unreadable', 'test fixture')]
    assert gate.archive_coverage('sample', record, entries)[1]
    assert gate.archive_coverage('different', record, entries)[0]
    record['unreadable_members'].append({'member': 'usr/bin/sample', 'error': 'invalid ELF'})
    assert len(gate.archive_coverage('sample', record, entries)[0]) == 1


def test_fresh_record_candidate_is_usable_and_does_not_edit_reference(gate, tmp_path):
    recipe = tmp_path / 'package.yml'
    recipe.write_text("name: sample\nversion: '1'\nrelease: 2\ndescription: sample\nlicense: MIT\nbuild_style: custom\nsource: []\n")
    archive = tmp_path / 'sample-1-2.igos.tar.gz'
    make_archive(archive, [('usr/bin/sample', Path('/usr/bin/true').read_bytes())])
    log = tmp_path / 'sample-ch8-20260101-000000.log'
    log.write_text('checking for a... yes\n')
    records = tmp_path / 'records'; records.mkdir()
    record = records / 'sample.tsv'
    record.write_text(f'transcript\tsample\t{log}\t{recipe}\narchive\tsample\t{archive}\t{recipe}\n')
    candidate = tmp_path / 'candidate.json'
    assert gate.record_index(records, Path('/'), candidate, 'fixture') == 0
    index = gate.load_index(candidate)
    assert index['packages']['sample']['logs']['ch8']['sha256'] == hashlib.sha256(log.read_bytes()).hexdigest()
    result = subprocess.run([sys.executable, '-I', str(GATE), 'check', '--signal', 'both',
                             '--package', 'sample', '--recipe', str(recipe), '--log', str(log),
                             '--archive', str(archive), '--index', str(candidate)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    log.write_text('checking for a... no\n')
    result = subprocess.run([sys.executable, '-I', str(GATE), 'check', '--signal', 'transcript',
                             '--package', 'sample', '--recipe', str(recipe), '--log', str(log),
                             '--index', str(candidate)], capture_output=True, text=True)
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'yes -> no' in result.stdout
    log.write_text('checking for a... yes\nchecking for x... checking for y... no\nchecking for a... no\n')
    both = [sys.executable, '-I', str(GATE), 'check', '--signal', 'both',
            '--package', 'sample', '--recipe', str(recipe), '--log', str(log),
            '--archive', str(archive), '--index', str(candidate)]
    result = subprocess.run(both, capture_output=True, text=True)
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'interleaved configure output' in result.stdout
    assert 'GREEN sample: archive' in result.stdout
    assert 'yes -> no' not in result.stdout  # after the boundary, no pair is invented
    allow = tmp_path / 'reviewed-allowlist'
    allow.write_text('sample\ttranscript: archive-only\tparallel fixture; archive remains the verdict\n')
    result = subprocess.run(both + ['--allowlist', str(allow)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'TRANSCRIPT archive-only' in result.stdout
    make_archive(archive, [('usr/bin/sample', Path('/usr/bin/true').read_bytes()), ('usr/bin/extra', b'script')])
    result = subprocess.run(both + ['--allowlist', str(allow)], capture_output=True, text=True)
    assert result.returncode == 1
    assert 'RED sample: archive' in result.stdout
    make_archive(archive, [('usr/bin/sample', Path('/usr/bin/true').read_bytes())])
    helper_root = tmp_path / 'helper-root'
    helper_recipe = helper_root / 'packages/core/sample'
    helper_recipe.mkdir(parents=True)
    (helper_recipe / 'package.yml').write_bytes(recipe.read_bytes())
    (helper_root / 'scripts').mkdir()
    (helper_root / 'scripts/check-configure-drift.py').symlink_to(GATE)
    source = (ROOT / 'scripts/pkg-functions.sh').read_text()
    helper = re.search(r'^pkg_configure_drift\(\) \{\n.*?^\}', source, re.M | re.S).group()
    env = {**os.environ, 'PKG_TEMPLATE_DIR': str(helper_recipe),
           'IGOS_CONFIGURE_DRIFT_MODE': 'enforce', 'IGOS_CONFIGURE_DRIFT_INDEX': str(candidate)}
    log.write_text('checking for a... yes\n')
    shell = helper + '\npkg_configure_drift "$1" sample "$2"\n'
    result = subprocess.run(['/usr/bin/bash', '-c', shell, '--', 'transcript', str(log)],
                            env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    result = subprocess.run(['/usr/bin/bash', '-c', shell, '--', 'archive', str(archive)],
                            env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    log.write_text('checking for a... no\n')
    result = subprocess.run(['/usr/bin/bash', '-c', shell, '--', 'transcript', str(log)],
                            env=env, capture_output=True, text=True)
    assert result.returncode == 1, result.stdout + result.stderr
    log.unlink()
    result = subprocess.run([sys.executable, '-I', str(GATE), 'check', '--signal', 'transcript',
                             '--package', 'sample', '--recipe', str(recipe), '--log', str(log),
                             '--index', str(candidate)], capture_output=True, text=True)
    assert result.returncode == 2


def test_incomplete_records_cannot_make_a_candidate(gate, tmp_path):
    records = tmp_path / 'records'; records.mkdir()
    log = tmp_path / 'sample-ch8-20260101-000000.log'; log.write_text('checking for a... yes\n')
    recipe = tmp_path / 'package.yml'; recipe.write_text('name: sample\n')
    (records / 'sample.tsv').write_text(f'transcript\tsample\t{log}\t{recipe}\n')
    with pytest.raises(ValueError, match='incomplete'):
        gate.record_index(records, Path('/'), tmp_path / 'candidate.json', 'fixture')
    assert not (tmp_path / 'candidate.json').exists()


def test_supplemental_reference_needs_reviewed_identity_and_hash(gate, tmp_path):
    archives = tmp_path / 'archives'; archives.mkdir()
    make_archive(archives / 'sample-1.igos.tar.gz', [('usr/bin/sample', b'script')])
    logs = tmp_path / 'lineage'; logs.mkdir()
    later = tmp_path / 'later'; later.mkdir()
    log = later / 'sample-20260102-000000.log'; log.write_text('checking for a... no\n')
    selections = tmp_path / 'selections.json'
    digest = hashlib.sha256(log.read_bytes()).hexdigest()
    selections.write_text(json.dumps([dict(package='sample', log=log.name, sha256=digest, reason='added after the from-scratch')]))
    candidate = tmp_path / 'candidate.json'
    with pytest.raises(ValueError, match='requires --supplemental-logs'):
        gate.generate_index(archives, logs, candidate, 1, 'fixture', supplemental_references=selections)
    assert gate.generate_index(archives, logs, candidate, 1, 'fixture', later, selections) == 0
    data = gate.load_index(candidate)
    assert data['packages']['sample']['logs']['python']['reference_reason'] == 'added after the from-scratch'
    log.write_text('changed')
    with pytest.raises(ValueError, match='hash mismatch'):
        gate.generate_index(archives, logs, candidate, 1, 'fixture', later, selections)


def test_a_new_internal_library_is_supplied_by_the_same_archive(gate):
    before, after = snapshots()
    after['version'] = '2'
    after['needed'] = ['libc.so.6', 'libinternal.so.2']
    after['provides'] = ['libinternal.so.2']
    assert not gate.compare_archive(before, after, {}, set())


def test_staged_identity_and_member_count_are_checked(gate, tmp_path):
    archive = tmp_path / 'sample.tar.gz'
    make_archive(archive, [('usr/bin/sample', b'script')])
    row = gate.archive_snapshot(archive)
    row['logs'] = {}
    path = tmp_path / 'reference.json'
    data = dict(schema=2, lineage_id='fixture', member_count=1, packages={'sample': row})
    path.write_text(json.dumps(data))
    assert gate.load_index(path)['member_count'] == 1
    data['member_count'] = 2
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='member count'):
        gate.load_index(path)
    data['member_count'] = 1
    data['lineage_id'] = ''
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='lineage identity'):
        gate.load_index(path)


def test_a_question_containing_checking_twice_is_not_interleaved(gate):
    answers, coverage = gate.read_transcript('checking for compiler option needed when checking for declarations... none\nchecking use login and su access checking if PAM not used... no\n')
    assert coverage is None
    assert answers == {'use login and su access checking if PAM not used': ['no']}


def reference_without_sample(gate, tmp_path, lineage='fixture'):
    archive = tmp_path / 'other.tar.gz'
    make_archive(archive, [('usr/bin/other', b'script')])
    row = gate.archive_snapshot(archive)
    row['name'], row['logs'] = 'other', {}
    index = tmp_path / 'reference.json'
    index.write_text(json.dumps(dict(schema=2, lineage_id=lineage, member_count=1, packages={'other': row})))
    recipe = tmp_path / 'package.yml'
    recipe.write_text("name: sample\nversion: '1'\nrelease: 2\ndescription: sample\nlicense: MIT\nbuild_style: custom\nsource: []\n")
    log = tmp_path / 'sample-20260101-000000.log'
    log.write_text('checking for optional.h... yes\n')
    return index, recipe, log


def run_gate(*arguments):
    result = subprocess.run([sys.executable, '-I', str(GATE), *arguments], capture_output=True, text=True)
    return result.returncode, result.stdout + result.stderr


def test_new_package_allowance_is_exact_and_lineage_bound(gate, tmp_path):
    path = tmp_path / 'allowlist'
    path.write_text('sample\tnew-package: fixture\tadded after the lineage\n')
    assert gate.load_allowlist(path) == [('sample', 'new-package', 'fixture', 'absent', 'added after the lineage')]
    for body in ['samp*\tnew-package: fixture\treason\n', 'sample\tnew-package: \treason\n']:
        path.write_text(body)
        with pytest.raises(ValueError):
            gate.load_allowlist(path)


def test_a_package_absent_from_the_reference_fails_closed_unless_named(gate, tmp_path):
    index, recipe, log = reference_without_sample(gate, tmp_path)
    allowlist = tmp_path / 'allowlist'
    check = ('check', '--signal', 'both', '--package', 'sample', '--recipe', str(recipe),
             '--log', str(log), '--index', str(index), '--allowlist', str(allowlist))
    for body in ['# nothing named\n', 'sample\tnew-package: elsewhere\tanother lineage\n']:
        allowlist.write_text(body)
        code, output = run_gate(*check)
        assert code == 2 and 'missing reference package: sample' in output
    allowlist.write_text('sample\tnew-package: fixture\tadded after the lineage\n')
    code, output = run_gate(*check)
    assert code == 0
    assert 'ALLOW sample: TRANSCRIPT new package absent from reference lineage fixture; added after the lineage' in output
    assert 'ALLOW sample: ARCHIVE new package absent from reference lineage fixture' in output
    assert 'GREEN sample: both; 0 findings, 2 allowlisted' in output
    code, output = run_gate(*check[:2], 'transcript', *check[3:])
    assert code == 0 and output.count('ALLOW sample') == 1
    assert 'GREEN sample: transcript; 0 findings, 1 allowlisted' in output


def test_validate_refuses_a_stale_new_package_allowance(gate, tmp_path):
    index, recipe, log = reference_without_sample(gate, tmp_path)
    allowlist = tmp_path / 'allowlist'
    validate = ('validate', '--index', str(index), '--allowlist', str(allowlist))
    for body, fragment in [('other\tnew-package: fixture\tbut the reference carries it\n', 'the reference carries it'),
                           ('sample\tnew-package: elsewhere\twrong lineage\n', 'is not the staged reference fixture')]:
        allowlist.write_text(body)
        code, output = run_gate(*validate)
        assert code == 2 and 'stale new-package allowance' in output and fragment in output
    allowlist.write_text('sample\tnew-package: fixture\tadded after the lineage\n')
    code, output = run_gate(*validate)
    assert code == 0
    assert 'ALLOW new package sample: absent from lineage fixture; added after the lineage' in output
    assert 'VALID reference lineage fixture' in output
