#!/usr/bin/env python3
"""sign-pty-feeder — run a signing command on a pty and answer its PIN prompts
from a pre-staged 0600 tmpfs file, so the operator enters the PIN exactly once
per ceremony instead of once per binary.

Why this exists (E3-F1, grounded 2026-07-18): the PIV 9c signature key carries
CKA_ALWAYS_AUTHENTICATE (per-signature PIN is the PIV standard for that slot).
libp11 0.4.13's context-specific re-login path (p11_key.c pkcs11_authenticate)
unconditionally prompts via the OpenSSL UI and has no code path that reuses a
URI-supplied PIN — pin-source= is consumed for the initial token login only.
No URI form can suppress the per-operation prompts; answering them is the only
one-PIN mechanism that keeps per-operation authentication intact.

Security posture (B-049-aligned):
  - The PIN itself never appears on argv, in the environment, or in any log.
    This tool receives only the FILE PATH; the PIN travels pin-file -> pty.
  - The pin file must be a regular file, mode 0600, non-empty.
  - Only prompts matching --expect-regex are answered, at most --max-answers
    times; an unrecognized prompt receives nothing, the child stalls, and the
    --timeout kills it loudly (fail-closed, never fail-quiet).
  - Child output is relayed verbatim except any exact occurrence of the PIN
    (defense in depth against a UI echoing input back).

Exit codes: child's own exit code; 96 = stall timeout (unanswered prompt or
hang); 97 = feeder infrastructure error before/around the child.
"""

import argparse
import errno
import os
import pty
import re
import select
import stat
import sys
import time


def die(msg: str, code: int = 97) -> "NoReturn":  # noqa: F821
    print(f"sign-pty-feeder: error: {msg}", file=sys.stderr)
    sys.exit(code)


def read_pin(path: str) -> bytes:
    try:
        st = os.stat(path)
    except OSError as e:
        die(f"cannot stat pin file {path}: {e}")
    if not stat.S_ISREG(st.st_mode):
        die(f"pin file {path} is not a regular file")
    if stat.S_IMODE(st.st_mode) != 0o600:
        die(f"pin file {path} permissions are not 0600")
    with open(path, "rb") as f:
        pin = f.read().strip(b"\n")
    if not pin:
        die(f"pin file {path} is empty")
    return pin


def main() -> int:
    ap = argparse.ArgumentParser(allow_abbrev=False)
    ap.add_argument("--pin-file", required=True)
    ap.add_argument("--expect-regex", required=True,
                    help="prompt pattern that may be answered with the PIN")
    ap.add_argument("--max-answers", type=int, default=2)
    ap.add_argument("--timeout", type=int, default=120,
                    help="seconds with no child output before a loud kill")
    ap.add_argument("cmd", nargs=argparse.REMAINDER,
                    help="-- command to run (everything after --)")
    args = ap.parse_args()

    cmd = args.cmd
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        die("no command given after --")

    pin = read_pin(args.pin_file)
    expect = re.compile(args.expect_regex.encode())

    pid, master = pty.fork()
    if pid == 0:  # child: the pty is now the controlling tty (/dev/tty)
        try:
            os.execvp(cmd[0], cmd)
        except OSError as e:
            print(f"sign-pty-feeder(child): exec failed: {e}", file=sys.stderr)
            os._exit(97)

    answered = 0
    window = b""  # rolling tail across reads so a split prompt still matches
    last_output = time.monotonic()
    try:
        while True:
            ready, _, _ = select.select([master], [], [], 1.0)
            if not ready:
                if time.monotonic() - last_output > args.timeout:
                    os.kill(pid, 9)
                    os.waitpid(pid, 0)
                    die(f"no child output for {args.timeout}s — unanswered "
                        f"prompt or hang; killed (fail-closed)", 96)
                continue
            try:
                chunk = os.read(master, 4096)
            except OSError as e:
                if e.errno == errno.EIO:  # child closed its end
                    break
                raise
            if not chunk:
                break
            last_output = time.monotonic()
            # relay, scrubbing any exact PIN echo
            sys.stdout.buffer.write(chunk.replace(pin, b"[PIN]"))
            sys.stdout.buffer.flush()
            window = (window + chunk)[-512:]
            if expect.search(window):
                if answered >= args.max_answers:
                    os.kill(pid, 9)
                    os.waitpid(pid, 0)
                    die(f"prompt matched more than --max-answers="
                        f"{args.max_answers} times — refusing (fail-closed)", 96)
                os.write(master, pin + b"\n")
                answered += 1
                window = b""  # never answer the same prompt text twice
    finally:
        try:
            os.close(master)
        except OSError:
            pass

    _, status = os.waitpid(pid, 0)
    if os.WIFEXITED(status):
        rc = os.WEXITSTATUS(status)
    else:
        rc = 96
    print(f"sign-pty-feeder: child exited rc={rc}, prompts answered: {answered}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
