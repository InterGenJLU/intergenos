# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Advisory validation protects both index generation and file loading."""

import gzip
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tarfile
from types import MappingProxyType

import pytest
import yaml

from pkm.repo import generate_index


ROOT = Path(__file__).resolve().parents[2]
MALFORMED = [
    pytest.param({"cves": "CVE-2026-12345"}, id="cves_scalar"),
    pytest.param({"cves": ["CVE-2026-12345", 12345]}, id="cves_non_string"),
    pytest.param(["CVE-2026-12345"], id="entry_list"),
    pytest.param("CVE-2026-12345", id="entry_string"),
]


@pytest.fixture
def archives(tmp_path):
    directory = tmp_path / "archives"
    directory.mkdir()
    body = b"pkgname = demo\npkgver = 2.0\npkgrel = 1\n"
    with tarfile.open(directory / "demo-2.0-1.igos.tar.gz", "w:gz") as archive:
        info = tarfile.TarInfo(".PKGINFO")
        info.size = len(body)
        archive.addfile(info, io.BytesIO(body))
    return directory


@pytest.mark.parametrize("entry", MALFORMED)
@pytest.mark.parametrize("key", ["demo-2.0", "unmatched-1.0"])
@pytest.mark.parametrize("existing", [False, True])
def test_direct_api_refuses_invalid_entries_before_writing(archives, entry, key, existing):
    output = archives / "InterGenOS.db"
    if existing:
        output.write_bytes(b"previous index\n")
    with pytest.raises(ValueError, match=re.escape(key)) as error:
        generate_index(archives, security_advisories={key: entry})
    assert len(str(error.value).splitlines()) == 1
    if existing:
        assert output.read_bytes() == b"previous index\n"
    else:
        assert not output.exists()


@pytest.mark.parametrize("entry", MALFORMED)
def test_loader_refuses_invalid_entries_with_one_error(tmp_path, entry):
    advisory = tmp_path / "advisories.yml"
    advisory.write_text(yaml.safe_dump({"demo-2.0": entry}))
    result = subprocess.run(
        [sys.executable, "-c",
         "import runpy,sys; runpy.run_path(sys.argv[1])['load_security_advisories']"
         "(sys.argv[2], required=True)",
         str(ROOT / "scripts/generate-repodb.py"), str(advisory)],
        cwd=tmp_path, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    error, = result.stderr.splitlines()
    assert error.startswith(f"ERROR: {advisory}: demo-2.0: ")
    assert "Traceback" not in error


@pytest.mark.parametrize("advisories", [None, {}, {"demo-2.0": {"cves": []}}])
def test_empty_advisories_keep_packages_unstamped(archives, advisories):
    output = generate_index(archives, security_advisories=advisories)
    with gzip.open(output, "rt") as handle:
        package = json.load(handle)["packages"]["demo"]
    assert "security" not in package and "cves" not in package


def test_mapping_inputs_preserve_cve_order_without_mutation(archives):
    cves = ["CVE-2026-12346", "CVE-2026-12345"]
    entry = MappingProxyType({"cves": cves})
    advisories = MappingProxyType({"demo-2.0": entry})
    output = generate_index(archives, security_advisories=advisories)
    with gzip.open(output, "rt") as handle:
        package = json.load(handle)["packages"]["demo"]
    assert package["security"] is True
    assert package["cves"] == cves == ["CVE-2026-12346", "CVE-2026-12345"]
    assert advisories["demo-2.0"] is entry and entry["cves"] is cves


@pytest.mark.parametrize("advisories", [[], False, "invalid"])
def test_direct_api_requires_a_mapping(archives, advisories):
    with pytest.raises(ValueError, match="expected a mapping"):
        generate_index(archives, security_advisories=advisories)
    assert not (archives / "InterGenOS.db").exists()


@pytest.mark.parametrize("entry", [{}, {"cves": None}, {"cves": [""]}])
def test_each_entry_requires_a_list_of_nonempty_strings(archives, entry):
    with pytest.raises(ValueError, match="demo-2.0: cves must be a list of non-empty strings"):
        generate_index(archives, security_advisories={"demo-2.0": entry})
    assert not (archives / "InterGenOS.db").exists()
