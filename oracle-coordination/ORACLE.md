# ORACLE implementation charter

Source: the user's “ORACLE — Distributed AI-Assisted Development Coordination and Arbitration Platform” initial specification, sections 1–93, supplied in this project conversation. This file summarizes the implementation contract; it is not a verbatim reproduction of that document.

## Purpose

ORACLE surrounds coding agents with coordination and specification authority. Humans keep independent sessions, machines, repositories, and partial views of related projects. The Oracle maintains explicit shared knowledge, identifies consequential missing specifications and incompatibilities, mediates bounded discussion, and distributes only necessary context.

## Required invariants

- The OKB is structured state with provenance, authority, lifecycle, visibility, and retained history.
- Human intent, agent interpretation, implementation plan, and observed implementation remain distinct.
- Confidence cannot override authority.
- Human decisions and approved specs are never silently overridden by model inference.
- Related entities and declared dependencies determine which sessions interact.
- Only affected sessions pause, at safe checkpoints.
- Decisions persist before propagation. Resume requires resolution acknowledgements.
- Negotiation is bounded. Security/product decisions escalate to authorized humans.
- Private evidence does not become public merely because a model summarized it.
- No transcript streaming, automatic code merging, or unrestricted peer chat is required.
- Messages, decisions, and active workflows recover after restart; duplicate transport delivery cannot duplicate state changes.
- Heartbeats do not invoke model inference.
- Models and transport implementations are replaceable.

## MVP choices from the supplied spec

Python 3.12+, Pydantic, SQLite, SSH, JSON, YAML, Git CLI, pytest, and a local MCP bridge. The target reasoning runtime is NemoClaw with a local Nemotron-class model. No Postgres, graph database, queue broker, or Kubernetes is required by ORACLE.

## Demonstration

Three repositories independently choose identity/authentication assumptions. ORACLE creates a durable identity question, records a human UUID decision, mediates an authentication mismatch, propagates per-session consequences, and resumes the affected sessions. A separate low-risk normalization demonstrates Oracle arbitration without giving it authority over security policy.

Implementation and evidence are mapped in [milestones](docs/milestones.md).

