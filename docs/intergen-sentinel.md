# InterGen Sentinel

The **InterGen Sentinel** is the security boundary and monitoring subsystem for the InterGen AI assistant. Because InterGen has the capability to perform system modifications, the Sentinel acts as a mandatory safety gate, ensuring that the AI cannot perform destructive or unauthorized actions without explicit user consent.

## Why Sentinel Exists
InterGenOS embraces the "Holy Grail" directive: security is not a trade-off for convenience. While the AI assistant is designed to be highly capable, giving an autonomous agent unrestricted access to system administration commands introduces unacceptable risks. The Sentinel provides defense-in-depth against both model hallucinations and malicious prompt injection.

## Core Responsibilities

### 1. Action Interception and Validation
When the InterGen intent engine determines that a user request requires executing a shell command, modifying a system file, or calling a privileged API, the request is passed to the Sentinel (\safety.py\ and \watchdog.py\).
- **Read-Only Operations**: Actions like checking system status, reading logs, or querying package databases are generally permitted without interruption.
- **State-Modifying Operations**: Actions that alter the system state are intercepted.

### 2. The User Confirmation Gate
Before a state-modifying action is executed, the Sentinel halts the process and presents the exact action to the user for confirmation.
- The user is shown the exact command or API call that will be executed.
- Confirmation is typically handled via a Polkit dialog, ensuring the user securely authorizes the escalation of privileges.
- If the user denies the request, the action is aborted, and the AI is informed of the cancellation.

### 3. Execution Sandboxing
When an action is approved, the Sentinel executes the command within a constrained environment. It monitors the execution for unexpected behavior (e.g., attempting to access out-of-scope files or opening unauthorized network connections) and enforces strict timeouts to prevent runaway processes.

### 4. Continuous Health Monitoring
The \watchdog.py\ component continuously monitors the health and resource usage of the local \llama.cpp\ inference engine. If the model begins to consume excessive memory or stalls, the Sentinel can gracefully restart the inference backend without crashing the user's session.

## Configuration
Advanced users can adjust the strictness of the Sentinel through the InterGen configuration files. However, completely disabling the confirmation gate for privileged actions is strongly discouraged and runs counter to the fundamental security design of InterGenOS.
