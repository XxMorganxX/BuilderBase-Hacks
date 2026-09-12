#!/usr/bin/env bash
# Install the ORACLE bridge on a developer laptop and wire it into Claude Code over SSH.
# Run from a checkout of this repository:  scripts/install-client.sh --oracle USER@HOST --principal NAME \
#   --project ID --repo /path/to/repo
# Idempotent: existing keys, known_hosts, and YAML are kept unless --force is given.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/install-client.sh --oracle USER@HOST --principal NAME --project ID --repo PATH
                                 [--port N] [--state-dir DIR] [--python BIN] [--skip-venv] [--force] [--register]

  --oracle USER@HOST  SSH destination of the Oracle server (the account whose authorized_keys the operator manages)
  --port N            SSH port (default 22)
  --principal NAME    Your Oracle identity; must match the name the operator enrolls
  --project ID        Oracle project you work on (one bridge config per project)
  --repo PATH         Local repository the coding agent works in
  --state-dir DIR     Private state (keys, known_hosts, configs, session DBs); default ~/.oracle
  --python BIN        Python interpreter >= 3.12 used for the venv (default python3)
  --skip-venv         Do not create/refresh .venv in this checkout
  --force             Overwrite an existing bridge YAML / registration JSON (never overwrites a key)
  --register          Register the session with Oracle now (after the operator has enrolled you)
EOF
}

ORACLE_HOST="" PORT=22 PRINCIPAL="" PROJECT="" REPO="" STATE_DIR="${HOME}/.oracle" PYTHON=python3
SKIP_VENV=0 FORCE=0 REGISTER=0
while [ $# -gt 0 ]; do
  case "$1" in
    --oracle) ORACLE_HOST="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --principal) PRINCIPAL="$2"; shift 2 ;;
    --project) PROJECT="$2"; shift 2 ;;
    --repo) REPO="$2"; shift 2 ;;
    --state-dir) STATE_DIR="$2"; shift 2 ;;
    --python) PYTHON="$2"; shift 2 ;;
    --skip-venv) SKIP_VENV=1; shift ;;
    --force) FORCE=1; shift ;;
    --register) REGISTER=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

fail() { echo "error: $*" >&2; exit 1; }
[ -n "$ORACLE_HOST" ] && [ -n "$PRINCIPAL" ] && [ -n "$PROJECT" ] && [ -n "$REPO" ] || { usage >&2; exit 2; }
case "$ORACLE_HOST" in -*|*" "*|"") fail "--oracle must look like user@host" ;; esac
case "$PRINCIPAL$PROJECT" in *[!A-Za-z0-9_.-]*) fail "--principal and --project may only contain letters, digits, . _ -" ;; esac
[ "$PORT" -ge 1 ] 2>/dev/null && [ "$PORT" -le 65535 ] || fail "--port must be 1-65535"
[ -d "$REPO" ] || fail "--repo $REPO is not a directory"
REPO="$(cd "$REPO" && pwd -P)"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
[ -f "$ROOT/pyproject.toml" ] || fail "run this script from a checkout of the oracle repository"

# --- prerequisites ---------------------------------------------------------------------------------------
for tool in git ssh ssh-keygen ssh-keyscan; do
  command -v "$tool" >/dev/null 2>&1 || fail "$tool is required"
done
command -v "$PYTHON" >/dev/null 2>&1 || fail "$PYTHON not found (use --python)"
"$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' \
  || fail "$PYTHON is $("$PYTHON" --version 2>&1); Python 3.12 or newer is required (use --python python3.12)"

# --- virtual environment ---------------------------------------------------------------------------------
VENV="$ROOT/.venv"
if [ "$SKIP_VENV" -eq 0 ]; then
  [ -x "$VENV/bin/python" ] || "$PYTHON" -m venv "$VENV"
  "$VENV/bin/python" -m pip install --quiet --disable-pip-version-check -e "$ROOT"
fi
[ -x "$VENV/bin/oracle-bridge" ] || fail "$VENV/bin/oracle-bridge missing; run without --skip-venv"

# --- private state and identity --------------------------------------------------------------------------
umask 077
mkdir -p "$STATE_DIR/keys"
chmod 700 "$STATE_DIR" "$STATE_DIR/keys"
KEY="$STATE_DIR/keys/${PRINCIPAL}_ed25519"
if [ ! -f "$KEY" ]; then
  ssh-keygen -q -t ed25519 -N "" -f "$KEY" -C "${PRINCIPAL}-oracle"
  echo "Created SSH identity $KEY (private key never leaves this machine)."
else
  echo "Using existing SSH identity $KEY."
fi
[ -f "$KEY.pub" ] || fail "$KEY.pub is missing; remove $KEY or restore its public half"

# --- host key ---------------------------------------------------------------------------------------------
KNOWN="$STATE_DIR/known_hosts"
HOSTNAME_ONLY="${ORACLE_HOST##*@}"
if [ ! -s "$KNOWN" ]; then
  ssh-keyscan -p "$PORT" -t ed25519,rsa,ecdsa "$HOSTNAME_ONLY" > "$KNOWN" 2>/dev/null || true
  [ -s "$KNOWN" ] || fail "could not fetch the host key of $HOSTNAME_ONLY:$PORT (is the Oracle host reachable?)"
  echo "Fetched host key for $HOSTNAME_ONLY:$PORT into $KNOWN."
fi
chmod 600 "$KNOWN"
echo
echo "================================================================================"
echo "  VERIFY THIS HOST KEY FINGERPRINT WITH THE ORACLE OPERATOR BEFORE CONTINUING"
echo "  (operator runs: ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub on the Oracle host)"
ssh-keygen -lf "$KNOWN" | sed 's/^/  /'
echo "  If it does not match, delete $KNOWN and stop: someone may be intercepting SSH."
echo "================================================================================"
echo

# --- bridge configuration -----------------------------------------------------------------------------------
YAML="$STATE_DIR/${PRINCIPAL}-${PROJECT}-bridge.yaml"
REG="$STATE_DIR/${PRINCIPAL}-${PROJECT}-registration.json"
STATE_DB="$STATE_DIR/${PRINCIPAL}-${PROJECT}-session.db"
MACHINE="$(hostname)"
if [ ! -f "$YAML" ] || [ "$FORCE" -eq 1 ]; then
  cat > "$YAML" <<EOF
principal: $PRINCIPAL
project_id: $PROJECT
repository: $REPO
state_file: $STATE_DB
transport: ssh
host: $ORACLE_HOST
port: $PORT
identity_file: $KEY
known_hosts: $KNOWN
command: oracle-server serve-ssh
heartbeat_seconds: 60
EOF
  echo "Wrote $YAML"
else
  echo "Keeping existing $YAML (use --force to rewrite)."
fi
if [ ! -f "$REG" ] || [ "$FORCE" -eq 1 ]; then
  cat > "$REG" <<EOF
{
  "project_id": "$PROJECT",
  "repository": "$(basename "$REPO")",
  "human_id": "$PRINCIPAL",
  "machine_id": "$MACHINE",
  "agent_type": "claude-code",
  "agent_session_id": "${PRINCIPAL}-${PROJECT}-${MACHINE}-1",
  "task": "",
  "interests": [],
  "pause_capability": "cooperative"
}
EOF
  echo "Wrote $REG"
else
  echo "Keeping existing $REG (use --force to rewrite)."
fi
chmod 600 "$YAML" "$REG"

# --- what to do next --------------------------------------------------------------------------------------
BRIDGE="$VENV/bin/oracle-bridge"
cat <<EOF

(a) SEND THIS PUBLIC KEY TO THE ORACLE OPERATOR (it is safe to share; the private key stays here):

$(cat "$KEY.pub")

    The operator enrolls you on the Oracle host with:

    oracle enroll $PRINCIPAL $PROJECT --pubkey /path/to/${PRINCIPAL}_ed25519.pub

(b) ADD THE BRIDGE TO CLAUDE CODE (user scope, available in every project):

    claude mcp add --scope user oracle -- $BRIDGE --config $YAML mcp

    Or commit this .mcp.json in the repository for project scope:

    {
      "mcpServers": {
        "oracle": {
          "command": "$BRIDGE",
          "args": ["--config", "$YAML", "mcp"]
        }
      }
    }

(c) AFTER THE OPERATOR CONFIRMS ENROLLMENT, VERIFY THE CONNECTION:

    $BRIDGE --config $YAML register $REG
    $BRIDGE --config $YAML checkpoint
    $BRIDGE --config $YAML status

    Expect a session id, connectivity ONLINE and an empty command list. FORBIDDEN means you are not
    enrolled yet or the principal/project do not match the enrollment.

(d) PASTE THIS INTO $REPO/CLAUDE.md SO THE AGENT USES ORACLE:

## ORACLE coordination (MCP server "oracle")
This repository is coordinated by ORACLE. The oracle_* tools talk to a bridge that only shares compact
project facts; never paste transcripts or secrets into them.
- At the start of a session call oracle_register with the values in $REG
  (set "task" to the current task and "interests" to the shared contracts you will touch).
- Call oracle_get_context at every task boundary and before designing a shared interface.
- Report assumptions with oracle_report_assumption and produced/consumed interfaces with
  oracle_report_interface as soon as you make them explicit; use oracle_report_question for missing specs.
- Before changing a shared contract (API shape, schema, identifier type, protocol) call oracle_gate and
  proceed only when status is ALLOW. Any other status (PAUSE, CLARIFICATION_REQUIRED, UNAVAILABLE) means do not change it.
- After each meaningful step call oracle_checkpoint and oracle_get_messages. On PAUSE: finish only the
  current safe atomic step, save your work, call oracle_ack_pause with the conflict_id/question_id, then
  wait for RESUME (poll oracle_get_messages; do not start new implementation work meanwhile).
- When a FINAL_DECISION arrives, read it, apply it, and acknowledge with oracle_reply(decision_id=...).
  Use oracle_request_resume once every decision is acknowledged.
EOF

if [ "$REGISTER" -eq 1 ]; then
  echo
  echo "Registering with Oracle at $ORACLE_HOST ..."
  "$BRIDGE" --config "$YAML" register "$REG"
  "$BRIDGE" --config "$YAML" status
fi
