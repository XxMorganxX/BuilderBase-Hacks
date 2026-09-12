# Install and connect a developer bridge

The bridge runs on the developer's machine next to their coding session. It does not require a local model or NemoClaw.

Using Claude Code? `scripts/install-client.sh` performs every step below and prints the exact `claude mcp add` command; the end-to-end walkthrough for two laptops and one Oracle host is in [claude-code.md](claude-code.md). The manual steps follow.

## Install

Copy the ORACLE repository to this machine and run:

```bash
cd /path/to/oracle
python3.12 -m venv .venv
.venv/bin/python -m pip install -e .
```

Requirements: Python 3.12+, Git, and the OpenSSH client. The first client integration is generic MCP; product-specific hooks and forceful process interruption are not required for the demo.

## Create your SSH identity

```bash
ssh-keygen -t ed25519 -f ~/.ssh/oracle_ed25519 -C alice-oracle
```

Send the `.pub` file to the Oracle host operator. Keep the private key local. Ask the operator for the verified server host key and install it in your known_hosts. The operator installs the restricted key binding and grants your project membership in one step (`oracle enroll alice backend --pubkey alice_ed25519.pub`); registration fails with FORBIDDEN until that has happened.

## Create bridge configuration

Save an `alice-bridge.yaml` file. Paths are resolved relative to this file; absolute paths are also accepted.

```yaml
principal: alice
project_id: backend
repository: /absolute/path/to/backend
state_file: /absolute/path/to/private-state/alice-session-1.db
transport: ssh
host: oracle-user@oracle-host
port: 22
identity_file: ~/.ssh/oracle_ed25519
known_hosts: ~/.ssh/known_hosts
command: oracle-server serve-ssh
heartbeat_seconds: 60
```

Use a separate state_file for each coding session. The server command is selected by the host-side forced key binding, so this configuration cannot impersonate another principal.

## Register and check connectivity

Save `registration.json`:

```json
{
  "project_id": "backend",
  "repository": "backend",
  "human_id": "alice",
  "machine_id": "alice-laptop",
  "agent_type": "generic-mcp",
  "agent_session_id": "alice-backend-1",
  "task": "Implement authentication",
  "interests": ["User.id", "AuthenticationResponse"],
  "pause_capability": "cooperative"
}
```

```bash
/path/to/oracle/.venv/bin/oracle-bridge --config alice-bridge.yaml register registration.json
/path/to/oracle/.venv/bin/oracle-bridge --config alice-bridge.yaml checkpoint
/path/to/oracle/.venv/bin/oracle-bridge --config alice-bridge.yaml status
```

Expect a session ID, ONLINE connectivity, and a checkpoint response. In a second terminal, run `oracle-bridge --config alice-bridge.yaml start` for heartbeats and background delivery. This process does not read full transcripts or acknowledge safe pauses on the agent's behalf.

## Connect an MCP coding client

Configure a local stdio MCP server using your client's supported configuration UI:

```json
{
  "mcpServers": {
    "oracle": {
      "command": "/absolute/path/to/oracle/.venv/bin/oracle-bridge",
      "args": ["--config", "/absolute/path/alice-bridge.yaml", "mcp"]
    }
  }
}
```

This is a common example shape, not a claim that every coding product uses the same config filename or JSON structure. Supply the command and arguments through the client's own MCP setup. The server uses the official MCP Python SDK v1 compatibility line.

The tool list should contain 14 names: oracle_register, oracle_get_context, oracle_report_task, oracle_report_progress, oracle_report_assumption, oracle_report_decision, oracle_report_interface, oracle_report_question, oracle_checkpoint, oracle_gate, oracle_get_messages, oracle_reply, oracle_ack_pause, and oracle_request_resume. (The specification writes these with dots, e.g. `oracle.gate`; MCP clients such as Claude Code only accept `[a-zA-Z0-9_-]` in tool names, so the wire names use underscores.)

Add this workflow to your coding session instructions:

> Read Oracle context at task boundaries. Report assumptions and interfaces explicitly. Before a shared-contract change call oracle_gate and proceed only on ALLOW. On PAUSE, finish only the current safe atomic action, save work, and call oracle_ack_pause. Review final decisions and acknowledge them with oracle_reply. Resume implementation only after Oracle permits it.

A fuller CLAUDE.md snippet is printed by `scripts/install-client.sh` and reproduced in [claude-code.md](claude-code.md).

MCP offers tools; it cannot guarantee that every client invokes them. A client that ignores checkpoints is unmonitored in practice. Native hooks or supervised execution are a separate integration task.

## Outages and reconnect

Checkpoints and reports persist in the local outbox and retry with the same message ID. Shared-contract gates return UNAVAILABLE and do not authorize execution. Run `oracle-bridge --config ... flush`, then `messages`, to resynchronize. Restarting the bridge preserves session identity and inbox state.

If status shows failed reports, inspect the local outbox response; deterministic authorization/schema errors are retained instead of retried forever. If the SSH host key changes, verify the replacement with the operator before updating known_hosts.

