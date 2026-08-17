"""Wedge tests for the lib32 governance folds in validate-package-tiers.py
(GE gate-tooling: RT-14 explicit lib32_source mapping field + RT-9 sibling
version lock + the elf_class:mixed governance allowlist).

Covers:
  * classify() pass 0/3 — lib32-* names bypass hard rules, patterns, AND
    consumer inference; tier derives ONLY from the declared lib32_source
    sibling (an unmapped lib32 is UNCLEAR, never pattern/inference-placed);
  * lib32_audit() — every governance defect named precisely, fail-closed:
    missing mapping, unresolvable mapping, lib32-to-lib32 mapping, RT-9
    version skew, missing elf_class:"32", field misuse on non-lib32,
    ungoverned elf_class:mixed;
  * the real CLI on planted fixture trees (red) and corrected trees (green),
    exercising --packages-dir exactly as a pipeline caller would;
  * zero behavior change on the REAL package tree (LIB32/MIXED=0, exit 0).
"""

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
VALIDATOR = REPO_ROOT / "scripts" / "validate-package-tiers.py"

_spec = importlib.util.spec_from_file_location("vpt", VALIDATOR)
vpt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vpt)


def _pkg(tier, version="1.0", lib32_source=None, elf_class=None,
         deps_build=(), deps_host=(), yml_path="packages/x/y/package.yml",
         pending=None, source_shas=None, patch_shas=None):
    # source_shas/patch_shas default None = "no source data provided" (the
    # W2-a identity gate skips); the real loader always provides lists.
    return {
        "tier": tier,
        "version": version,
        "version_is_str": isinstance(version, str),
        "deps_build": list(deps_build),
        "deps_host": list(deps_host),
        "yml_path": Path(yml_path),
        "pending_acquisition": pending,
        "lib32_source": lib32_source,
        "elf_class": elf_class,
        "source_shas": source_shas,
        "patch_shas": patch_shas,
    }


class TestClassifyLib32Derivation(unittest.TestCase):
    def test_unmapped_lib32_is_unclear_never_inferred(self):
        # An unmapped lib32 consumed by a desktop package must NOT inherit
        # desktop via consumer inference — fail-closed to UNCLEAR.
        pkgs = {
            "gtk-thing": _pkg("desktop", deps_build=["lib32-mystery"]),
            "lib32-mystery": _pkg("core"),
        }
        natural = vpt.classify(pkgs, lfs_ch8=set())
        self.assertEqual(natural["lib32-mystery"], "UNCLEAR")

    def test_mapped_lib32_derives_sibling_tier(self):
        # zlib is in FOUNDATIONAL_LIBS -> core; the lib32 twin derives core
        # from the mapping, not from any pattern or consumer.
        pkgs = {
            "zlib": _pkg("core"),
            "lib32-zlib": _pkg("core", lib32_source="zlib", elf_class="32"),
        }
        natural = vpt.classify(pkgs, lfs_ch8=set())
        self.assertEqual(natural["zlib"], "core")
        self.assertEqual(natural["lib32-zlib"], "core")

    def test_mapping_beats_consumer_inference(self):
        # The sibling says core even though the only consumer is desktop —
        # the mapping is authoritative (evaluated INSTEAD of inference).
        pkgs = {
            "zlib": _pkg("core"),
            "lib32-zlib": _pkg("core", lib32_source="zlib", elf_class="32"),
            "gtk-thing": _pkg("desktop", deps_build=["lib32-zlib"]),
        }
        natural = vpt.classify(pkgs, lfs_ch8=set())
        self.assertEqual(natural["lib32-zlib"], "core")

    def test_prefix_strip_mis_key_is_not_used(self):
        # lib32-libpulse's true source is pulseaudio; a name-prefix-strip
        # lookup would try 'libpulse' (absent). With the explicit mapping the
        # derivation lands on pulseaudio's tier.
        pkgs = {
            "pulseaudio": _pkg("desktop"),
            "lib32-libpulse": _pkg("desktop", lib32_source="pulseaudio",
                                   elf_class="32"),
        }
        pkgs["pulseaudio"]["deps_build"] = []
        natural = vpt.classify(pkgs, lfs_ch8=set())
        # pulseaudio isn't in a hard set here; give it a consumer to classify
        pkgs2 = dict(pkgs)
        pkgs2["gnome-shell-x"] = _pkg("desktop", deps_build=["pulseaudio"])
        natural = vpt.classify(pkgs2, lfs_ch8=set())
        self.assertEqual(natural["lib32-libpulse"], natural["pulseaudio"])


class TestLib32Audit(unittest.TestCase):
    def _audit(self, pkgs):
        return vpt.lib32_audit(pkgs)

    def test_missing_mapping_flagged(self):
        v = self._audit({"lib32-zlib": _pkg("core", elf_class="32")})
        self.assertIn("lib32-zlib", v)
        self.assertTrue(any("missing lib32_source" in m
                            for m in v["lib32-zlib"]))

    def test_unresolvable_mapping_flagged(self):
        v = self._audit({"lib32-libpulse": _pkg(
            "desktop", lib32_source="libpulse", elf_class="32")})
        self.assertTrue(any("does not resolve" in m
                            for m in v["lib32-libpulse"]))

    def test_lib32_to_lib32_mapping_flagged(self):
        v = self._audit({
            "lib32-a": _pkg("core", lib32_source="lib32-b", elf_class="32"),
            "lib32-b": _pkg("core", lib32_source="b", elf_class="32"),
            "b": _pkg("core"),
        })
        self.assertTrue(any("must name the 64-bit" in m
                            for m in v["lib32-a"]))

    def test_version_skew_flagged_rt9(self):
        v = self._audit({
            "mesa": _pkg("desktop", version="25.2.4"),
            "lib32-mesa": _pkg("desktop", version="25.1.0",
                               lib32_source="mesa", elf_class="32"),
        })
        msgs = v.get("lib32-mesa", [])
        self.assertTrue(any("version-lock" in m and "25.2.4" in m
                            and "25.1.0" in m for m in msgs))

    def test_version_match_clean(self):
        v = self._audit({
            "mesa": _pkg("desktop", version="25.2.4"),
            "lib32-mesa": _pkg("desktop", version="25.2.4",
                               lib32_source="mesa", elf_class="32"),
        })
        self.assertNotIn("lib32-mesa", v)

    def test_missing_elf_class_flagged(self):
        v = self._audit({
            "zlib": _pkg("core"),
            "lib32-zlib": _pkg("core", lib32_source="zlib"),
        })
        self.assertTrue(any('elf_class: "32"' in m
                            for m in v["lib32-zlib"]))

    def test_field_misuse_on_non_lib32_flagged(self):
        v = self._audit({"zlib": _pkg("core", lib32_source="zlib-src")})
        self.assertTrue(any("non-lib32" in m for m in v["zlib"]))

    def test_mixed_ungoverned_flagged(self):
        v = self._audit({"sneaky": _pkg("extra", elf_class="mixed")})
        self.assertTrue(any("ELF_CLASS_MIXED_ALLOWED" in m
                            for m in v["sneaky"]))

    def test_mixed_governed_grub_clean(self):
        v = self._audit({"grub": _pkg("core", elf_class="mixed")})
        self.assertNotIn("grub", v)


class TestCLIFixtureRedGreen(unittest.TestCase):
    """The real invocation on planted fixture trees — red catches the plant,
    the corrected twin tree passes."""

    def _write_tree(self, tmp, specs):
        pkgs = Path(tmp) / "packages"
        for relpath, text in specs.items():
            d = pkgs / relpath
            d.mkdir(parents=True)
            (d / "package.yml").write_text(text)
        return pkgs

    def _run(self, pkgs_dir, extra=()):
        return subprocess.run(
            [sys.executable, str(VALIDATOR), "--packages-dir", str(pkgs_dir),
             *extra],
            capture_output=True, text=True)

    # W2-a made the source pin part of the governance surface: the twin
    # fixture must pin a sha matching its sibling's, like every real twin.
    _SHA = "d" * 64
    _SRC = f"source:\n- url: https://x.example/z.tar.gz\n  sha256: {_SHA}\n"
    ZLIB = "name: zlib\nversion: \"1.3.2\"\ntier: core\n" + _SRC

    def test_red_version_skew_caught_then_green(self):
        with tempfile.TemporaryDirectory() as tmp:
            pkgs = self._write_tree(tmp, {
                "core/zlib": self.ZLIB,
                "core/lib32-zlib": ("name: lib32-zlib\nversion: \"1.3.1\"\n"
                                    "tier: core\nlib32_source: zlib\n"
                                    "elf_class: \"32\"\n" + self._SRC),
            })
            r = self._run(pkgs, extra=("lib32-zlib",))
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("LIB32-GOVERNANCE", r.stdout)
            self.assertIn("version-lock", r.stdout)
        with tempfile.TemporaryDirectory() as tmp:
            pkgs = self._write_tree(tmp, {
                "core/zlib": self.ZLIB,
                "core/lib32-zlib": ("name: lib32-zlib\nversion: \"1.3.2\"\n"
                                    "tier: core\nlib32_source: zlib\n"
                                    "elf_class: \"32\"\n" + self._SRC),
            })
            r = self._run(pkgs, extra=("lib32-zlib",))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn("LIB32-GOVERNANCE", r.stdout)

    def test_red_cli_source_drift_caught(self):
        # The W2-a plant at CLI level: same release string, mutated twin sha.
        with tempfile.TemporaryDirectory() as tmp:
            pkgs = self._write_tree(tmp, {
                "core/zlib": self.ZLIB,
                "core/lib32-zlib": ("name: lib32-zlib\nversion: \"1.3.2\"\n"
                                    "tier: core\nlib32_source: zlib\n"
                                    "elf_class: \"32\"\n"
                                    "source:\n- url: https://x.example/z.tar.gz\n"
                                    f"  sha256: {'e' * 64}\n"),
            })
            r = self._run(pkgs, extra=("lib32-zlib",))
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("LIB32-SOURCE-DRIFT", r.stdout)

    def test_red_unmapped_lib32_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            pkgs = self._write_tree(tmp, {
                "core/zlib": self.ZLIB,
                "core/lib32-zlib": ("name: lib32-zlib\nversion: \"1.3.2\"\n"
                                    "tier: core\nelf_class: \"32\"\n"),
            })
            r = self._run(pkgs, extra=("lib32-zlib",))
            self.assertEqual(r.returncode, 1)
            self.assertIn("missing lib32_source", r.stdout)

    def test_red_ungoverned_mixed_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            pkgs = self._write_tree(tmp, {
                "core/zlib": ("name: zlib\nversion: \"1.3.2\"\ntier: core\n"
                              "elf_class: mixed\n"),
            })
            r = self._run(pkgs, extra=("zlib",))
            self.assertEqual(r.returncode, 1)
            self.assertIn("MIXED-UNGOVERNED", r.stdout)


class TestRecertFindings(unittest.TestCase):
    """WC re-cert G1-a + G1-b closes (2026-07-02)."""

    def test_g1a_malformed_manifest_named_not_crashed(self):
        # deps authored as a yaml LIST used to crash the validator with no
        # summary (which the orchestrator's old deny-list waved through).
        # Now: a named MALFORMED-MANIFEST row + exit 1.
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "packages" / "core" / "broken"
            d.mkdir(parents=True)
            (d / "package.yml").write_text(
                "name: broken\nversion: \"1.0\"\ntier: core\n"
                "dependencies:\n  - zlib\n")
            r = subprocess.run(
                [sys.executable, str(VALIDATOR),
                 "--packages-dir", str(Path(tmp) / "packages")],
                capture_output=True, text=True)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("MALFORMED-MANIFEST", r.stdout)
            self.assertIn("dependencies", r.stdout)
            self.assertIn("# summary:", r.stdout)  # summary always emitted

    def test_g1a_unparseable_yaml_named_not_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "packages" / "core" / "broken"
            d.mkdir(parents=True)
            (d / "package.yml").write_text("::: not yaml {{{")
            r = subprocess.run(
                [sys.executable, str(VALIDATOR),
                 "--packages-dir", str(Path(tmp) / "packages")],
                capture_output=True, text=True)
            self.assertEqual(r.returncode, 1)
            self.assertIn("MALFORMED-MANIFEST", r.stdout)

    def test_g1a_library_caller_raises_loud(self):
        # No collector passed -> the malformed manifest raises, never a
        # silent drop.
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "core" / "broken"
            d.mkdir(parents=True)
            (d / "package.yml").write_text("just a string")
            with self.assertRaises(Exception):
                vpt.load_all_packages(Path(tmp))

    def test_g1a_list_valued_scalar_field_named_not_traceback(self):
        # Re-cert residual 3: a list where a scalar belongs (lib32_source)
        # must be a NAMED row, not a downstream traceback.
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "packages" / "core" / "lib32-zlib"
            d.mkdir(parents=True)
            (d / "package.yml").write_text(
                "name: lib32-zlib\nversion: \"1.3\"\ntier: core\n"
                "lib32_source:\n  - zlib\nelf_class: \"32\"\n")
            r = subprocess.run(
                [sys.executable, str(VALIDATOR),
                 "--packages-dir", str(Path(tmp) / "packages")],
                capture_output=True, text=True)
            self.assertEqual(r.returncode, 1)
            self.assertIn("MALFORMED-MANIFEST", r.stdout)
            self.assertIn("lib32_source", r.stdout)
            self.assertNotIn("Traceback", r.stderr)

    def test_g1b_unquoted_float_version_refused(self):
        # yaml 1.10 -> float 1.1 masks a trailing-zero skew vs "1.1";
        # the pair now refuses the coercion itself.
        v = vpt.lib32_audit({
            "alsa-lib": _pkg("desktop", version="1.1"),
            "lib32-alsa-lib": _pkg("desktop", version="1.1",
                                   lib32_source="alsa-lib", elf_class="32"),
        })
        self.assertNotIn("lib32-alsa-lib", v)  # both quoted strings: clean
        pkgs = {
            "alsa-lib": _pkg("desktop", version="1.1"),
            "lib32-alsa-lib": _pkg("desktop", version="1.1",
                                   lib32_source="alsa-lib", elf_class="32"),
        }
        pkgs["alsa-lib"]["version_is_str"] = False  # authored unquoted 1.10
        v = vpt.lib32_audit(pkgs)
        self.assertTrue(any("unquoted YAML number" in m
                            for m in v.get("lib32-alsa-lib", [])))

    def test_g1b_cli_unquoted_pair_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            pkgs = Path(tmp) / "packages"
            (pkgs / "core" / "zlib").mkdir(parents=True)
            (pkgs / "core" / "lib32-zlib").mkdir(parents=True)
            # 64-bit sibling authored UNQUOTED 1.10 (yaml float -> 1.1);
            # lib32 quoted "1.1" — the old equality PASSED this real skew.
            (pkgs / "core" / "zlib" / "package.yml").write_text(
                "name: zlib\nversion: 1.10\ntier: core\n")
            (pkgs / "core" / "lib32-zlib" / "package.yml").write_text(
                "name: lib32-zlib\nversion: \"1.1\"\ntier: core\n"
                "lib32_source: zlib\nelf_class: \"32\"\n")
            r = subprocess.run(
                [sys.executable, str(VALIDATOR), "--packages-dir",
                 str(pkgs), "lib32-zlib"],
                capture_output=True, text=True)
            self.assertEqual(r.returncode, 1)
            self.assertIn("unquoted YAML number", r.stdout)


class TestLib32SourceIdentity(unittest.TestCase):
    """W2-a (Wave-2 verify finding): the twin↔sibling source identity is a
    checked gate. The finder's exact plant: a twin sha mutated at the SAME
    release string was invisible to governance."""

    SHA_A = "a" * 64
    SHA_B = "b" * 64
    SHA_T = "c" * 64  # a sibling co-source the twin legitimately omits

    def test_mutated_twin_sha_flags_drift(self):
        pkgs = {
            "libXrender": _pkg("desktop", elf_class=None,
                               source_shas=[self.SHA_A]),
            "lib32-libXrender": _pkg("desktop", lib32_source="libXrender",
                                     elf_class="32",
                                     source_shas=[self.SHA_B]),
        }
        v = vpt.lib32_audit(pkgs)
        self.assertIn("lib32-libXrender", v)
        self.assertTrue(any("LIB32-SOURCE-DRIFT" in m
                            for m in v["lib32-libXrender"]))

    def test_identical_sha_passes(self):
        pkgs = {
            "libXrender": _pkg("desktop", source_shas=[self.SHA_A]),
            "lib32-libXrender": _pkg("desktop", lib32_source="libXrender",
                                     elf_class="32",
                                     source_shas=[self.SHA_A]),
        }
        self.assertEqual(vpt.lib32_audit(pkgs), {})

    def test_twin_subset_of_sibling_cosources_passes(self):
        # The lib32-glibc shape: the sibling carries a co-source (tzdata)
        # the twin legitimately omits.
        pkgs = {
            "glibc-core": _pkg("core",
                               source_shas=[self.SHA_A, self.SHA_T]),
            "lib32-glibc": _pkg("core", lib32_source="glibc-core",
                                elf_class="32",
                                source_shas=[self.SHA_A]),
        }
        self.assertEqual(vpt.lib32_audit(pkgs), {})

    def test_unpinned_twin_source_flags(self):
        pkgs = {
            "zlib": _pkg("core", source_shas=[self.SHA_A]),
            "lib32-zlib": _pkg("core", lib32_source="zlib",
                               elf_class="32", source_shas=[]),
        }
        v = vpt.lib32_audit(pkgs)
        self.assertTrue(any("no sha-pinned source" in m
                            for m in v.get("lib32-zlib", [])))

    def test_foreign_twin_patch_flags(self):
        pkgs = {
            "glibc-core": _pkg("core", source_shas=[self.SHA_A],
                               patch_shas=[self.SHA_T]),
            "lib32-glibc": _pkg("core", lib32_source="glibc-core",
                                elf_class="32", source_shas=[self.SHA_A],
                                patch_shas=[self.SHA_B]),
        }
        v = vpt.lib32_audit(pkgs)
        self.assertTrue(any("foreign patches" in m
                            for m in v.get("lib32-glibc", [])))

    def test_fixture_without_source_data_skips_identity(self):
        # None (loader-less fixtures) skips the gate — other rules still run.
        pkgs = {
            "zlib": _pkg("core"),
            "lib32-zlib": _pkg("core", lib32_source="zlib", elf_class="32"),
        }
        self.assertEqual(vpt.lib32_audit(pkgs), {})

    def test_dropped_sibling_patch_flags_omission(self):
        # W2-a latent edge (the Wave-2 verify's observation): a twin that
        # DROPS a library-affecting sibling patch builds divergent libs at
        # the same release string — the subset rule alone never saw it.
        pkgs = {
            "glibc-core": _pkg("core", source_shas=[self.SHA_A],
                               patch_shas=[self.SHA_T]),
            "lib32-glibc": _pkg("core", lib32_source="glibc-core",
                                elf_class="32", source_shas=[self.SHA_A],
                                patch_shas=[]),
        }
        v = vpt.lib32_audit(pkgs)
        self.assertTrue(any("LIB32-PATCH-OMISSION" in m
                            for m in v.get("lib32-glibc", [])))

    def test_twin_carrying_sibling_patch_verbatim_passes(self):
        # The real lib32-glibc shape: the fhs patch carried verbatim.
        pkgs = {
            "glibc-core": _pkg("core", source_shas=[self.SHA_A],
                               patch_shas=[self.SHA_T]),
            "lib32-glibc": _pkg("core", lib32_source="glibc-core",
                                elf_class="32", source_shas=[self.SHA_A],
                                patch_shas=[self.SHA_T]),
        }
        self.assertEqual(vpt.lib32_audit(pkgs), {})

    def test_governed_patch_omission_passes_ungoverned_still_flags(self):
        # The real lib32-mesa shape: the sibling's xdemos patch (64-bit demo
        # programs only) is a NAMED (twin, sha) entry in the governed
        # exemption set — that exact pair passes; any other dropped sibling
        # patch on the same twin still flags.
        xdemos = ("9677943764bfadc2800714e34933507365dfc24b33ec9d5a4"
                  "720db03b6168f3d")
        pkgs = {
            "mesa": _pkg("desktop", source_shas=[self.SHA_A],
                         patch_shas=[xdemos]),
            "lib32-mesa": _pkg("desktop", lib32_source="mesa",
                               elf_class="32", source_shas=[self.SHA_A],
                               patch_shas=[]),
        }
        self.assertEqual(vpt.lib32_audit(pkgs), {})
        pkgs["mesa"]["patch_shas"] = [xdemos, self.SHA_T]
        v = vpt.lib32_audit(pkgs)
        self.assertTrue(any("LIB32-PATCH-OMISSION" in m
                            for m in v.get("lib32-mesa", [])))


class TestRealTreeZeroBehaviorChange(unittest.TestCase):
    def test_real_tree_stays_green(self):
        r = subprocess.run([sys.executable, str(VALIDATOR)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
        self.assertIn("LIB32/MIXED=0", r.stdout)
        self.assertNotIn("MIXED-UNGOVERNED", r.stdout)


if __name__ == "__main__":
    unittest.main()
