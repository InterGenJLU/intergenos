"""GRUB boot-output parser — for Phase A-2 empirical testing.

Takes serial console capture (grub + early kernel output) and classifies
the kernel-load outcome into a small, testable set of categories. Used
by `test_grub_check_signatures` once Phase A-2 plumbing wires up a VM
that boots an installed InterGenOS target.

Why a standalone parser module:
  - Parsing is pure text-in, dict-out. Isolating it from VM
    orchestration means the failure modes that matter most to Phase A
    (did GRUB refuse the kernel, or did it load?) can be fixture-tested
    independently of libvirtd / qemu / swtpm.
  - When Phase A-2 plumbing lands, the test just calls
    `parse_grub_boot_output(captured_text)` and asserts on the
    `boot_outcome` field. No VM mocking in the test assertions
    themselves.

Output categories (`boot_outcome` values):
  - `kernel_loaded`: kernel's own banner appeared, handoff complete
  - `signature_missing`: GRUB's enforce path refused due to absent sig
    (this is the failure mode the hypothesis under test predicts
    for PE/COFF-sbsigned kernels under check_signatures=enforce)
  - `signature_verify_failed`: signature present but cryptographic
    verification failed (wrong key, corruption, etc.)
  - `file_not_found`: GRUB couldn't locate the kernel/initrd file at
    the configured path
  - `unknown`: no recognized pattern — the parser returns this instead
    of raising so the caller can capture the raw tail and triage.

Patterns are intentionally loose — real grub output varies across
versions. When Phase A-2 empirical runs land, pattern fidelity can be
tightened against actual captures. The categories themselves are
stable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Optional


# Patterns — matched case-insensitively, OR-joined per category.

# "Linux version X.Y.Z" is the first line the kernel itself emits after
# GRUB hands off — its presence is proof that GRUB's load + exec path
# completed without refusing the image.
_RE_KERNEL_BANNER = re.compile(
    r"\[\s*\d+\.\d+\]\s+Linux version\s+\S+", re.IGNORECASE,
)

# GRUB prints "Loading Linux <version> ..." before attempting to load
# the kernel file. Capturing this gives us the kernel path GRUB tried
# even when the load fails.
_RE_GRUB_LOADING_LINUX = re.compile(
    r"Loading Linux\s+(?P<version>\S+)", re.IGNORECASE,
)

# Signature-missing variants observed across GRUB 2.06-2.14. The
# character-class dodges a subtle trap: kernel version strings (and the
# file paths containing them) have dots, so [^.]*? wouldn't bridge a
# path like /boot/vmlinuz-6.18.10. Use .*? and rely on line-level
# matching via _first_match_line for scope.
#   error: /boot/vmlinuz-... has no signature.
#   error: file `/boot/vmlinuz-...' has no signature.
#   error: verification requested but file .* is not signed.
#   error: no signature for file
_RE_SIG_MISSING = re.compile(
    r"(?i)\berror:?\s+"
    r"(?:.*?has no signature"
    r"|.*?is not signed"
    r"|verification requested.*?(?:not\s+signed|no\s+sig)"
    r"|no signature for)"
)

# Signature-verify-failed variants: sig block present, cryptographic
# check rejected it.
#   error: bad signature
#   error: signature is invalid
#   error: verification failed
#   error: public key ... not found (enforce rejects because the
#     signing key isn't in any loaded keyring — counts as verify-fail)
_RE_SIG_VERIFY_FAIL = re.compile(
    r"(?i)\berror:?\s+"
    r"(?:bad signature"
    r"|signature is invalid"
    r"|verification failed"
    r"|public key.*?not found)"
)

# File-not-found: GRUB couldn't locate the referenced file.
#   error: file `/boot/foo' not found.
#   error: no such file
_RE_FILE_NOT_FOUND = re.compile(
    r"(?i)\berror:?\s+"
    r"(?:file.*?not found"
    r"|no such file)"
)


@dataclass
class ParseResult:
    boot_outcome: str
    captured_error_line: Optional[str]
    detected_kernel_path: Optional[str]
    raw_tail: str

    def to_dict(self) -> dict:
        return asdict(self)


def _first_match_line(text: str, pattern: re.Pattern) -> Optional[str]:
    """Return the full line containing the first pattern match, or None."""
    for line in text.splitlines():
        if pattern.search(line):
            return line.strip()
    return None


def _detect_kernel_path(text: str) -> Optional[str]:
    """Pull the kernel version/path GRUB tried to load, if visible.

    Priority: (1) an explicit path in an error line (`/boot/vmlinuz-...`)
    wins because it's the most specific; (2) fall back to the "Loading
    Linux <version>" line if no error path was captured.
    """
    # Explicit path in an error line — covers "error: /boot/vmlinuz-... has no signature"
    m = re.search(r"(/boot/vmlinuz\S*)", text)
    if m:
        return m.group(1).rstrip(".'`\"")

    m = _RE_GRUB_LOADING_LINUX.search(text)
    if m:
        return m.group("version")
    return None


def _tail(text: str, n: int = 20) -> str:
    """Return the last n lines of text, newline-joined."""
    lines = text.splitlines()
    return "\n".join(lines[-n:])


def parse_grub_boot_output(text: str) -> ParseResult:
    """Classify a grub+kernel boot capture into one of five outcomes.

    Order of checks:
      1. kernel_loaded — kernel's own banner is proof of handoff, even
         if prior grub output had warnings
      2. signature_missing — checked before verify_fail because the two
         regexes can both match some outputs; missing is the more
         specific (and load-bearing for our hypothesis) category
      3. signature_verify_failed
      4. file_not_found
      5. unknown (default)
    """
    kernel_path = _detect_kernel_path(text)
    tail = _tail(text)

    if _RE_KERNEL_BANNER.search(text):
        return ParseResult(
            boot_outcome="kernel_loaded",
            captured_error_line=None,
            detected_kernel_path=kernel_path,
            raw_tail=tail,
        )

    sig_missing_line = _first_match_line(text, _RE_SIG_MISSING)
    if sig_missing_line:
        return ParseResult(
            boot_outcome="signature_missing",
            captured_error_line=sig_missing_line,
            detected_kernel_path=kernel_path,
            raw_tail=tail,
        )

    sig_fail_line = _first_match_line(text, _RE_SIG_VERIFY_FAIL)
    if sig_fail_line:
        return ParseResult(
            boot_outcome="signature_verify_failed",
            captured_error_line=sig_fail_line,
            detected_kernel_path=kernel_path,
            raw_tail=tail,
        )

    not_found_line = _first_match_line(text, _RE_FILE_NOT_FOUND)
    if not_found_line:
        return ParseResult(
            boot_outcome="file_not_found",
            captured_error_line=not_found_line,
            detected_kernel_path=kernel_path,
            raw_tail=tail,
        )

    return ParseResult(
        boot_outcome="unknown",
        captured_error_line=None,
        detected_kernel_path=kernel_path,
        raw_tail=tail,
    )
