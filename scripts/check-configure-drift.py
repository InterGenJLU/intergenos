#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Compare configure answers and sealed contents with a staged build reference.

The reference contains archive identities, executable/library members, ELF
NEEDED and SONAME sets, and normalized detection answers from the build logs.
Only index generation reads the archive corpus and historical logs. Pre-flight
stages the private reference in build/; checking validates its lineage identity
and declared record count and prints its SHA256. An independent --pin is optional.
The public tree contains the checker and reasoned allowances, not reference data. Missing inputs and unreadable ELF data fail closed (exit 2).
Unallowlisted answer flips and undeclared gained libraries are RED (exit 1).
Executable/library member changes are RED at an unchanged recipe version.
An answer appearing on only one side is not a flip. Repeated questions compare
answer sets, not parallel output order; changed ambiguous sets fail closed.
Library losses are reported
as observations; the transcript signal independently detects detection losses.

Commands: index --archives DIR --logs DIR --output FILE --lineage-id ID;
record-index --records DIR --path-root ROOT --output FILE --lineage-id ID; validate;
check --signal transcript|archive|both --package NAME --recipe FILE [--log FILE]
[--archive FILE]. A generated index is a candidate until its provenance is reviewed.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import fnmatch
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INDEX = ROOT / 'build/configure-drift-reference.json'
DEFAULT_ALLOWLIST = ROOT / 'config/configure-drift-allowlist.txt'
LOG_NAME = re.compile(r'^(.*?)(?:-(ch8|ch10|core-extra|base))?-(\d{8}-\d{6})\.log$')
HEX = re.compile(r'[0-9a-f]{64}')


def file_hash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read_transcript(text):
    """Retain only pairs before any detected parallel-output interleaving."""
    answers = {}
    for number, line in enumerate(text.splitlines(), 1):
        line = re.sub(r'\x1b\[[0-9;]*m', '', line).strip()
        if len(re.findall(r'\bchecking ', line)) > 1:
            return dict(sorted(answers.items())), {'kind': 'parallel-configure-output', 'line': number}
        match = re.search(r'\bchecking (.+?)\.{3}\s*(.*?)\s*$', line)
        question = answer = None
        if match:
            question, value = match.groups()
            value = re.sub(r'^\(cached\)\s*', '', value).strip()
            if value.lower() in ('yes', 'no'):
                answer = value.lower()
            elif re.fullmatch(r'for [A-Za-z0-9_./+\-]+', question) and (value.startswith('/') or value == question[4:]):
                answer = 'yes'
            elif question == 'which library has the termcap functions':
                answer = value.removeprefix('using ')
            elif 'F_DUPFD_CLOEXEC' in question and value == 'needs runtime check':
                answer = value
        else:
            match = re.search(r'((?:Run-time |Build-time )?[Dd]ependency .+?|Library .+?|Program .+?|Has header .+?)\s+(?:found:|:)\s*(YES|NO)\b', line)
            if match:
                question, answer = match.group(1), match.group(2).lower()
                question = re.sub(r'^[Dd]ependency ', 'dependency ', question)
            else:
                match = re.match(r'(?:--\s*)?(Found|Could NOT find)\s+([^\s:]+)', line)
                if match:
                    question = 'cmake ' + match.group(2)
                    answer = 'yes' if match.group(1) == 'Found' else 'no'
        if answer is not None:
            question = re.sub(r'\s+', ' ', question).strip()
            answers.setdefault(question, []).append(answer)
    return dict(sorted(answers.items())), None


def parse_transcript(text):
    return read_transcript(text)[0]


def transcript_snapshot(path):
    raw = Path(path).read_bytes()
    answers, coverage = read_transcript(raw.decode(errors='replace'))
    return dict(name=Path(path).name, sha256=hashlib.sha256(raw).hexdigest(),
                answers=answers, coverage=coverage)


def load_allowlist(path):
    entries = []
    for number, line in enumerate(Path(path).read_text().splitlines(), 1):
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        fields = line.split('\t')
        if len(fields) != 3 or not all(field.strip() for field in fields):
            raise ValueError(f'{path}:{number}: expected package, check, reason separated by tabs; no entry without a reason')
        package, check, reason = fields
        if check == 'transcript: archive-only':
            if any(character in package for character in '*?[]'):
                raise ValueError(f'{path}:{number}: archive-only needs an exact package')
            entries.append((package, 'archive-only', '', '', reason))
        elif check.startswith('archive-unreadable: '):
            member = check.removeprefix('archive-unreadable: ')
            if normalized_member(member) != member or any(character in package + member for character in '*?[]'):
                raise ValueError(f'{path}:{number}: unreadable member needs exact package and path')
            entries.append((package, 'archive-unreadable', member, 'unreadable', reason))
        else:
            match = re.fullmatch(r'transcript: (.+) :: (.+) -> (.+)', check)
            if not match:
                raise ValueError(f'{path}:{number}: invalid check selector')
            question, before, after = match.groups()
            entries.append((package, question, before, after, reason))
    return entries


def compare_transcript(package, reference, current, allowlist):
    findings, allowed = [], []
    for question in sorted(reference.keys() & current.keys()):
        before, after = set(reference[question]), set(current[question])
        uncertain = (len(before) != 1 or len(after) != 1
                     or 'unresolved-interleaving' in before | after)
        if before == after:
            continue
        old, new = '|'.join(sorted(before)), '|'.join(sorted(after))
        if uncertain:
            detail = f'TRANSCRIPT unresolved repeated/interleaved question {question}: {old} -> {new}'
        else:
            detail = f'TRANSCRIPT {question}: {old} -> {new}'
        reasons = [reason for pkg, pattern, prior, next_answer, reason in allowlist
                   if fnmatch.fnmatchcase(package, pkg)
                   and fnmatch.fnmatchcase(question, pattern)
                   and fnmatch.fnmatchcase(old, prior)
                   and fnmatch.fnmatchcase(new, next_answer)]
        if reasons:
            allowed.append(detail + '; allowlisted: ' + '; '.join(reasons))
        else:
            findings.append(detail)
    return findings, allowed


def normalized_member(name):
    while name.startswith('./'):
        name = name[2:]
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or '\x00' in name:
        raise ValueError(f'unsafe archive member: {name!r}')
    return str(path)


def shipped_member(name):
    return (name.startswith(('usr/bin/', 'usr/sbin/'))
            or re.fullmatch(r'usr/lib[^/]*/lib[^/]*\.so[^/]*', name) is not None)


def archive_snapshot(path, collect_errors=False):
    """Stream the archive; inspect ELF bytes without unpacking any archive path."""
    starting_hash = file_hash(path)
    needed, provided, members = set(), set(), set()
    info = None
    unreadable = []
    try:
        with tarfile.open(path, 'r|gz') as archive:
            for member in archive:
                name = normalized_member(member.name)
                if name == '.PKGINFO':
                    if info is not None or not member.isfile():
                        raise ValueError('duplicate or non-regular .PKGINFO')
                    stream = archive.extractfile(member)
                    info = {}
                    for line in stream.read().decode('utf-8').splitlines():
                        key, sep, value = line.partition('=')
                        if sep and key.strip() in ('pkgname', 'pkgver', 'pkgrel'):
                            key = key.strip()
                            if key in info:
                                raise ValueError('duplicate .PKGINFO field: ' + key)
                            info[key] = value.strip()
                    continue
                if member.isfile() or member.issym() or member.islnk():
                    if shipped_member(name):
                        members.add(name)
                if not member.isfile():
                    continue
                stream = archive.extractfile(member)
                prefix = stream.read(4)
                if prefix != b'\x7fELF':
                    # tarfile's streaming seek can buffer an entire skipped
                    # payload; drain it in bounded chunks before advancing.
                    while stream.read(1024 * 1024):
                        pass
                    continue
                with tempfile.NamedTemporaryFile(prefix='configure-drift-elf-') as elf:
                    elf.write(prefix)
                    while chunk := stream.read(1024 * 1024):
                        elf.write(chunk)
                    elf.flush()
                    result = subprocess.run(['readelf', '-d', elf.name],
                                            capture_output=True, text=True,
                                            env={**os.environ, 'LC_ALL': 'C'})
                if result.returncode or result.stderr.strip():
                    if not collect_errors:
                        raise ValueError(f'{name}: readelf failed: {result.stderr.strip()}')
                    unreadable.append(dict(member=name, error=result.stderr.strip()))
                    continue
                needed.update(re.findall(r'\(NEEDED\).*?\[([^\]]+)\]', result.stdout))
                provided.update(re.findall(r'\(SONAME\).*?\[([^\]]+)\]', result.stdout))
        if info is None or not all(info.get(key) for key in ('pkgname', 'pkgver', 'pkgrel')):
            raise ValueError('missing .PKGINFO name, version or release')
    except (OSError, tarfile.TarError, UnicodeError) as error:
        raise ValueError(f'{path}: cannot inspect archive: {error}') from error
    if file_hash(path) != starting_hash:
        raise ValueError('archive changed while being measured: ' + str(path))
    return dict(name=info['pkgname'], version=info['pkgver'], release=info['pkgrel'],
                archive_name=Path(path).name, archive_sha256=starting_hash,
                needed=sorted(needed), members=sorted(members), provides=sorted(provided),
                unreadable_members=sorted(unreadable, key=lambda item: item['member']))


def archive_coverage(package, row, allowlist):
    findings, allowed = [], []
    for item in row.get('unreadable_members', []):
        reasons = [reason for name, question, before, after, reason in allowlist
                   if name == package and question == 'archive-unreadable'
                   and before == item['member'] and after == 'unreadable']
        detail = 'ARCHIVE unreadable member ' + item['member'] + ': ' + item['error']
        if reasons:
            allowed.append(detail + '; allowlisted: ' + '; '.join(reasons))
        else:
            findings.append(detail)
    return findings, allowed


def compare_archive(reference, current, providers, declared):
    findings = []
    for library in sorted(set(current['needed']) - set(reference['needed'])):
        owners = set(providers.get(library, []))
        if library in current.get('provides', []):
            owners.add(current['name'])
        if not owners or not owners.intersection(declared | {current['name']}):
            findings.append(f'ARCHIVE gained undeclared NEEDED {library}; providers: {", ".join(sorted(owners)) or "unknown"}')
    if current['version'] == reference['version']:
        for name in sorted(set(current['members']) - set(reference['members'])):
            findings.append('ARCHIVE added member at unchanged version: ' + name)
        for name in sorted(set(reference['members']) - set(current['members'])):
            findings.append('ARCHIVE removed member at unchanged version: ' + name)
    return findings


def load_index(path, pin=None):
    data = Path(path).read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    if pin is not None:
        expected = Path(pin).read_text().split()
        if len(expected) != 1 or not HEX.fullmatch(expected[0]):
            raise ValueError(f'{pin}: expected one SHA256 hash')
        if actual != expected[0]:
            raise ValueError(f'{path}: reference hash mismatch: {actual} != {expected[0]}')
    index = json.loads(data)
    if index.get('schema') != 2 or not isinstance(index.get('packages'), dict) or not index['packages']:
        raise ValueError(f'{path}: invalid or empty reference index')
    if not isinstance(index.get('lineage_id'), str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', index['lineage_id']):
        raise ValueError('missing or invalid reference lineage identity')
    if type(index.get('member_count')) is not int or index['member_count'] != len(index['packages']):
        raise ValueError('reference member count does not match package records')
    for name, row in index['packages'].items():
        if row.get('name') != name or not row.get('version') or not row.get('release'):
            raise ValueError('invalid package identity in reference: ' + name)
        if not HEX.fullmatch(row.get('archive_sha256', '')):
            raise ValueError('invalid archive hash in reference: ' + name)
        for key in ('needed', 'members', 'provides'):
            values = row.get(key)
            if not isinstance(values, list) or not all(isinstance(x, str) for x in values) or values != sorted(set(values)):
                raise ValueError(f'invalid {key} in reference: {name}')
        errors = row.get('unreadable_members')
        if not isinstance(errors, list) or any(not isinstance(item, dict) or not item.get('member') or not item.get('error') for item in errors):
            raise ValueError('invalid archive coverage in reference: ' + name)
        if not isinstance(row.get('logs'), dict):
            raise ValueError('invalid logs in reference: ' + name)
        for phase, log in row['logs'].items():
            if not HEX.fullmatch(log.get('sha256', '')) or not log.get('name'):
                raise ValueError('invalid log identity in reference: ' + name)
            coverage = log.get('coverage')
            if coverage is not None and (not isinstance(coverage, dict) or coverage.get('kind') != 'parallel-configure-output' or type(coverage.get('line')) is not int or coverage['line'] < 1):
                raise ValueError('invalid transcript coverage in reference: ' + name)
            if not isinstance(log.get('answers'), dict):
                raise ValueError('invalid reference answers: ' + name)
            for question, values in log['answers'].items():
                if not question or not isinstance(values, list) or not values or not all(isinstance(x, str) and x for x in values):
                    raise ValueError('invalid reference answer: ' + name)
    return index


def load_recipe(path):
    spec = importlib.util.spec_from_file_location('configure_drift_recipe', ROOT / 'igos-build/parser.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    try:
        return module.parse_template(Path(path))
    except module.TemplateError as error:
        raise ValueError(str(error)) from error


def recipe_dependencies(recipe):
    """Resolve the declared recipe namespace through its explicit ships_as field."""
    declared = set(recipe.dependencies.build + recipe.dependencies.host + recipe.dependencies.runtime)
    for path in (ROOT / 'packages').glob('*/*/package.yml'):
        # Read only declarations here; validating the selected recipe uses the
        # builder's parser above. Do not invent suffix aliases for providers.
        import yaml
        data = yaml.safe_load(path.read_text())
        if data.get('name') in declared and data.get('ships_as'):
            declared.add(data['ships_as'])
    return declared


def provider_map(index):
    result = {}
    for name, row in index['packages'].items():
        for library in row['provides']:
            result.setdefault(library, []).append(name)
    return result


def log_identity(path):
    match = LOG_NAME.fullmatch(Path(path).name)
    if not match:
        raise ValueError('unrecognized package log filename: ' + str(path))
    name, phase, stamp = match.groups()
    return name, phase or 'python', stamp


def generate_index(archives, logs, output, jobs, lineage_id, supplemental_logs=None, supplemental_references=None):
    paths = sorted(Path(archives).glob('*.igos.tar.gz'))
    if not paths:
        raise ValueError('no reference archives in ' + str(archives))
    log_paths = {}
    for path in sorted(Path(logs).glob('*.log')):
        try:
            name, phase, stamp = log_identity(path)
        except ValueError:
            continue
        log_paths.setdefault(name, {})[phase] = path
    reference_reasons = {}
    selections = json.loads(Path(supplemental_references).read_text()) if supplemental_references else []
    for selection in selections:
        package, filename, digest, reason = (selection[key] for key in ('package', 'log', 'sha256', 'reason'))
        if not reason or Path(filename).name != filename or not HEX.fullmatch(digest):
            raise ValueError('invalid supplemental reference selection: ' + package)
        if supplemental_logs is None:
            raise ValueError('supplemental reference requires --supplemental-logs: ' + package)
        path = Path(supplemental_logs) / filename
        name, phase, _ = log_identity(path)
        if name != package or file_hash(path) != digest:
            raise ValueError('supplemental reference identity or hash mismatch: ' + package)
        if phase in log_paths.get(name, {}):
            raise ValueError('supplemental reference cannot replace a lineage log: ' + package)
        log_paths.setdefault(name, {})[phase] = path
        reference_reasons[(name, phase)] = reason
    records = {}
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        for row in pool.map(lambda path: archive_snapshot(path, collect_errors=True), paths):
            name = row['name']
            if name in records:
                raise ValueError('multiple reference archives for package: ' + name)
            row['logs'] = {}
            for phase, path in sorted(log_paths.get(name, {}).items()):
                row['logs'][phase] = transcript_snapshot(path)
                if (name, phase) in reference_reasons:
                    row['logs'][phase]['reference_reason'] = reference_reasons[(name, phase)]
            records[name] = row
            print(f'INDEX {name}: {len(row["members"])} members, {len(row["needed"])} NEEDED, {len(row["logs"])} logs', flush=True)
    data = json.dumps(dict(schema=2, lineage_id=lineage_id, member_count=len(records), packages=dict(sorted(records.items()))), indent=2, sort_keys=True) + '\n'
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(data)
    print(f'INDEX lineage {lineage_id}; {len(records)} package members; sha256 {file_hash(output)}')
    missing = [name for name, row in sorted(records.items()) if not row['logs']]
    if missing:
        print('COVERAGE missing reference transcript (checks of these packages fail closed): ' + ', '.join(missing))
    return 0


def record_index(records, path_root, output, lineage_id):
    """Generate a candidate using only inputs recorded by this fresh build."""
    root = Path(path_root).resolve()
    packages = {}
    for path in sorted(Path(records).glob('*.tsv')):
        for number, line in enumerate(path.read_text().splitlines(), 1):
            fields = line.split('\t')
            if len(fields) != 4 or fields[0] not in ('archive', 'transcript'):
                raise ValueError(f'{path}:{number}: malformed record')
            signal, name, input_name, recipe_name = fields
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9+_.-]*', name):
                raise ValueError('invalid recorded package name')
            paths = []
            for value in (input_name, recipe_name):
                relative = PurePosixPath(value)
                if not relative.is_absolute() or '..' in relative.parts:
                    raise ValueError('recorded paths must be absolute and contain no parent traversal')
                resolved = (root / value.lstrip('/')).resolve()
                if not resolved.is_relative_to(root) or not resolved.is_file():
                    raise ValueError('recorded input absent or outside the build root: ' + value)
                paths.append(resolved)
            packages.setdefault(name, {})[signal] = paths
    if not packages:
        raise ValueError('no fresh-build records in ' + str(records))
    rows = {}
    for name, inputs in sorted(packages.items()):
        if set(inputs) != {'transcript', 'archive'}:
            raise ValueError('incomplete fresh-build record: ' + name)
        archive_path, recipe_path = inputs['archive']
        log_path, log_recipe = inputs['transcript']
        if recipe_path != log_recipe:
            raise ValueError('recorded recipe changed between signals: ' + name)
        recipe = load_recipe(recipe_path)
        row = archive_snapshot(archive_path, collect_errors=True)
        if (row['name'], row['version'], row['release']) != (name, recipe.version, str(recipe.release)):
            raise ValueError('recorded archive identity differs from recipe: ' + name)
        log_name, phase, _ = log_identity(log_path)
        if log_name != name:
            raise ValueError('recorded log identity differs from package: ' + name)
        row['logs'] = {phase: transcript_snapshot(log_path)}
        rows[name] = row
        print('RECORD INDEX ' + name, flush=True)
    data = json.dumps(dict(schema=2, lineage_id=lineage_id, member_count=len(rows), packages=rows), indent=2, sort_keys=True) + '\n'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(data)
    print(f'RECORD CANDIDATE {len(rows)} packages; sha256 {file_hash(output)}; {output}')
    return 0


def check(args, index):
    recipe = load_recipe(args.recipe)
    package = args.package
    if package not in (recipe.name, recipe.ships_as):
        raise ValueError(f'package {package} does not match recipe {recipe.name}')
    if package not in index['packages']:
        raise ValueError('missing reference package: ' + package)
    reference = index['packages'][package]
    allowlist = load_allowlist(args.allowlist)
    findings, allowed = [], []
    setup_errors = []
    if args.signal in ('transcript', 'both'):
        transcript_findings, transcript_allowed = [], []
        try:
            if not args.log:
                raise ValueError('transcript check requires --log')
            name, phase, _ = log_identity(args.log)
            if name != package:
                raise ValueError('current log package does not match ' + package)
            if phase not in reference['logs']:
                raise ValueError(f'missing reference transcript: {package} phase {phase}')
            current_log = transcript_snapshot(args.log)
            baseline_log = reference['logs'][phase]
            baseline, current = baseline_log['answers'], current_log['answers']
            coverage = []
            for label, log in [('reference', baseline_log), ('current', current_log)]:
                if log['coverage']:
                    coverage.append(f'TRANSCRIPT COVERAGE {label} parallel configure output at line {log["coverage"]["line"]}; only preceding pairs are attributable')
            waivers = [reason for name, kind, _, _, reason in allowlist
                       if name == package and kind == 'archive-only']
            if coverage and waivers:
                transcript_allowed.append('TRANSCRIPT archive-only; ' + '; '.join(waivers)
                                          + '; archive verdict is required before deployment')
            else:
                transcript_findings += coverage
                if baseline and not current and not current_log['coverage']:
                    raise ValueError('current log has no detection answers: ' + str(args.log))
                changes, waivers = compare_transcript(package, baseline, current, allowlist)
                transcript_findings += changes
                transcript_allowed += waivers
        except (OSError, ValueError) as error:
            setup_errors.append('TRANSCRIPT COVERAGE ERROR: ' + str(error))
            transcript_findings.append(setup_errors[-1])
        for item in transcript_allowed:
            print('ALLOW ' + package + ': ' + item)
        for item in transcript_findings:
            print('RED ' + package + ': ' + item)
        print(f'{"RED" if transcript_findings else "GREEN"} {package}: transcript; {len(transcript_findings)} findings, {len(transcript_allowed)} allowlisted')
        findings += transcript_findings
        allowed += transcript_allowed
    if args.signal in ('archive', 'both'):
        archive_findings, archive_allowed = [], []
        try:
            if not args.archive:
                raise ValueError('archive check requires --archive')
            current = archive_snapshot(args.archive, collect_errors=True)
            if (current['name'], current['version'], current['release']) != (package, recipe.version, str(recipe.release)):
                raise ValueError('archive identity does not match current recipe: ' + package)
            for label, row in [('reference', reference), ('current', current)]:
                errors, waivers = archive_coverage(package, row, allowlist)
                archive_findings += [label + ' ' + error for error in errors]
                archive_allowed += [label + ' ' + waiver for waiver in waivers]
            archive_findings += compare_archive(reference, current, provider_map(index), recipe_dependencies(recipe))
            lost = sorted(set(reference['needed']) - set(current['needed']))
            if lost:
                print('OBSERVATION lost NEEDED: ' + ', '.join(lost))
        except (OSError, ValueError, tarfile.TarError) as error:
            setup_errors.append('ARCHIVE COVERAGE ERROR: ' + str(error))
            archive_findings.append(setup_errors[-1])
        for item in archive_allowed:
            print('ALLOW ' + package + ': ' + item)
        for item in archive_findings:
            print('RED ' + package + ': ' + item)
        print(f'{"RED" if archive_findings else "GREEN"} {package}: archive; {len(archive_findings)} findings, {len(archive_allowed)} allowlisted')
        findings += archive_findings
        allowed += archive_allowed
    if args.signal == 'both':
        print(f'{"RED" if findings else "GREEN"} {package}: both; {len(findings)} findings, {len(allowed)} allowlisted')
    return 2 if setup_errors else int(bool(findings))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    generation = commands.add_parser('index')
    generation.add_argument('--archives', type=Path, required=True)
    generation.add_argument('--logs', type=Path, required=True)
    generation.add_argument('--output', type=Path, required=True)
    generation.add_argument('--jobs', type=int, default=4)
    generation.add_argument('--lineage-id', required=True)
    generation.add_argument('--supplemental-references', type=Path)
    generation.add_argument('--supplemental-logs', type=Path)
    recording = commands.add_parser('record-index')
    recording.add_argument('--records', type=Path, required=True)
    recording.add_argument('--path-root', type=Path, required=True)
    recording.add_argument('--output', type=Path, required=True)
    recording.add_argument('--lineage-id', required=True)
    for name in ('validate', 'check'):
        command = commands.add_parser(name)
        command.add_argument('--index', type=Path, default=Path(os.environ.get('IGOS_CONFIGURE_DRIFT_INDEX', DEFAULT_INDEX)))
        command.add_argument('--pin', type=Path)
        command.add_argument('--allowlist', type=Path, default=DEFAULT_ALLOWLIST)
        if name == 'check':
            command.add_argument('--signal', choices=('transcript', 'archive', 'both'), required=True)
            command.add_argument('--package', required=True)
            command.add_argument('--recipe', type=Path, required=True)
            command.add_argument('--log', type=Path)
            command.add_argument('--archive', type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'record-index':
            return record_index(args.records, args.path_root, args.output, args.lineage_id)
        if args.command == 'index':
            if not 1 <= args.jobs <= 32:
                raise ValueError('--jobs must be between 1 and 32')
            return generate_index(args.archives, args.logs, args.output, args.jobs, args.lineage_id, args.supplemental_logs, args.supplemental_references)
        index = load_index(args.index, args.pin)
        load_allowlist(args.allowlist)
        if args.command == 'validate':
            missing = [name for name, row in index['packages'].items() if not row['logs']]
            for name in missing:
                print('COVERAGE missing transcript: ' + name + '; transcript check fails closed')
            for name, row in index['packages'].items():
                for phase, log in row['logs'].items():
                    if log['coverage']:
                        print(f'COVERAGE {name} {phase}: parallel configure output at line {log["coverage"]["line"]}; transcript check fails closed unless reviewed as archive-only')
                errors, waivers = archive_coverage(name, row, load_allowlist(args.allowlist))
                for error in errors:
                    print('COVERAGE ' + name + ': ' + error + '; archive check fails closed')
                for waiver in waivers:
                    print('ALLOW ' + name + ': ' + waiver)
            print(f'VALID reference lineage {index["lineage_id"]}: {index["member_count"]} package members; sha256 {file_hash(args.index)}; {len(missing)} packages without transcripts (their transcript checks fail closed)')
            return 0
        return check(args, index)
    except (OSError, ValueError, KeyError, TypeError, ImportError, tarfile.TarError) as error:
        print(f'RED configure-drift ERROR: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
