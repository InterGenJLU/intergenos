"""InterGen intent registration — wires tools into the semantic matcher.

Registers keyword patterns (Layer 1) and semantic examples (Layer 2)
for each of the 7 core tools. This is the glue between user input
and tool dispatch — without it, everything falls through to P3 LLM
tool calling.
"""

from __future__ import annotations

from intergen.semantic import SemanticMatcher


def register_all_intents(matcher: SemanticMatcher) -> None:
    """Register all keyword and semantic intents for core tools."""
    _register_run_command(matcher)
    _register_read_file(matcher)
    _register_write_file(matcher)
    _register_manage_packages(matcher)
    _register_manage_services(matcher)
    _register_web_search(matcher)
    _register_open_application(matcher)
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
        ],
        threshold=0.90,
        tool_name="write_file",
    )


def _register_manage_packages(matcher: SemanticMatcher) -> None:
    matcher.register_keyword_pattern(
        "manage_packages",
        [
            r"^pkm\s+",
            r"^(?:install|remove|uninstall|search|update)\s+(?:package\s+)?",
            r"^what packages?\s+",
            r"^list\s+(?:installed\s+)?packages?",
            r"^is\s+\w+\s+installed",
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
            r"^search the web for\s+",
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
        ],
        threshold=0.90,
        tool_name="web_search",
    )


def _register_open_application(matcher: SemanticMatcher) -> None:
    matcher.register_keyword_pattern(
        "open_application",
        [
            r"^(?:open|launch|start|run)\s+(?:the\s+)?(?:app(?:lication)?\s+)?(?:firefox|chrome|terminal|nautilus|files|settings|tweaks|code|vscode|gimp|inkscape|thunderbird|libreoffice)",
            r"^(?:open|launch|start)\s+(?:the\s+)?(?:file manager|text editor|browser|email)",
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
        ],
        threshold=0.90,
        tool_name="open_application",
    )


def _register_system_info(matcher: SemanticMatcher) -> None:
    """System information queries that route to run_command with specific commands."""
    matcher.register_keyword_pattern(
        "system_info",
        [
            r"^(?:how much|what(?:'s| is))\s+(?:my\s+)?(?:disk|storage|space|memory|ram|cpu|uptime|hostname|kernel|ip|os|operating system|arch|gpu)",
            r"^(?:show|check|display|get)\s+(?:me\s+)?(?:my\s+)?(?:disk|storage|memory|ram|cpu|system|hostname|kernel|ip|network)\s*(?:usage|info|space|status|address|version)?",
            r"^(?:free|df|du|top|htop|uname|uptime|hostname|lscpu|lsblk|lspci|lsusb|ip\s+addr)\b",
            r"^system\s+(?:info|status|health)",
            r"what(?:'s| is) my (?:host|ip|kernel|os|arch|gpu|hostname|ip address)",
            r"(?:hostname|kernel version|ip address|os version|what kernel)",
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
        ],
        threshold=0.85,
        tool_name="run_command",
    )


def _register_analyze_file(matcher: SemanticMatcher) -> None:
    matcher.register_keyword_pattern(
        "analyze_file",
        [
            r"^(?:explain|analyze|diagnose|summarize|describe)\s+(?:this\s+)?(?:file\s+|config\s+|log\s+)?/",
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
        ],
        threshold=0.88,
        tool_name="analyze_file",
    )
