# InterGen AI Assistant User Guide

InterGen is the deeply integrated, privacy-respecting AI assistant built directly into InterGenOS. Unlike cloud-based assistants, InterGen runs entirely locally on your hardware, ensuring your data never leaves your machine.

## Architecture & Integration
InterGen is powered by a local large language model (LLM) managed by \llama.cpp\ (via \llama_manager.py\). It runs as a background D-Bus daemon (\dbus_daemon.py\), allowing seamless and secure communication with various desktop components and command-line interfaces.

### Core Components
- **Model Manager**: Automatically handles downloading, verifying, and loading the appropriate LLM weights for your hardware capabilities.
- **Intent Recognition**: InterGen analyzes your requests, identifying actions that require system-level changes versus conversational answers.
- **State Cache & Memory**: Maintains context of your current session and previous interactions to provide relevant, context-aware assistance.

## Interacting with InterGen

### Command Line Interface (CLI)
You can interact with InterGen directly from your terminal using the \intergen\ command.

\\\ash
# Ask a general question
intergen ask "How do I configure a static IP address?"

# Request a system action (requires confirmation)
intergen do "Update my system packages"
\\\

### Desktop Integration
InterGen is integrated into the GNOME desktop environment. You can trigger it via a configurable keyboard shortcut (default: \Super + Space\) to bring up a conversational overlay. From here, you can ask questions about your system, request application launches, or ask it to adjust system settings.

## System Actions and Safety
Because InterGen runs locally and has access to system APIs, it can perform actual system administration tasks (like modifying configuration files or installing packages). 
To protect your system, any action that modifies system state is routed through the **InterGen Sentinel**. The Sentinel ensures that destructive actions require explicit user confirmation via a secure Polkit prompt before execution. You are always in control.
