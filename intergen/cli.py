"""InterGen CLI — interact with the AI assistant from the terminal.

Usage:
  intergen ask "What packages are installed?"
  intergen status
  intergen tier
  intergen tools
  intergen tool-log [--clear|--json|--count] [--limit N]

This connects via D-Bus to the running InterGen daemon. If the daemon
isn't running, it starts a direct session (useful for development).

`intergen tool-log` reads the per-user D-008 RFC §9 dispatch audit log
at $XDG_STATE_HOME/intergen/tool-dispatch.jsonl. The log is the user's
own record of what InterGen's dispatcher decided + what the user
approved or denied via the review modal. `--clear` is the user-data
wipe path per the Q5 provisional default (30-day logrotate retention
canonical; --clear is the explicit user-initiated wipe).
"""

from __future__ import annotations

import json
import sys


def print_usage() -> None:
    print("Usage: intergen <command> [args]")
    print()
    print("Commands:")
    print("  ask <message>    Ask InterGen a question")
    print("  status           Show daemon status")
    print("  tier             Show hardware tier info")
    print("  tools            List available tools")
    print("  tool-log         Show the D-008 dispatch audit log")
    print("                     --clear       wipe the log (user-data delete)")
    print("                     --json        emit raw JSONL")
    print("                     --count       print record count and exit")
    print("                     --limit N     show last N records (default 50)")
    print("  test             Run self-test (hardware + tools)")
    print("  setup            Download and verify LLM model")
    print("  daemon           Start the InterGen daemon")
    print()
    print("InterGen — AI assistant for InterGenOS")


def try_dbus(method: str, *args: str) -> str | None:
    """Try to call a method on the InterGen D-Bus service."""
    try:
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib

        bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        result = bus.call_sync(
            "com.intergenos.InterGen",
            "/com/intergenos/InterGen",
            "com.intergenos.InterGen",
            method,
            GLib.Variant("(s)", args) if args else None,
            GLib.VariantType("(s)"),
            Gio.DBusCallFlags.NONE,
            5000,  # 5 second timeout
        )
        return result.unpack()[0]
    except Exception:
        return None


def cmd_ask(message: str) -> None:
    """Ask InterGen a question.

    Tries D-Bus first; falls through to direct mode only when no daemon
    is on the bus at all. The fallthrough is gated by a Status probe to
    distinguish:
      (a) no daemon running on the session bus → safe to start direct
          session
      (b) daemon running but Ask method-failed → starting direct mode
          would create a SECOND daemon, doubling model RAM via
          Gio.bus_own_name name_lost race
    """
    # Try D-Bus first.
    response = try_dbus("Ask", message)
    if response is not None:
        data = json.loads(response)
        print(data.get("response", response))
        return

    # Ask failed. Probe Status to disambiguate "no daemon" vs "daemon up
    # but Ask method-failed" before deciding whether to start a competing
    # direct session.
    status_probe = try_dbus("Status")
    if status_probe is not None:
        # Daemon IS up but Ask failed for some reason. Don't start a
        # second daemon — surface the symptom and exit.
        print("InterGen daemon is running but the Ask call failed.",
              file=sys.stderr)
        print("Check the daemon logs for details:", file=sys.stderr)
        print("  journalctl --user -u intergen -n 50", file=sys.stderr)
        sys.exit(2)

    # Status also failed → no daemon on the bus. Safe to start direct mode.
    print("InterGen daemon not running. Starting direct session...")
    from intergen.dbus_daemon import InterGenDaemon
    daemon = InterGenDaemon()
    daemon.start_service()
    response = daemon.ask(message)
    data = json.loads(response)
    print(data.get("response", response))


def cmd_status() -> None:
    """Show daemon status."""
    response = try_dbus("Status")
    if response:
        status = json.loads(response)
    else:
        # Direct mode
        from intergen.dbus_daemon import InterGenDaemon
        daemon = InterGenDaemon()
        daemon.start_service()
        status = json.loads(daemon.status())

    print("InterGen Status")
    print("=" * 40)
    print(f"  Running:    {status.get('running', False)}")
    print(f"  Version:    {status.get('version', 'unknown')}")

    tier = status.get("tier")
    if tier:
        print(f"  Tier:       {tier.get('level', '?')}")
        print(f"  RAM:        {tier.get('ram_gb', '?')} GB")
        print(f"  GPU:        {tier.get('gpu_vendor', 'none')}")
        print(f"  Model:      {tier.get('recommended_model', '?')} {tier.get('recommended_quant', '')}")

    print(f"  Requests:   {status.get('requests_handled', 0)}")
    print(f"  Last Error: {status.get('last_error', 'none')}")

    components = status.get("components", {})
    if components:
        print()
        print("Components:")
        for name, ready in components.items():
            marker = "+" if ready else "-"
            print(f"  [{marker}] {name}")


def cmd_tier() -> None:
    """Show hardware tier info."""
    response = try_dbus("GetTier")
    if response:
        tier = json.loads(response)
    else:
        from intergen.hardware import HardwareDetector
        detector = HardwareDetector()
        t = detector.detect()
        tier = {
            "level": t.tier.value,
            "ram_gb": t.ram_gb,
            "gpu_vendor": t.gpu_vendor,
            "gpu_model": t.gpu_model,
            "recommended_model": t.recommended_model,
            "recommended_quant": t.recommended_quant,
            "estimated_model_size_gb": t.estimated_model_size_gb,
        }

    print("Hardware Tier")
    print("=" * 40)
    print(f"  Level:      Tier {tier.get('level', '?')}")
    print(f"  RAM:        {tier.get('ram_gb', '?')} GB")
    print(f"  GPU:        {tier.get('gpu_vendor', 'none')} ({tier.get('gpu_model', '')})")
    print(f"  Model:      {tier.get('recommended_model', '?')} {tier.get('recommended_quant', '')}")
    print(f"  Model Size: ~{tier.get('estimated_model_size_gb', '?')} GB")


def cmd_tools() -> None:
    """List available tools."""
    from intergen.tools.run_command import RunCommandTool
    from intergen.tools.read_file import ReadFileTool
    from intergen.tools.write_file import WriteFileTool
    from intergen.tools.manage_packages import ManagePackagesTool
    from intergen.tools.manage_services import ManageServicesTool
    from intergen.tools.web_search import WebSearchTool
    from intergen.tools.open_application import OpenApplicationTool

    tools = [
        RunCommandTool(), ReadFileTool(), WriteFileTool(),
        ManagePackagesTool(), ManageServicesTool(),
        WebSearchTool(), OpenApplicationTool(),
    ]

    print("InterGen Tools")
    print("=" * 40)
    for tool in tools:
        safety = tool.schema.safety_tier.value
        print(f"  {tool.name:25s} [{safety:7s}]  {tool.description[:50]}")


def cmd_tool_log(args: list[str]) -> None:
    """Show or wipe the D-008 RFC §9 dispatch audit log.

    Flags:
      --clear      truncate the log (user-data-wipe path per Q5 default).
      --json       emit raw JSONL lines instead of human-readable rendering
                   (suitable for piping into jq).
      --count      print the record count and exit.
      --limit N    show only the last N records (default 50).
    """
    from intergen.audit_log import (
        clear_log, default_log_path, read_records, record_count,
    )

    if "--clear" in args:
        path = default_log_path()
        if not path.exists():
            print(f"Audit log already empty: {path}")
            return
        existing = record_count()
        ok = clear_log()
        if ok:
            print(f"Cleared {existing} record(s) from {path}")
        else:
            print(f"Failed to clear {path} (see logs)", file=sys.stderr)
            sys.exit(1)
        return

    if "--count" in args:
        print(record_count())
        return

    limit = 50
    if "--limit" in args:
        try:
            limit = int(args[args.index("--limit") + 1])
        except (IndexError, ValueError):
            print("--limit requires an integer argument", file=sys.stderr)
            sys.exit(1)
        if limit < 1:
            print("--limit must be >= 1", file=sys.stderr)
            sys.exit(1)

    records = list(read_records())
    if not records:
        print(f"Audit log empty: {default_log_path()}")
        return

    if "--json" in args:
        # Raw JSONL pass-through for jq/grep pipelines.
        for r in records[-limit:]:
            print(json.dumps(r, separators=(",", ":")))
        return

    # Human-readable rendering: one block per record.
    shown = records[-limit:]
    print(f"InterGen dispatch audit log — {len(shown)} of {len(records)} record(s)")
    print(f"  Path: {default_log_path()}")
    print()
    for r in shown:
        ts = r.get("timestamp", "?")
        name = r.get("tool_name", "?")
        outcome = r.get("user_decision", "?")
        declared = r.get("declared_provenance", "?")
        effective = r.get("effective_provenance", "?")
        exit_code = r.get("exit_code", "?")
        source = r.get("source_attribution", "")
        excerpt = r.get("excerpt", "")
        ingress = r.get("ingress_tools_this_turn", []) or []

        prov_marker = ""
        if declared != effective:
            prov_marker = f"  (escalated from {declared} -> {effective} per RFC §5.1)"

        print(f"  [{ts}] {name}  outcome={outcome}  exit={exit_code}")
        print(f"    provenance:   {effective}{prov_marker}")
        if ingress:
            print(f"    ingress tools (this turn): {', '.join(ingress)}")
        if source:
            print(f"    source:       {source}")
        if excerpt:
            snippet = excerpt[:200] + ("..." if len(excerpt) > 200 else "")
            print(f"    excerpt:      {snippet}")
        args_summary = r.get("arguments", {})
        if args_summary:
            print(f"    arguments:    {args_summary}")
        result = r.get("result_summary", "")
        if result:
            snippet = result[:160] + ("..." if len(result) > 160 else "")
            print(f"    result:       {snippet}")
        print()


def cmd_test() -> None:
    """Run self-test."""
    import unittest
    from intergen.tests import test_tools
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(test_tools)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


def main() -> None:
    if len(sys.argv) < 2:
        print_usage()
        sys.exit(1)

    command = sys.argv[1].lower()

    if command == "ask":
        if len(sys.argv) < 3:
            print("Usage: intergen ask <message>")
            sys.exit(1)
        cmd_ask(" ".join(sys.argv[2:]))
    elif command == "status":
        cmd_status()
    elif command == "tier":
        cmd_tier()
    elif command == "tools":
        cmd_tools()
    elif command == "tool-log":
        cmd_tool_log(sys.argv[2:])
    elif command == "test":
        cmd_test()
    elif command == "setup":
        from intergen.setup import run_setup
        run_setup(auto_yes="--yes" in sys.argv)
    elif command == "daemon":
        from intergen.dbus_daemon import main as daemon_main
        daemon_main()
    elif command in ("help", "--help", "-h"):
        print_usage()
    else:
        print(f"Unknown command: {command}")
        print_usage()
        sys.exit(1)


if __name__ == "__main__":
    main()
