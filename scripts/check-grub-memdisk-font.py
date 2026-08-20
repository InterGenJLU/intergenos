#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Fail closed unless a built GRUB image carries the console font in its memdisk.

Why this check exists
---------------------
GRUB's built-in shim_lock verifier has no GRUB_FILE_TYPE_FONT entry in its skip
list (grub-core/kern/efi/sb.c), and grub-core/font/font.c opens every font as
that type. Under Secure Boot, a font read off the ESP is therefore refused with
"prohibited by secure boot policy" and the menu renders in grub's built-in font
instead of the one the build produced. Carrying the font inside the image's
memdisk removes the unverified open entirely: grub-core/kern/verifiers.c returns
memdisk and procfs reads unverified, because the image holding those bytes is
itself signature-verified.

That makes the font's presence in the memdisk a boot-time-only property: if the
member goes missing, every build still succeeds and every artifact still boots —
only the console line and the fallback font differ. This check turns it into a
build-time failure.

How it reads the image
----------------------
grub-mkimage and grub-mkstandalone store the memdisk archive verbatim inside the
PE, so the member is read straight out of the image bytes: find the ustar header
whose name field is the member path, take the octal size from the header, and
slice the content that follows. The ustar magic is verified at its
header-relative offset (257) rather than accepting any occurrence of the name —
the same path also appears as ordinary text in the embedded config, and matching
that would report a font the image does not actually carry.

Usage:
  check-grub-memdisk-font.py --image grubx64.efi [--member fonts/unicode.pf2]
                             [--expect-sha256 <hex> | --expect-file <path>]

Exit: 0 the member is present (and matches, when an expectation was given)
      1 usage error
      2 the member is absent, unreadable, or does not match
"""

import argparse
import hashlib
import sys
from pathlib import Path

USTAR_MAGIC_OFFSET = 257
USTAR_SIZE_OFFSET = 124
USTAR_HEADER_BYTES = 512


def read_memdisk_member(image_bytes, member):
    """Return the bytes of `member` from a ustar archive embedded in an image.

    Returns None when no ustar header names that member.
    """
    name = member.encode()
    pos = 0
    while True:
        idx = image_bytes.find(name, pos)
        if idx < 0:
            return None
        header = image_bytes[idx:idx + USTAR_HEADER_BYTES]
        if (len(header) == USTAR_HEADER_BYTES
                and header[USTAR_MAGIC_OFFSET:USTAR_MAGIC_OFFSET + 5] == b"ustar"):
            size_field = header[USTAR_SIZE_OFFSET:USTAR_SIZE_OFFSET + 12]
            size_field = size_field.split(b"\0")[0].strip()
            try:
                size = int(size_field, 8)
            except ValueError:
                return None
            return image_bytes[idx + USTAR_HEADER_BYTES:
                               idx + USTAR_HEADER_BYTES + size]
        pos = idx + 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--image", required=True,
                    help="the built GRUB EFI binary to inspect")
    ap.add_argument("--member", default="fonts/unicode.pf2",
                    help="memdisk path of the font (default: fonts/unicode.pf2)")
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--expect-sha256",
                       help="sha256 the member's bytes must have")
    group.add_argument("--expect-file",
                       help="file whose bytes the member must equal")
    args = ap.parse_args(argv)

    image_path = Path(args.image)
    if not image_path.is_file():
        print(f"ERROR: image not found: {image_path}", file=sys.stderr)
        return 1

    expected = args.expect_sha256
    if args.expect_file:
        expect_path = Path(args.expect_file)
        if not expect_path.is_file():
            print(f"ERROR: --expect-file not found: {expect_path}",
                  file=sys.stderr)
            return 1
        expected = hashlib.sha256(expect_path.read_bytes()).hexdigest()

    member = read_memdisk_member(image_path.read_bytes(), args.member)
    if member is None:
        print(f"FAIL: {image_path} carries no memdisk member '{args.member}'.",
              file=sys.stderr)
        print("      The boot-time font load would fall back to the ESP copy, "
              "which Secure Boot refuses.", file=sys.stderr)
        return 2

    actual = hashlib.sha256(member).hexdigest()
    if expected and actual != expected:
        print(f"FAIL: memdisk member '{args.member}' is present but does not "
              f"match: sha256={actual}, expected {expected}", file=sys.stderr)
        return 2

    print(f"PASS: memdisk carries '{args.member}' "
          f"({len(member)} bytes, sha256 {actual})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
