"""
client/config.py — Client Configuration (Module 03: Client App)

Dataclass holding all configurable client parameters.  Populated from
command-line arguments in *main.py*.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ClientConfig:
    """Client configuration.

    Parameters
    ----------
    host :
        Server hostname or IP address (default ``127.0.0.1``).
    port :
        Server TCP port (default ``4444``).
    player_id :
        Unique player identifier (required).
    deck_file :
        Path to a text file with one card ID per line.
    verbose :
        Enable verbose PDU logging to stderr.
    """

    host: str = "127.0.0.1"
    port: int = 4444
    player_id: str = ""
    deck_file: str | None = None
    verbose: bool = False
