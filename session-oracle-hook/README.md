# session-oracle-hook

Fires when a coding-agent session ends, summarizes the transcript with
`claude -p` into a fixed high-level/low-level JSON shape, and scps it to a
drop box for downstream routing (the "oracle").

Works today with **Claude Code** and **Codex CLI** (same JSON-over-stdio
hook wire protocol). **OpenClaw** support is a best-effort native plugin,
untested against a live gateway -- see the caveat in `adapters/openclaw/`.

## How it works

1. `bin/session-oracle-hook.sh` is registered as the hook command. It reads
   the hook's JSON payload from stdin, immediately backgrounds
   `bin/summarize-and-ship.sh` (detached, `nohup ... &`), and exits. This
   matters because Claude Code's SessionEnd hook budget defaults to 1.5s and
   discards the hook's stdout either way -- the real work can't run inline.
2. `bin/summarize-and-ship.sh` pipes the session transcript into
   `claude -p` with a fixed prompt forcing the JSON shape documented in
   `schema/summary.schema.json`, validates it's parseable JSON (falls back
   to `{parse_error: true, raw: "..."}` if not), and `scp`s the result to
   `data-drop:~/Documents/dev/data-drop/`.

## Prerequisites

- `claude`, `jq`, `scp` on PATH.
- An SSH config alias named `data-drop` that resolves non-interactively
  (no password/passphrase prompts) to wherever the drop box is. Example
  (adjust host/user/port for your setup):

  ```
  Host data-drop
      HostName localhost
      Port 2222
      User dell
      ProxyJump you@your-jump-host.example.edu
      IdentityFile ~/.ssh/data_drop_ed25519
      IdentitiesOnly yes
  ```

  Generate a dedicated keypair, install it on the destination box's
  `authorized_keys` (through the jump host if there is one), and if the
  jump host itself needs a key passphrase, unlock it once with
  `ssh-add --apple-use-keychain <key>` (macOS) so it survives reboots.
  Hooks run non-interactively -- if any hop still needs a password, the
  scp will hang or fail silently in the background log.

## Install

Clone this repo somewhere stable, then wire in the adapter for your tool.

### Claude Code

Merge `adapters/claude-code/settings.snippet.json` into your
`settings.json` (project or user level), replacing the placeholder path
with the real path to `bin/session-oracle-hook.sh`. Fires once per session
on `SessionEnd`.

### Codex CLI

Merge `adapters/codex/hooks.snippet.json` into `~/.codex/hooks.json` (or a
project-level `.codex/hooks.json`), replacing the placeholder path. Codex
has no SessionEnd event, so this registers on `Stop` instead -- fires once
per turn where the agent decides it's done. For one-shot `codex exec` usage
that's effectively session-end; for long interactive sessions you'll get
incremental summaries, which is harmless (each one just ships to the drop
box under the same session_id, different timestamp).

### OpenClaw (best-effort, untested)

`adapters/openclaw/` is a native plugin skeleton: `index.js` registers
`api.on("agent_end", ...)`, pulls `event.messages` / `event.sessionKey`,
writes them to a temp file, and shells out to
`bin/session-oracle-hook.sh`. Install it as an OpenClaw plugin, set
`pluginConfig.hookScriptPath` to the absolute path of
`bin/session-oracle-hook.sh`, and check your gateway logs -- the
`agent_end` event field names were sourced from OpenClaw's public docs and
one example plugin (`oh-my-claw`), not verified against a running gateway.
If it doesn't fire or the transcript comes through empty, that's the first
thing to check.

## Output shape

See `schema/summary.schema.json`. Every summary carries `source_agent` so
the oracle can branch per-tool, and `parse_error` so a bad LLM response
never silently corrupts the drop.

## Smoke test

```sh
echo '{"transcript_path":"/path/to/any.jsonl","session_id":"smoketest","cwd":"/tmp"}' \
  | SOURCE_AGENT=manual-test bin/session-oracle-hook.sh
```

Then check `~/Documents/dev/data-drop/` on the drop box (or tail the log
path printed by `session-oracle-hook.sh` -- it's the `mktemp` path under
`$TMPDIR`) for `manual-test_smoketest_*.json`.
