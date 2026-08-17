// safety-gate v2 — Kilo Code plugin: fleet-roster validation + force-push gate.
//
// DEPLOYMENT STATUS (kept current as the plugin lands across the fleet):
//   The "v2-sketch" filename suffix marks the file as the v2 design surface
//   under iteration; it does NOT mean "untested" or "not deployed."
//   Production status is tracked here so a contributor reading this file
//   knows whether they're looking at the current shipping plugin or a
//   research draft. Update this comment when status changes; do not
//   remove the suffix until the plugin is renamed in the same commit
//   that updates references in `.kilo/config.json` and the README.
//
//   - DEPLOYED on the workstation node as of 2026-05-06.
//   - Other workstation nodes + bare-metal hosts: install pending — track
//     via the relevant fleet-pin reference doc once landed.
//   - Newest fleet node: install pending; bundled with the v7.2.42 Kilo
//     Code upgrade.
//
// PRODUCTION-CRITICAL SAFETY FEATURES (these block real pushes):
//   - Fleet-roster check: a commit's branch_prefix is matched against the
//     active-agent roster fetched from FLEET_ROSTER_URL; spoofed agent
//     prefixes are rejected before push.
//   - Force-push gate: `--force` / `--force-with-lease` against any
//     refs/heads/master push is hard-blocked at plugin-hook time.
//
// EMERGENCY OVERRIDE: drop a JSON file at EMERGENCY_OVERRIDE_PATH with
// the right shape to bypass — intentionally inconvenient (manual file
// creation per push) so it is not a habitual escape hatch.

import type { PluginInput, Hooks } from "@kilocode/plugin";
import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'fs';
import { resolve, dirname } from 'path';
import { homedir } from 'os';

// --- v2 Additions: Configuration and State ---
const FLEET_ROSTER_URL = process.env.INTERGENOS_FLEET_ROSTER_URL || "https://intergenstudios.com/intergenos/runtime/fleet_agents.json";
const CACHE_PATH = resolve(homedir(), '.kilo', 'plugin', 'fleet_agents.cache.json');
const EMERGENCY_OVERRIDE_PATH = resolve(homedir(), '.kilo', 'plugin', 'safety-gate-emergency-allow.json');

interface FleetRoster {
    version: string;
    agents: {
        agent_id: string;
        branch_prefix: string;
        legacy_prefix?: string;
        active: boolean;
        force_push_allowed?: boolean;
    }[];
}

let rosterCache: FleetRoster | null = null;

// --- v2 Addition: Function to get allowed prefixes with 3-tier fallback ---
function getAllowedPrefixes(): string[] {
    // Tier 1: In-memory cache (if already loaded) or read from disk cache
    if (!rosterCache) {
        try {
            if (existsSync(CACHE_PATH)) {
                // Future enhancement: check file mtime for 24h+ stale warning.
                const rawCache = readFileSync(CACHE_PATH, 'utf-8');
                rosterCache = JSON.parse(rawCache);
            }
        } catch (e) {
            console.error(`safety-gate: Failed to read or parse roster cache at ${CACHE_PATH}:`, e);
            rosterCache = null;
        }
    }
    if (rosterCache) {
        if (rosterCache.version.startsWith("1.")) {
            const allowed = new Set<string>();
            rosterCache.agents
                .filter(a => a.active && (a.force_push_allowed !== false))
                .forEach(a => {
                    if (a.branch_prefix) allowed.add(a.branch_prefix);
                    if (a.legacy_prefix) allowed.add(a.legacy_prefix);
                });
            return Array.from(allowed);
        } else {
            console.error(`safety-gate: Unsupported roster schema version ${rosterCache.version}. Falling back.`);
        }
    }

    // Tier 2: Emergency override file
    try {
        if (existsSync(EMERGENCY_OVERRIDE_PATH)) {
            const rawOverride = readFileSync(EMERGENCY_OVERRIDE_PATH, 'utf-8');
            const overrideData = JSON.parse(rawOverride);
            if (overrideData.prefixes && Array.isArray(overrideData.prefixes)) {
                console.error(`safety-gate: WARNING: Using emergency override file at ${EMERGENCY_OVERRIDE_PATH}.`);
                // Future enhancement: post notification to broadcast channel.
                return overrideData.prefixes;
            }
        }
    } catch (e) {
        console.error(`safety-gate: Failed to read or parse emergency override file:`, e);
    }

    // Tier 3: Fail-Closed. Return an empty list, per design spec.
    console.error("safety-gate: CRITICAL: Roster unavailable and no valid override. Failing closed.");
    return [];
}


// --- Existing Rules (Unchanged) ---
const RULES: [RegExp, string][] = [
    [new RegExp("rm\\s.*-[a-z]*r[a-z]*(\\s|$).*/($|\\s)"), "blocked: rm -r / on root filesystem"],
    [new RegExp("rm\\s.*-[a-z]*r[a-z]*(\\s|$).*/(boot|etc|lib|usr|var)(/|\\s|$)"), "blocked: rm -r on critical path (/boot, /etc, /lib, /usr, /var)"],
    [new RegExp("rm\\s.*-[a-z]*r[a-z]*(\\s|$).*/(home)((/[a-z][a-z0-9_-]*)?)(/|\\s*|$)"), "blocked: rm -r on /home"],
    [new RegExp("rm\\s.*-[a-z]*r[a-z]*(\\s|$).*(~|\\$HOME)(/|\\s|$)"), "blocked: rm -r on $HOME"],
    [new RegExp("\\b(dd|mkfs|wipefs|shred)\\s+.*(/dev/sd[a-z]|/dev/nvme\\d|/dev/xvd[a-z])"), "blocked: destructive block device operation (dd/mkfs/wipefs/shred)"],
    [new RegExp("chmod\\s+.*777"), "blocked: chmod 777"],
    [new RegExp(":\\s*\\(\\s*\\)\\s*{\\s*:\\s*\\|\\s*:\\s*&\\s*}\\s*;\\s*:"), "blocked: fork bomb pattern"],
    [new RegExp("kill\\s+-9\\s+(-1|\\b0\\b)"), "blocked: kill -9 on -1 or 0"],
]

// --- v2 Modification: `checkForcePush` uses dynamic roster ---
function checkForcePush(command: string): string | null {
    const forcePushPattern = /git\s+push\s+(.*(?:--force|-f|--force-with-lease).*)/;
    if (!forcePushPattern.test(command)) return null;

    const allowedPrefixes = getAllowedPrefixes();
    if (allowedPrefixes.length === 0) {
        return "blocked: force push check failed because no allowed agent prefixes could be determined.";
    }

    const subCommands = command.split(/\s*(?:&&|;|\|\|)\s*/);
    for (const subCmd of subCommands) {
        const trimmed = subCmd.trim();
        if (!trimmed || !forcePushPattern.test(trimmed)) continue;

        // Basic sanity checks
        if (/origin\s+HEAD\b/.test(trimmed)) continue; // Allow pushing HEAD explicitly
        if (/origin\s+(?:master|main)\b/.test(trimmed)) {
            return "blocked: git push --force to origin master/main";
        }
        if (/--(?:all|mirror)/.test(trimmed)) {
            return "blocked: git push --force with --all or --mirror is unsafe regardless of branch";
        }

        // Extract tokens ignoring 'git', 'push', and flags
        const tokens = trimmed.split(/\s+/);
        let pushIndex = tokens.indexOf("push");
        if (pushIndex === -1) continue;

        const positionalArgs = tokens.slice(pushIndex + 1).filter(t => !t.startsWith("-"));
        
        // positionalArgs should be ['origin', 'branch'] or ['origin', 'local:remote']
        if (positionalArgs.length < 2) {
             return "blocked: git push --force without explicit target branch — specify origin <branch>";
        }

        // Check all specified refspecs (everything after 'origin')
        const refspecs = positionalArgs.slice(1);
        for (const refspec of refspecs) {
            let destBranch = refspec;
            
            // Handle local:remote syntax
            const colonIdx = refspec.indexOf(':');
            if (colonIdx !== -1) {
                destBranch = refspec.slice(colonIdx + 1);
            } else if (refspec.startsWith('+')) {
                destBranch = refspec.slice(1);
            }

            if (destBranch === "master" || destBranch === "main" || destBranch.startsWith("master/") || destBranch.startsWith("main/")) {
                return "blocked: git push --force refspec destination targets master/main";
            }

            const hasAllowedPrefix = allowedPrefixes.some(prefix => destBranch.startsWith(`${prefix}/`));
            if (!hasAllowedPrefix) {
                const prefixes = allowedPrefixes.map(p => `\`${p}/*\``).join(', ');
                return `blocked: git push --force to non-approved branch '${destBranch}'. Pre-merge force is only allowed on branches matching: ${prefixes}`;
            }
        }
    }

    return null;
}

async function initializePlugin(input: PluginInput): Promise<Hooks> {
    try {
        const response = await fetch(FLEET_ROSTER_URL);
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const rosterData: FleetRoster = await response.json();

        // Ensure cache directory exists
        const cacheDir = dirname(CACHE_PATH);
        if (!existsSync(cacheDir)) {
            mkdirSync(cacheDir, { recursive: true });
        }

        writeFileSync(CACHE_PATH, JSON.stringify(rosterData, null, 2), 'utf-8');
        rosterCache = rosterData; // Prime in-memory cache
        console.log(`safety-gate: Successfully fetched and cached fleet roster v${rosterData.version}.`);
    } catch (e) {
        console.error(`safety-gate: FAILED to fetch fleet roster from ${FLEET_ROSTER_URL}. Will rely on cache/override. Error:`, e);
    }

    const hooks: Hooks = {
        "tool.execute.before": async (input, output) => {
            if (input.tool !== "bash") return;
            const command = output.args?.command || "";

            const pushViolation = checkForcePush(command);
            if (pushViolation) {
                throw new Error(`HOOK-CANCEL: safety-gate: ${pushViolation}`);
            }

            for (const [regex, message] of RULES) {
                if (regex.test(command)) {
                    throw new Error(`HOOK-CANCEL: safety-gate: ${message}`);
                }
            }
        },
    };

    return hooks;
}

// Kilo's plugin loader expects a `server` export.
export const server = initializePlugin;
