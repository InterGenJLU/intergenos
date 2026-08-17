# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""InterGen grounded reference index — anti-fabrication substrate (Goal-2).

The small local model fabricates tool/command names when it does not KNOW what
is on the machine (observed: it invented `png2jpg`/`word2pdf` while `magick`/
`libreoffice` were installed, and said `apt` despite a prompt rule). The proven
fix is to GROUND it in truth, not to add more prompt rules (which the research
showed get ignored). This module is that ground truth.

Two parts, deliberately blended:
  1. GROUND TRUTH (derived live, never hardcoded): which binaries actually exist
     on PATH right now — `is_installed()` / `known_tools()`. This is the index
     the dispatch/governance validation gate checks emitted commands against,
     and the filter that keeps the retrieval layer from ever surfacing a tool
     that is not present.
  2. A small CURATED capability map: everyday subject -> candidate tools +
     correct command shape + a docs pointer. The candidate list is curated
     (stable); which candidates are SURFACED is filtered by ground truth, so the
     map cannot drift into recommending something that was removed.

Design constraints (HG + research):
  - READ-ONLY. No new persistent writable state (same invariant as state_cache).
  - GROUND, DON'T SCRIPT. lookup() returns FACTS to make available to the model,
    never a canned response. The model still writes its own answer.
  - QUERY-SCOPED / SMALL. lookup() returns only the matched subject's few facts,
    never the full catalog — injected context costs prefill (~500-800ms/1k tok
    on this class of hardware; per the latency research).

The retrieval layer feeds the freeform answer path; the dispatch/governance
layer consumes is_installed()/known_tools() to validate emitted commands.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field


# Package managers the model reaches for out of training habit, and the one
# truth. The L2 gate uses this to catch `apt install ...` etc. in emitted text.
PKG_MANAGER = "pkm"
WRONG_PKG_MANAGERS = ("apt", "apt-get", "dnf", "yum", "pacman", "zypper",
                      "flatpak", "snap", "brew", "emerge")

# Always-true conventions the model defaults wrongly on without grounding.
CONVENTIONS = (
    "Package manager: pkm — install with `pkm install <name>` "
    "(never apt/dnf/yum/pacman/flatpak/snap). User-space conversions and media "
    "playback need no sudo. If the user says 'this' file without giving a path, "
    "ask which file they mean."
)


@dataclass
class Capability:
    """One everyday subject: how to actually do it on THIS distro."""
    subject: str
    keywords: tuple[str, ...]          # query match (Tier-0 keyword; deterministic, 0-latency)
    candidates: tuple[str, ...]        # tools that can do it, best first (CURATED)
    how: str                           # correct command shape (grounded fact, not a script)
    pointer: str = ""                  # where to read more (man page / --help)
    absent_note: str = ""              # what NOT to suggest / fallback when no candidate present


# Curated capability map — seeded from the validated fact-map + the live sweep
# of the dev box (2026-06-06). Candidate lists are curated; surfacing is filtered
# by is_installed() at lookup time so an absent tool is never recommended.
_CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        subject="image conversion",
        keywords=("convert", "png", "jpg", "jpeg", "image", "photo format",
                  "gif", "webp", "bmp", "tiff", "resize image"),
        candidates=("magick", "convert", "mogrify", "gimp"),
        how="magick input.png output.jpg   (batch: mogrify -format jpg *.png)",
        pointer="man magick",
        absent_note="If none present, say image tools are not installed; do not invent one.",
    ),
    Capability(
        subject="document / PDF conversion",
        keywords=("pdf", "docx", "word", "document", "odt", "spreadsheet",
                  "convert to pdf", "merge pdf", "presentation"),
        candidates=("libreoffice", "soffice", "pdfunite", "pdftoppm", "qpdf", "ps2pdf"),
        how="libreoffice --headless --convert-to pdf file.docx   "
            "(merge: pdfunite a.pdf b.pdf out.pdf)",
        pointer="man libreoffice",
        absent_note="pandoc/pdftk/unoconv are NOT installed — do not suggest them.",
    ),
    Capability(
        subject="printing",
        keywords=("print", "printer", "cups", "lp ", "spooler", "queue"),
        candidates=("lp", "lpr", "lpstat", "lpoptions"),
        how="print: lp file.pdf   |   list printers/jobs: lpstat -p -d   |   "
            "troubleshoot: lpstat -t, then GNOME Settings -> Printers",
        pointer="man lp",
        absent_note="lpadmin/system-config-printer are not on PATH; configure via GNOME Settings.",
    ),
    Capability(
        subject="bluetooth",
        keywords=("bluetooth", "speaker", "headphone", "headset", "pair",
                  "bluetooth audio"),
        candidates=("bluetoothctl", "wpctl", "pactl"),
        how="run `bluetoothctl`, then at its interactive prompt type these "
            "commands in turn: `power on`, `scan on`, `pair <MAC>`, "
            "`connect <MAC>`, `trust <MAC>` (these are bluetoothctl subcommands, "
            "NOT shell flags). Check audio routing with `wpctl status`.",
        pointer="bluetoothctl --help",
        absent_note="Use --help, not man (no man page). rfkill is not installed.",
    ),
    Capability(
        subject="media playback",
        keywords=("play", "movie", "video", "music", "audio file", "watch",
                  "mp4", "mkv", "mp3"),
        candidates=("mpv", "ffplay"),
        how="mpv /path/to/file.mp4   (from USB: find it with lsblk, then "
            "mpv /run/media/<user>/<label>/file.mp4)",
        pointer="man mpv",
        absent_note="vlc/totem/mplayer are NOT installed. Never use `mpv -d /dev/videoN` to play a file.",
    ),
    Capability(
        subject="phone photos / external media",
        keywords=("phone", "pictures off", "photos off", "usb drive", "sd card",
                  "external drive", "import photos", "mtp", "mount"),
        candidates=("gio", "udisksctl", "lsblk", "mount", "mtp-detect"),
        how="Phone (MTP): plug in, unlock, allow access -> mounts in the file "
            "manager (photos under DCIM/). USB: lsblk to find it; auto-mounts "
            "under /run/media/<user>/<label>/",
        pointer="gio --help",
        absent_note="jmtpfs/gphoto2/adb are NOT installed.",
    ),
    Capability(
        subject="archives",
        keywords=("zip", "unzip", "tar", "extract", "compress", "archive", "tarball"),
        candidates=("unzip", "tar", "zip", "gzip", "xz"),
        how="extract zip: unzip file.zip   |   extract tgz: tar xzf file.tar.gz",
        pointer="man tar",
        absent_note="7z is NOT installed.",
    ),
    Capability(
        subject="install software",
        keywords=("install", "uninstall", "remove package", "add program",
                  "get the app", "set up app"),
        candidates=("pkm",),
        how="pkm install <name>   |   remove: pkm remove <name>   "
            "(NEVER apt/dnf/flatpak/snap — they are not the package manager here)",
        pointer="man pkm",
    ),
    Capability(
        subject="databases",
        keywords=("postgres", "postgresql", "psql", "database", "sqlite",
                  "mysql", "mariadb", "sql"),
        candidates=("psql", "postgres", "sqlite3", "mariadb", "mysql"),
        how="PostgreSQL client: psql   |   lightweight/local: sqlite3 mydb.db   "
            "|   MariaDB/MySQL: mariadb",
        pointer="man psql",
    ),
    Capability(
        subject="scripting / building from source",
        keywords=("script", "bash script", "python script", "compile",
                  "from source", "build it", "makefile", "automate"),
        candidates=("bash", "python3", "gcc", "g++", "make", "cmake", "git"),
        how="scripts: bash or python3. Build from source: typically "
            "./configure && make && make install, or a cmake build dir.",
        pointer="",
        absent_note="rust (rustc/cargo) is NOT installed on this box.",
    ),
)


class ReferenceIndex:
    """Grounded, read-only reference index. Ground truth derived live; the
    curated capability map filtered by what is actually installed."""

    def __init__(self) -> None:
        # shutil.which results are stable within a session; cache lazily so we
        # never re-stat PATH on every lookup. Refresh by constructing anew.
        self._which_cache: dict[str, bool] = {}

    # ── Ground truth (the L2 validation index + the L1 surfacing filter) ──

    def is_installed(self, tool: str) -> bool:
        """True if `tool` is a real executable on PATH right now (cached)."""
        tool = tool.strip()
        if tool not in self._which_cache:
            self._which_cache[tool] = shutil.which(tool) is not None
        return self._which_cache[tool]

    def known_tools(self) -> set[str]:
        """Every curated candidate tool that is actually installed — the set
        the L2 gate treats as 'real' for command validation."""
        out: set[str] = set()
        for cap in _CAPABILITIES:
            out.update(t for t in cap.candidates if self.is_installed(t))
        return out

    def is_wrong_pkg_manager(self, token: str) -> bool:
        """True if `token` is a package manager that is NOT this distro's (apt
        etc.) — for the L2 gate to catch `apt install ...` in emitted text."""
        return token.strip().lower() in WRONG_PKG_MANAGERS

    # ── L1 retrieval (grounding facts to MAKE AVAILABLE — never a script) ──

    def lookup(self, query: str) -> str | None:
        """Return a compact grounded fact block for the subject the query is
        about, or None if no everyday subject matches (let the model answer
        freely — do not force grounding where it does not apply).

        Surfaced tools are filtered to what is ACTUALLY installed, so the model
        is never handed a tool that is not present. Query-scoped: the single
        best-matching subject only, kept small for prefill cost.
        """
        lower = query.lower()
        best: Capability | None = None
        best_hits = 0
        for cap in _CAPABILITIES:
            hits = sum(1 for kw in cap.keywords if kw in lower)
            if hits > best_hits:
                best, best_hits = cap, hits
        if best is None or best_hits == 0:
            return None

        present = [t for t in best.candidates if self.is_installed(t)]
        lines = [f"Subject: {best.subject}"]
        if present:
            lines.append("These tools are ALREADY installed — use them "
                         "directly, no install step needed: "
                         f"{', '.join(present)}")
            lines.append(f"Correct usage: {best.how}")
            if best.pointer:
                lines.append(f"More detail: {best.pointer}")
        else:
            lines.append("None of the usual tools for this are installed — say "
                         "so honestly; do not invent a tool or command.")
        if best.absent_note:
            lines.append(f"Note: {best.absent_note}")
        return "\n".join(lines)

    def conventions(self) -> str:
        """Always-true distro facts to keep available on grounded turns."""
        return CONVENTIONS
