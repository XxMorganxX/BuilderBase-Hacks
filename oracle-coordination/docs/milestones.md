# Milestones and validation boundaries

Status on 2026-09-12, measured on the development host (`/home/dell/Documents/dev/oracle`, NVIDIA GB10, Python 3.12). "Evidence" names the test, command, or artifact that demonstrates the milestone; anything not listed here should be treated as unverified.

Test suite: `.venv/bin/pytest -q` → 38 passed. Lint: `.venv/bin/ruff check src tests scripts` → clean.

## Specification milestones (section 89)

| # | Milestone | State | Evidence |
| --- | --- | --- | --- |
| 1 | Protocol: Envelope, Session, ContextPacket, KnowledgeFact, Question, Conflict, Decision, OracleCommand | Done | `src/oracle/protocol.py`; `test_protocol_rejects_unknown_versions_and_naive_times` |
| 2 | Bridge: `oracle-bridge start/status/checkpoint` | Done | `src/oracle/bridge.py`, `cli.py:bridge_main`; `test_offline_queue_restart_and_lost_ack` |
| 3 | Transport: SSHTransport, `oracle-server ingest/poll/serve-ssh` | Done | `src/oracle/transport.py`; `scripts/ssh_demo.py` run → `runtime/ssh-demo-*/result.json` PASS over real OpenSSH with three restricted forced-command keys |
| 4 | Sessions: multiple registrations, status display | Done | `test_principal_session_and_project_binding`; `oracle status PROJECT` text dashboard |
| 5 | Knowledge base: facts, assumptions, decisions, entities, relationships, questions | Done | `src/oracle/store.py` schema v1; `test_checkpoint_atomicity_and_staleness`, `test_duplicate_delivery_exactly_one_state_effect` |
| 6 | Knowledge extraction: deterministic, then model | Done | Deterministic: `service._checkpoint`. Model: `KnowledgeExtractor` skill; live demo extracted 6 PROPOSED facts (UUID, JWT bearer, 15-minute expiry, UTC ISO-8601) from "Make login work" |
| 7 | Relationship graph: dependencies, interfaces, consumer/provider | Done | `USES/DEPENDS_ON/PRODUCES/CONSUMES/IMPLEMENTS/TOUCHES` relationships; `test_same_file_overlap_is_advisory_and_project_scoped` |
| 8 | Underspecification detected, human asked | Done | `test_three_projects_clarification_and_selective_pause`; model-drafted question with options via `ClarificationGenerator`; `oracle question`/`oracle answer` |
| 9 | Conflict detection | Done | Deterministic typing in `service._conflict_type`; semantic `ConflictDetector` annotates open issues; `test_conflict_is_typed_deterministically`, `test_authority_is_not_confidence` |
| 10 | Pause/resume of affected sessions only | Done | `test_three_projects_clarification_and_selective_pause`, `test_multiple_blockers_prevent_early_resume`, `test_late_pause_ack_cannot_repause_resolved_session` |
| 11 | Mediation: proposal, response, bounded negotiation, decision | Done | `test_mediation_bounded_rounds_and_human_escalation`, `test_low_risk_oracle_arbitration_and_security_escalation`, `test_mediation_suggestion_auto_proposes_only_for_low_risk` |
| 12 | Knowledge propagation: participant-specific context | Done | `workflow.propagation_instruction`; `test_propagation_is_participant_specific`, `test_visibility_and_non_leaking_propagation` |
| 13 | MCP integration | Done | 14 tools in `mcp_server.py` named `oracle_register` … `oracle_request_resume` (underscores: the MCP tool-name grammar `^[a-zA-Z0-9_-]{1,128}$` forbids the dotted names of section 17; `TOOL_NAMES` maps them one-to-one); `test_mcp_exposes_all_tools_and_registers`, `test_mcp_stdio_process_registration` (real stdio subprocess through the official SDK) |
| 14 | End-to-end demo | Done | `oracle demo` (deterministic, PASS) and `oracle live-demo` (Qwen in the loop, PASS: 9 model runs, 0 failures, 33 s); `oracle dashboard` renders it live (section 75) |
| 15 | Replay of what happened | Done | `src/oracle/timeline.py` replays the audit ledger (seq order, causes before effects) into lane steps with cumulative world state; `oracle timeline`, dashboard Replay tab over `/api/timeline`; `tests/test_timeline.py` (`test_missing_database_is_waiting`, `test_demo_timeline`: question opened before PAUSE, pause/ack/decision/resume state transitions, 4 decisions incl. `oracle_arbitration`) |
| 16 | Participant enrolment | Done | `src/oracle/enroll.py` + `oracle enroll HUMAN PROJECT --pubkey`: owner-only grant plus one `restrict,command=` forced-command line, mode 600, idempotent, audit row without key material; `tests/test_enroll.py` (4 tests: enrol/append/deny FORBIDDEN-INVALID_KEY-NOT_FOUND/dry-run); `scripts/install-client.sh` + `docs/claude-code.md` for the two-laptop setup; e2e over real SSH on the dev host (`register` → ONLINE) |

## Acceptance criteria (section 90)

| # | Criterion | Evidence |
| --- | --- | --- |
| 1 | Multiple coding agents on different machines | Simulated: three bridges, three principals, three SSH keys on one host (`scripts/ssh_demo.py`). `scripts/install-client.sh` + `oracle enroll` are the documented path for real laptops (`docs/claude-code.md`); not yet run across three physical machines. |
| 2 | Multiple related repositories | Three Git repositories per demo run |
| 3 | Agent-independent registration | `Registration` carries `agent_type`; MCP and CLI paths share one bridge |
| 4 | Efficient periodic context transmission | Packets ≤128 KB, Git metadata only, heartbeats create no model work (`test_heartbeat_never_creates_model_work`) |
| 5 | Persistent evolving OKB | SQLite with lifecycle, provenance, supersession; `test_restart_restores_open_negotiation_and_replay` |
| 6 | Extraction of assumptions | Explicit reports plus model extraction into PROPOSED/OWNER_ONLY facts (`test_model_extraction_cannot_promote_authority_or_disclosure`) |
| 7 | Extraction of interfaces | `INTERFACE_UPDATE` → contract facts and PRODUCES/CONSUMES relationships |
| 8 | Project relationship tracking | `relationships` table; explicit `oracle share` for cross-project contracts |
| 9 | Detection of underspecification | Deterministic in `_analyze`; semantic annotation by the model |
| 10 | Human clarification request | Durable question, model-drafted options and recommendation, `oracle answer` prompt |
| 11 | Human answer persisted as specification | `oracle decide/answer` → `human_decision` fact with confidence 1.0, supersedes assumptions |
| 12 | Semantic conflict detection | `ConflictDetector` skill on the Qwen vLLM: smoke test 2/2 (`runtime/model-smoke-vllm.json`), Ollama qwen3:30b 2/2 (`runtime/model-smoke-ollama.json`) |
| 13 | Selective pause | Only affected sessions receive PAUSE; analytics continues during the authentication conflict |
| 14 | Oracle proposal | `MediationAgent` skill → `suggested_proposal`; auto-opens a round only for owner-allowlisted low-risk subjects |
| 15 | Agent responses | `MEDIATION_RESPONSE` with the six positions, one per participant per round, ≤700 bytes |
| 16 | Bounded agent negotiation | `negotiation_rounds` enforced (`NEGOTIATION_LIMIT`); peers receive only dispositions |
| 17 | Oracle or human final resolution | `oracle_arbitration` for allowlisted subjects with unanimous acceptance; otherwise `HUMAN_ESCALATION` |
| 18 | Decision persistence | `decisions` table plus authoritative fact; audit ledger |
| 19 | Minimum-context propagation | Per-session instruction names only the recipient's own prior position; context bounded to 6 KB and filtered by visibility |
| 20 | Coding resumption | RESUME after all acknowledgements and no other open blocker |
| 21 | No continuous full-transcript sharing | Bridge sends Git metadata and explicit compact reports; transcripts stay local |

## Live model evidence

| Route | Model | Result |
| --- | --- | --- |
| `--provider compatible` (default) — NemoClaw-managed vLLM, `http://127.0.0.1:8000/v1` | `nvidia/Qwen3.6-35B-A3B-NVFP4` | Smoke 2/2, ≈3 s per structured assessment; live demo PASS |
| `--provider ollama` — `http://127.0.0.1:11434` | `qwen3:30b` | Smoke 2/2, ≈10 s per assessment after load |
| `--provider nemoclaw` — sandbox `exec` to `inference.local` | (sandbox default) | Not validated; the only sandbox on the host is the operator's own assistant and was not repointed |

Structured output is enforced by JSON-schema constrained decoding on both routes. Ollama's grammar compiler rejects `$ref` and string-length keywords, so `model.inline_schema` resolves references and drops `maxLength`/`minLength`; Pydantic still validates the parsed result against the full model.

## Boundaries that remain

- Cooperative pause only. Stock coding clients are paused by instruction through MCP; there is no process-level interruption.
- Three physical machines and three real humans have not been exercised together; the SSH demo simulates them on one host.
- Semantic quality is demonstrated on a handful of cases, not measured statistically. False-positive and false-negative rates for the conflict detector are unknown.
- Schema version 1; no migration tooling yet.
- `ORACLE.md` is human context only; `oracle.yaml` requirements are the machine-readable spec input.
