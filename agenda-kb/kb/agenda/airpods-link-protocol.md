---
id: airpods-link-protocol
title: AirPods Link Protocol
project: AirPods Link
department: Wireless Technologies
team: Audio Transport
owner: Ines Okonkwo
status: active
period: 2026-H2
aliases:
  - airpods
  - airpods pro
  - earbuds
  - airpods protocol
  - airpods link
  - audio protocol
  - wireless audio
  - le audio
  - bluetooth audio
  - headphones
  - airpods audio
tags:
  - wireless
  - bluetooth
  - le audio
  - codec
  - latency
  - spatial audio
  - pairing
  - handoff
  - lossless
  - multipoint
updated: 2026-09-04
---

# AirPods Link — H2 2026 Agenda

## Mission

We own the radio link between an Apple device and the audio on someone's head. AirPods Link
is the transport underneath every pair of AirPods: discovery and pairing, codec negotiation,
retransmission, clock synchronization, and handoff between a person's devices. We do not own
the earbud, the codec, or the spatial renderer — we own the fact that sound arrives, in
order, on time, and without the listener ever forming a thought about it.

Our work is measured almost entirely in things that do not happen. No dropout on a subway
platform. No lip-sync drift in the last five minutes of a film. No two-second silence when a
call lands on a Mac while music is playing from an iPhone. Every objective below is a way of
buying more of that silence.

## Agenda for the period

1. **Ship lossless 48 kHz / 24-bit stereo over the H3 link.** Sustained lossless needs about
   1.15 Mbps with headroom, which classic Bluetooth A2DP cannot carry. This runs on our
   isochronous channel extension over LE Audio, negotiated down gracefully when the link
   budget will not support it. This is the defining bet of the half and the reason the H3
   radio exists.
2. **Cut end-to-end audio latency from 38ms to under 20ms.** Measured microphone-to-driver on
   the person, not controller-to-controller on the bench. Spatial audio head tracking and
   games both break perceptually somewhere between 20ms and 30ms, and today we are on the
   wrong side of that line. Most of the win is in collapsing our two-stage buffering into a
   single adaptive jitter buffer.
3. **Multipoint handoff in under 400ms with no audible gap.** A person wearing one pair of
   AirPods owns an iPhone, a Mac, and increasingly a Vision Pro. Today handoff takes 1.3s and
   clips the first syllable of a call. The target is a transition nobody notices, including
   across an iPhone Duo fold transition where audio focus moves between apps mid-stream.
4. **Hold bitrate in congested RF.** Airports, arenas, and open-plan offices are the worst
   2.4 GHz environments we ship into and the ones most likely to be filmed and posted. We are
   building an adaptive bitrate ladder with per-packet link quality feedback so degradation is
   a quiet drop in bitrate rather than a dropout.
5. **Certify against the BT SIG LE Audio profile.** Interoperability with non-Apple hosts is
   not optional, and our proprietary extensions have to sit cleanly on top of a compliant
   base rather than beside it.

## Success metrics

- Lossless available on 95% of sessions with an H3-equipped source device within 2m line of sight.
- End-to-end latency: 38ms → under 20ms at p95, measured on-person.
- Device-to-device handoff: 1.3s → under 400ms at p95, with zero audible gap in blind listening panels.
- Dropout-minutes per listening hour in a certified congested-RF test cell: 0.9% → under 0.1%.
- Battery regression from lossless plus low latency capped at 5% of rated playback time. This
  is a hard ceiling, not a target — an objective that breaches it does not ship.
- BT SIG LE Audio certification signed off before the fall release candidate.

## Dependencies

- **Silicon Engineering** for the H3 radio and its power telemetry. Objectives 1 and 2 are
  both gated on H3 samples landing by the end of the month.
- **macOS Platform** for the CoreAudio driver path and the Mac side of multipoint handoff.
  Objective 3 is half theirs.
- **iPhone Duo iOS** for fold-state audio focus. When the device changes posture mid-call, the
  OS decides which app owns audio and we have to follow that decision without a gap.
- **Vision Products Group** for the head-tracking timing budget that objective 2 has to fit inside.

## Explicitly not doing

- Not removing the classic Bluetooth A2DP fallback. Non-Apple hosts are a permanent
  requirement and any proposal that strips the fallback is dead on arrival.
- No Wi-Fi-based audio transport this half. Prototyped in H1; the power cost was roughly 4x
  and the range behavior was worse in exactly the congested environments we care about.
- Not owning ALAC or the spatial renderer. Requests about codec quality or head-tracking
  behavior route to Audio Software, not here.
- No public third-party SDK for the proprietary link extension this half. Ask again once the
  BT SIG base is certified.
