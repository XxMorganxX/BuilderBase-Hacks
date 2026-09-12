# ORACLE

Local coordination and arbitration for people using independent coding agents across related repositories.

**Development host:** `/home/dell/Documents/dev/oracle` on the SSH machine. Python 3.12, SQLite, SSH, MCP, and pluggable local model providers. Live reasoning runs on the host's NemoClaw-managed vLLM (`nvidia/Qwen3.6-35B-A3B-NVFP4`) or Ollama (`qwen3:30b`).

Start here:

- [Install the Oracle server](docs/install-server.md)
- [Install and connect a developer bridge](docs/connect-client.md)
- [Two laptops, one Oracle: Claude Code quickstart](docs/claude-code.md)
- [Run the three-repository demo](docs/demo.md)
- [Understand and divide the code](docs/module-map.md)
- [Milestones and validation boundaries](docs/milestones.md)
- [Authority, privacy, and runtime decisions](docs/design-decisions.md)

The functional loop is: register → report assumptions/interfaces → detect missing or conflicting contracts → pause affected sessions → clarify or mediate → record a decision → send scoped instructions → acknowledge → resume.

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/pytest -q
.venv/bin/oracle demo --output-dir runtime/my-first-demo          # deterministic, no model
.venv/bin/oracle live-demo --output-dir runtime/my-first-live     # local Qwen reasoning in the loop
.venv/bin/oracle worker                                           # drain reasoning jobs for a real deployment
.venv/bin/oracle --db runtime/live-watch/oracle.db dashboard      # read-only live web view (section 75)
.venv/bin/oracle --db runtime/live-watch/oracle.db timeline       # replay the audit ledger as a terminal transcript
```

The dashboard has two tabs. **Live** shows the current state (sessions, questions, conflicts, decisions, model jobs). **Replay** scrubs through the audit ledger step by step in swimlanes (humans, owner, Oracle, model) with the derived world state after every step and, for model steps, the exact evidence bundle and validated output; "Follow live" keeps it on the newest step while a demo runs. `oracle timeline [--heartbeats] [--json]` prints the same replay in the terminal. Participants are added with `oracle enroll HUMAN PROJECT --pubkey KEY.pub`, which grants membership and installs the restricted SSH forced command in one step; `scripts/install-client.sh` prepares a developer laptop.

The deterministic demo needs no model weights: it exercises real persistence, bridge calls, and the coordination state machine with simulated agents and scripted human answers. The live demo adds the reasoning worker: the local model extracts tentative specifications from a checkpoint, drafts the human clarification with options, assesses conflicts semantically, and proposes resolutions — always as validated, evidence-bound suggestions that application code checks before an authorized human or an owner-allowlisted arbitration acts on them.

This is a development MVP. Stock coding clients use **cooperative pause** through MCP/checkpoints; the bridge does not claim to forcibly interrupt every coding product. See the milestone record for tested capabilities and remaining integration work.

