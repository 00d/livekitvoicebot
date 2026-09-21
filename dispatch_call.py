"""
Dispatch script — creates a LiveKit room + agent dispatch to place one (or
all) test calls against the Pretty Good AI test line.

Usage:
    python dispatch_call.py --scenario simple_scheduling
    python dispatch_call.py --scenarios hours_location_insurance,sunday_hours_edge_case
    python dispatch_call.py --all                     # one call per scenario
    python dispatch_call.py --all --repeat 2           # two passes over all scenarios
    python dispatch_call.py --list                     # show available scenarios
    python dispatch_call.py --scenario medication_refill --phone +18054398008

Requires `python agent.py dev` (or `start`) to already be running so a
worker is registered and listening for dispatches.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time

from dotenv import load_dotenv
from livekit import api

from scenarios import ALL_SCENARIO_IDS, DEFAULT_SCENARIO_ID, SCENARIOS

load_dotenv(".env.local")
load_dotenv()

AGENT_NAME = os.getenv("AGENT_NAME", "pgai-patient-caller")
TARGET_PHONE_NUMBER = os.getenv("TARGET_PHONE_NUMBER", "+18054398008")


async def dispatch_one(lkapi: api.LiveKitAPI, scenario_id: str, phone_number: str) -> str:
    call_id = f"{scenario_id}-{int(time.time())}"
    room_name = f"pgai-call-{call_id}"

    await lkapi.room.create_room(api.CreateRoomRequest(name=room_name))

    metadata = json.dumps(
        {
            "scenario": scenario_id,
            "call_id": call_id,
            "phone_number": phone_number,
        }
    )
    await lkapi.agent_dispatch.create_dispatch(
        api.CreateAgentDispatchRequest(
            agent_name=AGENT_NAME,
            room=room_name,
            metadata=metadata,
        )
    )
    print(f"dispatched  scenario={scenario_id:<28} call_id={call_id}  room={room_name}")
    return call_id


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--scenario", choices=ALL_SCENARIO_IDS, help="Run a single scenario")
    group.add_argument(
        "--scenarios",
        help="Run a comma-separated list of specific scenarios, e.g. "
        "hours_location_insurance,sunday_hours_edge_case",
    )
    group.add_argument("--all", action="store_true", help="Run every scenario once")
    group.add_argument("--list", action="store_true", help="List available scenarios and exit")
    parser.add_argument("--repeat", type=int, default=1, help="Repeat --all this many times (default 1)")
    parser.add_argument("--phone", default=TARGET_PHONE_NUMBER, help="Override the number to dial")
    parser.add_argument(
        "--stagger",
        type=float,
        default=5.0,
        help="Seconds to wait between dispatches when running multiple calls (default 5)",
    )
    args = parser.parse_args()

    if args.list:
        for sid, s in SCENARIOS.items():
            print(f"{sid:<28} [{s['category']:<10}] {s['name']}")
        return

    scenario_ids: list[str]
    if args.all:
        scenario_ids = ALL_SCENARIO_IDS * args.repeat
    elif args.scenarios:
        scenario_ids = [s.strip() for s in args.scenarios.split(",") if s.strip()]
        unknown = [s for s in scenario_ids if s not in SCENARIOS]
        if unknown:
            parser.error(f"unknown scenario id(s): {', '.join(unknown)}")
    elif args.scenario:
        scenario_ids = [args.scenario]
    else:
        scenario_ids = [DEFAULT_SCENARIO_ID]

    lkapi = api.LiveKitAPI()  # reads LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET from env
    try:
        for i, sid in enumerate(scenario_ids):
            await dispatch_one(lkapi, sid, args.phone)
            if i < len(scenario_ids) - 1:
                await asyncio.sleep(args.stagger)
    finally:
        await lkapi.aclose()


if __name__ == "__main__":
    asyncio.run(main())
