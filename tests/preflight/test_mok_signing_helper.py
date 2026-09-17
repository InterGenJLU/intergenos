# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Nothing signs with the machine owner's key unless the owner is there to say so.

The key that signs this machine's boot images and driver modules is encrypted at
rest. Two shipped scripts sign with it on an installed machine — the kernel
package's post-install hook and the driver module signer — and both now resolve the
passphrase through one shared helper rather than each inventing its own answer.

These tests run the real helper against a real encrypted key made by the real
openssl, in a temporary directory. The only thing stubbed is the person: the prompt
that asks for the passphrase is replaced by a script that answers, or refuses, on
cue. Stubbing openssl instead would test nothing, because the question being asked
is whether openssl can open the key.

The refusal path is the one that matters most. It has to be a refusal — a non-zero
exit with the reason said plainly — and not a fallback that signs with nothing, or
skips signing and reports success, which is how the previous behaviour would have
degraded.
"""

import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
HELPER = REPO / "scripts" / "mok-signing.sh"
PASSPHRASE = "owner-passphrase-1"


def make_key(directory, passphrase):
    """A real encrypted key and certificate, made the way the installer makes them."""
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


def make_plain_key(directory):
    directory.mkdir(parents=True, exist_ok=True)
    key = directory / "mok.key"
    proc = subprocess.run(["openssl", "genrsa", "-out", str(key), "2048"],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return key


def stub_prompt(bin_dir, answers, calls_file):
    """A stand-in for the passphrase prompt that answers from a list, then fails.

    Records every call so a test can assert the person was NOT asked when the
    passphrase was already available.
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    answers_file = bin_dir / "answers"
    answers_file.write_text("\n".join(answers) + ("\n" if answers else ""))
    prompt = bin_dir / "systemd-ask-password"
    prompt.write_text(textwrap.dedent(f"""\
        #!/bin/bash
        echo "asked" >> {calls_file}
        n=$(wc -l < {calls_file})
        answer=$(sed -n "${{n}}p" {answers_file})
        if [ -z "$answer" ]; then
            exit 1
        fi
        printf '%s\\n' "$answer"
        """))
    prompt.chmod(0o755)
    return prompt


def run_helper(script_body, tmp_path, env_extra=None, path_prepend=None):
    """Source the helper and run a small script against it."""
    runner = tmp_path / "runner.sh"
    # A helper that is missing, or a function it does not define, must be an
    # ERROR and not look like a refusal. Without these two guards every
    # "it refuses" test below would pass against a tree with no helper in it at
    # all, which is a gate that is green for the wrong reason.
    runner.write_text(
        f"#!/bin/bash\nset -uo pipefail\n"
        f"source {HELPER} || {{ echo HELPER-NOT-SOURCED >&2; exit 97; }}\n"
        f"declare -F mok_resolve_passphrase >/dev/null || "
        f"{{ echo HELPER-INCOMPLETE >&2; exit 96; }}\n"
        f"{script_body}\n")
    runner.chmod(0o755)
    env = dict(os.environ)
    env.pop("IGOS_MOK_PASSPHRASE", None)
    if path_prepend:
        env["PATH"] = f"{path_prepend}:{env['PATH']}"
    if env_extra:
        env.update(env_extra)
    return subprocess.run(["/bin/bash", str(runner)], capture_output=True,
                          text=True, env=env, timeout=120)


def assert_refused(result):
    """The helper said no, and it was the helper that said it.

    A refusal is a non-zero exit from the helper's own code. An exit that means
    "there is no helper" (97), "the helper does not define this" (96) or "no
    such command" (127) is a broken test, not a proven refusal.
    """
    assert result.returncode not in (0, 96, 97, 127), (
        f"rc={result.returncode}: this is not the helper refusing.\n"
        f"{result.stdout}\n{result.stderr}")


def test_the_helper_is_shipped():
    assert HELPER.is_file(), (
        "the shared passphrase helper is missing; without it the kernel hook and "
        "the module signer each invent their own answer to the same question")


def test_the_kernel_package_stages_the_helper_and_owns_it():
    """One copy of the helper, shipped by the package that ships the hook.

    The kernel hook is registered twice — the core-tier linux-kernel package and
    the desktop-tier linux-kernel-pass2 package install the SAME hook file — but
    the helper is staged once, by the first pass, into the same
    /usr/lib/intergen/ directory as the other scripts that hook already calls.
    A second copy from the second pass would be two files that can drift, and
    two packages claiming one path.

    It is also named in the recipe's verify_paths, which is what turns its
    presence into a checked gate on every image rather than something noticed
    when a kernel upgrade refuses to sign.
    """
    build = REPO / "packages" / "core" / "linux-kernel" / "build.sh"
    recipe = REPO / "packages" / "core" / "linux-kernel" / "package.yml"
    assert "mok-signing.sh" in build.read_text(encoding="utf-8"), (
        "the kernel package does not stage the passphrase helper, so a machine "
        "built from it would have a signing hook with no way to ask")
    assert "/usr/lib/intergen/mok-signing.sh" in recipe.read_text(encoding="utf-8"), (
        "the helper is staged but not verified, so an image that lost it would "
        "pass every gate and fail at the owner's first kernel upgrade")


def test_both_kernel_hook_registrations_use_the_same_hook_file():
    """The two passes must not drift apart in what they do at signing time."""
    pass2 = (REPO / "packages" / "core" / "linux-kernel-pass2" / "build.sh"
             ).read_text(encoding="utf-8")
    assert "packages/core/linux-kernel/hooks/post-install.sh" in pass2


class TestPassphraseFromTheEnvironment:
    """During an install the installer supplies it and nobody is asked."""

    def test_environment_value_is_used_and_nobody_is_asked(self, tmp_path):
        key, cert = make_key(tmp_path / "mok", PASSPHRASE)
        calls = tmp_path / "calls"
        stub_prompt(tmp_path / "bin", ["never-used"], calls)
        result = run_helper(
            f'mok_resolve_passphrase "{key}" "signing a test" || exit 9\n'
            f'[ "$MOK_PASSPHRASE" = "{PASSPHRASE}" ] || exit 8\n'
            f'echo RESOLVED',
            tmp_path,
            env_extra={"IGOS_MOK_PASSPHRASE": PASSPHRASE},
            path_prepend=str(tmp_path / "bin"))
        assert result.returncode == 0, result.stdout + result.stderr
        assert "RESOLVED" in result.stdout
        assert not calls.exists(), (
            "the person was asked for a passphrase the installer had already "
            "supplied, in the middle of an unattended install")

    def test_a_wrong_environment_value_is_refused_not_used(self, tmp_path):
        """An environment value is checked against the key like any other."""
        key, cert = make_key(tmp_path / "mok", PASSPHRASE)
        calls = tmp_path / "calls"
        stub_prompt(tmp_path / "bin", [], calls)
        result = run_helper(
            f'mok_resolve_passphrase "{key}" "signing a test" && echo RESOLVED',
            tmp_path,
            env_extra={"IGOS_MOK_PASSPHRASE": "not-the-passphrase"},
            path_prepend=str(tmp_path / "bin"))
        assert_refused(result)
        assert "RESOLVED" not in result.stdout


class TestAskingThePerson:

    def test_the_owner_is_asked_and_the_answer_is_verified(self, tmp_path):
        key, cert = make_key(tmp_path / "mok", PASSPHRASE)
        calls = tmp_path / "calls"
        stub_prompt(tmp_path / "bin", [PASSPHRASE], calls)
        result = run_helper(
            f'mok_resolve_passphrase "{key}" "signing a test" || exit 9\n'
            f'[ "$MOK_PASSPHRASE" = "{PASSPHRASE}" ] || exit 8\n'
            f'echo RESOLVED',
            tmp_path, path_prepend=str(tmp_path / "bin"))
        assert result.returncode == 0, result.stdout + result.stderr
        assert calls.read_text().count("asked") == 1

    def test_a_wrong_answer_is_retried_a_stated_number_of_times_then_refused(
            self, tmp_path):
        key, cert = make_key(tmp_path / "mok", PASSPHRASE)
        calls = tmp_path / "calls"
        stub_prompt(tmp_path / "bin", ["wrong-1", "wrong-2", "wrong-3"], calls)
        result = run_helper(
            f'mok_resolve_passphrase "{key}" "signing a test" && echo RESOLVED',
            tmp_path, path_prepend=str(tmp_path / "bin"))
        assert_refused(result)
        assert "RESOLVED" not in result.stdout
        assert calls.read_text().count("asked") == 3, (
            "the number of attempts is stated in the helper and in the "
            "documents; this test is what keeps the three in step")

    def test_a_right_answer_after_a_wrong_one_is_accepted(self, tmp_path):
        key, cert = make_key(tmp_path / "mok", PASSPHRASE)
        calls = tmp_path / "calls"
        stub_prompt(tmp_path / "bin", ["wrong-1", PASSPHRASE], calls)
        result = run_helper(
            f'mok_resolve_passphrase "{key}" "signing a test" || exit 9\n'
            f'echo RESOLVED',
            tmp_path, path_prepend=str(tmp_path / "bin"))
        assert result.returncode == 0, result.stdout + result.stderr
        assert calls.read_text().count("asked") == 2

    def test_a_refused_prompt_is_a_refusal(self, tmp_path):
        """Nobody at the keyboard: the prompt fails and so does the signing."""
        key, cert = make_key(tmp_path / "mok", PASSPHRASE)
        calls = tmp_path / "calls"
        stub_prompt(tmp_path / "bin", [], calls)
        result = run_helper(
            f'mok_resolve_passphrase "{key}" "signing a test" && echo RESOLVED',
            tmp_path, path_prepend=str(tmp_path / "bin"))
        assert_refused(result)
        assert "RESOLVED" not in result.stdout

    def test_no_prompt_program_at_all_is_a_refusal(self, tmp_path):
        """An absent asking mechanism must not become a silent signing."""
        key, cert = make_key(tmp_path / "mok", PASSPHRASE)
        empty_bin = tmp_path / "emptybin"
        empty_bin.mkdir()
        result = run_helper(
            f'mok_resolve_passphrase "{key}" "signing a test" && echo RESOLVED',
            tmp_path,
            env_extra={"PATH": f"{empty_bin}:/usr/bin:/bin",
                       "MOK_ASK_PROGRAM": str(empty_bin / "does-not-exist")})
        assert_refused(result)
        assert "RESOLVED" not in result.stdout


class TestThePassphraseIsNeverPrinted:

    def test_it_is_not_echoed_on_success(self, tmp_path):
        key, cert = make_key(tmp_path / "mok", PASSPHRASE)
        calls = tmp_path / "calls"
        stub_prompt(tmp_path / "bin", [PASSPHRASE], calls)
        result = run_helper(
            f'mok_resolve_passphrase "{key}" "signing a test" || exit 9',
            tmp_path, path_prepend=str(tmp_path / "bin"))
        # Without this the test is vacuous: a run that never reached the helper
        # also prints no passphrase.
        assert result.returncode == 0, result.stdout + result.stderr
        assert PASSPHRASE not in result.stdout
        assert PASSPHRASE not in result.stderr

    def test_it_is_not_echoed_on_a_wrong_answer(self, tmp_path):
        key, cert = make_key(tmp_path / "mok", PASSPHRASE)
        calls = tmp_path / "calls"
        stub_prompt(tmp_path / "bin", ["wrong-1", "wrong-2", "wrong-3"], calls)
        result = run_helper(
            f'mok_resolve_passphrase "{key}" "signing a test"',
            tmp_path, path_prepend=str(tmp_path / "bin"))
        assert_refused(result)
        assert "wrong-1" not in result.stdout
        assert "wrong-1" not in result.stderr


class TestReadingTheKeyState:

    def test_an_encrypted_key_is_reported_as_protected(self, tmp_path):
        key, cert = make_key(tmp_path / "mok", PASSPHRASE)
        result = run_helper(f'mok_key_is_encrypted "{key}" && echo PROTECTED',
                            tmp_path)
        assert "PROTECTED" in result.stdout

    def test_a_plain_key_is_reported_as_unprotected(self, tmp_path):
        """The state every machine installed before this change is in."""
        key = make_plain_key(tmp_path / "mok")
        result = run_helper(f'mok_key_is_encrypted "{key}" || echo UNPROTECTED',
                            tmp_path)
        assert "UNPROTECTED" in result.stdout

    def test_an_absent_key_is_neither(self, tmp_path):
        """Absent is its own answer, never quietly 'unprotected'."""
        result = run_helper(
            f'mok_key_is_encrypted "{tmp_path}/nothing/mok.key"; '
            f'echo "rc=$?"',
            tmp_path)
        assert "rc=2" in result.stdout, (
            "an absent key must be distinguishable from a plain one: the first "
            "means there is nothing to sign with, the second means the thing to "
            "sign with is not protected")


class TestMigratingAKeyThatHasNoPassphrase:
    """Every machine installed before this change carries a plain key.

    Those machines are not left as they are and they are not quietly re-signed
    either. At the next kernel update the owner is asked to set a passphrase, the
    key is rewritten encrypted in place, the previous bytes are destroyed, and the
    result is read back before anything signs with it.

    A refused migration leaves the key exactly as it was. That is deliberate: the
    machine still boots, and the owner can do it at the next update. What it must
    NOT do is continue to sign unattended, which is the behaviour being retired.
    """

    def test_a_plain_key_is_rewritten_encrypted_and_reads_back(self, tmp_path):
        key = make_plain_key(tmp_path / "mok")
        before = key.read_bytes()
        calls = tmp_path / "calls"
        stub_prompt(tmp_path / "bin", [PASSPHRASE, PASSPHRASE], calls)
        result = run_helper(
            f'mok_migrate_plain_key "{key}" || exit 9\necho MIGRATED',
            tmp_path, path_prepend=str(tmp_path / "bin"))
        assert result.returncode == 0, result.stdout + result.stderr
        assert "MIGRATED" in result.stdout
        assert key.read_bytes() != before, "the key on disk was not rewritten"

        opens_empty = subprocess.run(
            ["openssl", "rsa", "-in", str(key), "-check", "-noout",
             "-passin", "pass:"], capture_output=True)
        assert opens_empty.returncode != 0, "the rewritten key is still plain"

        env = dict(os.environ, IGOS_TEST_PASS=PASSPHRASE)
        opens_right = subprocess.run(
            ["openssl", "rsa", "-in", str(key), "-check", "-noout",
             "-passin", "env:IGOS_TEST_PASS"], env=env, capture_output=True)
        assert opens_right.returncode == 0, (
            "the owner's new passphrase does not open the migrated key, so this "
            "machine can never sign again")

    def test_the_two_entries_must_match(self, tmp_path):
        key = make_plain_key(tmp_path / "mok")
        before = key.read_bytes()
        calls = tmp_path / "calls"
        stub_prompt(tmp_path / "bin", [PASSPHRASE, "something-else"], calls)
        result = run_helper(
            f'mok_migrate_plain_key "{key}" && echo MIGRATED',
            tmp_path, path_prepend=str(tmp_path / "bin"))
        assert_refused(result)
        assert "MIGRATED" not in result.stdout
        assert key.read_bytes() == before, (
            "a mistyped confirmation changed the key on disk")

    def test_a_refused_migration_leaves_the_key_exactly_as_it_was(self, tmp_path):
        key = make_plain_key(tmp_path / "mok")
        before = key.read_bytes()
        calls = tmp_path / "calls"
        stub_prompt(tmp_path / "bin", [], calls)
        result = run_helper(
            f'mok_migrate_plain_key "{key}" && echo MIGRATED',
            tmp_path, path_prepend=str(tmp_path / "bin"))
        assert_refused(result)
        assert key.read_bytes() == before

    def test_no_leftover_copy_of_the_plain_key_is_left_behind(self, tmp_path):
        """The point of the migration is that the plain bytes stop existing."""
        key = make_plain_key(tmp_path / "mok")
        plain_marker = key.read_text(encoding="utf-8").splitlines()[1][:40]
        calls = tmp_path / "calls"
        stub_prompt(tmp_path / "bin", [PASSPHRASE, PASSPHRASE], calls)
        result = run_helper(
            f'mok_migrate_plain_key "{key}" || exit 9',
            tmp_path, path_prepend=str(tmp_path / "bin"))
        assert result.returncode == 0, result.stdout + result.stderr
        for path in (tmp_path / "mok").rglob("*"):
            if path.is_file():
                text = path.read_text(encoding="utf-8", errors="replace")
                assert plain_marker not in text, (
                    f"a copy of the unprotected key survived at {path}")

    def test_an_already_encrypted_key_is_left_alone_and_nobody_is_asked(
            self, tmp_path):
        key, cert = make_key(tmp_path / "mok", PASSPHRASE)
        before = key.read_bytes()
        calls = tmp_path / "calls"
        stub_prompt(tmp_path / "bin", ["never-used"], calls)
        result = run_helper(
            f'mok_migrate_plain_key "{key}" || exit 9\necho DONE',
            tmp_path, path_prepend=str(tmp_path / "bin"))
        assert result.returncode == 0, result.stdout + result.stderr
        assert key.read_bytes() == before
        assert not calls.exists(), "the owner was asked about a key that is already protected"

    def test_the_new_passphrase_is_not_printed(self, tmp_path):
        key = make_plain_key(tmp_path / "mok")
        calls = tmp_path / "calls"
        stub_prompt(tmp_path / "bin", [PASSPHRASE, PASSPHRASE], calls)
        result = run_helper(
            f'mok_migrate_plain_key "{key}" || exit 9',
            tmp_path, path_prepend=str(tmp_path / "bin"))
        assert result.returncode == 0
        assert PASSPHRASE not in result.stdout
        assert PASSPHRASE not in result.stderr
