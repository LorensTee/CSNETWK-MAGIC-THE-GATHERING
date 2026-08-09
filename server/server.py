"""
server/server.py — Game Server (Module 02: Server Engine)

Top-level server orchestration.  Creates the listening socket, accepts
exactly two client connections, wraps each in a ``ServerConnection``,
connects the dispatcher, and hands off to ``GameLifecycle``.
"""

from __future__ import annotations

import asyncio
import time
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

    async def _watchdog_sweep(
        self,
        lifecycle: GameLifecycle,
        disconnect_times: dict,
    ) -> bool:
        """One disconnect-timeout sweep pass.

        If a closed connection has exceeded *disconnect_timeout_s*,
        broadcast GAME_OVER(DISCONNECT) to the surviving player and
        return ``True``.  Routing through ``lifecycle._end_game`` keeps
        the GAME_OVER seq-numbered via ``send_pdu`` (RFC §10.2.22,
        verbose-mode rubric prerequisite), resets the ready-state for the
        next LOBBY, and signals the engine to unwind gracefully.  The
        winner's socket is NOT closed — RFC §6.6 retains connections
        after GAME_OVER for the next LOBBY.
        """
        for i, c in enumerate(self._connections):
            if getattr(c, '_closed', False):
                if c not in disconnect_times:
                    print(f"[WATCHDOG] Detected {c.player_id} crash! Starting {self.config.disconnect_timeout_s}s timer...")
                    disconnect_times[c] = time.time()
                elif time.time() - disconnect_times[c] > self.config.disconnect_timeout_s:
                    print(f"[WATCHDOG] {c.player_id} timed out! Nuking game.")
                    winner_conn = self._connections[1 - i]

                    if not getattr(winner_conn, '_closed', False):
                        await lifecycle._end_game(
                            lifecycle.gs, "DISCONNECT",
                            winner_conn.player_id or "",
                            c.player_id or "",
                        )
                    else:
                        # Both connections are dead.  Still route through
                        # _end_game (with an empty winner) so the
                        # ready-state reset runs: a bare _game_over.set()
                        # would leave stale players_ready/conn.player_id,
                        # making the next LOBBY skip the READY wait and
                        # then send to dead sockets.
                        await lifecycle._end_game(
                            lifecycle.gs, "DISCONNECT", "", c.player_id or "",
                        )
                    return True
        return False

    async def _game_loop(self) -> None:
        """Accept pairs of connections and run game sessions."""
        while True:
            self._connections.clear()
            # Close any connections left queued from the previous session
            # (the queue is replaced below — otherwise the queued sockets
            # would leak until the process exits).
            while not self._incoming.empty():
                try:
                    leaked = self._incoming.get_nowait()
                    leaked.writer.close()
                except asyncio.QueueEmpty:
                    break
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

            async def connection_manager():
                disconnect_times = {}
                print("[WATCHDOG] ONLINE AND SWEEPING!") # If you don't see this, the task is dead.
                
                while not lifecycle._game_over.is_set():
                    try:
                        # 1. Sweep for timeouts
                        if await self._watchdog_sweep(lifecycle, disconnect_times):
                            return
                        
                        # 2. Process incoming connections
                        try:
                            new_conn = await asyncio.wait_for(self._incoming.get(), timeout=1.0)
                        except (asyncio.TimeoutError, TimeoutError):
                            continue # Nothing came in, go loop again
                            
                        # Find the empty seat
                        for i, old_conn in enumerate(self._connections):
                            if getattr(old_conn, '_closed', False):
                                if old_conn in disconnect_times and (time.time() - disconnect_times[old_conn] > self.config.disconnect_timeout_s):
                                    new_conn.writer.close()
                                    break
                                
                                print(f"[RECONNECT] {old_conn.player_id} rejoined the game!")
                                new_conn.player_id = old_conn.player_id
                                new_conn.seq_num = old_conn.seq_num
                                # Carry the priority token too, so the
                                # dispatcher's stale pre-filter stays
                                # armed for the reconnected player.
                                new_conn.grant_token = old_conn.grant_token
                                new_conn.on_pdu = lambda c, pdu, lc=lifecycle: dispatch(lc, c, pdu)
                                
                                self._connections[i] = new_conn
                                if old_conn in disconnect_times:
                                    del disconnect_times[old_conn]
                                
                                asyncio.create_task(new_conn.read_loop())
                                if lifecycle.gs:
                                    await lifecycle._broadcast_game_state(lifecycle.gs)
                                break
                    except Exception as e:
                        print(f"[WATCHDOG CRASHED]: {e}")
                        await asyncio.sleep(1)

            # 1. START THE WATCHDOG FIRST
            conn_manager_task = asyncio.create_task(connection_manager())

            # 2. RUN THE GAME SECOND
            await lifecycle.run()

            # 3. CLEAN UP THIRD
            conn_manager_task.cancel()

            # Reset for next game.
            for conn in self._connections:
                conn.seq_num = 0
                conn.player_id = None
                conn.grant_token = None

    async def _on_client_connected(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Callback for each new TCP connection.

        Accepts up to 2 connections; refuses extras.
        """

        active_count = sum(1 for c in self._connections if not c._closed)

        if active_count >= 2:
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

        # Bound the pending queue: while the session waits for its two
        # players (or a reconnect slot), every new TCP connection lands
        # here.  Without a cap an attacker can exhaust fds/memory by
        # opening connections in a loop (each sits queued until the
        # session ends).  Two slots is enough: one reconnect candidate
        # plus slack.
        if self._incoming.qsize() >= 2:
            if self.config.verbose:
                print(
                    f"[CONN] Refusing queued connection from "
                    f"{writer.get_extra_info('peername')} "
                    f"(queue full)",
                    file=__import__("sys").stderr,
                )
            writer.close()
            return

        conn = ServerConnection(
            reader, writer,
            on_pdu=None,  # Set by _game_loop after lifecycle is created.
            verbose=self.config.verbose,
        )
        await self._incoming.put(conn)
