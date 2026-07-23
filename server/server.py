"""
server/server.py — Game Server (Module 02: Server Engine)

Top-level server orchestration.  Creates the listening socket, accepts
exactly two client connections, wraps each in a ``ServerConnection``,
connects the dispatcher, and hands off to ``GameLifecycle``.
"""

from __future__ import annotations

import asyncio
from typing import Any

from server.card_loader import CardLoader
from server.config import ServerConfig
from server.connection import ServerConnection
from server.dispatcher import dispatch
from server.game_lifecycle import GameLifecycle


class GameServer:
    """The MTGNP game server.

    Parameters
    ----------
    config :
        Server configuration.
    """

    def __init__(self, config: ServerConfig) -> None:
        self.config = config

        # Load card data.
        self.card_loader = CardLoader()
        self.card_loader.load()

        # Active connections (max 2).
        self._connections: list[ServerConnection] = []

        # Queue for the accept loop.
        self._incoming: asyncio.Queue[ServerConnection] = asyncio.Queue()
        self._server_instance: asyncio.AbstractServer | None = None

    async def run(self) -> None:
        """Start the server and run game sessions indefinitely."""
        self._server_instance = await asyncio.start_server(
            self._on_client_connected,
            host=self.config.host,
            port=self.config.port,
        )

        addr = self._server_instance.sockets[0].getsockname()
        print(f"MTGNP Server listening on {addr[0]}:{addr[1]}")

        async with self._server_instance:
            await self._game_loop()

    async def _game_loop(self) -> None:
        """Accept pairs of connections and run game sessions."""
        while True:
            self._connections.clear()
            self._incoming = asyncio.Queue()

            # Accept exactly two connections.
            while len(self._connections) < 2:
                conn = await self._incoming.get()
                self._connections.append(conn)
                conn.player_id = f"player_{len(self._connections)}"  # temp
                peername = conn.writer.get_extra_info("peername")
                print(f"Player {len(self._connections)} connected ({peername})")

            # Create lifecycle and wire dispatcher.
            lifecycle = GameLifecycle(self.config, self.card_loader, self._connections)
            for conn in self._connections:
                conn.on_pdu = lambda c, pdu, lc=lifecycle: dispatch(lc, c, pdu)

            # Run the game.
            await lifecycle.run()

            # Reset for next game.
            for conn in self._connections:
                conn.seq_num = 0
                conn.player_id = None

    async def _on_client_connected(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Callback for each new TCP connection.

        Accepts up to 2 connections; refuses extras.
        """
        if len(self._connections) >= 2:
            # Already have two players — refuse.
            if self.config.verbose:
                import sys
                print(
                    f"[CONN] Refusing extra connection from "
                    f"{writer.get_extra_info('peername')}",
                    file=sys.stderr,
                )
            writer.close()
            return

        conn = ServerConnection(
            reader, writer,
            on_pdu=None,  # Set by _game_loop after lifecycle is created.
            verbose=self.config.verbose,
        )
        await self._incoming.put(conn)
