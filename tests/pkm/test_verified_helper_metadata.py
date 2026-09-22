# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Vendor version decisions use real signatures before interpreting metadata."""

import importlib.util
import json
import os
import shlex
import subprocess
from pathlib import Path

import pytest

from tests.pkm import test_helper_weak_digest_verify as signed_repo_fixture

REPO = Path(__file__).resolve().parents[2]
LIBRARY = REPO / "packages/core/intergenos-helper-lib/helper-lib.sh"
spec = importlib.util.spec_from_file_location(
    "deb_metadata", LIBRARY.with_name("deb-metadata.py"))
metadata = importlib.util.module_from_spec(spec)
spec.loader.exec_module(metadata)


@pytest.mark.parametrize("left,right,expected", [
    ("1.0", "1.0-0", 0), ("0:1.0", "1.0", 0),
    ("2:1.0", "10:0.1", -1), ("1:0.1", "999.0", 1),
    ("1.0~rc1", "1.0", -1), ("1.0-2", "1.0-10", -1),
    ("1.01", "1.1", 0), ("1.0-1~bpo1", "1.0-1", -1),
    ("1.0a", "1.0+", -1), ("1.0-1", "1.0", 1),
    ("1.0+really0.9-1", "1.0-9", 1),
])
def test_complete_debian_version_order(left, right, expected):
    assert metadata.compare_versions(left, right) == expected
    assert metadata.compare_versions(right, left) == -expected


@pytest.mark.parametrize("value", ["", "unknown", "x:1.0", "1.0-", "1.0\n2.0", "1/2", "１.0"])
def test_invalid_recorded_versions_are_errors(value):
    with pytest.raises(ValueError):
        metadata.compare_versions("1.0", value)


@pytest.fixture(scope="module")
def repository():
    fixture = signed_repo_fixture.WeakDigestScopedVerifyTests
    fixture.setUpClass()
    try:
        yield fixture
    finally:
        fixture.tearDownClass()


def lookup(repository, name, requested="", scoped="", query_installed=None):
    base = f"http://127.0.0.1:{repository.port}/{name}"
    args = ["steam-launcher", base, str(repository.keyring), "stable", "steam"]
    function = "igos_helper_find_verified_deb_in_packages"
    if query_installed is None:
        args += [requested, scoped]
    else:
        function = "igos_helper_query_deb_upgrade"
        args = [query_installed, *args, scoped]
    return subprocess.run(
        ["/bin/bash", "-c", 'source "$1"\nshift\n' + function + ' "$@"',
         "_", str(LIBRARY), *args], capture_output=True, text=True, timeout=60,
    )


def test_verified_lookup_keeps_epoch_and_payload_digest(repository):
    result = lookup(repository, "sha512-a")
    assert result.returncode == 0, result.stderr
    fields = result.stdout.strip().split("|")
    assert fields == [signed_repo_fixture.DEB_FILENAME, "1:1.0.0.85",
                      signed_repo_fixture.POOL_PATH, repository.deb_sha]


@pytest.mark.parametrize("installed,comparison", [
    ("1:1.0.0.85", 0), ("2:0.1", -1), ("1.999.0", 1),
])
def test_upgrade_query_orders_verified_version(repository, installed, comparison):
    result = lookup(repository, "sha512-a", query_installed=installed)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"version": "1:1.0.0.85", "comparison": comparison}


def test_reinstall_selects_only_the_requested_version(repository):
    result = lookup(repository, "sha512-a", requested="1:1.0.0.85")
    assert result.returncode == 0, result.stderr
    absent = lookup(repository, "sha512-a", requested="1:0.9")
    assert absent.returncode != 0
    assert "absent from verified metadata" in absent.stderr


def test_bad_signature_never_produces_a_version(repository):
    result = lookup(repository, "sha1-a-tampered", scoped=repository.fpr_a)
    assert result.returncode != 0
    assert result.stdout == ""
    assert "STEP 2 FAIL" in result.stderr


def test_modified_packages_never_produce_a_version(repository):
    path = repository._build_repo("modified-packages", "keyA@igos.test", "SHA512")
    packages = path / "dists/stable/steam/binary-amd64/Packages"
    packages.write_text(packages.read_text().replace("Version: 1:1.0.0.85", "Version: 99:1.0"))
    result = lookup(repository, "modified-packages")
    assert result.returncode != 0
    assert result.stdout == ""
    assert "Packages sha256 MISMATCH" in " ".join(result.stderr.split())


def test_unavailable_metadata_is_an_error(repository):
    result = lookup(repository, "not-published")
    assert result.returncode != 0
    assert result.stdout == ""
    assert "STEP 1 FAIL" in result.stderr


def test_scoped_weak_digest_policy_applies_to_the_version_lookup(repository):
    refused = lookup(repository, "sha1-a")
    accepted = lookup(repository, "sha1-a", scoped=repository.fpr_a)
    wrong_key = lookup(repository, "sha1-b", scoped=repository.fpr_a)
    assert refused.returncode != 0
    assert accepted.returncode == 0, accepted.stderr
    assert "SECURITY NOTICE" in accepted.stderr
    assert wrong_key.returncode != 0


@pytest.mark.parametrize("name,package,component", [
    ("vscode", "code", "main"), ("chrome", "google-chrome-stable", "main"),
    ("edge", "microsoft-edge-stable", "main"), ("brave", "brave-browser", "main"),
    ("signal", "signal-desktop", "main"), ("spotify", "spotify-client", "non-free"),
    ("steam", "steam-launcher", "steam"), ("chatgpt", "chatgpt", "main"),
])
def test_each_helper_queries_before_its_install_body(tmp_path, name, package, component):
    """The full generated helper routes a query without entering installation.

    The library stand-in records its arguments and supplies a protocol reply;
    actual signature verification is exercised by the real-library cases above.
    """
    source=(REPO / "packages/extra" / name / "build.sh").read_text()
    body=source.split("<< 'HELPEREOF'\n",1)[1].split("\nHELPEREOF",1)[0]
    assert body.startswith("#!/bin/bash\n")
    fixture=tmp_path / "query-library.sh"
    arguments=tmp_path / "arguments"
    fixture.write_text(
        "igos_helper_write_acceptance() { return 99; }\n"
        "igos_helper_query_deb_upgrade() {\n"
        f"printf '%s\\n' \"$@\" > {shlex.quote(str(arguments))}\n"
        "printf '%s\\n' '{\"version\":\"2.0\",\"comparison\":1}'\n"
        "}\n"
        "mktemp() { echo 'installation body reached' >&2; exit 99; }\n"
    )
    original="source /usr/share/igos/helpers/helper-lib.sh"
    assert body.count(original)==1
    helper=tmp_path / "helper.sh"
    helper.write_text(body.replace(original,"source "+shlex.quote(str(fixture))))
    result=subprocess.run(["/bin/bash",str(helper),"--check-upgrade","1.0"],
                          stdin=subprocess.DEVNULL,capture_output=True,text=True,
                          env=dict(os.environ,TMPDIR=str(tmp_path)),timeout=10)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)=={"version":"2.0","comparison":1}
    args=arguments.read_text().splitlines()
    assert args[0:2]==["1.0",package]
    assert args[5]==component
    assert len(args)==7


@pytest.mark.parametrize('function',['lookup','payload-verifier'])
def test_empty_work_directory_is_refused_before_a_download(tmp_path,repository,function):
    trace=tmp_path/'wget-ran'
    payload=tmp_path/'payload.deb'
    payload.write_bytes(b'fixture')
    call=('igos_helper_find_verified_deb_in_packages steam-launcher '
          'https://example.invalid "$2" stable steam') if function=='lookup' else (
          'igos_helper_verify_deb_via_signed_release payload.deb "$3" '
          'https://example.invalid "$2" stable steam')
    shell=('source "$1"\n'
           'mktemp() { :; }\n'
           f'wget() {{ printf called > {shlex.quote(str(trace))}; return 99; }}\n'
           +call+'\n')
    result=subprocess.run(['/bin/bash','-c',shell,'_',str(LIBRARY),str(repository.keyring),str(payload)],
                          capture_output=True,text=True)
    assert result.returncode==2,result.stderr
    assert 'temporary directory' in result.stderr
    assert not trace.exists()
