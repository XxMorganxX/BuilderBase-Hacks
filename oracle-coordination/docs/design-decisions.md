# Initial design decisions

## SQLite and one transaction boundary

SQLite represents graph concepts as indexed relational records. Events, state changes, and downstream commands are committed together. This avoids a broker/database dual-write gap in the MVP. Model calls run outside the transaction, using leased durable jobs.

The schema groups some conceptual tables from the spec: assumptions and requirements are typed facts; repositories, machines, and agents are attributes of registered sessions; schemas are interface facts. These are explicit MVP representations, not separate unimplemented empty tables.

## Authority is enforced by code

Agent reports cannot assert human_decision or project_spec. Human/admin operations are absent from the participant RPC and MCP tools. SSH forced commands bind a principal. The local administrative CLI is trusted host-operator tooling; it is not a remote multi-user API.

Oracle arbitration is disabled by default. Enabling it requires an explicit low-risk subject allowlist, participant acceptance, compatible higher-authority evidence, and sufficient disclosure rights. Authentication decisions remain human decisions in the demo.

## Visibility is applied before retrieval and propagation

Contract sharing is explicit. A dependency relationship and a matching visibility scope are both required for dependency-consumer disclosure. Cross-project context omits private paths and author identities. Raw negotiation prose is retained internally, not forwarded to peers.

Model suggestions do not automatically gain broader visibility than their evidence. Private evidence can lead to a generic coordination notice, but publication of the needed contract requires an authorized human.

## Model skills produce suggestions, code produces state

Four skills run as leased jobs: knowledge-extractor (checkpoint → PROPOSED owner-only facts), conflict-detector (differing values → assessment annotation, or a semantic-review question when no issue is open), clarification-generator (new question → drafted text, options, recommendation), and mediation-agent (fully paused conflict → proposal with per-participant changes). Each output must validate against its Pydantic schema and cite only fact IDs from its bundle; otherwise the job retries once and fails.

A suggestion becomes an action only through an authorized path: an owner runs `oracle answer` or `propose --suggested`, or the project rules allowlist the subject as low-risk with `oracle_arbitration` enabled and the model itself reports `requires_human: false`, in which case the bounded round opens under the `oracle-model` actor and existing disclosure checks. Reviewers see a suggestion only when every cited fact is visible to them. The model never sees developer prompts beyond the compact fields a session chose to report.

## Conservative context budgets

Packets are limited to 128 KB, ordinary context to 6 KB, model bundles to 24 KB, and negotiation responses to a conservative 700 UTF-8 bytes. These are practical bounded defaults, not tokenizer-based efficiency claims. The negotiation byte bound is deliberately tighter than the spec's suggested 700 tokens until a model-specific tokenizer is introduced.

## Cooperative pause

The bridge requests a safe pause. It does not freeze arbitrary coding-agent processes. Acknowledgement means the client explicitly reported reaching its safe checkpoint. No automatic acknowledgement is inferred from merely receiving a network message.

A final decision is recorded before delivery. All affected sessions must acknowledge it, and every other open blocker must be resolved, before resume. Disconnected or noncompliant participants require operator attention; there is no silent timeout that bypasses acknowledgement.

## Gates bind exact changes

Gate authorization covers the exact action, target, proposed change, and revision, with a one-hour validity window. It is not a broad permission for future changes. Gates fail closed when Oracle is offline. Enforcement still depends on the client invoking the gate before the action.

## Recoverability and operation scope

Message IDs are idempotency keys; reusing an ID with changed content is rejected. A bridge persists outgoing messages and incoming cursors. Semantic jobs retain attempts, leases, results, and timing. The current schema has version 1; breaking schema upgrades need explicit migrations before release.

The first delivery is a development MVP, not a proof of arbitrary-client enforcement, enterprise identity isolation, or semantic detection accuracy. Those claims need separate evidence.

## External references

The implementation uses the [official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk/tree/v1.x). NemoClaw's role and managed local inference are described in [NVIDIA's local inference documentation](https://docs.nvidia.com/nemoclaw/latest/inference/use-local-inference.html). The prior communication-efficiency research is retained separately and does not substitute for project-specific evaluation.

