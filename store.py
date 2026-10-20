"""JSON persistence with atomic writes + in-memory live log buffer."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from pathlib import Path
from typing import Any

import config
from config import (ACCOUNTS_FILE, GUILD_EXP_CACHE_FILE, LOG_BUFFER, STATE_FILE,
                    TOKEN_CACHE_FILE)

log = logging.getLogger("voidx")


def _read_json(path: Path, default: Any) -> Any:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    tmp.replace(path)


class LogBuffer(logging.Handler):
    """Ring buffer of recent lines; also fed by the logging module."""

    def __init__(self, size: int = LOG_BUFFER) -> None:
        super().__init__()
        self._items: deque[tuple[float, str, str]] = deque(maxlen=size)

    def log(self, level: str, message: str) -> None:
        self._items.append((time.time(), level.upper(), message))

    def emit(self, record: logging.LogRecord) -> None:
        self.log(record.levelname, self.format(record))

    def tail(self, count: int = 25) -> list[tuple[float, str, str]]:
        return list(self._items)[-count:]

    def clear(self) -> None:
        self._items.clear()


log_buffer = LogBuffer()
log_buffer.setFormatter(logging.Formatter("%(message)s"))
logging.getLogger().addHandler(log_buffer)
logging.getLogger().setLevel(logging.INFO)


class Store:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        raw = _read_json(ACCOUNTS_FILE, [])
        self.accounts: list[dict] = [
            a for a in raw if isinstance(a, dict) and str(a.get("uid", "")).isdigit()
        ]
        self.tokens: dict[str, dict] = _read_json(TOKEN_CACHE_FILE, {})
        self.guild_cache: dict[str, dict] = _read_json(GUILD_EXP_CACHE_FILE, {})
        self.state: dict = _read_json(STATE_FILE, {})

        # Seed the guild target once so a fresh deploy is armed with no input.
        guild = self.state.setdefault("guild", {})
        if not guild.get("id") and config.DEFAULT_GUILD_ID:
            guild["id"] = config.DEFAULT_GUILD_ID
            guild["region"] = config.DEFAULT_GUILD_REGION
            _write_json(STATE_FILE, self.state)

    # ---------------- accounts ----------------
    def get(self, uid: str) -> dict | None:
        uid = str(uid)
        for account in self.accounts:
            if str(account.get("uid")) == uid:
                return account
        return None

    def uids(self) -> list[str]:
        return [str(a["uid"]) for a in self.accounts]

    def count(self) -> int:
        return len(self.accounts)

    async def add(self, uid: str, password: str, **extra: Any) -> tuple[bool, str]:
        uid = str(uid).strip()
        password = str(password).strip()
        if not uid.isdigit():
            return False, "UID must be numeric"
        if not password:
            return False, "Password is empty"
        if self.get(uid):
            return False, f"{uid} already exists"
        account = {
            "uid": uid,
            "password": password,
            "nickname": extra.get("nickname", ""),
            "role": extra.get("role", "bot"),
            "in_guild": bool(extra.get("in_guild", False)),
            "region": extra.get("region"),
            "device_id": extra.get("device_id"),
            "paused": False,
            "added_at": int(time.time()),
            "last_token": 0,
            "status": "idle",
            "joined_guilds": [],
            "stats": {"joins": 0, "errors": 0},
        }
        self.accounts.append(account)
        await self.save_accounts()
        return True, f"Added {uid}"

    async def remove(self, uid: str) -> bool:
        uid = str(uid)
        before = len(self.accounts)
        self.accounts = [a for a in self.accounts if str(a["uid"]) != uid]
        self.tokens.pop(uid, None)
        if len(self.accounts) != before:
            await self.save_accounts()
            await self.save_tokens()
            return True
        return False

    async def set_paused(self, uid: str, paused: bool) -> bool:
        account = self.get(uid)
        if not account:
            return False
        account["paused"] = paused
        await self.save_accounts()
        return True

    async def update(self, uid: str, **fields: Any) -> None:
        account = self.get(uid)
        if not account:
            return
        account.update(fields)
        await self.save_accounts()

    async def save_accounts(self) -> None:
        async with self._lock:
            _write_json(ACCOUNTS_FILE, self.accounts)

    # ---------------- tokens ----------------
    async def save_tokens(self) -> None:
        async with self._lock:
            _write_json(TOKEN_CACHE_FILE, self.tokens)

    # ---------------- guild exp cache ----------------
    async def save_guild_cache(self) -> None:
        async with self._lock:
            _write_json(GUILD_EXP_CACHE_FILE, self.guild_cache)

    # ---------------- runtime state ----------------
    def guild(self) -> dict:
        guild = self.state.setdefault("guild", {})
        if not guild.get("id"):
            guild["id"] = config.DEFAULT_GUILD_ID
            guild["region"] = config.DEFAULT_GUILD_REGION
        return guild

    def setting(self, key: str, default: Any) -> Any:
        return self.state.setdefault("settings", {}).get(key, default)

    async def set_setting(self, key: str, value: Any) -> None:
        self.state.setdefault("settings", {})[key] = value
        await self.save_state()

    async def save_state(self) -> None:
        async with self._lock:
            _write_json(STATE_FILE, self.state)


store = Store()
