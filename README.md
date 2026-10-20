# VOID X BOT

Telegram control panel that mass-joins Free Fire guest accounts into a guild, with
an in-guild officer account auto-accepting the incoming requests.

Guild target is pre-armed: `3038929979` on region `IND`.

## What it does

- Mints a real Free Fire game JWT per account (guest OAuth -> AES MajorLogin).
- Sends an encrypted `GuildJoin` to the regional game server.
- Runs a background keepalive loop per account so sessions stay warm.
- Uses an in-guild Officer account to poll `GetGuildRequests` and accept applicants.
- Live dashboard, logs, per-account pause/resume, all from Telegram.

## Files

| File | Role |
| --- | --- |
| `app.py` | Telegram bot, handlers, inline dashboard |
| `config.py` | every tunable / env var |
| `store.py` | atomic JSON persistence + live log ring buffer |
| `devices.py` | device fingerprint pool (anti-duplicate UA) |
| `proto.py` | dependency-free protobuf codec |
| `garena.py` | guest OAuth + AES-CBC MajorLogin -> JWT |
| `game.py` | encrypted guild join / requests / accept / pulse |
| `worker.py` | join batch engine, per-account loop, acceptor |
| `ui.py` | keyboards and text builders |
| `bootstrap.py` | seeds JSON files into a Railway volume on first boot |
| `selftest.py` | pre-flight login + guild check, no Telegram needed |

## Local pre-flight

    pip install -r requirements.txt
    python selftest.py

Steps 1 and 2 must pass. Step 3 failing is usually just a proto path name and is
fixed with an env var, not code.

## Deploy on Railway

1. Push this folder to a GitHub repo.
2. Railway -> New project -> Deploy from GitHub repo.
3. Add a Volume, mount path `/data`. Without it accounts reset on every redeploy.
4. Set the variables from `.env.example`, at minimum:

       TELEGRAM_BOT_TOKEN
       TELEGRAM_ADMIN_IDS
       DATA_DIR=/data
       DEFAULT_REGION=IND
       DEFAULT_GUILD_ID=3038929979
       DEFAULT_GUILD_REGION=IND
       AUTO_ACCEPT=1

5. Deploy. Expected log lines:

       [bootstrap] seeded /data/devices.json
       VOID X online as @yourbot
       target guild 3038929979 (IND) - auto-join armed

## Before first run

Promote the guest account `8283599493` to **Officer** inside the guild.
A plain Member cannot accept join requests.

## Telegram commands

`/start` `/cancel` `/autojoin` `/status` `/accounts` `/logs`
`/add UID PASSWORD` `/token TOKEN` `/delete UID`
`/pause UID` `/pauseall` `/refresh UID` `/restart UID`
`/clanjoin GUILD_ID` `/guild GUILD_ID REGION` `/accept`

Non-admin users only get the Auto Guild Join wizard.

## If guild calls 404

Garena's guild proto path names are not documented and shift between OB patches.
Override them in env without touching code:

    GUILD_JOIN_PATH=GuildJoin
    GUILD_REQUESTS_PATH=GetGuildRequests
    GUILD_ACCEPT_PATH=AcceptGuildRequest
    GUILD_ID_FIELD=1
    GUILD_ID_AS_STRING=0
    ACCEPT_INCLUDE_GUILD=1

Candidates to try for a 404 on join: `GuildJoinReq`, `GuildJoinRequest`, `JoinGuild`.
For requests: `GuildGetRequestList`, `GetGuildRequestList`, `GuildRequests`.

## Known limits

- Free Fire guilds hold 50 members. Beyond that, accounts sit pending.
- `HTTP 200` means the server accepted the packet, not that the bot is a member.
  Only the officer accept step finishes the job.
- Keep the officer account un-paused. `/pauseall` pauses it too.
