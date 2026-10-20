"""Pre-flight: guest OAuth -> MajorLogin -> JWT -> guild call. Run before deploying."""
from __future__ import annotations

import asyncio
import sys

import httpx

import config
import game
import garena
from devices import devices
from store import store


async def main() -> int:
    guild = store.guild()
    print(f"guild target : {guild.get('id')} ({guild.get('region')})")
    print(f"accounts     : {len(store.accounts)}")
    if not store.accounts:
        print("!! accounts.json is empty")
        return 1

    uid = sys.argv[1] if len(sys.argv) > 1 else store.uids()[0]
    account = store.get(uid)
    device = devices.for_uid(uid)
    print(f"device       : {devices.summary(device)}")

    print("\n[1/3] guest OAuth ...")
    async with httpx.AsyncClient(timeout=config.HTTP_TIMEOUT) as client:
        try:
            token, open_id = await garena.guest_token(client, uid, account["password"])
        except garena.AuthError as exc:
            print(f"  FAIL {exc}")
            return 1
    print(f"  OK  access_token={token[:12]}...  open_id={open_id}")

    print("[2/3] MajorLogin -> JWT ...")
    try:
        entry = await garena.generate_jwt(uid, account["password"], device, force=True)
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL {exc}")
        return 1
    print(f"  OK  region={entry['region']}  server={entry['server_url']}")
    print(f"      jwt={entry['token'][:30]}...")

    session = game.GameSession(uid, entry)
    if account.get("role") == config.OFFICER_ROLE:
        print("[3/3] officer: read pending requests ...")
        try:
            pending = await session.pending_requests(guild["id"])
            print(f"  OK  pending={len(pending)} {pending[:5]}")
        except game.GameError as exc:
            print(f"  FAIL {exc}")
    else:
        print("[3/3] send join request ...")
        try:
            result = await session.join_guild(guild["id"])
            print(f"  HTTP {result['status']}  bytes={result['bytes']}")
        except game.GameError as exc:
            print(f"  FAIL {exc}")

    print("\nDone. If steps 1 and 2 passed, the hard part is behind you.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
