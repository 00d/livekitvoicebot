"""
One-time setup: registers a LiveKit outbound SIP trunk backed by a Twilio
Elastic SIP Trunk, and writes the resulting SIP_OUTBOUND_TRUNK_ID straight
into .env.local. Safe to re-run, since if a trunk with the same name already
exists, it's reused instead of creating a duplicate, and if
SIP_OUTBOUND_TRUNK_ID is already set in .env.local this is a no-op.

Prerequisites (do this in the Twilio console first):
  1. Buy any Twilio phone number (used only as the trunk's caller ID).
  2. Create an Elastic SIP Trunk (Twilio Console > Voice > Elastic SIP Trunking).
  3. Under that trunk's "Termination" settings, note the Termination SIP URI
     (looks like <something>.pstn.twilio.com).
  4. Under "Credential Lists" (or Origination auth), create a
     username/password credential and attach it to the trunk — LiveKit will
     authenticate to Twilio with these.
  5. Point the trunk's Origination URI at LiveKit's SIP signaling address:
     sip:<your-subdomain>.sip.livekit.cloud (see docs.livekit.io/telephony
     for the exact host for your project/region) so Twilio accepts calls
     LiveKit places outbound *through* Twilio.

Then fill these into .env.local:
  LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET
  TWILIO_TERMINATION_URI     e.g. my-trunk.pstn.twilio.com
  TWILIO_SIP_USERNAME
  TWILIO_SIP_PASSWORD

Usage:
    python setup_sip_trunk.py

This is called automatically by ./setup.sh — you normally don't need to run it by hand.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv, set_key

ENV_LOCAL = Path(__file__).parent / ".env.local"
TRUNK_NAME = "pgai-twilio-outbound"

load_dotenv(ENV_LOCAL)
load_dotenv()

from livekit import api  # noqa: E402  (import after env is loaded)


async def find_existing_trunk(lkapi: "api.LiveKitAPI") -> str | None:
    resp = await lkapi.sip.list_outbound_trunk(api.ListSIPOutboundTrunkRequest())
    for trunk in resp.items:
        if trunk.name == TRUNK_NAME:
            return trunk.sip_trunk_id
    return None


async def main() -> None:
    if os.getenv("SIP_OUTBOUND_TRUNK_ID"):
        print(f"SIP_OUTBOUND_TRUNK_ID already set ({os.environ['SIP_OUTBOUND_TRUNK_ID']}), skipping.")
        return

    required = ["TWILIO_TERMINATION_URI", "TWILIO_SIP_USERNAME", "TWILIO_SIP_PASSWORD"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        print(f"Skipping SIP trunk setup — missing from .env.local: {', '.join(missing)}")
        print("(Fill those in and re-run ./setup.sh once you have Twilio trunk credentials.)")
        return

    lkapi = api.LiveKitAPI()
    try:
        try:
            existing_id = await find_existing_trunk(lkapi)
        except Exception as e:
            print(f"Could not reach LiveKit ({e}).", file=sys.stderr)
            print("Check LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET in .env.local.", file=sys.stderr)
            sys.exit(1)

        if existing_id:
            sip_trunk_id = existing_id
            print(f"Reusing existing trunk {TRUNK_NAME!r}: {sip_trunk_id}")
        else:
            trunk = api.SIPOutboundTrunkInfo(
                name=TRUNK_NAME,
                address=os.environ["TWILIO_TERMINATION_URI"],
                numbers=[os.getenv("TWILIO_CALLER_ID_NUMBER", "")] if os.getenv("TWILIO_CALLER_ID_NUMBER") else [],
                auth_username=os.environ["TWILIO_SIP_USERNAME"],
                auth_password=os.environ["TWILIO_SIP_PASSWORD"],
            )
            try:
                result = await lkapi.sip.create_outbound_trunk(
                    api.CreateSIPOutboundTrunkRequest(trunk=trunk)
                )
            except Exception as e:
                print(f"Failed to create SIP trunk ({e}).", file=sys.stderr)
                print("Double-check the TWILIO_* values in .env.local against your Twilio trunk.", file=sys.stderr)
                sys.exit(1)
            sip_trunk_id = result.sip_trunk_id
            print(f"Created outbound trunk: {sip_trunk_id}")
    finally:
        await lkapi.aclose()

    if not ENV_LOCAL.exists():
        ENV_LOCAL.touch()
    set_key(str(ENV_LOCAL), "SIP_OUTBOUND_TRUNK_ID", sip_trunk_id)
    print(f"Wrote SIP_OUTBOUND_TRUNK_ID={sip_trunk_id} to {ENV_LOCAL}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyError as e:
        print(f"Missing required env var: {e}", file=sys.stderr)
        sys.exit(1)
