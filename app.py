"""VOID X BOT - Telegram control panel for Free Fire guild auto-join."""
import bootstrap

bootstrap.run()

import asyncio  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import re  # noqa: E402
import time  # noqa: E402

from telegram import InlineKeyboardMarkup, Update  # noqa: E402
from telegram.constants import ParseMode  # noqa: E402
from telegram.error import BadRequest  # noqa: E402
from telegram.ext import (Application, ApplicationBuilder,  # noqa: E402
                          CallbackQueryHandler, CommandHandler,
                          ContextTypes, MessageHandler, filters)

import config  # noqa: E402
import game  # noqa: E402
import ui  # noqa: E402
from store import log_buffer, store  # noqa: E402
from worker import JoinRun, acceptor, runtime  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("voidx")

PAIR_RE = re.compile(r"^(\d{6,20})\s*[:|\s]\s*(\S+)$")
GUEST_KEYS_UID = ("com.garena.msdk.guest_uid", "guest_uid", "uid")
GUEST_KEYS_PW = ("com.garena.msdk.guest_password", "guest_password", "password")


def is_admin(update: Update) -> bool:
    user = update.effective_user
    return bool(user and ui.is_admin(user.id))


def flow(context: ContextTypes.DEFAULT_TYPE) -> dict:
    return context.user_data.setdefault("flow", {})


def parse_pairs(text: str) -> tuple[list[tuple[str, str]], list[str]]:
    pairs: list[tuple[str, str]] = []
    bad: list[str] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = PAIR_RE.match(line)
        if not match:
            bad.append(line[:40])
            continue
        uid, password = match.group(1), match.group(2)
        if uid in seen:
            continue
        seen.add(uid)
        pairs.append((uid, password))
    return pairs, bad


def parse_guest_blob(text: str) -> list[tuple[str, str]]:
    """Accept a guest100067.txt style JSON blob (single or one-per-line)."""
    found: list[tuple[str, str]] = []
    for chunk in [text] + text.splitlines():
        chunk = chunk.strip()
        if not chunk.startswith("{"):
            continue
        try:
            data = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        info = data.get("guest_account_info") or data.get("guest") or data
        if not isinstance(info, dict):
            continue
        uid = next((info[k] for k in GUEST_KEYS_UID if info.get(k)), None)
        password = next((info[k] for k in GUEST_KEYS_PW if info.get(k)), None)
        if uid and password:
            pair = (str(uid), str(password))
            if pair not in found:
                found.append(pair)
    return found


# ----------------------------------------------------------------- handlers
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    admin = is_admin(update)
    context.user_data.pop("flow", None)
    await update.message.reply_text(
        f"*{config.BOT_NAME}*\n\n"
        + ("Admin dashboard ready." if admin else "Guest access: auto guild join only."),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=ui.main_menu(admin),
    )


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("flow", None)
    await update.message.reply_text(
        "Wizard cancelled.", reply_markup=ui.main_menu(is_admin(update))
    )


async def cmd_autojoin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    flow(context).update({"step": "accounts", "pairs": []})
    await update.message.reply_text(
        f"*Auto guild join*\n\nSend a `.txt` file or paste lines of `UID:PASSWORD`.\n"
        f"Max {config.MAX_WIZARD_ACCOUNTS} accounts.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=ui.back(),
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        return
    await update.message.reply_text(
        ui.dashboard_text(), parse_mode=ParseMode.MARKDOWN, reply_markup=ui.back()
    )


async def cmd_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        return
    await update.message.reply_text(
        ui.accounts_text(0), parse_mode=ParseMode.MARKDOWN, reply_markup=ui.back()
    )


async def cmd_logs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        return
    await update.message.reply_text(
        ui.logs_text(), parse_mode=ParseMode.MARKDOWN, reply_markup=ui.back()
    )


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        return
    if len(context.args) >= 2:
        await _add_and_report(update, context.args[0], context.args[1])
    else:
        context.user_data["awaiting"] = "add"
        await update.message.reply_text(
            "Send `UID PASSWORD` (or upload a guest `.txt`).", parse_mode=ParseMode.MARKDOWN
        )


async def _add_and_report(update: Update, uid: str, password: str) -> None:
    ok, message = await store.add(uid, password)
    if ok:
        runtime.spawn(str(uid))
    await update.message.reply_text(
        ("✅ " if ok else "⚠️ ") + message, reply_markup=ui.main_menu(True)
    )


async def cmd_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        return
    if context.args:
        await _store_token(update, context, context.args[0])
    else:
        context.user_data["awaiting"] = "token"
        await update.message.reply_text("Paste the JWT / token string.")


async def _store_token(update: Update, context: ContextTypes.DEFAULT_TYPE, token: str) -> None:
    store.state["manual_token"] = token.strip()
    await store.save_state()
    await update.message.reply_text("✅ Token stored.", reply_markup=ui.main_menu(True))


async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        return
    if not context.args:
        context.user_data["awaiting"] = "delete"
        return await update.message.reply_text("Send the UID to delete.")
    await _delete(update, context, context.args[0])


async def _delete(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: str) -> None:
    runtime.stop(str(uid))
    removed = await store.remove(uid)
    await update.message.reply_text(
        f"{'✅ Removed' if removed else '⚠️ Not found'} `{uid}`",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=ui.main_menu(True),
    )


async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update) or not context.args:
        return
    uid = context.args[0]
    account = store.get(uid)
    if not account:
        return await update.message.reply_text(
            f"⚠️ `{uid}` not found", parse_mode=ParseMode.MARKDOWN
        )
    paused = not account.get("paused")
    await store.set_paused(uid, paused)
    await update.message.reply_text(
        f"{'⏸ Paused' if paused else '▶️ Resumed'} `{uid}`",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=ui.main_menu(True),
    )


async def cmd_pauseall(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        return
    target = not all(a.get("paused") for a in store.accounts)
    for account in store.accounts:
        account["paused"] = target
    await store.save_accounts()
    await update.message.reply_text(
        f"{'⏸ Paused all' if target else '▶️ Resumed all'}", reply_markup=ui.main_menu(True)
    )


async def cmd_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update) or not context.args:
        return
    uid = str(context.args[0])
    if not store.get(uid):
        return await update.message.reply_text(
            f"⚠️ `{uid}` not found", parse_mode=ParseMode.MARKDOWN
        )
    await runtime.restart(uid)
    guild = store.guild()
    await game.refresh_guild_cache(guild.get("id", ""), guild.get("region", ""))
    await update.message.reply_text(
        f"🔄 Refreshed `{uid}`", parse_mode=ParseMode.MARKDOWN, reply_markup=ui.main_menu(True)
    )


async def cmd_restart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update) or not context.args:
        return
    uid = str(context.args[0])
    await runtime.restart(uid)
    await update.message.reply_text(
        f"♻️ Restarted `{uid}`", parse_mode=ParseMode.MARKDOWN, reply_markup=ui.main_menu(True)
    )


async def cmd_clanjoin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        return
    if not context.args:
        context.user_data["awaiting"] = "clanjoin"
        return await update.message.reply_text("Send the Guild / Clan ID.")
    await _set_guild(update, context, context.args[0], config.DEFAULT_REGION)


async def cmd_guild(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        return
    if len(context.args) < 2:
        context.user_data["awaiting"] = "guild"
        return await update.message.reply_text("Send `GUILD_ID REGION`.")
    await _set_guild(update, context, context.args[0], context.args[1])


async def _set_guild(update: Update, context: ContextTypes.DEFAULT_TYPE,
                     guild_id: str, region: str) -> None:
    guild_id = str(guild_id).strip()
    if not guild_id.isdigit() or not 6 <= len(guild_id) <= 12:
        return await update.message.reply_text(
            f"⚠️ `{guild_id}` is not a Guild ID (expect 9-10 digits).",
            parse_mode=ParseMode.MARKDOWN, reply_markup=ui.back(),
        )

    region = str(region).strip().upper() or config.DEFAULT_REGION
    if region not in config.REGION_SERVERS:
        return await update.message.reply_text(
            f"⚠️ Unknown region `{region}`.\nValid: `{', '.join(sorted(config.REGION_SERVERS))}`",
            parse_mode=ParseMode.MARKDOWN, reply_markup=ui.back(),
        )

    guild = store.guild()
    guild["id"] = guild_id
    guild["region"] = region
    for account in store.accounts:
        if account.get("role") == config.OFFICER_ROLE:
            account["joined_guilds"] = [guild_id]
        else:
            account["joined_guilds"] = []
    await store.save_accounts()
    await store.save_state()

    log_buffer.log("INFO", f"guild target set to {guild_id} ({region})")
    await update.message.reply_text(
        f"🏰 Guild `{guild_id}` / `{region}` saved.\n"
        f"All {len(store.accounts)} account(s) will re-send join requests on their next cycle.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=ui.main_menu(True),
    )


async def cmd_accept(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        return
    result = await acceptor.cycle()
    if result.get("skipped"):
        return await update.message.reply_text(
            "⚠️ No officer account found. Mark the in-guild guest with `role: officer` "
            "in accounts.json and keep it un-paused.",
            parse_mode=ParseMode.MARKDOWN, reply_markup=ui.back(),
        )
    if result.get("error"):
        return await update.message.reply_text(f"⚠️ {result['error']}", reply_markup=ui.back())
    await update.message.reply_text(
        f"✅ Pending `{result.get('pending', 0)}` | accepted now `{result.get('accepted', 0)}`",
        parse_mode=ParseMode.MARKDOWN, reply_markup=ui.main_menu(True),
    )


# ------------------------------------------------------------- text / files
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()
    current = flow(context)

    if current.get("step") == "accounts":
        pairs, bad = parse_pairs(text)
        if not pairs:
            pairs = parse_guest_blob(text)
        if not pairs:
            return await update.message.reply_text(
                "⚠️ No valid `UID:PASSWORD` rows found. Try again or `/cancel`.",
                parse_mode=ParseMode.MARKDOWN,
            )
        return await _accept_pairs(update, context, pairs, bad)

    if current.get("step") == "guild_id":
        if not text.isdigit():
            return await update.message.reply_text("⚠️ Guild ID must be numeric.")
        return await _launch_run(update, context, text, config.DEFAULT_REGION)

    awaiting = context.user_data.pop("awaiting", None)
    if awaiting == "add":
        pairs, _ = parse_pairs(text)
        if pairs:
            for uid, password in pairs:
                await store.add(uid, password)
            runtime.spawn(pairs[0][0])
            return await update.message.reply_text(
                f"✅ Added {len(pairs)} account(s).", reply_markup=ui.main_menu(True)
            )
        parts = text.split()
        if len(parts) >= 2:
            return await _add_and_report(update, parts[0], parts[1])
        return await update.message.reply_text("⚠️ Use `UID PASSWORD`.")
    if awaiting == "token":
        return await _store_token(update, context, text)
    if awaiting == "delete":
        return await _delete(update, context, text)
    if awaiting == "clanjoin":
        return await _set_guild(update, context, text, config.DEFAULT_REGION)
    if awaiting == "guild":
        parts = text.split()
        if len(parts) >= 2:
            return await _set_guild(update, context, parts[0], parts[1])
        return await update.message.reply_text("⚠️ Use `GUILD_ID REGION`.")

    if not is_admin(update):
        flow(context).update({"step": "accounts", "pairs": []})
        return await on_text(update, context)


async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    doc = update.message.document
    if doc is None:
        return
    if doc.file_size and doc.file_size > 512_000:
        return await update.message.reply_text("⚠️ File too large (max 512 KB).")

    file = await doc.get_file()
    raw = bytes(await file.download_as_bytearray())
    text = raw.decode("utf-8", "ignore")

    pairs, bad = parse_pairs(text)
    if not pairs:
        pairs = parse_guest_blob(text)
    if not pairs:
        return await update.message.reply_text("⚠️ No valid rows in that file.")

    if flow(context).get("step") == "accounts":
        return await _accept_pairs(update, context, pairs, bad)

    if not is_admin(update):
        flow(context).update({"step": "accounts"})
        return await _accept_pairs(update, context, pairs, bad)

    for uid, password in pairs:
        await store.add(uid, password)
    await update.message.reply_text(
        f"✅ Imported {len(pairs)} account(s).", reply_markup=ui.main_menu(True)
    )


async def _accept_pairs(update: Update, context: ContextTypes.DEFAULT_TYPE,
                        pairs: list[tuple[str, str]], bad: list[str]) -> None:
    if len(pairs) > config.MAX_WIZARD_ACCOUNTS:
        pairs = pairs[: config.MAX_WIZARD_ACCOUNTS]
    current = flow(context)
    current["pairs"] = pairs
    current["step"] = "guild_id"
    note = f"\n⚠️ Skipped {len(bad)} malformed row(s)." if bad else ""
    await update.message.reply_text(
        f"✅ Accepted `{len(pairs)}` unique account(s).{note}\n\nNow send the numeric Guild ID.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=ui.back(),
    )


async def _launch_run(update: Update, context: ContextTypes.DEFAULT_TYPE,
                      guild_id: str, region: str) -> None:
    pairs = flow(context).get("pairs", [])
    context.user_data.pop("flow", None)
    if not pairs:
        return await update.message.reply_text("⚠️ No accounts buffered.")

    run = JoinRun(pairs, guild_id, region)
    runtime.run = run
    message = await update.message.reply_text(
        ui.progress_text(run), parse_mode=ParseMode.MARKDOWN
    )
    task = asyncio.create_task(run.execute())
    asyncio.create_task(
        _watch_run(context.application, message.chat_id, message.message_id, run, task)
    )


async def _watch_run(app: Application, chat_id: int, message_id: int, run: JoinRun, task) -> None:
    while not run.stats.done:
        await asyncio.sleep(2.5)
        try:
            await app.bot.edit_message_text(
                ui.progress_text(run), chat_id=chat_id, message_id=message_id,
                parse_mode=ParseMode.MARKDOWN,
            )
        except BadRequest:
            pass
    try:
        await task
    except Exception as exc:  # noqa: BLE001
        log_buffer.log("ERROR", f"run failed: {exc}")
    try:
        await app.bot.edit_message_text(
            ui.summary_text(run), chat_id=chat_id, message_id=message_id,
            parse_mode=ParseMode.MARKDOWN,
        )
    except BadRequest:
        pass


# ------------------------------------------------------------------ callbacks
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data
    admin = ui.is_admin(query.from_user.id)

    async def show(text: str, markup=None) -> None:
        try:
            await query.edit_message_text(
                text, parse_mode=ParseMode.MARKDOWN,
                reply_markup=markup or ui.main_menu(admin),
            )
        except BadRequest:
            pass

    if data == "menu:home":
        return await show(f"*{config.BOT_NAME}*\n\nMain menu.", ui.main_menu(admin))
    if data == "wiz:start":
        flow(context).update({"step": "accounts", "pairs": []})
        return await show(
            f"*Auto guild join*\n\nSend a `.txt` file or paste `UID:PASSWORD` lines.\n"
            f"Max {config.MAX_WIZARD_ACCOUNTS} accounts.",
            ui.back(),
        )
    if not admin and not data.startswith("menu:help"):
        return await show("Admin only.", ui.main_menu(admin))

    if data == "menu:dash":
        return await show(ui.dashboard_text(), ui.back())
    if data.startswith("menu:accts"):
        page = int(data.split(":")[2]) if data.count(":") > 1 else 0
        return await show(ui.accounts_text(page), ui.back())
    if data == "menu:logs":
        return await show(ui.logs_text(), ui.back())
    if data == "menu:help":
        return await show(ui.help_text(admin), ui.back())
    if data == "menu:ginfo":
        return await show(ui.guild_text(), ui.back())
    if data == "menu:accept":
        result = await acceptor.cycle()
        return await show(
            f"✅ Pending `{result.get('pending', 0)}` | "
            f"accepted now `{result.get('accepted', 0)}`",
            ui.back(),
        )
    if data == "menu:add":
        context.user_data["awaiting"] = "add"
        return await show("Send `UID PASSWORD`, or upload a guest `.txt`.", ui.back())
    if data == "menu:token":
        context.user_data["awaiting"] = "token"
        return await show("Paste the token string.", ui.back())
    if data in ("menu:delete", "menu:del"):
        context.user_data["awaiting"] = "delete"
        return await show("Send the UID to delete.", ui.back())
    if data == "menu:clanjoin":
        context.user_data["awaiting"] = "clanjoin"
        return await show("Send the Guild / Clan ID.", ui.back())
    if data == "menu:pause":
        rows = [
            ui._row((("▶️ " if a.get("paused") else "⏸ ") + str(a["uid"]), f"tog:{a['uid']}"))
            for a in store.accounts[:12]
        ]
        return await show(
            "*Pause / resume*\nTap a UID to toggle.",
            InlineKeyboardMarkup(rows + [ui._row(("Back", "menu:home"))]),
        )
    if data == "menu:refresh":
        guild = store.guild()
        await game.refresh_guild_cache(guild.get("id", ""), guild.get("region", ""))
        return await show("🔄 Guild cache refreshed.\n\n" + ui.guild_text(), ui.back())
    if data == "menu:restart":
        for uid in store.uids():
            await runtime.restart(uid)
        return await show("♻️ All workers restarted.", ui.back())
    if data == "menu:settings":
        guild = store.guild()
        return await show(
            "*Settings*\n\n"
            f"Guild: `{guild.get('id') or 'not set'}` / `{guild.get('region') or '-'}`\n"
            f"Join interval: `{config.JOIN_INTERVAL}s`\n"
            f"Refresh interval: `{config.REFRESH_INTERVAL}s`\n"
            f"Concurrency: `{config.ACCOUNT_CONCURRENCY}`\n"
            f"Max wizard accounts: `{config.MAX_WIZARD_ACCOUNTS}`\n"
            f"Auto accept: `{config.AUTO_ACCEPT}` every `{config.ACCEPT_INTERVAL}s`\n"
            f"Release: `{config.RELEASE_VERSION}`\n"
            f"MajorLogin: `{config.MAJOR_LOGIN_URL}`",
            ui.back(),
        )
    if data.startswith("tog:"):
        uid = data.split(":", 1)[1]
        account = store.get(uid)
        if account:
            await store.set_paused(uid, not account.get("paused"))
        return await show(f"Toggled `{uid}`.", ui.back())


# ---------------------------------------------------------------------- main
async def post_init(app: Application) -> None:
    await runtime.start(app)
    me = await app.bot.get_me()
    guild = store.guild()
    log_buffer.log("INFO", f"{config.BOT_NAME} online as @{me.username}")
    log_buffer.log(
        "INFO",
        f"target guild {guild.get('id')} ({guild.get('region')}) - auto-join armed",
    )


async def post_shutdown(app: Application) -> None:
    await runtime.shutdown()


def build_app() -> Application:
    if not config.BOT_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set")
    if not config.ADMIN_IDS:
        log.warning("TELEGRAM_ADMIN_IDS is empty - nobody can use admin controls")

    app = (
        ApplicationBuilder()
        .token(config.BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("autojoin", cmd_autojoin))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("accounts", cmd_accounts))
    app.add_handler(CommandHandler("logs", cmd_logs))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("token", cmd_token))
    app.add_handler(CommandHandler("delete", cmd_delete))
    app.add_handler(CommandHandler("pause", cmd_pause))
    app.add_handler(CommandHandler("pauseall", cmd_pauseall))
    app.add_handler(CommandHandler("refresh", cmd_refresh))
    app.add_handler(CommandHandler("restart", cmd_restart))
    app.add_handler(CommandHandler("clanjoin", cmd_clanjoin))
    app.add_handler(CommandHandler("guild", cmd_guild))
    app.add_handler(CommandHandler("accept", cmd_accept))

    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return app


def main() -> None:
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    build_app().run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
