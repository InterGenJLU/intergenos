# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""scripts/check-initramfs-commands.py: every command an initramfs /init runs exists inside that initramfs.

Measured 2026-10-16: the R001.3 live medium panicked at boot ("Attempted to
kill init! exitcode=0x00007f00") because init.sh had gained a chmod call the
live builder's busybox applet list did not link, and PID 1 exited 127 under
set -e. These tests read scripts the way the gate reads them, run the gate
over staged trees, and run both real builders: the live builder's archive
must carry every command its /init runs, and the builder must refuse an init
that runs a command the archive lacks; the FDE builder's archive must carry
every command fde-init.sh runs, its two experimental unlock tool sets being
optional exactly when the build environment does not have them (the archive
is built with neither set, with each one alone and with both).
"""
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GATE = ROOT / "scripts" / "check-initramfs-commands.py"
LIVE_BUILDER = ROOT / "installer" / "init" / "build-initramfs.sh"
FDE_BUILDER = ROOT / "installer" / "init" / "build-fde-initramfs.sh"
LIVE_INIT = ROOT / "installer" / "init" / "init.sh"
FDE_INIT = ROOT / "installer" / "init" / "fde-init.sh"
BUSYBOX = Path("/usr/bin/busybox.static")
# fde-init.sh probes for these (command -v) and build-fde-initramfs.sh bundles
# each set only when its package (tpm2-tools-static, fido2-tools-static) is
# installed: {set: (the files its package directory holds, the names fde-init.sh runs)}.
FDE_BUNDLES = {
    "tpm2": (("tpm2", "tpm2_unseal"), ("tpm2_load", "tpm2_unseal")),
    "fido2": (("fido2-token", "fido2-cred", "fido2-assert"), ("fido2-token", "fido2-assert")),
}
FDE_OPTIONAL = FDE_BUNDLES["tpm2"][1] + FDE_BUNDLES["fido2"][1]
# The FDE builder runs ldd over every tool it bundles, so each stand-in is a
# dynamically linked program, as the real tools are.
DYNAMIC_STAND_IN = Path("/usr/bin/true")

needs_busybox = pytest.mark.skipif(
    not os.access(BUSYBOX, os.X_OK),
    reason="needs /usr/bin/busybox.static (the busybox-static package), the binary both builders put in the archive")
needs_build_tools = pytest.mark.skipif(
    not all(shutil.which(t) for t in ("cpio", "gzip", "modinfo", "depmod", "numfmt")),
    reason="the builders need cpio, gzip, modinfo, depmod and numfmt")


def _gate():
    spec = importlib.util.spec_from_file_location("check_initramfs_commands", GATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _calls(src):
    calls, functions = _gate().enumerate_commands(src)
    return [(t.literal if t.literal is not None else t.text, how) for t, how in calls], functions


def _programs(src):
    calls, functions = _calls(src)
    return [w for w, how in calls if how == "call" and w not in functions]


def _run_gate(*args):
    return subprocess.run([sys.executable, "-I", str(GATE), *map(str, args)],
                          capture_output=True, text=True, timeout=60)


def _stage(tmp_path, init_text, applets=("sh", "echo")):
    """A minimal staged initramfs: the real static busybox, the named applet links, the init script."""
    root = tmp_path / "work"
    (root / "bin").mkdir(parents=True)
    (root / "sbin").mkdir()
    shutil.copy2(BUSYBOX, root / "bin" / "busybox")
    for a in applets:
        os.symlink("busybox", root / "bin" / a)
    (root / "init").write_text(init_text)
    (root / "init").chmod(0o755)
    return root


# ---- the parser -------------------------------------------------------------------------------------------

def test_a_quoted_here_document_body_is_data_not_commands():
    src = ("cat > /f <<'EOF'\nExec=sh -c 'touch /tmp/x; exec /usr/bin/forge-gui-launch'\nfrobnicate --now\nEOF\n"
           "echo done\n")
    assert _programs(src) == ["cat", "echo"]


def test_an_unquoted_here_document_runs_its_substitutions():
    assert _programs("cat <<EOF\nbuilt $(date -u) on `hostname`\nEOF\n") == ["cat", "date", "hostname"]


def test_substitutions_in_assignments_arguments_and_double_quotes_are_read():
    src = ('tmpdir=$(mktemp -d 2>/dev/null) || tmpdir=/tmp/scan\n'
           'echo "kernel $(uname -r)"\n'
           '[ "$(id -u)" = 0 ] && x=${y:-$(pwd)}\n'
           'for arg in $(cat /proc/cmdline); do :; done\n')
    # the script's own commands first, then each substitution's, in the order they appear
    assert _programs(src) == ["echo", "[", ":", "mktemp", "uname", "id", "pwd", "cat"]


def test_a_single_quote_inside_double_quotes_does_not_hide_a_substitution():
    assert _programs('echo "don\'t $(rm -f /x)" \'$(not-run)\'\n') == ["echo", "rm"]


def test_lists_pipelines_and_every_compound_command_are_walked():
    src = """if a1 && ! a2; then
    b1 | b2 || b3
elif c1; then c2
else d1; fi
while e1; do e2; done
until f1; do f2; done
case "$m" in
    live|install-gui) g1 ;;
    (x*) g2; g3 ;;
    *) g4
esac
{ h1; h2; } > /dev/null
( i1 ) && i2
"""
    assert sorted(_programs(src)) == sorted(
        ["a1", "a2", "b1", "b2", "b3", "c1", "c2", "d1", "e1", "e2", "f1", "f2", "g1", "g2", "g3", "g4",
         "h1", "h2", "i1", "i2"])


def test_a_function_the_script_defines_is_not_a_program():
    src = "fatal() {\n    echo x >&2\n    exec /bin/sh\n}\ninfo () { echo \"$*\"; }\nfatal\ninfo hi\n"
    calls, functions = _calls(src)
    assert functions == {"fatal", "info"}
    assert _programs(src) == ["echo", "exec", "/bin/sh", "echo"]


def test_exec_command_and_a_command_v_probe():
    src = ("exec switch_root /newroot /sbin/init\n"
           "if ! command -v tpm2_unseal >/dev/null 2>&1; then :; fi\n"
           "command mount -a\n")
    calls, _ = _calls(src)
    assert calls == [("exec", "call"), ("switch_root", "call"), ("command", "call"), ("tpm2_unseal", "probe"),
                     (":", "call"), ("command", "call"), ("mount", "call")]


def test_redirections_assignment_prefixes_and_continuation_lines():
    src = ("2>/dev/null rmdir /x\nLANG=C sort /f > /out 2>&1\n"
           "mount -t overlay overlay \\\n    -o lowerdir=/a \\\n    /newroot \\\n    || fatal \"x\"\n")
    assert _programs(src) == ["rmdir", "sort", "mount", "fatal"]


def test_a_command_built_at_run_time_is_reported_and_sh_c_literal_is_read():
    calls, _ = _calls("$CMD arg\nsh -c 'chmod 644 /f'\nsh -c \"$script\"\n")
    assert ("$CMD", "call") in calls
    assert ("chmod", "call") in calls
    assert ("\"$script\"", "dynamic-script") in calls


def test_the_live_init_script_runs_the_three_commands_the_panic_named():
    words = set(_programs(LIVE_INIT.read_text()))
    assert {"chmod", "mktemp", "rmdir"} <= words
    # here-document bodies (unit files, desktop entries) are data written to the new root
    assert not words & {"touch", "dconf", "/usr/bin/dconf", "/usr/bin/forge-gui-launch"}


# ---- the gate over a staged tree --------------------------------------------------------------------------

@needs_busybox
def test_a_command_with_no_executable_in_the_tree_is_refused_by_name(tmp_path):
    root = _stage(tmp_path, "#!/bin/sh\nset -e\necho start\nchmod 644 /x\n")
    r = _run_gate("--root", root)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "REFUSED: 1 command word(s)" in r.stdout
    assert "chmod (no executable in sbin/, usr/sbin/, bin/, usr/bin/; line 4)" in r.stdout


@needs_busybox
def test_a_link_to_busybox_for_an_applet_it_does_not_have_is_refused(tmp_path):
    root = _stage(tmp_path, "#!/bin/sh\nfrobnicate --now\n", applets=("sh", "frobnicate"))
    r = _run_gate("--root", root)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "links to busybox, which has no frobnicate applet" in r.stdout


@needs_busybox
def test_builtins_are_confirmed_by_the_archive_shell_and_a_complete_tree_passes(tmp_path):
    root = _stage(tmp_path, "#!/bin/sh\nset -e\nf() { local d; [ -n \"$1\" ] && echo \"$1\"; }\nf x\nchmod 644 /x\n",
                  applets=("sh", "chmod"))
    r = _run_gate("--root", root)
    assert r.returncode == 0, r.stdout + r.stderr
    for word in ("set", "local", "[", "echo"):
        assert "  builtin     %-14s" % word in r.stdout
    assert "  program     chmod          -> bin/busybox (busybox applet)" in r.stdout
    assert "[initramfs-commands] PASS: every command word /init runs resolves inside" in r.stdout


@needs_busybox
def test_an_absent_program_passes_only_when_declared_optional(tmp_path):
    root = _stage(tmp_path, "#!/bin/sh\nif command -v tool >/dev/null; then tool --go; fi\n")
    refused = _run_gate("--root", root)
    assert refused.returncode == 1 and "tool (no executable" in refused.stdout
    allowed = _run_gate("--root", root, "--optional", "tool")
    assert allowed.returncode == 0, allowed.stdout + allowed.stderr
    assert "(optional and absent, by declaration: tool)" in allowed.stdout


@needs_busybox
def test_a_dynamic_command_word_and_eval_are_refused(tmp_path):
    root = _stage(tmp_path, "#!/bin/sh\n$CMD arg\neval \"$x\"\n")
    r = _run_gate("--root", root)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "$CMD (line 2: a command built at run time" in r.stdout
    assert "eval (line 3: a command built at run time" in r.stdout


@needs_busybox
def test_an_interpreter_the_tree_lacks_is_refused(tmp_path):
    root = _stage(tmp_path, "#!/bin/ash\necho x\n")
    r = _run_gate("--root", root)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "/bin/ash (the interpreter line:" in r.stdout


# ---- the real builders ------------------------------------------------------------------------------------

def _stub(path):
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    return path


def _build_live(tmp_path, init_script):
    kver = "0.0.0-initramfs-gate-test"
    modules = tmp_path / "modules" / kver
    modules.mkdir(parents=True)
    out = tmp_path / "live.cpio.gz"
    env = dict(os.environ, INIT_SCRIPT=str(init_script), BUSYBOX=str(BUSYBOX),
               VERITYSETUP_STATIC=str(_stub(tmp_path / "veritysetup-static")), MODULES_DIR=str(modules))
    r = subprocess.run(["bash", str(LIVE_BUILDER), kver, str(out)], capture_output=True, text=True, timeout=180,
                       env=env, stdin=subprocess.DEVNULL)
    return r, out


def _build_fde(tmp_path, init_script, present=()):
    kver = "0.0.0-initramfs-gate-test"
    modules = tmp_path / "modules" / kver
    modules.mkdir(parents=True)
    out = tmp_path / "fde.cpio.gz"
    tool_dirs = {}
    for name, (files, _) in FDE_BUNDLES.items():
        tool_dirs[name] = tmp_path / ("%s-tools-static" % name if name in present else "absent-%s" % name)
        if name in present:
            tool_dirs[name].mkdir()
            for f in files:
                shutil.copy2(DYNAMIC_STAND_IN, tool_dirs[name] / f)
    env = dict(os.environ, INIT_SCRIPT=str(init_script), BUSYBOX=str(BUSYBOX),
               CRYPTSETUP_STATIC=str(_stub(tmp_path / "cryptsetup-static")), MODULES_DIR=str(modules),
               TPM2_TOOLS_DIR=str(tool_dirs["tpm2"]), FIDO2_TOOLS_DIR=str(tool_dirs["fido2"]),
               CRYPTTAB_SRC=str(tmp_path / "absent-crypttab"))
    r = subprocess.run(["bash", str(FDE_BUILDER), kver, str(out)], capture_output=True, text=True, timeout=180,
                       env=env, stdin=subprocess.DEVNULL)
    return r, out


@needs_busybox
@needs_build_tools
def test_the_live_builder_archive_carries_every_command_its_init_runs(tmp_path):
    r, out = _build_live(tmp_path, LIVE_INIT)
    assert r.returncode == 0, r.stdout + r.stderr
    g = _run_gate("--cpio", out)
    assert g.returncode == 0, g.stdout + g.stderr
    assert "[initramfs-commands] PASS: every command word /init runs resolves inside" in g.stdout


@needs_busybox
@needs_build_tools
def test_the_live_builder_refuses_an_init_that_runs_a_command_the_archive_lacks(tmp_path):
    plant = tmp_path / "init-with-frobnicate.sh"
    plant.write_text(LIVE_INIT.read_text().replace(
        'info "switching root to /newroot, PID 1 = systemd"\n',
        'info "switching root to /newroot, PID 1 = systemd"\nfrobnicate --now\n', 1))
    assert "frobnicate --now" in plant.read_text()
    r, out = _build_live(tmp_path, plant)
    assert r.returncode != 0, r.stdout + r.stderr
    assert "[initramfs-commands] REFUSED: 1 command word(s)" in r.stdout
    assert "frobnicate (no executable in sbin/, usr/sbin/, bin/, usr/bin/;" in r.stdout
    assert "nothing was packed" in r.stderr
    assert not out.exists()


@needs_busybox
@needs_build_tools
def test_the_fde_builder_archive_carries_every_command_fde_init_runs(tmp_path):
    r, out = _build_fde(tmp_path, FDE_INIT)
    assert r.returncode == 0, r.stdout + r.stderr
    args = ["--cpio", out]
    for name in FDE_OPTIONAL:
        args += ["--optional", name]
    g = _run_gate(*args)
    assert g.returncode == 0, g.stdout + g.stderr
    clause = g.stdout.split("(optional and absent, by declaration: ", 1)[1].split(")", 1)[0]
    assert set(clause.split(", ")) == set(FDE_OPTIONAL)
    # without the declaration the same archive is refused on exactly those names
    bare = _run_gate("--cpio", out)
    assert bare.returncode == 1
    assert bare.stdout.count("  MISSING     ") == len(FDE_OPTIONAL)


@needs_busybox
@needs_build_tools
@pytest.mark.skipif(not shutil.which("ldd"), reason="the FDE builder runs ldd over every unlock tool it bundles")
@pytest.mark.parametrize("present", [("tpm2",), ("fido2",), ("tpm2", "fido2")], ids="+".join)
def test_a_probed_unlock_tool_is_optional_exactly_while_its_set_is_absent(tmp_path, present):
    r, out = _build_fde(tmp_path, FDE_INIT, present)
    assert r.returncode == 0, r.stdout + r.stderr
    for name in FDE_BUNDLES:
        assert ("bundling %s-tools-static" % name in r.stdout) == (name in present), r.stdout
    absent = [t for name, (_, tools) in FDE_BUNDLES.items() if name not in present for t in tools]
    args = ["--cpio", out]
    for t in absent:
        args += ["--optional", t]
    g = _run_gate(*args)
    assert g.returncode == 0, g.stdout + g.stderr
    # each name a bundled set carries resolves in the archive with no declaration behind it
    for name in present:
        for t in FDE_BUNDLES[name][1]:
            assert "  program     %-14s -> sbin/" % t in g.stdout, g.stdout
    if absent:
        clause = g.stdout.split("(optional and absent, by declaration: ", 1)[1].split(")", 1)[0]
        assert set(clause.split(", ")) == set(absent)
    else:
        assert "by declaration" not in g.stdout
    bare = _run_gate("--cpio", out)
    assert bare.returncode == (1 if absent else 0), bare.stdout + bare.stderr
    assert bare.stdout.count("  MISSING     ") == len(absent)


@needs_busybox
@needs_build_tools
def test_the_fde_check_refuses_an_fde_init_that_runs_a_command_the_archive_lacks(tmp_path):
    plant = tmp_path / "fde-init-with-frobnicate.sh"
    plant.write_text(FDE_INIT.read_text().replace(
        "exec switch_root /newroot /sbin/init\n", "frobnicate --now\nexec switch_root /newroot /sbin/init\n", 1))
    assert "frobnicate --now" in plant.read_text()
    r, out = _build_fde(tmp_path, plant)
    assert r.returncode == 0, r.stdout + r.stderr
    args = ["--cpio", out]
    for name in FDE_OPTIONAL:
        args += ["--optional", name]
    g = _run_gate(*args)
    assert g.returncode == 1, g.stdout + g.stderr
    assert "frobnicate (no executable in sbin/, usr/sbin/, bin/, usr/bin/;" in g.stdout
