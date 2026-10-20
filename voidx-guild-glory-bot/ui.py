"""Inline keyboards + text builders for the VOID X dashboard."""
from __future__ import annotations

import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from config import (ACCEPT_INTERVAL, ADMIN_IDS, AUTO_ACCEPT, BOT_NAME,
                    MAX_WIZARD_ACCOUNTS, REGION_SERVERS)
from devices import devices
from store import log_buffer, store


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def _row(*pairs: tuple[str, str]) -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text, callback_data=data) for text, data in pairs]


def main_menu(admin: bool) -> InlineKeyboardMarkup:
    if not admin:
        return InlineKeyboardMarkup(
            [_row(("Auto guild join", "wiz:start")), _row(("Help & support", "menu:help"))]
        )
    return InlineKeyboardMarkup(
        [
            _row(("Live dashboard", "menu:dash"), ("Accounts", "menu:accts:0")),
            _row(("Add UID account", "menu:add"), ("Add token", "menu:token")),
            _row(("Pause / resume", "menu:pause"), ("Live logs", "menu:logs")),
            _row(("Refresh", "menu:refresh"), ("Restart", "menu:restart")),
            _row(("Delete account", "menu:del"), ("Guild info", "menu:ginfo")),
            _row(("Clan join", "menu:clanjoin"), ("Accept now", "menu:accept")),
            _row(("Auto guild join", "wiz:start"), ("Settings", "menu:settings")),
            _row(("Help & support", "menu:help")),
        ]
    )


def back(to: str = "menu:home") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([_row(("Back", to), ("Home", "menu:home"))])


def dashboard_text() -> str:
    accounts = store.accounts
    online = sum(1 for a in accounts if a.get("status") == "online")
    paused = sum(1 for a in accounts if a.get("paused"))
    officers = sum(1 for a in accounts if a.get("role") == "officer")
    guild = store.guild()
    accepted = len(store.state.get("accepted_uids", []))
    return (
        f"*{BOT_NAME} - live dashboard*\n\n"
        f"Accounts: `{len(accounts)}`\n"
        f"Online: `{online}`\n"
        f"Paused: `{paused}`\n"
        f"Officer accounts: `{officers}`\n"
        f"Cached JWTs: `{len(store.tokens)}`\n"
        f"Device pool: `{devices.size}`\n"
        f"Guild: `{guild.get('id') or 'not set'}` (`{guild.get('region') or '-'}`)\n"
        f"Accepted total: `{accepted}`\n"
        f"Auto-accept: `{'on' if AUTO_ACCEPT else 'off'}` every `{ACCEPT_INTERVAL}s`\n\n"
        f"_Updated {time.strftime('%H:%M:%S')}_"
    )


def accounts_text(page: int = 0, per_page: int = 10) -> str:
    accounts = store.accounts
    if not accounts:
        return "*Accounts*\n\nNo accounts yet. Use `Add UID account`."
    pages = max(1, (len(accounts) + per_page - 1) // per_page)
    page = max(0, min(page, pages - 1))
    chunk = accounts[page * per_page:(page + 1) * per_page]
    lines = [f"*Accounts* ({page + 1}/{pages}) - {len(accounts)} total\n"]
    for account in chunk:
        flag = "paused" if account.get("paused") else account.get("status", "idle")
        role = account.get("role", "bot")
        lines.append(
            f"`{account['uid']}` | {role} | {flag} | "
            f"joins {account['stats']['joins']} | err {account['stats']['errors']}"
        )
    return "\n".join(lines)


def logs_text(count: int = 20) -> str:
    items = log_buffer.tail(count)
    if not items:
        return "*Live logs*\n\n(empty)"
    lines = ["*Live logs* (latest 20)\n"]
    for ts, level, message in items:
        lines.append(f"`{time.strftime('%H:%M:%S', time.localtime(ts))}` {level[:1]} {message}")
    return "\n".join(lines)


def guild_text() -> str:
    guild = store.guild()
    cache = store.guild_cache.get(guild.get("id", ""), {})
    return (
        "*Guild info*\n\n"
        f"Guild ID: `{guild.get('id') or 'not set'}`\n"
        f"Region: `{guild.get('region') or 'not set'}`\n"
        f"Cached exp: `{cache.get('exp', '-')}`\n"
        f"Cached level: `{cache.get('level', '-')}`\n"
        f"Cached members: `{cache.get('members', '-')}`\n\n"
        f"Regions: `{', '.join(sorted(REGION_SERVERS))}`"
    )


def help_text(admin: bool) -> str:
    common = (
        "*Help & support*\n\n"
        "`/start` dashboard  `/cancel` abort wizard\n"
        "`/autojoin` run the guild-join wizard\n\n"
        f"Max {MAX_WIZARD_ACCOUNTS} accounts per wizard run."
    )
    if not admin:
        return common + "\n\nAdmins unlock account/token/guild controls."
    return common + (
        "\n\n*Admin commands*\n"
        "`/status` `/accounts` `/logs` `/accept`\n"
        "`/add UID PASSWORD` `/token TOKEN` `/delete UID`\n"
        "`/pause UID` `/pauseall` `/refresh UID` `/restart UID`\n"
        "`/clanjoin GUILD_ID` `/guild GUILD_ID REGION`"
    )


def progress_text(run) -> str:
    stats = run.stats
    elapsed = max(1.0, time.time() - stats.started)
    rate = stats.processed / elapsed * 60
    lines = [
        "*Auto guild join - live*",
        f"Guild: `{run.guild_id}` | Region: `{run.region}`",
        "",
        f"Progress: `{stats.processed}/{stats.total}`",
        f"JWT ok/fail: `{stats.jwt_ok}` / `{stats.jwt_fail}`",
        f"Join requests: `{stats.requests}`",
        f"HTTP 200: `{stats.ok}` | other: `{stats.http_other}` | net err: `{stats.net_err}`",
        f"Rate: `{rate:.1f}` acct/min | current: `{stats.current or '-'}`",
    ]
    if stats.results:
        lines.append("\n*Last results*")
        for uid, note in stats.results[-5:]:
            lines.append(f"`{uid}` {note}")
    return "\n".join(lines)


def summary_text(run) -> str:
    stats = run.stats
    lines = [
        "*Auto guild join - finished*",
        f"Processed `{stats.processed}/{stats.total}` in `{time.time() - stats.started:.0f}s`",
        f"JWT ok `{stats.jwt_ok}` | JWT fail `{stats.jwt_fail}`",
        f"HTTP 200 `{stats.ok}` | other `{stats.http_other}` | net err `{stats.net_err}`",
    ]
    if stats.results:
        lines.append("\n*Per-account*")
        for uid, note in stats.results[:30]:
            lines.append(f"`{uid}` {note}")
    return "\n".join(lines)
