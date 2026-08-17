# shim-build — Test Fixtures

**These files are test-only.** They exist solely to drive the
manifest-phase test at [`tests/manifest/test_manifest_phase.sh`](../../manifest/test_manifest_phase.sh)
and are **never used in any production build path**, signing
pipeline, or release artifact.

---

## What's here

| File | Purpose |
|---|---|
| `test-vendor-cert.pem` | A throwaway X.509 certificate generated for test fixturing. PEM-encoded. |
| `test-vendor-cert.der` | The same certificate, DER-encoded. |
| `test-vendor-key.pem` | The **paired private key** for the above test cert. Useful for tests that need to demonstrate a sign-and-verify roundtrip against the test cert. **Never used in production.** |

---

## Why a private key is in the tree

Some manifest-phase tests verify that the shim build pipeline
correctly handles end-to-end sign-and-verify flows. Without the
paired key, those tests cannot exercise the signing side of the
flow and would only test verification against an externally-provided
signed artifact.

To avoid coupling the test suite to a hardware token or to the
production CA, the test fixture ships its own throwaway CA whose
private key is published in the open. Anyone with access to this
key can sign certificates that the **test fixture** would trust,
but **no production trust path references this key**:

- The shim-build Dockerfile copies `docker/shim-build/vendor-cert/intergenos-secure-boot-ca.{der,pem}`
  (the **production** vendor cert), not anything from this
  directory.
- The signing-ceremony scripts at `scripts/ceremony/` reference
  the production CA on the Nitrokey hardware token.
- The release-signing scripts (`scripts/sign-release.sh`,
  `scripts/sign-shim.sh`, etc.) reference the same.

Grep verification:

```sh
# These should return zero hits:
grep -rn 'test-vendor-key' /mnt/intergenos/packages /mnt/intergenos/scripts/sign-* /mnt/intergenos/scripts/ceremony /mnt/intergenos/docker

# This should return only the test path:
grep -rn 'test-vendor-key' /mnt/intergenos --include='*.sh' --include='*.py'
# Expected sole hit: tests/manifest/test_manifest_phase.sh
```

If a future commit introduces a reference to `test-vendor-key.pem`
from outside `tests/`, the pre-push public-content audit will flag
it for review.

---

## Audit context

This README closes the documentation portion of audit finding
**P-023** (Medium: tests/fixtures/shim-build/test-vendor-key.pem
private key in tree without isolation documentation) from the
2026-05-18 comprehensive state audit.

— InterGenJLU
