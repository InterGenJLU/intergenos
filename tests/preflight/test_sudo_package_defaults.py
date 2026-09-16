"""Exercise the sudo recipe's installed policy payload."""

import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]


def test_sudo_dropins_preserve_the_main_secure_path(tmp_path):
    # Stand in for upstream make install; execute the real policy installation.
    script = r'''
set -e
source "$1"
DESTDIR="$2"
make() {
  # Stand in for upstream's `make install`, faithfully enough for what the
  # recipe does afterwards: it installs the binary AND the main sudoers file,
  # which the recipe then sets to the mode sudo's syntax checker requires. A
  # stub that installed only the binary made the recipe's chmod fail here while
  # a real build was fine.
  /usr/bin/install -Dm755 /usr/bin/true "$DESTDIR/usr/bin/sudo"
  /usr/bin/install -Dm644 /dev/null "$DESTDIR/etc/sudoers"
  printf 'Defaults secure_path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin"\nroot ALL=(ALL:ALL) ALL\n' > "$DESTDIR/etc/sudoers"
}
do_install
'''
    subprocess.run(
        ["/usr/bin/bash", "-c", script, "sudo-policy-test",
         str(REPO / "packages/core/sudo/build.sh"), str(tmp_path)],
        check=True, capture_output=True, text=True,
    )
    policies = list((tmp_path / "etc/sudoers.d").iterdir())
    assert policies
    for policy in policies:
        active = [line.split("#", 1)[0] for line in policy.read_text().splitlines()]
        assert all("secure_path" not in line for line in active)
    assert (tmp_path / "etc/sudoers.d/00-sudo").read_text() == "%wheel ALL=(ALL) ALL\n"
    assert (tmp_path / "etc/pam.d/sudo").is_file()
