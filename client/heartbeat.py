from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from shared.pdus import create_ping

if TYPE_CHECKING:
    from client.client import GameClient


class HeartbeatManager:
    """manages PINGPONG heartbeat"""
    def __init__(self, client: GameClient) -> None:
        self.client = client
        self._ping_seq: int = 0
        self._last_ping_time: float | None = None
        self._pong_received = asyncio.Event()
        self._pong_received.set()

    async def run(self) -> None:
        """run heartbeat loop"""
        while self.client.state not in ("DISCONNECTED", "GAME_OVER"):
            await asyncio.sleep(30)

            if self.client.state == "DISCONNECTED":
                break

            #increment counter
            self._ping_seq += 1
            timestamp_ms = int(time.time() * 1000)

            ping_pdu = create_ping(seq_num=self._ping_seq, timestamp=timestamp_ms)
            await self.client.outgoing_queue.put(ping_pdu)

            self._last_ping_time = time.monotonic()
            self._pong_received.clear()

            #wait for PONG for 10 seconds, else dc
            try:
                await asyncio.wait_for(self._pong_received.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                print("Server unreachable (PONG timeout). Disconnecting.")
                self.client.state = "DISCONNECTED"
                return

    def on_pong(self, pdu: dict) -> None:
        """Handle a PONG PDU"""
        self._pong_received.set()
