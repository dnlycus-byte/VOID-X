"""Device fingerprint pool. One stable, unique device per account UID."""
from __future__ import annotations

import hashlib
import json
import re

import config

_ANDROID_BUILDS = {
    11: "RP1A.200720.011",
    12: "SP1A.210812.016",
    13: "TP1A.220905.001",
    14: "UKQ1.230924.001",
    15: "AP3A.240905.015.A2",
}

_FALLBACK = {
    "brand": "OPPO",
    "model": "CPH2611",
    "gpu_renderer": "Adreno (TM) 720",
    "system_software": "Android OS 14 / API-34",
    "screen_width": 1080,
    "screen_height": 2400,
    "screen_dpi": "417",
    "memory": 4096,
    "processor_details": "ARM64 FP ASIMD AES | 2600 | 8",
    "unique_device_id": "Google|00000000-0000-4000-8000-000000000000",
    "client_ip": "0.0.0.0",
}


class DevicePool:
    def __init__(self) -> None:
        self._by_key: dict[str, dict] = {}
        self._list: list[dict] = []
        self.reload()

    def reload(self) -> None:
        self._by_key.clear()
        self._list.clear()
        try:
            with config.DEVICES_FILE.open("r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, json.JSONDecodeError):
            raw = {}
        if isinstance(raw, dict):
            for key, value in raw.items():
                if isinstance(value, dict):
                    self._by_key[str(key)] = value
                    self._list.append(value)
        elif isinstance(raw, list):
            for value in raw:
                if isinstance(value, dict):
                    self._list.append(value)

    @property
    def size(self) -> int:
        return len(self._list)

    def for_uid(self, uid: str) -> dict:
        """Exact key match first (devices.json is keyed by id), else stable hash."""
        if uid in self._by_key:
            return self._by_key[uid]
        if not self._list:
            return dict(_FALLBACK)
        digest = hashlib.sha256(str(uid).encode()).digest()
        return self._list[int.from_bytes(digest[:4], "big") % len(self._list)]

    @staticmethod
    def android_version(device: dict) -> int:
        match = re.search(r"Android\s*(?:OS\s*)?(\d+)", device.get("system_software", ""))
        return int(match.group(1)) if match else 13

    @classmethod
    def user_agent(cls, device: dict) -> str:
        """Dalvik UA the Free Fire client sends on MajorLogin."""
        version = cls.android_version(device)
        build = _ANDROID_BUILDS.get(version, _ANDROID_BUILDS[13])
        model = device.get("model", "CPH2611")
        return f"Dalvik/2.1.0 (Linux; U; Android {version}; {model} Build/{build})"

    @staticmethod
    def summary(device: dict) -> str:
        return (
            f"{device.get('brand', '?')} {device.get('model', '?')} | "
            f"{device.get('system_software', '?')} | {device.get('gpu_renderer', '?')}"
        )


devices = DevicePool()
