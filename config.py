"""VOID X BOT - central configuration. Every tunable lives here."""
from __future__ import annotations

import base64
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _admin_ids() -> set[int]:
    ids: set[int] = set()
    for chunk in os.getenv("TELEGRAM_ADMIN_IDS", "").replace(" ", "").split(","):
        if chunk:
            try:
                ids.add(int(chunk))
            except ValueError:
                pass
    return ids


BOT_NAME = os.getenv("BOT_NAME", "VOID X")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
ADMIN_IDS = _admin_ids()

DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR))
DATA_DIR.mkdir(parents=True, exist_ok=True)

ACCOUNTS_FILE = DATA_DIR / "accounts.json"
DEVICES_FILE = DATA_DIR / "devices.json"
TOKEN_CACHE_FILE = DATA_DIR / "token_cache.json"
GUILD_EXP_CACHE_FILE = DATA_DIR / "guild_exp_cache.json"
STATE_FILE = DATA_DIR / "state.json"

# ---------- Garena / Free Fire ----------
CLIENT_ID = os.getenv("GARENA_CLIENT_ID", "100067")
CLIENT_SECRET = os.getenv(
    "GARENA_CLIENT_SECRET",
    "2ee44819e9b4598845141067b281621874d0d5d7af9d8f7e00c1e54715b7d1e3",
)
OAUTH_URL = os.getenv(
    "GARENA_OAUTH_URL", "https://100067.connect.garena.com/oauth/guest/token/grant"
)
TOKEN_INSPECT_URL = os.getenv(
    "GARENA_INSPECT_URL", "https://100067.connect.garena.com/oauth/token/inspect"
)
MAJOR_LOGIN_URL = os.getenv(
    "MAJOR_LOGIN_URL", "https://loginbp.ppmainecoonghj.com/MajorLogin"
)
MAIN_KEY = base64.b64decode(os.getenv("MAIN_KEY_B64", "WWcmdGMlREV1aDYlWmNeOA=="))
MAIN_IV = base64.b64decode(os.getenv("MAIN_IV_B64", "Nm95WkRyMjJFM3ljaGpNJQ=="))
RELEASE_VERSION = os.getenv("RELEASE_VERSION", "OB55")
X_UNITY_VERSION = os.getenv("X_UNITY_VERSION", "2018.4.12f1")
X_GA_SV = os.getenv("X_GA_SV", "1790603268")
MSDK_UA = os.getenv(
    "MSDK_UA",
    "GarenaMSDK/4.0.44(SM-G998B ;Android 9;en;US;app 1.132.1 2019121229;)",
)

REGION_SERVERS = {
    "IND": "https://client.ind.freefiremobile.com",
    "BD": "https://client.bd.freefiremobile.com",
    "PK": "https://client.pk.freefiremobile.com",
    "ID": "https://client.id.freefiremobile.com",
    "SG": "https://client.sg.freefiremobile.com",
    "TH": "https://client.th.freefiremobile.com",
    "VN": "https://client.vn.freefiremobile.com",
    "BR": "https://client.br.freefiremobile.com",
    "US": "https://client.us.freefiremobile.com",
    "ME": "https://client.me.freefiremobile.com",
    "RU": "https://client.ru.freefiremobile.com",
    "CIS": "https://client.cis.freefiremobile.com",
}
DEFAULT_REGION = os.getenv("DEFAULT_REGION", "IND").upper()

# ---------- guild target (pre-seeded) ----------
DEFAULT_GUILD_ID = os.getenv("DEFAULT_GUILD_ID", "3038929979")
DEFAULT_GUILD_REGION = os.getenv("DEFAULT_GUILD_REGION", DEFAULT_REGION).upper()

# ---------- game proto paths (env-overridable across OB patches) ----------
GUILD_JOIN_PATH = os.getenv("GUILD_JOIN_PATH", "GuildJoin")
GUILD_INFO_PATH = os.getenv("GUILD_INFO_PATH", "GetGuildInfo")
GUILD_ID_FIELD = _int("GUILD_ID_FIELD", 1)
GUILD_ID_AS_STRING = os.getenv("GUILD_ID_AS_STRING", "0") == "1"
PULSE_PATH = os.getenv("PULSE_PATH", "GetLoginData")

# ---------- auto accept (runs through the in-guild officer account) ----------
GUILD_REQUESTS_PATH = os.getenv("GUILD_REQUESTS_PATH", "GetGuildRequests")
GUILD_ACCEPT_PATH = os.getenv("GUILD_ACCEPT_PATH", "AcceptGuildRequest")
ACCEPT_INCLUDE_GUILD = os.getenv("ACCEPT_INCLUDE_GUILD", "1") == "1"
ACCEPT_INTERVAL = _int("ACCEPT_INTERVAL", 120)
AUTO_ACCEPT = os.getenv("AUTO_ACCEPT", "1") == "1"
OFFICER_ROLE = os.getenv("OFFICER_ROLE", "officer")

# ---------- behaviour ----------
JOIN_INTERVAL = _float("JOIN_INTERVAL", 3.0)
REFRESH_INTERVAL = _int("REFRESH_INTERVAL", 300)
KEEPALIVE_INTERVAL = _int("KEEPALIVE_INTERVAL", 600)
MAX_WIZARD_ACCOUNTS = _int("MAX_WIZARD_ACCOUNTS", 100)
TOKEN_TTL = _int("TOKEN_TTL", 21600)
HTTP_TIMEOUT = _float("HTTP_TIMEOUT", 25.0)
LOG_BUFFER = _int("LOG_BUFFER", 500)
ACCOUNT_CONCURRENCY = _int("ACCOUNT_CONCURRENCY", 5)
RETRIES = _int("RETRIES", 2)
