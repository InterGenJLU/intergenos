import type { PluginInput, Hooks } from "@kilocode/plugin";
import { readFileSync, writeFileSync, existsSync } from 'fs';
import { resolve } from 'path';
import { homedir } from 'os';

// --- v2 Additions: Configuration and State ---
const FLEET_ROSTER_URL = process.env.INTERGENOS_FLEET_ROSTER_URL || "https://intergenstudios.com/intergenos/runtime/fleet_agents.json";
const CACHE_PATH = resolve(homedir(), '.kilo', 'plugin', 'fleet_agents.cache.json');
const EMERGENCY_OVERRIDE_PATH = resolve(homedir(), '.kilo', 'plugin', 'safety-gate-emergency-allow.json');

interface FleetRoster {
    version: string;
    agents: {
        branch_prefix: string;
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
                const rawCache = readFileSync(CACHE_PATH, 'utf-8');
                rosterCache = JSON.parse(rawCache);
            }
        } catch (e) {
            console.error(`safety-gate: Failed to read or parse roster cache at ${CACHE_PATH}:`, e);
            rosterCache = null; // Ensure cache is null on error
        }
    }
    if (rosterCache) {
        // Validate schema major version
        if (rosterCache.version.startsWith("1.")) {
            return rosterCache.agents
                .filter(a => a.active && (a.force_push_allowed !== false))
                .map(a => a.branch_prefix);
        } else {
            console.error(`safety-gate: Unsupported roster schema version ${rosterCache.version}. Falling back.`);
        }
    }

    // Tier 2: Emergency override file
    try {
        if (existsSync(EMERGENCY_OVERRIDE_PATH)) {
            const rawOverride = readFileSync(EMERGENCY_OVERRIDE_PATH, 'utf-8');
            const overrideData = JSON.parse(rawOverride);
            // Basic validation for override structure
            if (overrideData.prefixes && Array.isArray(overrideData.prefixes)) {
                 // Per Main's request, log to stderr. Channel post is out of scope for this sketch.
                console.error(`safety-gate: WARNING: Using emergency override file at ${EMERGENCY_OVERRIDE_PATH}.`);
                return overrideData.prefixes;
            }
        }
    } catch (e) {
        console.error(`safety-gate: Failed to read or parse emergency override file:`, e);
    }

    // Tier 3: Hardcoded last-known-good roster
    console.error("safety-gate: CRITICAL: Roster unavailable and no valid override. Using hardcoded fallback.");
    return ["deepseek", "gemini-pro"]; // Update this list as the fleet grows
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
    const forcePushPattern = /git\s+push\s+.*(--force|-f|--force-with-lease)/;
    if (!forcePushPattern.test(command)) return null;

    const allowedPrefixes = getAllowedPrefixes();
    if (allowedPrefixes.length === 0) {
        return "blocked: force push check failed because no allowed agent prefixes could be determined."
    }
    const allowedPrefixPattern = new RegExp(
        `(?:origin\\s+)?(?:${allowedPrefixes.join("|")})\\/\\S+`
    );

    const subCommands = command.split(/\s*(?:&&|;|\|\|)\s*/);
    for (const subCmd of subCommands) {
        const trimmed = subCmd.trim();
        if (!trimmed || !forcePushPattern.test(trimmed)) continue;

        // --- v2 Change: Replace hardcoded 'deepseek' check ---
        // Was: if (/origin\s+deepseek\//.test(trimmed) || /deepseek\/\S+/.test(trimmed))
        if (allowedPrefixPattern.test(trimmed)) {
            const refspecMatch = trimmed.match(/(?:origin\s+)?\+?(\S+)$/m);
            if (refspecMatch) {
                const refspec = refspecMatch[1];
                const colonIdx = refspec.indexOf(':');
                if (colonIdx !== -1) {
                    const dest = refspec.slice(colonIdx + 1);
                    if (dest === "master" || dest === "main" || dest.startsWith("master/") || dest.startsWith("main/")) {
                        return "blocked: git push --force refspec destination targets master/main";
                    }
                }
            }
            continue; // Allowed
        }

        // --- Existing checks (Unchanged) ---
        if (/origin\s+HEAD\b/.test(trimmed)) continue;
        if (/origin\s+(?:master|main)\b/.test(trimmed)) {
            return "blocked: git push --force to origin master/main";
        }
        if (/--(?:all|mirror)/.test(trimmed)) {
            return "blocked: git push --force with --all or --mirror is unsafe regardless of branch";
        }
        if (!/origin\s+\S/.test(trimmed)) {
            return "blocked: git push --force without explicit target branch — specify origin <branch>";
        }

        const prefixes = allowedPrefixes.map(p => `\`${p}/*\``).join(', ');
        return `blocked: git push --force to non-approved branch. Pre-merge force is only allowed on branches matching: ${prefixes}`;
    }

    return null;
}

// --- v2 Modification: Plugin `server` function is now the init hook ---
export async function server(input: PluginInput): Promise<Hooks> {
    // --- v2 Addition: Init-time fetch logic ---
    try {
        // Using undici fetch available in Node.js environments Kilo runs in.
        const response = await fetch(FLEET_ROSTER_URL);
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const rosterData: FleetRoster = await response.json();
        writeFileSync(CACHE_PATH, JSON.stringify(rosterData, null, 2), 'utf-8');
        rosterCache = rosterData; // Prime in-memory cache
        console.log(`safety-gate: Successfully fetched and cached fleet roster v${rosterData.version}.`);
    } catch (e) {
        // If fetch fails, the plugin will proceed and rely on the fallback tiers
        console.error(`safety-gate: FAILED to fetch fleet roster from ${FLEET_ROSTER_URL}. Will rely on cache/override/fallback. Error:`, e);
    }

    // --- Hooks definition (Unchanged structure) ---
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
