"""Minimal protobuf codec - enough for Free Fire login + guild packets.

No compiled .pb2, no protoc, no protobuf runtime. Field numbers come from the
FreeFire.proto LoginReq/LoginRes schema.
"""
from __future__ import annotations

from typing import Any

WIRE_VARINT, WIRE_FIXED64, WIRE_BYTES, WIRE_FIXED32 = 0, 1, 2, 5

# LoginReq field numbers
REQ_OPEN_ID = 22
REQ_OPEN_ID_TYPE = 23
REQ_LOGIN_TOKEN = 29
REQ_ORIGN_PLATFORM = 99

# LoginRes field numbers
RES_ACCOUNT_ID = 1
RES_LOCK_REGION = 2
RES_NOTI_REGION = 3
RES_IP_REGION = 4
RES_TOKEN = 8
RES_TTL = 9
RES_SERVER_URL = 10


def _varint(value: int) -> bytes:
    if value < 0:
        value += 1 << 64
    out = bytearray()
    while True:
        chunk = value & 0x7F
        value >>= 7
        if value:
            out.append(chunk | 0x80)
        else:
            out.append(chunk)
            return bytes(out)


def _key(field: int, wire: int) -> bytes:
    return _varint((field << 3) | wire)


def put_bytes(field: int, value: bytes | str) -> bytes:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return _key(field, WIRE_BYTES) + _varint(len(raw)) + raw


def put_int(field: int, value: int) -> bytes:
    return _key(field, WIRE_VARINT) + _varint(value)


def _read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    shift = 0
    value = 0
    while pos < len(buf) and shift <= 70:
        byte = buf[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, pos
        shift += 7
    raise ValueError("truncated varint")


def parse(buf: bytes) -> list[tuple[int, int, Any]]:
    fields: list[tuple[int, int, Any]] = []
    pos = 0
    while pos < len(buf):
        key, pos = _read_varint(buf, pos)
        field, wire = key >> 3, key & 7
        if field == 0:
            raise ValueError("bad field 0")
        if wire == WIRE_VARINT:
            value, pos = _read_varint(buf, pos)
        elif wire == WIRE_BYTES:
            size, pos = _read_varint(buf, pos)
            if pos + size > len(buf):
                raise ValueError("truncated bytes")
            value = buf[pos:pos + size]
            pos += size
        elif wire == WIRE_FIXED64:
            value = buf[pos:pos + 8]
            pos += 8
        elif wire == WIRE_FIXED32:
            value = buf[pos:pos + 4]
            pos += 4
        else:
            raise ValueError(f"unsupported wire type {wire}")
        fields.append((field, wire, value))
    return fields


def build_login_request(open_id: str, login_token: str) -> bytes:
    """LoginReq{open_id=22, open_id_type=23, login_token=29, orign_platform_type=99}."""
    return (
        put_bytes(REQ_OPEN_ID, open_id)
        + put_bytes(REQ_OPEN_ID_TYPE, "4")
        + put_bytes(REQ_LOGIN_TOKEN, login_token)
        + put_bytes(REQ_ORIGN_PLATFORM, "4")
    )


def _decode_res(buf: bytes) -> dict | None:
    try:
        fields = parse(buf)
    except ValueError:
        return None

    out: dict[str, Any] = {}
    blobs: list[bytes] = []
    for field, wire, value in fields:
        if wire != WIRE_BYTES:
            if field == RES_ACCOUNT_ID and wire == WIRE_VARINT:
                out["account_id"] = str(value)
            continue
        if len(value) == 16:
            blobs.append(value)
        if field == RES_TOKEN:
            out["token"] = value.decode("utf-8", "ignore")
        elif field == RES_SERVER_URL:
            out["server_url"] = value.decode("utf-8", "ignore")
        elif field == RES_LOCK_REGION:
            out["lock_region"] = value.decode("utf-8", "ignore")
        elif field == RES_NOTI_REGION:
            out["noti_region"] = value.decode("utf-8", "ignore")
        elif field == RES_IP_REGION:
            out["ip_region"] = value.decode("utf-8", "ignore")

    if not out.get("token"):
        return None
    # Heuristic: first two 16-byte blobs are the session AES key + IV.
    if len(blobs) >= 2:
        out["aes_key"], out["aes_iv"] = blobs[0], blobs[1]
    return out


def parse_login_response_any(buf: bytes) -> dict | None:
    """LoginRes sometimes arrives behind a short transport prefix - scan for it."""
    for offset in range(0, min(64, len(buf))):
        decoded = _decode_res(buf[offset:])
        if decoded and decoded.get("token"):
            return decoded
    return None


def build_guild_packet(guild_id: str, field: int = 1, as_string: bool = False) -> bytes:
    """GuildJoinReq{guild_id} - field/type are env-tunable across OB patches."""
    if as_string:
        return put_bytes(field, str(guild_id))
    return put_int(field, int(guild_id))


def build_accept_packet(applicant_uid: str, guild_id: str, include_guild: bool = False,
                        field: int = 1) -> bytes:
    """AcceptGuildRequest{applicant_account_id[, guild_id]} - env-tunable layout."""
    payload = put_int(field, int(applicant_uid))
    if include_guild:
        payload += put_int(field + 1, int(guild_id))
    return payload


def find_ints(buf: bytes) -> dict[int, int]:
    """Flat int fields of an arbitrary response - used for exp/level readout."""
    out: dict[int, int] = {}
    try:
        for field, wire, value in parse(buf):
            if wire == WIRE_VARINT:
                out.setdefault(field, int(value))
    except ValueError:
        pass
    return out


def extract_uids(buf: bytes, exclude: frozenset[str] = frozenset(), depth: int = 0) -> list[str]:
    """Dig plausible account IDs out of an arbitrary guild response."""
    found: list[str] = []
    if depth > 3:
        return found
    try:
        fields = parse(buf)
    except ValueError:
        return found

    for _field, wire, value in fields:
        if wire == WIRE_VARINT:
            text = str(value)
            if 6 <= len(text) <= 12 and text not in found and text not in exclude:
                found.append(text)
        elif wire == WIRE_BYTES:
            text = value.decode("utf-8", "ignore")
            if text.isdigit() and 6 <= len(text) <= 12:
                if text not in found and text not in exclude:
                    found.append(text)
            else:
                for sub in extract_uids(value, exclude, depth + 1):
                    if sub not in found:
                        found.append(sub)
    return found
