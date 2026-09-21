"""
Pushes the current TWILIO_SIP_USERNAME / TWILIO_SIP_PASSWORD from .env.local
onto the *existing* trunk (SIP_OUTBOUND_TRUNK_ID) in place, without deleting
and recreating it. Use this after fixing credentials on the Twilio side
(e.g. removing a duplicate/stale credential) so LiveKit's stored trunk
matches exactly.

Usage:
    python update_sip_credentials.py
"""

from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv
from livekit import api

load_dotenv(".env.local")
load_dotenv()


async def main() -> None:
    trunk_id = os.environ["SIP_OUTBOUND_TRUNK_ID"]
    username = os.environ["TWILIO_SIP_USERNAME"]
    password = os.environ["TWILIO_SIP_PASSWORD"]

    lkapi = api.LiveKitAPI()
    try:
        updated = await lkapi.sip.update_outbound_trunk_fields(
            trunk_id,
            auth_username=username,
            auth_password=password,
        )
        print(f"Updated trunk {updated.sip_trunk_id}")
        print(f"  auth_username = {updated.auth_username!r}")
        print(f"  auth_password = {'<set>' if updated.auth_password else '<empty or redacted by API>'}")
    finally:
        await lkapi.aclose()


if __name__ == "__main__":
    asyncio.run(main())
