"""The shipped login.defs declares the password hashing method (R001.3 row 32).

The recipe used to rewrite a line upstream does not ship — it targeted
`#ENCRYPT_METHOD DES` while upstream ships `# ENCRYPT_METHOD YESCRYPT` — so the
substitution matched nothing, exited 0, and the shipped file declared no method
at all. Measured on an installed R001.2-03 machine on 2026-09-16:
`grep '^ENCRYPT_METHOD' /etc/login.defs` returned nothing.

These tests do not read the recipe and believe it. They EXECUTE the recipe's own
substitution and its guard against upstream's real text, and they execute them
against a file where the line has moved, which must stop the build instead of
shipping a system that declares nothing.
"""

import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SHADOW_BUILD_SH = REPO_ROOT / "packages" / "core" / "shadow" / "build.sh"

# Upstream shadow's login.defs, in the shape the shipped file on an installed
# machine carries: the method line present and commented, the accounting
# settings active, an ENV_PATH line for the other substitution in the same block.
UPSTREAM_LOGIN_DEFS = """\
#
# /etc/login.defs - Configuration control definitions for the shadow package.
#

#
# Enable display of unknown usernames when login(1) failures are recorded.
#
LOG_UNKFAIL_ENAB\tno

MAIL_DIR\t/var/spool/mail
ENV_SUPATH\tPATH=/usr/local/sbin:/usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin
ENV_PATH\tPATH=/usr/local/bin:/bin:/usr/bin

#
# This variable is deprecated. You should use ENCRYPT_METHOD instead.
#
#MD5_CRYPT_ENAB\tno

#
# Only works if ENCRYPT_METHOD is set to SHA256 or SHA512.
#
#SHA_CRYPT_MIN_ROUNDS 5000

# ENCRYPT_METHOD YESCRYPT

#
# Only works if ENCRYPT_METHOD is set to YESCRYPT.
#
#YESCRYPT_COST_FACTOR 5
"""

MOVED_LOGIN_DEFS = UPSTREAM_LOGIN_DEFS.replace(
    "# ENCRYPT_METHOD YESCRYPT", "# PASSWORD_HASHING_METHOD YESCRYPT")


def _recipe_configure_body():
    """The recipe's configure() body, so the test runs what the build runs."""
    text = SHADOW_BUILD_SH.read_text()
    start = text.index("configure() {")
    depth, i = 0, start
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    body = text[text.index("\n", start) + 1:i]
    return body


def _run_login_defs_edit(login_defs_text):
    """Run only the login.defs part of the recipe's configure() on a copy.

    The rest of configure() touches the shadow source tree, which is not present
    here; the login.defs lines are lifted verbatim from the recipe so what runs
    is the recipe's own text and not a paraphrase of it.
    """
    body = _recipe_configure_body()
    lines = body.splitlines(keepends=True)
    keep, taking = [], False
    for line in lines:
        if "sed -e 's/^#[[:space:]]*ENCRYPT_METHOD" in line:
            taking = True
        # The login.defs block ends where the recipe turns to the source tree.
        # Nothing past this point may run here: `touch /usr/bin/passwd` writes
        # to the real system, and a test that writes to the real system is a
        # defect of its own.
        if taking and (line.lstrip().startswith("touch ")
                       or line.lstrip().startswith("./configure")):
            break
        if taking:
            keep.append(line)
    assert keep, "the recipe no longer carries the login.defs substitution"
    script = "set -e\n" + "".join(keep)

    tmp = Path(tempfile.mkdtemp())
    (tmp / "etc").mkdir()
    (tmp / "etc" / "login.defs").write_text(login_defs_text)
    proc = subprocess.run(["bash", "-c", script], cwd=tmp,
                          capture_output=True, text=True)
    return proc, (tmp / "etc" / "login.defs").read_text()


class TestTheRecipeEditsUpstreamsRealText(unittest.TestCase):
    def test_the_shipped_file_declares_the_method(self):
        proc, result = _run_login_defs_edit(UPSTREAM_LOGIN_DEFS)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("\nENCRYPT_METHOD YESCRYPT\n", result,
                      "the shipped file must declare the method, not leave "
                      "upstream's commented line in place")
        self.assertNotIn("# ENCRYPT_METHOD YESCRYPT", result)

    def test_the_prose_lines_that_mention_the_setting_are_left_alone(self):
        _proc, result = _run_login_defs_edit(UPSTREAM_LOGIN_DEFS)
        self.assertIn("# This variable is deprecated. You should use "
                      "ENCRYPT_METHOD instead.", result)
        self.assertIn("# Only works if ENCRYPT_METHOD is set to SHA256 or "
                      "SHA512.", result)

    def test_the_other_edits_in_the_same_block_still_happen(self):
        _proc, result = _run_login_defs_edit(UPSTREAM_LOGIN_DEFS)
        self.assertIn("MAIL_DIR\t/var/mail", result)
        self.assertIn("ENV_PATH\tPATH=/usr/local/bin:/usr/bin:"
                      "/usr/local/sbin:/usr/sbin", result)

    def test_a_moved_upstream_line_stops_the_build_instead_of_shipping(self):
        proc, result = _run_login_defs_edit(MOVED_LOGIN_DEFS)
        self.assertNotEqual(proc.returncode, 0,
                            "a substitution that matched nothing must halt the "
                            "build; exiting 0 is how this shipped undeclared "
                            "for as long as it did")
        self.assertIn("ENCRYPT_METHOD", proc.stderr)
        self.assertNotIn("\nENCRYPT_METHOD YESCRYPT\n", result)


class TestTheRecipeTextItself(unittest.TestCase):
    def test_the_dead_substitution_is_gone(self):
        # The old form as an EXPRESSION, not as a mention: the comment above the
        # new substitution names it deliberately, so that the next reader knows
        # what was wrong, and that mention must not fail this test.
        self.assertNotIn("s:#ENCRYPT_METHOD DES:", SHADOW_BUILD_SH.read_text(),
                         "the substitution that matched nothing must not remain")

    def test_the_guard_is_present(self):
        text = SHADOW_BUILD_SH.read_text()
        self.assertIn("grep -qE '^ENCRYPT_METHOD[[:space:]]+YESCRYPT$'", text)


if __name__ == "__main__":
    unittest.main()
