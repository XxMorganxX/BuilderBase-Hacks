#!/usr/bin/env bash
# Hook entrypoint for Claude Code (SessionEnd) and Codex CLI (Stop).
# Must return almost instantly -- Claude Code's SessionEnd budget defaults
# to 1.5s and discards this script's stdout either way. Do the real work
# (LLM summarize + scp) in a backgrounded, detached worker instead.

payload="$(cat)"
dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
log="$(mktemp -t session-oracle-hook.XXXXXX.log)"

nohup "$dir/summarize-and-ship.sh" "$payload" "${SOURCE_AGENT:-unknown}" >"$log" 2>&1 &
disown

exit 0
