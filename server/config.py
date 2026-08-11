from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ServerConfig:
    """
    port 4444
    """

    host: str = "0.0.0.0"
    port: int = 4444
    verbose: bool = False
    time_limit_ms: int = 3_600_000
    disconnect_timeout_s: int = 10
