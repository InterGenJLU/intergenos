# SPDX-License-Identifier: GPL-3.0-or-later
# The population the lib32 staged-payload assertion judges.
#
# lib32_assert_only_lib32 (scripts/lib32-env.sh) asserts that "the staged
# package payload contains nothing outside /usr/lib32". Its population was the
# WHOLE staging root, which is only the same thing as the payload when the root
# belongs to this package alone. A --stage-only build sets DESTDIR to the shared
# system root (igos-build/builder.py: the untracked branch), so a second build of
# a lib32 package refused on the license file its own previous run had left
# there, and a first build into a root that already held any other package would
# refuse on that package's files.
#
# The profile therefore records what the staging root ALREADY held immediately
# before it stages anything, and the assertion judges what appeared since. These
# wedges drive the REAL shipped bash against synthetic staging roots: that a
# repeat build passes, that every stray this build actually produced still
# refuses, and that a baseline belonging to a different root is never used.

import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LIB32_ENV = REPO / "scripts" / "lib32-env.sh"
STYLES = REPO / "igos-build" / "styles"


def run_bash(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


def profile() -> str:
    return f'source "{LIB32_ENV}" >/dev/null 2>&1; '


def private_root(tmp_path: Path, name: str = "m32root") -> Path:
    """The private install root every lib32 recipe installs into before it
    stages: `make DESTDIR="$PWD/m32root" install`."""
    root = tmp_path / name
    (root / "usr/lib32/pkgconfig").mkdir(parents=True)
    (root / "usr/lib32/libogg.so.0").write_text("elf")
    (root / "usr/lib32/pkgconfig/ogg.pc").write_text("pc")
    return root


def used_staging_root(tmp_path: Path) -> Path:
    """A staging root that is NOT this package's alone — the --stage-only
    system root. It carries the exact content the 2026-09-19 measurement hit:
    the package's own license directory, written by the previous build's
    bundle-license phase, plus another package's payload."""
    dest = tmp_path / "system"
    (dest / "usr/share/licenses/lib32-libogg").mkdir(parents=True)
    (dest / "usr/share/licenses/lib32-libogg/COPYING").write_text("license")
    (dest / "usr/lib/pkgconfig").mkdir(parents=True)
    (dest / "usr/lib/libz.so.1").write_text("another package")
    (dest / "usr/bin").mkdir(parents=True)
    (dest / "usr/bin/gzip").write_text("another package")
    return dest


def stage_and_assert(dest: Path, root: Path, extra: str = "") -> str:
    return (
        profile()
        + f'DESTDIR="{dest}"; '
        + f'lib32_stage_libs "{root}" {extra} && lib32_assert_only_lib32 {extra}'
    )


# ------------------------------------------------- the row's own defect ----

def test_a_build_into_a_used_staging_root_passes(tmp_path):
    # The reported refusal, reproduced as a wedge: the staging root already
    # holds this package's license file and another package's payload, and the
    # build stages a clean lib32 tree. Nothing this build staged sits outside
    # /usr/lib32, so the assertion must pass.
    dest = used_staging_root(tmp_path)
    root = private_root(tmp_path)
    r = run_bash(stage_and_assert(dest, root))
    assert r.returncode == 0, r.stderr


def test_a_repeat_build_into_its_own_previous_payload_passes(tmp_path):
    # The second half of the same class: the root already holds the payload
    # this package staged last time. Re-staging the same files adds nothing
    # outside /usr/lib32 and must not refuse.
    dest = tmp_path / "system"
    (dest / "usr/lib32").mkdir(parents=True)
    (dest / "usr/lib32/libogg.so.0").write_text("previous")
    (dest / "usr/share/licenses/lib32-libogg").mkdir(parents=True)
    (dest / "usr/share/licenses/lib32-libogg/COPYING").write_text("license")
    root = private_root(tmp_path)
    r = run_bash(stage_and_assert(dest, root))
    assert r.returncode == 0, r.stderr


# --------------------------------------------- the guard is not blinded ----

def test_a_stray_this_build_staged_still_fails(tmp_path):
    # The narrowing must not cost the guard its teeth: an UNDECLARED extra that
    # this build staged sits outside /usr/lib32 and must still refuse, even
    # though the root was already dirty.
    dest = used_staging_root(tmp_path)
    root = private_root(tmp_path)
    (root / "usr/share/vulkan/icd.d").mkdir(parents=True)
    (root / "usr/share/vulkan/icd.d/ogg_icd.i686.json").write_text("{}")
    r = run_bash(
        profile()
        + f'DESTDIR="{dest}"; '
        + f'lib32_stage_libs "{root}" usr/share/vulkan/icd.d '
        + "&& lib32_assert_only_lib32"
    )
    assert r.returncode != 0, "an extra staged but not declared to the assert must FATAL"
    assert "FATAL" in r.stderr
    assert "icd.d" in r.stderr
    # ...and it names what this build staged, not what the root already held.
    assert "usr/bin/gzip" not in r.stderr, r.stderr
    assert "usr/lib/libz.so.1" not in r.stderr, r.stderr


def test_a_stray_written_after_the_baseline_fails(tmp_path):
    # The population is decided by WHEN, not by path class: a file the recipe
    # writes into the staging root after staging — the same path class the root
    # already carried legitimately — is this build's payload and must refuse.
    dest = used_staging_root(tmp_path)
    root = private_root(tmp_path)
    r = run_bash(
        profile()
        + f'DESTDIR="{dest}"; '
        + f'lib32_stage_libs "{root}" && '
        + 'install -Dm644 /dev/null "${DESTDIR}/usr/share/licenses/lib32-libogg/EXTRA" && '
        + "lib32_assert_only_lib32"
    )
    assert r.returncode != 0, "a file this build added outside /usr/lib32 must FATAL"
    assert "FATAL" in r.stderr
    assert "EXTRA" in r.stderr


def test_a_stray_is_reported_even_behind_five_pre_existing_paths(tmp_path):
    # The refusal prints the first five matches. Pre-existing paths must be
    # removed from the sweep BEFORE that cut, or a real stray hides behind the
    # root's existing contents.
    dest = tmp_path / "system"
    (dest / "usr/share/doc").mkdir(parents=True)
    for i in range(12):
        (dest / "usr/share/doc" / f"old-{i:02d}").write_text("previous build")
    root = private_root(tmp_path)
    r = run_bash(
        profile()
        + f'DESTDIR="{dest}"; '
        + f'lib32_stage_libs "{root}" && '
        + 'install -Dm644 /dev/null "${DESTDIR}/usr/include/ogg/ogg.h" && '
        + "lib32_assert_only_lib32"
    )
    assert r.returncode != 0
    assert "ogg.h" in r.stderr, r.stderr
    assert "old-" not in r.stderr, r.stderr


def test_a_stray_empty_directory_this_build_created_fails(tmp_path):
    # The empty-directory sweep is narrowed by the same baseline and keeps the
    # same contract.
    dest = used_staging_root(tmp_path)
    root = private_root(tmp_path)
    r = run_bash(
        profile()
        + f'DESTDIR="{dest}"; '
        + f'lib32_stage_libs "{root}" && '
        + 'mkdir -p "${DESTDIR}/usr/include/ogg" && '
        + "lib32_assert_only_lib32"
    )
    assert r.returncode != 0, "an empty directory this build created must FATAL"
    assert "EMPTY" in r.stderr


def test_a_pre_existing_empty_directory_does_not_fail(tmp_path):
    # Its mirror: an empty directory the root already carried is not this
    # build's payload.
    dest = used_staging_root(tmp_path)
    (dest / "usr/share/empty-from-a-previous-build").mkdir(parents=True)
    root = private_root(tmp_path)
    r = run_bash(stage_and_assert(dest, root))
    assert r.returncode == 0, r.stderr


# ------------------------------------------------- the baseline's scope ----

def test_a_baseline_recorded_for_another_root_is_refused(tmp_path):
    # The bash drivers run every package in ONE shell, so a baseline could
    # outlive the package that recorded it. A baseline that does not belong to
    # the staging root now being judged is never used to narrow anything.
    first = tmp_path / "first"
    (first / "usr/lib32").mkdir(parents=True)
    second = used_staging_root(tmp_path)
    root = private_root(tmp_path)
    r = run_bash(
        profile()
        + f'DESTDIR="{first}"; lib32_stage_libs "{root}"; '
        + f'DESTDIR="{second}"; lib32_assert_only_lib32'
    )
    assert r.returncode != 0, "a baseline from another staging root must FATAL"
    assert "baseline" in r.stderr, r.stderr
    assert str(first) in r.stderr, r.stderr


def test_the_baseline_does_not_survive_its_own_assertion(tmp_path):
    # One staging pass, one judgement: the recorded baseline is cleared by the
    # assertion that consumed it, so the next package cannot inherit it.
    dest = used_staging_root(tmp_path)
    root = private_root(tmp_path)
    r = run_bash(
        profile()
        + f'DESTDIR="{dest}"; lib32_stage_libs "{root}"; '
        + 'echo "DURING=[${IGOS_LIB32_STAGE_BASELINE:-}]"; '
        + "lib32_assert_only_lib32; "
        + 'echo "AFTER=[${IGOS_LIB32_STAGE_BASELINE:-}]"'
    )
    assert r.returncode == 0, r.stderr
    assert "DURING=[]" not in r.stdout, r.stdout
    assert "AFTER=[]" in r.stdout, r.stdout


def test_lib32_env_end_clears_the_baseline(tmp_path):
    # The profile cleans up after itself on the success path, the baseline
    # included — and the file it wrote does not outlive the build.
    dest = used_staging_root(tmp_path)
    root = private_root(tmp_path)
    r = run_bash(
        profile()
        + f'DESTDIR="{dest}"; lib32_stage_libs "{root}"; '
        + 'echo "FILE=${IGOS_LIB32_STAGE_BASELINE}"; '
        + "lib32_env_end; "
        + 'echo "AFTER=[${IGOS_LIB32_STAGE_BASELINE:-}]"'
    )
    assert r.returncode == 0, r.stderr
    assert "AFTER=[]" in r.stdout, r.stdout
    recorded = [
        line.split("=", 1)[1]
        for line in r.stdout.splitlines()
        if line.startswith("FILE=")
    ]
    assert recorded and recorded[0], r.stdout
    assert not os.path.exists(recorded[0]), "the baseline file must be removed"


# ------------------------------------- the precondition, checked in tree ----

def _lib32_recipes() -> list[Path]:
    return sorted(
        p
        for p in REPO.glob("packages/*/lib32-*/build.sh")
        if "lib32_assert_only_lib32" in p.read_text()
    )


def test_the_tree_carries_lib32_recipes_to_check():
    # An instrument with an empty population certifies nothing.
    assert len(_lib32_recipes()) >= 15


def test_every_lib32_recipe_stages_before_it_asserts():
    # The assertion can only judge this build's payload if the profile recorded
    # the root first, which lib32_stage_libs does. A recipe that asserted
    # without staging would get the whole-root answer this row removed.
    offenders = [
        str(p.relative_to(REPO))
        for p in _lib32_recipes()
        if "lib32_stage_libs" not in p.read_text()
    ]
    assert not offenders, f"assert without staging: {offenders}"


def test_no_lib32_recipe_installs_straight_into_the_staging_root():
    # The baseline is recorded when staging begins, so anything a recipe wrote
    # into DESTDIR before that point would be read as pre-existing. Every lib32
    # recipe installs into a private root and stages from it; this keeps that
    # true as recipes are added.
    offenders = []
    for p in _lib32_recipes():
        for number, line in enumerate(p.read_text().splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "install" not in stripped:
                continue
            if 'DESTDIR="${DESTDIR}"' in stripped or 'DESTDIR="$DESTDIR"' in stripped:
                offenders.append(f"{p.relative_to(REPO)}:{number}: {stripped}")
    assert not offenders, f"installs straight into the staging root: {offenders}"


def test_every_lib32_style_pairs_staging_with_the_assertion():
    # The Python tiers compose the install phase as a list of commands, each run
    # in its own shell, so the baseline only reaches the assertion when the two
    # calls sit in the SAME command string.
    for name in ("autotools.py", "make.py", "meson.py"):
        text = (STYLES / name).read_text()
        paired = [
            line
            for line in text.splitlines()
            if "lib32_assert_only_lib32" in line
        ]
        assert paired, f"{name}: no lib32 assertion call site"
        for line in paired:
            assert "lib32_stage_libs" in line, (
                f"{name}: the assertion must share its command with the staging "
                f"call that records the baseline: {line.strip()}"
            )


def test_no_lib32_style_installs_straight_into_the_staging_root():
    # The style-driven half of the lib32 set (autotools/meson/make) never names
    # the helpers in its own recipe — the style composes the install phase. The
    # same precondition applies there: the install must go to a private root, so
    # that nothing of this build is already in DESTDIR when the record is taken.
    for name in ("autotools.py", "make.py", "meson.py"):
        text = (STYLES / name).read_text()
        lines = text.splitlines()
        for number, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#") or "m32root" not in stripped:
                continue
            if "install" not in stripped:
                continue
            assert '${DESTDIR}' not in stripped, (
                f"{name}:{number}: a lib32 install must stage through a private "
                f"root, not into DESTDIR: {stripped}"
            )
        # ...and the lib32 install phase must actually use one.
        assert "m32root" in text, f"{name}: no private install root in the lib32 lane"
