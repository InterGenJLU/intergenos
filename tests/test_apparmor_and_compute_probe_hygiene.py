# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Two recipe-level hardening items from the post-burn audit batch.

1. APPARMOR PROFILE LOADING HAS ONE OWNER, AND IT FAILS ON A FATAL PARSE.

The apparmor recipe carried a post_install loop that walked
/etc/apparmor.d/*, ran `apparmor_parser -r` on each top-level profile, and on
failure printed a WARNING and continued. pkm's own canonical hook
`apparmor-reload` does the same job: same trigger set (the regex
^etc/apparmor\\.d/[^/]+$ is exactly "top-level files under /etc/apparmor.d"),
same chroot and absent-interface guards, one parser invocation over all
matched profiles — and it is CRITICAL, so a failure flags the operation for
rollback instead of printing a line nobody reads.

Both ran on every install of the package: two parser passes over the same
profiles with contradictory verdicts on failure, and the weaker one ran
second-guessing nothing. Deleting the recipe loop IS the fail-on-fatal
hardening the audit batch asked for, and it widens coverage at the same time:
the canonical hook fires for profiles shipped by ANY package, while the
recipe loop only ever ran when apparmor itself was installed.

2. THE HIP PROBES RESOLVE BY BARE NAME, IN EVERY EXEC CONTEXT.

/opt/rocm/bin is on no default PATH. The measured consequence for the sibling
tool was a silent product defect: a GPU library's subprocess call to
`rocminfo` found nothing, so the warp size defaulted to 64 on wave32 silicon
and every ROCm machine mis-detected. That was fixed for rocminfo and
rocm_agent_enumerator with /usr/bin symlinks (rocminfo r2, decided
2026-08-13, chosen over a profile.d PATH entry because profile.d never
reaches a systemd service).

hipcc and hipconfig are the same class and are named in the same tracked
batch, and the tree carries its own evidence that bare-name resolution fails
today: packages/ai/bitsandbytes/build.sh has to prepend /opt/rocm/bin to PATH
in both configure() and build() for the one reason its own comment states —
its upstream CMakeLists calls `hipconfig --version` by bare name — and
packages/compute/llama-cpp-hip/build.sh calls hipconfig through its absolute
path for the same reason. A build-time PATH export fixes neither a runtime
subprocess nor a service, which is the context the rocminfo decision named.
"""

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
APPARMOR_BUILD = REPO_ROOT / "packages/core/apparmor/build.sh"
APPARMOR_YML = REPO_ROOT / "packages/core/apparmor/package.yml"
ROCM_HIP_BUILD = REPO_ROOT / "packages/compute/rocm-hip/build.sh"
ROCM_HIP_YML = REPO_ROOT / "packages/compute/rocm-hip/package.yml"
ROCMINFO_BUILD = REPO_ROOT / "packages/compute/rocminfo/build.sh"


def release_of(package_yml: Path) -> int:
    for line in package_yml.read_text().splitlines():
        m = re.match(r"^release:\s*(\d+)", line)
        if m:
            return int(m.group(1))
    raise AssertionError(f"{package_yml} declares no release")


class ApparmorProfileLoadingHasOneCriticalOwner(unittest.TestCase):

    def test_the_recipe_no_longer_runs_the_parser_itself(self):
        text = APPARMOR_BUILD.read_text()
        self.assertNotIn(
            "apparmor_parser -r", text,
            "the recipe still loads profiles itself; pkm's critical "
            "apparmor-reload hook is the owner")

    def test_the_recipe_declares_no_post_install_at_all(self):
        text = APPARMOR_BUILD.read_text()
        self.assertNotRegex(
            text, r"(?m)^post_install\s*\(\)",
            "the recipe's only post_install work was the duplicate profile "
            "load; an empty one left behind would ship a .scripts entry that "
            "does nothing")

    def test_no_comment_still_claims_the_recipe_loads_profiles(self):
        """A comment describing work the file no longer does is a stub in
        shape: a later reader acts on it as if it were true."""
        text = APPARMOR_BUILD.read_text()
        self.assertNotIn("post_install() loads profiles directly", text)

    def test_the_canonical_hook_covers_the_same_profiles_and_is_critical(self):
        from pkm.hooks import CANONICAL_HOOKS
        hook = next((h for h in CANONICAL_HOOKS
                     if h.id == "apparmor-reload"), None)
        self.assertIsNotNone(
            hook, "no canonical apparmor-reload hook: deleting the recipe "
                  "loop would leave profiles unloaded")
        self.assertTrue(hook.critical,
                        "a failed profile load must flag the operation, not "
                        "warn and continue — that was the recipe's weakness")
        # The set the deleted loop iterated: top-level files under
        # /etc/apparmor.d, with the abstractions/ tunables/ abi/ disable/
        # local/ cache/ subdirectories excluded.
        for profile in ("etc/apparmor.d/usr.bin.foo",
                        "etc/apparmor.d/sbin.dhclient"):
            self.assertTrue(hook.pattern.match(profile), profile)
        for not_a_profile in ("etc/apparmor.d/abstractions/base",
                              "etc/apparmor.d/tunables/global",
                              "etc/apparmor.d/local/usr.bin.foo",
                              "etc/apparmor.d/disable/usr.bin.foo"):
            self.assertIsNone(hook.pattern.match(not_a_profile), not_a_profile)

    def test_the_recipe_release_was_bumped_for_the_behaviour_change(self):
        self.assertGreaterEqual(
            release_of(APPARMOR_YML), 3,
            "install-time behaviour changed; the release must advance or the "
            "publish preflight refuses a same-version republish")


class HipProbesResolveByBareName(unittest.TestCase):

    def test_hipcc_and_hipconfig_are_symlinked_into_usr_bin(self):
        text = ROCM_HIP_BUILD.read_text()
        for tool in ("hipcc", "hipconfig"):
            self.assertRegex(
                text,
                rf"ln -sf? /opt/rocm/bin/{tool} \"\$\{{DESTDIR\}}/usr/bin/{tool}\"",
                f"{tool} is not reachable by bare name outside a login shell")

    def test_the_symlinks_are_declared_as_load_bearing_paths(self):
        yml = ROCM_HIP_YML.read_text()
        for tool in ("hipcc", "hipconfig"):
            self.assertIn(f"/usr/bin/{tool}", yml,
                          f"/usr/bin/{tool} is not in verify_paths, so the "
                          f"pre-squashfs audit would not catch its absence")

    def test_the_recipe_release_was_bumped_by_hand(self):
        """This recipe pins upstream tarballs and ships no first-party
        sibling files, so the release bumper does not track it. Changed bytes
        at an unchanged (version, release) is exactly what the publish
        preflight refuses."""
        self.assertGreaterEqual(release_of(ROCM_HIP_YML), 3)

    def test_it_matches_the_shape_already_decided_for_the_sibling_tool(self):
        """The same remedy, in the same place, for the same reason — stated
        as a test so the two cannot drift apart."""
        rocminfo = ROCMINFO_BUILD.read_text()
        self.assertIn('ln -sf /opt/rocm/bin/rocminfo "${DESTDIR}/usr/bin/rocminfo"',
                      rocminfo,
                      "fixture premise: the sibling tool's symlink is the "
                      "decided shape")


class TheEvidenceForTheProbeFixIsStillInTheTree(unittest.TestCase):
    """The grounding, pinned. If a future edit removes these workarounds the
    justification above has to be re-read, not silently inherited."""

    def test_a_first_party_recipe_still_has_to_patch_path_for_hipconfig(self):
        bnb = REPO_ROOT / "packages/ai/bitsandbytes/build.sh"
        text = bnb.read_text()
        self.assertIn('export PATH="/opt/rocm/bin:$PATH"', text)
        self.assertIn("hipconfig", text,
                      "the PATH patch's stated reason is the bare-name "
                      "hipconfig call")

    def test_a_first_party_recipe_still_calls_hipconfig_by_absolute_path(self):
        llama = REPO_ROOT / "packages/compute/llama-cpp-hip/build.sh"
        self.assertIn("/opt/rocm/bin/hipconfig", llama.read_text())


if __name__ == "__main__":
    unittest.main()
