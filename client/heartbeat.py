from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from shared.pdus import create_ping

if TYPE_CHECKING:
    from client.client import GameClient


class HeartbeatManager:
    # 30 secs between PINGs
    PING_INTERVAL_S = 30

    # 10 secs to wait for PONG reply
    PONG_TIMEOUT_S = 10

    def __init__(self, client: GameClient) -> None:
        self.client = client
        self._ping_seq: int = 0
        self._last_ping_time: float | None = None
        self._pong_received = asyncio.Event()
        self._pong_received.set()

    async def run(self) -> None:
        while self.client.state != "DISCONNECTED":
            await asyncio.sleep(self.PING_INTERVAL_S)

            if self.client.state == "DISCONNECTED":
                break

            # increment ping seq num and send PING to server
            self._ping_seq += 1
            timestamp_ms = int(time.time() * 1000)

            ping_pdu = create_ping(seq_num=self._ping_seq, timestamp=timestamp_ms)
            await self.client.outgoing_queue.put(ping_pdu)

            self._last_ping_time = time.monotonic()
            self._pong_received.clear()

            # wait for PONG 10 secs, else disconnect if timeout
            try:
                await asyncio.wait_for(
                    self._pong_received.wait(), timeout=self.PONG_TIMEOUT_S
                )
            except asyncio.TimeoutError:
                print("Server unreachable (PONG timeout). Disconnecting.")
                self.client.state = "DISCONNECTED"
                return

    #handle PONG response from server
    def on_pong(self, pdu: dict) -> None:
        self._pong_received.set()
