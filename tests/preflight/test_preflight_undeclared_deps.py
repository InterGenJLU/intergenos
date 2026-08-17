"""Unit tests for preflight-undeclared-deps (Scan A.2)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent.parent / "scripts" / "preflight-undeclared-deps.py"

# Dynamic import (script has a hyphen in filename)
spec = importlib.util.spec_from_file_location("preflight_undeclared_deps", SCRIPT_PATH)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)


# ----------------------------------------------------------------------------
# PKG_CHECK_MODULES
# ----------------------------------------------------------------------------

def test_pkg_check_modules_2arg_required():
    src = "PKG_CHECK_MODULES([LUA], [lua >= 5.2])"
    fs = mod.parse_pkg_check_modules(src)
    assert len(fs) == 1
    assert fs[0]["deps"] == ["lua"]
    assert fs[0]["required"] is True


def test_pkg_check_modules_4arg_with_error_required():
    src = """PKG_CHECK_MODULES([LUA],
        [lua >= 5.2],
        [],
        [AC_MSG_ERROR([lua not present or too old])])"""
    fs = mod.parse_pkg_check_modules(src)
    assert len(fs) == 1
    assert fs[0]["deps"] == ["lua"]
    assert fs[0]["required"] is True


def test_pkg_check_modules_4arg_with_fallback_soft():
    src = """PKG_CHECK_MODULES([SQLITE],
        [sqlite3 >= 3.7.17],
        [enable_sqlite=yes],
        [enable_sqlite=no])"""
    fs = mod.parse_pkg_check_modules(src)
    assert len(fs) == 1
    assert fs[0]["deps"] == ["sqlite3"]
    assert fs[0]["required"] is False


def test_pkg_check_modules_multi_deps():
    src = "PKG_CHECK_MODULES([X], [foo >= 1 bar baz >= 3.0])"
    fs = mod.parse_pkg_check_modules(src)
    assert len(fs) == 1
    assert fs[0]["deps"] == ["foo", "bar", "baz"]


def test_pkg_check_modules_rpm_lua_real_case():
    """The exact pattern that halted Build #9."""
    src = """PKG_CHECK_MODULES([LUA],
        [lua >= 5.2],
        [],
        [AC_MSG_ERROR([lua not present or too old)])])"""
    fs = mod.parse_pkg_check_modules(src)
    assert len(fs) == 1
    assert fs[0]["deps"] == ["lua"]
    assert fs[0]["required"] is True


# ----------------------------------------------------------------------------
# AC_CHECK_LIB
# ----------------------------------------------------------------------------

def test_ac_check_lib_default_soft():
    """Bare 2-arg AC_CHECK_LIB sets HAVE_LIB* but doesn't halt — SOFT."""
    src = "AC_CHECK_LIB([z], [deflate])"
    fs = mod.parse_ac_check_lib(src)
    assert len(fs) == 1
    assert fs[0]["deps"] == ["z"]
    assert fs[0]["required"] is False


def test_ac_check_lib_with_error_action_hard():
    src = "AC_CHECK_LIB([crypto], [EVP_DigestInit_ex], [], [AC_MSG_ERROR([libcrypto missing])])"
    fs = mod.parse_ac_check_lib(src)
    assert len(fs) == 1
    assert fs[0]["deps"] == ["crypto"]
    assert fs[0]["required"] is True


def test_ac_check_lib_3arg_soft():
    """3-arg form (action-if-found provided) defaults to SOFT on failure."""
    src = "AC_CHECK_LIB([cap], [cap_get_file], [LIBS=-lcap])"
    fs = mod.parse_ac_check_lib(src)
    assert len(fs) == 1
    assert fs[0]["required"] is False


# ----------------------------------------------------------------------------
# AC_CHECK_HEADERS
# ----------------------------------------------------------------------------

def test_ac_check_headers_default_soft():
    src = "AC_CHECK_HEADERS([zlib.h])"
    fs = mod.parse_ac_check_headers(src)
    assert len(fs) == 1
    assert fs[0]["deps"] == ["zlib.h"]
    assert fs[0]["required"] is False


def test_ac_check_headers_with_error_action_hard():
    src = "AC_CHECK_HEADERS([openssl/evp.h], [], [AC_MSG_ERROR([missing OpenSSL header])])"
    fs = mod.parse_ac_check_headers(src)
    assert len(fs) == 1
    assert fs[0]["deps"] == ["openssl/evp.h"]
    assert fs[0]["required"] is True


def test_ac_check_headers_multi():
    src = "AC_CHECK_HEADERS([fcntl.h sys/time.h unistd.h])"
    fs = mod.parse_ac_check_headers(src)
    assert len(fs) == 3
    assert [f["deps"][0] for f in fs] == ["fcntl.h", "sys/time.h", "unistd.h"]


# ----------------------------------------------------------------------------
# meson dependency()
# ----------------------------------------------------------------------------

def test_meson_dependency_default_required():
    src = "foo = dependency('foo')"
    fs = mod.parse_meson_dependency(src)
    assert len(fs) == 1
    assert fs[0]["deps"] == ["foo"]
    assert fs[0]["required"] is True


def test_meson_dependency_explicit_required_true():
    src = "foo = dependency('foo', required: true)"
    fs = mod.parse_meson_dependency(src)
    assert len(fs) == 1
    assert fs[0]["required"] is True


def test_meson_dependency_explicit_required_false():
    src = "foo = dependency('foo', required: false)"
    fs = mod.parse_meson_dependency(src)
    assert len(fs) == 1
    assert fs[0]["required"] is False


def test_meson_dependency_feature_conditional_soft():
    """Conditional ``required: feature_var`` must classify as SOFT."""
    src = "libnsl = dependency('libnsl', required: feature_nis)"
    fs = mod.parse_meson_dependency(src)
    assert len(fs) == 1
    assert fs[0]["required"] is False


def test_meson_dependency_get_option_conditional_soft():
    src = "foo = dependency('foo', required: get_option('with_foo'))"
    fs = mod.parse_meson_dependency(src)
    assert len(fs) == 1
    assert fs[0]["required"] is False


# ----------------------------------------------------------------------------
# meson find_program()
# ----------------------------------------------------------------------------

def test_meson_find_program_default_required():
    src = "p = find_program('xmllint')"
    fs = mod.parse_meson_find_program(src)
    assert len(fs) == 1
    assert fs[0]["deps"] == ["xmllint"]
    assert fs[0]["required"] is True


def test_meson_find_program_required_false():
    src = "p = find_program('w3m', required: false)"
    fs = mod.parse_meson_find_program(src)
    assert len(fs) == 1
    assert fs[0]["required"] is False


def test_meson_find_program_feature_conditional():
    src = "p = find_program('xmllint', required: feature_docs)"
    fs = mod.parse_meson_find_program(src)
    assert len(fs) == 1
    assert fs[0]["required"] is False


# ----------------------------------------------------------------------------
# cmake find_package()
# ----------------------------------------------------------------------------

def test_cmake_find_package_required():
    src = "find_package(OpenSSL REQUIRED)"
    fs = mod.parse_cmake_find_package(src)
    assert len(fs) == 1
    assert fs[0]["deps"] == ["OpenSSL"]
    assert fs[0]["required"] is True


def test_cmake_find_package_without_required():
    src = "find_package(Qt5)"
    fs = mod.parse_cmake_find_package(src)
    assert len(fs) == 1
    assert fs[0]["deps"] == ["Qt5"]
    assert fs[0]["required"] is False


def test_cmake_find_package_with_version_required():
    src = "find_package(Boost 1.75 REQUIRED COMPONENTS system filesystem)"
    fs = mod.parse_cmake_find_package(src)
    assert len(fs) == 1
    assert fs[0]["deps"] == ["Boost"]
    assert fs[0]["required"] is True


# ----------------------------------------------------------------------------
# Alias resolution
# ----------------------------------------------------------------------------

def test_resolve_pkg_config_direct_alias():
    aliases = {"pkg-config": {"glib-2.0": "glib2"}}
    known = {"glib2", "lua"}
    assert mod.resolve_pkg_config_name("glib-2.0", aliases, known) == "glib2"


def test_resolve_pkg_config_known_name():
    aliases = {"pkg-config": {}}
    known = {"lua", "openssl"}
    assert mod.resolve_pkg_config_name("lua", aliases, known) == "lua"


def test_resolve_pkg_config_strip_version_suffix():
    aliases = {"pkg-config": {}}
    known = {"glib", "gtk"}
    # glib-2.0 → strip version → glib (heuristic)
    assert mod.resolve_pkg_config_name("glib-2.0", aliases, known) == "glib"


def test_resolve_pkg_config_unresolved_returns_none():
    aliases = {"pkg-config": {}}
    known = {"lua"}
    assert mod.resolve_pkg_config_name("nonexistent-pkg", aliases, known) is None


def test_resolve_pkg_config_strips_version_qualifier():
    """Input like 'lua >= 5.2' from PKG_CHECK_MODULES split."""
    aliases = {"pkg-config": {}}
    known = {"lua"}
    assert mod.resolve_pkg_config_name("lua", aliases, known) == "lua"


def test_resolve_program_via_alias():
    aliases = {"program": {"xmllint": "libxml2"}}
    known = {"libxml2"}
    assert mod.resolve_program_name("xmllint", aliases, known) == "libxml2"


def test_resolve_library_glibc_alias():
    aliases = {"library": {"z": "zlib", "pthread": "glibc"}}
    known = {"zlib", "glibc"}
    assert mod.resolve_library_name("z", aliases, known) == "zlib"
    assert mod.resolve_library_name("pthread", aliases, known) == "glibc"


def test_resolve_header_via_alias():
    aliases = {"header": {"lua.h": "lua"}}
    assert mod.resolve_header_name("lua.h", aliases) == "lua"
    assert mod.resolve_header_name("nonexistent.h", aliases) is None


# ----------------------------------------------------------------------------
# Argument splitting (m4 bracket-aware)
# ----------------------------------------------------------------------------

def test_split_top_level_args_simple():
    args = mod.split_top_level_args("foo, bar, baz")
    assert args == ["foo", "bar", "baz"]


def test_split_top_level_args_with_brackets():
    args = mod.split_top_level_args("[NAME], [pkg >= 1.0, other]")
    # commas inside [...] don't split
    assert args == ["[NAME]", "[pkg >= 1.0, other]"]


def test_split_top_level_args_nested_parens():
    args = mod.split_top_level_args("foo, AC_MSG_ERROR([oops, oops]), bar")
    assert args == ["foo", "AC_MSG_ERROR([oops, oops])", "bar"]


def test_strip_m4_brackets():
    assert mod.strip_m4_brackets("[hello]") == "hello"
    assert mod.strip_m4_brackets("[[nested]]") == "nested"
    assert mod.strip_m4_brackets("nobrackets") == "nobrackets"


# ----------------------------------------------------------------------------
# Macro call finder (parens-balanced)
# ----------------------------------------------------------------------------

def test_find_macro_calls_basic():
    src = "PKG_CHECK_MODULES([X], [foo])"
    calls = mod.find_macro_calls(src, "PKG_CHECK_MODULES")
    assert len(calls) == 1
    assert calls[0][1] == "[X], [foo]"


def test_find_macro_calls_skips_substring_in_other_macro():
    """``MYPKG_CHECK_MODULES(...)`` is NOT a PKG_CHECK_MODULES call."""
    src = "MYPKG_CHECK_MODULES([X], [foo])"
    calls = mod.find_macro_calls(src, "PKG_CHECK_MODULES")
    assert calls == []


def test_find_macro_calls_nested_parens():
    src = "AC_CHECK_LIB(crypto, EVP_DigestInit_ex, [], [AC_MSG_ERROR([fail])])"
    calls = mod.find_macro_calls(src, "AC_CHECK_LIB")
    assert len(calls) == 1
    # The arg string captures everything inside the outer parens
    assert "AC_MSG_ERROR([fail])" in calls[0][1]


# ----------------------------------------------------------------------------
# package.yml parser
# ----------------------------------------------------------------------------

def test_parse_pkg_yml_basic(tmp_path):
    yml = tmp_path / "package.yml"
    yml.write_text("""name: foo
version: "1.2.3"
source:
  - url: https://example.com/foo-${version}.tar.gz
    sha256: deadbeef
dependencies:
  build:
    - bar
    - baz
""")
    info = mod.parse_pkg_yml(yml)
    assert info["name"] == "foo"
    assert info["version"] == "1.2.3"
    assert info["dependencies"]["build"] == ["bar", "baz"]
    assert info["source"][0]["url"] == "https://example.com/foo-${version}.tar.gz"


def test_parse_pkg_yml_empty_deps(tmp_path):
    yml = tmp_path / "package.yml"
    yml.write_text("""name: leaf
version: "1.0"
dependencies:
  build: []
""")
    info = mod.parse_pkg_yml(yml)
    assert info["dependencies"]["build"] == []


def test_parse_pkg_yml_missing_file(tmp_path):
    info = mod.parse_pkg_yml(tmp_path / "nope.yml")
    assert info["dependencies"]["build"] == []


def test_parse_pkg_yml_flush_left_source_list(tmp_path):
    """Regression: YAML allows `- url:` at indent 0 as a child of `source:`.
    The flush-left style is used by ~600 of 726 package.yml files in tree.
    Prior parser bug treated indent-0 `- ` lines as state-reset, dropping
    the source URL and producing 87% SOURCE-NOT-FOUND in full-tree scan.
    """
    yml = tmp_path / "package.yml"
    yml.write_text("""name: Mako
version: "1.3.10"
tier: desktop
source:
- url: https://files.pythonhosted.org/packages/source/M/Mako/mako-${version}.tar.gz
  sha256: deadbeef
dependencies:
  build:
  - docbook-xsl-nons
""")
    info = mod.parse_pkg_yml(yml)
    assert info["name"] == "Mako"
    assert info["version"] == "1.3.10"
    assert len(info["source"]) == 1
    assert "mako-${version}.tar.gz" in info["source"][0]["url"]
    assert info["source"][0]["sha256"] == "deadbeef"
    assert info["dependencies"]["build"] == ["docbook-xsl-nons"]


def test_parse_pkg_yml_indented_source_list(tmp_path):
    """Sibling: indented (2-space) source list works too — preserve both
    styles per YAML spec."""
    yml = tmp_path / "package.yml"
    yml.write_text("""name: indented
version: "1.0"
source:
  - url: https://example.com/indented-${version}.tar.gz
    sha256: abc123
dependencies:
  build: []
""")
    info = mod.parse_pkg_yml(yml)
    assert info["source"][0]["sha256"] == "abc123"


# ----------------------------------------------------------------------------
# Exit-code policy via HARD_TYPES constant
# ----------------------------------------------------------------------------

def test_hard_types_includes_pkg_config_required():
    assert "UNDECLARED-PKG-CONFIG-REQUIRED" in mod.HARD_TYPES


def test_hard_types_includes_meson_required():
    assert "UNDECLARED-MESON-DEP-REQUIRED" in mod.HARD_TYPES
    assert "UNDECLARED-MESON-PROGRAM" in mod.HARD_TYPES


def test_hard_types_excludes_autotools_lib_header():
    """v1 keeps AC_CHECK_LIB/HEADERS at INFO due to AS_IF false-positive risk."""
    assert "UNDECLARED-LIB-REQUIRED" not in mod.HARD_TYPES
    assert "UNDECLARED-HEADER-REQUIRED" not in mod.HARD_TYPES


def test_hard_types_includes_cmake_required():
    assert "UNDECLARED-CMAKE-DEP-REQUIRED" in mod.HARD_TYPES


# ----------------------------------------------------------------------------
# Conditional-block tracking (Limit A + B)
# ----------------------------------------------------------------------------

def test_is_inside_conditional_shell_simple_if():
    src = """foo
if test "x$flag" = xyes; then
    PKG_CHECK_MODULES(LUA, [lua])
fi
bar"""
    # PKG_CHECK is at line 3, inside an if-block
    assert mod.is_inside_conditional(src, 3, is_meson=False) is True


def test_is_inside_conditional_shell_outside_if():
    src = """foo
if test "x$flag" = xyes; then
    PKG_CHECK_MODULES(LUA, [lua])
fi
PKG_CHECK_MODULES(BAR, [bar])
"""
    # First PKG_CHECK at line 3 inside; second at line 5 outside (after fi)
    assert mod.is_inside_conditional(src, 3, is_meson=False) is True
    assert mod.is_inside_conditional(src, 5, is_meson=False) is False


def test_is_inside_conditional_shell_nested_if():
    src = """foo
if test "x$a" = xyes; then
    if test "x$b" = xyes; then
        PKG_CHECK_MODULES(NESTED, [nested])
    fi
fi
"""
    assert mod.is_inside_conditional(src, 4, is_meson=False) is True


def test_is_inside_conditional_meson_if():
    src = """project('x')
if backend == 'nettle'
  nettle = dependency('nettle')
endif
"""
    assert mod.is_inside_conditional(src, 3, is_meson=True) is True


def test_is_inside_conditional_meson_outside():
    src = """project('x')
foo = dependency('foo')
if cond
  bar = dependency('bar')
endif
"""
    assert mod.is_inside_conditional(src, 2, is_meson=True) is False
    assert mod.is_inside_conditional(src, 4, is_meson=True) is True


# ----------------------------------------------------------------------------
# Build-system detection (Limit C)
# ----------------------------------------------------------------------------

def test_detect_build_systems_explicit_autotools(tmp_path):
    build_sh = tmp_path / "build.sh"
    build_sh.write_text("#!/bin/bash\n./configure --prefix=/usr\nmake\n")
    info = {"build_style": "autotools"}
    assert mod.detect_build_systems(build_sh, info) == {"autotools"}


def test_detect_build_systems_explicit_meson(tmp_path):
    build_sh = tmp_path / "build.sh"
    build_sh.write_text("#!/bin/bash\nmeson setup build\n")
    info = {"build_style": "meson"}
    assert mod.detect_build_systems(build_sh, info) == {"meson"}


def test_detect_build_systems_custom_with_autotools(tmp_path):
    """Custom build_style: detect from build.sh invocation."""
    build_sh = tmp_path / "build.sh"
    build_sh.write_text("#!/bin/bash\nconfigure() {\n  ./configure --prefix=/usr\n}\n")
    info = {"build_style": "custom"}
    assert mod.detect_build_systems(build_sh, info) == {"autotools"}


def test_detect_build_systems_custom_with_meson(tmp_path):
    build_sh = tmp_path / "build.sh"
    build_sh.write_text("#!/bin/bash\nconfigure() {\n  meson setup build\n}\n")
    info = {"build_style": "custom"}
    assert mod.detect_build_systems(build_sh, info) == {"meson"}


def test_detect_build_systems_git_uses_autotools_not_meson(tmp_path):
    """Regression: git ships both configure.ac and meson.build; our build.sh
    uses ./configure. Scanner must NOT read meson.build for git."""
    build_sh = tmp_path / "build.sh"
    build_sh.write_text("""#!/bin/bash
configure() {
    set -e
    ./configure --prefix=/usr \\
                --with-gitconfig=/etc/gitconfig \\
                --with-libpcre2
}
""")
    info = {"build_style": "custom"}
    assert mod.detect_build_systems(build_sh, info) == {"autotools"}


# ----------------------------------------------------------------------------
# Multi-provider alias (Limit D)
# ----------------------------------------------------------------------------

def test_resolve_pkg_config_returns_list_for_multi_provider():
    aliases = {"pkg-config": {"glib-2.0": ["glib2", "glib2-bootstrap"]}}
    known = {"glib2", "glib2-bootstrap"}
    result = mod.resolve_pkg_config_name("glib-2.0", aliases, known)
    assert result == ["glib2", "glib2-bootstrap"]


# ----------------------------------------------------------------------------
# Comment stripping
# ----------------------------------------------------------------------------

def test_strip_comments_meson_hash():
    """meson `#` comment must be stripped — `dependency()` inside a comment
    should not match. Regression: gobject-introspection-pass1 meson.build:356
    had `# ... dependency('gobject-introspection-1.0', ...)` in a comment."""
    src = """foo = dependency('foo')
# dependency('gobject-introspection-1.0', fallback: ...)
bar = dependency('bar')"""
    stripped = mod._strip_comments(src, is_meson=True)
    # Line numbers must be preserved
    assert stripped.count("\n") == src.count("\n")
    deps = mod.parse_meson_dependency(stripped)
    names = [f["deps"][0] for f in deps]
    assert "foo" in names
    assert "bar" in names
    assert "gobject-introspection-1.0" not in names


def test_strip_comments_autotools_dnl():
    """autotools `dnl` comments must be stripped."""
    src = """PKG_CHECK_MODULES([REAL], [real])
dnl PKG_CHECK_MODULES([FAKE], [fake-in-comment])
PKG_CHECK_MODULES([OTHER], [other])"""
    stripped = mod._strip_comments(src, is_meson=False)
    fs = mod.parse_pkg_check_modules(stripped)
    names = [d for f in fs for d in f["deps"]]
    assert "real" in names
    assert "other" in names
    assert "fake-in-comment" not in names


def test_strip_comments_preserves_quoted_hash():
    """A `#` inside a single-quoted string shouldn't be treated as comment."""
    src = "name = 'foo#bar'  # actual comment"
    stripped = mod._strip_comments(src, is_meson=True)
    # `foo#bar` preserved; trailing comment whitespace
    assert "foo#bar" in stripped
    assert "actual comment" not in stripped


# ----------------------------------------------------------------------------
# AS_IF / AS_CASE detection (autotools m4 conditionals)
# ----------------------------------------------------------------------------

def test_find_conditional_macro_ranges_as_if():
    src = """AC_INIT([foo], [1.0])
AS_IF([test "${enable_libproxy}" = "yes"], [
    PKG_CHECK_MODULES([LIBPROXY], [libproxy-1.0])
])
PKG_CHECK_MODULES([REAL], [real])"""
    ranges = mod.find_conditional_macro_ranges(src)
    assert len(ranges) == 1
    start, end = ranges[0]
    # AS_IF starts at line 2, body spans through line 4 (the `]`)
    assert start == 2
    assert end >= 3  # includes line where PKG_CHECK is


def test_is_inside_conditional_as_if_wraps_macro():
    """Regression: wget's libproxy PKG_CHECK_MODULES is inside
    AS_IF([test "${enable_libproxy}" = "yes"], [...]) — must be detected as
    conditional (not HARD)."""
    src = """AC_INIT([wget], [1.25.0])
AS_IF([test "${enable_libproxy}" = "yes"], [
    PKG_CHECK_MODULES([LIBPROXY], [libproxy-1.0])
])"""
    m4_ranges = mod.find_conditional_macro_ranges(src)
    # The PKG_CHECK_MODULES line is inside the AS_IF body
    assert mod.is_inside_conditional(src, 3, is_meson=False, m4_ranges=m4_ranges) is True
    # AC_INIT at line 1 is not
    assert mod.is_inside_conditional(src, 1, is_meson=False, m4_ranges=m4_ranges) is False


# ----------------------------------------------------------------------------
# Summary output formatting (RC001 tooling: FAIL-paragraph wrap + INFO collapse)
# ----------------------------------------------------------------------------

def _finding(ftype, i=0):
    return {"type": ftype, "consumer": f"pkg{i}", "dep": "somedep",
            "dep_raw": "somedep", "kind": "pkg-config",
            "file": "configure.ac", "line": 100 + i}


def _mixed_findings():
    """3 HARD across 2 categories + 62 INFO across 5 categories."""
    out = []
    out += [_finding("UNDECLARED-PKG-CONFIG-REQUIRED", i) for i in range(2)]
    out += [_finding("UNDECLARED-MESON-DEP-REQUIRED", i) for i in range(1)]
    out += [_finding("UNDECLARED-PKG-CONFIG-SOFT", i) for i in range(5)]
    out += [_finding("UNDECLARED-MESON-DEP-SOFT", i) for i in range(3)]
    out += [_finding("UNRESOLVED-PKG-NAME", i) for i in range(12)]
    out += [_finding("SOURCE-NOT-FOUND", i) for i in range(40)]
    out += [_finding("SOURCE-EXTRACT-FAILED", i) for i in range(2)]
    return out


def test_summary_all_stdout_lines_within_output_width(capsys):
    """No summary line exceeds _OUTPUT_WIDTH (the FAIL paragraph is wrapped;
    the INFO summary is phrased to fit)."""
    mod.emit_summary(_mixed_findings(), verbose=False)
    out = capsys.readouterr().out
    over = [ln for ln in out.splitlines() if len(ln) > mod._OUTPUT_WIDTH]
    assert not over, f"lines over {mod._OUTPUT_WIDTH}: {over}"


def test_summary_fail_paragraph_is_wrapped(capsys):
    """The resolution guidance renders as multiple wrapped lines, not one
    long unwrapped line, and still leads with the FAIL marker."""
    mod.emit_summary(_mixed_findings(), verbose=False)
    out = capsys.readouterr().out
    assert "FAIL — undeclared HARD build-deps detected." in out
    # The guidance keywords land on more than one physical line (wrapped).
    resolve_block = out.split("FAIL —", 1)[1]
    resolve_lines = [ln for ln in resolve_block.splitlines() if ln.strip()]
    assert len(resolve_lines) >= 3
    assert all(len(ln) <= mod._OUTPUT_WIDTH for ln in resolve_lines)


def test_summary_info_categories_collapse_to_one_line(capsys):
    """The per-category INFO wall collapses to a single summary line while
    each HARD category stays itemized."""
    mod.emit_summary(_mixed_findings(), verbose=False)
    out = capsys.readouterr().out
    info_lines = [ln for ln in out.splitlines() if ln.strip().startswith("[INFO]")]
    assert len(info_lines) == 1
    # 62 INFO findings across 5 categories, reported in one line.
    assert "62 findings in 5 categories" in info_lines[0]
    # No per-category INFO line leaked through.
    for cat in ("SOURCE-NOT-FOUND", "UNRESOLVED-PKG-NAME",
                "UNDECLARED-PKG-CONFIG-SOFT"):
        assert f"[INFO] {cat}" not in out
    # HARD categories remain individually itemized.
    hard_lines = [ln for ln in out.splitlines() if ln.strip().startswith("[HARD]")]
    assert len(hard_lines) == 2


def test_summary_no_info_line_when_all_hard(capsys):
    mod.emit_summary([_finding("UNDECLARED-PKG-CONFIG-REQUIRED")], verbose=False)
    out = capsys.readouterr().out
    assert "[INFO]" not in out
    assert "[HARD] UNDECLARED-PKG-CONFIG-REQUIRED: 1" in out


def test_report_artifact_carries_per_category_summary(tmp_path):
    """The --report JSON gains a `summary.by_type` block so the collapsed
    INFO detail has a home (the 'route to --report' side of the change)."""
    (tmp_path / "build").mkdir()
    json_path, _tsv = mod.write_artifacts(tmp_path, _mixed_findings(),
                                          "20260710T000000Z")
    import json as _json
    data = _json.loads(json_path.read_text())
    assert data["summary"]["total"] == 65
    assert data["summary"]["hard"] == 3
    assert data["summary"]["by_type"]["SOURCE-NOT-FOUND"] == 40
    assert data["summary"]["by_type"]["UNDECLARED-PKG-CONFIG-REQUIRED"] == 2
    # Raw findings still present (unchanged).
    assert len(data["findings"]) == 65


# ----------------------------------------------------------------------------
# Gate-mode coverage (fail-closed source inspection)
# ----------------------------------------------------------------------------

def _fixture_repo(tmp_path: Path, source_block: str) -> Path:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    pkg = repo / "packages" / "core" / "foo"
    pkg.mkdir(parents=True)
    (pkg / "package.yml").write_text(
        "name: foo\nversion: \"1.0\"\ntier: core\n"
        + source_block
        + "dependencies:\n  build: []\n")
    (repo / "build" / "sources").mkdir(parents=True)
    (repo / "build" / "scan-cache").mkdir(parents=True)
    return repo


def _scan_fixture(repo: Path, only=None):
    return mod.scan(repo, repo / "build" / "sources",
                    repo / "build" / "scan-cache", {}, only=only)


def test_no_source_declared_is_by_design_advisory(tmp_path):
    repo = _fixture_repo(tmp_path, "source: []\n")
    findings = _scan_fixture(repo, only=["foo"])
    assert [f["type"] for f in findings] == ["NO-SOURCE-DECLARED"]
    assert "NO-SOURCE-DECLARED" not in mod.COVERAGE_TYPES


def test_declared_source_absent_is_coverage_class(tmp_path):
    repo = _fixture_repo(
        tmp_path,
        "source:\n  - url: https://example.org/foo-1.0.tar.gz\n"
        "    sha256: " + "0" * 64 + "\n")
    findings = _scan_fixture(repo, only=["foo"])
    assert [f["type"] for f in findings] == ["SOURCE-NOT-FOUND"]
    assert "SOURCE-NOT-FOUND" in mod.COVERAGE_TYPES


def test_non_archive_source_is_by_design_advisory(tmp_path):
    repo = _fixture_repo(
        tmp_path,
        "source:\n  - url: https://example.org/foo-1.0.msi\n"
        "    sha256: " + "0" * 64 + "\n")
    (repo / "build" / "sources" / "foo-1.0.msi").write_bytes(b"not-an-archive")
    findings = _scan_fixture(repo, only=["foo"])
    assert [f["type"] for f in findings] == ["SOURCE-NOT-ARCHIVE"]
    assert "SOURCE-NOT-ARCHIVE" not in mod.COVERAGE_TYPES


def test_corrupt_archive_is_coverage_class(tmp_path):
    repo = _fixture_repo(
        tmp_path,
        "source:\n  - url: https://example.org/foo-1.0.tar.gz\n"
        "    sha256: " + "0" * 64 + "\n")
    (repo / "build" / "sources" / "foo-1.0.tar.gz").write_bytes(b"garbage")
    findings = _scan_fixture(repo, only=["foo"])
    assert [f["type"] for f in findings] == ["SOURCE-EXTRACT-FAILED"]
    assert "SOURCE-EXTRACT-FAILED" in mod.COVERAGE_TYPES


def test_unknown_only_name_raises(tmp_path):
    repo = _fixture_repo(tmp_path, "source: []\n")
    with pytest.raises(mod.UnknownOnlyNames):
        _scan_fixture(repo, only=["no-such-pkg"])


def _run_main(repo: Path, argv: list[str], monkeypatch, capsys) -> int:
    monkeypatch.setattr("sys.argv",
                        ["preflight-undeclared-deps.py",
                         "--root", str(repo)] + argv)
    rc = mod.main()
    capsys.readouterr()
    return rc


def test_main_gate_mode_fails_closed_on_coverage(tmp_path, monkeypatch, capsys):
    repo = _fixture_repo(
        tmp_path,
        "source:\n  - url: https://example.org/foo-1.0.tar.gz\n"
        "    sha256: " + "0" * 64 + "\n")
    assert _run_main(repo, ["--only", "foo"], monkeypatch, capsys) == 3


def test_main_advisory_keeps_historical_exit(tmp_path, monkeypatch, capsys):
    repo = _fixture_repo(
        tmp_path,
        "source:\n  - url: https://example.org/foo-1.0.tar.gz\n"
        "    sha256: " + "0" * 64 + "\n")
    assert _run_main(repo, ["--only", "foo", "--advisory"],
                     monkeypatch, capsys) == 0


def test_main_missing_sources_dir_fails_closed(tmp_path, monkeypatch, capsys):
    repo = _fixture_repo(tmp_path, "source: []\n")
    import shutil
    shutil.rmtree(repo / "build" / "sources")
    assert _run_main(repo, [], monkeypatch, capsys) == 3


def test_main_unknown_only_exits_two(tmp_path, monkeypatch, capsys):
    repo = _fixture_repo(tmp_path, "source: []\n")
    assert _run_main(repo, ["--only", "typo-pkg"], monkeypatch, capsys) == 2


def test_main_by_design_classes_pass_gate_mode(tmp_path, monkeypatch, capsys):
    repo = _fixture_repo(tmp_path, "source: []\n")
    assert _run_main(repo, ["--only", "foo"], monkeypatch, capsys) == 0
