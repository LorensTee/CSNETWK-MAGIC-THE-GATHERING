"""
server/config.py — Server Configuration (Module 02: Server Engine)

Dataclass holding all configurable server parameters.  Populated from
command-line arguments in *main.py*.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ServerConfig:
    """Immutable server configuration.

    Parameters
    ----------
    host :
        IP address to bind (default ``"0.0.0.0"``).
    port :
        TCP port to listen on (default ``4444``).
    verbose :
        Enable verbose PDU/state logging to stderr.
    time_limit_ms :
        Default time limit for a PRIORITY_GRANT in milliseconds.
    disconnect_timeout_s :
        Seconds before a disconnected player is considered gone and
        ``GAME_OVER(DISCONNECT)`` is broadcast.
    """

    host: str = "0.0.0.0"
    port: int = 4444
    verbose: bool = False
    time_limit_ms: int = 3_600_000
    disconnect_timeout_s: int = 6000
