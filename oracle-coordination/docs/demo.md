# Run the demonstrations

## Fast local-transport demo

Run on the SSH machine:

```bash
cd /home/dell/Documents/dev/oracle
.venv/bin/oracle demo --output-dir runtime/demo-new
```

Use a new directory each time. The demo creates three actual Git repositories and three bridge databases, then exercises:

1. UUID/integer/email identity assumptions.
2. A durable missing-specification question and scripted human UUID answer.
3. An authentication mismatch affecting backend and web while analytics continues.
4. Safe-pause acknowledgements, proposal, bounded responses, and human security decision.
5. A complete login response with access_token, expires_at, and user_id.
6. A separate low-risk naming normalization resolved by permitted Oracle arbitration.
7. Scoped decision propagation, acknowledgements, and all sessions resuming.

Artifacts: oracle.db, transcript.json, result.json, and one context JSON file per participant. The context files show the minimum information each participant receives.

## Live model demo

```bash
.venv/bin/oracle live-demo --output-dir runtime/live-new
```

Same three repositories, with the reasoning worker draining jobs between steps on the local Qwen model (default: the NemoClaw vLLM route; `--provider ollama` for qwen3:30b). Adds to the deterministic flow:

1. A checkpoint whose agent interpretation of "Make login work" is extracted into PROPOSED, owner-only facts (UUID, JWT bearer, 15-minute expiry, UTC ISO-8601).
2. A model-drafted clarification with quick-select options and a recommendation, which the scripted owner accepts.
3. A semantic assessment and a model proposal for the authentication conflict, marked `requires_human`; the owner adopts it with `propose --suggested`, agents accept, and the owner decides.
4. A low-risk encoding normalization where the model opens the bounded round itself and Oracle arbitrates after unanimous acceptance.

Artifacts add `steps.json` (what the owner would have seen) and `model-runs.json` (every validated model output with timing). Last measured run: 9 model calls, 0 failures, 33 s wall clock.

## Watch it live

```bash
.venv/bin/oracle --db runtime/live-watch/oracle.db dashboard --host 0.0.0.0 --port 8765   # terminal 1
.venv/bin/oracle live-demo --narrate --pace 8 --output-dir runtime/live-watch              # terminal 2
```

Open `http://<oracle-host>:8765/` in a browser. The dashboard is a read-only, standard-library web view over the SQLite file: sessions and their pause state, open questions with the model's drafted options, conflicts with pause acknowledgements, rounds and proposals, decisions with acknowledgement counts, the local model's job queue, the living contract list, and the audit timeline. It never creates or writes the database, so it can be started before the demo. `--pace` inserts a delay between steps so viewers can follow; `--narrate` prints the same story on stderr.

Bind to `127.0.0.1` (the default) and tunnel with `ssh -L 8765:127.0.0.1:8765 host` when the host is not on a trusted network; the view exposes project state to anyone who can reach the port.

## Watch it back

```bash
.venv/bin/oracle --db runtime/live-watch/oracle.db dashboard --host 0.0.0.0 --port 8765   # terminal 1
.venv/bin/oracle live-demo --pace 8 --output-dir runtime/live-watch                        # terminal 2
```

Open `http://<oracle-host>:8765/` and switch to the **Replay** tab. It is served from `/api/timeline`, a read-only replay of the audit ledger in seq order (causes before effects). Tick **Follow live** to jump to the newest step as the demo progresses, or untick it and scrub with the slider, the step buttons, or the arrow keys. Each step shows who acted and who received it (swimlanes for alice/bob/carol, the owner, Oracle, and the model), a plain-language title, the classification or instruction text, the raw payload, and the world state after that step: every session's `work_state`, open questions and conflicts, and the decision and model-run counters. Model steps show the exact evidence bundle sent to the model next to its validated output and timing. Heartbeats are hidden by default.

The same replay is available in the terminal, after or during a run:

```bash
.venv/bin/oracle --db runtime/live-watch/oracle.db timeline               # one line per step, details indented
.venv/bin/oracle --db runtime/live-watch/oracle.db timeline --heartbeats  # include HEARTBEAT rows
.venv/bin/oracle --db runtime/live-watch/oracle.db timeline --json        # the structure the Replay tab consumes
```

Both readers open the database read-only and never create it, so they can be pointed at a database that does not exist yet (`status: waiting`).

## Real SSH transport demo

```bash
.venv/bin/python scripts/ssh_demo.py
```

This host-operator script generates three dedicated demo keys and appends three restricted forced-command entries to the current account's authorized_keys. Each key can only access the demo database as one principal. It preserves existing keys. Private keys stay in the mode-700 runtime directory.

The demo makes actual OpenSSH calls through the production SSHTransport class. It simulates three developer machines on one physical host; it does not count as a test with three real people or three physical computers.

Demo credentials are intended for development. To retire a run, remove only its three oracle-demo public-key entries from authorized_keys and remove its private runtime artifacts when no longer needed. Do not remove unrelated login keys.

## Model validation

`oracle demo` is deterministic and reports semantic_model_used=false; `oracle live-demo` reports true. `scripts/model_smoke.py [--provider ...]` checks two structured cases per route. Model serving quality, NemoClaw sandbox routing, and actual coding-client pause behavior remain separate validation dimensions; see [milestones](milestones.md).

## Test suite

```bash
.venv/bin/pytest -q
.venv/bin/ruff check src tests scripts
```

Tests cover authority, disclosure, participant enrolment, the audit replay timeline, registration identity, transaction rollback, stale checkpoints, idempotency, offline replay, bounded mediation, multiple blockers, restart recovery, gates, the MCP tool interface, conflict typing, participant-specific propagation, same-file advisories, curation, and every reasoning skill with fixture providers (fabricated evidence is rejected; suggestions never gain authority).

