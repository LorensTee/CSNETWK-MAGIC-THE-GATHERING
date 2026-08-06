"""
client/client.py — Game Client State Machine (Module 03: Client App)

The central client class that manages the connection, state, and all
concurrent tasks (read, write, render, heartbeat, input).
"""

from __future__ import annotations

import asyncio
import sys
from .connection import connect
from typing import Any

from client.config import ClientConfig
from client.connection import ClientConnection
from client.dispatcher import ClientDispatcher
from client.heartbeat import HeartbeatManager
from client.input_handler import InputHandler
from client.renderer import Renderer
from client.verbose import (
    log_connection,
    log_pdu_received,
    log_pdu_sent,
    log_state_transition,
)
from shared.framing import ProtocolError
from shared.pdus import create_player_ready, create_ping


class GameClient:
    """The MTGNP game client.

    Connects to the server, manages the client lifecycle state, and runs
    five concurrent tasks:
    1. *_read_loop* — receive PDUs from the server.
    2. *_write_loop* — send PDUs to the server.
    3. *_render_loop* — periodically redraw the terminal.
    4. *_heartbeat_loop* — send PING every 30 s.
    5. *_input_loop* — read player commands.

    Parameters
    ----------
    config :
        Client configuration.
    """

    def __init__(self, config: ClientConfig) -> None:
        self.config = config
        self.connection: ClientConnection | None = None
        self.state: str = "DISCONNECTED"
        self.visible_state: dict[str, Any] = {}
        self.outgoing_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.deck_list: list[str] = []

        # The seq_num from the most recent PRIORITY_GRANT (for echo).
        self._current_priority_seq: int = 0
        # Event-driven re-render signal.
        self._render_event: asyncio.Event = asyncio.Event()

        # Sub-components (created in run()).
        self.dispatcher: ClientDispatcher | None = None
        self.renderer: Renderer | None = None
        self.heartbeat: HeartbeatManager | None = None
        self.input_handler: InputHandler | None = None

    # ═══════════════════════════════════════════════════════════════════════════
    # Public entry point
    # ═══════════════════════════════════════════════════════════════════════════

    async def run(self) -> None:
        """Connect to the server and start all concurrent tasks."""
        # 1. Load deck from file if provided.
        if self.config.deck_file:
            self._load_deck_file(self.config.deck_file)

        # 2. Connect.
        self.state = "CONNECTING"
        try:
            self.connection = await connect(
                host=self.config.host,
                port=self.config.port,
                verbose=self.config.verbose,
            )
        except (ConnectionError, OSError) as exc:
            print(f"Failed to connect to {self.config.host}:{self.config.port}: {exc}")
            self.state = "DISCONNECTED"
            return

        # 3. Create sub-components.
        self.dispatcher = ClientDispatcher(self)
        self.renderer = Renderer(self)
        self.heartbeat = HeartbeatManager(self)
        self.input_handler = InputHandler(self)

        log_connection(self.config.host, self.config.port)
        self.state = "LOBBY"

        my_deck = (
            [f"forest_{i:03d}" for i in range(1, 15)] +          # 14 Forests
            [f"mountain_{i:03d}" for i in range(1, 9)] +         # 8 Mountains
            [f"lightning_bolt_{i:03d}" for i in range(1, 5)] +   # 4 Instants
            [f"grizzly_bears_{i:03d}" for i in range(1, 5)] +    # 4 Bears
            [f"lava_spike_{i:03d}" for i in range(1, 5)]         # 4 Sorceries
        )

        ready_pdu = {
            "type": "PLAYER_READY",
            "seq_num": 1, 
            "player_id": self.config.player_id,
            "deck_list": my_deck 
        }
        await self.connection.send_pdu(ready_pdu)

        # 4. Run concurrent tasks (use gather for Python 3.10 compatibility).
        tasks = [
            asyncio.create_task(self._read_loop()),
            asyncio.create_task(self._write_loop()),
            asyncio.create_task(self._render_loop()),
            asyncio.create_task(self._heartbeat_loop()),
            asyncio.create_task(self._input_loop()),
        ]
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for t in tasks:
                t.cancel()
        finally:
            await self._cleanup()

    # ═══════════════════════════════════════════════════════════════════════════
    # Async loops
    # ═══════════════════════════════════════════════════════════════════════════

    async def _read_loop(self) -> None:
        """Read PDUs from the server and dispatch them."""
        conn = self.connection
        if conn is None:
            return
        while self.state != "DISCONNECTED":
            try:
                pdu = await conn.recv_pdu()
            except (ConnectionError, EOFError, OSError, ProtocolError) as exc:
                print(f"\nDisconnected from server: {exc}")
                self.state = "DISCONNECTED"
                break

            # conn.recv_pdu() already logs in verbose mode.
            if self.dispatcher is not None:
                await self.dispatcher.dispatch(pdu)

    async def _write_loop(self) -> None:
        """Drain the outgoing queue and send PDUs."""
        while self.state != "DISCONNECTED":
            try:
                pdu = await self.outgoing_queue.get()
                if self.connection is not None:
                    await self.connection.send_pdu(pdu)
                    # connection.send_pdu() already logs in verbose mode.
            except (ConnectionError, OSError) as exc:
                print(f"\nSend failed: {exc}")
                self.state = "DISCONNECTED"
                break

    async def _render_loop(self) -> None:
        """Wait for render events and redraw the screen."""
        renderer = self.renderer
        if renderer is None:
            return
        # Trigger an initial draw.
        self._render_event.set()
        while self.state != "DISCONNECTED":
            await self._render_event.wait()
            self._render_event.clear()
            renderer.draw(self.state, self.visible_state)

    async def _heartbeat_loop(self) -> None:
        """Run the PING/PONG heartbeat."""
        if self.heartbeat is not None:
            await self.heartbeat.run()

    async def _input_loop(self) -> None:
        """Run the input handler."""
        if self.input_handler is not None:
            await self.input_handler.run()

    # ═══════════════════════════════════════════════════════════════════════════
    # Deck file loading
    # ═══════════════════════════════════════════════════════════════════════════

    def _load_deck_file(self, path: str) -> None:
        """Read a deck file: one card ID per line, skip ``#`` comments."""
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    self.deck_list.append(line)
            print(f"Loaded {len(self.deck_list)} cards from '{path}'.")
        except (FileNotFoundError, OSError) as exc:
            print(f"Error reading deck file '{path}': {exc}")
            print("Falling back to interactive deck entry.")
            self.deck_list = []

    # ═══════════════════════════════════════════════════════════════════════════
    # Cleanup
    # ═══════════════════════════════════════════════════════════════════════════

    async def _cleanup(self) -> None:
        """Close the connection gracefully."""
        if self.connection is not None:
            await self.connection.close()
            self.connection = None
