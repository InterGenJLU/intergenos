"""InterGen CLI — interact with the AI assistant from the terminal.

Usage:
  intergen ask "What packages are installed?"
  intergen status
  intergen tier
  intergen tools

This connects via D-Bus to the running InterGen daemon. If the daemon
isn't running, it starts a direct session (useful for development).
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
    """Ask InterGen a question."""
    # Try D-Bus first
    response = try_dbus("Ask", message)
    if response:
        data = json.loads(response)
        print(data.get("response", response))
        return

    # Direct mode — no daemon running
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
