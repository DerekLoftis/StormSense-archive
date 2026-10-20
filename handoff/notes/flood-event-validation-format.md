---
name: flood-event-validation-format
description: Derek wants the Sept 2026 validation deck format reused for every major flood event look-back (diagnostically critical)
metadata:
  type: feedback
  modified: 2026-10-08T02:29:26.217Z
---

When looking back at a major flood event, produce the same output as the Sept 2026 deck (tidewatch-asa/output/monthly_decks/2026-09/, built by asa/monthly_deck.py). Derek's words (2026-10-08 02:27Z): "Exactly what I was looking for. Please remember this output format when we look back at major flood events, as it is diagnostically critical!"

The format:
- a pptx with autoplay embedded mp4s, stations in sequence;
- each station opens with a full stats table for every forecast (TW Skill, plus NWS 36 h and 72 h columns where available);
- then one ~5 s animation per 36 h forecast (time series plus animated regression and stats table; ~6 s with the NWS 72 h rows);
- forecasts coloured in sequence red, orange, yellow, green, blue, indigo, violet, then red again; observations blue;
- last, a composite: observations as a thin light-blue line underneath, each forecast labelled centred below it in black with a 2 px halo in its colour, giving issuance time and TWskill.

**Why:** Derek uses it to diagnose forecast behaviour at flood peaks, including Tidewatch vs NWS.
**How to apply:** default to this format for any event review (e.g. the 2025 king tide and nor'easter, future storms) without asking. See [[tidewatch-asa]].
