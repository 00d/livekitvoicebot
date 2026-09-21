"""
Diagnostic: prints exactly what LiveKit has stored for your outbound SIP
trunk(s), so you can eyeball it against what's actually configured in the
Twilio console. Doesn't place any calls.

Usage:
    python check_sip_trunk.py
"""

from __future__ import annotations

import asyncio

from dotenv import load_dotenv
from livekit import api

load_dotenv(".env.local")
load_dotenv()


async def main() -> None:
    lkapi = api.LiveKitAPI()
    try:
        resp = await lkapi.sip.list_outbound_trunk(api.ListSIPOutboundTrunkRequest())
        if not resp.items:
            print("No outbound trunks exist on this LiveKit project at all.")
            print("-> setup_sip_trunk.py either wasn't run, or failed before creating one.")
            return

        for t in resp.items:
            print("=" * 60)
            print(f"sip_trunk_id   = {t.sip_trunk_id}")
            print(f"name           = {t.name!r}")
            print(f"address        = {t.address!r}   <-- must exactly match your Twilio Termination SIP URI (host only, no 'sip:' prefix, no port)")
            print(f"transport      = {t.transport}")
            print(f"auth_username  = {t.auth_username!r}")
            print(f"auth_password  = {'<set>' if t.auth_password else '<empty or redacted by API>'}")
            print(f"numbers        = {list(t.numbers)!r}")
            print(f"media_encryption = {t.media_encryption}")
    finally:
        await lkapi.aclose()


if __name__ == "__main__":
    asyncio.run(main())
