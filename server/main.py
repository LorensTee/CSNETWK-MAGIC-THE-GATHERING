"""
server/main.py — Server Entry Point (Module 02: Server Engine)

Parses command-line arguments and starts the ``GameServer`` event loop.
"""

import argparse
import asyncio

from server.config import ServerConfig
from server.server import GameServer


def main() -> None:
    parser = argparse.ArgumentParser(
        description="MTGNP 1.0 Game Server — Magic: The Gathering "
                    "Multiplayer Network Protocol",
    )
    parser.add_argument(
        "--host", default="0.0.0.0",
        help="IP address to bind (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port", type=int, default=4444,
        help="TCP port to listen on (default: 4444)",
    )
    parser.add_argument(
        "--verbose", action="store_true", default=False,
        help="Enable verbose PDU/state logging to stderr",
    )
    parser.add_argument(
        "--time-limit-ms", type=int, default=60000,
        help="Default priority time limit in milliseconds (default: 60000)",
    )
    parser.add_argument(
        "--disconnect-timeout-s", type=int, default=10,
        help="Seconds before a disconnected player is considered gone "
             "(default: 10)",
    )

    args = parser.parse_args()

    config = ServerConfig(
        host=args.host,
        port=args.port,
        verbose=args.verbose,
        time_limit_ms=args.time_limit_ms,
        disconnect_timeout_s=args.disconnect_timeout_s,
    )

    asyncio.run(GameServer(config).run())


if __name__ == "__main__":
    main()
