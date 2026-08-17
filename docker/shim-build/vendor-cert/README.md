# InterGenOS Secure Boot CA — vendor cert for shim embedding

**This is a BUILD-INPUT artifact, not a runtime artifact.** The
files in this directory exist to be **embedded into the InterGenOS
shim** during the `docker/shim-build/` shim-compilation pipeline.
They are **not shipped** in any InterGenOS release ISO, qcow2, or
installed-system tree.

---

## What's here

| File | Purpose |
|---|---|
| `intergenos-secure-boot-ca.pem` | X.509 certificate (PEM-encoded). The InterGenOS Secure Boot vendor CA, embedded into shim as the `vendor_cert` so MOK-signed binaries are trusted by the InterGenOS shim. |
| `intergenos-secure-boot-ca.der` | Same certificate, DER-encoded. Convenience format for the shim build tooling. |
| `intergenos-secure-boot-ca-pubkey.der` | Public-key extract from the above certificate. DER-encoded SubjectPublicKeyInfo. Convenience artifact for tooling that wants only the key, not the full cert. |

**Validity window:** notBefore 2026-05-14, notAfter 2028-05-13 (two
years). The certificate is self-signed and was generated for the
purpose described below.

**No private key in this directory.** The private key for this CA
lives offline on the operator's hardware token (Nitrokey, PIV
slot 9c). The Dockerfile at [`docker/shim-build/Dockerfile`](../Dockerfile)
copies only the public certificate into the build environment;
signing happens out-of-band via the ceremony pipeline documented at
[`scripts/ceremony/`](../../../scripts/ceremony/).

---

## How this is used

1. The shim-build Dockerfile copies these files into the build
   container at `/build/vendor_cert.{der,pem}`.
2. The shim source compiles with `-DVENDOR_CERT_FILE=/build/vendor_cert.der`,
   which embeds the public cert into the shim binary at
   compile time.
3. The compiled shim is then signed (separately, via the ceremony
   pipeline) by the offline-held private CA on the Nitrokey.
4. The signed shim is what ships in `packages/core/shim-signed/` and
   is what users run.

**Users never see these files.** The embedded certificate inside the
shim binary is the only thing that ships.

---

## License and redistribution

The certificate is a cryptographic artifact, not copyrightable
material. The repository's root [`LICENSE`](../../../LICENSE)
(GPL-3.0-or-later) does not assert copyright over the cert's bits;
the cert is provided here for the project's own build pipeline use.
The project's [`SOURCES.md`](../../../SOURCES.md) commitment to
make corresponding source available does not require us to publish
the private key (which we don't have outside the hardware token).

External parties wishing to use this certificate as a trust anchor
in their own environment may do so; we make no representation that
the certificate is suitable for any purpose other than the
InterGenOS shim build.

---

## Audit context

This README closes the documentation portion of audit finding
**P-023** (Medium: build-time vendor-cert in `docker/shim-build/`
had no LICENSE / README clarifying intent and not-for-shipping
status) from the 2026-05-18 comprehensive state audit. See also
[`tests/fixtures/shim-build/README.md`](../../../tests/fixtures/shim-build/README.md)
for the separate test-fixture cert/key handling.

— InterGenJLU
