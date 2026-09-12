---
id: macos-platform
title: macOS Platform
project: macOS
department: Software Engineering
team: macOS Core
owner: Gideon Park
status: active
period: 2026-H2
aliases:
  - macos
  - mac
  - mac os
  - macos shasta
  - desktop os
  - mac software
  - finder
  - macbook
  - mac platform
  - desktop software
tags:
  - macos
  - desktop
  - window management
  - continuity
  - security
  - performance
  - tiling
  - rosetta
  - permissions
updated: 2026-08-27
---

# macOS Platform — H2 2026 Agenda

## Mission

macOS is the Mac. We ship one release a year to an install base of roughly 105 million active
machines, more than half of which are over three years old, and the job every year is to make
the system feel newer without making a fourteen-year-old muscle memory wrong.

That constraint is the whole character of this team. We are not optimizing for the demo; we are
optimizing for the person who has had the same Dock arrangement since 2013 and will file a bug
if we move it. New capability has to arrive as something you can find, not something you have
to survive. This half that means window management, continuity with the Duo, and finishing the
Intel transition we started six years ago.

## Agenda for the period

1. **Window management people actually keep using.** Tiling that survives sleep, display
   changes, and undocking from an external monitor — the three moments where today's tiling
   quietly forgets everything. Adoption, not availability, is the measure: most people who try
   tiling on macOS today stop within a week, and that is our failure, not theirs.
2. **Continuity with iPhone Duo.** The Mac becomes a display target for the Duo's inner screen
   and the Duo's cover display becomes a Mac control surface. This is the marquee cross-product
   story of the release and it is gated on the Duo announcement — if that date moves, this ships
   dark behind a flag rather than slipping the release.
3. **Finish the Rosetta wind-down.** Rosetta 2 moves to supported-but-deprecated: still present,
   no new capability, loud diagnostics. The work is not the runtime, it is getting the top 200
   Intel-only apps to either a native build or a documented migration path before we tell anyone
   the clock is running.
4. **On-device intelligence in system surfaces, inside a fixed memory budget.** Spotlight, Mail,
   and Finder get on-device models, and no feature may cost more than 1.5 GB resident on an 8 GB
   base machine. The budget is the feature. A capability that only works on a 32 GB Mac Studio is
   not a macOS capability.
5. **End permission fatigue.** A new Mac currently shows a median of 23 consent prompts in its
   first hour, which trains people to click Allow without reading. Getting under 8 without
   weakening a single guarantee means consolidating prompts by purpose and moving several to the
   point of first use.

## Success metrics

- Share of sessions using tiling: 9% → 30%, with 60% of first-time users still tiling in week four.
- Duo continuity: handoff in under 500ms, zero dropped input events across the transition.
- Top 200 Intel-only apps: 100% with a native build or published migration path before the
  deprecation is announced.
- On-device intelligence resident memory: under 1.5 GB on 8 GB machines, measured at steady state
  after a full Spotlight index.
- First-hour consent prompts: median 23 → under 8, with no reduction in the scope of what each
  prompt actually authorizes.

## Dependencies

- **iPhone Duo iOS** for the Continuity protocol and posture signals. Objective 2 is a shared
  objective and their announcement gate is our schedule risk.
- **AirPods Link Protocol** for the CoreAudio handoff path. Their multipoint objective is what
  makes audio follow a person from iPhone to Mac without a gap; the Mac half of that is ours.
- **Silicon Engineering** for M-series power and memory telemetry, which is how we hold the
  budget in objective 4 honest rather than aspirational.
- **Developer Relations** for Rosetta migration communications. Objective 3 is more a
  communications program than an engineering one.

## Explicitly not doing

- **No touch support for the Mac.** Asked every year, declined every year, declined again this
  year. The Duo continuity work in objective 2 is not a step toward it.
- Not merging macOS and iPadOS. Continuity is not convergence.
- Not redesigning Finder wholesale. It gets on-device search in objective 4 and nothing else;
  the broader rework is deferred past this release.
- Not removing Rosetta 2 in this release. Deprecated is not removed, and anyone planning against
  removal this year is planning against the wrong date.
