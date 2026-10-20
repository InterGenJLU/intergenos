# SPDX-License-Identifier: GPL-3.0-or-later
"""Opt-in subprocess relay keeps capture, return and timeout contracts."""

import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def trace(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "trace_live_test", ROOT / "scripts/lib/igos_trace.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._VERBOSE = True
    module._open_sink(tmp_path / "trace.jsonl")
    yield module
    module.close_trace()


def python(code):
    return [sys.executable, "-c", code]


def test_default_still_captures_without_relay(trace, capsys):
    result = trace.traced_run(python("print('captured')"))
    assert result.stdout == "captured\n"
    assert result.stderr == ""
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("payload", [None, "", "hello\n", b"", b"binary\xff\n", bytearray(b"data\n")])
def test_streaming_keeps_input_and_completed_process_types(trace, capsysbinary, payload):
    code = ("import os; os.write(1, b'out\\n'); os.write(2, b'err\\n')"
            if payload is None else
            "import sys; data=sys.stdin.buffer.read(); sys.stdout.buffer.write(data)")
    result = trace.traced_run(python(code), input=payload, stream_output=True)
    binary = isinstance(payload, (bytes, bytearray))
    assert isinstance(result.stdout, bytes if binary else str)
    expected = "out\n" if payload is None else payload
    assert result.stdout == expected
    assert result.returncode == 0
    if payload is None:
        assert result.stderr == "err\n"
        assert capsysbinary.readouterr() == (b"out\n", b"err\n")


def test_streaming_text_capture_matches_default_decoding_and_newlines(trace, capsysbinary):
    cmd = python("import os; os.write(1, b'first\\rsecond\\r\\nlast\\xff'); os.write(2, b'err\\r')")
    base = trace.traced_run(cmd)
    result = trace.traced_run(cmd, stream_output=True)
    assert (result.stdout, result.stderr) == (base.stdout, base.stderr)
    assert result.stdout == "first\nsecond\nlast\ufffd"


def test_large_input_and_both_output_streams_do_not_deadlock_or_truncate(trace, capsys):
    payload = b"x" * (1024 * 1024)
    cmd = python("import sys; sys.stderr.buffer.write(b'e' * 1048576); "
                 "sys.stderr.flush(); sys.stdout.buffer.write(sys.stdin.buffer.read())")
    result = trace.traced_run(cmd, input=payload, timeout=5, stream_output=True)
    assert result.stdout == payload
    assert result.stderr == b"e" * 1048576
    output = capsys.readouterr()
    assert len(output.out) == len(output.err) == 1048576


def test_a_child_that_closes_stdin_early_keeps_its_exit_status(trace, capsys):
    result = trace.traced_run(python("import os; os.close(0); print('done')"),
                              input=b"x" * 1048576, stream_output=True, timeout=5)
    assert result.returncode == 0
    assert result.stdout == b"done\n"


def test_checked_failure_keeps_captured_output(trace, capsys):
    with pytest.raises(subprocess.CalledProcessError) as caught:
        trace.traced_run(python("import sys; print('out'); print('err', file=sys.stderr); sys.exit(7)"),
                         check=True, stream_output=True)
    assert caught.value.returncode == 7
    assert (caught.value.stdout, caught.value.stderr) == ("out\n", "err\n")
    assert capsys.readouterr() == ("out\n", "err\n")


@pytest.mark.parametrize("close_pipes", [False, True])
def test_finite_timeout_still_terminates_and_reaps_the_child(trace, tmp_path, capsys, close_pipes):
    pidfile = tmp_path / "pid"
    code = (f"import os,time; open({str(pidfile)!r},'w').write(str(os.getpid())); "
            "os.write(1,b'out'); os.write(2,b'err'); "
            + ("os.close(1); os.close(2); " if close_pipes else "") + "time.sleep(10)")
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        trace.traced_run(python(code), timeout=0.5, stream_output=True)
    assert caught.value.stdout == b"out"
    assert caught.value.stderr == b"err"
    assert capsys.readouterr() == ("out", "err")
    with pytest.raises(ProcessLookupError):
        os.kill(int(pidfile.read_text()), 0)
    rows = [json.loads(line) for line in (tmp_path / "trace.jsonl").read_text().splitlines()]
    assert rows[-1]["type"] == "subprocess_exception"


def test_no_timeout_and_no_output_still_complete(trace, capsys):
    result = trace.traced_run(python("import time; time.sleep(0.05)"),
                              timeout=None, stream_output=True)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")
    assert capsys.readouterr() == ("", "")


def test_text_only_console_handles_split_multibyte_characters(trace, monkeypatch):
    output, errors = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setattr(sys, "stderr", errors)
    cmd = python("import os,time; os.write(1,b'\\xe2'); time.sleep(0.05); "
                 "os.write(1,b'\\x82\\xac\\r'); os.write(2,b'error')")
    result = trace.traced_run(cmd, stream_output=True)
    assert output.getvalue() == "€\r"
    assert errors.getvalue() == "error"
    assert result.stdout == "€\n"


def test_pending_console_text_stays_before_the_relayed_output(trace, monkeypatch):
    raw = io.BytesIO()
    console = io.TextIOWrapper(raw, encoding="utf-8")
    console.write("before\n")
    monkeypatch.setattr(sys, "stdout", console)
    trace.traced_run(python("print('child')"), stream_output=True)
    assert raw.getvalue() == b"before\nchild\n"


def test_relay_keeps_trace_redaction(trace, tmp_path, capsys):
    payload = "root:$6$saltsaltsalt$" + "A" * 86 + "\n"
    result = trace.traced_run(python("import sys; sys.stdout.write(sys.stdin.read())"),
                              input=payload, stream_output=True)
    assert result.stdout == payload
    rows = [json.loads(line) for line in (tmp_path / "trace.jsonl").read_text().splitlines()]
    assert rows[0]["stdin"] == "<REDACTED>"
    assert rows[-1]["stdout"] == "<REDACTED>"
    assert rows[-1]["stdout_bytes"] == len(payload)


def test_spawn_failure_keeps_its_exception(trace, tmp_path):
    with pytest.raises(FileNotFoundError):
        trace.traced_run([str(tmp_path / "missing-command")], stream_output=True)
