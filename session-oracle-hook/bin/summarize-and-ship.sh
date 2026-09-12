#!/usr/bin/env bash
# Worker: summarize a coding-agent session transcript via `claude -p` and
# scp the result to the data-drop oracle box. Invoked detached by
# session-oracle-hook.sh -- no host timeout applies here. Every failure
# path logs to stderr and exits 0; this must never surface an error to the
# host agent.

payload="$1"
agent="${2:-unknown}"

transcript_path="$(printf '%s' "$payload" | jq -r '.transcript_path // empty')"
session_id="$(printf '%s' "$payload" | jq -r '.session_id // "unknown"')"
cwd="$(printf '%s' "$payload" | jq -r '.cwd // empty')"

if [ -z "$transcript_path" ] || [ ! -f "$transcript_path" ]; then
  echo "session-oracle-hook: no transcript at '$transcript_path', skipping" >&2
  exit 0
fi

ts="$(date -u +%Y%m%dT%H%M%SZ)"
work="$(mktemp -d)"
out="$work/${agent}_${session_id}_${ts}.json"
raw="$work/raw.txt"

prompt=$(cat <<EOF
You are summarizing a coding-agent session transcript for an automated
routing "oracle" that decides which downstream system should receive it.
Output ONLY valid JSON, no markdown fences, no commentary, matching exactly
this shape:
{
  "schema_version": "1.0",
  "source_agent": "$agent",
  "session_id": "$session_id",
  "cwd": "$cwd",
  "generated_at": "$ts",
  "high_level": {
    "task": "one-line description of what was asked",
    "outcome": "done | partial | blocked | failed",
    "summary": "2-4 sentence narrative summary",
    "key_decisions": ["..."],
    "next_steps": ["..."]
  },
  "low_level": {
    "files_changed": ["..."],
    "commands_run": ["..."],
    "tool_call_count": 0,
    "errors": ["..."]
  }
}
Transcript follows:
EOF
)

if ! claude -p "$prompt" < "$transcript_path" > "$raw" 2>"$work/err.log"; then
  echo "session-oracle-hook: claude -p failed: $(cat "$work/err.log")" >&2
  rm -rf "$work"
  exit 0
fi

# Models sometimes wrap JSON in a ```json fence despite instructions not
# to -- strip fence lines before validating rather than failing open on them.
grep -v '^```' "$raw" > "$work/stripped.json"

if ! jq . "$work/stripped.json" > "$out" 2>/dev/null; then
  jq -n --arg raw "$(cat "$raw")" --arg agent "$agent" --arg session_id "$session_id" --arg ts "$ts" \
    '{schema_version: "1.0", source_agent: $agent, session_id: $session_id, generated_at: $ts, parse_error: true, raw: $raw}' \
    > "$out"
fi

if scp -o BatchMode=yes -o ConnectTimeout=20 "$out" data-drop:~/Documents/dev/data-drop/ 2>>"$work/err.log"; then
  echo "session-oracle-hook: shipped $(basename "$out")" >&2
else
  echo "session-oracle-hook: scp failed: $(cat "$work/err.log")" >&2
fi

rm -rf "$work"
exit 0
