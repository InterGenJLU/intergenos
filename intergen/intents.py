# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""InterGen intent registration — wires tools into the semantic matcher.

Registers keyword patterns (Layer 1) and semantic examples (Layer 2)
for each of the 7 core tools. This is the glue between user input
and tool dispatch — without it, everything falls through to P3 LLM
tool calling.
"""

from __future__ import annotations

from intergen.semantic import SemanticMatcher


# WD-1 (2026-07-09): an optional leading courtesy / show-me / imperative prefix so a
# PREFIXED explicit web-search dispatch ("show me search the web for …", "if you don't
# mind, search the web for …") still anchors on the web-search keyword patterns instead
# of landing in freeform. Whitelisted leads ONLY — a non-dispatch lead ("why would you",
# "don't", "i wouldn't") is deliberately absent, so a non-dispatch sentence that merely
# contains "search the web" is not captured. Kept ^-anchored (a prefix, not free-floating).
_WEB_DISPATCH_LEAD = (
    r"(?:(?:please|kindly|hey|ok|okay|so|now|well|could you|can you|would you|"
    r"will you|i want you to|i'?d like you to|i'?d like to|i need you to|show me|"
    r"if you don'?t mind,?|would you mind,?)[,\s]+)*")


# Boot/startup performance complaints → real boot timing (systemd-analyze), not
# a fabricated or deflected answer. ONE shared pattern, referenced by BOTH the
# system_info intent gate (_register_system_info) and the command selector
# (router._natural_language_to_command), so the two can never drift: a complaint
# that routes to the intent is guaranteed to also resolve to a command. (They
# HAD drifted — the intent allowed a 15-char boot↔slow gap, the selector only
# 12, so "boot is extremely slow" matched the intent but the selector missed it
# and the turn deflected to the LLM.) Gaps stay bounded so a "boot…slow" form
# can't span unrelated clauses; a bare "boot" key is avoided to dodge "reboot".
BOOT_PERF_COMPLAINT_PATTERN = (
    r"(?:took|taking|takes).{0,15}to\s+(?:boot|start)"
    r"|(?:slow|sluggish|long|forever|laggy).{0,10}(?:boot|start)"
    r"|(?:boot|start ?up|startup|booting).{0,15}"
    r"(?:slow|sluggish|long|forever|ages|takes|took)"
    r"|\bboot\s+time\b"
)


def register_all_intents(matcher: SemanticMatcher) -> None:
    """Register all keyword and semantic intents for core tools."""
    _register_run_command(matcher)
    _register_read_file(matcher)
    _register_write_file(matcher)
    _register_manage_packages(matcher)
    # open_application BEFORE manage_services: its keyword carries an explicit
    # app-name list (terminal/firefox/calculator/…), so an ambiguous "start <X>"
    # resolves to the app launch when X is a known app ("start the terminal"),
    # while a service name not in the list ("start the ssh service") falls
    # through to manage_services. P1 keyword returns the FIRST registered match,
    # so the more-specific intent must register first. P2 is an argmax over the
    # candidates that clear their own threshold, which is order-free — it was NOT
    # order-free while a higher-scoring ineligible candidate could displace an
    # eligible one; see intergen/tests/test_semantic_candidate_integrity.py.
    _register_open_application(matcher)
    _register_manage_services(matcher)
    _register_web_search(matcher)
    _register_system_info(matcher)
    _register_analyze_file(matcher)


def _register_run_command(matcher: SemanticMatcher) -> None:
    matcher.register_keyword_pattern(
        "run_command",
        [
            r"^run\s+",
            r"^execute\s+",
            r"^shell\s+",
            r"^\$\s+",
            r"^kill\s+",
            r"^find\s+(?:the\s+|all\s+|my\s+)?(?:largest|biggest|big|hidden)\b",
            # File COPY -> run_command(cp ...): "put/copy/move the contents of X
            # into/to Y", "copy X to /Y". Routed here (the extractor builds a
            # CONFIRM-tier `cp src dst`, or clarifies if src/dst aren't clean paths)
            # NOT to write_file, which would write the literal words "the contents
            # of X". The dest "/"-anchor on the bare-copy form avoids stealing
            # "copy this to the clipboard".
            r"^(?:put|copy|move)\s+(?:the\s+)?contents?\s+of\s+.+\b(?:in)?to\s+\S",
            r"^copy\s+\S+\s+(?:in)?to\s+~?/\S",
        ],
        tool_name="run_command",
    )
    matcher.register_intent(
        "run_command",
        [
            "run this command",
            "execute this in the terminal",
            "run a shell command",
            "can you run",
            "execute the following",
            "type this in the terminal",
            # action-surface recall (grounded misses, 2B embedder)
            "list the printers",
            "show me the running processes",
            "list the available printers",
            "what processes are running",
            "pull up the running processes",
            "kill the hung process",
            "find the largest files",
            "run the disk check",
        ],
        threshold=0.88,
        tool_name="run_command",
    )


def _register_read_file(matcher: SemanticMatcher) -> None:
    matcher.register_keyword_pattern(
        "read_file",
        [
            r"^(?:show|read|cat|display|print|view)\s+(?:me\s+)?(?:the\s+)?(?:file\s+|contents?\s+(?:of\s+)?)?/",
            r"^what(?:'s| is) in\s+/",
            r"^cat\s+/",
        ],
        tool_name="read_file",
    )
    matcher.register_intent(
        "read_file",
        [
            "show me the contents of this file",
            "read this file",
            "what's in this file",
            "display the file",
            "cat this file",
            "let me see that config file",
            "open this file and show me",
            "print the log file",
        ],
        threshold=0.88,
        tool_name="read_file",
    )


def _register_write_file(matcher: SemanticMatcher) -> None:
    matcher.register_keyword_pattern(
        "write_file",
        [
            r"^(?:write|save|create|edit|modify|update|change)\s+(?:the\s+)?(?:file\s+)?/",
            r"^append\s+to\s+/",
            # "<verb> <content> to /path" — the natural form ("save this to
            # /tmp/notes.txt", "append this line to /etc/hosts") where content
            # sits between the verb and the path, so the path is not adjacent.
            r"^(?:write|save|append)\s+.*\bto\s+/\S",
        ],
        tool_name="write_file",
    )
    matcher.register_intent(
        "write_file",
        [
            "write this to a file",
            "save this configuration",
            "create a new file with this content",
            "edit this config file",
            "modify the settings file",
            "update the configuration",
            "add this line to the file",
            "change the value in this file",
            # action-surface recall (grounded misses, 2B embedder)
            "save this to a file",
            "write this text to a file",
            "append this line to the file",
            "save this note to a file",
        ],
        threshold=0.90,
        tool_name="write_file",
    )


def _register_manage_packages(matcher: SemanticMatcher) -> None:
    matcher.register_keyword_pattern(
        "manage_packages",
        [
            r"^pkm\s+",
            r"^(?:install|remove|uninstall|update)\s+(?:package\s+)?",
            # "search" is split out with a negative lookahead so a WEB search
            # ("search the web/online/internet for ...") is NOT stolen by the
            # package-search keyword (it was — registration order put packages
            # before web_search). A package search ("search for a markdown
            # editor") still matches.
            r"^search\s+(?!(?:the\s+)?(?:web|internet|online)\b)(?:for\s+)?(?:a\s+|the\s+)?(?:package\s+)?",
            r"^(?:get|grab|fetch|give)\s+(?:me\s+)?(?:the\s+)?\w+",
            r"^what packages?\s+",
            r"^list\s+(?:installed\s+)?packages?",
            r"^show\s+(?:me\s+)?(?:my\s+|all\s+)?(?:installed\s+)?packages?\b",
            r"^is\s+\w+\s+installed",
            r"^is\s+there\s+a\s+package",
            r"^do\s+(?:you|we|i)\s+have\s+a\s+package",
            # explicit "...package..." version/info queries belong to pkm, not
            # run_command's uname (which answers the kernel string, not the
            # package release) — anchored on the word "package" to stay narrow
            r"^what\s+version\s+of\s+.+\bpackage",
        ],
        tool_name="manage_packages",
    )
    matcher.register_intent(
        "manage_packages",
        [
            "install a package",
            "remove this package",
            "search for a package",
            "list installed packages",
            "is this package installed",
            "what packages do I have",
            "uninstall this software",
            "find a package called",
            "show package information",
            "check what version is installed",
            # Messy/fragment examples
            "install firefox",
            "do I have git?",
            "is python installed",
            "get me htop",
            "remove that package",
            "what version of gcc",
            # action-surface recall (grounded misses, 2B embedder)
            "do I have docker installed",
            "add the obs-studio package",
            "is docker installed",
            "add a package",
            "show my installed packages",
            "show me what packages are installed",
        ],
        threshold=0.85,
        tool_name="manage_packages",
    )


def _register_manage_services(matcher: SemanticMatcher) -> None:
    matcher.register_keyword_pattern(
        "manage_services",
        [
            r"^(?:start|stop|restart|enable|disable)\s+(?:the\s+)?(?:service\s+)?",
            r"^(?:is|check)\s+\w+\s+(?:running|active|enabled)",
            r"^(?:service|systemctl)\s+",
            r"^(?:status\s+(?:of\s+)?)",
            r"what services?\s+are\s+running",
        ],
        tool_name="manage_services",
    )
    matcher.register_intent(
        "manage_services",
        [
            "start the ssh service",
            "stop the web server",
            "restart NetworkManager",
            "is the firewall running",
            "check if this service is active",
            "enable this service on boot",
            "disable the bluetooth service",
            "what services are running",
            "show the status of",
            "list all active services",
            # Messy/fragment examples
            "is ssh running?",
            "restart network",
            "stop bluetooth",
            "services?",
            "is nginx up",
            "start sshd",
            # Lexical variations
            "could you check on the web server",
            "I'm worried about the database service",
            "make sure ssh is still going",
            "kick nginx back up",
            "is my firewall doing its thing",
            # action-surface recall (grounded misses, 2B embedder)
            "check if postgresql is active",
            "bring the web server back up",
            "is postgresql active",
            "bring nginx back up",
        ],
        threshold=0.85,
        tool_name="manage_services",
    )


def _register_web_search(matcher: SemanticMatcher) -> None:
    matcher.register_keyword_pattern(
        "web_search",
        [
            r"^(?:search|google|look up|find online)\s+",
            r"^what is the latest\s+",
            # WD-1: tolerate a leading courtesy/show-me prefix before the explicit
            # web-search dispatch (the clean unprefixed form still matches — the lead
            # group is optional/zero-width).
            r"^" + _WEB_DISPATCH_LEAD + r"search the web for\s+",
            # web-anchored search ("search the web/internet/online ...") — the
            # counterpart to the package-search narrowing, so a web search lands
            # here deterministically rather than depending on registration order.
            r"^" + _WEB_DISPATCH_LEAD + r"search\s+(?:the\s+)?(?:web|internet|online)\b",
            # "find <...> online" where the path between is not adjacent.
            r"^find\b.*\bonline\b",
        ],
        tool_name="web_search",
    )
    matcher.register_intent(
        "web_search",
        [
            "search the web for",
            "look this up online",
            "google this",
            "find information about",
            "search for the latest",
            "what does the internet say about",
            "look up this error message",
            "find a solution online",
            # action-surface recall (grounded miss)
            "find information about this online",
            "find information about this topic online",
        ],
        threshold=0.90,
        tool_name="web_search",
    )


def _register_open_application(matcher: SemanticMatcher) -> None:
    matcher.register_keyword_pattern(
        "open_application",
        [
            r"^(?:open|launch|start|run)\s+(?:the\s+)?(?:app(?:lication)?\s+)?(?:firefox|chrome|terminal|nautilus|files|settings|tweaks|code|vscode|gimp|inkscape|thunderbird|libreoffice|calculator|calendar|camera|clocks|characters)",
            r"^(?:open|launch|start)\s+(?:the\s+)?(?:file manager|text editor|browser|email)",
            # generic "open the application <name>" — explicit app-launch framing
            r"^(?:open|launch)\s+the\s+app(?:lication)?\s+\w+",
            # "open the <X> settings" / "open settings" — a settings panel is an
            # app launch, not a service action ("open the firewall settings" must
            # NOT hit manage_services).
            r"^(?:open|launch|show)\s+(?:the\s+)?(?:\w+\s+)?settings\b",
        ],
        tool_name="open_application",
    )
    matcher.register_intent(
        "open_application",
        [
            "open the file manager",
            "launch firefox",
            "start the terminal",
            "open settings",
            "launch the text editor",
            "open the browser",
            "start VS Code",
            "open GIMP",
            "launch the email client",
            # action-surface recall (grounded miss)
            "open the application calculator",
            "open calculator",
            "launch the calculator app",
            "open the firewall settings",
            "open the settings",
        ],
        threshold=0.90,
        tool_name="open_application",
    )


def _register_system_info(matcher: SemanticMatcher) -> None:
    """System information queries that route to run_command with specific commands."""
    matcher.register_keyword_pattern(
        "system_info",
        [
            # LEG 1 (wave-7): a live-state ask needs a possessive/quantity signal —
            # "how much <noun>" or "what is MY <noun>". The old optional (?:my )? let a
            # DEFINITIONAL "what is memory / what is disk" (a teach ask) route to
            # run_command live-state; require the signal so the teach reading falls
            # through to the model. "what is a kernel" (article) never matched here.
            r"^(?:how much\s+(?:my\s+)?|what(?:'s| is)\s+my\s+)(?:disk|storage|space|memory|ram|cpu|uptime|hostname|kernel|ip|os|operating system|arch|gpu)",
            r"^(?:show|check|display|get)\s+(?:me\s+)?(?:my\s+)?(?:disk|storage|memory|ram|cpu|system|hostname|kernel|ip|network)\s*(?:usage|info|space|status|address|version)?",
            r"^(?:free|df|du|top|htop|uname|uptime|hostname|lscpu|lsblk|lspci|lsusb|ip\s+addr)\b",
            r"^system\s+(?:info|status|health)",
            r"what(?:'s| is) my (?:host|ip|kernel|os|arch|gpu|hostname|ip address)",
            # External/public-IP classifier parity (FACE 154/155 -> 155/155): the
            # internal-IP frame above matches "what's my ip [address]" but a scope
            # qualifier between "my" and "ip" ("my external ip", "my public ip
            # address") broke it, so classification MISSED the external-IP asks even
            # though the route-level handler (router._answer_ip_query) answers both
            # internal and external. Recognize the possessive scope+ip form at the
            # classifier layer, in parity with router._IP_QUERY_RE. The leadin +
            # my/your possessive anchor keeps definitional ("what is an external ip")
            # and the how-to lead-in ("how do I find my public ip", which teaches)
            # out. Runtime is unchanged — _is_ip_query still preempts at the route.
            r"\b(?:what(?:'s| is)|show me|get|check|display)\s+(?:my|your)\s+(?:current|local|external|public|private|internal|wan|lan)\s+ip(?:\s+address)?\b",
            r"(?:hostname|kernel version|ip address|os version|what kernel)",
            r"how long.*(?:running|been up|uptime|been on)",
            r"what(?:'s| is) this (?:box|machine|computer|system) called",
            r"(?:name of|identify) (?:this |my )?(?:machine|computer|box|system)",
            # "machine name?" / "computer name" word order (inverse of "name of
            # <thing>" above) — missed both layers and fell to the 2B, which ran a
            # firewalld status. (dyno lex_hostname_terse.)
            r"\b(?:machine|computer|box|system|host)\s+name\b",
            r"(?:how full|filling up|running out|space left)",
            r"(?:do i have enough|running low on) (?:disk|space|storage|memory|ram)",
            # "what's eating/using/hogging my disk" -> du (what is USING the space).
            r"(?:eating|using|hogging|taking up|chewing up|filling up)\s+(?:up\s+)?(?:my\s+|the\s+)?(?:disk|space|storage|drive)",
            # "space" as a disk synonym — low collision, kept broad.
            r"how much space\b",
            r"\bspace\s+(?:left|free|available|remaining)\b",
            # "room" as a disk synonym — anchored to a disk/quantity-left signal so
            # metaphors ("how much room for improvement", "no room left in the
            # schedule", "enough room to dance") do NOT classify as system_info,
            # while "how much room do I have left" still does. (WC room-collision
            # LOW; dyno lex_disk_natural.)
            r"how much room\b.*\b(?:left|free|do i have|i have|disk|drive|storage)\b",
            r"\broom\s+(?:left|free|available|remaining)\s+on\b",
            # ── FACE coverage (Bucket A, 2026-07-01): grounded system-state
            # siblings that slipped past the patterns above. Each is anchored to a
            # self-referential / possessive signal ("do i have", "my", "this
            # computer/machine", "am i", "is this") so shopping and comparison
            # forms ("what cpu should I buy", "how many cores does an apple have",
            # "how much does more RAM cost", "how do I overclock") do NOT classify
            # as system_info — they teach or fall through. The selector
            # (_natural_language_to_command) resolves each to an AUTO command.
            r"\bwhat version (?:am i|is this|of (?:the )?(?:os|system|distro))\b",
            r"\bwhat os (?:is this|am i|are we)\b",
            r"\b(?:is (?:my|this)|am i)\b.*\b(?:32|64)[\s-]?bit\b",
            r"\b(?:32 or 64|64 or 32)[\s-]?bit\b",
            r"\b(?:my|this) (?:cpu|processor|system) architecture\b",
            r"\b(?:what|which) (?:cpu|processor) (?:do i have|have i|is (?:in |this)|does this)\b",
            r"\bhow many (?:cores|cpus|threads|processors) (?:do i|does (?:this|my)|have i|are in (?:this|my))\b",
            r"\bmy (?:hardware|specs|spec sheet)\b",
            r"\b(?:computer'?s?|machine'?s?|pc'?s?|system'?s?|rig'?s?|laptop'?s?)\s+(?:hardware|specs|spec sheet)\b",
            r"\b(?:show me|what are)\b.{0,20}\b(?:hardware|specs|spec sheet)\b",
            r"\bfree space\b",
            r"\btaking up (?:all )?(?:my |the )?(?:disk|space|storage|drive)\b",
            r"\bgraphics card\b.*\b(?:do i have|i have|is in|does this|check)\b",
            r"\bmy graphics card\b",
            r"\bcpu usage\b",
            r"\bwhy is my (?:cpu|processor)\b",
            r"\b(?:cpu|processor) (?:is |running )?(?:so |too )?high\b",
            # Time of day -> `date` (PI-218-3/-4). "what time is it" otherwise fell
            # to the ~50s llm_tools path on the slow iGPU and mis-selected
            # take_screenshot / read_file(/usr/bin/time) (.218 trace). \btime\b has
            # no word boundary inside "uptime", so this never hijacks an uptime ask.
            r"\bwhat(?:'s| is)?\s+(?:the\s+)?time\b",
            r"\btime is it\b",
            r"\bcurrent time\b",
            r"\btime of day\b",
            # Boot/startup performance complaints → real boot timing
            # (systemd-analyze), not a fabricated or deflected answer. These are
            # the deterministic gate; _natural_language_to_command picks the
            # command. "my computer took forever to boot" lands here instead of
            # mis-classifying as identity (the pronoun-ish "computer") → freeform.
            # ONE shared pattern with the selector so the two cannot drift.
            BOOT_PERF_COMPLAINT_PATTERN,
        ],
        tool_name="run_command",
    )
    matcher.register_intent(
        "system_info",
        [
            # Clean examples
            "how much disk space do I have",
            "what's my memory usage",
            "show me CPU information",
            "check system uptime",
            "how much RAM is free",
            "show disk usage",
            "what's my system status",
            "display system information",
            "how much storage is left",
            "check system health",
            "what is my hostname",
            "what kernel am I running",
            "what's my IP address",
            "show me my network interfaces",
            "what OS am I running",
            "what GPU do I have",
            "show me USB devices",
            "list my block devices",
            # Messy/fragment examples (real user input)
            "disk full?",
            "hostname?",
            "how much ram",
            "check disk",
            "my ip",
            "whats my hostname",
            "kernel version",
            "am I running out of space",
            "memory low",
            "is my disk full",
            # Lexical variations (casual, slang, indirect)
            "what's this box called",
            "name of this machine",
            "what do they call this computer",
            "am I filling up",
            "got enough space",
            "is storage getting tight",
            "how's my disk looking",
            "do I have room left",
            # Boot/startup performance complaints
            "my computer took forever to boot",
            "why is my boot so slow",
            "boot is really slow",
            "startup takes ages",
            "the system took a long time to start up",
        ],
        threshold=0.85,
        tool_name="run_command",
    )


def _register_analyze_file(matcher: SemanticMatcher) -> None:
    matcher.register_keyword_pattern(
        "analyze_file",
        [
            r"^(?:explain|analyze|diagnose|summarize|describe)\s+(?:this\s+|the\s+)?(?:file\s+|config\s+|log\s+)?/",
            r"^what does\s+/\S+\s+do",
            r"^(?:is there (?:anything|something) wrong with|check)\s+/",
        ],
        tool_name="analyze_file",
    )
    matcher.register_intent(
        "analyze_file",
        [
            "explain this config file",
            "what does this configuration do",
            "analyze this log file for errors",
            "summarize this script",
            "is there anything wrong with this config",
            "diagnose this systemd unit",
            "what's this file for",
            "explain what this service does",
            "check this config for problems",
            "help me understand this file",
            # action-surface recall (grounded miss: "analyze the file X for errors")
            "analyze the file for errors",
            "analyze this file for errors",
        ],
        threshold=0.88,
        tool_name="analyze_file",
    )
