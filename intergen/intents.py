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
    r"yes|yeah|yep|sure|"
    r"if you don'?t mind,?|would you mind,?)[,\s]+)*")
# The affirmative leads (yes/yeah/yep/sure) were added 2026-08-25 from field language:
# a user who has just been offered a search answers "yes, do a web search for X". They
# are safe HERE and only here because every pattern that uses this lead REQUIRES an
# explicit web-search phrase after it — a bare "yes" matches nothing, which the field
# fixture asserts as its own case.


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


# What a person calls a piece of SOFTWARE when they do not know its name.
# Read by the "find me software" keyword patterns below and defined once here so
# the two patterns cannot drift apart. Program kinds only: the document nouns
# (recipe, pattern, instructions, tutorial, manual, template) belong to
# web_search, which registers its own forms for them.
_PROGRAM_KIND_NOUN = (
    r"(?:editor|browser|player|viewer|reader|client|app|apps|application|"
    r"applications|program|programs|tool|tools|manager|utility|ide|terminal|"
    r"emulator|launcher|recorder)"
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
            # The same question in the two other shapes the field uses. Only
            # "is X installed" had a pattern, so "check if docker is installed"
            # (the exact clause the whole-battery re-drive recorded) and "do I
            # have docker installed" (a sentence this intent's own embedding
            # examples list) reached the deterministic rung as nothing at all.
            # On a box whose embedding server is down these are the ONLY route
            # to the carrier, which is why they are patterns and not more
            # examples.
            r"^(?:check|see)\s+(?:if|whether)\s+(?:the\s+)?[\w.+-]+\s+"
            r"(?:package\s+)?is\s+installed\b",
            r"^do\s+(?:i|we|you)\s+have\s+(?:the\s+)?[\w.+-]+\s+"
            r"(?:package\s+)?installed\b",
            r"^is\s+there\s+a\s+package",
            r"^do\s+(?:you|we|i)\s+have\s+a\s+package",
            # explicit "...package..." version/info queries belong to pkm, not
            # run_command's uname (which answers the kernel string, not the
            # package release) — anchored on the word "package" to stay narrow
            r"^what\s+version\s+of\s+.+\bpackage",
            # ── FIND ME SOFTWARE (2026-08-26). "find a pdf editor" is how a
            # person asks for a program, and no rung claimed it: no keyword
            # pattern matched, and measured against the live embedding server on
            # a dual-GPU workstation its best similarity across the ENTIRE intent
            # corpus was 0.5968 — under every intent's own threshold, so the
            # semantic rung had no candidate to admit. The clause landed in a
            # freeform model turn built with_tools=False and the model invented
            # system state ("You don't have a dedicated PDF editor installed,
            # but qpdf is available") with no tool having run. Confirmed on the
            # live daemon the same day: glass turn bca447f55988c2d4,
            # semantic_score=0.5968062877655029, source=llm_freeform,
            # tool_count=0. The extractor could already handle the clause —
            # _extract_arguments returns {"action": "search", "query": "pdf
            # editor"} for it — so only the recognition was missing.
            #
            # WHY DETERMINISTIC AND NOT MORE EMBEDDING EXAMPLES. Both were
            # measured against the live embedder. Adding example sentences pulls
            # the clause over the bar, and pulls three questions that must NOT
            # dispatch over it too: "what is a pdf editor" reaches 0.8989, "how
            # do I edit a pdf" 0.9136 and "tell me about pdf editors" 0.8870,
            # all above the router's 0.85 admission bar, because the embedding
            # is dominated by the OBJECT ("pdf editor") while the thing that
            # separates a request from a question is the LEADING VERB. A keyword
            # pattern reads exactly that. This is the same argument the
            # web_search registration below already makes for its own forms.
            #
            # The noun list is program kinds only. It deliberately excludes the
            # document nouns web_search owns (recipe/pattern/instructions/
            # tutorial/manual/template) and does not touch run_command's
            # "^find ... largest/biggest/hidden" file searches; both were held as
            # controls in the measurement, along with "find my car keys" and
            # "find a good movie to watch", which must reach no carrier at all.
            r"^(?:find|get)\s+(?:me\s+)?(?:a|an)\s+(?:\w+\s+){0,2}?"
            + _PROGRAM_KIND_NOUN + r"\b",
            r"^is\s+there\s+(?:a|an)\s+(?:\w+\s+){0,2}?"
            + _PROGRAM_KIND_NOUN + r"\b",
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
            # ── FIELD LANGUAGE (2026-08-25). "How do I update this system?" is the
            # first thing a new user asks, and the shipped corpus had no form of it:
            # measured, it reached 0.5575 on manage_packages while write_file's
            # "update the configuration" outranked it at 0.7104. Updating the SYSTEM
            # is package management; updating a CONFIGURATION is not, and the two
            # were close enough in the old corpus for the wrong one to lead.
            "how do I update this system",
            "how do I update my computer",
            "update this system",
            "update my system",
            "can you update the system for me",
            "are there any updates for this machine",
            "how do I install updates",
            "keep this system up to date",
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
            # THE OTHER WORD ORDER. Every pattern above anchors "search the web";
            # none of them matches "web search for X", and that is the form the first
            # outside user actually typed — four times across two sessions, in three
            # variations, none of which was served. Measured on the released corpus
            # (2026-08-25): the embedding layer cannot rescue them either, because
            # "web search for the average price of ..." reaches only 0.4138 against
            # web_search's 0.90 threshold while manage_packages' "search for a
            # package" example outranks it at 0.4315 — a lower floor would admit the
            # WRONG tool sooner, so the recognition has to be deterministic here.
            #
            # "websearch" as one word is included: it is what she typed. The optional
            # do/run/perform + article covers "do a web search for X". Anchored at the
            # start after the courtesy lead, so a sentence that merely mentions a web
            # search in passing is not captured.
            r"^" + _WEB_DISPATCH_LEAD
            + r"(?:(?:do|run|perform|make)\s+(?:a|an|another)\s+)?web\s*search\b",
            # "show me a picture of X" — a request for an IMAGE the assistant has to
            # go and fetch. Narrow on purpose: the noun list is images only, and the
            # trailing "of" requires a subject, so "show me the picture settings" and
            # "show me a photo I already have" are not captured by this form.
            r"^" + _WEB_DISPATCH_LEAD
            + r"show\s+(?:me\s+)?(?:a|an|the|some)?\s*(?:picture|photo|image|pictures|photos|images)\s+of\b",
            # "find a recipe / pattern / instructions for X" — "find" here is an
            # instruction to GO AND LOOK, and the object is a document that lives on
            # the internet. Anchored to that object list rather than to a bare
            # "^find", which run_command already owns for file searches and which
            # would otherwise swallow anything. The contrast that matters is with
            # "can you RECOMMEND a good chocolate chip cookie recipe" — a request the
            # model answers itself, which does not start with a look-it-up verb and
            # is asserted as a negative in the field fixture.
            r"^" + _WEB_DISPATCH_LEAD
            + r"(?:find|look\s+for|search\s+for)\s+(?:me\s+)?(?:a|an|the|some)?\s*"
            + r"(?:\w+\s+){0,3}?(?:recipe|pattern|instructions|tutorial|manual|template)\b",
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
            # ── FIELD LANGUAGE (2026-08-25). Every example above is an IMPERATIVE
            # DISPATCH PHRASE — "search the web for", "google this", "look this up".
            # Measured against the sentences the first outside user actually typed,
            # that corpus reached at most 0.4643 against a 0.90 threshold, because
            # real people do not issue dispatches: they ask CONTENT QUESTIONS whose
            # answer is only obtainable by looking something up. Those are the class
            # below. They are written as the CLASS, not as the field sentences —
            # generalisation to unseen sentences of the same class is measured
            # separately and reported, because a corpus tuned until one fixture
            # passes is memorisation, not recognition.
            #
            # The line these have to hold: a question the model can answer from its
            # own knowledge ("what's the capital of Spain", "what year did Babe Ruth
            # start playing baseball", "how can I locate my septic tank") must stay
            # BELOW threshold. The separating signal is not the topic, it is whether
            # the answer is a CURRENT, EXTERNAL fact — a price, a listing, an image,
            # a document to go and fetch.
            #
            # asking for a current price or valuation
            "what is this antique worth",
            "what is the value of this piece of furniture",
            "how much does this sell for",
            "what is the going price for this",
            "how much is one of these worth these days",
            # The valuation class needed WIDTH, not more of the same. Measured
            # 2026-08-25 on held-out sentences never used to write this corpus: with
            # only the five forms above, "what is my grandmother's ring worth" and
            # "how much do vintage typewriters go for now" both fell below threshold
            # while the field sentence passed — the class was matching a wording, not
            # a meaning. These add the possessive subject, the plural subject, the
            # "go for" and "fetch" verbs, and the resale framing.
            "what is my ring worth",
            "what are these worth",
            "how much do these go for",
            "how much would this fetch at auction",
            "what do these sell for second hand",
            "is this worth anything",
            # asking to be shown an image that has to be fetched
            "show me a picture of that",
            "show me a photo of this style of furniture",
            "can you find me a picture of it",
            "find an image of this",
            # asking to go and find a document, pattern, recipe or listing
            "find a recipe for dinner",
            "find me a recipe for this dish",
            "can I find a free sewing pattern",
            "is there a free pattern for this anywhere",
            "find instructions for making this",
            "look for a free download of this",
        ],
        # THRESHOLD LOWERED 0.90 -> 0.68, and the number comes from a measurement,
        # not from tuning until a fixture passed.
        #
        # On the RELEASED corpus this move would have been WRONG and was refused on
        # exactly that ground: the positive and negative bands OVERLAPPED. Real
        # requests reached 0.41-0.46 while "how can I locate my septic tank in my
        # yard" reached 0.5130 and a bare "yes" reached 0.5040, so every threshold
        # that admitted a real request admitted a non-request with it.
        #
        # The corpus above is what separated them. Measured on this branch, the two
        # requests this layer has to admit — the ones the deterministic patterns do
        # NOT catch, because they carry no look-it-up verb — sit at 0.7266 ("what is
        # the value of ...") and 0.8241 ("can I find a free ... pattern"), while the
        # highest similarity ANY non-request reaches against ANY intent is 0.5746
        # ("can you recommend a good chocolate chip cookie recipe", against this very
        # corpus). The gap is 0.5746 to 0.7266 and 0.68 sits inside it.
        #
        # It is deliberately NOT the midpoint. 0.68 leaves 0.105 of headroom above the
        # highest non-request and 0.047 below the lowest request, because the two
        # errors are not equally expensive: refusing to look something up costs a turn
        # the model still answers, while running a search nobody asked for spends the
        # user's network and attention on something they did not want.
        threshold=0.68,
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
            #
            # THE VERB IS REQUIRED. It used to be optional, which made the two
            # words "what time" enough to claim any sentence they opened —
            # measured on this tree: a question about when the sun would set in a
            # named town, one about when to plant tomatoes, and one about which
            # part of the day is best to water plants were each answered with
            # this machine's clock. The genuine clock asks do not depend on this
            # pattern: "what time is it" and "what's the time" are answered
            # earlier by the direct-answer probe, and the sibling patterns below
            # keep the rest.
            r"\bwhat(?:'s| is)\s+(?:the\s+)?time\b",
            r"\btime is it\b",
            r"\bcurrent time\b",
            # "time of day" only as a CLOCK reading. "what time of day is best
            # to water plants?" is asking for advice about part of the day, and
            # the bare phrase was answering it with this machine's clock.
            r"\btime\s+of\s+day\s+is\s+it\b",
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
