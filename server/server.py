from __future__ import annotations

import asyncio
import json
import struct
from typing import Any

from server.card_loader import CardLoader
from server.config import ServerConfig
from server.connection import ServerConnection
from server.dispatcher import dispatch
from server.game_lifecycle import GameLifecycle


class GameServer:
    def __init__(self, config: ServerConfig) -> None:
        self.config = config

        # load card data
        self.card_loader = CardLoader()
        self.card_loader.load()

        # max 2 active connections
        self._connections: list[ServerConnection] = []

        # queue for accept loop
        self._incoming: asyncio.Queue[ServerConnection] = asyncio.Queue()
        self._server_instance: asyncio.AbstractServer | None = None

    async def run(self) -> None:
        """start the server and run game sessions infinitely"""
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
        """accept pairs of connections and run game session"""
        while True:
            self._connections.clear()
            self._incoming = asyncio.Queue()

            # 2 CONNECTIONS ONLY
            while len(self._connections) < 2:
                conn = await self._incoming.get()
                self._connections.append(conn)
                conn.player_id = f"player_{len(self._connections)}"  # temp
                peername = conn.writer.get_extra_info("peername")
                print(f"Player {len(self._connections)} connected ({peername})")

            # lifecycle
            lifecycle = GameLifecycle(self.config, self.card_loader, self._connections)
            for conn in self._connections:
                conn.on_pdu = lambda c, pdu, lc=lifecycle: dispatch(lc, c, pdu)

            async def connection_manager():
                import time, json, struct
                disconnect_times = {}
                print("[WATCHDOG] ONLINE AND SWEEPING!")
                
                while not lifecycle._game_over.is_set():
                    try:
                        # sweep for timeouts
                        for i, c in enumerate(self._connections):
                            if getattr(c, '_closed', False):
                                if c not in disconnect_times:
                                    print(f"[WATCHDOG] Detected {c.player_id} crash! Starting {self.config.disconnect_timeout_s}s timer...")
                                    disconnect_times[c] = time.time()
                                elif time.time() - disconnect_times[c] > self.config.disconnect_timeout_s:
                                    print(f"[WATCHDOG] {c.player_id} timed out! Nuking game.")
                                    winner_conn = self._connections[1 - i]
                                    
                                    if not getattr(winner_conn, '_closed', False):
                                        go_pdu = {
                                            "type": "GAME_OVER",
                                            "seq_num": winner_conn.seq_num,
                                            "winner_id": winner_conn.player_id,
                                            "loser_id": c.player_id,
                                            "reason": "DISCONNECT"
                                        }
                                        payload = json.dumps(go_pdu).encode('utf-8')
                                        winner_conn.writer.write(struct.pack('>I', len(payload)) + payload)
                                        winner_conn.writer.close()
                                    
                                    lifecycle._game_over.set()
                                    return
                        
                        # process incoming connections
                        try:
                            new_conn = await asyncio.wait_for(self._incoming.get(), timeout=1.0)
                        except (asyncio.TimeoutError, TimeoutError):
                            continue # loop again
                            
                        # find empty seat
                        for i, old_conn in enumerate(self._connections):
                            if getattr(old_conn, '_closed', False):
                                if old_conn in disconnect_times and (time.time() - disconnect_times[old_conn] > self.config.disconnect_timeout_s):
                                    new_conn.writer.close()
                                    break
                                
                                print(f"[RECONNECT] {old_conn.player_id} rejoined the game!")
                                new_conn.player_id = old_conn.player_id
                                new_conn.seq_num = old_conn.seq_num
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

            # START WATCHDOG FIRST
            conn_manager_task = asyncio.create_task(connection_manager())

            # RUN THE GAME
            await lifecycle.run()

            # CLEAN UP
            conn_manager_task.cancel()

            # reset for next game
            for conn in self._connections:
                conn.seq_num = 0
                conn.player_id = None

    async def _on_client_connected(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """callback for each new TCP connection"""
        active_count = sum(1 for c in self._connections if not c._closed)

        if active_count >= 2:
            # refuse if more than 2
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
            on_pdu=None,
            verbose=self.config.verbose,
        )
        await self._incoming.put(conn)
