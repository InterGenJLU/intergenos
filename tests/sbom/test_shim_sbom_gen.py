"""Unit tests for scripts/shim-sbom-gen.py — parse_dockerfile, read_sbat_entry,
build_spdx_doc, main() argparse + happy-path generation.

Complements tests/sbom/test_sha256_git_blob.py (F17 helper coverage) by
covering the four remaining functions in the SBOM generator. Happy-path
tests reference the canonical files under docker/shim-build/ on master
(fixtures-as-snapshots: SPOC option (a) from 14:47:15Z dispatch); edge
and error-path tests use per-test tempdir fixtures.

The happy-path assertions are structural — they pin shape (regex,
length, field count, type) rather than specific values, so routine
Dockerfile / SBAT CSV edits (shim version bumps, etc.) don't require
test updates. Where vendor identity IS asserted (SBAT vendor/product
fields), those are stable-by-design and acceptable to pin.
"""

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "shim-sbom-gen.py"

# Hyphen in script name blocks normal import; load via importlib spec
# (same pattern as test_sha256_git_blob.py + tests/preflight/).
_spec = importlib.util.spec_from_file_location("shim_sbom_gen", SCRIPT_PATH)
sbom = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sbom)


# ----------------------------------------------------------------------
# parse_dockerfile() — 3 tests
# ----------------------------------------------------------------------


class TestParseDockerfile(unittest.TestCase):
    """parse_dockerfile() — canonical happy path + 2 missing-anchor error paths."""

    def test_parses_canonical_dockerfile_returns_all_six_fields(self):
        """Reference-master fixture: canonical Dockerfile parses to 6 fields."""
        path = REPO_ROOT / "docker" / "shim-build" / "Dockerfile"
        result = sbom.parse_dockerfile(path)
        self.assertEqual(
            set(result.keys()),
            {
                "base_image_ref",
                "base_image_digest",
                "apt_snapshot_timestamp",
                "source_date_epoch",
                "shim_version",
                "shim_commit",
            },
        )
        # Structural assertions — survive routine value bumps.
        self.assertTrue(result["base_image_ref"])
        self.assertRegex(result["base_image_digest"], r"^[0-9a-f]{64}$")
        self.assertRegex(result["apt_snapshot_timestamp"], r"^\d{8}T\d{6}Z$")
        self.assertRegex(result["source_date_epoch"], r"^\d+$")
        self.assertTrue(result["shim_version"])
        self.assertRegex(result["shim_commit"], r"^[0-9a-f]{40}$")

    def test_missing_from_line_raises_value_error(self):
        """Dockerfile without digest-pinned FROM → ValueError with helpful msg."""
        with tempfile.TemporaryDirectory() as tmp_:
            path = Path(tmp_) / "Dockerfile"
            path.write_text(
                "# no FROM line at all\n"
                "ARG SOURCE_DATE_EPOCH=1746489600\n"
                "ARG SHIM_VERSION=16.1\n"
                "ARG SHIM_COMMIT=" + "a" * 40 + "\n"
                "RUN echo snapshot.debian.org/archive/debian/20260501T000000Z/\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError) as ctx:
                sbom.parse_dockerfile(path)
            self.assertIn("digest-pinned FROM", str(ctx.exception))

    def test_missing_arg_raises_value_error(self):
        """Dockerfile without ARG SHIM_COMMIT → ValueError mentioning the ARG."""
        with tempfile.TemporaryDirectory() as tmp_:
            path = Path(tmp_) / "Dockerfile"
            path.write_text(
                "FROM debian:bookworm-slim@sha256:" + "b" * 64 + "\n"
                "ARG SOURCE_DATE_EPOCH=1746489600\n"
                "ARG SHIM_VERSION=16.1\n"
                # ARG SHIM_COMMIT intentionally absent
                "RUN echo snapshot.debian.org/archive/debian/20260501T000000Z/\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError) as ctx:
                sbom.parse_dockerfile(path)
            self.assertIn("SHIM_COMMIT", str(ctx.exception))


# ----------------------------------------------------------------------
# read_sbat_entry() — 3 tests
# ----------------------------------------------------------------------


class TestReadSbatEntry(unittest.TestCase):
    """read_sbat_entry() — canonical happy path + multi-line + wrong-columns errors."""

    def test_parses_canonical_sbat_returns_all_eight_fields(self):
        """Reference-master fixture: SBAT entry parses to all 8 dict keys."""
        path = REPO_ROOT / "docker" / "shim-build" / "sbat" / "sbat.intergenos.csv"
        result = sbom.read_sbat_entry(path)
        self.assertEqual(
            set(result.keys()),
            {
                "raw_line",
                "raw_bytes",
                "name",
                "generation",
                "vendor",
                "product",
                "version",
                "url",
            },
        )
        self.assertIsInstance(result["raw_bytes"], bytes)
        # Vendor identity is stable-by-design; pin those.
        self.assertEqual(result["name"], "shim.intergenos")
        self.assertEqual(result["generation"], "1")
        self.assertEqual(result["vendor"], "InterGenOS")
        self.assertEqual(result["product"], "shim")
        # Version + URL structural rather than pinned.
        self.assertTrue(result["version"])
        self.assertTrue(result["url"].startswith("https://"))

    def test_multi_line_sbat_raises_value_error(self):
        """SBAT with 2 data lines → ValueError "expected exactly 1"."""
        with tempfile.TemporaryDirectory() as tmp_:
            path = Path(tmp_) / "sbat.csv"
            path.write_text(
                "shim.foo,1,Foo,shim,16.1,https://example.invalid\n"
                "shim.bar,1,Bar,shim,16.1,https://example.invalid\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError) as ctx:
                sbom.read_sbat_entry(path)
            self.assertIn("expected exactly 1", str(ctx.exception))

    def test_wrong_column_count_raises_value_error(self):
        """SBAT line with 5 fields instead of 6 → ValueError 6-fields msg."""
        with tempfile.TemporaryDirectory() as tmp_:
            path = Path(tmp_) / "sbat.csv"
            path.write_text(
                "shim.foo,1,Foo,shim,16.1\n",  # URL field missing
                encoding="utf-8",
            )
            with self.assertRaises(ValueError) as ctx:
                sbom.read_sbat_entry(path)
            self.assertIn("6 comma-separated", str(ctx.exception))


# ----------------------------------------------------------------------
# build_spdx_doc() — 3 tests
# ----------------------------------------------------------------------


def _mock_dockerfile_dict() -> dict:
    """Reusable mock parsed-Dockerfile dict for build_spdx_doc tests."""
    return {
        "base_image_ref": "debian:bookworm-slim",
        "base_image_digest": "a" * 64,
        "apt_snapshot_timestamp": "20260501T000000Z",
        "source_date_epoch": "1746489600",
        "shim_version": "16.1",
        "shim_commit": "b" * 40,
    }


def _mock_sbat_dict() -> dict:
    """Reusable mock parsed-SBAT dict for build_spdx_doc tests."""
    return {
        "raw_line": "shim.intergenos,1,InterGenOS,shim,16.1,https://example.invalid",
        "raw_bytes": (
            b"shim.intergenos,1,InterGenOS,shim,16.1,https://example.invalid\n"
        ),
        "name": "shim.intergenos",
        "generation": "1",
        "vendor": "InterGenOS",
        "product": "shim",
        "version": "16.1",
        "url": "https://example.invalid",
    }


class TestBuildSpdxDoc(unittest.TestCase):
    """build_spdx_doc() — structure + namespace determinism + relationship coverage.

    build_spdx_doc() calls sha256_git_blob() internally for PEM/DER/SBAT
    SHAs. These resolve against REPO_ROOT (the actual repo at HEAD), which
    is the F17-correct host-independent path.
    """

    SHIM_SHA = "c" * 64

    def _build(self, shim_sha=SHIM_SHA, submission_tag="intergenos-shim-x64-test"):
        return sbom.build_spdx_doc(
            dockerfile=_mock_dockerfile_dict(),
            sbat=_mock_sbat_dict(),
            repo_root=REPO_ROOT,
            shim_sha256=shim_sha,
            shim_size=1020990,
            shim_version="16.1",
            submission_tag=submission_tag,
            created_iso="2026-05-12T15:00:00Z",
        )

    def test_output_structure_six_packages_two_files_six_relationships(self):
        """Canonical doc shape: SPDX 2.3, 6 packages, 2 files, 6 relationships."""
        doc = self._build()
        self.assertEqual(doc["spdxVersion"], "SPDX-2.3")
        self.assertEqual(doc["SPDXID"], "SPDXRef-DOCUMENT")
        self.assertEqual(len(doc["packages"]), 6)
        self.assertEqual(len(doc["files"]), 2)
        self.assertEqual(len(doc["relationships"]), 6)

    def test_document_namespace_deterministic_in_shim_sha256(self):
        """Same shim_sha256 → identical documentNamespace; different sha → different ns."""
        doc_a1 = self._build(shim_sha="a" * 64)
        doc_a2 = self._build(shim_sha="a" * 64)
        doc_b = self._build(shim_sha="d" * 64)
        self.assertEqual(doc_a1["documentNamespace"], doc_a2["documentNamespace"])
        self.assertNotEqual(doc_a1["documentNamespace"], doc_b["documentNamespace"])
        # First 16 chars of shim_sha256 must appear in the namespace.
        self.assertIn("a" * 16, doc_a1["documentNamespace"])
        self.assertIn("d" * 16, doc_b["documentNamespace"])

    def test_relationships_cover_all_four_types_with_correct_subjects(self):
        """All 4 SPDX relationship types present; DESCRIBES subject is DOCUMENT."""
        doc = self._build()
        types = {r["relationshipType"] for r in doc["relationships"]}
        self.assertEqual(
            types,
            {"DESCRIBES", "GENERATED_FROM", "DEPENDS_ON", "CONTAINS"},
        )
        # The DESCRIBES relationship's subject must be the SPDX document itself,
        # pointing at the shim binary package.
        describes = [
            r for r in doc["relationships"] if r["relationshipType"] == "DESCRIBES"
        ]
        self.assertEqual(len(describes), 1)
        self.assertEqual(describes[0]["spdxElementId"], "SPDXRef-DOCUMENT")
        self.assertEqual(
            describes[0]["relatedSpdxElement"], "SPDXRef-Package-shimx64-efi"
        )


# ----------------------------------------------------------------------
# main() — 3 tests
# ----------------------------------------------------------------------


class TestMain(unittest.TestCase):
    """main() entry point — happy path + missing-args + invalid-sha256-format errors."""

    SHIM_SHA = "441f9bd1bb75d5dbfc9c5d2c8451b210c9156573515923786d0a1cc4a2a01e25"

    def test_happy_path_with_shim_sha256_writes_output_file(self):
        """--shim-sha256 + --shim-size path produces a valid SPDX JSON output file."""
        with tempfile.TemporaryDirectory() as tmp_:
            out_path = Path(tmp_) / "out.spdx.json"
            argv = [
                "--output", str(out_path),
                "--repo-root", str(REPO_ROOT),
                "--shim-sha256", self.SHIM_SHA,
                "--shim-size", "1020990",
            ]
            rc = sbom.main(argv)
            self.assertEqual(rc, 0)
            self.assertTrue(out_path.is_file())
            # Output must be valid JSON with the expected document shape.
            doc = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(doc["spdxVersion"], "SPDX-2.3")
            self.assertEqual(len(doc["packages"]), 6)
            self.assertEqual(len(doc["files"]), 2)

    def test_missing_shim_args_exits_with_argparse_error(self):
        """Neither --shim-binary nor --shim-sha256 supplied → argparse SystemExit(2)."""
        with tempfile.TemporaryDirectory() as tmp_:
            out_path = Path(tmp_) / "out.spdx.json"
            argv = [
                "--output", str(out_path),
                "--repo-root", str(REPO_ROOT),
                # Both --shim-binary and --shim-sha256 deliberately omitted.
            ]
            # argparse's p.error() raises SystemExit(2) after writing to stderr.
            with self.assertRaises(SystemExit) as ctx:
                sbom.main(argv)
            self.assertEqual(ctx.exception.code, 2)

    def test_invalid_shim_sha256_format_returns_error(self):
        """--shim-sha256 with wrong length → main returns 2 + writes stderr msg."""
        with tempfile.TemporaryDirectory() as tmp_:
            out_path = Path(tmp_) / "out.spdx.json"
            argv = [
                "--output", str(out_path),
                "--repo-root", str(REPO_ROOT),
                "--shim-sha256", "deadbeef",  # too short to match 64-hex regex
                "--shim-size", "1020990",
            ]
            err = io.StringIO()
            with redirect_stderr(err):
                rc = sbom.main(argv)
            self.assertEqual(rc, 2)
            self.assertIn("64 hex chars", err.getvalue())


if __name__ == "__main__":
    unittest.main()
