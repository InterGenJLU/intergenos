# InterGen — MCP Architecture & Glasswing Security Model

**Date:** 2026-04-09

## JARVIS MCP Implementation (Proven, Portable)

JARVIS has bidirectional MCP:

**Outbound (JARVIS as MCP server):** `mcp_server.py` (265 lines)
- FastMCP on stdio transport, exposes 7 tools
- Security: explicitly blocks run_command and confirm_pending from external callers
- WAL mode SQLite sharing with running instance

**Inbound (JARVIS consuming MCP servers):** `mcp_client.py` (297 lines)
- MCPBridge class with dedicated async thread
- Subprocess lifecycle via AsyncExitStack
- Auto-discovery: list_tools() → convert to OpenAI schema → register in tool_registry
- Semantic pruning: intent_examples in config create virtual skills for relevance matching
- Reconnection: exponential backoff (2s base, 30s cap, 5 attempts)
- Env var resolution: ${VAR} syntax in config

**Config format:**
```yaml
mcp_servers:
  server_name:
    command: npx
    args: [-y, package]
    env:
      KEY: ${ENV_VAR}
    intent_examples:
      - "example query for semantic pruning"
    system_prompt_rule: >
      Use these tools when...
```

## OWASP MCP Top 10 Attack Vectors

| # | Attack | Mitigation |
|---|--------|------------|
| MCP01 | Token/secret exposure | Env var isolation, credential redaction |
| MCP02 | Tool poisoning | Schema scanning, description sanitization |
| MCP03 | Cross-server shadowing | Namespace isolation (mcp_server_tool) |
| MCP04 | Supply chain tampering | Binary checksums, pinned versions |
| MCP05 | Command injection | Input validation, sandboxed execution |
| MCP07 | Missing auth | Permission manifest, user approval |
| — | Rug pulls | Schema hash pinning, change detection |
| — | Sampling exploits | Disable MCP sampling feature |

## InterGen Tiered Trust Model

| Level | Description | Restrictions |
|-------|-------------|-------------|
| system | Built-in InterGen tools | Full access |
| verified | Audited, signed MCP servers | Declared permissions enforced |
| community | User-installed | Full sandbox, explicit approval |
| untrusted | Development/testing | Maximum sandbox, session-only |

## Permission Declaration Format

```yaml
mcp_server:
  name: email-imap
  permissions:
    network:
      - host: imap.gmail.com
        port: 993
    filesystem:
      read: []
      write: []
    environment:
      required: [GMAIL_ADDRESS, GMAIL_APP_PASSWORD]
    capabilities: [read_data, write_data]
    tool_count: 5
    schema_hash: sha256:abc123...
```

## Enforcement Layers

1. Process sandboxing (systemd scope, seccomp, network namespace)
2. Schema pinning (hash verified on every reconnect)
3. Tool description scanning (regex + LLM for injection patterns)
4. Full audit logging (every tool call with args, results, timing)
5. Rate limiting (per-server, configurable)
6. Cross-server namespace isolation
7. User approval flow with plain-language permission display

## Glasswing Integration Points

Glasswing/Mythos is not publicly available. InterGen implements equivalent
security using available tools:
- Claude API for tool description injection scanning
- Static analysis of MCP server code before approval
- Runtime anomaly detection on tool call patterns
- Dependency auditing for supply chain integrity
