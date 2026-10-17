# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""One signing operation asks the owner once, and a person at a terminal is never
met by the boot-image signer's own raw prompt.

Measured on a real driver install on 2026-10-16 (`pkm install nvidia ...` run
from a terminal window): the owner was asked for the signing passphrase five
times, once per driver module, then a sixth time for the boot image, and then
the boot-image signer printed its own "Enter PEM pass phrase:" on the terminal,
ignored the passphrase it had been handed on standard input and refused the key
("bad decrypt ... maybe wrong password"). The boot image carrying the driver's
cmdline was never written and the dGPU-wired outputs stayed dark after the
reboot.

This runs the stand-in of that operation with the SHIPPED pieces on this
machine's real tools, under a real controlling terminal (a pty), with a real
encrypted key: one process resolves the passphrase, then starts the shipped
module signer five times on five real modules, then starts a process that
signs a boot image the way the kernel's hook does — resolving the passphrase
and calling the helper's mok_build_signed_uki with its output captured — on
this machine's kernel image with the real ukify and sbsign. The only stand-in
is the person at the prompt, who answers every time asked and is counted.

On the tree before the change: seven questions for the one operation, then the
boot-image signer waiting on the terminal until the run was killed.
"""

import fcntl
import os
import pty
import select
import shutil
import signal
import subprocess
import termios
import textwrap
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SIGNER = REPO / "packages" / "extra" / "nvidia" / "hooks" / "sign-module.sh"
HELPER = REPO / "scripts" / "mok-signing.sh"
PASSPHRASE = "owner-passphrase-operation-1"

RUNNING_KVER = os.uname().release
SIGN_FILE = Path(f"/lib/modules/{RUNNING_KVER}/build/scripts/sign-file")
MODULE_SOURCE = sorted(
    p for p in Path(f"/lib/modules/{RUNNING_KVER}/kernel").rglob("*.ko*")
    if p.suffix in (".ko", ".gz"))[:1]
VMLINUZ = Path(f"/boot/vmlinuz-{RUNNING_KVER}")
STUB = Path("/usr/lib/systemd/boot/efi/linuxx64.efi.stub")

_missing = [what for what, ok in (
    (f"the kernel's sign-file at {SIGN_FILE}", SIGN_FILE.is_file()),
    ("an uncompressed or gzip kernel module to sign", bool(MODULE_SOURCE)),
    (f"a readable kernel image at {VMLINUZ}", os.access(VMLINUZ, os.R_OK)),
    (f"the UKI stub at {STUB}", STUB.is_file()),
    ("ukify", shutil.which("ukify")),
    ("sbsign", shutil.which("sbsign")),
    ("sbverify", shutil.which("sbverify")),
    ("setsid", shutil.which("setsid")),
    ("openssl", shutil.which("openssl")),
) if not ok]

needs_real_tools = pytest.mark.skipif(
    bool(_missing),
    reason="the real signing tools are not all on this machine: missing "
           + "; ".join(_missing))

# Long enough for five module signings and one ukify build on a slow machine;
# a signer waiting at a terminal prompt never finishes, so this is also the
# point at which the run is declared to be waiting on the terminal.
TIMEOUT_SECONDS = 120


def make_key(directory, passphrase):
    directory.mkdir(parents=True, exist_ok=True)
    key = directory / "mok.key"
    cert = directory / "mok.crt"
    env = dict(os.environ, IGOS_TEST_PASS=passphrase)
    proc = subprocess.run(
        ["openssl", "req", "-new", "-x509", "-newkey", "rsa:2048",
         "-keyout", str(key), "-out", str(cert), "-outform", "PEM",
         "-days", "30", "-subj", "/CN=InterGenOS Machine Owner Key/",
         "-passout", "env:IGOS_TEST_PASS"],
        env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    probe = subprocess.run(
        ["openssl", "rsa", "-in", str(key), "-check", "-noout", "-passin", "pass:"],
        capture_output=True)
    assert probe.returncode != 0, "the scratch key is not encrypted"
    return key, cert


def counting_prompt(bin_dir, answer, calls_file):
    """The person at the prompt: answers every time, and every time is counted."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    prompt = bin_dir / "systemd-ask-password"
    prompt.write_text(textwrap.dedent(f"""\
        #!/bin/bash
        echo "asked: $*" >> {calls_file}
        printf '%s\\n' '{answer}'
        """))
    prompt.chmod(0o755)


def stage_module(tmp_path, index):
    """An unsigned copy of a real module: the build-time signature stripped."""
    src = MODULE_SOURCE[0]
    ko = tmp_path / f"module-{index}.ko"
    if src.suffix == ".gz":
        with open(ko, "wb") as out:
            subprocess.run(["gzip", "-dc", str(src)], stdout=out, check=True)
    else:
        shutil.copyfile(src, ko)
    subprocess.run(["strip", "--strip-debug", str(ko)], capture_output=True)
    data = ko.read_bytes()
    marker = b"~Module signature appended~"
    if marker in data:
        ko.write_bytes(data[:data.rindex(marker) - 40])
    ko.chmod(0o644)
    return ko


def run_at_a_terminal(argv, env, timeout):
    """Run argv in a session whose controlling terminal is a fresh pty.

    That is what a person in a terminal window gives a command. Standard input
    is /dev/null, so anything that wants an answer must ask on the terminal —
    which is exactly what a person would see. Returns (status, terminal text),
    status None when the run was still going at the timeout and was killed.
    """
    master, slave = pty.openpty()
    pid = os.fork()
    if pid == 0:  # pragma: no cover - child
        os.setsid()
        fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
        devnull = os.open(os.devnull, os.O_RDONLY)
        os.dup2(devnull, 0)
        os.dup2(slave, 1)
        os.dup2(slave, 2)
        os.close(master)
        os.execve(argv[0], argv, env)
    os.close(slave)
    seen = b""
    status = None
    deadline = time.monotonic() + timeout
    while True:
        done, wstatus = os.waitpid(pid, os.WNOHANG)
        if done:
            status = os.waitstatus_to_exitcode(wstatus)
        ready, _, _ = select.select([master], [], [], 0.2)
        if ready:
            try:
                chunk = os.read(master, 65536)
            except OSError:
                chunk = b""
            seen += chunk
            if not chunk and status is not None:
                break
        elif status is not None:
            break
        if status is None and time.monotonic() > deadline:
            os.killpg(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
            break
    os.close(master)
    return status, seen.decode("utf-8", "replace")


@needs_real_tools
class TestOneSigningOperation:

    def test_five_modules_and_a_boot_image_ask_once(self, tmp_path):
        key, cert = make_key(tmp_path / "mok", PASSPHRASE)
        modules = [stage_module(tmp_path, i) for i in range(5)]
        befores = [m.read_bytes() for m in modules]
        calls = tmp_path / "asked"
        bin_dir = tmp_path / "bin"
        counting_prompt(bin_dir, PASSPHRASE, calls)
        uki = tmp_path / "image.efi"
        uki_log = tmp_path / "image.log"

        image_step = tmp_path / "image-step.sh"
        image_step.write_text(textwrap.dedent(f"""\
            #!/bin/bash
            # The boot image, signed the way the kernel's hook signs it.
            set -u
            . {HELPER}
            mok_resolve_passphrase "$MOK_KEY" "the boot image" || exit 73
            out=$(mok_build_signed_uki build --linux={VMLINUZ} --output={uki} \\
                  --secureboot-private-key="$MOK_KEY" \\
                  --secureboot-certificate="$MOK_CERT" 2>&1)
            rc=$?
            printf '%s\\n' "$out" > {uki_log}
            exit $rc
            """))
        operation = tmp_path / "operation.sh"
        operation.write_text(textwrap.dedent(f"""\
            #!/bin/bash
            # One driver install: resolve, five module signers, one boot image.
            set -u
            . {HELPER}
            mok_resolve_passphrase "$MOK_KEY" "the stand-in driver install" || exit 70
            for ko in {' '.join(str(m) for m in modules)}; do
                /bin/bash {SIGNER} "$ko" {RUNNING_KVER} || exit 71
            done
            /bin/bash {image_step} || exit 72
            exit 0
            """))
        env = {k: v for k, v in os.environ.items()
               if k != "IGOS_MOK_PASSPHRASE"}
        env.update({
            "PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
            "MOK_HELPER": str(HELPER),
            "MOK_KEY": str(key),
            "MOK_CERT": str(cert),
            "MOK_PROTECTION_RECORD": str(tmp_path / "protection"),
        })

        status, terminal = run_at_a_terminal(
            ["/bin/bash", str(operation)], env, TIMEOUT_SECONDS)

        asked = calls.read_text().splitlines() if calls.exists() else []
        assert status is not None, (
            "the operation was still waiting at the terminal after "
            f"{TIMEOUT_SECONDS} s; the terminal showed: {terminal[-400:]!r}; "
            f"the owner had been asked {len(asked)} time(s)")
        assert "PEM pass phrase" not in terminal, (
            "the boot-image signer asked on the terminal itself: "
            f"{terminal[-400:]!r}")
        assert status == 0, (
            f"the operation failed (exit {status}); terminal: {terminal[-600:]!r}; "
            f"image log: {uki_log.read_text() if uki_log.exists() else ''!r}")
        assert len(asked) == 1, (
            f"one operation asked the owner {len(asked)} times: {asked}")
        # The one question names the secret by the installer's own label and
        # says which secret it is not: at a prompt that named neither, the
        # owner of a real install typed the MOK enrollment password.
        assert "Signing key passphrase" in asked[0], asked[0]
        assert "not the MOK enrollment password" in asked[0], asked[0]
        for module, before in zip(modules, befores):
            data = module.read_bytes()
            assert data != before, f"{module.name} came back unchanged"
            assert data.endswith(b"~Module signature appended~\n"), (
                f"{module.name} carries no signature")
        verify = subprocess.run(["sbverify", "--cert", str(cert), str(uki)],
                                capture_output=True, text=True)
        assert verify.returncode == 0, (
            f"the boot image does not verify against the owner's certificate: "
            f"{verify.stdout}{verify.stderr}")
        assert PASSPHRASE not in terminal
        assert PASSPHRASE not in uki_log.read_text()

    def test_the_held_passphrase_still_has_to_open_the_key(self, tmp_path):
        """Rule 3 holds for a held value: a wrong one is refused, not used."""
        key, cert = make_key(tmp_path / "mok", PASSPHRASE)
        module = stage_module(tmp_path, 0)
        before = module.read_bytes()
        calls = tmp_path / "asked"
        counting_prompt(tmp_path / "bin", PASSPHRASE, calls)
        env = {k: v for k, v in os.environ.items()}
        env.update({
            "PATH": f"{tmp_path / 'bin'}:{os.environ.get('PATH', '/usr/bin:/bin')}",
            "MOK_HELPER": str(HELPER),
            "MOK_KEY": str(key),
            "MOK_CERT": str(cert),
            "IGOS_MOK_PASSPHRASE": "not-the-passphrase",
        })
        result = subprocess.run(
            ["/bin/bash", str(SIGNER), str(module), RUNNING_KVER],
            env=env, capture_output=True, text=True, timeout=120)
        assert result.returncode != 0, "a wrong held passphrase was used"
        assert module.read_bytes() == before
        assert not calls.exists(), "a held value that failed fell back to asking"
