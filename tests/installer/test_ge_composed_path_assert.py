"""Wedge tests for the RT-2 composed-path (pressure-vessel) proof.

Covers:
  * ge-composed-path-assert.py — the fail-closed assertor over
    steam-runtime-system-info JSON: green on both stacks resolving; red on
    a missing/broken i386 half (the exact RT-2 failure class: 64-bit works,
    32-bit silently doesn't); red on capsule-import library issues; red on
    schema drift / malformed input (a gate that cannot see must halt);
  * the REAL checks/gaming.sh check function, driven end-to-end in bash
    with a stubbed pkm + stubbed container entry point emitting fixture
    JSON — expectation gating (SKIP without the gaming meta), WARN vs
    strict-FAIL on a missing runtime, PASS/FAIL through the real pipeline.
"""

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ASSERTOR = REPO_ROOT / "installer" / "smoke" / "ge-composed-path-assert.py"
SMOKE_DIR = REPO_ROOT / "installer" / "smoke"


def _arch(can_run=True, issues=(), vk_issues=(), renderer="AMD RADV NAVI48",
          short="x86_64"):
    return {
        "can-run": can_run,
        "library-issues-summary": list(issues),
        "graphics-details": {
            f"{short}/vulkan": {
                "renderer": renderer,
                "issues": list(vk_issues),
            },
            f"{short}/glx": {"renderer": renderer},
        },
    }


def green_doc():
    return {"architectures": {
        "x86_64-linux-gnu": _arch(short="x86_64"),
        "i386-linux-gnu": _arch(short="i386"),
    }}


class TestAssertor(unittest.TestCase):
    def _run(self, doc):
        return subprocess.run(
            [sys.executable, str(ASSERTOR)],
            input=json.dumps(doc) if isinstance(doc, dict) else doc,
            capture_output=True, text=True)

    def test_green_both_stacks_resolve(self):
        r = self._run(green_doc())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("PASS", r.stderr)

    def test_red_i386_missing_entirely(self):
        # THE RT-2 class: the 64-bit half works, the 32-bit half was never
        # imported — component canaries pass, every game breaks.
        doc = green_doc()
        del doc["architectures"]["i386-linux-gnu"]
        r = self._run(doc)
        self.assertEqual(r.returncode, 1)
        self.assertIn("i386-linux-gnu", r.stderr)

    def test_red_i386_cannot_run(self):
        doc = green_doc()
        doc["architectures"]["i386-linux-gnu"]["can-run"] = False
        r = self._run(doc)
        self.assertEqual(r.returncode, 1)
        self.assertIn("can-run", r.stderr)

    def test_red_i386_vulkan_missing(self):
        doc = green_doc()
        del doc["architectures"]["i386-linux-gnu"]["graphics-details"]["i386/vulkan"]
        r = self._run(doc)
        self.assertEqual(r.returncode, 1)
        # the assertor now scans for any "*/vulkan" key (window-system-agnostic)
        # rather than the arch-spelled guess, so the message names the absence.
        self.assertIn("no '*/vulkan' entry", r.stderr)
        self.assertIn("i386", r.stderr)

    def test_red_capsule_import_issues(self):
        doc = green_doc()
        doc["architectures"]["i386-linux-gnu"]["library-issues-summary"] = [
            "libvulkan.so.1: missing"]
        r = self._run(doc)
        self.assertEqual(r.returncode, 1)
        self.assertIn("unresolved libraries", r.stderr)

    def test_red_vulkan_issues_named(self):
        doc = green_doc()
        doc["architectures"]["x86_64-linux-gnu"]["graphics-details"][
            "x86_64/vulkan"]["issues"] = ["cannot-load"]
        r = self._run(doc)
        self.assertEqual(r.returncode, 1)
        self.assertIn("cannot-load", r.stderr)

    def test_red_no_renderer_no_devices(self):
        doc = green_doc()
        doc["architectures"]["i386-linux-gnu"]["graphics-details"][
            "i386/vulkan"]["renderer"] = ""
        r = self._run(doc)
        self.assertEqual(r.returncode, 1)
        self.assertIn("enumerates nothing", r.stderr)

    def test_fail_closed_schema_drift(self):
        r = self._run({"something": "else"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("refusing to assume", r.stderr)

    def test_fail_closed_malformed_json(self):
        r = self._run("this is not json")
        self.assertEqual(r.returncode, 2)
        self.assertIn("could not see", r.stderr)

    # ---- WC re-cert F3-a close (2026-07-02) ----

    def test_f3a_software_rasterizer_rejected(self):
        # Both arches "resolve" vulkan to lavapipe — the GPU driver did NOT
        # capsule-import; games would run on CPU. Must FAIL, not pass.
        doc = green_doc()
        for triplet, short in (("x86_64-linux-gnu", "x86_64"),
                               ("i386-linux-gnu", "i386")):
            doc["architectures"][triplet]["graphics-details"][
                f"{short}/vulkan"]["renderer"] = "llvmpipe (LLVM 19.1, 256 bits)"
        r = self._run(doc)
        self.assertEqual(r.returncode, 1)
        self.assertIn("SOFTWARE rasterizer", r.stderr)
        self.assertIn("did not capsule-import", r.stderr)

    def test_f3a_lavapipe_case_insensitive(self):
        doc = green_doc()
        doc["architectures"]["i386-linux-gnu"]["graphics-details"][
            "i386/vulkan"]["renderer"] = "Lavapipe"
        r = self._run(doc)
        self.assertEqual(r.returncode, 1)
        self.assertIn("SOFTWARE rasterizer", r.stderr)

    def test_f3a_cpu_device_type_rejected(self):
        doc = green_doc()
        doc["architectures"]["i386-linux-gnu"]["graphics-details"][
            "i386/vulkan"]["devices"] = [
                {"name": "SomeSoft VK", "type": "cpu"}]
        r = self._run(doc)
        self.assertEqual(r.returncode, 1)
        self.assertIn("cpu-type device", r.stderr)

    def test_f3a_hyphenated_device_type_key_rejected(self):
        # Re-cert residual 1: srsi hyphenates key names, so the cpu reject
        # must fire on "device-type" too, not just "type".
        doc = green_doc()
        doc["architectures"]["i386-linux-gnu"]["graphics-details"][
            "i386/vulkan"]["devices"] = [
                {"name": "SomeSoft VK", "device-type": "cpu"}]
        r = self._run(doc)
        self.assertEqual(r.returncode, 1)
        self.assertIn("cpu-type device", r.stderr)

    def test_f3a_real_gpu_still_passes(self):
        doc = green_doc()
        doc["architectures"]["x86_64-linux-gnu"]["graphics-details"][
            "x86_64/vulkan"]["devices"] = [
                {"name": "AMD Radeon RX 9070 (RADV NAVI48)",
                 "type": "discrete-gpu"}]
        r = self._run(doc)
        self.assertEqual(r.returncode, 0, r.stderr)


class TestGamingCheckBash(unittest.TestCase):
    """Drive the REAL check_gaming_composed_path with stubbed pkm + entry."""

    def _harness(self, tmp, *, gaming_installed, runtime_json=None,
                 strict=False, entry_rc=0, runtime_dirs=("SteamLinuxRuntime_sniper",)):
        """Build a stub world and run the real check; returns its output.

        runtime_dirs lets a test pin a specific SLR generation (or several) —
        the locator is generation-agnostic, so SLR_4 alone, sniper alone, or
        both must all drive the composed-path proof."""
        t = Path(tmp)
        bindir = t / "bin"
        bindir.mkdir()
        pkm = bindir / "pkm"
        pkm.write_text("#!/bin/sh\n" +
                       ("exit 0\n" if gaming_installed else "exit 1\n"))
        pkm.chmod(pkm.stat().st_mode | stat.S_IEXEC)

        home = t / "home"
        common = home / ".steam/root/steamapps/common"
        if runtime_json is not None:
            payload = json.dumps(runtime_json)
            for gen in runtime_dirs:
                rt = common / gen
                rt.mkdir(parents=True)
                entry = rt / "_v2-entry-point"
                entry.write_text(
                    "#!/bin/sh\n"
                    f"[ {entry_rc} -ne 0 ] && exit {entry_rc}\n"
                    f"cat <<'EOF'\n{payload}\nEOF\n")
                entry.chmod(entry.stat().st_mode | stat.S_IEXEC)
        elif gaming_installed:
            # A steam library WITHOUT the runtime (pre-first-launch state).
            (home / ".steam/root/steamapps").mkdir(parents=True)

        script = f"""
set -u
SMOKE_STRICT={'1' if strict else '0'}
SMOKE_JSON=0
SMOKE_VERBOSE=0
SCRIPT_DIR="{SMOKE_DIR}"
. "{SMOKE_DIR}/lib.sh"
. "{SMOKE_DIR}/checks/gaming.sh"
check_gaming_composed_path
"""
        env = dict(os.environ,
                   PATH=f"{bindir}:{os.environ['PATH']}",
                   HOME=str(home))
        return subprocess.run(["bash", "-c", script], env=env,
                              capture_output=True, text=True)

    def test_skip_without_gaming_meta(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self._harness(tmp, gaming_installed=False)
            self.assertIn("SKIP", r.stdout)
            self.assertIn("not expected", r.stdout)

    def test_warn_runtime_absent_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self._harness(tmp, gaming_installed=True)
            self.assertIn("WARN", r.stdout)
            self.assertIn("NOT PROVEN", r.stdout)

    def test_strict_fail_runtime_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self._harness(tmp, gaming_installed=True, strict=True)
            self.assertIn("FAIL", r.stdout)
            self.assertIn("CANNOT be proven", r.stdout)

    def test_green_through_real_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self._harness(tmp, gaming_installed=True,
                              runtime_json=green_doc())
            self.assertIn("PASS", r.stdout)
            self.assertIn("resolve inside the pressure-vessel", r.stdout)

    def test_green_slr4_generation_probed(self):
        # The generation is per-tool: GE-Proton11/Proton 11 use SLR 4.0, NOT
        # sniper. The locator must find SLR_4 when only it is present.
        with tempfile.TemporaryDirectory() as tmp:
            r = self._harness(tmp, gaming_installed=True,
                              runtime_json=green_doc(),
                              runtime_dirs=("SteamLinuxRuntime_4",))
            self.assertIn("PASS", r.stdout)
            self.assertIn("1 runtime generation", r.stdout)

    def test_green_both_generations_probed(self):
        # Both installed -> both probed and proved.
        with tempfile.TemporaryDirectory() as tmp:
            r = self._harness(
                tmp, gaming_installed=True, runtime_json=green_doc(),
                runtime_dirs=("SteamLinuxRuntime_4", "SteamLinuxRuntime_sniper"))
            self.assertIn("PASS", r.stdout)
            self.assertIn("2 runtime generation", r.stdout)

    def test_strict_fail_no_generation_present(self):
        # gaming meta installed, steam library present, but no SLR generation
        # downloaded -> strict FAIL naming the probe set.
        with tempfile.TemporaryDirectory() as tmp:
            r = self._harness(tmp, gaming_installed=True, strict=True)
            self.assertIn("FAIL", r.stdout)
            self.assertIn("no Steam Linux Runtime", r.stdout)

    def test_red_broken_i386_through_real_pipeline(self):
        doc = green_doc()
        doc["architectures"]["i386-linux-gnu"]["can-run"] = False
        with tempfile.TemporaryDirectory() as tmp:
            r = self._harness(tmp, gaming_installed=True, runtime_json=doc)
            self.assertIn("FAIL", r.stdout)
            self.assertIn("can-run", r.stdout)

    def test_red_container_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self._harness(tmp, gaming_installed=True,
                              runtime_json=green_doc(), entry_rc=3)
            self.assertIn("FAIL", r.stdout)
            self.assertIn("could not compose", r.stdout)


def _real_arch(can_run=True, libraries_ok=True, vk_renderer="NVIDIA GeForce",
               devices=None, glx_renderer="NVIDIA GeForce"):
    """Report shaped like the REAL sniper steam-runtime-system-info output
    captured on-metal 2026-07-08: window-system-keyed graphics-details
    ("x11/vulkan", "glx/gl"), `libraries-ok` bool + `library-details`,
    hyphenated `device-type`."""
    if devices is None:
        devices = [{"name": vk_renderer, "device-type": "discrete-gpu",
                    "driver-name": "NVIDIA", "api-version": "1.4.312"}]
    return {
        "can-run": can_run,
        "libraries-ok": libraries_ok,
        "library-details": {"libGL.so.1": {"soname": None, "path": "/x"}},
        "graphics-details": {
            "x11/vulkan": {"renderer": vk_renderer, "version": "580",
                           "devices": devices},
            "glx/gl": {"renderer": glx_renderer},
            "x11/vdpau": {"renderer": vk_renderer},
        },
    }


class TestAssertorRealSniperSchema(unittest.TestCase):
    """Pins the assertor against the REAL sniper schema (the on-metal capture
    that the assumed-key fixtures above never exercised — item-5)."""

    def _run(self, doc):
        return subprocess.run(
            [sys.executable, str(ASSERTOR)], input=json.dumps(doc),
            capture_output=True, text=True)

    def _doc(self, x86=None, i386=None):
        return {"architectures": {
            "x86_64-linux-gnu": x86 if x86 is not None else _real_arch(),
            "i386-linux-gnu": i386 if i386 is not None else _real_arch(
                vk_renderer="Intel(R) Iris(R) Xe Graphics",
                devices=[{"name": "Intel(R) Iris(R) Xe Graphics",
                          "device-type": "integrated-gpu"}],
                glx_renderer="Mesa Intel"),
        }}

    def test_green_real_schema_both_resolve(self):
        r = self._run(self._doc())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("PASS", r.stderr)

    def test_green_cpu_fallback_alongside_real_gpu(self):
        # THE regression this fix closes: x86_64 vulkan enumerates NVIDIA +
        # Intel + llvmpipe(cpu) together with NVIDIA as renderer — the healthy
        # Mesa state. A cpu device in the list must NOT false-fail.
        x86 = _real_arch(devices=[
            {"name": "NVIDIA GeForce RTX 3070 Ti", "device-type": "discrete-gpu"},
            {"name": "Intel(R) Iris(R) Xe", "device-type": "integrated-gpu"},
            {"name": "llvmpipe (LLVM 21.1.8, 256 bits)", "device-type": "cpu"},
        ])
        r = self._run(self._doc(x86=x86))
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_green_i386_igpu_only(self):
        # the actual on-metal result: 32-bit resolves to the Intel iGPU only
        # (no dGPU, no software) — a real GPU, so the composed path PASSES.
        r = self._run(self._doc())  # default i386 is Intel integrated-gpu
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_red_libraries_not_ok(self):
        r = self._run(self._doc(x86=_real_arch(libraries_ok=False)))
        self.assertEqual(r.returncode, 1)
        self.assertIn("libraries-ok", r.stderr)

    def test_red_all_cpu_software_fallback(self):
        # i386 resolves ONLY to llvmpipe (renderer + sole device) — the GPU
        # driver did not capsule-import; must FAIL.
        i386 = _real_arch(vk_renderer="llvmpipe (LLVM 21.1.8, 256 bits)",
                          devices=[{"name": "llvmpipe", "device-type": "cpu"}],
                          glx_renderer="llvmpipe")
        r = self._run(self._doc(i386=i386))
        self.assertEqual(r.returncode, 1)
        self.assertIn("SOFTWARE rasterizer", r.stderr)

    def test_red_missing_both_library_keys_failcloses(self):
        x86 = _real_arch()
        del x86["libraries-ok"]  # neither libraries-ok nor library-issues-summary
        r = self._run(self._doc(x86=x86))
        self.assertEqual(r.returncode, 1)
        self.assertIn("cannot", r.stderr)


if __name__ == "__main__":
    unittest.main()
