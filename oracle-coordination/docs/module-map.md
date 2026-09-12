# Module map and work boundaries

All core code is under `src/oracle/`. This follows Python's src layout while preserving the conceptual boundaries in specification sections 64–73. CLI names are installed through pyproject.toml.

| Module | Owns | Depends on | Independent work boundary |
| --- | --- | --- | --- |
| protocol.py | Pydantic wire/domain contracts, enums, authority ranking | Pydantic | Versioned event/schema changes and validation |
| store.py | SQLite schema, transactions, audit ledger, deliveries | protocol | Migration, indexing, retention, backup/recovery |
| policy.py | Membership, owner authority, fact visibility, explicit sharing | store | Visibility/authorization behavior with denial tests |
| service.py | Ingest, registration, checkpoints, graph updates, deterministic detection, context | protocol/store/policy/workflow | Knowledge ingestion and dependency filtering |
| workflow.py | Questions, mediation rounds, arbitration guards, decisions, acknowledgements, resume, gates | protocol/store/policy | Coordination state-machine changes |
| transport.py | Abstract transport, OpenSSH transport, test-local transport | asyncio, SSH CLI | Add another transport against the same RPC contract |
| rpc.py | Participant operation allowlist | service | Wire operation compatibility; no human-admin methods |
| bridge.py | Local outbox, local inbox, offline behavior, checkpoint control | protocol/transport/observer | Client recovery and event scheduling |
| observer.py | Git metadata and local context packet compilation | git CLI/protocol | Additional deterministic observers and disclosure filters |
| mcp_server.py | Fourteen agent-facing tools | official MCP SDK/bridge | MCP client integration without changing coordination logic |
| config.py | Validated bridge config, path resolution, provider construction | YAML/protocol | Portable installation/configuration |
| model.py | Provider abstraction, OpenAI-compatible (vLLM), Ollama, NemoClaw route, grammar-safe schemas | httpx/Pydantic | Model serving, structured-output compatibility, measurements |
| reasoning.py | Skill registry (extractor, conflict detector, clarification generator, mediation agent), evidence bundles, leased jobs, validation | model/store | Semantic quality, extraction, minimization, evaluation |
| cli.py | Human/admin CLI (incl. `enroll`, `timeline`, `dashboard`), server entrypoint, bridge entrypoint | service/config | Human workflows and operating commands |
| demo.py | Repeatable deterministic three-project scenario | public bridge/service APIs | Acceptance scenarios and integration evidence |
| live.py | Same scenario with the reasoning worker and local model in the loop, narration, pacing | demo/reasoning/model | Live model evidence and prompt regression |
| dashboard.py | Read-only HTTP dashboard over the SQLite file (section 75): Live tab, Replay tab, `/api/state`, `/api/timeline` | stdlib http.server/sqlite3/timeline | Operator visualization; no write path |
| timeline.py | Audit-ledger replay: lane-oriented steps with titles, details, payloads, and cumulative world state; text renderer (`oracle timeline`) | stdlib sqlite3/store.dump | Replay presentation; read-only, never changes coordination state |
| enroll.py | Participant enrolment: public-key parsing, restricted `authorized_keys` line, bridge YAML, audit (`oracle enroll`) | policy/transport/service.grant | Onboarding and key management; owner authority enforced |
| skills/*/SKILL.md | Bounded reasoning instructions | protocol/domain contracts | Prompt changes evaluated independently of authority enforcement |

## Interfaces to preserve

Agent input: EventEnvelope → typed payload. Server input: authenticated principal + RPC request. The principal is supplied by a trusted SSH forced command, never trusted from the envelope.

Model input: task + bounded evidence bundle + Pydantic output schema. Model output is validated before it can create a review question. It cannot write directly to SQLite or issue a final high-impact decision.

Transport input: JSON RPC request. Transport output: validated success/error response. No transport-specific code belongs in the knowledge or workflow modules.

Persistence: accepted event, resulting state, and queued commands commit atomically. No model/network wait occurs inside that transaction.

## How to divide work

One person can own protocol/transport compatibility; another can own semantic reasoning and evaluations; another can own bridge/client integration; another can own human CLI and docs. Changes to protocol enums, authority rules, or workflow states should include scenario tests and a brief design note.

Run the relevant tests while editing, then the full small suite before handing off. Avoid splitting one authoritative state transition between independently committing services.

