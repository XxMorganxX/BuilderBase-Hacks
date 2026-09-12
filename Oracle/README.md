# Oracle Inbox

Deliver Oracle context and assignments over a local network into existing Claude Code or Codex sessions.

```text
Oracle / OpenClaw --oracle_send_to_client--> HTTP delivery queue
                                                   |
                                          client polls (~2s)
                                                   |
                                         local JSON inbox
                                                   |
                                      Claude Code command hooks
```

OpenClaw is the **sender only**. There is no OpenClaw receiving adapter. The client makes outgoing requests; it does not open a listening port. No MCP, database, frontend, cloud service, or model dependency is included. Oracle reasoning, prompt capture, access decisions, model serving, and task completion tracking are outside this project.

## Quick verification

Use Node.js 24.6+ for this package. On Oracle's machine, also meet the installed OpenClaw version's runtime requirements; the current OpenClaw documentation requires Node 24.16+ or 26.1+. OpenClaw is not installed by this package.

Dependencies are pinned in the lockfile. The project-local `.npmrc` uses legacy peer resolution to avoid an npm 11.5 optional-peer resolver crash; the optional OpenClaw peer is supplied by its host. Vite 7 is pinned for the test runner to avoid the newer native bundler dependency.

```sh
npm ci
npm test
npm run demo
```

The demo uses real loopback HTTP, a real disk queue and two client inboxes. It invokes the OpenClaw sender registration through a fixture and supplies simulated Claude Code hook input. It does **not** call a model or imply a live host integration test. See [verification details](docs/verification.md).

## Run on two machines

### 1. Oracle machine: delivery service

Copy `examples/server.json` to `examples/server.local.json`. Set `host` to Oracle's LAN IP or `0.0.0.0`; the example defaults to loopback. Permit the configured port through the machine's firewall for the participating clients.

Generate three different tokens in the server's shell, without committing them:

```sh
export ORACLE_SENDER_TOKEN="$(openssl rand -hex 32)"
export ORACLE_AUDIO_TOKEN="$(openssl rand -hex 32)"
export ORACLE_CHIP_TOKEN="$(openssl rand -hex 32)"
node dist/cli.js serve examples/server.local.json
```

Provision the same sender token to the Oracle/OpenClaw sender, and only the matching client token to each client. Tokens must be at least 24 characters and distinct. Environment variables must be set in each process's actual launch environment; they are not automatically inherited across terminals or containers.

The server uses HTTP. Examples enable plaintext HTTP explicitly for a trusted LAN hackathon with synthetic data. Bearer tokens and content are not encrypted over HTTP. For private data, use an HTTPS reverse proxy or an encrypted tunnel; clients support HTTPS and reject redirects. Do not expose this demo service to the public internet.

### 2. Claude Code machine: receiver

Copy `examples/client.json` to `examples/client.local.json`. Set `oracleUrl` to the reachable Oracle origin, e.g. `http://192.168.1.20:8787`. Set the recipient to the server's exact registered name. Config paths are resolved relative to the config file, not the current working directory.

Set `ORACLE_AUDIO_TOKEN` in this shell to the value provisioned by the Oracle operator. Then:

```sh
node dist/cli.js poll-once examples/client.local.json
node dist/cli.js poll examples/client.local.json
```

Leave the poller running in a terminal. Ctrl-C stops it. It retries outages with bounded backoff and jitter, retains local messages, and advances its cursor only after saving an entire response. The Claude hook does no networking and needs no token.

Each recipient should have a separate inbox, state directory, token, and client configuration. For the demo, use one named recipient per active Claude Code session. Explicit `sessionId` targeting supports tighter routing. Messages without a session ID apply once to **each** session using that recipient, including newly created sessions; set `expiresAt` to prevent old messages from applying indefinitely.

### 3. Install the Claude Code hook configuration

Generate an absolute-path settings fragment:

```sh
node dist/cli.js settings examples/client.local.json > examples/claude-hooks.local.json
```

For a new session, load the file explicitly:

```sh
claude --settings /absolute/path/to/Oracle/examples/claude-hooks.local.json
```

Alternatively merge its `hooks` entries into the coding project's `.claude/settings.local.json`, preserving existing hooks. This repository never modifies global Claude settings. For an already-running session, use Claude's `/hooks` interface to load/inspect the configuration according to your installed version. Once installed, delivery keeps the same session and never restarts it. Avoid installing the fragment twice.

Hook commands use absolute paths to this checkout, Node, and the client config. Regenerate the fragment after moving the checkout or changing Node installations. Do not commit a machine-specific generated fragment.

### 4. Send a message

From a shell with the sender token, test the endpoint without OpenClaw:

```sh
ORACLE_ALLOW_HTTP=1 node dist/cli.js send http://192.168.1.20:8787 examples/context.json
ORACLE_ALLOW_HTTP=1 node dist/cli.js send http://192.168.1.20:8787 examples/task.json
```

Replace the IP with Oracle's address. Reusing an unchanged message ID is idempotent; to send a new message use a new ID. Reusing an ID with different content is rejected. The CLI's `queued` result means durable queue acceptance, not that Claude has received or acted on it.

For a network-free client smoke test:

```sh
node dist/cli.js write examples/client.local.json examples/context.json
```

This example writer validates the recipient and writes atomically to the local inbox. It does not submit to the Oracle queue.

## Delivery semantics

### Codex configuration

Use `examples/codex-client.json` for Codex. It reads the same audio inbox as the Claude example but keeps independent delivery receipts. Its Oracle URL and token are configured just like the Claude client. Run only one poller for a shared inbox; either host can then read it using its own configuration.

Generate a Codex hooks file:

```sh
node dist/cli.js codex-settings examples/codex-client.json > examples/codex-hooks.local.json
```

Merge its `hooks` entries into the coding project's `.codex/hooks.json`, preserving existing entries. The generated commands contain absolute paths, so regenerate after moving the checkout. Codex requires trusted project configuration and review of new hooks; open `/hooks` in the Codex CLI and trust the Oracle commands. Global Codex settings need no changes. Hooks must be enabled by host policy. [Official Codex hooks documentation](https://learn.chatgpt.com/docs/hooks)

For a local test, start `codex` in the configured project. In another terminal run `node dist/cli.js write examples/codex-client.json examples/context.json`, then ask Codex what shared audio interface version Oracle sent. Expect v2. A repeated ID is suppressed in the same session. Network delivery uses the existing poller with this client config; no OpenClaw change is needed.

The shared hook reader supports Codex `UserPromptSubmit`, `PostToolUse`, and `Stop` JSON formats. Normal tasks use Stop continuation and the same loop guard. Generated Codex context handlers disable output spilling because the reader already caps the output. Stop continuation feedback still uses Codex's own output limit. Local tool calls have PostToolUse coverage; hosted tools such as web search do not guarantee that boundary. Tests use real subprocesses with documented Codex event fixtures, not live model calls.

### Message timing

| Message | Boundary | Behavior |
| --- | --- | --- |
| Context | `UserPromptSubmit` or `PostToolUse` | Adds reference information through `additionalContext` |
| Urgent task | `UserPromptSubmit` or `PostToolUse` | Adds an assignment asking Claude to reprioritize, ahead of ordinary context in the delivery budget |
| Normal task | Natural `Stop` | Releases one task using `decision: block` and `reason`, asking Claude to continue in the same session |

**A natural Stop means the current response ended, not that an entire multi-turn project finished.** This is the hackathon delivery gate, not a task scheduler. If Claude stopped to ask a question, a queued task can be released at that point too. Tool execution is never preempted. Normal tasks are not released during `stop_hook_active` continuations, preventing an endless loop. Remaining normal tasks wait for another natural Stop. Nothing wakes an idle session.

Messages cannot override host permissions. Bodies are serialized as labeled content and never executed by the delivery code. Formatting is not a security boundary against a compromised Oracle or prompt injection: Oracle must choose authorized content before sending it. No agent-to-agent communication feature is provided.

The default aggregate context budget is 8,000 characters, including formatting. Oversized messages remain pending; increase the budget (maximum 9,000) or have Oracle send smaller messages with new IDs. JSON escaping can make rendered content longer than its Markdown body. Expired, malformed, temporary, and non-regular message files are skipped. Unknown hook inputs, missing inboxes, lock contention, and reader failures leave normal agent work running.

The schema permits a 6,000-character body; file reads are limited to 64 KiB. The demo queue/reader caps directory entries at 10,000. This is bounded hackathon storage, not a long-running message broker. Files are not automatically pruned. Remove expired inbox files when sessions no longer need them. Do not delete or renumber server queue entries while clients use their cursors; coordinated queue reset requires resetting poll cursors. Keep delivery receipts to avoid replay.

## OpenClaw sender

Build the checkout on Oracle's machine, install it using the installed OpenClaw release's local-plugin workflow, and merge `examples/openclaw-config.json` into its plugin settings. The package declares `openclaw.extensions`, and `openclaw.plugin.json` declares the `oracle_send_to_client` tool. The entry uses the documented `definePluginEntry` and `registerTool` interfaces.

Oracle calls `oracle_send_to_client` with a complete message from [the contract](docs/contract.md). It supplies a stable ID, target recipient, `kind`, and optional `priority: "urgent"`. The sender reads credentials from its configured environment variable. No prompt-reading hook or conversation access is required by this plugin.

OpenClaw plugin APIs are experimental. Pin and verify the actual Oracle installation before claiming compatibility. After installing and enabling the plugin, inspect it with the documented command:

```sh
openclaw plugins inspect oracle-inbox --runtime --json
```

Ensure Oracle's tool policy allows `oracle_send_to_client`. Restart the gateway through your existing deployment workflow after plugin changes. No gateway was modified here.

## NemoClaw / OpenShell placement

Run the queue service on Oracle's host, or another location reachable from both the OpenClaw sandbox and client laptops. A sandbox's `127.0.0.1` is not automatically the host. Configure a reachable origin and allow that exact destination/port in the deployment's network policy. Provision the sender token through the deployment's supported mechanism; do not assume a host environment variable crosses the sandbox boundary.

There is **no shared filesystem mount requirement between Oracle and Claude Code** in this network design. Queue files stay with the delivery service; inbox and receipt files stay on the client. Verify a real tool send from inside the deployed sandbox, then a client poll and hook delivery. OpenShell visibility/network policy and local model routing were not verified here because that stack is not installed.

## Durability and inspection

`state/sessions/<hash>/<message-id-hash>.json` records `emission-attempted`, the hook event, and timestamp. It stores no message body. Separate locks cover inbox writes, queue writes, polling, and each session's receipts. Lock leases recover after about 10 seconds following a process crash. Use a local filesystem, not an unverified network filesystem.

Receipts are persisted **before** writing hook output. A crash between those operations can leave a message marked attempted without delivering it. Persisting afterward would instead risk duplicate delivery. There is no host acknowledgement transaction, exactly-once guarantee, or proof of model consumption. For a known missed emission, after stopping hook activity, remove that one receipt to allow replay. Never infer task completion from it.

Atomic rename prevents readers seeing partial JSON during ordinary process operation; the writer does not fsync files/directories, so sudden power loss has weaker durability guarantees. The network cursor commits after storage; if a poller crashes before cursor commit it safely replays unchanged files. The server persists queue sequence numbers in immutable records across restart. Never reuse a message ID for a different logical message.

## Source references

- [Claude Code command-hook schemas](https://code.claude.com/docs/en/hooks)
- [OpenClaw plugin entry and registration](https://docs.openclaw.ai/plugins/building-plugins)
- [NemoClaw / OpenShell architecture](https://docs.nvidia.com/nemoclaw/user-guide/openclaw/about/ecosystem)

Implementation is in `src/`; examples are intentionally small. [Oracle integration contract](docs/contract.md) and [verification record](docs/verification.md) describe the handoff and remaining deployment checks.
