# Install the Oracle server

## Requirements

A trusted Linux host with Python 3.12 or newer, Git, an SSH server, and disk space for the database. Local AI reasoning additionally needs a model service and appropriate memory; the deterministic demo does not.

On the current development machine, the source already lives at:

```bash
cd /home/dell/Documents/dev/oracle
```

On a new host, copy this repository to your chosen directory, then run:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/pytest -q
```

Once a dependency snapshot is present, use `pip install -r requirements-lock.txt` followed by `pip install --no-deps -e .` to reproduce the tested versions. The normal install resolves compatible ranges in pyproject.toml.

## Create isolated state and the first project

Run these as the trusted Oracle host operator:

```bash
mkdir -p runtime
chmod 700 runtime
export ORACLE_DB="$PWD/runtime/oracle.db"
export ORACLE_PRINCIPAL="owner"
.venv/bin/oracle init oracle --owner owner
.venv/bin/oracle import-spec oracle oracle.yaml
.venv/bin/oracle status oracle
```

Use a new project ID on subsequent runs; init intentionally does not overwrite an existing project. SQLite creates its schema automatically. Facts imported from YAML retain a hash of the source specification in the decision rationale.

The machine-readable requirements are executable inputs. ORACLE.md is human context; the MVP does not pretend to turn arbitrary Markdown into approved structured requirements automatically.

## Add related projects and users

```bash
.venv/bin/oracle init backend --owner owner
.venv/bin/oracle init web --owner owner
.venv/bin/oracle grant backend alice
.venv/bin/oracle grant web bob
.venv/bin/oracle share backend web AuthenticationResponse
.venv/bin/oracle share backend web User.id
```

Sharing authorizes a named contract relationship. A fact still needs a matching visibility scope. Mere equality of subject names does not grant access to another project.

## Bind each participant to an SSH key

Each developer generates an Ed25519 key on their own machine and sends only its public key to the trusted host operator. The operator installs it with one command, which grants project membership (owner authority required) and appends exactly one restricted entry to `~/.ssh/authorized_keys` (mode 600, idempotent on the key material, audited without the key itself):

```bash
.venv/bin/oracle enroll alice backend --pubkey alice_ed25519.pub            # --role, --authorized-keys, --dry-run
```

The line it writes has this shape (a manual entry is equivalent). Replace paths and the public key:

```text
restrict,command="env ORACLE_DB=/absolute/path/oracle/runtime/oracle.db ORACLE_PRINCIPAL=alice /absolute/path/oracle/.venv/bin/oracle-server serve-ssh" ssh-ed25519 AAAA... alice-oracle
```

The forced command ignores the participant's requested shell command and exposes only the RPC allowlist. Keep normal administrator login keys separate. For a production installation use a dedicated Oracle OS account; sharing the host operator's unrestricted SSH access would bypass application isolation.

Verify the server host key out of band and install it in the client's known_hosts. The bridge requires strict host verification and never automatically accepts an unknown key.

## Local inference

The Oracle host runs a NemoClaw-managed vLLM container serving a Qwen model on the loopback OpenAI-compatible route. The worker uses it by default and discovers the served model name automatically:

```bash
curl -s http://127.0.0.1:8000/v1/models        # expect nvidia/Qwen3.6-35B-A3B-NVFP4
.venv/bin/python scripts/model_smoke.py         # 2 structured cases; writes runtime/model-smoke.json
.venv/bin/oracle worker --once                  # process one queued reasoning job
.venv/bin/oracle worker                         # run continuously next to the server
```

The worker drains `reasoning_jobs` created by checkpoints, differing reports, new questions, and fully paused conflicts. Heartbeats never create jobs. Every model output is validated against a Pydantic schema, must cite only fact IDs from its evidence bundle, and is stored as a suggestion or PROPOSED fact; it cannot record a decision, publish a contract, or pause a session.

Alternatives:

```bash
.venv/bin/oracle worker --provider ollama --model qwen3:30b       # native Ollama structured output
.venv/bin/oracle worker --provider compatible --endpoint http://127.0.0.1:8000/v1 --model MODEL_ID
.venv/bin/oracle worker --provider nemoclaw --sandbox NAME        # managed inference.local via sandbox exec (unvalidated)
```

Ollama unloads the model after each request (`keep_alive: 0`); the first request after idle pays the load time. Do not repoint an unrelated NemoClaw sandbox at ORACLE.

## Human operation

```bash
.venv/bin/oracle status backend                 # text dashboard; add --json for scripts
.venv/bin/oracle questions backend --json
.venv/bin/oracle question q-...                 # positions, related contracts, model recommendation
.venv/bin/oracle answer q-...                   # interactive numbered prompt (section 74)
.venv/bin/oracle answer q-... --choose 1 --reason "Canonical identity"
.venv/bin/oracle conflicts backend --json       # includes type, round, suggested_proposal, assessment
.venv/bin/oracle propose c-... --suggested      # adopt the validated model proposal
.venv/bin/oracle propose c-... '"JWT bearer"' --reason "Owner proposal"
.venv/bin/oracle decide backend AuthenticationResponse authentication '"JWT bearer"' --conflict c-... --reason "..."
.venv/bin/oracle correct backend User.id type '"UUID"' --reason "Project owner confirms canonical identity"
.venv/bin/oracle curate backend                 # retire closed-session assumptions, report duplicates and stalls
```

Model suggestions appear only when every fact they cite is visible to the reviewing owner. Affected participants must acknowledge pause before a conflict can be proposed on or decided.

## Smoke test and troubleshooting

```bash
.venv/bin/oracle demo --output-dir runtime/install-smoke
```

Expect status PASS and three RUNNING sessions. Choose a fresh output directory for another run.

- `UNAUTHENTICATED`: the forced command did not bind ORACLE_PRINCIPAL.
- `FORBIDDEN`: check project membership, key-to-principal binding, and session ownership.
- `IDEMPOTENCY_CONFLICT`: a client reused an ID for different content.
- `INVALID_STATE` during mediation: inspect outstanding pause acknowledgements.
- A worker failure leaves deterministic coordination working; inspect the reasoning_jobs result and audit rows.
- Back up SQLite with its backup API or stop writers before copying the database; copying only the main file during WAL writes is insufficient.

