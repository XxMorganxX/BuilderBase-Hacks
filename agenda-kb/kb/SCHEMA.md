# Agenda Doc Schema

One file per agenda doc, in `kb/agenda/`. Filename should match the `id`: `kb/agenda/<id>.md`.

Every doc is Markdown with a YAML frontmatter block. The frontmatter is what makes the doc
*selectable*; the body is what the agent actually reads back.

```yaml
---
id: atlas-checkout            # required. kebab-case, unique, matches the filename
title: Atlas Checkout         # required. human name of the product or department
project: Atlas                # required. the company project this doc belongs to
department: Payments          # required. owning department
team: Checkout Squad          # required. the specific team
owner: Dana Okafor            # required. the manager accountable for this doc
status: active                # required. active | draft | archived
period: 2026-H2               # required. the horizon this agenda covers
aliases:                      # optional but strongly recommended
  - product 1
  - checkout
tags:                         # optional. free-form topical keywords
  - payments
  - conversion
updated: 2026-08-14           # required. ISO date this doc was last revised
---
```

## Why aliases matter

An agent is rarely asked about "atlas-checkout". It is asked about "product 1", "the
checkout flow", "Dana's team". Every name a human might use belongs in `aliases`. This is
the single highest-leverage field for retrieval quality.

## Body

Free-form Markdown. The suggested sections — this is a convention, not enforced:

- **Mission** — one paragraph: what this team exists to do.
- **Agenda for the period** — the numbered objectives.
- **Success metrics** — how each objective is measured.
- **Dependencies** — other teams this agenda relies on.
- **Explicitly not doing** — the boundaries. Prevents an agent from assuming scope.

Write it in full prose. The agent returns the body verbatim, so vagueness here becomes
vagueness in every answer built on it.
