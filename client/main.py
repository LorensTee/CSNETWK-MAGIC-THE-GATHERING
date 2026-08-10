import argparse
import asyncio

from client.client import GameClient
from client.config import ClientConfig


def main() -> None:
    parser = argparse.ArgumentParser(
        description="MTGNP 1.0 Player Client — Magic: The Gathering "
                    "Multiplayer Network Protocol",
    )
    parser.add_argument(
        "--host", default="127.0.0.1",
        help="Server hostname or IP (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port", type=int, default=4444,
        help="Server TCP port (default: 4444)",
    )
    parser.add_argument(
        "--player-id", required=True,
        help="Unique player identifier (required)",
    )
    parser.add_argument(
        "--deck-file", default=None,
        help="Path to deck file (one card ID per line, '#' for comments)",
    )
    parser.add_argument(
        "--verbose", action="store_true", default=False,
        help="Enable verbose PDU logging to stderr",
    )

    args = parser.parse_args()

    config = ClientConfig(
        host=args.host,
        port=args.port,
        player_id=args.player_id,
        deck_file=args.deck_file,
        verbose=args.verbose,
    )

    asyncio.run(GameClient(config).run())


if __name__ == "__main__":
    main()
