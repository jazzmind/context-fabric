/**
 * context-fabric — the policy layer for a cache-native agent runtime.
 *
 * Responsibilities:
 *   1. Append-only enforcement: log every tool call in strict order, per session,
 *      and flag (never silently allow) anything that looks like a reorder/rewrite.
 *   2. Prefix-invalidation detection: watch for tool-schema or source-slice changes
 *      that would silently break an already-primed context pack's cache hash.
 *   3. Checkpoint-based compaction: replace OpenCode's default compaction prompt
 *      entirely with one that produces a structured handoff and hands off to a NEW
 *      named pack version, instead of degrading the current one in place.
 *
 * Hook signatures below were checked against the authoritative Hooks interface in
 * @opencode-ai/plugin's own source (packages/plugin/src/index.ts) on 2026-08-21, not
 * just tutorial docs — see README.md's "What's actually implemented vs. left for you
 * to verify" section for the exact source used. Two important corrections from an
 * earlier draft of this plugin: (1) `file.edited` and `session.compacted` are NOT
 * their own top-level hooks — they only arrive as `event.type` values inside the
 * generic `event` hook; (2) `tool.definition` fires once PER TOOL with
 * `input: { toolID }` / `output: { description, parameters }`, not once for a whole
 * tool-list array. Even so, this plugin still fails soft: if your installed OpenCode
 * version's actual behavior differs, it logs a warning to
 * .context-fabric/logs/plugin-warnings.log and no-ops rather than crashing your
 * session.
 */
import type { Plugin, PluginInput } from "@opencode-ai/plugin"
import type { Part } from "@opencode-ai/sdk"
import { appendFileSync, mkdirSync, readFileSync, writeFileSync, existsSync } from "node:fs"
import { join, relative, isAbsolute } from "node:path"
import { createHash, randomUUID } from "node:crypto"

const CF_DIR = ".context-fabric"
const LOG_DIR = join(CF_DIR, "logs")
const SESSION_LOG_DIR = join(CF_DIR, "session-log")

function ensureDirs(root: string) {
  for (const d of [LOG_DIR, SESSION_LOG_DIR]) {
    const full = join(root, d)
    if (!existsSync(full)) mkdirSync(full, { recursive: true })
  }
}

function warn(root: string, message: string) {
  try {
    ensureDirs(root)
    appendFileSync(
      join(root, LOG_DIR, "plugin-warnings.log"),
      `[${new Date().toISOString()}] ${message}\n`,
    )
  } catch {
    // Last resort: don't let logging failures break the session either.
  }
}

function appendSessionLog(root: string, sessionID: string, record: Record<string, unknown>) {
  try {
    ensureDirs(root)
    const file = join(root, SESSION_LOG_DIR, `${sessionID}.jsonl`)
    appendFileSync(file, JSON.stringify({ ts: new Date().toISOString(), ...record }) + "\n")
  } catch (e) {
    warn(root, `appendSessionLog failed: ${e}`)
  }
}

function hashOf(text: string): string {
  return "sha256:" + createHash("sha256").update(text).digest("hex")
}

/** Reads the current active pack's name from .context-fabric/history/pack-events.jsonl
 * (the last "primed" event), mirroring scripts/context_status.py's find_active_pack(). */
function activePackName(root: string): string | null {
  try {
    const path = join(root, CF_DIR, "history", "pack-events.jsonl")
    if (!existsSync(path)) return null
    const lines = readFileSync(path, "utf-8").trim().split("\n").filter(Boolean)
    let last: string | null = null
    for (const line of lines) {
      try {
        const ev = JSON.parse(line)
        if (ev.event === "primed") last = ev.context_pack
      } catch {
        /* skip malformed line, append-only log should never need rewriting to fix this */
      }
    }
    return last
  } catch {
    return null
  }
}

/** Tracks the last-seen (description, parameters) hash per tool ID, to detect the
 * "tool schema changed" invalidation case /context-status reports on. Keyed by
 * toolID, not sessionID — `tool.definition` fires per-tool and is not session-scoped. */
const toolSchemaHash = new Map<string, string>()

/** Normalizes a possibly-absolute file path from an event/tool payload to a path
 * relative to the project root, so it can be matched against the relative paths
 * stored in context_pack YAML source_slices. */
function toProjectRelativePath(root: string, path: string): string {
  if (isAbsolute(path)) return relative(root, path)
  return path
}

const CONFIG_PATH = join(CF_DIR, "config.json")

/** Reads whether auto mode is on: CONTEXT_FABRIC_AUTO env var wins if set (0/false/off/no
 * -> false, 1/true/on/yes -> true), else .context-fabric/config.json's `auto` field, else
 * default ON. Fails soft (never throws) — a missing/corrupt config should not disable the
 * feature it's supposed to control. */
function readAutoEnabled(root: string): boolean {
  const envVal = process.env.CONTEXT_FABRIC_AUTO
  if (envVal !== undefined) {
    const normalized = envVal.trim().toLowerCase()
    if (["0", "false", "off", "no"].includes(normalized)) return false
    if (["1", "true", "on", "yes"].includes(normalized)) return true
  }
  try {
    const configPath = join(root, CONFIG_PATH)
    if (!existsSync(configPath)) return true
    const raw = JSON.parse(readFileSync(configPath, "utf-8"))
    if (typeof raw?.auto === "boolean") return raw.auto
    return true
  } catch {
    return true
  }
}

// Session-scoped auto-mode state. Module-level Sets/Map are fine here because a single
// plugin instance is created per OpenCode process, not per request.
const autoPlannedSessions = new Set<string>()
const pendingAutoCheckpoint = new Set<string>()
let lastAutoIndexAt = 0
const AUTO_INDEX_MIN_INTERVAL_MS = 15_000

type ShellResult = { ok: boolean; text: string }

async function runShell(shell: PluginInput["$"], root: string, cmd: ReturnType<PluginInput["$"]>): Promise<ShellResult> {
  try {
    const result = await cmd.cwd(root).nothrow().quiet()
    const text = await result.text()
    return { ok: result.exitCode === 0, text }
  } catch (e) {
    warn(root, `auto-mode shell command failed: ${e}`)
    return { ok: false, text: String(e) }
  }
}

async function autoIndex(shell: PluginInput["$"], root: string): Promise<ShellResult> {
  return runShell(shell, root, shell`python3 scripts/context_index.py`)
}

async function autoPlan(shell: PluginInput["$"], root: string, task: string): Promise<ShellResult> {
  // Bun shell template-literal interpolation auto-quotes `task` as a single safe argument —
  // no manual escaping needed, and it must NOT be pre-escaped or it will be double-escaped.
  return runShell(shell, root, shell`python3 scripts/context_plan.py --task ${task}`)
}

async function autoCheckpoint(shell: PluginInput["$"], root: string, pack: string): Promise<ShellResult> {
  return runShell(shell, root, shell`python3 scripts/context_checkpoint.py --pack ${pack}`)
}

/** Builds a synthetic TextPart carrying an auto-mode note, tagged so the agent (via
 * AGENTS.md's standing instructions) recognizes it as something to act on directly rather
 * than a normal user message. `synthetic: true` matches the SDK's own TextPart field for
 * exactly this purpose (see @opencode-ai/sdk's Part type). */
function makeNotePart(sessionID: string, messageID: string, text: string): Part {
  return {
    id: `cf-auto-${randomUUID()}`,
    sessionID,
    messageID,
    type: "text",
    text,
    synthetic: true,
  } as Part
}

export const ContextFabricPlugin: Plugin = async (ctx) => {
  const root = ctx.directory
  const shell = ctx.$

  return {
    // --- Auto mode: reindex/plan/checkpoint without waiting to be asked ---------
    // See README's "Auto mode" section. Deterministic steps (index/plan/checkpoint
    // scripts) run here directly; reasoning steps (finalizing invariants, writing the
    // checkpoint block, deciding when to prime) are delegated to whichever agent is
    // already running the session, via an injected [context-fabric:auto] note plus
    // standing instructions in the project's root AGENTS.md telling it to act on that
    // tag autonomously -- pausing only for genuine `unknowns`.
    "chat.message": async (input: any, output: { message: any; parts: Part[] }) => {
      try {
        if (!readAutoEnabled(root)) return
        const sessionID: string = input?.sessionID ?? "unknown-session"
        const messageID: string = input?.messageID ?? output?.message?.id ?? randomUUID()

        const firstTextPart = output.parts.find((p: any) => p?.type === "text") as { text?: string } | undefined
        const userText = firstTextPart?.text ?? ""

        const notes: string[] = []

        // Throttled reindex on every message -- cheap, no reasoning needed.
        if (Date.now() - lastAutoIndexAt > AUTO_INDEX_MIN_INTERVAL_MS) {
          lastAutoIndexAt = Date.now()
          const result = await autoIndex(shell, root)
          if (!result.ok) warn(root, `auto-mode autoIndex failed: ${result.text}`)
        }

        // Right after compaction: scaffold the next pack version, then nudge the agent
        // to fill in its checkpoint block and re-prime on its own.
        if (pendingAutoCheckpoint.has(sessionID)) {
          pendingAutoCheckpoint.delete(sessionID)
          const pack = activePackName(root)
          if (pack) {
            const result = await autoCheckpoint(shell, root, pack)
            if (result.ok) {
              notes.push(
                `[context-fabric:auto] Compaction just finished and I scaffolded the next context pack version from ${pack} (see the checkpoint script's output above, and .context-fabric/packs / .context-fabric/history, for the exact new pack path/version). Fill in that pack's checkpoint block from the handoff you just wrote, re-derive source_slices/invariants/acceptance_tests for the next unit of work, then run /context-prime on it yourself now. Don't wait for me to ask.`,
              )
            } else {
              warn(root, `auto-mode autoCheckpoint failed for pack ${pack}: ${result.text}`)
            }
          }
        }

        // First substantial message of a session: auto-draft a pack from the code
        // graph and nudge the agent to finalize + prime it. Skips trivial messages
        // and explicit slash-command invocations (already have their own flow).
        if (
          !autoPlannedSessions.has(sessionID) &&
          userText.length >= 12 &&
          !userText.startsWith("/")
        ) {
          autoPlannedSessions.add(sessionID)
          const result = await autoPlan(shell, root, userText)
          if (result.ok) {
            notes.push(
              `[context-fabric:auto] I drafted a context pack for this task from the static code graph (see the plan script's output above for its exact path/name). Review the code cone above, finalize invariants + acceptance_tests, resolve unknowns, and refine subtasks, then run /context-prime <name>:v1 yourself. Only pause to ask me if something under unknowns genuinely needs my input.`,
            )
          } else {
            warn(root, `auto-mode autoPlan failed for session ${sessionID}: ${result.text}`)
          }
        }

        if (notes.length > 0) {
          output.parts.push(makeNotePart(sessionID, messageID, notes.join("\n\n")))
        }
      } catch (e) {
        warn(root, `chat.message auto-mode hook failed: ${e}`)
      }
    },

    // --- Append-only enforcement -------------------------------------------------
    "tool.execute.before": async (input: any, _output: any) => {
      try {
        const sessionID = input?.sessionID ?? "unknown-session"
        appendSessionLog(root, sessionID, {
          kind: "tool.execute.before",
          tool: input?.tool,
          context_pack: activePackName(root),
        })
      } catch (e) {
        warn(root, `tool.execute.before failed: ${e}`)
      }
    },

    "tool.execute.after": async (input: any, output: any) => {
      try {
        const sessionID = input?.sessionID ?? "unknown-session"
        // We log a size proxy, not the raw content, to keep the session log itself
        // cheap — the actual content lives in OpenCode's own session history.
        const contentStr = typeof output?.output === "string" ? output.output : JSON.stringify(output?.output ?? "")
        appendSessionLog(root, sessionID, {
          kind: "tool.execute.after",
          tool: input?.tool,
          content: contentStr.length > 500 ? contentStr.slice(0, 500) + "…" : contentStr,
          context_pack: activePackName(root),
        })
      } catch (e) {
        warn(root, `tool.execute.after failed: ${e}`)
      }
    },

    // --- Invalidation detection: a tool's own schema changing mid-session --------
    // Fires once per tool (input.toolID identifies which one), not once for the
    // whole tool list — output is that single tool's { description, parameters }.
    "tool.definition": async (input: any, output: any) => {
      try {
        const toolID: string = input?.toolID ?? "unknown-tool"
        const schemaText = JSON.stringify({ description: output?.description, parameters: output?.parameters })
        const currentHash = hashOf(schemaText)
        const seen = toolSchemaHash.get(toolID)
        if (seen && seen !== currentHash) {
          const pack = activePackName(root)
          // Include the active pack name in the message itself (not just the log
          // context) so scripts/context_status.py's plain substring match against
          // pack_name in plugin-warnings.log can actually surface this warning.
          warn(
            root,
            `Tool schema changed mid-session for '${toolID}': ${seen} -> ${currentHash}. ` +
              (pack ? `Active pack ${pack}'s ` : `Any primed pack's `) +
              `prefix_hash is now stale — run /context-checkpoint before continuing, or ` +
              `/context-status will keep reporting a stale hit rate.`,
          )
        }
        toolSchemaHash.set(toolID, currentHash)
      } catch (e) {
        warn(root, `tool.definition hook failed: ${e}`)
      }
    },

    // --- Inject the primed pack's immutable prefix into the system prompt -------
    // Confirmed against @opencode-ai/plugin's Hooks type: input is
    // { sessionID?, model }, output is { system: string[] }. Still, the
    // guaranteed, version-independent way to get the prefix in is the
    // /context-prime command's `!`command`` shell injection (see
    // .opencode/commands/context-prime.md) — this hook is a nicer-UX layer on
    // top of that, not a replacement for it.
    "experimental.chat.system.transform": async (_input: any, output: any) => {
      try {
        const pack = activePackName(root)
        if (!pack) return
        const prefixPath = join(root, CF_DIR, "prefixes", `${pack.replace(":", "-")}.prefix.txt`)
        if (!existsSync(prefixPath)) return
        const prefix = readFileSync(prefixPath, "utf-8")
        if (Array.isArray(output?.system)) {
          output.system.push(`<context-fabric-pack name="${pack}">\n${prefix}\n</context-fabric-pack>`)
        }
      } catch (e) {
        warn(root, `experimental.chat.system.transform failed: ${e}`)
      }
    },

    // --- Checkpoint-based compaction: replace the default prompt entirely -------
    "experimental.session.compacting": async (input: any, output: any) => {
      try {
        const pack = activePackName(root)
        output.prompt = [
          "You are producing a TASK CHECKPOINT, not a generic summary.",
          pack ? `Active context pack: ${pack}.` : "No context pack is currently primed.",
          "",
          "Produce exactly these five sections, each as a bullet list:",
          "1. changed_files — every file touched since the pack was primed",
          "2. verified_facts — things you confirmed true by reading code or running tests",
          "3. failed_hypotheses — approaches you tried and ruled out, and why",
          "4. test_status — current pass/fail state, verbatim if you have it",
          "5. next_decision — the single next decision or action, stated concretely",
          "",
          "Do not restate the immutable prefix. Do not summarize files you did not touch.",
          "This handoff becomes the seed for the next context_pack version via",
          "/context-checkpoint — write it so scripts/context_checkpoint.py's `checkpoint`",
          "block could be filled in directly from it.",
        ].join("\n")
      } catch (e) {
        warn(root, `experimental.session.compacting failed: ${e}`)
      }
    },

    // --- session.compacted and file.edited are EVENTS, not their own hooks ------
    // Both only arrive as `event.type` inside the generic `event` hook. Property
    // field name is `event.properties` per @opencode-ai/plugin's own Event types;
    // we also check `event.data` as a fallback in case your installed version
    // differs, since third-party docs aren't fully consistent on this point.
    event: async (input: { event: any }) => {
      try {
        const event = input?.event ?? {}
        if (event.type === "session.compacted") {
          const sessionID = event.properties?.sessionID ?? event.data?.sessionID ?? "unknown-session"
          const pack = activePackName(root)
          appendSessionLog(root, sessionID, {
            kind: "session.compacted",
            context_pack: pack,
            note: "Run /context-checkpoint to turn this compaction into a new named pack version.",
          })
          if (readAutoEnabled(root)) pendingAutoCheckpoint.add(sessionID)
          return
        }
        if (event.type === "file.edited") {
          const pack = activePackName(root)
          if (!pack) return
          const packPath = join(root, CF_DIR, "packs", `${pack.replace(":", "-")}.yaml`)
          if (!existsSync(packPath)) return
          const packText = readFileSync(packPath, "utf-8")
          const rawPath: string | undefined = event.properties?.file ?? event.data?.file ?? event.data?.path
          if (!rawPath) return
          const editedPath = toProjectRelativePath(root, rawPath)
          if (packText.includes(editedPath)) {
            warn(
              root,
              `${editedPath} is a source_slice of primed pack ${pack} and was just edited. ` +
                `The pack's prefix_hash is now stale. Re-run /context-prime ${pack} (it will refuse ` +
                `and tell you to /context-checkpoint instead, which is the correct next step).`,
            )
          }
        }
      } catch (e) {
        warn(root, `event hook failed: ${e}`)
      }
    },
  }
}

export default ContextFabricPlugin
