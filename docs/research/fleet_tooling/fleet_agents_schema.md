# Fleet Agents Roster: Schema and Lifecycle

## 1. Purpose

This document specifies the schema and operational lifecycle of the `fleet_agents.json` resource. This file serves as the centralized, authoritative roster of all active AI agents in the fleet. Its primary consumer is the `safety-gate.ts` Kilo plugin, which uses the roster to dynamically authorize `git push --force-with-lease` operations on agent-specific branches (e.g., `deepseek/b2-dockerfile`, `gemini-pro/safety-gate-v2-design`). This mechanism enforces Canonical Rule 09 (`09_pre_merge_force_push.md`), ensuring that destructive Git operations are confined to an agent's designated development branches, preventing accidental damage to shared branches like `main` or `master`.

## 2. Schema Specification (Version 1.0)

The `fleet_agents.json` file is a JSON object containing metadata and a list of agent records.

```json
{
  "version": "1.0",
  "updated": "2026-04-29T12:00:00Z",
  "updated_by": "chris-windows-code-claude",
  "agents": [
    {
      "agent_id": "chris-ubuntu-codium-deepseek",
      "branch_prefix": "deepseek",
      "host": "ubuntu2404",
      "active": true,
      "force_push_allowed": true,
      "notes": "DeepSeek V4 (PRO) on ubuntu2404, primary B2 lane"
    }
  ]
}
```

### Top-Level Fields

-   **`version`** (string, required): The semantic version of the schema itself (e.g., `"1.0"`). This allows consumers to handle schema changes gracefully.
-   **`updated`** (string, required): An ISO-8601 timestamp indicating when the roster was last modified.
-   **`updated_by`** (string, required): The `agent_id` of the agent or user who performed the last update, for audit purposes.

### Agent Object Fields

-   **`agent_id`** (string, required): The unique, canonical identifier for the agent (e.g., `chris-windows-codium-gemini_pro`).
-   **`branch_prefix`** (string, required): The designated Git branch prefix for the agent. The safety-gate plugin will allow force-pushes to branches that match `<prefix>/*`.
-   **`host`** (string, required): An identifier for the host machine where the agent primarily operates (e.g., `windows`, `ubuntu2404`). This is for informational and audit purposes.
-   **`active`** (boolean, required): A flag indicating if the agent is currently active in the fleet. Inactive agents may still be listed for historical reasons but will be ignored by consumers.
-   **`force_push_allowed`** (boolean, recommended, default: `true`): An explicit flag to control force-push permissions. This field was adopted from Main's proposal (`10:43:58Z`) to provide more granular control. An agent can be `active` for other tasks but disallowed from force-pushing (e.g., a read-only audit agent). If omitted, consumers should default to `true` for active agents to maintain backward compatibility.
-   **`notes`** (string, optional): A free-text field for human-readable context about the agent, as suggested by Main (`10:43:58Z`).
-   **`expires`** (string, optional): An ISO-8601 timestamp for time-boxed agent access. This field was considered from Main's proposal but **rejected** for v1.0 to maintain simplicity. The operational overhead of managing expirations is not justified by current use cases. The `active` flag is sufficient for managing agent lifecycle.

## 3. Schema Evolution Policy

The schema follows a semantic versioning (`MAJOR.MINOR`) pattern.

-   **MAJOR** bumps (e.g., 1.0 -> 2.0) are for backward-incompatible changes, such as removing a required field or fundamentally changing a field's data type.
-   **MINOR** bumps (e.g., 1.0 -> 1.1) are for backward-compatible changes, such as adding a new optional field.

Plugin consumers **must** validate the schema version. As suggested by Laptop (`10:42:57Z`), if a plugin encounters a schema with a major version it does not recognize (e.g., plugin built for v1.x reads v2.0), it must fall back to its last-known-good cached data and log a critical error. This prevents newer, incompatible schemas from breaking older, deployed plugins.

## 4. Write Protocol

The `fleet_agents.json` file is managed under a strict, owner-only write protocol.

-   **Self-registration is Forbidden**: As mandated by Main (`10:43:58Z`), agents cannot modify this file or register themselves. This prevents a compromised agent from expanding its own permissions or adding unauthorized agents to the trust graph.
-   **Update Mechanism**: Updates are performed exclusively by the owner or a designated SPOC agent (e.g., `chris-ubuntu-code-claude`) with the necessary permissions. The process involves:
    1.  Modifying a local copy of `fleet_agents.json`.
    2.  Using a secure transport (e.g., SSH/`scp`) to upload the file to its authoritative location on the VPS (`/srv/intergen-mcp/runtime/fleet_agents.json`).
-   **No Write Endpoint**: There is no HTTP or PHP endpoint for writing to this file. It is served as a static asset.

## 5. Cache Lifecycle (Consumer-Side)

To ensure high performance and resilience against network failures, the `safety-gate.ts` plugin consumes this roster via a local cache. This design synthesizes proposals from Laptop and DeepSeek.

1.  **Fetch on Init**: The plugin fetches the authoritative `fleet_agents.json` from its HTTPS endpoint **once** during the Kilo session initialization (plugin load). This avoids adding network latency to the `tool.execute.before` hot path.
2.  **Local Disk Cache**: The fetched roster is immediately written to a local cache file at `~/.kilo/plugin/fleet_agents.cache.json`.
3.  **Runtime Read**: For every tool execution, the safety-gate logic **synchronously** reads the local disk cache. This `readFileSync` operation is sub-millisecond and does not introduce network dependencies or `async` complexity into the hook.
4.  **Refresh Cadence**: The cache is refreshed automatically the next time the Kilo instance is reloaded (e.g., restarting the IDE). This cadence aligns with the update cycle for the plugin's source code itself. There is no time-based TTL.

## 6. Failure Modes

The plugin uses a three-tier fallback system, sourced from DeepSeek's proposal (`10:43:57Z`), to ensure safety and resilience. When checking for permissions, it attempts to resolve the allowed prefixes in this order:

1.  **Local Disk Cache**: The primary source is `~/.kilo/plugin/fleet_agents.cache.json`. If this file is present, valid, and readable, its content is used.
2.  **Emergency Override File**: If the primary cache fails, the plugin checks for `~/.kilo/plugin/safety-gate-emergency-allow.json`. This is an escape hatch for the owner to manually authorize an agent during an outage. If used, a warning is logged to `stderr`.
3.  **Fail-Closed**: If both the cache and the emergency override are unavailable or invalid, the plugin **fails closed**. It will block any force-push operation it cannot validate against a known roster, logging a critical error. This is the default safe state.

## 7. Initial Roster

The following agents constitute the initial fleet roster:

-   `chris-ubuntu-code-claude` (SPOC)
-   `chris-intergenos-code-claude` (Laptop)
-   `chris-windows-code-claude` (Zephyrus Pair)
-   `chris-ubuntu-codium-deepseek`
-   `chris-windows-codium-gemini_pro`
