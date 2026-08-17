#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""nvidia-eula — InterGenOS EULA install-helper for the NVIDIA proprietary userspace.

Hybrid model (first instance in the project, ratified 2026-05-28):
  * pkm sees the nvidia package has an `eula_helper: nvidia-eula` field
    in .PKGINFO + invokes this helper BEFORE the package install
    proceeds.
  * System-wide marker `/var/lib/intergen/eula/nvidia-userspace.accepted`
    gates first-install. If present, this helper exits 0 immediately
    (no re-prompt — decided posture: "only on the first
    install, I don't ever remember re-accepting a eula for any other
    distro. Ever.").
  * Marker missing → reads the EULA text BUNDLED with this helper
    (staged at build time from the LICENSE file inside NVIDIA's own
    .run installer for the exact driver version being installed) +
    presents it in a prompt_toolkit full-screen pager with
    ACCEPT/DECLINE buttons. ACCEPT is pre-highlighted so Enter on the
    default-focused control just works.

    DESIGN SUPERSESSION (operator GO 2026-07-06, PI-Z15): the original
    live-fetch design (decided 2026-05-28: "fetches from
    Nvidia- I'm not interested in maintaining their EULA in our repo")
    proved unrunnable-by-construction — the assumed
    download.nvidia.com/.../<ver>/LICENSE URL pattern never existed
    (HTTP 404 across driver generations, discovered on the first live
    gate run, Zephyrus GE-02). The bundled text honors the original
    concern: it is NVIDIA's own document, extracted from NVIDIA's own
    installer at build time, never maintained in our repo, and
    bit-identical to what `nvidia-installer` itself displays at its
    interactive accept — the EULA that governs version X is the one
    shipped inside version X's installer. It also removes the
    network dependency from a gate that must work on offline installs
    (a fresh Forge target installs from local archives).
  * ACCEPT → write the marker + the verbatim EULA text + sha256 +
    bundled-source provenance + timestamp to system-wide path.
  * DECLINE → exit 1 with a plain-English non-cryptic message. The
    open-source nouveau driver (already in kernel) remains the active
    GPU driver.

Exit codes (consumed by pkm.installer's pre-install EULA gate):
  0 — marker already present OR newly accepted; pkm proceeds with
      the nvidia package install.
  1 — user declined the EULA in the interactive pager.
  2 — could not read the bundled EULA text (sidecar missing /
      unreadable / empty — corrupted install media). Cannot proceed
      without the user reviewing the EULA.
  3 — could not create / write the marker file or accepted-EULA
      transcript at /var/lib/intergen/eula/ (filesystem error).
  4 — interactive TTY required (e.g. invoked from cron, scripted
      install, non-interactive shell) — the EULA review needs a
      real terminal.

Files written on ACCEPT (atomic via tempfile + os.replace):
  /var/lib/intergen/eula/nvidia-userspace.accepted
      JSON marker: {accepted_at, eula_sha256, eula_source,
      eula_version_string}. Mode 0o644, owner root:root. Presence
      gates re-prompt on subsequent pkm install nvidia invocations.

  /var/lib/intergen/eula/nvidia-userspace-<accepted-at>-<sha256-short>.txt
      Verbatim EULA text the user accepted. Mode 0o644, owner
      root:root. Provides after-the-fact audit-ability: if NVIDIA
      ever asks "what license did you accept", the file proves
      exactly what was on screen at acceptance time.

Security-only alignment:
  * No PII captured beyond the accepted_at timestamp (no username,
    no hostname, no MAC, no machine-id).
  * sha256 + bundled-source provenance embedded in marker so
    post-acceptance verification is possible (third-party can extract
    LICENSE from the same versioned .run — sha-pinned in package.yml —
    hash + compare).
  * Atomic writes — partial-write states are not observable to a
    subsequent install attempt.

Prime Directive alignment: every screen + every banner explains
what's happening + where the EULA came from + what each choice does.
Nothing hidden. The EULA text is NVIDIA's own LICENSE from the exact
.run installer this package was built from — the terms that actually
govern the version being installed, shown bit-identical to what
NVIDIA's own installer displays at its interactive accept.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Marker + transcript live under /var/lib/intergen/eula/. The package's
# build.sh creates this directory at install time (mode 0o755 root:root)
# so the marker write here never has to mkdir from a missing parent
# under unusual permission contexts. Defensive mkdir below still tries
# to create it if absent.
EULA_DIR = Path("/var/lib/intergen/eula")
MARKER_FILE = EULA_DIR / "nvidia-userspace.accepted"

# The driver version this helper ships with. build.sh asserts this
# constant equals its NV_VERSION at build time (fail-closed lockstep —
# the same drift class that bit kernel verify_paths on the r3 bump).
NVIDIA_DRIVER_VERSION = "580.159.04"

# The bundled EULA text. build.sh stages the LICENSE file from inside
# NVIDIA's .run installer (sha-pinned in package.yml) alongside this
# helper at build time. Sidecar resolution via Path(__file__) matches
# banner.txt below — and survives pkm's first-install archive-fallback,
# which extracts the whole eula-helpers/ subtree into a tempdir
# (pkm.installer._extract_eula_helper_from_archive, PI-Z6 fix).
#
# HISTORY (PI-Z15): this replaced a live fetch of
# https://us.download.nvidia.com/XFree86/Linux-x86_64/<ver>/LICENSE —
# a URL pattern that never existed (404 across driver generations);
# the in-code "stable for ~15 years" claim was never verified. See the
# module docstring's DESIGN SUPERSESSION note.
EULA_TEXT_PATH = Path(__file__).resolve().parent / "nvidia-eula.LICENSE"

# Human-readable provenance recorded in the acceptance marker.
EULA_SOURCE = (
    f"bundled: LICENSE from NVIDIA-Linux-x86_64-"
    f"{NVIDIA_DRIVER_VERSION}.run (staged at package build)"
)

# Banner shipped alongside the helper (read at runtime; not inlined).
BANNER_PATH = Path(__file__).resolve().parent / "banner.txt"

# Maximum EULA size we accept. The real document is ~22 KB; 1 MiB is
# a sanity bound — a bigger sidecar means corrupted install media,
# and we refuse to page it rather than render garbage.
EULA_MAX_BYTES = 1024 * 1024


# ---------------------------------------------------------------------------
# Banner + fetch
# ---------------------------------------------------------------------------

def print_banner() -> None:
    """Print the loud banner that introduces the EULA review."""
    try:
        text = BANNER_PATH.read_text(encoding="utf-8")
    except OSError:
        # Fallback if the banner file got lost from the install — never
        # rely on the file being there.
        text = (
            "===============================================================\n"
            " NVIDIA PROPRIETARY USERSPACE - END USER LICENSE AGREEMENT\n"
            " Required acceptance before InterGenOS can install the NVIDIA\n"
            " userspace libraries on your system. Presenting the EULA\n"
            " bundled with this driver version - reviewing now.\n"
            "===============================================================\n"
        )
    sys.stdout.write(text)
    sys.stdout.flush()


def read_eula(path: Path = EULA_TEXT_PATH) -> tuple[bytes, str]:
    """Read the bundled NVIDIA EULA staged alongside this helper.

    Returns (raw_bytes, decoded_text). raw_bytes is hashed for the
    marker file; decoded_text is what we render in the pager.

    Raises:
        RuntimeError: on any read / decode / size / empty-file failure.
        Caller translates into the exit-2 message + return code.
        Fail-closed: the sidecar ships in the same signature-verified
        archive as the driver bits, so a miss means corrupted install
        media — never proceed without the text on screen.
    """
    try:
        raw = path.read_bytes()
    except OSError as e:
        raise RuntimeError(f"Cannot read bundled EULA at {path}: {e}")

    if not raw:
        raise RuntimeError(
            f"Bundled EULA at {path} is EMPTY — corrupted install "
            f"media; refusing to present a blank license."
        )
    if len(raw) > EULA_MAX_BYTES:
        raise RuntimeError(
            f"Bundled EULA at {path} exceeds the {EULA_MAX_BYTES} byte "
            f"sanity cap; refusing to load. Inspect manually."
        )

    # Decode for display. NVIDIA's LICENSE blob is plain ASCII / UTF-8
    # in practice; fall back to latin-1 if upstream ever rotates encoding
    # so the pager still gets renderable text rather than a stack trace.
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1", errors="replace")

    return raw, text


# ---------------------------------------------------------------------------
# Pager UI
# ---------------------------------------------------------------------------

def run_pager(eula_text: str, _input=None, _output=None) -> bool:
    """Show the EULA in a full-screen prompt_toolkit pager.

    UX contract (decided):
      * Scrollable EULA body using a ScrollablePane wrapping a
        FormattedTextControl. Up/Down/PgUp/PgDn/Home/End scroll.
      * Two buttons at the bottom: ACCEPT (pre-highlighted) +
        DECLINE. Tab toggles focus between them. Enter activates
        the focused button. Esc -> DECLINE (consistent with Forge
        installer Esc-to-cancel pattern).
      * No hidden shortcuts. The footer hint enumerates every key
        binding so the user does not need to Google what to press.

    Returns True if the user pressed ACCEPT (or Enter on the default
    focus), False on DECLINE / Esc / Ctrl-C.

    Test-injection kwargs: _input and _output forward to
    Application(input=, output=). Production callers leave them None
    and prompt_toolkit picks up the controlling terminal. Tests pass
    a PipeInput + DummyOutput pair so the Application can be driven
    without a real tty.
    """
    # Import lazily so unit tests can exercise non-UI paths without a
    # prompt_toolkit dependency installed in every test harness.
    from prompt_toolkit.application import Application
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.document import Document
    from prompt_toolkit.filters import has_focus
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import (
        Float,
        FloatContainer,
        HSplit,
        Layout,
        ScrollablePane,
        VSplit,
        Window,
    )
    from prompt_toolkit.layout.controls import (
        BufferControl,
        FormattedTextControl,
    )
    from prompt_toolkit.layout.dimension import Dimension
    from prompt_toolkit.styles import Style
    from prompt_toolkit.widgets import Button, Frame, Label

    # The result mailbox. Closing the Application from a button handler
    # writes the answer here; main() reads after Application.run().
    result: dict[str, bool] = {"accepted": False}

    # EULA body — a read-only buffer with the full text. BufferControl +
    # Window gives us cursor-driven scroll for free (PageUp/Down,
    # arrow keys, Home/End). FormattedTextControl would be cleaner for
    # static text, but BufferControl gives the right scroll behavior
    # out-of-the-box with no extra wiring.
    eula_buffer = Buffer(
        document=Document(text=eula_text, cursor_position=0),
        read_only=True,
        multiline=True,
    )

    eula_control = BufferControl(buffer=eula_buffer, focusable=True)
    eula_window = Window(
        content=eula_control,
        wrap_lines=True,
        always_hide_cursor=False,
        style="class:eula-body",
    )

    # Buttons. ACCEPT is the default focus per the decision
    # ("ACCEPT being already highlighted ... no one knows ... you have
    # to hit 'tab' in the ubuntu package I'm referring to- everyone has
    # to google it").
    def on_accept() -> None:
        result["accepted"] = True
        app.exit()

    def on_decline() -> None:
        result["accepted"] = False
        app.exit()

    accept_button = Button(text="ACCEPT", handler=on_accept, width=12)
    decline_button = Button(text="DECLINE", handler=on_decline, width=12)

    # Header label — restates what the user is about to decide.
    header = Label(
        text=(
            "NVIDIA proprietary userspace EULA. Source: "
            f"{EULA_SOURCE}\n"
            "Scroll: Up/Down/PgUp/PgDn/Home/End. "
            "Tab: switch ACCEPT <-> DECLINE. Enter: activate focused "
            "button. Esc: DECLINE."
        ),
    )

    # Footer hint — repeats key bindings so the user never has to guess.
    footer = Label(
        text=(
            "[ACCEPT] = install NVIDIA proprietary userspace. "
            "[DECLINE] = abort install + stay on nouveau (open-source)."
        ),
    )

    button_row = VSplit(
        [
            Window(width=Dimension(weight=1)),  # left spacer
            accept_button,
            Window(width=2, char=" "),
            decline_button,
            Window(width=Dimension(weight=1)),  # right spacer
        ],
        padding=0,
        height=1,
    )

    body = HSplit(
        [
            Frame(header, title="EULA review"),
            Frame(eula_window, title="License text"),
            Frame(button_row, title="Decision"),
            footer,
        ],
    )

    layout = Layout(container=body, focused_element=accept_button)

    kb = KeyBindings()

    @kb.add("tab")
    def _(event):
        # Cycle focus between ACCEPT, DECLINE, and the EULA body so
        # the user can scroll the body via Tab into the buffer.
        focus_order = [accept_button, decline_button, eula_window]
        current = event.app.layout.current_window
        try:
            idx = focus_order.index(current)
        except ValueError:
            idx = -1
        next_focus = focus_order[(idx + 1) % len(focus_order)]
        event.app.layout.focus(next_focus)

    @kb.add("s-tab")
    def _(event):
        # Reverse-tab.
        focus_order = [accept_button, decline_button, eula_window]
        current = event.app.layout.current_window
        try:
            idx = focus_order.index(current)
        except ValueError:
            idx = 0
        prev_focus = focus_order[(idx - 1) % len(focus_order)]
        event.app.layout.focus(prev_focus)

    @kb.add("escape")
    def _(event):
        # Esc -> DECLINE. Consistent with Forge installer "Esc to cancel"
        # pattern + the decided UX contract.
        result["accepted"] = False
        event.app.exit()

    @kb.add("c-c")
    def _(event):
        # Ctrl-C -> DECLINE. SIGINT during EULA review is "I don't want
        # to commit"; mapping to DECLINE is less surprising than a
        # KeyboardInterrupt traceback.
        result["accepted"] = False
        event.app.exit()

    # When the EULA body has focus, the standard cursor-movement keys
    # already scroll it (BufferControl default bindings). No extra
    # bindings needed for PgUp/PgDn/Home/End.

    # Light styling — distinguish frame titles + button focus from body
    # text. Color choices are subtle so this works on both dark and light
    # terminals.
    style = Style.from_dict({
        "frame.label": "bold",
        "frame.border": "ansigreen",
        "button.focused": "bg:ansigreen #000000 bold",
        "eula-body": "",
    })

    app_kwargs = dict(
        layout=layout,
        key_bindings=kb,
        full_screen=True,
        mouse_support=True,
        style=style,
    )
    if _input is not None:
        app_kwargs["input"] = _input
    if _output is not None:
        app_kwargs["output"] = _output

    app: Application = Application(**app_kwargs)
    app.run()
    return result["accepted"]


# ---------------------------------------------------------------------------
# Marker write
# ---------------------------------------------------------------------------

def write_marker_and_transcript(
    raw_eula: bytes,
    eula_text: str,
    source: str,
    version_string: str,
    eula_dir: Optional[Path] = None,
) -> tuple[Path, Path]:
    """Atomic-write the marker JSON + the verbatim-text transcript.

    Both files are written under eula_dir. Mode 0o644, owner root:root
    inherits from the process running this helper (pkm runs as root
    when invoked via `pkm install`; this helper inherits that uid).

    Atomicity:
      * Each file is written to a sibling `.tmp` path then os.replace'd
        into final position. POSIX-atomic within the same filesystem.
      * If the transcript write succeeds but the marker write fails,
        the transcript stays on disk (cheap leak) but the marker is
        absent — so the next pkm install nvidia attempt re-prompts.
        Failing closed on the gate is correct.

    Returns (marker_path, transcript_path) on success.

    Raises OSError on any filesystem failure; caller translates to
    exit code 3.

    Default-arg note: eula_dir defaults to None and resolves to the
    MODULE-LEVEL EULA_DIR at call time. Same rationale as
    marker_present's marker_path default (test isolation via
    patch.object would otherwise be defeated by definition-time
    capture of the default).
    """
    if eula_dir is None:
        eula_dir = EULA_DIR
    eula_dir.mkdir(parents=True, exist_ok=True, mode=0o755)
    try:
        os.chmod(str(eula_dir), 0o755)
    except OSError:
        # If we cannot chmod (e.g. dir already exists with stricter
        # perms set by an admin), do not fight it — proceed with the
        # write. The contents are world-readable JSON either way.
        pass

    digest = hashlib.sha256(raw_eula).hexdigest()
    accepted_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    short_sha = digest[:16]

    transcript_path = eula_dir / (
        f"nvidia-userspace-{accepted_at}-{short_sha}.txt"
    )

    # Write transcript first. If the marker write subsequently fails,
    # the next attempt re-fetches + re-prompts and writes a fresh
    # transcript; the orphan from this attempt is harmless.
    tmp_transcript = transcript_path.with_name(transcript_path.name + ".tmp")
    with open(str(tmp_transcript), "w", encoding="utf-8") as f:
        f.write(eula_text)
    os.chmod(str(tmp_transcript), 0o644)
    os.replace(str(tmp_transcript), str(transcript_path))

    marker_payload = {
        "accepted_at": accepted_at,
        "eula_sha256": digest,
        "eula_source": source,
        "eula_version_string": version_string,
        "transcript_path": str(transcript_path),
        # Security-only alignment: explicitly enumerate the absence of PII so a
        # future reader can audit the marker schema + see the gap is
        # intentional.
        "captured_pii": "none (no username, hostname, or machine-id)",
    }
    marker_path = eula_dir / "nvidia-userspace.accepted"
    tmp_marker = marker_path.with_name(marker_path.name + ".tmp")
    with open(str(tmp_marker), "w", encoding="utf-8") as f:
        json.dump(marker_payload, f, indent=2, sort_keys=True)
        f.write("\n")
    os.chmod(str(tmp_marker), 0o644)
    os.replace(str(tmp_marker), str(marker_path))

    return marker_path, transcript_path


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def marker_present(marker_path: Optional[Path] = None) -> bool:
    """Return True iff the marker file exists and parses as JSON.

    Existence-only would be technically sufficient per the recorded decision
    spec ("presence = accepted"), but a JSON-parse check catches the
    case where the file was truncated by a crashed earlier write
    (the os.replace atomicity above prevents this in practice, but
    a stray `:>` from a shell redirect could still corrupt it).

    Default-arg note: the path defaults to None and resolves to the
    MODULE-LEVEL MARKER_FILE at call time. Capturing MARKER_FILE in
    the parameter default would freeze the value at function-definition
    time, which breaks tests that patch.object(nvidia_eula, "MARKER_FILE", ...)
    to redirect the marker check to a tmpdir.
    """
    if marker_path is None:
        marker_path = MARKER_FILE
    if not marker_path.is_file():
        return False
    try:
        with open(str(marker_path), "r", encoding="utf-8") as f:
            json.load(f)
    except (OSError, json.JSONDecodeError):
        return False
    return True


def main(argv: Optional[list[str]] = None) -> int:
    """Helper entrypoint.

    Returns the integer exit code (also passed to sys.exit). Public
    return-int contract is used by the test suite to drive non-UI
    branches without forking a subprocess.
    """
    # Marker check first. Decided posture: first install only,
    # never re-prompt. No CLI flag overrides this — re-accept requires
    # the operator to manually delete the marker, surfacing the intent.
    if marker_present():
        sys.stdout.write(
            f"  NVIDIA EULA already accepted on this system "
            f"(marker: {MARKER_FILE}). Proceeding with install.\n"
        )
        sys.stdout.flush()
        return 0

    # TTY gate. The pager is interactive; cron / scripted installs
    # cannot drive ACCEPT/DECLINE. Surface clearly rather than hang
    # waiting for input that will never come.
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        sys.stderr.write(
            "  ERROR: interactive TTY required to review the NVIDIA EULA.\n"
            "  Run this command from an interactive shell.\n"
            "  (cron / scripted-install / non-interactive contexts cannot\n"
            "  drive the ACCEPT/DECLINE buttons.)\n"
        )
        return 4

    # Banner first so the user sees what is about to happen.
    print_banner()

    try:
        raw_eula, eula_text = read_eula(EULA_TEXT_PATH)
    except RuntimeError as e:
        sys.stderr.write(
            f"  ERROR: Could not read the NVIDIA EULA bundled with this\n"
            f"  package. The EULA text ships inside the nvidia archive\n"
            f"  itself — a miss means the install media is incomplete or\n"
            f"  corrupted. Re-download / re-stage the nvidia archive and\n"
            f"  try again. Cannot proceed without you reviewing the EULA.\n"
            f"  Details: {e}\n"
        )
        return 2

    # Pager — blocking, full-screen. Returns True on ACCEPT.
    try:
        accepted = run_pager(eula_text)
    except KeyboardInterrupt:
        accepted = False
    except Exception as e:
        # Defensive: any prompt_toolkit error (terminal capability
        # mismatch, etc.) should NOT silently auto-accept. Treat as
        # DECLINE + surface the error so the user knows why.
        sys.stderr.write(
            f"  ERROR: EULA pager failed to render: {e}\n"
            f"  Treating as DECLINE. Re-run after fixing the terminal "
            f"capability mismatch.\n"
        )
        return 1

    if not accepted:
        sys.stdout.write(
            "  NVIDIA EULA declined — InterGenOS will not install the\n"
            "  NVIDIA proprietary userspace. The open-source nouveau\n"
            "  driver remains your active GPU driver.\n"
        )
        sys.stdout.flush()
        return 1

    # ACCEPT path. Write marker + transcript, atomically.
    try:
        marker_path, transcript_path = write_marker_and_transcript(
            raw_eula=raw_eula,
            eula_text=eula_text,
            source=EULA_SOURCE,
            version_string=f"NVIDIA-Linux-x86_64-{NVIDIA_DRIVER_VERSION}",
        )
    except OSError as e:
        sys.stderr.write(
            f"  ERROR: Could not write the EULA acceptance marker to\n"
            f"  {EULA_DIR}. Filesystem error: {e}\n"
            f"  Cannot proceed without recording acceptance — re-run as\n"
            f"  root once the underlying issue (disk full, read-only\n"
            f"  filesystem, missing parent dir) is resolved.\n"
        )
        return 3

    sys.stdout.write(
        f"  NVIDIA EULA accepted.\n"
        f"    Marker:     {marker_path}\n"
        f"    Transcript: {transcript_path}\n"
        f"  pkm will now proceed with the NVIDIA package install.\n"
    )
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
