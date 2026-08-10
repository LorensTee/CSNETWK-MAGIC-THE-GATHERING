from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ClientConfig:
    host: str = "127.0.0.1"
    port: int = 4444
    player_id: str = ""
    deck_file: str | None = None
    verbose: bool = False
