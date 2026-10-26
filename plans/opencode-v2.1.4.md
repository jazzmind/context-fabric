Plan: port context-fabric to opencode v2.1.4
Everything below is grounded in what we already verified (log evidence, V2 types from plugin.d.ts, the working mtplx-session-headers reference, the V2 config schema). Phases are ordered so the blocker is fixed first and each phase has a concrete done-criterion.
Situation summary
- Source of truth: ~/Code/utilities/context-fabric/.opencode/plugins/context-fabric.ts (417 lines) — byte-identical to the installed copy at ~/Code/eSales/.opencode/plugins/context-fabric.ts (just confirmed with diff).
- Current state: the plugin is auto-discovered from the project's .opencode/plugins/ dir (no config entry involved), loads, then fails schema validation on v2.1.4:
PluginModule.LoadError: Plugin must export a default definition with an
id and an effect or setup function. (SchemaError: Expected object at ["default"])
(repeated in opencode.log at 15:24 and 16:24 today). Result: all six hooks inert — no append-only logging, no auto-mode notes, no checkpoint-compaction prompt.
- The file's shape: helpers at lines 36–191, then export const ContextFabricPlugin: Plugin = async (ctx) => {...} at line 193, and export default ContextFabricPlugin at line 417. V2 rejects a bare function default export; it requires {id, server?/setup?}.
- Reference that works: ~/.config/opencode/plugins/mtplx-session-headers/server.js — export default { id, server: <v1-style hook function>, setup(ctx) {...} }. Proven loading on v2.1.4.
Phase 1 — export-shape fix (the actual blocker, ~5 min)
In upstream context-fabric.ts:
1. Keep the named export ContextFabricPlugin (line 193) untouched — hook bodies are unchanged; the V2 Hooks interface has every event the plugin registers, including the generic event hook and experimental.session.compacting (plugin.d.ts line 283, which is what drives the five-section checkpoint handoff).
2. Replace line 417 export default ContextFabricPlugin with:
export default {
  id: "context-fabric",
  server: ContextFabricPlugin,
}
3. Check the file's import ... from "@opencode-ai/plugin" — if the installed package version there predates V2, bump the dev dependency in the upstream package.json so tsc (there's a tsconfig.json) type-checks against the V2 types; if the import is type-only it's erased at runtime and the dep bump is just hygiene.
4. Sync to the install: either re-run install.sh (it does cp -R "$SRC_DIR/.opencode/plugins/." — first verify it's idempotent w.r.t. the AGENTS.md auto-mode block so we don't duplicate it) or copy the single file. Prefer the single-file copy now; treat install.sh re-run as part of Phase 6.
5. Reload + verify: v2 watches plugin files (log shows watcher subscribe on this exact file), so the edit should hot-reload; if not, restart opencode. Done-criterion: fresh loading plugin line for context-fabric.ts in opencode.log with no failed to load plugin WARN.
Phase 2 — hook-by-hook audit (read the full file, lines 193–416)
We confirmed the six event names exist in V2; this phase confirms the payload shapes still match, since V1→V2 renamed a few fields:
- For each hook (chat.message, chat.params, chat.headers, tool.execute.before, tool.execute.after, event): cross-check every input.* / output.* field the plugin reads against the V2 signatures in plugin.d.ts (lines 187–320). Known-good anchors from the working reference: input.sessionID, input.message.id, input.model.providerID, output.headers.
- experimental.session.compacting: confirm whether the plugin appends via context[] or replaces the full prompt, and that the V2 signature supports the same. This is the five-section checkpoint prompt — the plugin's signature feature, so it gets the closest read.
- Fix any mismatches in-place (expected: few or none — the file already targets the newer API surface, per its own header note about verifying against plugin source).
- Done-criterion: full read complete, any fixes applied, tsc --noEmit clean upstream.
Phase 3 — setup(ctx) block (only if Phase 2 finds gaps)
If any needed event is only reachable via ctx.session.hook(name, fn, {providerID}) (the V2-only channel the reference plugin uses for model.request), add a setup function to the default-export object. My expectation based on the event set: not needed. If we do add one, provider-scoping goes through the same {providerID} option.
Phase 4 — Python deps + slash-command verification
- pip install -r ~/Code/utilities/context-fabric/scripts/requirements.txt (pyyaml, jsonschema, requests — not installed; every /context-* command shells out to python3 scripts/*.py and would fail on import).
- Smoke: /context-auto status and /context-backend status from an eSales session.
- Verify command frontmatter (agent: build) is still valid in V2 — the top-level agent config key is deprecated in the V2 schema in favor of agents, so command frontmatter is the one spot I haven't yet confirmed. Done-criterion: one command runs end-to-end and reports real state.
Phase 5 — backend repointing (the only substantive code change beyond line 417)
scripts/lib/backends.py supports exactly two backends: ollama (default model qwen3.8:27b-mlx — likely not even pulled) and omlx (base :8000 — now an MTPLX port — but with model Qwen3.8-27B-8bit and omlx's api style, so it won't actually talk to MTPLX correctly). Two options:
- A (recommended): add an mtplx backend spec — OpenAI-compatible chat API, base http://127.0.0.1:8000, model mtplx-qwen38-27b-optimized-quality, plus a worker variant at :8001 with mtplx-qwen36-35b-a3b-optimized-speed for the cheap lane. Keeps the whole fabric on the one stack we're already running; no second model runtime.
- B (fallback): point the fabric at ollama only if we decide the 35b is resident there anyway.
- Then context_backend.py set mtplx and confirm the resolved backend/URL/model.
- Add/extend a test in the upstream tests/ for the new spec (there's an existing test dir; mirror whatever pattern it uses).
Phase 6 — upstream hygiene + install.sh
- Update the file header comment (currently claims hook signatures verified against @opencode-ai/plugin source as of 2026-08-21) with the v2.1.4 re-verification date and the {id, server} requirement, so the next person doesn't regress it.
- README: note opencode v2 support and the export-shape rule.
- Re-run install.sh into eSales to prove the installer ships the fixed plugin cleanly (and check the AGENTS.md block isn't duplicated).
- Commit upstream (it's its own repo, unlike eSales).
Phase 7 — end-to-end verification checklist
1. opencode.log: plugin loads, no WARN.
2. Do a few tool calls in eSales → .context-fabric/session-log/ (currently empty) gets ordered per-session records.
3. Auto mode on (AGENTS.md block) → .context-fabric/logs/ (also currently empty) starts filling; a synthetic [context-fabric:auto] note appears in-session.
4. /context-auto status clean (post-Phase 4).
5. Compaction: let a session cross ~49.5k tokens (or force it via the session compact API) → verify (a) the compaction request hit the 35B-A3B worker on :8001 (server logs), (b) the five-section checkpoint prompt was used, (c) the next pack version got scaffolded.
6. Append-only flag: trigger the reorder/rewrite scenario and confirm it warns rather than silently passes.
7. Memory: watch swap during the compaction run — the whole point of routing it to the worker.
Risks & rollback
- Payload drift (Phase 2): if V2 renamed fields the plugin reads, worst case the hooks no-op silently. Mitigated by the runtime evidence in step 2 of Phase 7 (log files filling) — that's the canary, not the type-checker.
- experimental.* hooks: the compacting hook is experimental in V2 naming; if it's been renamed, the checkpoint handoff degrades to stock compaction (no data loss, just no structured handoff). Caught in Phase 2.
- Rollback: revert line 417 upstream + re-copy (one line); the opencode config compaction fix is independent and already verified, stays either way.
Open questions for you
1. Phase 5, option A vs B — I recommend A (add the mtplx backend). Confirm, or say "just make it load" and I'll stop at Phase 4.
2. Re-run install.sh (Phase 6) vs single-file copy (Phase 1) — I lean copy-now, installer-later, to keep the first fix minimal and independently verifiable. Objections?