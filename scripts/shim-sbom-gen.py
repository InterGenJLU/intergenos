#!/usr/bin/env python3
"""SBOM generator for the InterGenOS MS-signed shim binary.

Emits a SPDX 2.3 JSON document attesting the build inputs of shimx64.efi:
the upstream rhboot/shim source @ pinned commit, the digest-pinned debian
bookworm-slim base image, the snapshot.debian.org apt pin, the embedded
InterGenOS Secure Boot CA vendor cert (DER + PEM), and the embedded SBAT
vendor entry.

The generator parses repo-on-disk inputs (Dockerfile + SBAT CSV + cert
files) — it does NOT require the shim binary to be on the local filesystem
unless --shim-binary is supplied. When supplied, the binary's SHA-256 and
size are recorded; without it, the canonical SHA-256 from the document
namespace is required via --shim-sha256 instead.

Output: SPDX 2.3 JSON document at the path given by --output, optionally
PGP-signed-detached (.asc companion file) when --sign is present.

Usage:
    python3 scripts/shim-sbom-gen.py \\
        --output docs/sboms/intergenos-shim-x64-20260515.spdx.json \\
        --shim-binary path/to/shimx64.efi \\
        [--sign --sign-key D7AA641D81ACD690C5AD865E7276E14DD8886BFE]

If --shim-binary is omitted:
    python3 scripts/shim-sbom-gen.py \\
        --output docs/sboms/intergenos-shim-x64-20260515.spdx.json \\
        --shim-sha256 b6c0c2c59cd2c6cc8306138ffd58a70210926defab4147b332663c91097ccf75 \\
        --shim-size 1020990

Conventions:
- Repo-on-disk paths are resolved relative to --repo-root (default = CWD).
- Document namespace is deterministic given the input set, so re-runs over
  unchanged inputs produce byte-identical SPDX JSON output.
- The script never invokes signing operations without the explicit --sign
  flag; signing requires a hardware-key touch (Nitrokey 3 NFC PIV slot).
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# Default release-signing subkey [S1] — primary maintainer NK#1 daily-driver
# (per docs/signing-key.md and docs/shim-review-submission.md Q26).
DEFAULT_SIGN_KEY_FPR = "D7AA641D81ACD690C5AD865E7276E14DD8886BFE"

DOCKERFILE_RELPATH = "docker/shim-build/Dockerfile"
SBAT_CSV_RELPATH = "docker/shim-build/sbat/sbat.intergenos.csv"
CERT_PEM_RELPATH = "docker/shim-build/vendor-cert/intergenos-secure-boot-ca.pem"
CERT_DER_RELPATH = "docker/shim-build/vendor-cert/intergenos-secure-boot-ca.der"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_dockerfile(path: Path) -> dict:
    """Extract the reproducibility anchors from the shim-build Dockerfile.

    Returns a dict with keys: base_image_ref, base_image_digest,
    apt_snapshot_timestamp, source_date_epoch, shim_version, shim_commit.
    Raises ValueError if any anchor is missing — the Dockerfile is the
    source of truth for the build inputs and missing anchors mean the SBOM
    can't be generated honestly.
    """
    text = path.read_text(encoding="utf-8")

    # FROM debian:bookworm-slim@sha256:HEX
    m = re.search(
        r"^FROM\s+(\S+)@sha256:([0-9a-f]{64})\s*$",
        text,
        re.MULTILINE,
    )
    if not m:
        raise ValueError(f"Dockerfile {path}: no digest-pinned FROM line found")
    base_image_ref = m.group(1)
    base_image_digest = m.group(2)

    def arg(name: str) -> str:
        m = re.search(rf"^ARG\s+{re.escape(name)}=(\S+)\s*$", text, re.MULTILINE)
        if not m:
            raise ValueError(f"Dockerfile {path}: ARG {name} not found")
        return m.group(1)

    source_date_epoch = arg("SOURCE_DATE_EPOCH")
    shim_version = arg("SHIM_VERSION")
    shim_commit = arg("SHIM_COMMIT")

    # snapshot.debian.org/archive/debian/TIMESTAMP/
    m = re.search(
        r"snapshot\.debian\.org/archive/debian/(\d{8}T\d{6}Z)",
        text,
    )
    if not m:
        raise ValueError(
            f"Dockerfile {path}: no snapshot.debian.org timestamp pin found"
        )
    apt_snapshot_timestamp = m.group(1)

    return {
        "base_image_ref": base_image_ref,
        "base_image_digest": base_image_digest,
        "apt_snapshot_timestamp": apt_snapshot_timestamp,
        "source_date_epoch": source_date_epoch,
        "shim_version": shim_version,
        "shim_commit": shim_commit,
    }


def read_sbat_entry(path: Path) -> dict:
    """Read the InterGenOS SBAT vendor entry CSV.

    Returns a dict with keys: raw_line, name, generation, vendor, product,
    version, url. Asserts exactly one non-empty data line is present.
    """
    text = path.read_text(encoding="utf-8")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) != 1:
        raise ValueError(
            f"SBAT CSV {path}: expected exactly 1 data line, found {len(lines)}"
        )
    parts = lines[0].split(",")
    if len(parts) != 6:
        raise ValueError(
            f"SBAT CSV {path}: expected 6 comma-separated fields, found {len(parts)}"
        )
    name, generation, vendor, product, version, url = parts
    return {
        "raw_line": lines[0],
        "raw_bytes": text.encode("utf-8"),
        "name": name,
        "generation": generation,
        "vendor": vendor,
        "product": product,
        "version": version,
        "url": url,
    }


def build_spdx_doc(
    dockerfile: dict,
    sbat: dict,
    cert_pem_path: Path,
    cert_der_path: Path,
    shim_sha256: str,
    shim_size: int,
    shim_version: str,
    submission_tag: str,
    created_iso: str,
) -> dict:
    """Construct the SPDX 2.3 JSON document as a plain dict.

    Output dict keys are emitted in insertion order; json.dumps with
    sort_keys=False preserves that order in the rendered file.
    """
    document_namespace = (
        f"https://intergenstudios.com/sbom/{submission_tag}-"
        f"{shim_sha256[:16]}"
    )

    cert_pem_sha = sha256_file(cert_pem_path)
    cert_der_sha = sha256_file(cert_der_path)
    sbat_sha = sha256_bytes(sbat["raw_bytes"])

    pkg_shim_binary = {
        "SPDXID": "SPDXRef-Package-shimx64-efi",
        "name": "shimx64.efi",
        "versionInfo": shim_version,
        "supplier": "Organization: InterGenOS",
        "downloadLocation": (
            f"https://github.com/InterGenJLU/shim-review/tree/{submission_tag}"
        ),
        "filesAnalyzed": False,
        "checksums": [
            {"algorithm": "SHA256", "checksumValue": shim_sha256},
        ],
        "licenseConcluded": "BSD-3-Clause",
        "licenseDeclared": "BSD-3-Clause",
        "copyrightText": "NOASSERTION",
        "comment": (
            f"InterGenOS-built unsigned shim binary, {shim_size} bytes. "
            f"MS-signing happens externally; this artifact is the input to "
            f"that signing pass. Reproducible byte-for-byte across native-Linux "
            f"Docker hosts per docker/shim-build/Dockerfile."
        ),
    }

    pkg_shim_source = {
        "SPDXID": "SPDXRef-Package-rhboot-shim-source",
        "name": "rhboot/shim",
        "versionInfo": shim_version,
        "supplier": "Organization: rhboot",
        "downloadLocation": (
            f"git+https://github.com/rhboot/shim.git@{dockerfile['shim_commit']}"
        ),
        "filesAnalyzed": False,
        "checksums": [],
        "licenseConcluded": "BSD-3-Clause",
        "licenseDeclared": "BSD-3-Clause",
        "copyrightText": "NOASSERTION",
        "comment": (
            f"Upstream shim source pinned at git tag {shim_version}, "
            f"commit {dockerfile['shim_commit']} (40-char SHA assertion enforced "
            f"in Dockerfile build step)."
        ),
    }

    pkg_debian_base = {
        "SPDXID": "SPDXRef-Package-debian-bookworm-slim-base",
        "name": "debian-bookworm-slim",
        "versionInfo": "bookworm-slim",
        "supplier": "Organization: Debian Project",
        "downloadLocation": (
            f"docker://{dockerfile['base_image_ref']}"
            f"@sha256:{dockerfile['base_image_digest']}"
        ),
        "filesAnalyzed": False,
        "checksums": [
            {"algorithm": "SHA256", "checksumValue": dockerfile["base_image_digest"]},
        ],
        "licenseConcluded": "NOASSERTION",
        "licenseDeclared": "NOASSERTION",
        "copyrightText": "NOASSERTION",
        "comment": (
            "Digest-pinned Debian 12 bookworm-slim container base image. "
            "Single-supplier trust anchor for the build environment."
        ),
    }

    pkg_apt_snapshot = {
        "SPDXID": "SPDXRef-Package-debian-apt-snapshot",
        "name": "debian-apt-snapshot-bookworm",
        "versionInfo": dockerfile["apt_snapshot_timestamp"],
        "supplier": "Organization: Debian Project",
        "downloadLocation": (
            f"http://snapshot.debian.org/archive/debian/"
            f"{dockerfile['apt_snapshot_timestamp']}/"
        ),
        "filesAnalyzed": False,
        "checksums": [],
        "licenseConcluded": "NOASSERTION",
        "licenseDeclared": "NOASSERTION",
        "copyrightText": "NOASSERTION",
        "comment": (
            f"snapshot.debian.org timestamp pin {dockerfile['apt_snapshot_timestamp']}; "
            f"all apt packages installed during build resolve to versions "
            f"available at this immutable timestamp."
        ),
    }

    pkg_vendor_cert = {
        "SPDXID": "SPDXRef-Package-intergenos-secure-boot-ca",
        "name": "intergenos-secure-boot-ca",
        "versionInfo": "v1-2026-05-05-ceremony",
        "supplier": "Organization: InterGenOS",
        "downloadLocation": (
            "https://github.com/InterGenJLU/intergenos/tree/master/"
            "docker/shim-build/vendor-cert/"
        ),
        "filesAnalyzed": True,
        "hasFiles": [
            "SPDXRef-File-vendor-cert-pem",
            "SPDXRef-File-vendor-cert-der",
        ],
        "checksums": [],
        "licenseConcluded": "NOASSERTION",
        "licenseDeclared": "NOASSERTION",
        "copyrightText": "Copyright (c) InterGenOS",
        "comment": (
            "InterGenOS Secure Boot CA vendor certificate. Public half embedded "
            "in shim's vendor_cert slot; private half hardware-bound to "
            "Nitrokey 3 NFC PIV slot 9c, generated on-card during the "
            "2026-05-05 air-gapped Tails ceremony."
        ),
    }

    pkg_sbat_entry = {
        "SPDXID": "SPDXRef-Package-sbat-vendor-entry",
        "name": "shim.intergenos-sbat-vendor-entry",
        "versionInfo": sbat["generation"],
        "supplier": "Organization: InterGenOS",
        "downloadLocation": (
            "https://github.com/InterGenJLU/intergenos/blob/master/"
            "docker/shim-build/sbat/sbat.intergenos.csv"
        ),
        "filesAnalyzed": False,
        "checksums": [
            {"algorithm": "SHA256", "checksumValue": sbat_sha},
        ],
        "licenseConcluded": "NOASSERTION",
        "licenseDeclared": "NOASSERTION",
        "copyrightText": "NOASSERTION",
        "comment": f"SBAT entry concatenated into shim's .sbat section: {sbat['raw_line']}",
    }

    file_cert_pem = {
        "SPDXID": "SPDXRef-File-vendor-cert-pem",
        "fileName": "./intergenos-secure-boot-ca.pem",
        "checksums": [{"algorithm": "SHA256", "checksumValue": cert_pem_sha}],
        "licenseConcluded": "NOASSERTION",
        "copyrightText": "Copyright (c) InterGenOS",
    }
    file_cert_der = {
        "SPDXID": "SPDXRef-File-vendor-cert-der",
        "fileName": "./intergenos-secure-boot-ca.der",
        "checksums": [{"algorithm": "SHA256", "checksumValue": cert_der_sha}],
        "licenseConcluded": "NOASSERTION",
        "copyrightText": "Copyright (c) InterGenOS",
    }

    relationships = [
        {
            "spdxElementId": "SPDXRef-DOCUMENT",
            "relationshipType": "DESCRIBES",
            "relatedSpdxElement": "SPDXRef-Package-shimx64-efi",
        },
        {
            "spdxElementId": "SPDXRef-Package-shimx64-efi",
            "relationshipType": "GENERATED_FROM",
            "relatedSpdxElement": "SPDXRef-Package-rhboot-shim-source",
        },
        {
            "spdxElementId": "SPDXRef-Package-shimx64-efi",
            "relationshipType": "DEPENDS_ON",
            "relatedSpdxElement": "SPDXRef-Package-debian-bookworm-slim-base",
        },
        {
            "spdxElementId": "SPDXRef-Package-shimx64-efi",
            "relationshipType": "DEPENDS_ON",
            "relatedSpdxElement": "SPDXRef-Package-debian-apt-snapshot",
        },
        {
            "spdxElementId": "SPDXRef-Package-shimx64-efi",
            "relationshipType": "CONTAINS",
            "relatedSpdxElement": "SPDXRef-Package-intergenos-secure-boot-ca",
        },
        {
            "spdxElementId": "SPDXRef-Package-shimx64-efi",
            "relationshipType": "CONTAINS",
            "relatedSpdxElement": "SPDXRef-Package-sbat-vendor-entry",
        },
    ]

    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"InterGenOS {submission_tag}",
        "documentNamespace": document_namespace,
        "creationInfo": {
            "created": created_iso,
            "creators": [
                "Tool: scripts/shim-sbom-gen.py-1.0",
                "Organization: InterGenOS",
            ],
            "licenseListVersion": "3.24",
        },
        "packages": [
            pkg_shim_binary,
            pkg_shim_source,
            pkg_debian_base,
            pkg_apt_snapshot,
            pkg_vendor_cert,
            pkg_sbat_entry,
        ],
        "files": [file_cert_pem, file_cert_der],
        "relationships": relationships,
    }


def gpg_detach_sign(spdx_path: Path, key_fpr: str) -> Path:
    """Produce an ASCII-armored detached PGP signature for the SPDX file.

    Returns the path of the .asc companion file. Requires gpg available on
    PATH and the named secret key accessible (typically via Nitrokey card
    pkcs11 / scdaemon). Raises subprocess.CalledProcessError on failure.
    """
    sig_path = spdx_path.with_suffix(spdx_path.suffix + ".asc")
    if sig_path.exists():
        sig_path.unlink()
    subprocess.run(
        [
            "gpg",
            "--batch",
            "--yes",
            "--armor",
            "--detach-sign",
            "--local-user",
            key_fpr,
            "--output",
            str(sig_path),
            str(spdx_path),
        ],
        check=True,
    )
    return sig_path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Output SPDX 2.3 JSON file path",
    )
    p.add_argument(
        "--repo-root",
        type=Path,
        default=Path.cwd(),
        help="Path to the InterGenOS repo root (default: current dir)",
    )
    p.add_argument(
        "--shim-binary",
        type=Path,
        help="Path to shimx64.efi (preferred — SHA-256 + size computed locally)",
    )
    p.add_argument(
        "--shim-sha256",
        help="Override SHA-256 of shimx64.efi (required if --shim-binary not given)",
    )
    p.add_argument(
        "--shim-size",
        type=int,
        help="Override file size of shimx64.efi in bytes (required with --shim-sha256)",
    )
    p.add_argument(
        "--submission-tag",
        default="intergenos-shim-x64-20260515",
        help="Submission branch tag in InterGenJLU/shim-review fork",
    )
    p.add_argument(
        "--created",
        help="ISO 8601 UTC creation timestamp (default: now)",
    )
    p.add_argument(
        "--sign",
        action="store_true",
        help="Produce an ASCII-armored PGP detached signature alongside the SPDX file",
    )
    p.add_argument(
        "--sign-key",
        default=DEFAULT_SIGN_KEY_FPR,
        help=f"GPG key fingerprint to sign with (default: {DEFAULT_SIGN_KEY_FPR})",
    )

    args = p.parse_args(argv)

    if args.shim_binary is None and (args.shim_sha256 is None or args.shim_size is None):
        p.error(
            "either --shim-binary <path> OR both --shim-sha256 <hex> and "
            "--shim-size <bytes> must be supplied"
        )

    repo = args.repo_root.resolve()
    dockerfile_path = repo / DOCKERFILE_RELPATH
    sbat_path = repo / SBAT_CSV_RELPATH
    cert_pem_path = repo / CERT_PEM_RELPATH
    cert_der_path = repo / CERT_DER_RELPATH

    for path in (dockerfile_path, sbat_path, cert_pem_path, cert_der_path):
        if not path.is_file():
            print(f"ERROR: required input not found: {path}", file=sys.stderr)
            return 2

    dockerfile = parse_dockerfile(dockerfile_path)
    sbat = read_sbat_entry(sbat_path)

    if args.shim_binary is not None:
        if not args.shim_binary.is_file():
            print(
                f"ERROR: --shim-binary path not found: {args.shim_binary}",
                file=sys.stderr,
            )
            return 2
        shim_sha = sha256_file(args.shim_binary)
        shim_size = args.shim_binary.stat().st_size
    else:
        shim_sha = args.shim_sha256.lower()
        if not re.fullmatch(r"[0-9a-f]{64}", shim_sha):
            print(
                f"ERROR: --shim-sha256 must be 64 hex chars; got {len(shim_sha)}",
                file=sys.stderr,
            )
            return 2
        shim_size = args.shim_size

    created = args.created or datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    doc = build_spdx_doc(
        dockerfile=dockerfile,
        sbat=sbat,
        cert_pem_path=cert_pem_path,
        cert_der_path=cert_der_path,
        shim_sha256=shim_sha,
        shim_size=shim_size,
        shim_version=dockerfile["shim_version"],
        submission_tag=args.submission_tag,
        created_iso=created,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(doc, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote SPDX 2.3 JSON to {args.output}")
    print(f"  shim sha256: {shim_sha}")
    print(f"  shim size:   {shim_size}")
    print(f"  packages:    {len(doc['packages'])}")
    print(f"  files:       {len(doc['files'])}")
    print(f"  relationships: {len(doc['relationships'])}")

    if args.sign:
        sig = gpg_detach_sign(args.output, args.sign_key)
        print(f"Wrote detached PGP signature to {sig}")
        print(f"  signing key fpr: {args.sign_key}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
