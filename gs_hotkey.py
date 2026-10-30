#!/home/shyn/Documents/Workspaces/The-GREAT-SAGE-Linux/.venv/bin/python
"""Pokes a running Great Sage from a compositor keybind (the niri path).

niri keybinds fire on the key PRESS - the key-up never reaches the app -
so this sends ``ptt_toggle`` by default: press once to start listening,
press again to stop and transcribe. Great Sage must already be running;
a failed connect simply means it is not.

Usage:
    gs_hotkey.py [ptt_toggle | ptt_start | ptt_stop]

Adding more key combos = adding more niri ``binds`` entries that spawn
this with a message type the server understands.
"""
import asyncio
import json
import sys

import websockets

# Keep in sync with great_sage/server.py (PORT = 8765). Deliberately not
# imported from there: importing the server module drags in torch and
# friends, and a keybind poke should be near-instant.
URI = "ws://localhost:8765"


async def _send(msg_type: str) -> None:
    async with websockets.connect(URI) as ws:
        await ws.send(json.dumps({"type": msg_type}))


def main() -> int:
    msg_type = sys.argv[1] if len(sys.argv) > 1 else "ptt_toggle"
    try:
        asyncio.run(_send(msg_type))
    except Exception as exc:
        # A spawned process has no terminal; this lands nowhere, but it
        # makes the failure visible if the script is run by hand.
        print(f"great-sage hotkey ({msg_type}): {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
