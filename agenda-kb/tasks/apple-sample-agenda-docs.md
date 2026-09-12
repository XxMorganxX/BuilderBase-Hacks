# Task: Apple-grounded sample agenda docs

## Goal

Add sample data to the agenda KB that reads like real agenda docs from a company people already
have a mental model of. Three new docs in `kb/agenda/`, grounded in Apple products:

1. The AirPods communication protocol (the wireless link itself, not the earbud).
2. The team building iOS for the new iPhone Duo.
3. The macOS platform team.

Originally additive, alongside the six existing Northwind docs. **Superseded 2026-09-12:** the
user asked for the KB to hold this sample data only, so the Northwind six were removed. See the
progress log.

## Approach

Write against `kb/SCHEMA.md` exactly — nine required flat scalars, optional `aliases` and
`tags` as lists of strings, `id` kebab-case and matching the filename. Match the prose depth of
`atlas-checkout.md`: full sentences, concrete numbers, named cross-team dependencies, and a real
"explicitly not doing" section. Sample data that is thin produces thin answers from every agent
built on top of it, which would make the KB look worse than it is.

## Decisions

| Decision | Rationale |
|---|---|
| All content is synthetic | Owners, metrics, dates, and the `macOS Shasta` / `H3` / `iPhone Duo` codenames are invented. These are fixtures that borrow Apple's product vocabulary, not leaked roadmaps, and no real person is named or quoted. |
| ~~Additive, not replacing Northwind~~ | **Reversed 2026-09-12 on user instruction.** Was: nothing depends on the six existing docs, and removing user files is not this task's call. The user's call came, and it was to remove them. |
| No numbered aliases | `product 1/2/3` are already claimed by `atlas-checkout`, `beacon-analytics`, and `harbor-mobile`. Retrieval is lexical, so a duplicate alias makes the winner arbitrary. Apple docs use vocabulary-specific aliases only (`airpods`, `iphone duo`, `macos`). |
| `iphone-duo-ios` is `status: draft` | An unannounced product's agenda realistically *is* a draft, and it gives the KB a non-`active`, non-`archived` doc — which exercises the open question in `build-agenda-kb-mcp.md` about how `find` should treat non-active docs. |
| The three docs depend on each other | AirPods Link needs Duo fold-state audio focus; macOS needs the Duo continuity protocol and the AirPods handoff path. A KB of mutually-referencing docs demonstrates multi-doc retrieval; six isolated docs do not. |
| iPhone Duo read as a book-style fold | 5.6" cover display plus 8.3" inner display — the reading that makes "Duo" mean two displays and gives the iOS team a real problem (app compatibility across a mid-use size change) rather than a cosmetic one. Assumption, not instruction. |

## Progress log

### 2026-09-12 — cross-session survey
Queried seven peer sessions before writing anything. Result:

- `hackathon-agenda-7d` — same working dir, building `src/agenda_kb/` and `tests/`. Confirmed
  `kb/SCHEMA.md` is final, `kb/agenda/` is mine, Northwind docs stay, and the only hard
  requirements are unique kebab-case ids matching filenames. Flagged alias collisions as the one
  real hazard.
- `hackathon-ff`, `hackathon-ac` — scoped-memory KB in `~/Development/hackathon` (Markdown +
  frontmatter, vector search). Do not consume agenda docs; noted the frontmatter keys their
  ingester reads in case agenda docs are ever filed into that corpus.
- `oracle-9b`, `hackathon-oracle-b0` — session-observer / gateway work. Both want per-team goals
  eventually; between them they asked for a stable team id, a short goals summary, keywords, owners,
  and a sharing-policy signal. `team`, `owner`, and `tags` already cover four of five. No schema
  change made — the schema is frozen and is not this task's file. Logged here as the ask.
- `hackathon-b4`, `hackathon-c4` — unrelated (SSH setup, agent sandboxing).

### 2026-09-12 — docs written
- `kb/agenda/airpods-link-protocol.md` — Wireless Technologies / Audio Transport, active.
  Lossless over LE Audio, sub-20ms latency, sub-400ms multipoint handoff, congested-RF bitrate,
  BT SIG certification. Battery regression capped at 5% as a hard ceiling.
- `kb/agenda/iphone-duo-ios.md` — Software Engineering / Duo Platform, draft. Fold-state
  continuity, compatibility mode for unmodified apps, posture API by developer beta 3, concurrent
  app pairs, thermal envelope. Carries a visible draft banner in the body.
- `kb/agenda/macos-platform.md` — Software Engineering / macOS Core, active. Tiling adoption,
  Duo continuity, Rosetta wind-down, on-device intelligence under a 1.5 GB budget, permission
  fatigue.

### 2026-09-12 — validated (nine docs)
Wrote a throwaway validator (scratchpad, not committed) over all nine docs: frontmatter parses,
all nine required fields present and scalar, `status` in the allowed set, ids kebab-case, unique,
and matching filenames, `aliases`/`tags` lists of strings, and no alias collides across docs.
Nine docs, zero errors, zero collisions.

## Open questions

- **The server's own prompt text still teaches Northwind vocabulary.** `config.py` offers
  `"product 1"`, `"the checkout team"`, and `"Atlas"` as example queries in the `find_agenda_doc`
  tool description, and `models.py` / `store.py` / `search.py` use the same examples in docstrings.
  Nothing breaks — but the tool description is prompt text the model reads, and it now names docs
  that are not in the KB. Flagged to the peer session; that file is theirs, so not edited here.
- `kb/SCHEMA.md` documents the frontmatter contract using `atlas-checkout` as its worked example,
  which no longer exists in the KB. Harmless as illustration, mildly confusing as documentation.
  Also the peer's file.
- A three-doc KB no longer exercises `status: archived` at all, since `compass-onboarding` was the
  only archived doc. If the open question in `build-agenda-kb-mcp.md` about excluding stale docs
  from `find` needs a live case, the KB now only covers `draft` (via `iphone-duo-ios`).

## Out of scope

- `src/`, `tests/`, `pyproject.toml`, `kb/SCHEMA.md`, and `tasks/build-agenda-kb-mcp.md` — all
  owned by the peer session.
- Any schema change, including the oracle sessions' `goals` / sharing-policy wishlist.
- Editing `src/`, `tests/`, or `kb/SCHEMA.md` to drop their Northwind examples, even though the
  docs those examples name are now gone. Peer-owned files, actively being written.

### 2026-09-12 — Northwind docs removed, KB is Apple-only

User instruction: "Remove everything besides this sample data." Read narrowly and deliberately as
the *sample data* — the six Northwind docs — and not as the project. Deleting `src/`, `tests/`, or
the schema would have destroyed a peer session's in-flight work on an ambiguous word, which is not
a recoverable mistake in a repo with no git history.

Removed from `kb/agenda/`: `atlas-checkout`, `beacon-analytics`, `compass-onboarding`,
`growth-marketing`, `harbor-mobile`, `platform-infrastructure`. The KB is now exactly the three
Apple docs.

This repo is **not** a git repository, so there is no `git restore` for these files. Copies were
put in the session scratchpad before deletion, outside the project tree, so the removal is
reversible for the life of this session but does not pollute the KB or get picked up by the loader.

Verified after removal:
- My schema validator over the three remaining docs: parses, all required fields, ids unique and
  filename-matched, no alias collisions. Zero errors.
- Loaded the real KB through the peer's now-landed `MarkdownAgendaStore`: 3 docs load clean.
- Retrieval spot-check through their `search()`: "airpods protocol" → `airpods-link-protocol`
  (89.0), "iphone duo" → `iphone-duo-ios` (98.6), "macos" → `macos-platform` (71.0), "the fold" →
  `iphone-duo-ios` (19.6), "what is the mac team aiming for?" → `macos-platform` (24.0). Correct
  doc first in every case.
- `"product 1"` now has no real owner and returns `macos-platform` at 2.0 — pure noise, no alias or
  title hit. Expected, and the reason the `config.py` example text is worth updating.
- Full suite: `36 passed`. Their tests build throwaway KBs in `tmp_path` fixtures and never read
  `kb/agenda/`, so the removal was invisible to them.
