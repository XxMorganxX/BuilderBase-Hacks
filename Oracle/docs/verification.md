# Verification record

## Environment inspected

- Empty Git checkout; no existing application or repository instructions found.
- macOS ARM64, Node.js 24.6.0, npm 11.5.1.
- Claude Code 2.0.20 installed. Its installed JavaScript declares `additionalContext` for both `UserPromptSubmit` and `PostToolUse`, and emits `stop_hook_active` for Stop. CLI help includes `--settings`.
- Codex CLI 0.137.0 installed; `codex features list` reports hooks enabled. The Codex config generator is tested against documented event fixtures through generated shell commands, including paths with spaces and apostrophes. Live model consumption is not claimed.
- OpenClaw, NemoClaw, and OpenShell are not installed in this workspace environment.

## Automated scope

`npm test` builds the TypeScript source and runs Vitest. Tests exercise real files, cross-process command-hook invocation, and loopback HTTP. OpenClaw registration and Claude event inputs are fixtures. No cloud AI API is called.

Covered: missing inboxes; malformed, temporary and symlink files; recipient/session isolation; per-session replay semantics; expiration; total context budgets and deferred messages; immutable IDs; concurrent writers; overlapping hooks in one process and across processes; normal and urgent task boundaries; continuation-loop guard; subagent filtering; hook stdout JSON and fail-open behavior; sender/read credential separation; read impersonation rejection; HTTP opt-in; queue paging; cursor replay; outages; malformed response routing; and the OpenClaw sender function through a registration fixture.

`npm run demo` exercises sender → real HTTP queue → poller → local files → simulated hook boundaries for audio and chip clients. It asserts that the audio task waits for Stop and the chip client receives nothing.

Observed results: clean `npm ci` succeeded, all 28 tests passed (including three Codex config cases), the demo passed, and the dependency audit reported zero vulnerabilities. The suite includes urgent-message ordering under a tight budget. Ordinary sandbox execution denies local port binding, so network tests need an environment that permits a temporary loopback listener.

## Not claimed

- Live Claude Code model consumption or successful execution of assigned tasks. Source schema inspection is not an end-to-end host test.
- A loaded OpenClaw plugin on a pinned gateway version. `openclaw-entry.mjs` imports the SDK from the host; the local tests exercise `registerSender`, not the host loader.
- Linux execution, Dell GB10 hardware, local model inference, or connectivity through the actual OpenShell sandbox.
- Real cross-machine LAN routing, firewall, or TLS configuration.
- Exactly-once delivery, indefinite queue retention, semantic task completion, or enforcement of enterprise disclosure policy.

Before the live demo, run the acceptance sequence in `docs/contract.md` on the actual machines. Verify the installed OpenClaw runtime requirements and sender tool loading, destination network policy, and credential availability inside its execution environment. No invented mount or OpenShell CLI commands are needed: this integration is network-based.
