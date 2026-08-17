"""Unit tests for scripts/iso-sbom-gen.py — the ISO-level SPDX SBOM generator.

Three properties carry the weight, and each exists because the alternative
failure is silent:

  1. THE SHIPPED SET IS THE PIPELINE'S OWN. The generator resolves
     ``iso_include`` through igos-build/parser.py, so the tier-based default
     (``tier: extra`` and ``tier: compute`` default to MIRROR) is applied by the
     same code the build uses. A hand list or a filename glob could be wrong
     while looking right. These tests pin the derivation against fixture trees
     covering the explicit-true, explicit-false and defaulted cases.

  2. IT FAILS CLOSED. A package that cannot be identified stops the run and is
     named; no document is written. An SBOM missing a shipped package asserts a
     completeness it does not have, which is worse than no SBOM at all.

  3. LICENCES ARE REPRESENTED, NOT COERCED. A declaration that is not a
     well-formed SPDX expression becomes a LicenseRef carrying the raw text,
     never NOASSERTION — dropping the declaration would defeat the one audit
     the document exists to serve.

Determinism is pinned too: with ``created`` fixed, two runs over one tree must
produce byte-identical JSON, because a re-run that differs cannot be diffed.

Assertions are structural where values are incidental (counts, regex, field
presence) so ordinary package edits do not require test edits, following the
convention in test_shim_sbom_gen.py.
"""

import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "iso-sbom-gen.py"

# Hyphen in the script name blocks a normal import; load via importlib spec —
# same pattern as test_shim_sbom_gen.py and tests/preflight/.
_spec = importlib.util.spec_from_file_location("iso_sbom_gen", SCRIPT_PATH)
sbom = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sbom)


MINIMAL_YML = """\
name: {name}
version: {version}
release: {release}
description: A fixture package for SBOM generator tests.
license: {license}
tier: {tier}
build_style: custom
source: []
"""


def write_pkg(packages_dir: Path, tier: str, name: str, *, version="1.0",
              release=1, license="MIT", extra_lines="") -> Path:
    """Create packages/<tier>/<name>/package.yml in a fixture tree."""
    pkg_dir = packages_dir / tier / name
    pkg_dir.mkdir(parents=True, exist_ok=True)
    text = MINIMAL_YML.format(name=name, version=version, release=release,
                              license=license, tier=tier)
    if extra_lines:
        text += extra_lines if extra_lines.endswith("\n") else extra_lines + "\n"
    (pkg_dir / "package.yml").write_text(text)
    return pkg_dir / "package.yml"


class FixtureTree:
    """A throwaway repo-shaped tree: packages/ plus an archives dir."""

    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.packages = self.root / "packages"
        self.packages.mkdir()
        self.archives = self.root / "archives"
        self.archives.mkdir()
        self.output = self.root / "out" / "sbom.spdx.json"

    def stage_archive(self, name: str, version: str, payload: bytes) -> Path:
        path = self.archives / f"{name}-{version}.igos.tar.gz"
        path.write_bytes(payload)
        return path

    def run(self, *extra, expect=0, archives=False):
        """Invoke main() with captured streams. Returns (rc, stdout, stderr)."""
        argv = ["--output", str(self.output), "--packages", str(self.packages),
                "--created", "2026-01-01T00:00:00Z", *extra]
        if archives:
            argv += ["--archives", str(self.archives)]
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = sbom.main(argv)
        assert rc == expect, (
            f"expected exit {expect}, got {rc}\nstdout:\n{out.getvalue()}\n"
            f"stderr:\n{err.getvalue()}")
        return rc, out.getvalue(), err.getvalue()

    def doc(self) -> dict:
        return json.loads(self.output.read_text())

    def close(self):
        self._tmp.cleanup()


class TreeTestCase(unittest.TestCase):
    def setUp(self):
        self.tree = FixtureTree()
        self.addCleanup(self.tree.close)


# ----------------------------------------------------------------------
# The shipped-set derivation
# ----------------------------------------------------------------------


class TestShippedSetDerivation(TreeTestCase):
    def test_tier_default_decides_when_iso_include_is_absent(self):
        """extra and compute default to MIRROR; other tiers default to shipped.

        This is the parser's rule, and the whole point of importing it rather
        than restating it. If the parser's default changes, this test changes
        with it — which is correct, because the ISO's contents would change too.
        """
        write_pkg(self.tree.packages, "core", "shipped-core")
        write_pkg(self.tree.packages, "desktop", "shipped-desktop")
        write_pkg(self.tree.packages, "extra", "mirror-extra")
        write_pkg(self.tree.packages, "compute", "mirror-compute")
        write_pkg(self.tree.packages, "toolchain", "gcc-pass1")

        shipped, refusals, mirror = sbom.derive_shipped_set(self.tree.packages)
        self.assertEqual(refusals, [])
        self.assertEqual(sorted(p.name for p in shipped),
                         ["shipped-core", "shipped-desktop"])
        self.assertEqual(mirror, 3)

    def test_toolchain_intermediates_are_not_part_of_the_shipped_set(self):
        """pass*/tmp twins are torn down before the image exists.

        Under the pre-2026-08-06 default they were counted as shipped, and the
        first release-lane --require-archives firing refused all 25 of them
        for having no staged archives — which they can never have.
        """
        write_pkg(self.tree.packages, "core", "real-package")
        write_pkg(self.tree.packages, "toolchain", "binutils-pass1")
        write_pkg(self.tree.packages, "toolchain", "xz-tmp")
        self.tree.stage_archive("real-package", "1.0", b"payload")
        self.tree.run("--require-archives", archives=True)
        names = [p["name"] for p in self.tree.doc()["packages"]
                 if p["SPDXID"] != "SPDXRef-Package-intergenos-iso"]
        self.assertEqual(names, ["real-package"])

    def test_explicit_iso_include_overrides_the_tier_default_both_ways(self):
        write_pkg(self.tree.packages, "extra", "extra-but-shipped",
                  extra_lines="iso_include: true")
        write_pkg(self.tree.packages, "core", "core-but-mirror",
                  extra_lines="iso_include: false")

        shipped, refusals, mirror = sbom.derive_shipped_set(self.tree.packages)
        self.assertEqual(refusals, [])
        self.assertEqual([p.name for p in shipped], ["extra-but-shipped"])
        self.assertEqual(mirror, 1)

    def test_mirror_packages_are_absent_from_the_document(self):
        write_pkg(self.tree.packages, "core", "in-iso")
        write_pkg(self.tree.packages, "extra", "not-in-iso")
        self.tree.run()
        names = [p["name"] for p in self.tree.doc()["packages"]]
        self.assertIn("in-iso", names)
        self.assertNotIn("not-in-iso", names)

    def test_a_directory_without_package_yml_is_ignored_not_refused(self):
        write_pkg(self.tree.packages, "core", "real")
        (self.tree.packages / "core" / "notapackage").mkdir(parents=True)
        shipped, refusals, _ = sbom.derive_shipped_set(self.tree.packages)
        self.assertEqual(refusals, [])
        self.assertEqual([p.name for p in shipped], ["real"])

    def test_shipped_packages_are_sorted_deterministically(self):
        for name in ("zebra", "alpha", "mango"):
            write_pkg(self.tree.packages, "core", name)
        shipped, _, _ = sbom.derive_shipped_set(self.tree.packages)
        self.assertEqual([p.name for p in shipped], ["alpha", "mango", "zebra"])


# ----------------------------------------------------------------------
# ships_as resolution (the ch8 dual-name twins)
# ----------------------------------------------------------------------


class TestShipsAsResolution(TreeTestCase):
    def test_a_dual_name_twin_is_described_under_its_ship_name(self):
        """glibc-core ships as glibc: entry name, SPDXID, archive, URL.

        The archive, the installed manifest, the .PKGINFO and the mirror all
        carry the ships_as name — describing the recipe name would state a
        package the ISO does not actually carry.
        """
        write_pkg(self.tree.packages, "core", "glibc-core",
                  version="2.43", extra_lines="ships_as: glibc\n")
        self.tree.stage_archive("glibc", "2.43", b"glibc-bytes")
        self.tree.run("--require-archives", archives=True)
        entries = {p["name"]: p for p in self.tree.doc()["packages"]
                   if p["SPDXID"] != "SPDXRef-Package-intergenos-iso"}
        self.assertIn("glibc", entries)
        self.assertNotIn("glibc-core", entries)
        entry = entries["glibc"]
        self.assertTrue(
            entry["downloadLocation"].endswith("/glibc-2.43.igos.tar.gz"))
        self.assertEqual(entry["checksums"][0]["checksumValue"],
                         hashlib.sha256(b"glibc-bytes").hexdigest())
        self.assertIn("glibc-core", entry["comment"])  # recipe provenance kept

    def test_require_archives_accepts_the_ship_name_archive(self):
        """Only the ship-name archive exists on a real chroot; no refusal.

        The 2026-08-06 release firing refused all 19 -core twins by composing
        the archive filename from the recipe name.
        """
        write_pkg(self.tree.packages, "core", "gcc-core",
                  version="15.2.0", extra_lines="ships_as: gcc\n")
        self.tree.stage_archive("gcc", "15.2.0", b"x")
        self.tree.run("--require-archives", archives=True)


# ----------------------------------------------------------------------
# Fail-closed
# ----------------------------------------------------------------------


class TestFailsClosed(TreeTestCase):
    def test_unparseable_package_yml_refuses_the_whole_run(self):
        """An undecidable package must not be silently absent.

        scripts/derive-iso-exclusions.py now refuses this same shape
        (fail-closed, same rationale): if we cannot tell whether the
        package ships, we cannot claim the document is complete.
        """
        write_pkg(self.tree.packages, "core", "good")
        bad = self.tree.packages / "core" / "broken"
        bad.mkdir(parents=True)
        (bad / "package.yml").write_text("name: broken\nversion: [this is not\n")

        _, _, err = self.tree.run(expect=1)
        self.assertIn("REFUSING", err)
        self.assertIn("broken", err)
        self.assertFalse(self.tree.output.exists(),
                         "no document may be written when a package is refused")

    def test_a_missing_required_field_refuses_and_names_the_package(self):
        write_pkg(self.tree.packages, "core", "good")
        bad = self.tree.packages / "core" / "nolicense"
        bad.mkdir(parents=True)
        (bad / "package.yml").write_text(
            "name: nolicense\nversion: 1.0\nrelease: 1\n"
            "description: no license field\ntier: core\nbuild_style: custom\n"
            "source: []\n")
        _, _, err = self.tree.run(expect=1)
        self.assertIn("nolicense", err)
        self.assertFalse(self.tree.output.exists())

    def test_every_bad_package_is_named_in_one_run(self):
        """One run reports all refusals — fixing them one crash at a time is
        how a ten-package problem becomes ten review cycles."""
        write_pkg(self.tree.packages, "core", "good")
        for bad_name in ("broken-one", "broken-two", "broken-three"):
            d = self.tree.packages / "core" / bad_name
            d.mkdir(parents=True)
            (d / "package.yml").write_text(f"name: {bad_name}\nversion: [nope\n")
        _, _, err = self.tree.run(expect=1)
        for bad_name in ("broken-one", "broken-two", "broken-three"):
            self.assertIn(bad_name, err)

    def test_an_empty_shipped_set_is_refused_not_emitted(self):
        """An empty document would assert that the ISO ships nothing."""
        write_pkg(self.tree.packages, "extra", "mirror-only")
        _, _, err = self.tree.run(expect=1)
        self.assertIn("REFUSING", err)
        self.assertFalse(self.tree.output.exists())

    def test_require_archives_refuses_a_package_with_no_staged_archive(self):
        write_pkg(self.tree.packages, "core", "hashed", version="2.0")
        write_pkg(self.tree.packages, "core", "unhashed", version="3.0")
        self.tree.stage_archive("hashed", "2.0", b"ARCHIVE-BYTES")
        _, _, err = self.tree.run("--require-archives", archives=True, expect=1)
        self.assertIn("unhashed", err)
        self.assertNotIn("REFUSED hashed", err)
        self.assertFalse(self.tree.output.exists())

    def test_require_archives_without_archives_dir_is_a_usage_error(self):
        write_pkg(self.tree.packages, "core", "any")
        _, _, err = self.tree.run("--require-archives", expect=2)
        self.assertIn("--archives", err)

    def test_a_missing_packages_dir_is_a_usage_error(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = sbom.main(["--output", str(self.tree.output),
                            "--packages", str(self.tree.root / "nope")])
        self.assertEqual(rc, 2)
        self.assertIn("not found", err.getvalue())


# ----------------------------------------------------------------------
# Archive checksums
# ----------------------------------------------------------------------


class TestArchiveChecksums(TreeTestCase):
    def test_a_staged_archive_contributes_its_sha256(self):
        payload = b"REAL-ARCHIVE-CONTENT"
        write_pkg(self.tree.packages, "core", "pkg", version="4.2")
        self.tree.stage_archive("pkg", "4.2", payload)
        self.tree.run(archives=True)
        entry = [p for p in self.tree.doc()["packages"] if p["name"] == "pkg"][0]
        self.assertEqual(
            entry["checksums"],
            [{"algorithm": "SHA256",
              "checksumValue": hashlib.sha256(payload).hexdigest()}])

    def test_the_archive_name_omits_the_release(self):
        """Binary archives are <name>-<version>.igos.tar.gz — four places in the
        build compose them that way. Looking for the source-archive shape
        (which does carry the release) would find nothing."""
        self.assertEqual(sbom.archive_basename("foo", "1.2"),
                         "foo-1.2.igos.tar.gz")
        write_pkg(self.tree.packages, "core", "foo", version="1.2", release=7)
        self.tree.stage_archive("foo", "1.2", b"X")
        self.tree.run(archives=True)
        entry = [p for p in self.tree.doc()["packages"] if p["name"] == "foo"][0]
        self.assertIn("foo-1.2.igos.tar.gz", entry["downloadLocation"])
        self.assertEqual(entry["versionInfo"], "1.2-7")
        self.assertEqual(len(entry["checksums"]), 1)

    def test_an_absent_archive_yields_no_checksum_and_says_so(self):
        """Recorded honestly rather than refused: the generator must be runnable
        on a bare checkout. The entry states the absence in its own comment."""
        write_pkg(self.tree.packages, "core", "nohash", version="1.0")
        _, out, _ = self.tree.run(archives=True)
        entry = [p for p in self.tree.doc()["packages"]
                 if p["name"] == "nohash"][0]
        self.assertEqual(entry["checksums"], [])
        self.assertIn("no staged archive", entry["comment"])
        self.assertIn("0 of 1", out)

    def test_the_summary_counts_how_many_entries_carry_a_hash(self):
        write_pkg(self.tree.packages, "core", "a", version="1.0")
        write_pkg(self.tree.packages, "core", "b", version="1.0")
        self.tree.stage_archive("a", "1.0", b"A")
        _, out, _ = self.tree.run(archives=True)
        self.assertIn("1 of 2", out)


# ----------------------------------------------------------------------
# Licence representation
# ----------------------------------------------------------------------


class TestLicenceExpressionShape(unittest.TestCase):
    def test_accepts_plain_and_compound_spdx_expressions(self):
        for expr in ("MIT", "GPL-3.0-or-later", "MIT OR Apache-2.0",
                     "LGPL-2.1-or-later AND GPL-2.0-or-later",
                     "GPL-2.0-or-later WITH Font-exception-2.0",
                     "(MIT OR Apache-2.0) AND BSD-3-Clause",
                     "GPL-2.0+", "BSD-3-Clause AND LicenseRef-Intel-SOF-Binary"):
            self.assertTrue(sbom.license_expression_is_wellformed(expr), expr)

    def test_rejects_free_text_and_malformed_expressions(self):
        for expr in ("Public Domain", "Various (redistributable)", "",
                     "   ", "MIT AND", "AND MIT", "MIT Apache-2.0",
                     "(MIT OR Apache-2.0", "MIT OR Apache-2.0)"):
            self.assertFalse(sbom.license_expression_is_wellformed(expr), expr)

    def test_license_refs_are_collected_from_a_valid_expression(self):
        self.assertEqual(
            sbom.license_refs_in("BSD-3-Clause AND LicenseRef-Intel-SOF-Binary"),
            ["LicenseRef-Intel-SOF-Binary"])


class TestLicenceRepresentation(TreeTestCase):
    def test_a_valid_expression_passes_through_unchanged(self):
        write_pkg(self.tree.packages, "core", "plain", license="Apache-2.0")
        self.tree.run()
        entry = [p for p in self.tree.doc()["packages"]
                 if p["name"] == "plain"][0]
        self.assertEqual(entry["licenseDeclared"], "Apache-2.0")

    def test_free_text_becomes_a_licenseref_that_preserves_the_raw_text(self):
        """NOASSERTION would validate and lose the only fact that matters."""
        write_pkg(self.tree.packages, "core", "firmware",
                  license="Various (redistributable)")
        self.tree.run()
        doc = self.tree.doc()
        entry = [p for p in doc["packages"] if p["name"] == "firmware"][0]
        self.assertTrue(entry["licenseDeclared"].startswith("LicenseRef-"))
        self.assertNotEqual(entry["licenseDeclared"], "NOASSERTION")
        infos = {i["licenseId"]: i for i in doc["hasExtractedLicensingInfos"]}
        self.assertIn(entry["licenseDeclared"], infos)
        self.assertEqual(infos[entry["licenseDeclared"]]["name"],
                         "Various (redistributable)")
        self.assertIn("Various (redistributable)",
                      infos[entry["licenseDeclared"]]["extractedText"])

    def test_a_licenseref_inside_a_valid_expression_is_still_defined(self):
        """SPDX requires every LicenseRef used to be defined; an expression that
        passes through untouched still needs its refs declared."""
        write_pkg(self.tree.packages, "core", "sof",
                  license="BSD-3-Clause AND LicenseRef-Intel-SOF-Binary")
        self.tree.run()
        doc = self.tree.doc()
        ids = {i["licenseId"] for i in doc["hasExtractedLicensingInfos"]}
        self.assertIn("LicenseRef-Intel-SOF-Binary", ids)

    def test_no_extracted_infos_block_when_every_licence_is_a_plain_id(self):
        write_pkg(self.tree.packages, "core", "clean", license="MIT")
        self.tree.run()
        self.assertNotIn("hasExtractedLicensingInfos", self.tree.doc())

    def test_a_payload_license_is_recorded_without_replacing_the_package_licence(self):
        write_pkg(self.tree.packages, "core", "helper",
                  license="GPL-3.0-or-later",
                  extra_lines="payload_license: LicenseRef-Vendor-EULA")
        self.tree.run()
        entry = [p for p in self.tree.doc()["packages"]
                 if p["name"] == "helper"][0]
        self.assertEqual(entry["licenseDeclared"], "GPL-3.0-or-later")
        self.assertIn("payload", entry["comment"].lower())
        self.assertIn("LicenseRef-Vendor-EULA", entry["comment"])


# ----------------------------------------------------------------------
# Document shape and determinism
# ----------------------------------------------------------------------


class TestDocumentShape(TreeTestCase):
    def setUp(self):
        super().setUp()
        write_pkg(self.tree.packages, "core", "alpha", version="1.0")
        write_pkg(self.tree.packages, "desktop", "beta", version="2.0")
        write_pkg(self.tree.packages, "extra", "mirror")
        self.tree.run()
        self.d = self.tree.doc()

    def test_spdx_envelope_fields_are_present_and_correct(self):
        self.assertEqual(self.d["spdxVersion"], "SPDX-2.3")
        self.assertEqual(self.d["dataLicense"], "CC0-1.0")
        self.assertEqual(self.d["SPDXID"], "SPDXRef-DOCUMENT")
        self.assertRegex(self.d["documentNamespace"], r"^https://\S+$")
        self.assertEqual(self.d["creationInfo"]["created"],
                         "2026-01-01T00:00:00Z")
        self.assertTrue(any("iso-sbom-gen" in c
                            for c in self.d["creationInfo"]["creators"]))

    def test_the_document_describes_one_iso_root_that_contains_each_package(self):
        describes = [r for r in self.d["relationships"]
                     if r["relationshipType"] == "DESCRIBES"]
        self.assertEqual(len(describes), 1)
        root_id = describes[0]["relatedSpdxElement"]
        contains = [r for r in self.d["relationships"]
                    if r["relationshipType"] == "CONTAINS"]
        self.assertTrue(all(r["spdxElementId"] == root_id for r in contains))
        # One CONTAINS per shipped package: alpha and beta, not mirror.
        self.assertEqual(len(contains), 2)

    def test_every_spdxid_is_unique_and_well_formed(self):
        ids = [p["SPDXID"] for p in self.d["packages"]]
        self.assertEqual(len(ids), len(set(ids)))
        for spdxid in ids:
            self.assertRegex(spdxid, r"^SPDXRef-[A-Za-z0-9.\-]+$")

    def test_every_relationship_points_at_a_declared_element(self):
        declared = {p["SPDXID"] for p in self.d["packages"]} | {"SPDXRef-DOCUMENT"}
        for rel in self.d["relationships"]:
            self.assertIn(rel["spdxElementId"], declared)
            self.assertIn(rel["relatedSpdxElement"], declared)

    def test_each_package_entry_carries_the_required_spdx_fields(self):
        required = {"SPDXID", "name", "versionInfo", "supplier",
                    "downloadLocation", "filesAnalyzed", "licenseConcluded",
                    "licenseDeclared", "copyrightText"}
        for entry in self.d["packages"]:
            self.assertTrue(required.issubset(entry), entry.get("name"))

    def test_version_info_carries_version_and_release(self):
        entry = [p for p in self.d["packages"] if p["name"] == "alpha"][0]
        self.assertEqual(entry["versionInfo"], "1.0-1")


class TestDeterminism(TreeTestCase):
    def _emit(self, path: Path, *extra, archives=False):
        argv = ["--output", str(path), "--packages", str(self.tree.packages),
                "--created", "2026-01-01T00:00:00Z", *extra]
        if archives:
            argv += ["--archives", str(self.tree.archives)]
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            rc = sbom.main(argv)
        self.assertEqual(rc, 0)
        return path.read_bytes()

    def test_two_runs_over_one_tree_are_byte_identical(self):
        write_pkg(self.tree.packages, "core", "alpha")
        write_pkg(self.tree.packages, "desktop", "beta")
        first = self._emit(self.tree.root / "one.json")
        second = self._emit(self.tree.root / "two.json")
        self.assertEqual(hashlib.sha256(first).hexdigest(),
                         hashlib.sha256(second).hexdigest())

    def test_source_date_epoch_supplies_a_fixed_created_timestamp(self):
        import os
        write_pkg(self.tree.packages, "core", "alpha")
        prior = os.environ.get("SOURCE_DATE_EPOCH")
        os.environ["SOURCE_DATE_EPOCH"] = "0"
        try:
            self.assertEqual(sbom.default_created(), "1970-01-01T00:00:00Z")
        finally:
            if prior is None:
                del os.environ["SOURCE_DATE_EPOCH"]
            else:
                os.environ["SOURCE_DATE_EPOCH"] = prior

    def test_the_namespace_changes_when_the_package_set_changes(self):
        write_pkg(self.tree.packages, "core", "alpha")
        before = json.loads(
            self._emit(self.tree.root / "before.json"))["documentNamespace"]
        write_pkg(self.tree.packages, "core", "gamma")
        after = json.loads(
            self._emit(self.tree.root / "after.json"))["documentNamespace"]
        self.assertNotEqual(before, after)

    def test_the_namespace_is_stable_when_nothing_changes(self):
        write_pkg(self.tree.packages, "core", "alpha")
        first = json.loads(
            self._emit(self.tree.root / "a.json"))["documentNamespace"]
        second = json.loads(
            self._emit(self.tree.root / "b.json"))["documentNamespace"]
        self.assertEqual(first, second)

    def test_a_release_bump_changes_the_namespace(self):
        """versionInfo carries the release, so a rebuilt package is a different
        bill of materials even at the same upstream version."""
        write_pkg(self.tree.packages, "core", "alpha", release=1)
        before = json.loads(
            self._emit(self.tree.root / "r1.json"))["documentNamespace"]
        write_pkg(self.tree.packages, "core", "alpha", release=2)
        after = json.loads(
            self._emit(self.tree.root / "r2.json"))["documentNamespace"]
        self.assertNotEqual(before, after)


if __name__ == "__main__":
    unittest.main()
