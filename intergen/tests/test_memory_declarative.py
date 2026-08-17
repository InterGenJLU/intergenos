"""Deterministic tests for bare-declarative classification + yes/no detection.

The router offers (never silently stores) on a stated preference or a reported
complaint, and abstains on anything ambiguous. That decision is deterministic
and high-precision by design — these tests pin it so a corpus/dyno regression
in the (nondeterministic) model can't mask a regression in the guard itself.
"""

import pytest

from intergen.memory import MemoryManager


@pytest.mark.parametrize("message,expected_kind", [
    # Clear preferences / stated facts → offer to store.
    ("My editor is vim", "preference"),
    ("My backup drive is /dev/sdb1", "preference"),
    ("My name is Chris", "preference"),
    ("My theme is dark mode", "preference"),
    ("My favorite color is blue", "preference"),
    ("I prefer dark mode", "preference"),
    ("I use zsh", "preference"),
    ("I like tabs", "preference"),
    # Clear complaints → offer to assist (NOT store).
    ("My screen is too bright", "complaint"),
    ("My internet is slow", "complaint"),
    ("My computer is broken", "complaint"),
    ("My laptop is overheating", "complaint"),
    ("My disk is failing", "complaint"),
    # Ambiguous / not-a-declarative → abstain (fall through unchanged).
    ("My day is going well", None),
    ("My computer took forever to boot", None),
    ("What do you know about me?", None),
    ("I want my disk to stop crashing", None),
    ("Tell me a joke", None),
])
def test_classify_declarative(message, expected_kind):
    kind, _key, _value = MemoryManager.classify_declarative(message)
    assert kind == expected_kind


def test_preference_extracts_key_and_value():
    kind, key, value = MemoryManager.classify_declarative("My editor is vim")
    assert (kind, key, value) == ("preference", "editor", "vim")


def test_complaint_extracts_key_and_value():
    kind, key, value = MemoryManager.classify_declarative("My screen is too bright")
    assert kind == "complaint"
    assert key == "screen"
    assert value == "too bright"


@pytest.mark.parametrize("message", [
    "yes", "Yes please", "sure", "ok", "okay", "go ahead", "do it", "yep",
])
def test_is_affirmative(message):
    assert MemoryManager.is_affirmative(message)
    assert not MemoryManager.is_negative(message)


@pytest.mark.parametrize("message", [
    "no", "nope", "nah", "not now", "no thanks", "skip", "leave it",
])
def test_is_negative(message):
    assert MemoryManager.is_negative(message)
    assert not MemoryManager.is_affirmative(message)


@pytest.mark.parametrize("message", ["tell me a joke", "what time is it"])
def test_neither_affirmative_nor_negative(message):
    assert not MemoryManager.is_affirmative(message)
    assert not MemoryManager.is_negative(message)


@pytest.mark.parametrize("message", [
    "Show me everything you remember",
    "show me everything you know",
    "tell me all you know",
    "list everything you've stored",
    "what do you remember",
    "what all do you know",
    "what do you know about me",
    "what have you learned",
])
def test_is_transparency_request(message):
    # A dump-everything request must route to the transparency handler (memory
    # recall, no tool), not fall through to a tool dispatch.
    assert MemoryManager.is_transparency_request(message)


@pytest.mark.parametrize("message", [
    "remember everything I told you",      # imperative store, not a dump request
    "I know everything about you",         # user asserting, not asking
    "show me the disk usage",              # ordinary tool query
    "remember that my editor is vim",      # store request
    "do you remember to save files",       # not about stored facts
    "everything is broken on my computer",  # complaint
])
def test_is_not_transparency_request(message):
    assert not MemoryManager.is_transparency_request(message)


def test_clear_sessions_resets_session_state(tmp_path):
    # clear_sessions hard-deletes session rows so a session_awareness test can
    # reset to the pre-seed baseline between --repeat runs (WC residual 2b).
    m = MemoryManager(str(tmp_path / "memory.db"))
    m.start_session()
    m.record_turn("checked disk space", ["run_command"])
    m.end_session("checking disk space")
    m.start_session()
    assert m.get_last_session() is not None      # a completed session exists
    cleared = m.clear_sessions()
    assert cleared >= 1
    assert m.get_last_session() is None          # all sessions gone

    # facts are untouched by clear_sessions (it is session-only)
    m.store("editor", "vim")
    m.clear_sessions()
    assert m.count == 1
