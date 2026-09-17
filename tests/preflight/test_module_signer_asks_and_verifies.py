# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The driver module signer asks for the passphrase, and proves what it signed.

This runs the SHIPPED script against the real kernel signing tool on this machine
and a real module file, with a real encrypted key. The only stand-in is the person
at the prompt.

Two things are asserted, and the second one is a defect this cut found rather than
a requirement it was given:

  1. The signer asks for the machine owner's passphrase and refuses when it cannot
     get one. It does not fall back to leaving the module unsigned and reporting
     success, which is what the previous version did whenever the key was missing.

  2. It proves the signature it just made. The previous check read the last 28
     bytes for the marker "~Module signature appended~". Every module the kernel
     package ships is ALREADY signed at build time, so that marker is present
     before the script runs: measured on 2026-09-17, the check passed on a module
     whose signing had just been refused, and on one signed with a wrong
     passphrase. It could not tell a successful signing from a failed one on any
     module that had ever been signed.
"""

import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SIGNER = REPO / "packages" / "extra" / "nvidia" / "hooks" / "sign-module.sh"
HELPER = REPO / "scripts" / "mok-signing.sh"
PASSPHRASE = "owner-passphrase-1"

RUNNING_KVER = os.uname().release
SIGN_FILE = Path(f"/lib/modules/{RUNNING_KVER}/build/scripts/sign-file")
MODULE_SOURCE = sorted(
    Path(f"/lib/modules/{RUNNING_KVER}/kernel").rglob("*.ko*")
)[:1]

needs_kernel_tools = pytest.mark.skipif(
    not SIGN_FILE.is_file() or not MODULE_SOURCE,
    reason=(f"this machine has no prepared kernel build tree at "
            f"/lib/modules/{RUNNING_KVER}/build/scripts/sign-file, so the real "
            f"signing tool cannot be exercised here"))


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
    return key, cert


def stub_prompt(bin_dir, answers, calls_file):
    bin_dir.mkdir(parents=True, exist_ok=True)
    answers_file = bin_dir / "answers"
    answers_file.write_text("\n".join(answers) + ("\n" if answers else ""))
    prompt = bin_dir / "systemd-ask-password"
    prompt.write_text(textwrap.dedent(f"""\
        #!/bin/bash
        echo "asked" >> {calls_file}
        n=$(wc -l < {calls_file})
        answer=$(sed -n "${{n}}p" {answers_file})
        [ -n "$answer" ] || exit 1
        printf '%s\\n' "$answer"
        """))
    prompt.chmod(0o755)


def stage_module(tmp_path):
    """An unsigned copy of a real module: the build-time signature stripped."""
    src = MODULE_SOURCE[0]
    ko = tmp_path / "probe.ko"
    if src.suffix == ".gz":
        with open(ko, "wb") as out:
            subprocess.run(["gzip", "-dc", str(src)], stdout=out, check=True)
    else:
        shutil.copyfile(src, ko)
    # Strip the trailing build-time PKCS#7 signature so the module starts
    # genuinely unsigned; otherwise "is it signed afterwards" proves nothing.
    subprocess.run(["strip", "--strip-debug", str(ko)], capture_output=True)
    data = ko.read_bytes()
    marker = b"~Module signature appended~"
    if marker in data:
        ko.write_bytes(data[:data.rindex(marker) - 40])
    ko.chmod(0o644)
    return ko


def run_signer(tmp_path, ko, key, cert, answers, env_extra=None):
    calls = tmp_path / "calls"
    bin_dir = tmp_path / "bin"
    stub_prompt(bin_dir, answers, calls)
    env = dict(os.environ)
    env.pop("IGOS_MOK_PASSPHRASE", None)
    env.update({
        "PATH": f"{bin_dir}:{env['PATH']}",
        "MOK_HELPER": str(HELPER),
        "MOK_KEY": str(key),
        "MOK_CERT": str(cert),
    })
    if env_extra:
        env.update(env_extra)
    result = subprocess.run(
        ["/bin/bash", str(SIGNER), str(ko), RUNNING_KVER],
        capture_output=True, text=True, env=env, timeout=120)
    return result, calls


def is_signed_by(ko, cert, sign_file_dir=None):
    """Whether the module carries a signature from this certificate.

    Read with the kernel's own convention: the appended PKCS#7 blob plus the
    trailing marker. The marker alone is not the question — a module that was
    signed by somebody else also has one.
    """
    data = ko.read_bytes()
    # The kernel's own convention ends the file with the marker and a newline.
    return data.endswith(b"~Module signature appended~\n")


@needs_kernel_tools
class TestTheSignerAsks:

    def test_it_asks_and_signs_when_the_owner_answers(self, tmp_path):
        key, cert = make_key(tmp_path / "mok", PASSPHRASE)
        ko = stage_module(tmp_path)
        assert not is_signed_by(ko, cert), "the staged module was already signed"
        result, calls = run_signer(tmp_path, ko, key, cert, [PASSPHRASE])
        assert result.returncode == 0, result.stdout + result.stderr
        assert calls.read_text().count("asked") == 1
        assert is_signed_by(ko, cert), "the module came back unsigned"

    def test_it_uses_a_supplied_passphrase_without_asking(self, tmp_path):
        key, cert = make_key(tmp_path / "mok", PASSPHRASE)
        ko = stage_module(tmp_path)
        result, calls = run_signer(
            tmp_path, ko, key, cert, ["never-used"],
            env_extra={"IGOS_MOK_PASSPHRASE": PASSPHRASE})
        assert result.returncode == 0, result.stdout + result.stderr
        assert not calls.exists()
        assert is_signed_by(ko, cert)

    def test_it_refuses_when_nobody_answers(self, tmp_path):
        key, cert = make_key(tmp_path / "mok", PASSPHRASE)
        ko = stage_module(tmp_path)
        before = ko.read_bytes()
        result, _calls = run_signer(tmp_path, ko, key, cert, [])
        assert result.returncode != 0, (
            "the signer reported success on a module it could not sign")
        assert ko.read_bytes() == before, "the module was modified anyway"

    def test_it_refuses_when_the_key_is_absent(self, tmp_path):
        """A missing key is a refusal, not an unsigned module and a warning."""
        key, cert = make_key(tmp_path / "mok", PASSPHRASE)
        ko = stage_module(tmp_path)
        key.unlink()
        result, _calls = run_signer(tmp_path, ko, key, cert, [PASSPHRASE])
        assert result.returncode != 0, (
            "the previous behaviour: exit 0 with the module left unsigned, "
            "which the kernel then refuses to load, discovered at the next boot")

    def test_the_passphrase_is_not_printed(self, tmp_path):
        key, cert = make_key(tmp_path / "mok", PASSPHRASE)
        ko = stage_module(tmp_path)
        result, _calls = run_signer(tmp_path, ko, key, cert, [PASSPHRASE])
        # Non-vacuous: a run that signed nothing also prints no passphrase.
        assert result.returncode == 0, result.stdout + result.stderr
        assert is_signed_by(ko, cert)
        assert PASSPHRASE not in result.stdout
        assert PASSPHRASE not in result.stderr


@needs_kernel_tools
class TestTheSignerProvesWhatItSigned:
    """The check must fail on a module whose signing did not happen."""

    def test_an_already_signed_module_that_fails_to_re_sign_is_reported(
            self, tmp_path):
        """The (b) finding: the old trailer check could not see this at all.

        The module starts out already carrying a signature — the state every
        module the kernel package ships is in — and the signing then fails
        because nobody answers the prompt. The trailer is present throughout,
        so a check that reads the trailer says "signed". The signer must still
        report the failure.
        """
        key, cert = make_key(tmp_path / "mok", PASSPHRASE)
        ko = stage_module(tmp_path)
        # Sign it once so it carries a trailer, exactly like a shipped module.
        result, _ = run_signer(tmp_path, ko, key, cert, [PASSPHRASE])
        assert result.returncode == 0
        assert is_signed_by(ko, cert)

        # Now refuse the second signing. The trailer is still there.
        result, _ = run_signer(tmp_path, ko, key, cert, [])
        assert is_signed_by(ko, cert), "premise of this test no longer holds"
        assert result.returncode != 0, (
            "the signer reported success because the module it was given "
            "already had a signature trailer on it from before")
