#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Smoke tests for the NVIDIA EULA install-helper.

Covers every non-UI exit path:
  * marker present -> exit 0 without reading the EULA sidecar
  * marker malformed -> treated as missing -> read + prompt
  * non-TTY -> exit 4 with TTY-required message
  * bundled-EULA read failure (missing/empty/oversized sidecar) ->
    exit 2 with clear message (PI-Z15: the text ships in the archive;
    a miss means corrupted install media, fail-closed)
  * accept path -> marker + transcript written atomically with
    correct shape (sha256, bundled-source provenance, timestamp, no PII)
  * banner.txt is reachable from the helper directory

The interactive pager itself (run_pager) is exercised by a
prompt_toolkit-headed test that drives synthetic keypresses via
the DummyOutput / PipeInput facility — verifies that ACCEPT is
pre-focused (Enter on default -> accepted=True) and Esc -> DECLINE.
"""

import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

HELPER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HELPER_DIR))

# Import the helper module under test. The .py extension is in the
# filename so we use importlib for the dashed module name.
import importlib.util

_spec = importlib.util.spec_from_file_location(
    "nvidia_eula", str(HELPER_DIR / "nvidia-eula.py"),
)
nvidia_eula = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nvidia_eula)


class BannerShipsTest(unittest.TestCase):
    """The banner.txt asset ships alongside the helper script."""

    def test_banner_file_exists(self):
        self.assertTrue(nvidia_eula.BANNER_PATH.is_file())

    def test_banner_mentions_nvidia_and_bundled_source(self):
        text = nvidia_eula.BANNER_PATH.read_text(encoding="utf-8")
        self.assertIn("NVIDIA", text)
        # PI-Z15: the EULA is bundled, not fetched — the banner must
        # say so (and must NOT promise a network fetch).
        self.assertIn("bundled", text)
        self.assertNotIn("download.nvidia.com", text)


class ReadEulaTest(unittest.TestCase):
    """read_eula fail-closed branches (PI-Z15 bundled-sidecar design).

    The real sidecar (nvidia-eula.LICENSE) is staged at PACKAGE BUILD
    time from the .run's LICENSE — it does not exist in the source
    tree, so these tests drive read_eula against tmp files only.
    build.sh's [ -s ] assert + verify_paths cover the staged artifact.
    """

    def test_reads_text_and_returns_raw_plus_decoded(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "LICENSE"
            path.write_text("NVIDIA EULA BODY", encoding="utf-8")
            raw, text = nvidia_eula.read_eula(path)
            self.assertEqual(raw, b"NVIDIA EULA BODY")
            self.assertEqual(text, "NVIDIA EULA BODY")

    def test_missing_sidecar_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                nvidia_eula.read_eula(Path(tmp) / "absent")

    def test_empty_sidecar_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "LICENSE"
            path.write_bytes(b"")
            with self.assertRaises(RuntimeError):
                nvidia_eula.read_eula(path)

    def test_oversized_sidecar_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "LICENSE"
            path.write_bytes(b"x" * (nvidia_eula.EULA_MAX_BYTES + 1))
            with self.assertRaises(RuntimeError):
                nvidia_eula.read_eula(path)

    def test_non_utf8_falls_back_to_latin1(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "LICENSE"
            path.write_bytes(b"caf\xe9")  # latin-1 e-acute, invalid UTF-8
            raw, text = nvidia_eula.read_eula(path)
            self.assertEqual(raw, b"caf\xe9")
            self.assertEqual(text, "caf\xe9".encode("latin-1").decode("latin-1"))


class MarkerPresentTest(unittest.TestCase):
    """marker_present returns True only for a real JSON-parseable file."""

    def test_missing_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(
                nvidia_eula.marker_present(Path(tmp) / "absent"),
            )

    def test_valid_json_returns_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "marker"
            path.write_text(json.dumps({"accepted_at": "2026-01-01T00:00:00Z"}))
            self.assertTrue(nvidia_eula.marker_present(path))

    def test_malformed_json_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "marker"
            path.write_text("{not valid json")
            self.assertFalse(nvidia_eula.marker_present(path))

    def test_truncated_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "marker"
            path.write_text("")
            self.assertFalse(nvidia_eula.marker_present(path))


class WriteMarkerAndTranscriptTest(unittest.TestCase):
    """Marker + transcript writes are atomic + carry the expected fields."""

    def test_writes_both_files_with_correct_perms(self):
        with tempfile.TemporaryDirectory() as tmp:
            eula_dir = Path(tmp) / "eula"
            raw = b"EULA TEXT HERE"
            marker_path, transcript_path = (
                nvidia_eula.write_marker_and_transcript(
                    raw_eula=raw,
                    eula_text="EULA TEXT HERE",
                    source="bundled: LICENSE from example.run",
                    version_string="NVIDIA-Linux-x86_64-580.159.04",
                    eula_dir=eula_dir,
                )
            )
            self.assertTrue(marker_path.is_file())
            self.assertTrue(transcript_path.is_file())
            self.assertEqual(
                marker_path.name, "nvidia-userspace.accepted",
            )
            self.assertTrue(
                transcript_path.name.startswith("nvidia-userspace-")
            )
            self.assertTrue(transcript_path.name.endswith(".txt"))

            # File mode — 0o644 (chmod is best-effort but should land
            # when we own the file we just wrote in tmpdir).
            marker_mode = marker_path.stat().st_mode & 0o777
            transcript_mode = transcript_path.stat().st_mode & 0o777
            self.assertEqual(marker_mode, 0o644)
            self.assertEqual(transcript_mode, 0o644)

    def test_marker_carries_sha256_source_timestamp_no_pii(self):
        with tempfile.TemporaryDirectory() as tmp:
            eula_dir = Path(tmp) / "eula"
            raw = b"hash me"
            marker_path, _ = nvidia_eula.write_marker_and_transcript(
                raw_eula=raw,
                eula_text="hash me",
                source="bundled: LICENSE from example.run",
                version_string="NVIDIA-Linux-x86_64-580.159.04",
                eula_dir=eula_dir,
            )
            with open(str(marker_path), "r") as f:
                payload = json.load(f)
            self.assertIn("accepted_at", payload)
            self.assertIn("eula_sha256", payload)
            self.assertEqual(payload["eula_source"],
                             "bundled: LICENSE from example.run")
            self.assertEqual(
                payload["eula_version_string"],
                "NVIDIA-Linux-x86_64-580.159.04",
            )
            self.assertEqual(payload["captured_pii"],
                             "none (no username, hostname, or machine-id)")
            # security-only-alignment explicit-no-PII: marker MUST NOT contain a
            # username, hostname, or any identifier beyond the timestamp.
            for forbidden in ("user", "username", "hostname", "machine_id"):
                self.assertNotIn(
                    forbidden, payload,
                    f"marker carries PII field {forbidden!r}",
                )

    def test_transcript_records_verbatim_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            eula_dir = Path(tmp) / "eula"
            eula_body = "VERBATIM\nEULA\nTEXT"
            _, transcript_path = nvidia_eula.write_marker_and_transcript(
                raw_eula=eula_body.encode("utf-8"),
                eula_text=eula_body,
                source="bundled: LICENSE from example.run",
                version_string="x",
                eula_dir=eula_dir,
            )
            self.assertEqual(
                transcript_path.read_text(encoding="utf-8"),
                eula_body,
            )


class MainExitCodesTest(unittest.TestCase):
    """Each main() exit path returns the documented integer code."""

    def test_marker_present_returns_0_without_reading_eula(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "marker"
            marker.write_text(json.dumps({"accepted_at": "x"}))

            with patch.object(nvidia_eula, "MARKER_FILE", marker), \
                 patch.object(nvidia_eula, "read_eula") as mock_read:
                rc = nvidia_eula.main([])
                self.assertEqual(rc, 0)
                mock_read.assert_not_called()

    def test_non_tty_returns_4(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "absent"
            with patch.object(nvidia_eula, "MARKER_FILE", marker), \
                 patch.object(sys, "stdin", io.StringIO("")), \
                 patch.object(sys, "stderr", io.StringIO()) as err:
                # sys.stdout is a StringIO -> isatty() returns False.
                rc = nvidia_eula.main([])
                self.assertEqual(rc, 4)
                self.assertIn("TTY", err.getvalue())

    def test_read_failure_returns_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "absent"
            # Force the TTY check to pass + the sidecar read to fail.
            stdin_tty = io.StringIO()
            stdin_tty.isatty = lambda: True
            stdout_tty = io.StringIO()
            stdout_tty.isatty = lambda: True
            with patch.object(nvidia_eula, "MARKER_FILE", marker), \
                 patch.object(sys, "stdin", stdin_tty), \
                 patch.object(sys, "stdout", stdout_tty), \
                 patch.object(sys, "stderr", io.StringIO()) as err, \
                 patch.object(nvidia_eula, "read_eula",
                              side_effect=RuntimeError("sidecar missing")):
                rc = nvidia_eula.main([])
                self.assertEqual(rc, 2)
                self.assertIn("Could not read", err.getvalue())
                self.assertIn("bundled", err.getvalue())

    def test_decline_returns_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "absent"
            stdin_tty = io.StringIO()
            stdin_tty.isatty = lambda: True
            stdout_tty = io.StringIO()
            stdout_tty.isatty = lambda: True
            with patch.object(nvidia_eula, "MARKER_FILE", marker), \
                 patch.object(sys, "stdin", stdin_tty), \
                 patch.object(sys, "stdout", stdout_tty), \
                 patch.object(nvidia_eula, "read_eula",
                              return_value=(b"text", "text")), \
                 patch.object(nvidia_eula, "run_pager", return_value=False):
                rc = nvidia_eula.main([])
                self.assertEqual(rc, 1)
                self.assertIn("declined", stdout_tty.getvalue())
                # Marker MUST NOT be written on decline.
                self.assertFalse(marker.exists())

    def test_accept_writes_marker_and_returns_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            eula_dir = Path(tmp) / "eula"
            stdin_tty = io.StringIO()
            stdin_tty.isatty = lambda: True
            stdout_tty = io.StringIO()
            stdout_tty.isatty = lambda: True
            # marker_present check uses MARKER_FILE -> point both
            # MARKER_FILE and EULA_DIR at the tmpdir.
            with patch.object(nvidia_eula, "MARKER_FILE",
                              eula_dir / "nvidia-userspace.accepted"), \
                 patch.object(nvidia_eula, "EULA_DIR", eula_dir), \
                 patch.object(sys, "stdin", stdin_tty), \
                 patch.object(sys, "stdout", stdout_tty), \
                 patch.object(nvidia_eula, "read_eula",
                              return_value=(b"the EULA", "the EULA")), \
                 patch.object(nvidia_eula, "run_pager", return_value=True):
                rc = nvidia_eula.main([])
                self.assertEqual(rc, 0)
                marker = eula_dir / "nvidia-userspace.accepted"
                self.assertTrue(marker.is_file())
                with open(str(marker)) as f:
                    payload = json.load(f)
                self.assertIn("eula_sha256", payload)
                self.assertEqual(payload["captured_pii"],
                                 "none (no username, hostname, or machine-id)")


class PagerKeybindingsTest(unittest.TestCase):
    """run_pager wires ACCEPT pre-highlighted + Esc -> DECLINE.

    Driven via prompt_toolkit's PipeInput / DummyOutput so we exercise
    the real Application code path without needing a tty. If
    prompt_toolkit is not installed in the test env, the whole class
    is skipped.
    """

    def setUp(self):
        try:
            import prompt_toolkit  # noqa: F401
        except ImportError:
            self.skipTest("prompt_toolkit not installed in test env")

    def test_enter_on_default_focus_accepts(self):
        """ACCEPT is pre-highlighted; pressing Enter on default focus
        without Tab should fire on_accept and yield True.

        Drives the real Application via PipeInput + DummyOutput so the
        focused-button + key-binding wiring is exercised end-to-end.
        """
        from prompt_toolkit.input import create_pipe_input
        from prompt_toolkit.output import DummyOutput

        with create_pipe_input() as pipe_input:
            # Enter on the default (ACCEPT) focus activates on_accept.
            # Both \r and \n are accepted enter keys in prompt_toolkit.
            pipe_input.send_text("\r")
            accepted = nvidia_eula.run_pager(
                "EULA TEXT",
                _input=pipe_input,
                _output=DummyOutput(),
            )
        self.assertTrue(accepted)

    def test_escape_declines(self):
        """Esc fires the escape binding -> result["accepted"] = False."""
        from prompt_toolkit.input import create_pipe_input
        from prompt_toolkit.output import DummyOutput

        with create_pipe_input() as pipe_input:
            pipe_input.send_text("\x1b")  # Esc -> DECLINE binding
            accepted = nvidia_eula.run_pager(
                "EULA TEXT",
                _input=pipe_input,
                _output=DummyOutput(),
            )
        self.assertFalse(accepted)


if __name__ == "__main__":
    unittest.main()
