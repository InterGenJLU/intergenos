# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""LocalRulesScanner — the always-on deterministic scanner floor.

No LLM, no network, microseconds — this is what makes "scan EVERY MCP
interaction" feasible offline (Sentinel design plan §2, impl #1). It is the
baseline every scan runs first; the deeper Local-Qwen / Cloud scanners only
run on FLAG or when depth=deep.

Pattern provenance (grounded in the existing code, not invented):
  * INGRESS injection patterns are a strict SUPERSET of the inline list in
    `SentinelGuard.validate_tool_description` (mcp_client.py) — the ten
    phrases there are all covered here, partitioned into BLOCK (unambiguous
    jailbreaks / control-token injection) vs FLAG (softer role-redefinition
    that can appear benignly), plus extensions.
  * The marker-spoof rule mirrors `spotlighting._SPOOF_GUARD_PATTERN`: content
    carrying an `UNTRUSTED-INGRESS` marker literal is an attempt to break out
    of the spotlight wrapper and is treated as high-confidence adversarial.
  * EGRESS rules detect real secret material (private keys, cloud keys, JWTs,
    crypt hashes, provider tokens) at BLOCK, and credential-shaped assignments
    / suspicious exfil URLs at FLAG.

(A future consolidation could have SentinelGuard delegate to this canonical
list so the two cannot drift; that wiring belongs to the chokepoint lane, not
here — this package ships independently off master.)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from intergen.interfaces.scanner import (
    Scanner,
    ScanContext,
    ScanDirection,
    ScanDisposition,
    ScanVerdict,
    most_severe,
)


@dataclass(frozen=True)
class _Rule:
    disposition: ScanDisposition
    category: str
    pattern: re.Pattern[str]
    reason: str


# Confidence score reported per disposition. Deterministic — the rules floor
# does not estimate; it classifies.
_SCORE = {
    ScanDisposition.ALLOW: 0.0,
    ScanDisposition.FLAG: 0.6,
    ScanDisposition.BLOCK: 0.95,
}


def _ci(p: str) -> re.Pattern[str]:
    return re.compile(p, re.IGNORECASE)


# --------------------------------------------------------------------------
# INGRESS — content arriving before it re-enters the LLM context (injection).
# --------------------------------------------------------------------------
_INGRESS_RULES: list[_Rule] = [
    # --- BLOCK: unambiguous instruction-override / jailbreak ---
    _Rule(ScanDisposition.BLOCK, "injection.override",
          _ci(r"ignore\s+(?:all\s+|any\s+)?(?:previous|prior|above|earlier|the\s+above)\s+instructions"),
          "Instruction-override directive ('ignore previous instructions')"),
    _Rule(ScanDisposition.BLOCK, "injection.override",
          _ci(r"disregard\s+(?:all\s+|any\s+|the\s+)?(?:previous|prior|above|earlier|your)\b"),
          "Instruction-override directive ('disregard previous/your ...')"),
    _Rule(ScanDisposition.BLOCK, "injection.override",
          _ci(r"forget\s+(?:your\s+instructions|everything|all\s+(?:previous|prior)|what\s+you\s+were\s+told)"),
          "Instruction-override directive ('forget your instructions')"),
    # --- BLOCK: chat-template / control-token injection ---
    _Rule(ScanDisposition.BLOCK, "injection.control-token",
          re.compile(r"<\|(?:im_start|im_end|system|user|assistant|endoftext)\|>"),
          "Chat-template control token injected in content"),
    # --- BLOCK: spotlight marker-spoof (mirrors spotlighting._SPOOF_GUARD_PATTERN) ---
    _Rule(ScanDisposition.BLOCK, "injection.marker-spoof",
          _ci(r"</?UNTRUSTED-INGRESS\b"),
          "Spotlight marker literal in content (wrapper break-out attempt)"),

    # --- FLAG: bare instruction-override forms (no anchor word) ---
    # SentinelGuard.validate_tool_description flags the bare substrings
    # "ignore previous" / "ignore above" / "disregard"; covering them at FLAG
    # keeps this floor a strict superset of that list while reserving BLOCK
    # for the unambiguous full directives above.
    _Rule(ScanDisposition.FLAG, "injection.override",
          _ci(r"\bignore\s+(?:the\s+)?(?:previous|above|prior|preceding)\b"),
          "Possible instruction-override ('ignore previous/above ...')"),
    _Rule(ScanDisposition.FLAG, "injection.override",
          _ci(r"\bdisregard\b"),
          "Possible instruction-override ('disregard ...')"),

    # --- FLAG: softer role-redefinition / suspicious phrasing ---
    _Rule(ScanDisposition.FLAG, "injection.role-redefine",
          _ci(r"\byou\s+are\s+now\b"),
          "Role-redefinition phrasing ('you are now ...')"),
    _Rule(ScanDisposition.FLAG, "injection.role-redefine",
          _ci(r"\bnew\s+instructions?\b"),
          "Possible injected directive ('new instructions')"),
    _Rule(ScanDisposition.FLAG, "injection.role-redefine",
          _ci(r"\bsystem\s+prompt\b"),
          "Reference to the system prompt in untrusted content"),
    _Rule(ScanDisposition.FLAG, "injection.role-redefine",
          _ci(r"\boverride\b"),
          "Override phrasing in untrusted content"),
    # --- FLAG: covert-action lures ---
    _Rule(ScanDisposition.FLAG, "injection.covert",
          _ci(r"\b(?:do\s+not|don'?t|never)\s+(?:tell|mention|inform|reveal\s+to)\s+(?:the\s+)?user"),
          "Covert-action lure ('do not tell the user')"),
    # --- FLAG: embedded tool-call lures ---
    _Rule(ScanDisposition.FLAG, "injection.tool-lure",
          _ci(r"\b(?:execute|run|invoke|call)\s+(?:the\s+)?(?:following\s+)?(?:command|tool|function|script)\b"),
          "Embedded tool/command lure"),
]


# --------------------------------------------------------------------------
# EGRESS — arguments leaving the machine toward an external surface (exfil).
# --------------------------------------------------------------------------
_EGRESS_RULES: list[_Rule] = [
    # --- BLOCK: real secret material present in outbound args ---
    _Rule(ScanDisposition.BLOCK, "secret.private-key",
          re.compile(r"-----BEGIN (?:RSA |DSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"),
          "Private key block in outbound arguments"),
    _Rule(ScanDisposition.BLOCK, "secret.aws-key",
          re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
          "AWS access key id in outbound arguments"),
    _Rule(ScanDisposition.BLOCK, "secret.jwt",
          re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
          "JSON Web Token in outbound arguments"),
    # Crypt password hashes. The scheme id may be followed by parameter
    # fields (rounds=, bcrypt cost, yescrypt/argon2 params) BEFORE the salt,
    # so we tolerate up to four '$'-delimited fields between the scheme id and
    # the base64-ish hash material rather than requiring the salt to follow
    # the id directly. Fields stop at ':' so we do not bleed across the
    # colon-delimited /etc/shadow record. (WC verify finding on 1ff3b482: the
    # original rule missed sha512+rounds, bcrypt+cost, and yescrypt — the
    # modern Debian/InterGenOS default — the EGRESS analog of the AI-4
    # not-a-true-superset catch.)
    _Rule(ScanDisposition.BLOCK, "secret.crypt-hash",
          re.compile(
              r"\$(?:1|2[abxy]?|5|6|7|y|gy|argon2(?:id|i|d)?|P|H)\$"  # crypt scheme id
              r"(?:[^$\s:]{1,72}\$){0,4}"                             # optional rounds/cost/params + salt
              r"[./A-Za-z0-9+=]{16,}"                                 # base64-ish hash material
          ),
          "Unix crypt password hash (/etc/shadow-style, incl. rounds/bcrypt/yescrypt/argon2) in outbound arguments"),
    # Provider API/access tokens. Distinctive vendor prefixes — adding a
    # common format is the same completeness principle as the crypt set (a
    # credential floor that misses a high-prevalence token is a real exfil
    # gap; trilateral-review finding on the engine). The long tail beyond these
    # stays covered by credential.assignment FLAG + the deeper scanners.
    _Rule(ScanDisposition.BLOCK, "secret.provider-token",
          re.compile(
              r"\b(?:"
              r"ghp_[A-Za-z0-9]{36}"                     # GitHub personal access token
              r"|github_pat_[A-Za-z0-9_]{22,}"           # GitHub fine-grained PAT
              r"|glpat-[0-9A-Za-z_-]{20,}"               # GitLab PAT
              r"|xox[baprs]-[A-Za-z0-9-]{10,}"           # Slack token
              r"|sk-[A-Za-z0-9]{20,}"                    # OpenAI-style key
              r"|(?:sk|rk)_(?:live|test)_[0-9A-Za-z]{24,}"  # Stripe secret/restricted key
              r"|AIza[0-9A-Za-z_-]{35}"                  # Google API key
              r"|npm_[A-Za-z0-9]{36}"                    # npm access token
              r"|SG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}"  # SendGrid API key
              r")"
          ),
          "Provider API/access token in outbound arguments"),

    # --- FLAG: credential-shaped assignments / suspicious destinations ---
    # Leading guard is (?:^|[^A-Za-z0-9]) rather than \b: an underscore-glued
    # prefix (PREFIX_API_KEY=...) has no word boundary before "API", so a \b
    # anchor silently skipped that whole assignment class (adversarial-corpus
    # finding, adopted 2026-07-23).
    _Rule(ScanDisposition.FLAG, "credential.assignment",
          _ci(r"(?:^|[^A-Za-z0-9])(?:api[_-]?key|secret(?:[_-]?key)?|password|passwd|access[_-]?token|auth[_-]?token)\b\s*[=:]\s*\S{6,}"),
          "Credential-shaped assignment in outbound arguments"),
    _Rule(ScanDisposition.FLAG, "credential.bearer",
          _ci(r"\bbearer\s+[A-Za-z0-9._-]{12,}"),
          "Bearer token in outbound arguments"),
    _Rule(ScanDisposition.FLAG, "exfil.relay-url",
          _ci(r"https?://(?:(?:[a-z0-9-]+\.)*(?:webhook\.site|requestbin\.[a-z]+|pipedream\.net|ngrok\.io|ngrok-free\.app|burpcollaborator\.net|interact\.sh|oast\.(?:fun|site|pro|live))\b"
              r"|hooks\.slack\.com/services/)"),  # FLAG not BLOCK: legitimate webhook uses exist
          "Suspicious exfil/relay URL in outbound arguments"),
    _Rule(ScanDisposition.FLAG, "exfil.raw-ip-url",
          re.compile(r"https?://\d{1,3}(?:\.\d{1,3}){3}\b"),
          "Outbound URL to a bare IP address"),
    _Rule(ScanDisposition.FLAG, "exfil.shadow-path",
          re.compile(r"/etc/shadow\b"),
          "Reference to /etc/shadow in outbound arguments"),
]


class LocalRulesScanner(Scanner):
    """Deterministic, always-on, on-device rules floor."""

    _RULES = {
        ScanDirection.INGRESS: _INGRESS_RULES,
        ScanDirection.EGRESS: _EGRESS_RULES,
    }

    @property
    def name(self) -> str:
        return "local-rules"

    @property
    def is_local(self) -> bool:
        return True

    def scan(self, content: str, ctx: ScanContext) -> ScanVerdict:
        if not content:
            return ScanVerdict.allow(scanner=self.name)

        rules = self._RULES.get(ctx.direction)
        if rules is None:
            # Fail CLOSED on an unknown direction. The enum is closed today so
            # this is unreachable, but it matches the fail-closed posture
            # everywhere else (trilateral-review nit) — never silently ALLOW.
            return ScanVerdict(
                disposition=ScanDisposition.FLAG,
                reason=f"unknown scan direction: {ctx.direction!r}",
                score=0.6,
                scanner=self.name,
                categories=["scanner.unknown-direction"],
            )

        disposition = ScanDisposition.ALLOW
        categories: list[str] = []
        reason = ""

        for rule in rules:
            if rule.pattern.search(content):
                # First match at a strictly-higher severity owns the reason
                # (most-severe-wins, and the strongest reason is the useful one).
                if rule.disposition.severity > disposition.severity:
                    reason = rule.reason
                disposition = most_severe(disposition, rule.disposition)
                if rule.category not in categories:
                    categories.append(rule.category)

        return ScanVerdict(
            disposition=disposition,
            reason=reason,
            score=_SCORE[disposition],
            scanner=self.name,
            categories=categories,
        )
