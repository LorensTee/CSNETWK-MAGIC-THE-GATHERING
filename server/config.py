from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 4444
    verbose: bool = False
    time_limit_ms: int = 60_000
    disconnect_timeout_s: int = 10
