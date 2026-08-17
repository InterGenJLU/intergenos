# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Precision/recall/arg-validity harness for the locked-down 2B dispatch path.

Under the 2B dispatch lockdown the native LLM tool-decision path is gated off, so the
DETERMINISTIC matcher + code arg-extractor + explain gate are the SOLE dispatch path —
a corpus gap, a malformed arg, or a wrong-tool collision is a wrong/dropped ACTION the
model can no longer rescue. This measures the four lockdown-critical axes against the
REAL matcher/router/explain-gate, model-free wrt the 2B (the routing decision is
deterministic given the embedder):

  RECALL    : an imperative matches the correct intent (the action dispatches)
  ARG-VALID : the matched intent builds VALID args — set(args) == the tool's required
              params, NO catch-all {"query":...} for a non-search tool; an indeterminate
              write CORRECTLY returns None (clarify, never a truncating write)
  PRECISION : teaching routes to the EXPLAIN gate; a soft/polite imperative still
              DISPATCHES (the inverse misfire); conversational hits no action intent
  COLLISION : an ambiguous verb resolves to the RIGHT tool AND the loser does NOT also
              fire above threshold (resolved, not out-scored by luck)

Opt-in + embedder-gated like ws_harness: skipped unless INTERGEN_PR_HARNESS=1 and a
nomic-embed /v1/embeddings endpoint is reachable (INTERGEN_PR_EMBED_URL, default
http://127.0.0.1:8081/v1/embeddings). Labeled set folds WC's adversarial cases.

Run live (e.g. on a daemon box):
    INTERGEN_PR_HARNESS=1 python -m pytest intergen/tests/test_matcher_precision_recall.py -q
or as a report:
    INTERGEN_PR_HARNESS=1 python intergen/tests/test_matcher_precision_recall.py
"""
from __future__ import annotations

import json
import os
import unittest
import urllib.request

_ENABLED = os.environ.get("INTERGEN_PR_HARNESS") == "1"
_EMBED_URL = os.environ.get("INTERGEN_PR_EMBED_URL",
                            "http://127.0.0.1:8081/v1/embeddings")


def _embedder(texts):
    if not texts:
        return []
    payload = json.dumps({"input": list(texts), "model": "embedding"}).encode()
    req = urllib.request.Request(_EMBED_URL, data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    rows = data.get("data")
    if not isinstance(rows, list) or len(rows) != len(texts):
        return None
    return [r["embedding"] for r in sorted(rows, key=lambda r: r.get("index", 0))]


def _embedder_reachable() -> bool:
    try:
        return _embedder(["ping"]) is not None
    except Exception:
        return False


# intent_id -> dispatched tool (system_info is its own intent dispatching run_command)
_INTENT_TOOL = {
    "run_command": "run_command", "read_file": "read_file",
    "write_file": "write_file", "manage_packages": "manage_packages",
    "manage_services": "manage_services", "web_search": "web_search",
    "open_application": "open_application", "analyze_file": "analyze_file",
    "system_info": "run_command",
}
# Required arg keys per tool (must equal set(extracted_args), no extra 'query').
_REQUIRED_ARGS = {
    "run_command": {"command"}, "read_file": {"path"},
    "write_file": {"path", "content"}, "analyze_file": {"path"},
    "manage_packages": {"action"}, "manage_services": {"action"},
    "web_search": {"query"}, "open_application": {"name"},
}

# RECALL — imperative -> expected intent_id (incl. WC section 4 messy variants).
_RECALL = {
    "manage_packages": ["install firefox", "uninstall vlc", "remove the gimp package",
        "update all my packages", "is git installed", "get me neovim",
        "do I have docker installed", "show my installed packages"],
    "manage_services": ["restart the sshd service", "start bluetooth", "stop nginx",
        "is the firewall running", "enable docker on boot", "check if postgresql is active",
        "what services are running", "is docker running"],
    "run_command": ["run ls -la", "execute df -h", "list the printers",
        "show me the running processes", "could you pull up the running processes",
        "find the largest files",
        "put the contents of /var/log/syslog into /tmp/out.txt",
        "copy /tmp/a.txt to /tmp/b.txt"],
    "system_info": ["how much disk space do I have", "what kernel am I running",
        "how long has the system been up", "how much RAM is free",
        "what's eating my disk"],
    "read_file": ["show me /etc/fstab", "cat /etc/os-release", "what's in /var/log/syslog"],
    "write_file": ["write hello to /tmp/a.txt", "write hello world to /tmp/a.txt"],
    "analyze_file": ["explain /etc/fstab", "analyze the file /var/log/syslog for errors",
        "analyze /var/log/syslog for errors", "what does /etc/nsswitch.conf do"],
    "web_search": ["search the web for the latest kernel release", "google python asyncio",
        "look up this error online", "search the web for rust tutorials"],
    "open_application": ["open firefox", "launch the file manager",
        "open the application calculator", "start the terminal"],
}

# Section 1 positive controls — these MUST build valid args today.
_ARG_CONTROLS = [
    ("install firefox", "manage_packages", {"action", "package"}),
    ("restart sshd", "manage_services", {"action", "service"}),
    ("read /etc/hostname", "read_file", {"path"}),
    ("write hello to /tmp/a.txt", "write_file", {"path", "content"}),
    ("analyze the file /etc/fstab", "analyze_file", {"path"}),
]
# Full-tool-set arg validity — consumes _REQUIRED_ARGS across EVERY tool (not just
# the 5 inline controls above, the gap WC flagged: _REQUIRED_ARGS was defined but
# unconsumed). One representative dispatching query per tool; the assertion is that
# the tool's REQUIRED params are all present (optional specifics like package/service
# may be EXTRA) and no catch-all 'query' leaks into a non-search tool.
_ARG_FULL_SET = {
    "run_command": "run ls -la",
    "read_file": "read /etc/hostname",
    "write_file": "write hello to /tmp/a.txt",
    "analyze_file": "analyze the file /etc/fstab",
    "manage_packages": "install firefox",
    "manage_services": "restart sshd",
    "web_search": "search the web for rust tutorials",
    "open_application": "open firefox",
}
# Command-selector recall: run_command phrasings whose INTENT matches but which must
# also resolve to a non-empty COMMAND (else they drop at the arg layer under lockdown).
# All map to ratified AUTO-safe + netlink-free commands (lpstat/ps/du). ("what's my
# IP" is NOT here — it is handled by the route-level IP handler, covered in
# test_ip_answer.py, not the run_command selector.)
_RUN_CMD_SELECTOR = ["list the printers", "show me the running processes",
                     "find the largest files", "what's eating my disk"]
# Indeterminate writes — MUST return None (clarify), not a malformed/empty dispatch.
_ARG_CLARIFY_WRITES = [
    "save this to /tmp/notes.txt", "create the file /tmp/x.conf with these settings",
    "append this line to /etc/hosts", "put the contents of the log into /tmp/out.txt",
]
# Ungrounded run_command phrasings — the INTENT is command-class but NO specific
# command can be grounded: a kill needs a target, and "the disk check" is a
# description, not a command. The extractor MUST return None -> a freeform clarify
# (ask what to run), never a destructive guess (killing an arbitrary process) or a
# nonsense dispatch ("the disk check" -> a failing CONFIRM). Honest coverage: these
# CLARIFY, they do NOT dispatch (previously masked-recall — counted as a dispatch).
_RUN_CMD_CLARIFY = ["kill the hung process", "run the disk check"]
# File COPY — "put/copy the contents of SRC into/to DST" / "copy SRC to DST" build a
# CONFIRM-tier `cp SRC DST` (a copy, NOT a write_file of the literal words "the
# contents of SRC"); the user sees source->dest before it runs. (query -> command).
_COPY_DISPATCH = [
    ("put the contents of /var/log/syslog into /tmp/out.txt",
     "cp /var/log/syslog /tmp/out.txt"),
    ("copy /tmp/a.txt to /tmp/b.txt", "cp /tmp/a.txt /tmp/b.txt"),
    ("copy the contents of ~/notes.md to /tmp/n.md", "cp ~/notes.md /tmp/n.md"),
]
# Copy phrasings without clean paths -> clarify (None), never a guessed copy: "the
# log" is not a path, and "copy this to the clipboard" is not a file copy.
_COPY_CLARIFY = ["put the contents of the log into /tmp/out.txt",
                 "copy this to the clipboard"]

# Section 2 — teaching MUST explain; polite imperatives MUST dispatch (inverse misfire).
_TEACH_MUST_EXPLAIN = ["how do I install firefox", "what's the command to install firefox",
    "show me how to remove docker", "how would I restart the network service",
    "remind me how to open a terminal", "what do I type to search for a package",
    "how do I check disk space", "what command shows the kernel version",
    "how does systemd work", "explain how package management works"]
_POLITE_MUST_DISPATCH = [
    ("can you install firefox", "manage_packages"),
    ("go ahead and restart sshd", "manage_services"),
    ("just remove docker already", "manage_packages"),
]
_CONVERSATIONAL = ["what year did the berlin wall fall", "thanks, that's helpful",
    "who are you", "what is the capital of France"]

# Section 3 — verb collisions: winner intent, and losers that must NOT fire >= threshold.
_COLLISIONS = [
    ("search the web for rust tutorials", "web_search", ["manage_packages"]),
    ("start the terminal", "open_application", ["manage_services"]),
    ("open the firewall settings", "open_application", ["manage_services"]),
    ("list the printers", "run_command", ["manage_packages"]),
]
_COLLISION_THRESHOLD = 0.85


def _build():
    from intergen.semantic import SemanticMatcher
    from intergen.intents import register_all_intents
    from intergen.router import ConversationRouter
    from intergen.howto import HowtoCorpus
    m = SemanticMatcher(embedder=_embedder)
    register_all_intents(m)
    r = ConversationRouter.__new__(ConversationRouter)
    r._semantic = m
    r._conversation_history = []
    r._max_history = 20
    r._pending_action_offer = None
    r._reference = None
    try:
        r._howto = HowtoCorpus(embedder=_embedder, reference=None)
    except Exception:
        r._howto = None
    return m, r


def _explain_serves(r, q) -> bool:
    if r._howto is None:
        return False
    res, _prior = r._try_explain(q)
    return bool(res is not None and getattr(res, "handled", False)
                and getattr(res, "source", None) == "explain")


def _loser_standalone_score(m, q, loser) -> float:
    """Max cosine of the (normalized) query against the LOSER intent's own example
    embeddings — the score the loser would get on its own, i.e. on a phrasing where
    the winner's keyword precedence does not apply. Below _COLLISION_THRESHOLD proves
    the collision is ROBUSTLY resolved, not luck-resolved by a fragile 0.84/0.86 split.

    FORCED-FAIL when the score cannot be computed (per-query embed miss, missing or
    empty loser-intent embeddings): returns +inf so the assertLess FAILS rather than
    false-passing. A 0.0 here would be < threshold and silently pass an unverified
    collision — the dead-constant class all over again."""
    import numpy as np
    qe = m._embed([m._normalize_input(q)])
    ei = m._embedding_intents.get(loser)
    emb = getattr(ei, "embeddings", None) if ei is not None else None
    # len() (not truthiness) — emb is a numpy array, whose bool() is ambiguous.
    if qe is None or emb is None or len(emb) == 0:
        return float("inf")
    qe = qe[0]
    return max(float(np.dot(qe, e) / (np.linalg.norm(qe) * np.linalg.norm(e)))
               for e in emb)


@unittest.skipUnless(_ENABLED and _embedder_reachable(),
                     "INTERGEN_PR_HARNESS=1 + a reachable nomic-embed required")
class MatcherPrecisionRecallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m, cls.r = _build()

    def test_recall(self):
        for expected, queries in _RECALL.items():
            for q in queries:
                iid = getattr(self.m.match(q), "intent_id", None)
                self.assertEqual(iid, expected, f"recall: {q!r} -> {iid}")

    def test_arg_validity_controls(self):
        for q, tool, required in _ARG_CONTROLS:
            args = self.r._extract_arguments(tool, q)
            self.assertIsInstance(args, dict, f"{q!r}")
            self.assertNotIn("query", args, f"{q!r} got catch-all query")
            self.assertEqual(set(args), required, f"{q!r} -> {args}")

    def test_arg_validity_full_tool_set(self):
        # Consume _REQUIRED_ARGS across the FULL tool set, not just the 5 inline
        # controls (WC: the constant was defined-but-unconsumed). Every tool's
        # required params must be present and no catch-all 'query' leaks into a
        # non-search tool — a malformed dispatch is a wrong ACTION under lockdown.
        for tool, q in _ARG_FULL_SET.items():
            required = _REQUIRED_ARGS[tool]
            args = self.r._extract_arguments(tool, q)
            self.assertIsInstance(args, dict, f"{tool}: {q!r} -> {args}")
            if tool != "web_search":
                self.assertNotIn("query", args, f"{tool}: {q!r} got catch-all query")
            self.assertTrue(
                required.issubset(set(args)),
                f"{tool}: {q!r} -> {args}, missing required {required - set(args)}")

    def test_run_command_selector(self):
        from intergen.safety import classify_command
        from intergen.interfaces.types import SafetyTier
        for q in _RUN_CMD_SELECTOR:
            args = self.r._extract_arguments("run_command", q)
            self.assertIsInstance(args, dict, f"selector dropped {q!r} -> None")
            command = str(args.get("command", "")).strip()
            self.assertTrue(command, f"{q!r} -> {args} (no command)")
            # TIER + sanity (WC): asserting non-empty is not enough — a non-empty
            # but CONFIRM command (e.g. a du|sort|head pipeline where sort is not in
            # the AUTO tier) runs behind a needless confirm prompt = coverage !=
            # capability, and a nonsense command would not classify AUTO either.
            # The selector's commands are read-only queries, so they MUST be AUTO.
            tier = classify_command(command)
            self.assertEqual(
                tier, SafetyTier.AUTO,
                f"{q!r} -> {command!r} classified {tier}, expected AUTO "
                f"(a read-only selector command must auto-run, not gate behind confirm)")

    def test_indeterminate_writes_clarify(self):
        for q in _ARG_CLARIFY_WRITES:
            self.assertIsNone(self.r._extract_arguments("write_file", q),
                              f"indeterminate write must clarify: {q!r}")

    def test_ungrounded_run_command_clarifies(self):
        # Command-class intent but no groundable command -> clarify (None), never a
        # destructive guess or a nonsense dispatch (the masked-recall fix).
        for q in _RUN_CMD_CLARIFY:
            self.assertIsNone(
                self.r._extract_arguments("run_command", q),
                f"ungrounded run_command must clarify (None), not dispatch: {q!r}")

    def test_copy_contents_dispatches_cp(self):
        from intergen.safety import classify_command
        from intergen.interfaces.types import SafetyTier
        for q, expected in _COPY_DISPATCH:
            args = self.r._extract_arguments("run_command", q)
            self.assertIsInstance(args, dict, f"{q!r} -> {args}")
            self.assertEqual(args.get("command"), expected, f"{q!r} -> {args}")
            # cp must be CONFIRM-tier — the user sees source->dest before it runs.
            self.assertEqual(classify_command(expected), SafetyTier.CONFIRM,
                             f"cp must be CONFIRM: {expected!r}")

    def test_copy_without_clean_paths_clarifies(self):
        for q in _COPY_CLARIFY:
            self.assertIsNone(
                self.r._extract_arguments("run_command", q),
                f"unclear copy must clarify (None), not guess: {q!r}")

    def test_teaching_routes_to_explain(self):
        for q in _TEACH_MUST_EXPLAIN:
            self.assertTrue(_explain_serves(self.r, q),
                            f"teaching must explain: {q!r}")

    def test_polite_imperatives_dispatch(self):
        for q, expected in _POLITE_MUST_DISPATCH:
            self.assertFalse(_explain_serves(self.r, q), f"must not teach: {q!r}")
            iid = getattr(self.m.match(q), "intent_id", None)
            self.assertEqual(_INTENT_TOOL.get(iid), _INTENT_TOOL[expected], f"{q!r} -> {iid}")

    def test_conversational_no_action(self):
        for q in _CONVERSATIONAL:
            iid = getattr(self.m.match(q), "intent_id", None)
            self.assertIsNone(iid, f"conversational must not match an action: {q!r} -> {iid}")

    def test_collisions_resolved(self):
        for q, winner, losers in _COLLISIONS:
            iid = getattr(self.m.match(q), "intent_id", None)
            self.assertEqual(iid, winner, f"collision winner: {q!r} -> {iid}")
            # Resolved, NOT luck-resolved (WC): the loser must be genuinely BELOW the
            # match threshold on its own standalone score, so it would not fire on a
            # phrasing where the winner's keyword precedence does not apply. A bare
            # assertNotEqual would false-pass a fragile loser=0.84 / winner=0.86 split.
            for loser in losers:
                self.assertNotEqual(iid, loser, f"loser fired: {q!r} -> {loser}")
                score = _loser_standalone_score(self.m, q, loser)
                self.assertLess(
                    score, _COLLISION_THRESHOLD,
                    f"{q!r}: loser {loser} standalone {score:.3f} >= "
                    f"{_COLLISION_THRESHOLD} — luck-resolved, not robust")


def _report():
    m, r = _build()
    def rate(name, passed, total):
        print(f"{name}: {passed}/{total} = {100*passed/total:.1f}%" if total else f"{name}: n/a")
    rp = sum(getattr(m.match(q), "intent_id", None) == e
             for e, qs in _RECALL.items() for q in qs)
    rt = sum(len(v) for v in _RECALL.values())
    rate("RECALL", rp, rt)
    ap = 0
    for q, tool, req in _ARG_CONTROLS:
        a = r._extract_arguments(tool, q)
        ap += isinstance(a, dict) and "query" not in a and set(a) == req
    rate("ARG-CONTROLS", ap, len(_ARG_CONTROLS))
    cw = sum(r._extract_arguments("write_file", q) is None for q in _ARG_CLARIFY_WRITES)
    rate("CLARIFY-WRITES", cw, len(_ARG_CLARIFY_WRITES))
    tp = sum(_explain_serves(r, q) for q in _TEACH_MUST_EXPLAIN)
    rate("TEACH->EXPLAIN", tp, len(_TEACH_MUST_EXPLAIN))
    pp = sum((not _explain_serves(r, q)) and
             _INTENT_TOOL.get(getattr(m.match(q), "intent_id", None)) == _INTENT_TOOL[e]
             for q, e in _POLITE_MUST_DISPATCH)
    rate("POLITE->DISPATCH", pp, len(_POLITE_MUST_DISPATCH))
    cp = sum(getattr(m.match(q), "intent_id", None) == w for q, w, _ in _COLLISIONS)
    rate("COLLISIONS", cp, len(_COLLISIONS))


def _bare_router():
    """A router constructed without the embedder/daemon — enough for the pure
    code-owned arg-extraction + synthesis helpers (no _semantic needed on the
    manage_packages path)."""
    from intergen.router import ConversationRouter
    return ConversationRouter.__new__(ConversationRouter)


class ManagePackagesArgExtraction(unittest.TestCase):
    """Extracted intents, never raw user sentences (2026-07-14). Observed in a
    live test session: "Can you update this system for me?" had no update branch,
    fell to the search fallback, and pkm searched the literal SENTENCE
    (query="update this system for me?") — a nonsense dispatch the 2B narrated."""

    def setUp(self):
        self.r = _bare_router()

    def test_update_system_maps_to_whole_system_update(self):
        for q in ("Can you update this system for me?", "update this system",
                  "update all my packages", "upgrade everything",
                  "update my system", "upgrade all packages", "update"):
            a = self.r._extract_arguments("manage_packages", q)
            self.assertEqual(a, {"action": "update"}, f"{q!r} -> {a}")

    def test_update_named_package_targets_it(self):
        for q, pkg in (("update firefox", "firefox"),
                       ("upgrade vlc", "vlc"),
                       ("update the gimp package", "gimp")):
            a = self.r._extract_arguments("manage_packages", q)
            self.assertEqual(a, {"action": "update", "package": pkg}, f"{q!r} -> {a}")

    def test_raw_sentence_never_becomes_the_search_query(self):
        # The core honesty guarantee: no extraction returns the whole sentence as
        # the pkm query. Either a real action, a clean term, or None — never the
        # captured query="update this system for me?".
        for q in ("Can you update this system for me?",
                  "please update everything on this machine",
                  "how do I keep this thing current"):
            a = self.r._extract_arguments("manage_packages", q)
            if a is not None:
                self.assertNotEqual(a.get("query"), q, f"raw sentence leaked: {q!r}")

    def test_explicit_search_extracts_clean_term(self):
        for q, term in (("search for a markdown editor", "markdown editor"),
                        ("find vlc", "vlc"),
                        ("search for the htop package", "htop")):
            a = self.r._extract_arguments("manage_packages", q)
            self.assertEqual(a, {"action": "search", "query": term}, f"{q!r} -> {a}")

    def test_named_package_search_still_works(self):
        a = self.r._extract_arguments("manage_packages", "is there a package for docker")
        self.assertEqual(a, {"action": "search", "query": "docker"})

    def test_no_groundable_term_declines_rather_than_search_sentence(self):
        # No search verb + no update/list/info signal -> None (route on), never a
        # raw-sentence search.
        for q in ("can you help me with packages", "what about packages"):
            self.assertIsNone(self.r._extract_arguments("manage_packages", q), q)


class ToolResultPreambleStrip(unittest.TestCase):
    """A weak 2B can PARROT the synthesis framing verbatim — observed in a live
    test session: "The tool returned: No repository index yet. …" reached the
    user. Persona rendering is enforced in code, not hoped for: preamble stripped."""

    def setUp(self):
        self.strip = _bare_router()._strip_tool_result_preamble

    def test_strips_parroted_preambles(self):
        cases = [
            ("The tool returned: No repository index yet. Run 'sudo pkm sync'.",
             "No repository index yet. Run 'sudo pkm sync'."),
            ("Tool 'manage_packages' returned: 812 packages installed.",
             "812 packages installed."),
            ("The command returned: /dev/sda1 80% full",
             "/dev/sda1 80% full"),
            ("the manage_services tool reported: sshd is active",
             "sshd is active"),
        ]
        for raw, expected in cases:
            self.assertEqual(self.strip(raw), expected, f"{raw!r}")

    def test_real_sentences_are_untouched(self):
        # No colon after returned/output -> a genuine sentence, never stripped.
        for s in ("The command returned 3 errors during the scan.",
                  "Your disk is 80% full.",
                  "The tool ran and everything looks healthy.",
                  "pkm sync downloaded the repository index."):
            self.assertEqual(self.strip(s), s, f"{s!r}")

    def test_bare_preamble_is_left_intact(self):
        # Stripping to empty is a no-op — never blank the message silently.
        self.assertEqual(self.strip("The tool returned:"), "The tool returned:")


if __name__ == "__main__":
    _report()
