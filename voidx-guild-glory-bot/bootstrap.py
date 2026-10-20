"""Copy seed files into DATA_DIR on first boot (Railway volume support)."""
from __future__ import annotations

import shutil

import config

SEEDS = (
    "devices.json",
    "accounts.json",
    "token_cache.json",
    "guild_exp_cache.json",
    "state.json",
)


def run() -> None:
    if config.DATA_DIR == config.BASE_DIR:
        return
    for name in SEEDS:
        source = config.BASE_DIR / name
        target = config.DATA_DIR / name
        if source.exists() and not target.exists():
            shutil.copy2(source, target)
            print(f"[bootstrap] seeded {target}")


if __name__ == "__main__":
    run()
