"""Encrypted game-server calls: guild join, requests, accept, guild info, pulse."""
from __future__ import annotations

import time

import httpx

import config
import garena
import proto
from store import log_buffer, store


class GameError(RuntimeError):
    pass


class GameSession:
    def __init__(self, uid: str, entry: dict) -> None:
        self.uid = uid
        self.entry = entry
        self.token = entry["token"]
        self.server_url = (entry.get("server_url") or "").rstrip("/")
        self.region = entry.get("region", config.DEFAULT_REGION)
        self.key, self.iv = garena.session_keys(entry)

    def _headers(self) -> dict:
        return {
            "User-Agent": self.entry.get(
                "device_ua", "Dalvik/2.1.0 (Linux; U; Android 13;)"
            ),
            "Connection": "Keep-Alive",
            "Accept-Encoding": "gzip",
            "Content-Type": "application/octet-stream",
            "X-Unity-Version": config.X_UNITY_VERSION,
            "X-GA": "v1 1",
            "ReleaseVersion": config.RELEASE_VERSION,
            "Authorization": f"Bearer {self.token}",
        }

    def _encrypt(self, payload: bytes) -> bytes:
        return garena._encrypt(payload, self.key, self.iv)

    async def post(self, client: httpx.AsyncClient, path: str, payload: bytes) -> httpx.Response:
        if not self.server_url:
            self.server_url = config.REGION_SERVERS.get(self.region, "")
        if not self.server_url:
            raise GameError(f"no server url for region {self.region}")
        url = f"{self.server_url}/{path.lstrip('/')}"
        return await client.post(url, content=self._encrypt(payload), headers=self._headers())

    async def join_guild(self, guild_id: str) -> dict:
        payload = proto.build_guild_packet(
            guild_id, config.GUILD_ID_FIELD, config.GUILD_ID_AS_STRING
        )
        async with httpx.AsyncClient(timeout=config.HTTP_TIMEOUT) as client:
            try:
                resp = await self.post(client, config.GUILD_JOIN_PATH, payload)
            except httpx.HTTPError as exc:
                raise GameError(f"network: {exc}") from exc
        return {
            "status": resp.status_code,
            "bytes": len(resp.content),
            "body": resp.content,
            "ok": resp.status_code == 200 and bool(resp.content),
        }

    async def pending_requests(self, guild_id: str) -> list[str]:
        payload = proto.build_guild_packet(
            guild_id, config.GUILD_ID_FIELD, config.GUILD_ID_AS_STRING
        )
        async with httpx.AsyncClient(timeout=config.HTTP_TIMEOUT) as client:
            try:
                resp = await self.post(client, config.GUILD_REQUESTS_PATH, payload)
            except httpx.HTTPError as exc:
                raise GameError(f"network: {exc}") from exc
        if resp.status_code != 200:
            raise GameError(f"requests HTTP {resp.status_code}")
        exclude = frozenset({guild_id, str(self.entry.get("account_id", ""))})
        return proto.extract_uids(resp.content, exclude)

    async def accept_request(self, applicant_uid: str, guild_id: str) -> bool:
        payload = proto.build_accept_packet(
            applicant_uid, guild_id, config.ACCEPT_INCLUDE_GUILD
        )
        async with httpx.AsyncClient(timeout=config.HTTP_TIMEOUT) as client:
            try:
                resp = await self.post(client, config.GUILD_ACCEPT_PATH, payload)
            except httpx.HTTPError as exc:
                raise GameError(f"network: {exc}") from exc
        return resp.status_code == 200

    async def guild_info(self, guild_id: str) -> dict:
        payload = proto.build_guild_packet(
            guild_id, config.GUILD_ID_FIELD, config.GUILD_ID_AS_STRING
        )
        async with httpx.AsyncClient(timeout=config.HTTP_TIMEOUT) as client:
            try:
                resp = await self.post(client, config.GUILD_INFO_PATH, payload)
            except httpx.HTTPError as exc:
                raise GameError(f"network: {exc}") from exc
        ints = proto.find_ints(resp.content)
        return {
            "status": resp.status_code,
            "ints": ints,
            "exp": ints.get(2),
            "level": ints.get(3),
            "members": ints.get(4),
            "ok": resp.status_code == 200,
        }

    async def pulse(self) -> bool:
        async with httpx.AsyncClient(timeout=config.HTTP_TIMEOUT) as client:
            try:
                resp = await self.post(client, config.PULSE_PATH, b"")
            except httpx.HTTPError:
                return False
        return resp.status_code == 200


async def refresh_guild_cache(guild_id: str, region: str) -> dict | None:
    """Read guild exp/glory through the first healthy account session."""
    for uid in store.uids():
        account = store.get(uid)
        if not account or account.get("paused"):
            continue
        entry = store.tokens.get(uid)
        if not entry:
            continue
        session = GameSession(uid, entry)
        session.region = region or session.region
        try:
            info = await session.guild_info(guild_id)
        except GameError:
            continue
        if info["ok"]:
            store.guild_cache[guild_id] = {
                "region": session.region,
                "exp": info.get("exp"),
                "level": info.get("level"),
                "members": info.get("members"),
                "updated_at": int(time.time()),
            }
            await store.save_guild_cache()
            log_buffer.log("INFO", f"guild cache updated for {guild_id}")
            return store.guild_cache[guild_id]
    return None
