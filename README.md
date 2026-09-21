# Pretty Good AI — Patient Caller Voice Bot

A LiveKit Agents (Python, pipeline mode — separate STT/LLM/TTS, no
speech-to-speech) voice bot that calls the assessment test line and role-plays
as a patient to QA the scheduling assistant. See `ARCHITECTURE.md` for the
design writeup and `bug_report/BUG_REPORT.md` for findings.

## 1. Accounts you need first

| Variable | Where to get it |
| `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | https://cloud.livekit.io |
| `GOOGLE_API_KEY` | https://aistudio.google.com/apikey |
| `TWILIO_TERMINATION_URI`, `TWILIO_SIP_USERNAME`, `TWILIO_SIP_PASSWORD`, `TWILIO_CALLER_ID_NUMBER` | Twilio Console, Elastic SIP Trunking |

Deepgram STT/TTS need no separate account since they're routed through
LiveKit Inference and billed with your LiveKit Cloud project (see `ARCHITECTURE.md`).

## 2. Setup — one command

```bash
git clone <this-repo>
cd pgai-voicebot
cp .env.example .env.local   # fill in the keys from the table above
./setup.sh
```

`./setup.sh` creates the virtual env, installs dependencies, downloads the
Silero VAD model files, and registers (or reuses) the Twilio SIP trunk —
writing `SIP_OUTBOUND_TRUNK_ID` back into `.env.local` for you. It's
idempotent: if you re-run it after only some accounts are ready, it
installs what it can and cleanly skips the trunk step until the Twilio
vars are filled in, telling you what's still missing. If you haven't set
up Twilio yet and just want to try scheduling calls once that part is
ready, fill in the LiveKit/Google keys now, run `./setup.sh`, add the
Twilio keys later, and run `./setup.sh` again — it'll pick up right where
it left off.

## 3. Run it

Terminal 1 — start the worker (leave this running):

```bash
source venv/bin/activate && python agent.py dev
```

Terminal 2 — place calls:

```bash
python dispatch_call.py --list                      # see all scenarios
python dispatch_call.py --scenario simple_scheduling # one call
python dispatch_call.py --all                        # one call per scenario (12 calls)
```

Each call's transcript is in `calls/<scenario>-<timestamp>/`:

- `transcript.txt` — human-readable, timestamped
- `transcript.json` — same data, structured
- `manifest.json` — scenario, duration, how the call ended, any AMD/IVR events

## 4. Getting the audio recordings

Audio is recorded automatically with LiveKit Cloud's built-in session
recording (`record=True` in `agent.py` — no S3/GCS account needed, see
`ARCHITECTURE.md` for why).

## 5. Scenarios covered

Run `python dispatch_call.py --list` for the live list. Covers: new patient
scheduling, rescheduling, canceling, medication refills (including a
definite prescription request and a refill for a medication not on
file), office hours/location/insurance questions, and five edge cases
(weekend request when the office is closed, interruptions, a
deliberately vague opening request, insurance that may not be accepted, a
repeat caller, and a mid-sentence pause). See `scenarios.py` for the full personas.

## Notes

- `TARGET_PHONE_NUMBER` defaults to the assessment's test number (`+1-805-439-8008`)
- `MAX_CALL_SECONDS` (default 240) is a hard safety net hangup independent
  of the LLM's own `end_call` tool, so a stuck call can't run forever.
- No secrets are committed; `.env.local` is git-ignored.
