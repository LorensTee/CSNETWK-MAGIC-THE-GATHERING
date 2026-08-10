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
    def __init__(self, config: ClientConfig) -> None:
        self.config = config
        self.connection: ClientConnection | None = None
        self.state: str = "DISCONNECTED"
        self.visible_state: dict[str, Any] = {}
        self.outgoing_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.deck_list: list[str] = []
        self._current_priority_seq: int = 0
        self._render_event: asyncio.Event = asyncio.Event()
        self.dispatcher: ClientDispatcher | None = None
        self.renderer: Renderer | None = None
        self.heartbeat: HeartbeatManager | None = None
        self.input_handler: InputHandler | None = None

    async def run(self) -> None:
        # load the deck from file if provided
        if self.config.deck_file:
            self._load_deck_file(self.config.deck_file)

        # try to connect to server
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

        #create the dispatcher, renderer, heartbeat manager, and input handler
        self.dispatcher = ClientDispatcher(self)
        self.renderer = Renderer(self)
        self.heartbeat = HeartbeatManager(self)
        self.input_handler = InputHandler(self)

        log_connection(self.config.host, self.config.port)
        self.state = "LOBBY"
        # initialize deck
        my_deck = (
            [f"mountain_{i:03d}" for i in range(1, 11)] +
            [f"forest_{i:03d}" for i in range(1, 11)] +
            [f"goblin_guide_{i:03d}" for i in range(1, 5)] +
            [f"grizzly_bears_{i:03d}" for i in range(1, 5)] +
            [f"giant_growth_{i:03d}" for i in range(1, 5)] +
            [f"lightning_bolt_{i:03d}" for i in range(1, 5)]
        )

        #send player ready PDU to server
        ready_pdu = {
            "type": "PLAYER_READY",
            "seq_num": self.connection.client_seq_num + 1,
            "player_id": self.config.player_id,
            "deck_list": my_deck 
        }
        if self.connection:
            self.connection.client_seq_num += 1
        await self.connection.send_pdu(ready_pdu)

        #start the concurrent tasks
        tasks = [
            asyncio.create_task(self._read_loop()),
            asyncio.create_task(self._write_loop()),
            asyncio.create_task(self._render_loop()),
            asyncio.create_task(self._heartbeat_loop()),
        ]
        input_task = asyncio.create_task(self._input_loop())
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for t in tasks:
                t.cancel()
        finally:
            input_task.cancel()
            await self._cleanup()

    #receive PDU from the server and dispatches it
    async def _read_loop(self) -> None:
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

            if self.dispatcher is not None:
                await self.dispatcher.dispatch(pdu)

    #send PDU to server
    async def _write_loop(self) -> None:
        while self.state != "DISCONNECTED":
            try:
                pdu = await self.outgoing_queue.get()
                if self.connection is not None:
                    await self.connection.send_pdu(pdu)
            except (ConnectionError, OSError) as exc:
                print(f"\nSend failed: {exc}")
                self.state = "DISCONNECTED"
                break

    #periodically redraw
    async def _render_loop(self) -> None:
        renderer = self.renderer
        if renderer is None:
            return
        # initially trigger a render
        self._render_event.set()
        while self.state != "DISCONNECTED":
            await self._render_event.wait()
            self._render_event.clear()
            renderer.draw(self.state, self.visible_state)

    # PING PONG - ping 
    async def _heartbeat_loop(self) -> None:
        if self.heartbeat is not None:
            await self.heartbeat.run()

    #read player input, send it to the server
    async def _input_loop(self) -> None:
        if self.input_handler is not None:
            await self.input_handler.run()

    #for user created deck file, load the deck list from the file
    def _load_deck_file(self, path: str) -> None:
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    self.deck_list.append(line)
            print(f"Loaded {len(self.deck_list)} cards from '{path}'.")
        except(FileNotFoundError, OSError) as exc:
            print(f"Error reading deck file '{path}': {exc}")
            print("Falling back to interactive deck entry.")
            self.deck_list = []

    # cleanup
    async def _cleanup(self) -> None:
        if self.connection is not None:
            await self.connection.close()
            self.connection = None
