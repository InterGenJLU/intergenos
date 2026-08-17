"""Unit tests for validate-tarball-membership.

The gate asserts that every path a generated package's install step takes from
its extracted source tree is a member of that package's generated tarball. These
tests build miniature package trees and tarballs in tmp_path — nothing here
reads the real repository, touches the network, or needs privilege.
"""
from __future__ import annotations

import importlib.util
import tarfile
from pathlib import Path

import pytest

SCRIPT_PATH = (Path(__file__).resolve().parent.parent.parent
               / "scripts" / "validate-tarball-membership.py")

# Dynamic import (script has hyphens in the filename)
spec = importlib.util.spec_from_file_location("validate_tarball_membership",
                                              SCRIPT_PATH)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)


# ---------------------------------------------------------------------------
# fixture helpers
# ---------------------------------------------------------------------------

def make_package(packages_dir: Path, name: str, do_install: str,
                 version: str = "1.0", tarball: str | None = None,
                 release_staged_source: str | None = None) -> Path:
    """A minimal tier/package recipe declaring one generated source.

    `release_staged_source` is written verbatim as the recipe's top-level
    declaration, including deliberately malformed values, so the gate's
    strictness about it can be tested rather than assumed.
    """
    pkg_dir = packages_dir / "desktop" / name
    pkg_dir.mkdir(parents=True)
    url = tarball if tarball else f"{name}-${{version}}.tar.xz"
    declaration = ("" if release_staged_source is None
                   else f"release_staged_source: {release_staged_source}\n")
    (pkg_dir / "package.yml").write_text(
        f"name: {name}\n"
        f"version: \"{version}\"\n"
        f"release: 1\n"
        f"tier: desktop\n"
        f"build_style: custom\n"
        f"install_func: do_install\n"
        f"{declaration}"
        f"source:\n"
        f"- url: file:///{url}\n"
        f"  generated: true\n",
        encoding="utf-8")
    (pkg_dir / "build.sh").write_text(
        "#!/bin/bash\n"
        "do_install() {\n"
        f"{do_install}"
        "}\n",
        encoding="utf-8")
    return pkg_dir


def make_tarball(sources_dir: Path, filename: str, members: dict[str, str],
                 top: str = "pkg") -> Path:
    """A tarball whose members sit under one top-level dir the builder strips."""
    sources_dir.mkdir(parents=True, exist_ok=True)
    stage = sources_dir / f".stage-{filename}"
    for rel, content in members.items():
        target = stage / top / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    out = sources_dir / filename
    with tarfile.open(out, "w:xz") as tf:
        tf.add(stage / top, arcname=top)
    return out


@pytest.fixture()
def tree(tmp_path: Path):
    packages = tmp_path / "packages"
    sources = tmp_path / "sources"
    packages.mkdir()
    sources.mkdir()
    return packages, sources


def run_gate(capsys, packages: Path, sources: Path) -> tuple[int, str, str]:
    import sys
    argv = sys.argv
    sys.argv = ["validate-tarball-membership.py",
                "--packages-dir", str(packages), "--sources-dir", str(sources)]
    try:
        rc = mod.main()
    finally:
        sys.argv = argv
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


# ---------------------------------------------------------------------------
# a known-good package passes
# ---------------------------------------------------------------------------

def test_known_good_package_passes(tree, capsys):
    packages, sources = tree
    make_package(packages, "goodpkg",
                 '    set -e\n'
                 '    install -dm755 "${DESTDIR}/usr/bin"\n'
                 '    install -m755 app.py "${DESTDIR}/usr/bin/app.py"\n'
                 '    install -m644 icon.svg "${DESTDIR}/usr/share/icon.svg"\n')
    make_tarball(sources, "goodpkg-1.0.tar.xz",
                 {"app.py": "x", "icon.svg": "<svg/>"})

    rc, out, err = run_gate(capsys, packages, sources)
    assert rc == 0, err
    assert "PASS" in out
    assert "2 consumed path(s)" in out


def test_directory_creation_is_not_a_consumed_path(tree, capsys):
    """`install -dm755` creates; only `-D` forms carry a source."""
    packages, sources = tree
    make_package(packages, "dirpkg",
                 '    install -dm755 "${DESTDIR}/usr/share/things"\n'
                 '    install -Dm644 man/tool.1 "${DESTDIR}/usr/share/man/tool.1"\n')
    make_tarball(sources, "dirpkg-1.0.tar.xz", {"man/tool.1": "manpage"})

    rc, out, err = run_gate(capsys, packages, sources)
    assert rc == 0, err
    assert "1 consumed path(s)" in out


def test_heredoc_payload_is_not_parsed_as_commands(tree, capsys):
    """A generated wrapper script is content, not consumption."""
    packages, sources = tree
    make_package(packages, "wrapperpkg",
                 '    install -dm755 "${DESTDIR}/usr/bin"\n'
                 '    cat > "${DESTDIR}/usr/bin/tool" <<\'WRAP\'\n'
                 '#!/bin/sh\n'
                 'install -m644 not-a-real-source /somewhere\n'
                 'WRAP\n'
                 '    chmod 755 "${DESTDIR}/usr/bin/tool"\n'
                 '    install -m644 real.conf "${DESTDIR}/etc/real.conf"\n')
    make_tarball(sources, "wrapperpkg-1.0.tar.xz", {"real.conf": "k=v"})

    rc, out, err = run_gate(capsys, packages, sources)
    assert rc == 0, err
    assert "1 consumed path(s)" in out


def test_quoted_semicolon_does_not_break_parsing(tree, capsys):
    """A `;` inside a quoted diagnostic is not a command separator."""
    packages, sources = tree
    make_package(packages, "quotepkg",
                 '    if [ ! -d "gtk-3.0" ]; then\n'
                 '        echo "quotepkg: expected gtk-3.0 at root; layout changed" >&2\n'
                 '        exit 1\n'
                 '    fi\n'
                 '    install -m644 index.theme "${DESTDIR}/usr/share/index.theme"\n')
    make_tarball(sources, "quotepkg-1.0.tar.xz",
                 {"index.theme": "t", "gtk-3.0/gtk.css": "css"})

    rc, out, err = run_gate(capsys, packages, sources)
    assert rc == 0, err


def test_guarded_optional_path_absent_is_not_a_failure(tree, capsys):
    """A copy the recipe itself guards on is optional by construction."""
    packages, sources = tree
    make_package(packages, "optpkg",
                 '    install -m644 core.conf "${DESTDIR}/etc/core.conf"\n'
                 '    if [ -d previews ]; then\n'
                 '        cp -a previews/. "${DESTDIR}/usr/share/previews/"\n'
                 '    fi\n')
    make_tarball(sources, "optpkg-1.0.tar.xz", {"core.conf": "k=v"})

    rc, out, err = run_gate(capsys, packages, sources)
    assert rc == 0, err


def test_absolute_source_is_out_of_scope(tree, capsys):
    """A path read from the build host is not a tarball member."""
    packages, sources = tree
    make_package(packages, "hostpkg",
                 '    install -m644 /mnt/intergenos/scripts/lib/igos_trace.py \\\n'
                 '        "${DESTDIR}/usr/lib/igos_trace.py"\n'
                 '    install -m644 own.conf "${DESTDIR}/etc/own.conf"\n')
    make_tarball(sources, "hostpkg-1.0.tar.xz", {"own.conf": "k=v"})

    rc, out, err = run_gate(capsys, packages, sources)
    assert rc == 0, err
    assert "1 consumed path(s)" in out


def test_literal_for_loop_values_are_checked(tree, capsys):
    packages, sources = tree
    make_package(packages, "looppkg",
                 '    for theme in Alpha Beta ; do\n'
                 '        cp -a "${theme}" "${DESTDIR}/usr/share/themes/"\n'
                 '    done\n')
    make_tarball(sources, "looppkg-1.0.tar.xz",
                 {"Alpha/index.theme": "a", "Beta/index.theme": "b"})

    rc, out, err = run_gate(capsys, packages, sources)
    assert rc == 0, err


# ---------------------------------------------------------------------------
# a member removed from a tarball fails BY NAME
# ---------------------------------------------------------------------------

def test_missing_member_fails_by_name(tree, capsys):
    """The 2026-07-30 defect: the recipe installs a file the generator stopped
    staging. The package cannot build; the gate must name the exact path."""
    packages, sources = tree
    make_package(packages, "welcomer",
                 '    install -m755 app.py "${DESTDIR}/usr/bin/app.py"\n'
                 '    install -m644 org.intergenos.Wiki.svg \\\n'
                 '        "${DESTDIR}/usr/share/icons/org.intergenos.Wiki.svg"\n')
    make_tarball(sources, "welcomer-1.0.tar.xz", {"app.py": "x"})

    rc, out, err = run_gate(capsys, packages, sources)
    assert rc == 1
    assert "HALT" in err
    assert "org.intergenos.Wiki.svg" in err
    assert "welcomer" in err
    # the path that IS present must not be reported
    assert "app.py" not in err


def test_missing_member_inside_loop_fails_by_name(tree, capsys):
    packages, sources = tree
    make_package(packages, "looppkg",
                 '    for uuid in ext-a ext-b ; do\n'
                 '        cp -a "${uuid}" "${DESTDIR}/usr/share/extensions/"\n'
                 '    done\n')
    make_tarball(sources, "looppkg-1.0.tar.xz", {"ext-a/metadata.json": "{}"})

    rc, out, err = run_gate(capsys, packages, sources)
    assert rc == 1
    assert "ext-b" in err


def test_missing_tarball_is_reported_and_fails(tree, capsys):
    """The builder hard-fails on an absent source (igos-build/builder.py:521).
    The gate reaches the same verdict earlier, and says why."""
    packages, sources = tree
    make_package(packages, "nopkg",
                 '    install -m644 a.conf "${DESTDIR}/etc/a.conf"\n')
    # no tarball generated

    rc, out, err = run_gate(capsys, packages, sources)
    assert rc == 1
    assert "COULD NOT DETERMINE" in err
    assert "nopkg-1.0.tar.xz" in err


# ---------------------------------------------------------------------------
# an unparseable install script fails as "could not determine"
# ---------------------------------------------------------------------------

def test_unrecognised_command_is_could_not_determine(tree, capsys):
    packages, sources = tree
    make_package(packages, "oddpkg",
                 '    install -m644 a.conf "${DESTDIR}/etc/a.conf"\n'
                 '    frobnicate --everything ./mystery\n')
    make_tarball(sources, "oddpkg-1.0.tar.xz", {"a.conf": "k=v"})

    rc, out, err = run_gate(capsys, packages, sources)
    assert rc == 1
    assert "COULD NOT DETERMINE" in err
    assert "frobnicate" in err


def test_sourced_helper_is_could_not_determine(tree, capsys):
    """The cut names helpers explicitly: an install step that sources another
    file has a consumed set this gate cannot see, so it must not claim to."""
    packages, sources = tree
    make_package(packages, "helperpkg",
                 '    source ./helpers/install-lib.sh\n'
                 '    install_everything\n')
    make_tarball(sources, "helperpkg-1.0.tar.xz",
                 {"helpers/install-lib.sh": "true"})

    rc, out, err = run_gate(capsys, packages, sources)
    assert rc == 1
    assert "COULD NOT DETERMINE" in err
    assert "sources a helper" in err


def test_unresolvable_variable_is_could_not_determine(tree, capsys):
    packages, sources = tree
    make_package(packages, "varpkg",
                 '    install -m644 "${MYSTERY_PATH}" "${DESTDIR}/etc/a.conf"\n')
    make_tarball(sources, "varpkg-1.0.tar.xz", {"a.conf": "k=v"})

    rc, out, err = run_gate(capsys, packages, sources)
    assert rc == 1
    assert "COULD NOT DETERMINE" in err


def test_missing_install_func_is_could_not_determine(tree, capsys):
    packages, sources = tree
    pkg_dir = make_package(packages, "emptypkg", '    :\n')
    (pkg_dir / "build.sh").write_text("#!/bin/bash\nbuild() { :; }\n",
                                      encoding="utf-8")
    make_tarball(sources, "emptypkg-1.0.tar.xz", {"a.conf": "k=v"})

    rc, out, err = run_gate(capsys, packages, sources)
    assert rc == 1
    assert "COULD NOT DETERMINE" in err
    assert "do_install" in err


def test_undeterminable_package_does_not_mask_a_clean_one(tree, capsys):
    """One unparseable recipe must not stop the others being checked."""
    packages, sources = tree
    make_package(packages, "cleanpkg",
                 '    install -m644 a.conf "${DESTDIR}/etc/a.conf"\n')
    make_tarball(sources, "cleanpkg-1.0.tar.xz", {"a.conf": "k=v"})
    make_package(packages, "oddpkg", '    frobnicate ./mystery\n')
    make_tarball(sources, "oddpkg-1.0.tar.xz", {"a.conf": "k=v"})

    rc, out, err = run_gate(capsys, packages, sources)
    assert rc == 1
    assert "1 clean" in err
    assert "oddpkg" in err


# ---------------------------------------------------------------------------
# the gate reports its own runtime
# ---------------------------------------------------------------------------

def test_runtime_is_reported_on_pass(tree, capsys):
    packages, sources = tree
    make_package(packages, "goodpkg",
                 '    install -m644 a.conf "${DESTDIR}/etc/a.conf"\n')
    make_tarball(sources, "goodpkg-1.0.tar.xz", {"a.conf": "k=v"})

    rc, out, err = run_gate(capsys, packages, sources)
    assert rc == 0, err
    assert "s)" in out and "(" in out  # e.g. "(0.01s)"
    import re
    assert re.search(r"\(\d+\.\d+s\)", out), out


def test_runtime_is_reported_on_halt(tree, capsys):
    packages, sources = tree
    make_package(packages, "badpkg",
                 '    install -m644 gone.conf "${DESTDIR}/etc/gone.conf"\n')
    make_tarball(sources, "badpkg-1.0.tar.xz", {"other.conf": "k=v"})

    rc, out, err = run_gate(capsys, packages, sources)
    assert rc == 1
    import re
    assert re.search(r"\(\d+\.\d+s\)", err), err


# ---------------------------------------------------------------------------
# setup errors are distinct from findings
# ---------------------------------------------------------------------------

def test_no_generated_packages_is_a_setup_error(tree, capsys):
    """A gate that would check nothing says so rather than passing."""
    packages, sources = tree
    (packages / "desktop" / "plainpkg").mkdir(parents=True)
    (packages / "desktop" / "plainpkg" / "package.yml").write_text(
        "name: plainpkg\nversion: \"1.0\"\nsource:\n"
        "- url: https://example.invalid/plainpkg-1.0.tar.xz\n"
        "  sha256: " + "0" * 64 + "\n", encoding="utf-8")

    rc, out, err = run_gate(capsys, packages, sources)
    assert rc == 2
    assert "SETUP ERROR" in err


def test_missing_sources_dir_is_a_setup_error(tmp_path, capsys):
    packages = tmp_path / "packages"
    packages.mkdir()
    rc, out, err = run_gate(capsys, packages, tmp_path / "absent")
    assert rc == 2
    assert "SETUP ERROR" in err


# ---------------------------------------------------------------------------
# the substring pre-filter must not lose a package
# ---------------------------------------------------------------------------

def test_prefilter_keeps_unusually_formatted_declarations(tree, capsys):
    """Enumeration skips the YAML parse for recipes that never mention
    "generated". A recipe that declares it with different spacing, quoting or
    ordering must still be found and checked."""
    packages, sources = tree
    pkg_dir = packages / "desktop" / "oddyaml"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "package.yml").write_text(
        'name: oddyaml\n'
        'version: "1.0"\n'
        'source:\n'
        '- generated:   true\n'          # extra spaces, declared before url
        '  url: file:///oddyaml-${version}.tar.xz\n',
        encoding="utf-8")
    (pkg_dir / "build.sh").write_text(
        '#!/bin/bash\n'
        'do_install() {\n'
        '    install -m644 gone.conf "${DESTDIR}/etc/gone.conf"\n'
        '}\n', encoding="utf-8")
    make_tarball(sources, "oddyaml-1.0.tar.xz", {"other.conf": "k=v"})

    rc, out, err = run_gate(capsys, packages, sources)
    assert rc == 1
    assert "oddyaml" in err
    assert "gone.conf" in err


def test_prefilter_skips_recipes_that_never_mention_generated(tree, capsys):
    """A pinned recipe alongside a generated one is not checked and not
    reported — it has no generated tarball to be a member of."""
    packages, sources = tree
    make_package(packages, "genpkg",
                 '    install -m644 a.conf "${DESTDIR}/etc/a.conf"\n')
    make_tarball(sources, "genpkg-1.0.tar.xz", {"a.conf": "k=v"})
    plain = packages / "desktop" / "pinnedpkg"
    plain.mkdir(parents=True)
    (plain / "package.yml").write_text(
        'name: pinnedpkg\nversion: "2.0"\nsource:\n'
        '- url: https://example.invalid/pinnedpkg-2.0.tar.xz\n'
        '  sha256: ' + "0" * 64 + '\n', encoding="utf-8")

    rc, out, err = run_gate(capsys, packages, sources)
    assert rc == 0, err
    assert "1 generated package(s)" in out
    assert "pinnedpkg" not in out


# ---------------------------------------------------------------------------
# release-staged sources: "the generator produced nothing" is a different,
# declared state from "the recipe could not be read"
#
# The shape under test is intergenos-wiki's: its tarball is generated from a
# rendered book staged into build/wiki-book at release time, so every by-hand
# firing of this gate found the tarball absent. The declaration changes exactly
# one verdict — an absent tarball — and nothing else.
# ---------------------------------------------------------------------------

STAGED = ('"build/wiki-book — staged from the wiki repository at release time; '
          'the generator SKIPs and produces no tarball without it"')


def test_declared_absent_tarball_is_its_own_state_and_not_a_halt(tree, capsys):
    """The wiki shape. Exit 0, because nothing failed and nothing was masked.

    Exit semantics: an absent generated tarball is still fatal where it
    actually matters — the builder refuses to build a package whose declared
    source is not on disk (igos-build/builder.py extract_source). This gate
    declining to halt on it cannot let such a build through, so halting here
    only produced a standing failure that readers learned to skip past. The
    verdict is reported as its own named state and counted separately, so the
    run never reads as "everything checked".
    """
    packages, sources = tree
    make_package(packages, "wikipkg",
                 '    cp -a ./book/. "${DESTDIR}/usr/share/doc/wiki/"\n',
                 release_staged_source=STAGED)
    make_package(packages, "sibling",
                 '    install -m644 a.conf "${DESTDIR}/etc/a.conf"\n')
    make_tarball(sources, "sibling-1.0.tar.xz", {"a.conf": "k=v"})
    # wikipkg's tarball is deliberately never generated.

    rc, out, err = run_gate(capsys, packages, sources)
    assert rc == 0, err
    assert "RELEASE-STAGED SOURCE ABSENT" in out
    assert "wikipkg" in out
    assert "1 NOT VERIFIED" in out
    assert "1 verified against their tarballs" in out
    # The declaration itself is quoted back, so the state can never be claimed
    # without saying which input is staged and why.
    assert "build/wiki-book" in out
    # It is NOT the could-not-determine verdict, on either stream.
    assert "COULD NOT DETERMINE" not in out
    assert "COULD NOT DETERMINE" not in err


def test_declaration_does_not_skip_a_tarball_that_is_present(tree, capsys):
    """A present input is checked in full, declaration or not."""
    packages, sources = tree
    make_package(packages, "wikipkg",
                 '    install -m644 pages.json "${DESTDIR}/usr/share/pages.json"\n'
                 '    install -m644 pages.json.asc "${DESTDIR}/usr/share/pages.json.asc"\n',
                 release_staged_source=STAGED)
    make_tarball(sources, "wikipkg-1.0.tar.xz",
                 {"pages.json": "{}", "pages.json.asc": "sig"})

    rc, out, err = run_gate(capsys, packages, sources)
    assert rc == 0, err
    assert "2 consumed path(s)" in out
    assert "NOT VERIFIED" not in out


def test_declaration_does_not_excuse_a_missing_member(tree, capsys):
    """The same package, one member short: still a halt, named by path."""
    packages, sources = tree
    make_package(packages, "wikipkg",
                 '    install -m644 pages.json "${DESTDIR}/usr/share/pages.json"\n'
                 '    install -m644 pages.json.asc "${DESTDIR}/usr/share/pages.json.asc"\n',
                 release_staged_source=STAGED)
    make_tarball(sources, "wikipkg-1.0.tar.xz", {"pages.json": "{}"})

    rc, out, err = run_gate(capsys, packages, sources)
    assert rc == 1
    assert "pages.json.asc" in err


def test_declared_absent_tarball_still_halts_on_an_unreadable_recipe(tree, capsys):
    """Only the absent input is excused. An install step this gate cannot
    parse is a halt whether or not the package declares the class — otherwise
    declaring it would buy a package an unparsed recipe until release day."""
    packages, sources = tree
    make_package(packages, "wikipkg",
                 '    frobnicate ./book "${DESTDIR}/usr/share/doc/wiki/"\n',
                 release_staged_source=STAGED)
    make_package(packages, "sibling",
                 '    install -m644 a.conf "${DESTDIR}/etc/a.conf"\n')
    make_tarball(sources, "sibling-1.0.tar.xz", {"a.conf": "k=v"})

    rc, out, err = run_gate(capsys, packages, sources)
    assert rc == 1
    assert "COULD NOT DETERMINE" in err
    assert "frobnicate" in err


def test_undeclared_absent_tarball_still_halts(tree, capsys):
    """The fail-closed default is untouched: no declaration, no excuse."""
    packages, sources = tree
    make_package(packages, "plainpkg",
                 '    install -m644 a.conf "${DESTDIR}/etc/a.conf"\n')

    rc, out, err = run_gate(capsys, packages, sources)
    assert rc == 1
    assert "COULD NOT DETERMINE" in err
    assert "the generator must run before this gate" in err


@pytest.mark.parametrize("value", ["true", '""', "'   '", "17", "[a, b]"])
def test_malformed_declaration_is_a_setup_error(tree, capsys, value):
    """Strict like the recipe parser's security booleans: a declaration that
    is not a non-empty string is a declaration error, never a soft true."""
    packages, sources = tree
    make_package(packages, "wikipkg",
                 '    install -m644 a.conf "${DESTDIR}/etc/a.conf"\n',
                 release_staged_source=value)
    make_tarball(sources, "wikipkg-1.0.tar.xz", {"a.conf": "k=v"})

    rc, out, err = run_gate(capsys, packages, sources)
    assert rc == 2
    assert "SETUP ERROR" in err
    assert "release_staged_source must be a non-empty string" in err


def test_a_run_that_verified_nothing_is_a_setup_error(tree, capsys):
    """Every generated package release-staged and absent means this run
    compared nothing against anything. The gate already refuses to report on a
    tree where no package declares a generated source at all; a run that
    checked none of them is the same statement and must not print PASS."""
    packages, sources = tree
    make_package(packages, "wikipkg",
                 '    cp -a ./book/. "${DESTDIR}/usr/share/doc/wiki/"\n',
                 release_staged_source=STAGED)

    rc, out, err = run_gate(capsys, packages, sources)
    assert rc == 2
    assert "SETUP ERROR" in err
    assert "verified nothing" in err
    assert "PASS" not in out


def test_release_staged_report_is_visible_on_the_halt_path(tree, capsys):
    """An unverified package is named when another package halts the run too —
    a halting run must not swallow the fact that something went unchecked."""
    packages, sources = tree
    make_package(packages, "wikipkg",
                 '    cp -a ./book/. "${DESTDIR}/usr/share/doc/wiki/"\n',
                 release_staged_source=STAGED)
    make_package(packages, "brokenpkg",
                 '    install -m644 gone.conf "${DESTDIR}/etc/gone.conf"\n')
    make_tarball(sources, "brokenpkg-1.0.tar.xz", {"other.conf": "k=v"})

    rc, out, err = run_gate(capsys, packages, sources)
    assert rc == 1
    assert "gone.conf" in err
    assert "RELEASE-STAGED SOURCE ABSENT" in err
    assert "1 not verified (release-staged source absent)" in err


def test_wiki_recipe_declares_the_class_and_the_parser_knows_the_key():
    """The real recipe, against the real parser: the declaration is registered
    in KNOWN_FIELDS, so a misspelled key fails the build at parse time instead
    of silently returning the package to the halting behaviour."""
    import importlib
    import sys

    repo_root = SCRIPT_PATH.parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    parser = importlib.import_module("igos-build.parser")

    assert "release_staged_source" in parser.KNOWN_FIELDS

    recipe = repo_root / "packages" / "desktop" / "intergenos-wiki" / "package.yml"
    pkg = parser.parse_template(recipe)          # unknown-key check runs here
    assert pkg.name == "intergenos-wiki"

    declared = mod.find_generated_packages(repo_root / "packages")
    wiki = [p for p in declared if p["name"] == "intergenos-wiki"]
    assert wiki, "intergenos-wiki no longer declares a generated source"
    assert "build/wiki-book" in wiki[0]["release_staged_source"]


# ---------------------------------------------------------------------------
# member stripping matches `tar --strip-components=1`
# ---------------------------------------------------------------------------

def test_leading_dot_component_is_stripped_like_tar(tmp_path):
    """`det_tar ... .` stores `./Name/file`; tar strips the `.`, leaving
    `Name/file` at the extract root."""
    stage = tmp_path / "stage"
    (stage / "Variant" / "cursors").mkdir(parents=True)
    (stage / "Variant" / "cursors" / "left_ptr").write_text("x", encoding="utf-8")
    out = tmp_path / "dotted.tar.xz"
    with tarfile.open(out, "w:xz") as tf:
        tf.add(stage, arcname=".")

    members = mod.tarball_members(out)
    assert "Variant/cursors/left_ptr" in members
    assert "Variant" in members
