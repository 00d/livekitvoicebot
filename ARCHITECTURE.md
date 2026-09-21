# Architecture

## What it is

A LiveKit Agents worker (`agent.py`) plays a "patient" persona and places an
outbound SIP call to the Pretty Good AI test line. It runs a standard
pipeline voice agent — separate STT, LLM, and TTS stages, no
speech-to-speech model — driven by a per-scenario system prompt
(`scenarios.py`). `dispatch_call.py` triggers calls by creating a room and
an agent dispatch with the scenario name in the job metadata; the worker
reads that metadata, dials out, and lets the target's assistant answer
first (we called them, so we don't speak first). Every call writes a
turn-by-turn transcript (JSON + plain text) to `calls/<call_id>/` as it
happens, independent of any external service, so the transcript deliverable
never depends on a cloud dashboard being reachable after the fact.

## Provider choices and why

**LLM — Gemini (`gemini-3.6-flash`).** Given directly in the assessment
brief (existing API access), and flash-tier latency matters more here than
frontier reasoning. The "brain" only needs to hold a patient persona and
react to a scheduling conversation, not solve hard problems.

**STT/TTS — LiveKit Inference, not direct provider accounts.** Rather than
signing up for Deepgram/Cartesia/ElevenLabs directly, both stages go
through `livekit.agents.inference` (`deepgram/nova-3` for STT,
`deepgram/aura-2` for TTS), billed through the LiveKit Cloud
project that's already required for SIP. This removes an entire class
of setup friction and per-provider key management.
The trade-off is a thin dependency on LiveKit's routing layer adding a small
amount of extra hop latency instead of hitting Deepgram directly.
`deepgram/nova-2-phonecall` (tuned for 8kHz phone audio specifically)
is worth A/B-testing against `nova-3` if transcription accuracy on the
SIP-quality audio turns out to be the bottleneck.

**Turn detection & interruptions.** Silero VAD (endpoint-agnostic, cheap,
local) plus LiveKit's semantic turn-detector model
(`inference.TurnDetector`), rather than relying on VAD silence timeouts
alone. Pure VAD-based endpointing is fooled constantly by mid-sentence
pauses ("um, let me check my—" _pause_ "—insurance card"); the
semantic model uses the actual transcript to decide whether a turn is
really over, which matters directly for the `silence_and_mumbling` and
`interruption_barge_in` test scenarios. `allow_interruptions` stays on
defaults so the patient persona can genuinely barge in on the target
agent, since testing barge-in handling is an explicit scenario requirement.
`ivr_detection=True` is also enabled so the SDK's built-in AMD/IVR handling
kicks in for free if the call hits a menu or voicemail instead of a live
(AI) agent — logged separately from the conversation transcript.

**Telephony — LiveKit SIP + a Twilio Elastic SIP Trunk.** LiveKit Cloud
handles the outbound SIP signaling; Twilio is only the PSTN carrier leg.
This is the standard, documented path (`ctx.add_sip_participant`) and
avoids any hosted voice platform (Vapi/Retell/Bland are explicitly
disallowed by the brief). `setup_sip_trunk.py` is a one-time script to
register the trunk so this doesn't require manual dashboard clicking beyond
the Twilio side (buying a number, creating the trunk credentials).

## Recording: a deliberate trade-off

The two real options were (a) LiveKit's Egress API (Room Composite → file),
which needs an S3/GCS/Azure bucket configured, or (b) `AgentSession.start(record=True)`,
which uses LiveKit Cloud's built-in session recording — audio, transcript,
and traces uploaded automatically, downloadable from the project's Agent
Insights dashboard, with zero extra cloud-storage account. Given the stated
constraint of not wanting to spin up more accounts than necessary, (b) is
the default here. The cost: it's a project-level LiveKit Cloud feature
rather than a file that lands on disk automatically, so audio files for the
`calls/` folder need one manual "download" pass from the dashboard per call
before submission (documented in the README). The local
`TranscriptRecorder` in `agent.py` exists specifically so the _text_
transcript deliverable never depends on that dashboard step working —
it's written straight from the same `conversation_item_added` events the
SDK uses internally, so it exists the moment the process exits even if
cloud upload is slow, fails, or the project's observability feature is
off.

## Ending calls and safety net

The patient agent has one tool, `end_call`, which the LLM invokes itself
once its scenario objective is resolved or the conversation stalls — this
keeps calls to the requested 1–3 minute range instead of a fixed script
length. A `MAX_CALL_SECONDS` watchdog (default 240s) forcibly ends any call
that runs long regardless of what the LLM decides, so a stuck conversation
can't run indefinitely and rack up cost.

A second safety net handles the LLM itself failing outright
(exhausted retries, not a transient blip — the SDK's own `LLMError.recoverable`
flag distinguishes the two). Rather than sit in dead air until the other
side gives up and hangs up on us, `agent.py` hooks `session.on("error", ...)`
and, on an unrecoverable LLM error, has the patient say a short apology via
`session.say()` — TTS-only, so it still works even though the LLM is the
thing that's down — then ends the call cleanly. This exists because it's
exactly what happened once in testing (see below): a Gemini quota error
left the bot silent for 37 seconds while the target agent asked "are you
still there?" twice before hanging up.

## Infrastructure failures hit while getting this running

Each of these produced a genuinely confusing symptom before the actual
cause was found, so they're worth mentioning:

1. **SIP calls connected but were completely silent, no Twilio call log at
   all.** Looked like a routing/DNS problem at first. Twilio's Debugger
   (not the Call Log, which had nothing) showed error 32202 — the trunk's
   Twilio credential list had a stale/duplicate entry, so LiveKit's
   username/password never matched. Fixed by cleaning up the credential
   list and pushing the corrected password onto the _existing_ LiveKit
   trunk with `update_sip_credentials.py` rather than deleting and
   recreating it.
2. **A full `--all` batch run: the first 5 calls worked, the next 7 all
   came back with completely empty transcripts** — not even the target's
   own opening greeting got transcribed, which ruled out an LLM problem
   (STT doesn't touch the LLM at all) before it ruled out anything else.
   Turned out to be LiveKit Cloud's own concurrent-STT-connection limit:
   `dispatch_call.py`'s default 5-second stagger let multiple 2-3 minute
   calls overlap, and the project's concurrency cap silently capped how
   many of those simultaneous STT streams could actually run. Fixed by
   staggering dispatches past `MAX_CALL_SECONDS` instead of a fixed short
   gap, trading batch wall-clock time for guaranteed non-overlap.

## What I'd change with more time

Given the 6 hour limit, this skips: a self-hosted Egress pipeline for
guaranteed local audio files (see trade-off above), automatic
transcript-based bug flagging (currently manual review against
`bug_report/BUG_REPORT.md`), and multi-turn scenario branching (each call
is one fixed persona/goal rather than a decision tree that adapts based on
what the target agent says). Also worth doing: extending the
unrecoverable-error handler above to cover `STTError`/`TTSError`, not just
`LLMError` — the concurrency-limit failures above produced no graceful
apology because nothing was listening for that error type; and making
`dispatch_call.py` stagger by scenario duration automatically instead of a
manually-tuned constant, so a batch run can't silently re-trigger the same
concurrency ceiling.
