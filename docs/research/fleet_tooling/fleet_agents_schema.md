# Fleet Agents Roster: Schema and Lifecycle

## 1. Purpose

This document specifies the schema and operational lifecycle of the `fleet_agents.json` resource. This file serves as the centralized, authoritative roster of all active AI agents in the fleet. Its primary consumer is the `safety-gate.ts` Kilo plugin, which uses the roster to dynamically authorize `git push --force-with-lease` operations on agent-specific branches (e.g., `deepseek/b2-dockerfile`, `gemini-pro/safety-gate-v2-design`). This mechanism enforces the force-push policies outlined in Canonical Rule 09 (`09_repo_workflow.md`), ensuring that destructive Git operations are confined to an agent's designated development branches.

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

-   **MAJOR** bumps (e.g., 1.0 -> 2.0) are for backward-incompatible changes.
-   **MINOR** bumps (e.g., 1.0 -> 1.1) are for backward-compatible changes, like adding optional fields.

Plugin consumers **must** validate the schema version. As suggested by Laptop (`10:42:57Z`), if a plugin encounters a schema with a major version it does not recognize, it must fall back to its last-known-good cached data and log a critical error.

## 4. Write Protocol

The `fleet_agents.json` file is managed under a strict, owner-only write protocol.

-   **Self-registration is Forbidden**: As mandated by Main (`10:43:58Z`), agents cannot modify this file.
-   **Update Mechanism**: Updates are performed exclusively by the owner or a designated SPOC via secure transport (e.g., SSH/`scp`) to the authoritative VPS location (`/srv/intergen-mcp/runtime/fleet_agents.json`).
-   **No Write Endpoint**: There is no HTTP or PHP write endpoint for this file.

## 5. Cache Lifecycle (Consumer-Side)

The `safety-gate.ts` plugin consumes this roster via a local cache to ensure high performance and resilience.

1.  **Fetch on Init**: The plugin fetches the roster from its HTTPS endpoint **once** during Kilo session initialization.
2.  **Local Disk Cache**: The fetched roster is written to a local cache file at `~/.kilo/plugin/fleet_agents.cache.json`.
3.  **Runtime Read**: The safety-gate logic **synchronously** reads from the local disk cache.
4.  **Refresh Cadence**: The cache is refreshed automatically on the next Kilo reload. For long-running sessions (>24 hours), a soft warning may be logged to recommend a restart to refresh the roster, but the stale cache will continue to be used otherwise.

## 6. Failure Modes

The plugin uses a three-tier fallback system to ensure safety.

1.  **Local Disk Cache**: The primary source is `~/.kilo/plugin/fleet_agents.cache.json`.
2.  **Emergency Override File**: If the primary cache fails, the plugin checks for `~/.kilo/plugin/safety-gate-emergency-allow.json`.
3.  **Fail-Closed**: If both the cache and the emergency override are unavailable or invalid, the plugin **fails closed**, blocking any force-push operation it cannot validate.

### 6.1 Emergency Override Schema

The emergency override file is a simple JSON object specifying an array of allowed branch prefixes and an expiration date.

```json
{
  "prefixes": ["gemini-pro", "experimental-agent"],
  "expires": "2026-04-30T23:59:59Z"
}
```
*Note: A production version of the plugin should post a notification to the broadcast channel when this override is consumed, as an audit trail for a security-relevant escape hatch.*

## 7. Initial Roster

The following agents constitute the initial fleet roster.

-   **`chris-ubuntu-code-claude`** (SPOC)
    -   `branch_prefix`: `main-claude` (Owner decision needed)
-   **`chris-intergenos-code-claude`** (Laptop)
    -   `branch_prefix`: `laptop-claude` (Owner decision needed)
-   **`chris-windows-code-claude`** (Zephyrus Pair)
    -   `branch_prefix`: `windows-claude`
-   **`chris-ubuntu-codium-deepseek`**
    -   `branch_prefix`: `deepseek`
-   **`chris-windows-codium-gemini_pro`**
    -   `branch_prefix`: `gemini-pro`

*Note: `chris-ios-app-claude` is excluded by design as this slot is for owner-relay interaction only and does not perform Git operations.*
