"""JoinRun batch engine, per-account keepalive loop, guild acceptor."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

import config
import garena
import game
from devices import devices
from store import log_buffer, store


@dataclass
class RunStats:
    total: int = 0
    processed: int = 0
    jwt_ok: int = 0
    jwt_fail: int = 0
    requests: int = 0
    ok: int = 0
    http_other: int = 0
    net_err: int = 0
    skipped: int = 0
    current: str = ""
    done: bool = False
    started: float = field(default_factory=time.time)
    results: list[tuple[str, str]] = field(default_factory=list)


class JoinRun:
    def __init__(self, accounts: list[tuple[str, str]], guild_id: str, region: str) -> None:
        self.accounts = accounts
        self.guild_id = guild_id
        self.region = region
        self.stats = RunStats(total=len(accounts))

    async def execute(self) -> RunStats:
        stats = self.stats
        sem = asyncio.Semaphore(config.ACCOUNT_CONCURRENCY)

        async def one(uid: str, password: str) -> None:
            async with sem:
                stats.current = uid
                await self._process(uid, password)

        for uid, password in self.accounts:
            asyncio.create_task(one(uid, password))
            await asyncio.sleep(config.JOIN_INTERVAL)
        while stats.processed < stats.total:
            await asyncio.sleep(0.5)
        stats.done = True
        return stats

    async def _process(self, uid: str, password: str) -> None:
        stats = self.stats
        account = store.get(uid)
        device = devices.for_uid(uid)
        try:
            entry = await garena.generate_jwt(uid, password, device)
            stats.jwt_ok += 1
        except Exception as exc:  # noqa: BLE001
            stats.jwt_fail += 1
            stats.results.append((uid, f"JWT fail: {exc}"))
            log_buffer.log("WARN", f"[{uid}] JWT fail: {exc}")
            if account:
                account["stats"]["errors"] += 1
                await store.update(uid, status="jwt_fail")
            stats.processed += 1
            return

        if account:
            await store.update(uid, status="joining", region=entry.get("region"))

        session = game.GameSession(uid, entry)
        for attempt in range(config.RETRIES + 1):
            try:
                stats.requests += 1
                result = await session.join_guild(self.guild_id)
            except game.GameError as exc:
                stats.net_err += 1
                if attempt == config.RETRIES:
                    stats.results.append((uid, f"network: {exc}"))
                    log_buffer.log("WARN", f"[{uid}] join network: {exc}")
                continue

            if result["status"] == 200:
                stats.ok += 1
                stats.results.append((uid, f"HTTP 200 ({result['bytes']}b) - request sent"))
                log_buffer.log("INFO", f"[{uid}] join request sent to {self.guild_id}")
                if account:
                    account["stats"]["joins"] += 1
                    if self.guild_id not in account["joined_guilds"]:
                        account["joined_guilds"].append(self.guild_id)
                    await store.update(uid, status="joined", region=entry.get("region"))
                break

            stats.http_other += 1
            stats.results.append((uid, f"HTTP {result['status']}"))
            log_buffer.log("WARN", f"[{uid}] join HTTP {result['status']}")
            break

        stats.processed += 1


class GuildAcceptor:
    """Drives the in-guild officer account to accept pending join requests."""

    def __init__(self) -> None:
        self.task: asyncio.Task | None = None

    def start(self) -> None:
        if config.AUTO_ACCEPT and self.task is None:
            self.task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        if self.task:
            self.task.cancel()
            self.task = None

    def officer(self) -> str | None:
        for account in store.accounts:
            if account.get("role") == config.OFFICER_ROLE and not account.get("paused"):
                return str(account["uid"])
        return None

    async def _loop(self) -> None:
        while True:
            try:
                await self.cycle()
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001
                log_buffer.log("ERROR", f"acceptor: {exc}")
            await asyncio.sleep(config.ACCEPT_INTERVAL)

    async def cycle(self) -> dict:
        guild_id = store.guild().get("id")
        uid = self.officer()
        if not guild_id or not uid:
            return {"skipped": True}

        entry = store.tokens.get(uid)
        if not entry:
            account = store.get(uid)
            try:
                entry = await garena.generate_jwt(
                    uid, account["password"], devices.for_uid(uid)
                )
            except Exception as exc:  # noqa: BLE001
                log_buffer.log("WARN", f"acceptor auth: {exc}")
                return {"skipped": True}

        session = game.GameSession(uid, entry)
        try:
            pending = await session.pending_requests(guild_id)
        except game.GameError as exc:
            log_buffer.log("WARN", f"acceptor requests: {exc}")
            return {"error": str(exc)}

        accepted: list[str] = store.state.setdefault("accepted_uids", [])
        done = 0
        for applicant in pending:
            if applicant in accepted:
                continue
            try:
                if await session.accept_request(applicant, guild_id):
                    accepted.append(applicant)
                    done += 1
                    log_buffer.log("INFO", f"accepted {applicant} into guild {guild_id}")
            except game.GameError as exc:
                log_buffer.log("WARN", f"accept {applicant}: {exc}")

        if done:
            await store.save_state()
        return {"pending": len(pending), "accepted": done}


class Runtime:
    """Owns the background worker tasks. One per un-paused account."""

    def __init__(self) -> None:
        self.tasks: dict[str, asyncio.Task] = {}
        self.run: JoinRun | None = None
        self.app = None

    async def start(self, app) -> None:
        self.app = app
        for uid in store.uids():
            self.spawn(uid)
        acceptor.start()
        log_buffer.log("INFO", f"runtime started with {len(self.tasks)} worker(s)")

    async def shutdown(self) -> None:
        acceptor.stop()
        for task in list(self.tasks.values()):
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        self.tasks.clear()

    def spawn(self, uid: str) -> None:
        if uid in self.tasks and not self.tasks[uid].done():
            return
        self.tasks[uid] = asyncio.create_task(self._loop(uid))

    def stop(self, uid: str) -> None:
        task = self.tasks.pop(uid, None)
        if task:
            task.cancel()

    async def restart(self, uid: str) -> None:
        self.stop(uid)
        await asyncio.sleep(0.2)
        self.spawn(uid)

    async def _loop(self, uid: str) -> None:
        while True:
            try:
                await self._tick(uid)
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001
                log_buffer.log("ERROR", f"[{uid}] worker: {exc}")
            await asyncio.sleep(config.REFRESH_INTERVAL)

    async def _tick(self, uid: str) -> None:
        account = store.get(uid)
        if not account or account.get("paused"):
            return

        device = devices.for_uid(uid)
        try:
            entry = await garena.generate_jwt(uid, account["password"], device)
        except Exception as exc:  # noqa: BLE001
            log_buffer.log("WARN", f"[{uid}] refresh fail: {exc}")
            await store.update(uid, status="auth_fail")
            return

        session = game.GameSession(uid, entry)

        if account.get("role") == config.OFFICER_ROLE or account.get("in_guild"):
            await store.update(uid, status="officer", last_token=int(time.time()))
        else:
            guild_id = account.get("guild_id") or store.guild().get("id")
            if guild_id and guild_id not in account.get("joined_guilds", []):
                guild_region = store.guild().get("region")
                if guild_region and guild_region != entry.get("region"):
                    log_buffer.log(
                        "WARN",
                        f"[{uid}] account region {entry.get('region')} "
                        f"!= guild region {guild_region}",
                    )
                try:
                    result = await session.join_guild(guild_id)
                    if result["status"] == 200:
                        account["joined_guilds"].append(guild_id)
                        log_buffer.log("INFO", f"[{uid}] join request sent to guild {guild_id}")
                except game.GameError as exc:
                    log_buffer.log("WARN", f"[{uid}] join: {exc}")

        if config.KEEPALIVE_INTERVAL:
            await session.pulse()
        await store.update(uid, status="online", last_token=int(time.time()))


runtime = Runtime()
acceptor = GuildAcceptor()
