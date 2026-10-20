# SPDX-License-Identifier: GPL-3.0-or-later
"""The real package-hook consumer relays each stream before the hook exits."""

import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONSUMER = r'''
import json
from pathlib import Path
import sys
from pkm import installer
root = Path(sys.argv[1])
installer._TRACE_AVAILABLE = sys.argv[2] == "trace"
installer._trace.module._open_sink(root / "trace.jsonl")
installer._post_install_hook_cmd = lambda root, hook: ["/bin/sh", str(hook)]
instance = installer.PackageInstaller.__new__(installer.PackageInstaller)
instance.root = root
rc = instance._run_post_install_hook("probe", "1")
(root / "result.json").write_text(json.dumps({"rc": rc}))
'''


def fire(tmp_path, script, trace=True):
    hook = tmp_path / "var/lib/pkm/hooks/probe/post-install"
    hook.parent.mkdir(parents=True)
    hook.write_text("#!/bin/sh\n" + script)
    hook.chmod(0o755)
    env = {**os.environ, "PYTHONPATH": str(ROOT),
           "IGOS_BUILD_DEBUG_VERBOSE": "1", "FORGE_DEBUG_VERBOSE": "1"}
    argv = [sys.executable, "-c", CONSUMER, str(tmp_path),
            "trace" if trace else "fallback"]
    output = {"stdout": bytearray(), "stderr": bytearray()}
    events = []
    with subprocess.Popen(argv, env=env, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE) as child:
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ, "stdout")
            selector.register(child.stderr, selectors.EVENT_READ, "stderr")
            deadline = time.monotonic() + 10
            while selector.get_map():
                if time.monotonic() >= deadline:
                    child.kill()
                    pytest.fail("hook consumer did not finish")
                for key, _ in selector.select(0.1):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    output[key.data].extend(chunk)
                    events.append((key.data, chunk, not (tmp_path / "done").exists()))
        assert child.wait(timeout=2) == 0, bytes(output["stderr"])
    result = json.loads((tmp_path / "result.json").read_text())
    rows = [json.loads(line) for line in (tmp_path / "trace.jsonl").read_text().splitlines()]
    return output, events, result, rows


@pytest.mark.parametrize("stream,content", [
    ("stdout", "step is running\\n"), ("stderr", "meter 20%\\r")])
@pytest.mark.parametrize("trace", [True, False])
def test_output_reaches_the_console_while_the_hook_is_running(tmp_path, stream, content, trace):
    redirect = " >&2" if stream == "stderr" else ""
    output, events, result, _ = fire(
        tmp_path, f"printf '%b' '{content}'{redirect}\nsleep 0.5\n"
        f"touch '{tmp_path / 'done'}'\n", trace)
    expected = content.replace("\\n", "\n").replace("\\r", "\r").encode()
    assert expected in output[stream]
    assert any(name == stream and expected in chunk and live
               for name, chunk, live in events)
    assert result["rc"] == 0


@pytest.mark.parametrize("code,word", [(0, None), (7, b"WARNING"), (77, b"REFUSED TO SIGN")])
def test_output_capture_and_hook_status_are_preserved(tmp_path, code, word):
    output, _, result, rows = fire(
        tmp_path, "printf 'out\\rnext\\n'\nprintf 'err\\n' >&2\n"
        f"exit {code}\n")
    assert result["rc"] == code
    end = next(row for row in rows if row["type"] == "subprocess_end")
    assert (end["stdout"], end["stderr"], end["rc"]) == ("out\nnext\n", "err\n", code)
    assert end["stdout_bytes"] == len("out\nnext\n")
    assert end["stderr_bytes"] == len("err\n")
    assert next(row for row in rows if row["type"] == "pkm_hook_done")["rc"] == code
    if word is not None:
        assert word in output["stderr"]


def test_a_missing_hook_stays_unrun(tmp_path, monkeypatch):
    from pkm import installer
    instance = installer.PackageInstaller.__new__(installer.PackageInstaller)
    instance.root = tmp_path
    monkeypatch.setattr(installer._trace, "traced_run",
                        lambda *a, **kw: pytest.fail("missing hook ran"))
    assert instance._run_post_install_hook("absent", "1") is None
