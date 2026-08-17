"""Tests for LLMRouter._parse_dialect_tool_call — the narrow fallback that
recovers a tool call from a non-Hermes dialect that leaked into the model's
content stream (InternVL's occasional pythonic / InternLM tool-call form) —
plus the streaming wire-in (_dialect_scan + stream_with_tools recovery).
"""
import json
import unittest
from unittest import mock

from intergen.llm import LLMRouter
from intergen.interfaces.types import Message, MessageRole, ToolSchema, ToolCall


class TestDialectToolCall(unittest.TestCase):
    parse = staticmethod(LLMRouter._parse_dialect_tool_call)

    def test_pythonic_keyvalue_body(self):
        # The exact dialect observed leaking in the InternVL dyno (1/122).
        text = (
            '<function=run_command>\n'
            '<parameter>command="df -h"</parameter>\n'
            '<parameter>timeout=30</parameter>'
        )
        self.assertEqual(
            self.parse(text),
            ("run_command", {"command": "df -h", "timeout": 30}),
        )

    def test_pythonic_name_attr_body(self):
        text = (
            '<function=manage_packages>'
            '<parameter name="action">install</parameter>'
            '<parameter name="package">htop</parameter>'
        )
        self.assertEqual(
            self.parse(text),
            ("manage_packages", {"action": "install", "package": "htop"}),
        )

    def test_internlm_action_plugin_json(self):
        text = (
            '<|action_start|><|plugin|>'
            '{"name": "run_command", "parameters": {"command": "ls"}}'
            '<|action_end|>'
        )
        self.assertEqual(self.parse(text), ("run_command", {"command": "ls"}))

    def test_internlm_arguments_key(self):
        text = (
            '<|plugin|>{"name": "manage_services", '
            '"arguments": {"action": "status", "unit": "sshd"}}<|action_end|>'
        )
        self.assertEqual(
            self.parse(text),
            ("manage_services", {"action": "status", "unit": "sshd"}),
        )

    def test_no_args_function(self):
        self.assertEqual(self.parse("<function=list_models>"), ("list_models", {}))

    def test_ordinary_prose_is_none(self):
        self.assertIsNone(self.parse("Your disk is 50% full; nothing to worry about."))

    def test_empty_is_none(self):
        self.assertIsNone(self.parse(""))
        self.assertIsNone(self.parse("   "))

    def test_malformed_internlm_json_is_none(self):
        self.assertIsNone(
            self.parse("<|plugin|>{not valid json}<|action_end|>")
        )

    def test_leading_whitespace_tolerated(self):
        self.assertEqual(
            self.parse('   <function=uptime>'), ("uptime", {})
        )

    def test_single_quoted_value(self):
        text = "<function=run_command><parameter>command='whoami'</parameter>"
        self.assertEqual(self.parse(text), ("run_command", {"command": "whoami"}))

    def test_pythonic_parameter_eq_key(self):
        # Key embedded in the opening tag after '=' — a live InternVL emission that
        # the original parser dropped (it only handled name="k" or k=value body).
        text = (
            '<function=read_file>'
            '<parameter=path>/etc/hostname</parameter>'
            '<parameter=start_line>1</parameter>'
        )
        self.assertEqual(
            self.parse(text),
            ("read_file", {"path": "/etc/hostname", "start_line": 1}),
        )

    def test_bare_named_tag_params(self):
        # Params emitted as bare named tags OUTSIDE a <parameter> wrapper — the
        # exact shape that dropped source_of_request and forced the malformed
        # apology on natural phrasings (observed live for "What is systemd?").
        text = (
            '<function=web_search>'
            '<parameter=query>What is systemd?</parameter>'
            '<num_results>5</num_results>'
            '<source_of_request>user_direct</source_of_request>'
        )
        self.assertEqual(
            self.parse(text),
            ("web_search", {"query": "What is systemd?",
                            "num_results": 5,
                            "source_of_request": "user_direct"}),
        )

    def test_provenance_param_without_value_not_recovered(self):
        # A genuine model fumble: the provenance param NAME with no value. We must
        # NOT fabricate it — the recovered args carry no source_of_request, so the
        # D-008 gate still refuses (no-fallback). The real params are still parsed.
        text = (
            '<function=read_file>'
            '<parameter=path>/home/u/.bashrc</parameter>'
            '<parameter>source_of_request</parameter>'
        )
        name, args = self.parse(text)
        self.assertEqual(name, "read_file")
        self.assertEqual(args, {"path": "/home/u/.bashrc"})
        self.assertNotIn("source_of_request", args)


class _FakeResp:
    """Minimal stand-in for the urllib SSE response: iterable byte lines + close."""
    def __init__(self, lines):
        self._lines = lines

    def __iter__(self):
        return iter(self._lines)

    def close(self):
        pass


def _sse(*chunks):
    lines = [b"data: " + json.dumps(c).encode() for c in chunks]
    lines.append(b"data: [DONE]")
    return _FakeResp(lines)


def _content(text):
    return {"choices": [{"delta": {"content": text}, "finish_reason": None}]}


def _stop():
    return {"choices": [{"delta": {}, "finish_reason": "stop"}]}


_TOOLS = [ToolSchema(name="run_command", description="run a shell command",
                     parameters={"type": "object",
                                 "properties": {"command": {"type": "string"}}})]
_MSGS = [Message(MessageRole.SYSTEM, "sys"), Message(MessageRole.USER, "do it")]


class TestDialectScan(unittest.TestCase):
    scan = staticmethod(LLMRouter._dialect_scan)

    def test_match_openers(self):
        self.assertEqual(self.scan("<function=run_command>"), "match")
        self.assertEqual(self.scan("<|action_start|>x"), "match")
        self.assertEqual(self.scan("<|plugin|>{}"), "match")

    def test_prefix_partial(self):
        for p in ("<", "<f", "<fun", "<|", "<|a", "<|p"):
            self.assertEqual(self.scan(p), "prefix", p)

    def test_plain(self):
        self.assertEqual(self.scan("Hello world"), "plain")
        self.assertEqual(self.scan("<html>hi"), "plain")
        self.assertEqual(self.scan("<formatting the disk"), "plain")


class TestStreamDialectRecovery(unittest.TestCase):
    """The stream wire-in: normal content is byte-identical; a dialect block in
    content is recovered to a ToolCall when it carries provenance, else passed
    through raw (no regression)."""

    def _run(self, resp):
        router = LLMRouter({"tool_calling": True})
        with mock.patch("urllib.request.urlopen", return_value=resp):
            return list(router.stream_with_tools(_MSGS, tools=_TOOLS))

    def test_normal_content_streams_unchanged(self):
        out = self._run(_sse(_content("Hello"), _content(" world"), _stop()))
        self.assertEqual([o for o in out if isinstance(o, str)],
                         ["Hello", " world"])
        self.assertFalse([o for o in out if isinstance(o, ToolCall)])

    def test_dialect_with_provenance_recovered(self):
        out = self._run(_sse(
            _content('<function=run_command>'),
            _content('<parameter>command="ls"</parameter>'),
            _content('<parameter>source_of_request=user_direct</parameter>'),
            _stop(),
        ))
        calls = [o for o in out if isinstance(o, ToolCall)]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "run_command")
        self.assertEqual(calls[0].arguments, {"command": "ls"})
        # raw dialect must NOT leak as content
        self.assertFalse([o for o in out
                          if isinstance(o, str) and "<function" in o])

    def test_dialect_without_provenance_yields_honest_line(self):
        # No source_of_request -> provenance gate refuses. The buffer PARSED as a
        # dialect tool-call attempt, so emit an honest failed-action line — never
        # the raw tool syntax, never a fabricated dispatch.
        out = self._run(_sse(
            _content('<function=run_command><parameter>command="ls"</parameter>'),
            _stop(),
        ))
        self.assertFalse([o for o in out if isinstance(o, ToolCall)])
        joined = "".join(o for o in out if isinstance(o, str))
        self.assertNotIn("<function", joined)
        self.assertIn("malformed", joined)

    def test_dialect_disallowed_tool_yields_honest_line(self):
        # Recovered with provenance but the tool is not in the allowed set ->
        # refused like a hallucinated tool -> honest line, not raw syntax.
        out = self._run(_sse(
            _content('<function=rm_rf>'),
            _content('<parameter>source_of_request=user_direct</parameter>'),
            _stop(),
        ))
        self.assertFalse([o for o in out if isinstance(o, ToolCall)])
        joined = "".join(o for o in out if isinstance(o, str))
        self.assertNotIn("<function", joined)
        self.assertIn("malformed", joined)

    def test_dialect_bare_provenance_recovered_to_toolcall(self):
        # End-to-end win: a tool call whose provenance arrives as a bare named tag
        # outside a <parameter> wrapper now recovers to a real ToolCall instead of
        # the malformed-apology — the gate sees source_of_request and dispatches.
        out = self._run(_sse(
            _content('<function=run_command>'),
            _content('<parameter=command>df -h</parameter>'),
            _content('<source_of_request>user_direct</source_of_request>'),
            _stop(),
        ))
        calls = [o for o in out if isinstance(o, ToolCall)]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "run_command")
        self.assertEqual(calls[0].arguments, {"command": "df -h"})
        self.assertFalse([o for o in out
                          if isinstance(o, str) and "malformed" in o])

    def test_angle_bracket_nondialect_flushed_as_content(self):
        out = self._run(_sse(_content("<"), _content("html>hi"), _stop()))
        self.assertFalse([o for o in out if isinstance(o, ToolCall)])
        self.assertEqual("".join(o for o in out if isinstance(o, str)), "<html>hi")


if __name__ == "__main__":
    unittest.main()
