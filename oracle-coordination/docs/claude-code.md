# Two laptops, one Oracle: Claude Code quickstart

This walkthrough connects two developers who each run Claude Code on their own laptop to one Oracle server over SSH. Laptop A works on `backend`, Laptop B on `web`; both depend on the shared contract `User.id`. Names used below: operator account `oracle` on host `oracle.example.org`, developers `alice` (A) and `bob` (B). Substitute your own.

Three roles, three machines:

| Where | Who | Does |
| --- | --- | --- |
| Oracle host | operator (`owner`) | creates projects, shares contracts, enrolls each developer's SSH key, answers questions, runs worker + dashboard |
| Laptop A | alice | `scripts/install-client.sh`, `claude mcp add`, works in `backend` with Claude Code |
| Laptop B | bob | same, in `web` |

Nothing on a laptop can impersonate another principal: the operator binds each public key to a forced command that fixes `ORACLE_PRINCIPAL`, and the bridge refuses unknown host keys.

## 1. Operator: prepare the Oracle host

Install the server once ([install-server.md](install-server.md)). Every `oracle` command below needs to know which database to use and who is acting, so export both in the operator shell:

```bash
cd /path/to/oracle                      # the checkout with .venv on the Oracle host
export ORACLE_DB="$PWD/runtime/oracle.db"   # absolute; the same path is baked into every enrolled key
export ORACLE_PRINCIPAL=owner               # the human who owns the projects
export PATH="$PWD/.venv/bin:$PATH"

oracle init backend --owner owner
oracle init web --owner owner
oracle share backend web User.id
oracle share backend web AuthenticationResponse
```

Keep the operator's own login key unrestricted and separate. Everything the developers get is a `restrict,command=...` entry in `~oracle/.ssh/authorized_keys`, written by `oracle enroll` (next section). Do not run `oracle enroll` under a different `ORACLE_DB` than the server uses, or the developer will talk to an empty database.

Start the two long-running processes in two terminals (or under `nohup`/systemd):

```bash
oracle worker                                  # semantic reasoning on the local model; optional but recommended
oracle dashboard --host 0.0.0.0 --port 8765    # read-only view; open http://oracle.example.org:8765
```

The dashboard is read-only and shows sessions, questions, conflicts, decisions and the audit timeline of everything that happens.

## 2. Laptop A and Laptop B: install the client

On each laptop, clone this repository and run the installer. It checks Python 3.12+/git/ssh, creates `.venv`, generates a dedicated ed25519 key, fetches and prints the server host-key fingerprint, and writes the bridge configuration and a registration file into `~/.oracle`.

Laptop A:

```bash
git clone <oracle repo> ~/src/oracle && cd ~/src/oracle
scripts/install-client.sh --oracle oracle@oracle.example.org --principal alice --project backend --repo ~/src/backend
```

Laptop B:

```bash
git clone <oracle repo> ~/src/oracle && cd ~/src/oracle
scripts/install-client.sh --oracle oracle@oracle.example.org --principal bob --project web --repo ~/src/web
```

Flags: `--port N` for a non-standard SSH port, `--state-dir DIR` instead of `~/.oracle`, `--python python3.12` if `python3` is older, `--skip-venv` to reuse an existing venv, `--force` to rewrite the YAML/JSON (keys are never overwritten), `--register` to register at the end (only useful after enrollment).

The script prints four blocks. Act on them in order:

1. **Verify the host-key fingerprint.** Compare the printed `SHA256:...` with what the operator sees from `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` on the Oracle host. If it differs, delete `~/.oracle/known_hosts` and stop.
2. **Send the public key to the operator** (`~/.oracle/keys/alice_ed25519.pub`, one line starting with `ssh-ed25519`). Only the `.pub` file leaves the laptop.
3. **Wait for the operator to enroll you** (section 3), then verify the connection:

   ```bash
   ~/src/oracle/.venv/bin/oracle-bridge --config ~/.oracle/alice-backend-bridge.yaml register ~/.oracle/alice-backend-registration.json
   ~/src/oracle/.venv/bin/oracle-bridge --config ~/.oracle/alice-backend-bridge.yaml checkpoint
   ~/src/oracle/.venv/bin/oracle-bridge --config ~/.oracle/alice-backend-bridge.yaml status
   ```

   Expect a session id, connectivity `ONLINE` and no pending commands. Edit `task` and `interests` in the registration JSON first if you already know them (`"interests": ["User.id"]`).
4. **Add the MCP server to Claude Code** with the printed command:

   ```bash
   claude mcp add --scope user oracle -- ~/src/oracle/.venv/bin/oracle-bridge --config ~/.oracle/alice-backend-bridge.yaml mcp
   claude mcp list       # "oracle" should be listed and connect
   ```

   Use `--scope project` (or commit the printed `.mcp.json`) if the whole team shares the repository and each developer keeps their own YAML at the same path. Restart Claude Code in the repository; `/mcp` should show 14 `oracle_*` tools.
5. **Paste the printed CLAUDE.md snippet** into the repository's `CLAUDE.md`. It tells the agent to register first, read context at task boundaries, report assumptions and interfaces explicitly, gate shared-contract changes, and honour PAUSE/RESUME/FINAL_DECISION. The snippet is reproduced at the end of this document.

## 3. Operator: enroll each developer

For each `.pub` file received (through a channel where you can confirm who sent it):

```bash
oracle enroll alice backend --pubkey /tmp/alice_ed25519.pub
oracle enroll bob web --pubkey /tmp/bob_ed25519.pub
```

`enroll` does two things atomically from the operator's point of view: it grants the principal membership of the project (`--role developer|component_owner|project_owner`, default developer) and appends exactly one `restrict,command="env ORACLE_DB=... ORACLE_PRINCIPAL=alice .../oracle-server serve-ssh" ssh-ed25519 ...` line to `~/.ssh/authorized_keys` (600). It refuses malformed keys, unknown projects and non-owner actors; running it twice with the same key reports `already_enrolled` and changes nothing. `--dry-run` prints the line instead of writing it; `--authorized-keys PATH` and `--executable PATH` override the defaults (operator's `~/.ssh/authorized_keys`, `oracle-server` next to the running Python). The output includes a bridge YAML template and next steps you can forward to the developer. The audit ledger records `participant_enrolled` without any key material.

If the Oracle server runs under a dedicated OS account (recommended), run `oracle enroll` as that account, or pass `--authorized-keys /home/oracle/.ssh/authorized_keys` and make sure `ORACLE_DB` and `--executable` are paths that account can read.

## 4. Try it: one shared contract, two opinions

Both developers open Claude Code in their repository (A in `backend`, B in `web`). The operator keeps the dashboard open.

On Laptop A, tell Claude Code:

> Register with Oracle using ~/.oracle/alice-backend-registration.json with interests ["User.id"], then report the assumption that User.id has type UUID, visible to dependency consumers (visibility DEPENDENCY_CONSUMERS, consumer_projects ["web"]).

Claude calls `oracle_register`, then `oracle_report_assumption` with `{"subject": "User.id", "predicate": "type", "value": "UUID", "visibility": "DEPENDENCY_CONSUMERS", "consumer_projects": ["web"]}`. The reply classification is `CONSISTENT` (first opinion) and the dashboard shows one RUNNING session and one fact.

On Laptop B, tell Claude Code:

> Register with Oracle using ~/.oracle/bob-web-registration.json with interests ["User.id"], then report the assumption that User.id has type integer, visible to dependency consumers.

Now two assumptions disagree with no authoritative decision, so Oracle classifies the second report `UNDERSPECIFIED`, opens a clarification question, and sends `PAUSE` to both sessions. In the dashboard the audit timeline / Replay reads, in order: `ASSUMPTION_UPDATE` (alice) → `ASSUMPTION_UPDATE` (bob, UNDERSPECIFIED) → `clarification_opened` → `delivery_queued` PAUSE ×2. If the worker is running, `semantic_clarification_generator` follows with a drafted question and options for the owner.

Each Claude Code session sees the pause on its next `oracle_checkpoint` or `oracle_get_messages` call. Following CLAUDE.md, it finishes the current atomic step, saves, and calls `oracle_ack_pause(question_id=...)`. The dashboard shows both sessions `PAUSED`.

The operator decides:

```bash
oracle questions backend --json          # find the question id q-...
oracle question q-...                    # positions from both sessions, the model's suggestion if any
oracle answer q-... --choose 1 --reason "Canonical identity is a UUID everywhere"
```

Oracle records the decision (`decision_recorded`), queues `FINAL_DECISION` for both sessions and, once each acknowledges, `RESUME`. On each laptop, ask Claude Code to check for messages: it calls `oracle_get_messages`, reads the `FINAL_DECISION` (`User.id type = "UUID"`), applies it, and acknowledges with `oracle_reply(decision_id=...)`. `oracle_request_resume` (or the next checkpoint) then returns `RUNNING`; the dashboard shows both sessions running again and the decision in the Decisions card. Laptop B's agent now knows to use UUIDs without ever seeing Laptop A's private evidence.

To see a genuine conflict rather than an underspecification, have A report with `oracle_report_decision` (or the operator record a decision with `oracle decide`) before B reports the contradicting value: the second report is classified `CONFLICTING`, `conflict_confirmed` appears, and the operator resolves it with `oracle propose ... --suggested` / `oracle decide ... --conflict c-...`.

## Troubleshooting

- **`FORBIDDEN` on register/report.** The principal is not enrolled or not a member of that project, or the YAML `principal`/`project_id` does not match what the operator enrolled. Operator: `oracle enroll NAME PROJECT --pubkey ...` (safe to repeat); check `grep ORACLE_PRINCIPAL=NAME ~/.ssh/authorized_keys`.
- **`UNAUTHENTICATED`.** The SSH login worked but without the forced command, so `ORACLE_PRINCIPAL` was not set: the key line in `authorized_keys` was edited or the developer used a different key. Re-enroll the right `.pub`.
- **Host key mismatch / `Host key verification failed`.** The bridge uses `StrictHostKeyChecking=yes` with its own `~/.oracle/known_hosts`. If the server was legitimately reinstalled, confirm the new fingerprint with the operator, delete the file, and rerun `install-client.sh` (it re-fetches and prints the fingerprint). Never blindly copy a new key.
- **`oracle-bridge ... status` shows `OFFLINE` / gates return `UNAVAILABLE`.** The laptop cannot reach the Oracle host. Reports are queued in the local outbox and retried with the same message id; `oracle-bridge --config ... flush` resends them. Gates are deliberately blocked while offline: the agent must not change shared contracts it cannot get authorized. Check `ssh -T -i ~/.oracle/keys/NAME_ed25519 -o UserKnownHostsFile=~/.oracle/known_hosts -o IdentitiesOnly=yes -p PORT USER@HOST` by hand; the server prints an `UNAUTHENTICATED`/`INVALID_REQUEST` JSON reply, which proves the tunnel works.
- **MCP tools not showing in Claude Code.** Run `claude mcp list` (the `oracle` entry must connect) and `claude mcp get oracle` to confirm the command path is the absolute venv `oracle-bridge` and the YAML path exists. Restart Claude Code after adding a server; project-scope `.mcp.json` servers need approval on first use. Run the exact command by hand (`.../oracle-bridge --config ... mcp`); it must stay silent on stdout and wait for stdin. Tool names are `oracle_*` with underscores — the dotted names from the specification are not valid MCP tool names.
- **`SSH transport failed` with pip/venv errors.** The developer's venv is missing the `mcp` package or is built with an old Python; rerun `install-client.sh` without `--skip-venv` (Python 3.12+ required).
- **Two sessions from the same laptop.** Use a different `agent_session_id` and `state_file` per session (copy the YAML/JSON with a `-2` suffix); the bridge stores one session per state file.

## CLAUDE.md snippet

```markdown
## ORACLE coordination (MCP server "oracle")
This repository is coordinated by ORACLE. The oracle_* tools talk to a bridge that only shares compact
project facts; never paste transcripts or secrets into them.
- At the start of a session call oracle_register with the values in ~/.oracle/NAME-PROJECT-registration.json
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
```

MCP offers tools; it cannot force a client to call them. An agent that skips checkpoints is unmonitored in practice, which is why the CLAUDE.md instructions matter as much as the installation.
