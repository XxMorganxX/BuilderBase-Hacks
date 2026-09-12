---
id: iphone-duo-ios
title: iPhone Duo iOS
project: iPhone Duo
department: Software Engineering
team: Duo Platform
owner: Nadia Farrokhzad
status: draft
period: 2026-H2
aliases:
  - iphone duo
  - duo
  - duo ios
  - the fold
  - foldable iphone
  - folding iphone
  - book fold
  - dual display iphone
  - fold
  - folding phone
  - foldable
tags:
  - ios
  - foldable
  - multitasking
  - app compatibility
  - sdk
  - continuity
  - hinge
  - posture
  - thermal
updated: 2026-09-10
---

# iPhone Duo iOS — H2 2026 Agenda (DRAFT)

> **This agenda is a draft.** It covers an unannounced product and is still in review. Every
> number below is an internal target, not a commitment, and the announcement gate can move all
> of the dates. Treat anything sourced from this doc as provisional.

## Mission

iPhone Duo is the first iPhone whose screen changes size while someone is using it. Closed, it
is a 5.6-inch cover display that behaves like a phone. Opened, it is an 8.3-inch inner display
that behaves like something else. We own the OS layer that makes that transition a non-event.

The hard part is not the new experiences we design for the inner display. The hard part is the
1.4 million apps in the App Store that will never be recompiled for this device and must still
look deliberate on it. A developer who does nothing should get a result they would have chosen.
That is the bar, and it is the bar we will be judged against on day one.

## Agenda for the period

1. **State continuity across every fold transition.** Any app resumes in under 120ms with
   scroll position, text selection, keyboard state, and in-flight video and audio preserved.
   Zero relaunches — a relaunch on fold is a P1 bug, not a performance issue. This is the
   single objective that determines whether the device feels finished.
2. **Adaptive compatibility mode for unmodified apps.** Letterboxing the inner display is not
   an acceptable outcome. We reflow deterministically using the size classes apps already
   declare through Auto Layout and SwiftUI, plus a new hinge safe area that keeps interactive
   elements off the crease. Where we cannot reflow confidently, we choose a conservative layout
   rather than a clever one.
3. **Ship the posture API surface by developer beta 3.** Fold angle, discrete posture
   (closed / half / flat / tent), display-transition callbacks, and crease-avoidance layout
   guides. Developer beta 3 is the last build that gives launch partners enough runway, so this
   date is load-bearing for the whole announcement.
4. **Concurrent app pairs on the inner display.** Two apps side by side that survive folding,
   rotation, and backgrounding as a single restorable unit. The pair is the thing a person
   returns to, not two apps that happen to be adjacent.
5. **Stay inside the thermal and battery envelope.** Two displays and a split-cell battery, and
   no permission to feel warmer or last shorter than the current Pro. This constrains every
   objective above and has already cost us one approach to objective 4.

## Success metrics

- Fold-transition resume time: under 120ms at p95, measured to first interactive frame.
- Zero app relaunches on fold across the top 500 App Store apps.
- Visual compatibility review: 100% pass for the top 500 apps with no developer changes, 95%
  for the top 5,000.
- Posture API adopted by at least 40 launch partners at announcement.
- Unfolded video playback: 16 hours or better.
- Sustained-brightness skin temperature within the current Pro envelope, unfolded, at 25°C ambient.

## Dependencies

- **Hardware Engineering** for hinge sensor calibration and display driver timing. Objective 1
  cannot be validated on the software simulator alone and needs EVT units in developer hands.
- **AirPods Link Protocol** for audio focus across fold states. A call that starts on the cover
  display and continues on the inner display must not gap, which is their objective 3 and our
  objective 1 meeting in the middle.
- **macOS Platform** for the Continuity work that lets a Mac use the Duo as a control surface.
  Shared objective; they carry the Mac half.
- **Legal and Marketing** for the announcement gate. Nothing in this doc is public and the SDK
  seeding schedule in objective 3 is downstream of their date.

## Explicitly not doing

- **Not forking iOS.** One operating system with conditional behavior, one SDK, one set of
  frameworks. Any proposal that starts with a separate OS target for this device is out of scope
  by design, not by schedule.
- No third-party access to the raw fold-angle sensor stream this cycle. Continuous hinge angle
  is a side channel for what someone is physically doing with the device; apps get discrete
  postures and transition callbacks instead.
- Not supporting external display mirroring while folded.
- No iPadOS convergence work. The inner display is not a small iPad and we are not building
  toward one this cycle.
