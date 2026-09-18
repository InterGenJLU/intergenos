"""The reader of the HIP build's architecture record must not take a comment's
words as architectures.

The record is the file the HIP recipe installs at
``/opt/rocm/share/llama-cpp-hip/gpu-targets``. It is a machine-written list of
the GPU architectures the installed build carries device code for, and the
engine gate asks it whether the card that will be served on is covered.

The reader split the whole file on whitespace and dropped only the tokens that
themselves began with ``#``. A record whose comment reads::

    # written by the recipe
    gfx1100;gfx1102

therefore yielded ``{'by', 'gfx1100', 'gfx1102', 'recipe', 'the', 'written'}``:
five English words admitted as architecture names alongside the two real ones.

WHY THAT MATTERS even though today's shipped record carries no comment. The set
is used two ways, and the words break both in the same direction — towards
claiming support that was never declared. ``hip_is_supported_here`` intersects
it with the architectures detected on the machine, and the engine gate compares
a specific card's architecture against it. A word that happens to equal a gfx
name — and a comment naming the architectures it was built for is the natural
comment for this file to carry — is indistinguishable from a declaration. The
refusal this gate exists to produce is what keeps llama-server from segfaulting
at model load on a card the build has no code for, so a reader that can be
talked into a wrong "yes" by prose is the failure mode to close, not the
cosmetic one.

The fix is to strip comments LINE-wise: everything from a ``#`` to the end of
that line goes, and what remains is split.
"""

import textwrap

from intergen.serving_device import hip_build_gpu_targets


def _record(tmp_path, text):
    p = tmp_path / "gpu-targets"
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_a_comment_line_contributes_no_architectures(tmp_path):
    """The defect, stated as the cut states it: a leading comment line."""
    path = _record(tmp_path, textwrap.dedent("""\
        # written by the recipe
        gfx1100;gfx1102
        """))
    assert hip_build_gpu_targets(path) == {"gfx1100", "gfx1102"}


def test_a_trailing_comment_on_the_architecture_line_contributes_nothing(tmp_path):
    """A comment after the list on the SAME line ends at the line's end."""
    path = _record(tmp_path, "gfx1100;gfx1102  # built for the two cards here\n")
    assert hip_build_gpu_targets(path) == {"gfx1100", "gfx1102"}


def test_a_comment_naming_an_architecture_does_not_declare_it(tmp_path):
    """The case that turns the defect into a wrong answer rather than noise.

    A comment that mentions gfx1030 must not put gfx1030 into the declared set,
    because the build carries no device code for it.
    """
    path = _record(tmp_path, textwrap.dedent("""\
        # dropped gfx1030 from this build, it was never tested
        gfx1100;gfx1102
        """))
    targets = hip_build_gpu_targets(path)
    assert "gfx1030" not in targets
    assert targets == {"gfx1100", "gfx1102"}


def test_a_hash_comments_out_the_rest_of_its_line(tmp_path):
    """A ``#`` mid-line ends the line, as it does in every other record here.

    THIS IS A DELIBERATE BEHAVIOUR CHANGE and it is the only one. Before, a
    ``#gfx1030`` token was dropped by itself and ``gfx1102`` after it on the same
    line was still read. Under line-wise comment stripping the whole remainder
    of the line goes with it, so only ``gfx1100`` is declared.

    It is the right direction. The change can only ever DROP architectures from
    the declared set, never add one: a dropped architecture makes the engine gate
    refuse HIP on a card it would have accepted, and the assistant serves on the
    Vulkan engine instead — working, slower. An ADDED one makes the gate accept
    HIP on a card the build has no device code for, and llama-server segfaults at
    model load. The file is machine-written by the recipe from one variable
    (packages/compute/llama-cpp-hip/build.sh writes ``printf '%s\\n'
    "${GPU_TARGETS}"``), so it carries no comment at all today and no shipped
    record takes this path.
    """
    path = _record(tmp_path, "gfx1100;#gfx1030;gfx1102\n")
    assert hip_build_gpu_targets(path) == {"gfx1100"}


def test_a_comment_ends_at_its_own_line_and_not_beyond(tmp_path):
    """The line after a comment line is read in full."""
    path = _record(tmp_path, "# a note\ngfx1100;gfx1102\n# another note\ngfx1201\n")
    assert hip_build_gpu_targets(path) == {"gfx1100", "gfx1102", "gfx1201"}


def test_the_shipped_record_shape_is_unchanged(tmp_path):
    """The record this project actually ships reads exactly as before."""
    path = _record(tmp_path, "gfx1100;gfx1102;gfx1201\n")
    assert hip_build_gpu_targets(path) == {"gfx1100", "gfx1102", "gfx1201"}


def test_separators_still_all_work(tmp_path):
    """Semicolons, commas, spaces and newlines all still separate entries."""
    path = _record(tmp_path, "gfx1100;gfx1102, gfx1201\ngfx900\n")
    assert hip_build_gpu_targets(path) == {
        "gfx1100", "gfx1102", "gfx1201", "gfx900"}


def test_a_record_that_is_only_comments_declares_nothing(tmp_path):
    """An empty set is the reader's 'unknown', and callers treat it as unknown."""
    path = _record(tmp_path, "# this build declares nothing yet\n#\n")
    assert hip_build_gpu_targets(path) == set()


def test_an_absent_record_still_returns_the_empty_set(tmp_path):
    """Unchanged behaviour: absent or unreadable is unknown, never a guess."""
    assert hip_build_gpu_targets(str(tmp_path / "not-here")) == set()


def test_an_unreadable_record_still_returns_the_empty_set(tmp_path):
    """A directory in the record's place raises OSError on open."""
    d = tmp_path / "gpu-targets"
    d.mkdir()
    assert hip_build_gpu_targets(str(d)) == set()
