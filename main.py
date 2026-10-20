"""VOID X - Guild Glory Bot (standalone, no Telegram)"""
import asyncio
import json
import logging
import os
import time
from pathlib import Path

import httpx
from Crypto.Cipher import AES

log = logging.getLogger("voidx")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-5s  %(message)s",
    datefmt="%H:%M:%S",
)

# ── Config ────────────────────────────────────────────────────────────────────
GUILD_ID         = os.getenv("GUILD_ID",          "3038929979")
GUILD_REGION     = os.getenv("GUILD_REGION",       "IND")
OFFICER_UID      = os.getenv("OFFICER_UID",        "8283599493")
OFFICER_PASS     = os.getenv("OFFICER_PASS",       "2C7E2BBE47382BE4FD95B4B41C4661A4FE24671EB1866083877078DB74440750")
ACCEPT_INTERVAL  = int(os.getenv("ACCEPT_INTERVAL",  "120"))
JOIN_INTERVAL    = float(os.getenv("JOIN_INTERVAL",   "5"))
TOKEN_TTL        = int(os.getenv("TOKEN_TTL",         "21600"))
HTTP_TIMEOUT     = float(os.getenv("HTTP_TIMEOUT",    "25"))
RETRIES          = int(os.getenv("RETRIES",           "2"))

CLIENT_ID     = os.getenv("GARENA_CLIENT_ID",     "100067")
CLIENT_SECRET = os.getenv("GARENA_CLIENT_SECRET", "2ee44819e9b4598845141067b281621874d0d5d7af9d8f7e00c1e54715b7d1e3")
OAUTH_URL     = os.getenv("GARENA_OAUTH_URL",     "https://100067.connect.garena.com/oauth/guest/token/grant")
LOGIN_URL     = os.getenv("MAJOR_LOGIN_URL",      "https://loginbp.ppmainecoonghj.com/MajorLogin")
RELEASE       = os.getenv("RELEASE_VERSION",      "OB55")
UNITY_VER     = os.getenv("X_UNITY_VERSION",      "2018.4.12f1")

REGION_SERVERS = {
    "IND": "https://client.ind.freefiremobile.com",
    "BD":  "https://client.bd.freefiremobile.com",
    "PK":  "https://client.pk.freefiremobile.com",
    "SG":  "https://client.sg.freefiremobile.com",
    "ID":  "https://client.id.freefiremobile.com",
    "TH":  "https://client.th.freefiremobile.com",
    "VN":  "https://client.vn.freefiremobile.com",
    "BR":  "https://client.br.freefiremobile.com",
    "US":  "https://client.us.freefiremobile.com",
}

MAIN_KEY = bytes.fromhex("596727746325444575683625" + "5a635e38")[:16]
MAIN_IV  = bytes.fromhex("366f795a4472323245337963" + "686a4d25")[:16]

MSDK_UA = "GarenaMSDK/4.0.44(SM-G998B ;Android 9;en;US;app 1.132.1 2019121229;)"

# ── Protobuf helpers ──────────────────────────────────────────────────────────
def _varint(n: int) -> bytes:
    out = []
    while n > 0x7F:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    out.append(n)
    return bytes(out)

def _field(num: int, wire: int, data: bytes) -> bytes:
    return _varint((num << 3) | wire) + data

def _str_field(num: int, s: str) -> bytes:
    enc = s.encode()
    return _field(num, 2, _varint(len(enc)) + enc)

def _int_field(num: int, n: int) -> bytes:
    return _field(num, 0, _varint(n))

def build_login_proto(open_id: str, access_token: str) -> bytes:
    return _str_field(1, open_id) + _str_field(2, access_token) + _int_field(6, int(CLIENT_ID))

def build_guild_packet(guild_id: str) -> bytes:
    try:
        return _int_field(1, int(guild_id))
    except ValueError:
        return _str_field(1, guild_id)

def build_accept_packet(applicant_uid: str, guild_id: str) -> bytes:
    return _int_field(1, int(applicant_uid)) + _int_field(2, int(guild_id))

def parse_login_response(raw: bytes) -> dict:
    """Minimal proto parser for the login response."""
    result: dict = {}
    i = 0
    while i < len(raw):
        if i >= len(raw):
            break
        try:
            tag_byte = raw[i]; i += 1
            field_num = tag_byte >> 3
            wire_type = tag_byte & 0x07
            if wire_type == 0:  # varint
                val = 0; shift = 0
                while True:
                    b = raw[i]; i += 1
                    val |= (b & 0x7F) << shift
                    shift += 7
                    if not (b & 0x80):
                        break
                result[field_num] = val
            elif wire_type == 2:  # length-delimited
                length = 0; shift = 0
                while True:
                    b = raw[i]; i += 1
                    length |= (b & 0x7F) << shift
                    shift += 7
                    if not (b & 0x80):
                        break
                data = raw[i:i + length]; i += length
                try:
                    result[field_num] = data.decode("utf-8", errors="replace")
                except Exception:
                    result[field_num] = data
            else:
                break
        except (IndexError, KeyError):
            break
    return result

def extract_uids(raw: bytes, exclude: set) -> list:
    """Extract numeric UIDs from a proto response."""
    uids = []
    i = 0
    while i < len(raw):
        try:
            tag_byte = raw[i]; i += 1
            wire_type = tag_byte & 0x07
            if wire_type == 0:
                val = 0; shift = 0
                while True:
                    b = raw[i]; i += 1
                    val |= (b & 0x7F) << shift
                    shift += 7
                    if not (b & 0x80):
                        break
                s = str(val)
                if len(s) >= 6 and s not in exclude:
                    uids.append(s)
            elif wire_type == 2:
                length = 0; shift = 0
                while True:
                    b = raw[i]; i += 1
                    length |= (b & 0x7F) << shift
                    shift += 7
                    if not (b & 0x80):
                        break
                i += length
            else:
                break
        except (IndexError, KeyError):
            break
    return list(dict.fromkeys(uids))

# ── Crypto helpers ────────────────────────────────────────────────────────────
def _pkcs7(data: bytes) -> bytes:
    pad = 16 - (len(data) % 16)
    return data + bytes([pad]) * pad

def _encrypt(data: bytes, key: bytes = MAIN_KEY, iv: bytes = MAIN_IV) -> bytes:
    return AES.new(key, AES.MODE_CBC, iv).encrypt(_pkcs7(data))

# ── Token cache ───────────────────────────────────────────────────────────────
_tokens: dict[str, dict] = {}

async def login(uid: str, password: str) -> dict:
    """Guest OAuth → MajorLogin → returns session dict with token + server_url."""
    cached = _tokens.get(uid)
    if cached and cached.get("expires_at", 0) > time.time() + 60:
        return cached

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, http2=False) as client:
        # Step 1: Guest OAuth
        resp = await client.post(OAUTH_URL, json={
            "client_id": int(CLIENT_ID),
            "client_secret": CLIENT_SECRET,
            "client_type": 2,
            "password": str(password),
            "response_type": "token",
            "uid": int(uid),
        }, headers={
            "User-Agent": MSDK_UA,
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
        })
        resp.raise_for_status()
        body = resp.json()
        data = body.get("data") if isinstance(body.get("data"), dict) else body
        access_token = data["access_token"]
        open_id = str(data["open_id"])

        # Step 2: MajorLogin
        payload = _encrypt(build_login_proto(open_id, access_token))
        resp2 = await client.post(LOGIN_URL, content=payload, headers={
            "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 13; SM-G998B Build/TP1A.220624.014)",
            "Connection": "Keep-Alive",
            "Accept-Encoding": "gzip",
            "Content-Type": "application/octet-stream",
            "X-Unity-Version": UNITY_VER,
            "X-GA": "v1 1",
            "ReleaseVersion": RELEASE,
        })
        resp2.raise_for_status()
        parsed = parse_login_response(resp2.content)

    token = parsed.get(1) or parsed.get(3)
    if not token:
        raise RuntimeError(f"[{uid}] No token in login response — banned or wrong creds")

    region = (parsed.get(11) or parsed.get(14) or GUILD_REGION or "IND").upper()
    server_url = parsed.get(12) or REGION_SERVERS.get(region, REGION_SERVERS["IND"])

    session = {
        "uid": uid,
        "token": token,
        "region": region,
        "server_url": str(server_url).rstrip("/"),
        "expires_at": int(time.time()) + TOKEN_TTL,
    }
    _tokens[uid] = session
    log.info(f"[{uid}] login OK  region={region}")
    return session

# ── Game calls ────────────────────────────────────────────────────────────────
def _game_headers(token: str) -> dict:
    return {
        "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 13; SM-G998B Build/TP1A.220624.014)",
        "Connection": "Keep-Alive",
        "Accept-Encoding": "gzip",
        "Content-Type": "application/octet-stream",
        "X-Unity-Version": UNITY_VER,
        "X-GA": "v1 1",
        "ReleaseVersion": RELEASE,
        "Authorization": f"Bearer {token}",
    }

async def guild_join(session: dict, guild_id: str) -> int:
    payload = _encrypt(build_guild_packet(guild_id))
    url = f"{session['server_url']}/GuildJoin"
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.post(url, content=payload, headers=_game_headers(session["token"]))
    log.info(f"[{session['uid']}] GuildJoin → HTTP {resp.status_code}")
    return resp.status_code

async def get_pending(session: dict, guild_id: str) -> list:
    payload = _encrypt(build_guild_packet(guild_id))
    url = f"{session['server_url']}/GetGuildRequests"
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.post(url, content=payload, headers=_game_headers(session["token"]))
    if resp.status_code != 200:
        raise RuntimeError(f"GetGuildRequests HTTP {resp.status_code}")
    return extract_uids(resp.content, {guild_id, str(session["uid"])})

async def accept_one(session: dict, applicant: str, guild_id: str) -> bool:
    payload = _encrypt(build_accept_packet(applicant, guild_id))
    url = f"{session['server_url']}/AcceptGuildRequest"
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.post(url, content=payload, headers=_game_headers(session["token"]))
    return resp.status_code == 200

# ── Officer accept loop ───────────────────────────────────────────────────────
accepted_set: set = set()

async def officer_loop():
    log.info(f"[officer] loop started — accept every {ACCEPT_INTERVAL}s")
    while True:
        try:
            sess = await login(OFFICER_UID, OFFICER_PASS)
            pending = await get_pending(sess, GUILD_ID)
            new_pending = [u for u in pending if u not in accepted_set]
            log.info(f"[officer] pending={len(pending)}  new={len(new_pending)}")
            for uid in new_pending:
                ok = await accept_one(sess, uid, GUILD_ID)
                if ok:
                    accepted_set.add(uid)
                    log.info(f"[officer] ✅ accepted {uid}")
                await asyncio.sleep(1)
        except Exception as e:
            log.error(f"[officer] error: {e}")
        await asyncio.sleep(ACCEPT_INTERVAL)

# ── Guest join loop ───────────────────────────────────────────────────────────
async def join_account(uid: str, password: str):
    for attempt in range(RETRIES + 1):
        try:
            sess = await login(uid, password)
            status = await guild_join(sess, GUILD_ID)
            if status == 200:
                log.info(f"[{uid}] ✅ join request sent")
            else:
                log.warning(f"[{uid}] join HTTP {status}")
            return
        except Exception as e:
            log.warning(f"[{uid}] attempt {attempt+1} failed: {e}")
            if attempt < RETRIES:
                await asyncio.sleep(5)

async def guest_join_loop(accounts: list):
    guests = [a for a in accounts if a.get("role") != "officer"]
    if not guests:
        log.info("[join] no guest accounts — add UIDs to accounts.json to start joining")
        return
    log.info(f"[join] starting with {len(guests)} guest account(s)")
    while True:
        for acc in guests:
            await join_account(acc["uid"], acc["password"])
            await asyncio.sleep(JOIN_INTERVAL)
        log.info("[join] cycle done — sleeping 5 minutes")
        await asyncio.sleep(300)

# ── Main ──────────────────────────────────────────────────────────────────────
async def main():
    log.info("=" * 50)
    log.info("  VOID X Guild Glory Bot  — STARTING")
    log.info(f"  Guild  : {GUILD_ID} ({GUILD_REGION})")
    log.info(f"  Officer: {OFFICER_UID}")
    log.info("=" * 50)

    # Load extra guest accounts from accounts.json if present
    accounts_file = Path(os.getenv("DATA_DIR", ".")) / "accounts.json"
    accounts: list = []
    if accounts_file.exists():
        try:
            accounts = json.loads(accounts_file.read_text())
            log.info(f"Loaded {len(accounts)} account(s) from accounts.json")
        except Exception as e:
            log.warning(f"Could not read accounts.json: {e}")

    # Always include officer
    if not any(str(a.get("uid")) == OFFICER_UID for a in accounts):
        accounts.append({"uid": OFFICER_UID, "password": OFFICER_PASS, "role": "officer"})

    # Run both loops concurrently
    await asyncio.gather(
        officer_loop(),
        guest_join_loop(accounts),
    )

if __name__ == "__main__":
    asyncio.run(main())
