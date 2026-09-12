// OpenClaw plugin: on agent_end, ship a summary of the run to the
// data-drop oracle box via the same bin/session-oracle-hook.sh used by the
// Claude Code and Codex CLI adapters.
//
// BEST-EFFORT / UNTESTED against a live OpenClaw gateway. Sourced from
// OpenClaw's public plugin docs (definePluginEntry, api.on) and the
// `oh-my-claw` example plugin (agent_end field names: messages, sessionKey,
// runId, channelId), compat >=2026.4.12. If this doesn't fire, the field
// names on `event` below are the first thing to check against your
// installed OpenClaw version.
//
// agent_end is documented as fire-and-forget from the gateway's side (it
// only .catch()s the handler) -- we don't need to await or gate anything,
// just capture the transcript and hand off.

import { definePluginEntry } from "openclaw/plugin-sdk/core";
import { spawn } from "node:child_process";
import { writeFileSync, mkdtempSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

export default definePluginEntry({
  id: "session-oracle-hook",
  name: "session oracle hook",
  description: "Ships a summarized copy of each OpenClaw agent run to the data-drop oracle box.",

  register(api) {
    const scriptPath = api.pluginConfig?.hookScriptPath;
    if (!scriptPath) {
      api.logger?.warn?.("[session-oracle-hook] pluginConfig.hookScriptPath not set, hook disabled");
      return;
    }

    api.on("agent_end", (event) => {
      try {
        const messages = event?.messages ?? [];
        const sessionId = event?.sessionKey || event?.runId || "unknown";

        const dir = mkdtempSync(join(tmpdir(), "openclaw-session-"));
        const transcriptPath = join(dir, "transcript.json");
        writeFileSync(transcriptPath, JSON.stringify(messages));

        const stdinPayload = JSON.stringify({
          transcript_path: transcriptPath,
          session_id: sessionId,
          cwd: event?.cwd || process.cwd(),
          hook_event_name: "agent_end",
        });

        const child = spawn(scriptPath, [], {
          env: { ...process.env, SOURCE_AGENT: "openclaw" },
          stdio: ["pipe", "ignore", "ignore"],
          detached: true,
        });
        child.stdin.write(stdinPayload);
        child.stdin.end();
        child.unref();
      } catch (err) {
        api.logger?.warn?.(`[session-oracle-hook] agent_end handler failed: ${err?.message ?? err}`);
      }
    });
  },
});
