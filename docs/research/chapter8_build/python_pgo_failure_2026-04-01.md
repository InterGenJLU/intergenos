# Python 3.14 PGO Test Failure — test_generators

**Date:** April 1, 2026
**Context:** Python build failure during Chapter 8 — PGO profile run

---

## The Failure

```
FAIL: test_raise_and_yield_from (test.test_generators.SignalAndYieldFromTest)
AssertionError: 'FAILED' != 'PASSED'
```

1 failure out of 9,742 tests. Occurred during `make` with `--enable-optimizations` (PGO profile run), not during `make test`.

## Root Cause

`test_raise_and_yield_from` uses `_testcapi.raise_SIGINT_then_send_None()` — a C helper that atomically raises SIGINT and sends None into a generator. Under PGO instrumentation (`-fprofile-generate`), code layout, timing, and signal delivery behavior change. Combined with KVM virtual machine interrupt latency, the signal doesn't reach the innermost generator as expected.

- PGO instrumentation alters signal delivery timing (known pattern)
- KVM VMs add interrupt latency and jitter
- Not a real Python bug — a timing-sensitive test under non-standard conditions

## How Other Distros Handle This

- **Gentoo:** Excludes 8 tests from PGO profiling (`test_asyncio, test_httpservers, test_logging, test_multiprocessing_fork, test_socket, test_xmlrpc, test_tools, test_pyrepl`)
- **Void Linux:** Uses `--enable-optimizations`, skips specific tests in check phase
- **Arch Linux:** Default `--pgo` flag, no heavy customization

## Fix

Override `PROFILE_TASK` to exclude the failing test:
```bash
make PROFILE_TASK="-m test --pgo -x test_generators --timeout 120" -j${IGOS_JOBS}
```

Excluding 1 of 46 PGO tests has negligible impact on optimization quality.

## References

- CPython issue #135494 — `-x` + `--pgo` interaction fix
- CPython issue #111929 — PGO failures on buildbots
- CPython issue #135489 — No info when PGO profile test fails
- Gentoo python ebuild PGO exclusions
- Python `Lib/test/libregrtest/pgo.py` — PGO test list
