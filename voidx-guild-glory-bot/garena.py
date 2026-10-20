"""Garena authentication: guest OAuth -> AES-CBC MajorLogin -> game JWT."""
from __future__ import annotations

import base64
import time

import httpx
from Crypto.Cipher import AES

import config
import proto
from store import log_buffer, store


class AuthError(RuntimeError):
    pass


def _pkcs7(data: bytes) -> bytes:
    pad = 16 - (len(data) % 16)
    return data + bytes([pad]) * pad


def _encrypt(data: bytes, key: bytes, iv: bytes) -> bytes:
    return AES.new(key, AES.MODE_CBC, iv).encrypt(_pkcs7(data))


def _b64(value: bytes | None) -> str:
    return base64.b64encode(value).decode() if value else ""


def _unb64(value: str | None) -> bytes | None:
    try:
        return base64.b64decode(value) if value else None
    except (ValueError, TypeError):
        return None


async def guest_token(client: httpx.AsyncClient, uid: str, password: str) -> tuple[str, str]:
    """Exchange guest UID/password for a Garena OAuth access_token + open_id."""
    payload = {
        "client_id": int(config.CLIENT_ID),
        "client_secret": config.CLIENT_SECRET,
        "client_type": 2,
        "password": str(password),
        "response_type": "token",
        "uid": int(uid),
    }
    headers = {
        "User-Agent": config.MSDK_UA,
        "Accept": "application/json",
        "Content-Type": "application/json; charset=utf-8",
        "Connection": "Keep-Alive",
    }
    try:
        resp = await client.post(config.OAUTH_URL, json=payload, headers=headers)
        resp.raise_for_status()
        body = resp.json()
    except httpx.HTTPError as exc:
        raise AuthError(f"oauth network: {exc}") from exc
    except ValueError as exc:
        raise AuthError("oauth returned non-JSON") from exc

    data = body.get("data") if isinstance(body.get("data"), dict) else body
    if data.get("error"):
        raise AuthError(str(data.get("error_description") or data["error"]))

    token = data.get("access_token")
    open_id = data.get("open_id")
    if not token or not open_id:
        raise AuthError("oauth response incomplete")
    return str(token), str(open_id)


async def major_login(
    client: httpx.AsyncClient, open_id: str, access_token: str, user_agent: str
) -> dict:
    """Encrypted MajorLogin -> account_id, game JWT, region server URL."""
    body = _encrypt(
        proto.build_login_request(open_id, access_token), config.MAIN_KEY, config.MAIN_IV
    )
    headers = {
        "User-Agent": user_agent,
        "Connection": "Keep-Alive",
        "Accept-Encoding": "gzip",
        "Content-Type": "application/octet-stream",
        "Expect": "100-continue",
        "X-Unity-Version": config.X_UNITY_VERSION,
        "X-GA": "v1 1",
        "ReleaseVersion": config.RELEASE_VERSION,
        "X-GA-SV": config.X_GA_SV,
    }
    try:
        resp = await client.post(config.MAJOR_LOGIN_URL, content=body, headers=headers)
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise AuthError(f"majorlogin HTTP {exc.response.status_code}") from exc
    except httpx.HTTPError as exc:
        raise AuthError(f"majorlogin network: {exc}") from exc

    decoded = proto.parse_login_response_any(resp.content)
    if not decoded or not decoded.get("token"):
        raise AuthError("majorlogin gave no token (banned / unregistered / bad creds)")
    return decoded


async def generate_jwt(uid: str, password: str, device: dict, *, force: bool = False) -> dict:
    """Full login chain with token cache. Returns a token-cache entry."""
    uid = str(uid)
    cached = store.tokens.get(uid)
    if cached and not force and cached.get("expires_at", 0) > time.time() + 60:
        return cached

    from devices import devices

    user_agent = devices.user_agent(device)
    async with httpx.AsyncClient(timeout=config.HTTP_TIMEOUT, http2=False) as client:
        access_token, open_id = await guest_token(client, uid, password)
        data = await major_login(client, open_id, access_token, user_agent)

    region = (
        data.get("lock_region")
        or data.get("noti_region")
        or data.get("ip_region")
        or config.DEFAULT_REGION
    ).upper()
    server_url = data.get("server_url") or config.REGION_SERVERS.get(region, "")

    entry = {
        "token": data["token"],
        "account_id": data.get("account_id", uid),
        "region": region,
        "server_url": server_url,
        "aes_key": _b64(data.get("aes_key")),
        "aes_iv": _b64(data.get("aes_iv")),
        "device_ua": user_agent,
        "created_at": int(time.time()),
        "expires_at": int(time.time()) + config.TOKEN_TTL,
    }

    store.tokens[uid] = entry
    await store.save_tokens()
    log_buffer.log("INFO", f"[{uid}] JWT ok region={region}")
    return entry


def session_keys(entry: dict) -> tuple[bytes, bytes]:
    """Per-session AES keys from LoginRes, falling back to the static client pair."""
    key = _unb64(entry.get("aes_key")) or config.MAIN_KEY
    iv = _unb64(entry.get("aes_iv")) or config.MAIN_IV
    if len(key) != 16:
        key = config.MAIN_KEY
    if len(iv) != 16:
        iv = config.MAIN_IV
    return key, iv
