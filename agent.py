"""
pgai-patient-caller: LiveKit Agents worker.

This worker is dispatched into a fresh room, dials the Pretty Good AI test
line as an outbound SIP call, and plays a "patient" persona (defined in
scenarios.py) against whatever answers. It logs a full turn-by-turn
transcript to disk for every call and relies on LiveKit Cloud's built-in
session recording (`record=True`) for the audio side. See ARCHITECTURE.md
for why.

Run with:
    python agent.py dev      # connect to LiveKit Cloud, wait for dispatches
Then in another terminal:
    python dispatch_call.py --scenario simple_scheduling
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from livekit.agents import (
    Agent,
    AgentSession,
    AutoSubscribe,
    JobContext,
    RoomInputOptions,
    RoomOutputOptions,
    TurnHandlingOptions,
    WorkerOptions,
    cli,
    function_tool,
    get_job_context,
    llm,
)
from livekit.agents.inference import STT, TTS, TurnDetector
from livekit.plugins import google, silero

from scenarios import DEFAULT_SCENARIO_ID, SCENARIOS

load_dotenv(".env.local")
load_dotenv()

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("pgai-patient-caller")

CALLS_DIR = Path(__file__).parent / "calls"
CALLS_DIR.mkdir(exist_ok=True)

TARGET_PHONE_NUMBER = os.getenv("TARGET_PHONE_NUMBER", "+18054398008")
SIP_OUTBOUND_TRUNK_ID = os.getenv("SIP_OUTBOUND_TRUNK_ID", "")
MAX_CALL_SECONDS = int(os.getenv("MAX_CALL_SECONDS", "240"))
DEEPGRAM_STT_MODEL = os.getenv("DEEPGRAM_STT_MODEL", "deepgram/nova-3")
DEEPGRAM_TTS_VOICE = os.getenv("DEEPGRAM_TTS_VOICE", "aura-2-luna-en")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")


def prewarm(proc):
    """Load VAD once per worker process instead of once per call."""
    proc.userdata["vad"] = silero.VAD.load()


class PatientAgent(Agent):
    """The simulated patient. Instructions come entirely from scenarios.py."""

    def __init__(self, scenario: dict):
        super().__init__(instructions=scenario["system_prompt"])
        self.scenario = scenario

    @function_tool
    async def end_call(self, reason: str):
        """Call this once the call has reached a natural conclusion: your
        objective for this call is resolved, the other party has said
        goodbye, or you're clearly stuck in an unproductive loop after a
        couple of tries. Say a brief natural goodbye first, then hang up.

        Args:
            reason: One short sentence describing why the call is ending.
        """
        logger.info("end_call requested: %s", reason)
        session = self.session
        try:
            await session.generate_reply(
                instructions="Say a brief, natural goodbye appropriate to a "
                "phone call, then stop talking."
            )
        except Exception:
            logger.exception("failed to generate goodbye line")
        job_ctx = get_job_context()
        job_ctx.shutdown(reason=f"patient ended call: {reason}")


class TranscriptRecorder:
    """Buffers conversation turns as they happen and writes them to disk."""

    def __init__(self, call_id: str, scenario_id: str, scenario: dict, phone_number: str):
        self.call_id = call_id
        self.scenario_id = scenario_id
        self.scenario = scenario
        self.phone_number = phone_number
        self.started_at = time.time()
        self.entries: list[dict] = []
        self.amd_events: list[dict] = []

    def on_conversation_item(self, item) -> None:
        role = getattr(item, "role", None)
        if role is None:
            # AgentHandoff or other non-message item; skip for the transcript.
            return
        content = getattr(item, "content", [])
        text = "".join(part for part in content if isinstance(part, str)).strip()
        if not text:
            return
        self.entries.append(
            {
                "t": getattr(item, "created_at", time.time()),
                "role": role,
                "text": text,
            }
        )

    def on_amd_prediction(self, ev) -> None:
        self.amd_events.append(
            {
                "t": time.time(),
                "category": str(getattr(ev, "category", "")),
                "reason": getattr(ev, "reason", ""),
                "transcript": getattr(ev, "transcript", ""),
            }
        )
        logger.info("AMD prediction: %s (%s)", getattr(ev, "category", "?"), getattr(ev, "reason", ""))

    def write(self, call_dir: Path, ended_reason: str) -> None:
        ended_at = time.time()
        manifest = {
            "call_id": self.call_id,
            "scenario_id": self.scenario_id,
            "scenario_name": self.scenario.get("name"),
            "scenario_category": self.scenario.get("category"),
            "phone_number": self.phone_number,
            "started_at": datetime.fromtimestamp(self.started_at, tz=timezone.utc).isoformat(),
            "ended_at": datetime.fromtimestamp(ended_at, tz=timezone.utc).isoformat(),
            "duration_seconds": round(ended_at - self.started_at, 1),
            "ended_reason": ended_reason,
            "turn_count": len(self.entries),
            "amd_events": self.amd_events,
        }
        (call_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
        (call_dir / "transcript.json").write_text(json.dumps(self.entries, indent=2))

        lines = [
            f"Call: {self.call_id}",
            f"Scenario: {self.scenario.get('name')} ({self.scenario_id})",
            f"Phone number dialed: {self.phone_number}",
            f"Duration: {manifest['duration_seconds']}s   Ended: {ended_reason}",
            "-" * 60,
        ]
        for e in self.entries:
            ts = datetime.fromtimestamp(e["t"], tz=timezone.utc).strftime("%H:%M:%S")
            speaker = "PATIENT (bot)" if e["role"] == "assistant" else "PGAI AGENT"
            lines.append(f"[{ts}] {speaker}: {e['text']}")
        (call_dir / "transcript.txt").write_text("\n".join(lines) + "\n")
        logger.info("wrote transcript for %s (%d turns) to %s", self.call_id, len(self.entries), call_dir)


async def entrypoint(ctx: JobContext):
    raw_metadata = ctx.job.metadata or "{}"
    try:
        meta = json.loads(raw_metadata)
    except json.JSONDecodeError:
        logger.warning("could not parse job metadata as JSON: %r", raw_metadata)
        meta = {}

    scenario_id = meta.get("scenario", DEFAULT_SCENARIO_ID)
    scenario = SCENARIOS.get(scenario_id)
    if scenario is None:
        logger.error("unknown scenario id %r, falling back to default", scenario_id)
        scenario_id = DEFAULT_SCENARIO_ID
        scenario = SCENARIOS[scenario_id]

    call_id = meta.get("call_id") or f"{scenario_id}-{int(time.time())}"
    phone_number = meta.get("phone_number", TARGET_PHONE_NUMBER)

    call_dir = CALLS_DIR / call_id
    call_dir.mkdir(parents=True, exist_ok=True)

    ctx.log_context_fields = {"call_id": call_id, "scenario": scenario_id}

    @ctx.room.on("participant_connected")
    def _on_participant_connected(participant):
        logger.info("participant joined: identity=%s kind=%s", participant.identity, participant.kind)

    @ctx.room.on("participant_disconnected")
    def _on_participant_disconnected(participant):
        logger.info("participant left: identity=%s", participant.identity)

    @ctx.room.on("track_subscribed")
    def _on_track_subscribed(track, publication, participant):
        logger.info(
            "subscribed to track: participant=%s kind=%s source=%s",
            participant.identity, track.kind, publication.source,
        )

    @ctx.room.on("track_subscription_failed")
    def _on_track_subscription_failed(participant, track_sid, error):
        logger.error(
            "track subscription FAILED: participant=%s track_sid=%s error=%s",
            participant.identity, track_sid, error,
        )

    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    vad = ctx.proc.userdata.get("vad") or silero.VAD.load()

    session = AgentSession(
        vad=vad,
        stt=STT(model=DEEPGRAM_STT_MODEL),
        llm=google.LLM(model=GEMINI_MODEL, api_key=os.getenv("GOOGLE_API_KEY"), temperature=0.8),
        tts=TTS(model="deepgram/aura-2", voice=DEEPGRAM_TTS_VOICE),
        turn_handling=TurnHandlingOptions(turn_detection=TurnDetector()),
        ivr_detection=True,
    )

    recorder = TranscriptRecorder(call_id, scenario_id, scenario, phone_number)

    @session.on("conversation_item_added")
    def _on_item(ev):
        recorder.on_conversation_item(ev.item)

    ended_reason = {"value": "unknown"}

    @session.on("close")
    def _on_close(ev):
        ended_reason["value"] = str(getattr(ev, "reason", "unknown"))

    @session.on("error")
    def _on_error(ev):
        error = ev.error
        if isinstance(error, llm.LLMError) and not error.recoverable:
            # Retries are exhausted (e.g. a sustained rate limit/quota error,
            # not a one-off blip) — dead air until the other side hangs up on
            # us is worse than ending the call ourselves. session.say() only
            # needs TTS, not the LLM, so this still works when the LLM is
            # completely unavailable.
            logger.error("LLM unrecoverable, ending call gracefully: %s", error.error)

            async def _bail_out() -> None:
                try:
                    await session.say(
                        "I'm sorry, I'm having some trouble on my end — let me "
                        "try calling back in a bit. Thanks, bye.",
                        allow_interruptions=False,
                    )
                except Exception:
                    logger.exception("failed to say graceful-exit line")
                ended_reason["value"] = "llm_unrecoverable"
                get_job_context().shutdown(reason="LLM unrecoverable")

            asyncio.create_task(_bail_out())

    agent = PatientAgent(scenario)

    await session.start(
        agent=agent,
        room=ctx.room,
        record=True,  # LiveKit Cloud session recording (audio+transcript), zero extra storage account needed
        room_input_options=RoomInputOptions(),
        room_output_options=RoomOutputOptions(transcription_enabled=True),
    )

    if not SIP_OUTBOUND_TRUNK_ID:
        logger.error("SIP_OUTBOUND_TRUNK_ID is not set; cannot place outbound call")
        ctx.shutdown(reason="missing SIP_OUTBOUND_TRUNK_ID")
        return

    logger.info("dialing %s via trunk %s", phone_number, SIP_OUTBOUND_TRUNK_ID)
    try:
        await ctx.add_sip_participant(
            call_to=phone_number,
            trunk_id=SIP_OUTBOUND_TRUNK_ID,
            participant_identity="pgai-target",
            participant_name="Pivot Point Ortho Assistant",
        )
    except Exception:
        logger.exception("failed to dial out to %s", phone_number)
        recorder.write(call_dir, ended_reason="dial_failed")
        ctx.shutdown(reason="dial failed")
        return

    # We deliberately do NOT speak first: we called them, so we let their
    # scheduling assistant answer and greet, then respond naturally.

    watchdog_task = asyncio.create_task(_watchdog(ctx, MAX_CALL_SECONDS, ended_reason))
    amd_hook_task = asyncio.create_task(_attach_amd_listener(session, recorder, MAX_CALL_SECONDS))

    async def _finalize(reason: str = "") -> None:
        watchdog_task.cancel()
        amd_hook_task.cancel()
        recorder.write(call_dir, ended_reason=ended_reason["value"] or reason or "unknown")

    ctx.add_shutdown_callback(_finalize)


async def _watchdog(ctx: JobContext, max_seconds: int, ended_reason: dict) -> None:
    await asyncio.sleep(max_seconds)
    logger.warning("max call duration (%ss) reached, ending call", max_seconds)
    ended_reason["value"] = "max_duration_reached"
    ctx.shutdown(reason="max duration reached")


async def _attach_amd_listener(session: AgentSession, recorder: "TranscriptRecorder", max_seconds: int) -> None:
    """`session.amd` only exists once IVR/voicemail detection actually
    activates mid-call (it's None right after `.start()`), so poll briefly
    instead of hooking it eagerly."""
    for _ in range(int(max_seconds / 0.5)):
        if session.amd is not None:
            session.amd.on("amd_prediction", recorder.on_amd_prediction)
            return
        await asyncio.sleep(0.5)


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
            agent_name=os.getenv("AGENT_NAME", "pgai-patient-caller"),
        )
    )
