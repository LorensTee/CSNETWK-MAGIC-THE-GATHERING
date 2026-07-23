"""
client/heartbeat.py — PING/PONG Heartbeat (Module 03: Client App)

Implements the client-side heartbeat protocol (RFC §4.3):

* Send ``PING`` every 30 seconds with a client-maintained seq_num and
  a Unix-epoch-milliseconds timestamp.
* The server echoes ``PONG`` with the same seq_num and timestamp.
* If no ``PONG`` is received within 10 seconds, the client considers the
  server unreachable and disconnects.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from shared.pdus import create_ping

if TYPE_CHECKING:
    from client.client import GameClient


class HeartbeatManager:
    """Manages the PING/PONG heartbeat cycle.

    Parameters
    ----------
    client :
        The ``GameClient`` whose *outgoing_queue* is used to send PINGs.
    """

    def __init__(self, client: GameClient) -> None:
        self.client = client
        self._ping_seq: int = 0
        self._last_ping_time: float | None = None
        self._pong_received = asyncio.Event()
        self._pong_received.set()  # Start in "received" state.

    async def run(self) -> None:
        """Run the heartbeat loop.

        Sends a ``PING`` every 30 seconds and waits up to 10 seconds for a
        ``PONG``.  On timeout, sets ``client.state = 'DISCONNECTED'`` and
        returns.
        """
        while self.client.state not in ("DISCONNECTED", "GAME_OVER"):
            await asyncio.sleep(30)

            if self.client.state == "DISCONNECTED":
                break

            # Increment our counter.
            self._ping_seq += 1
            timestamp_ms = int(time.time() * 1000)

            ping_pdu = create_ping(seq_num=self._ping_seq, timestamp=timestamp_ms)
            await self.client.outgoing_queue.put(ping_pdu)

            self._last_ping_time = time.monotonic()
            self._pong_received.clear()

            # Wait for PONG with 10-second timeout.
            try:
                await asyncio.wait_for(self._pong_received.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                print("Server unreachable (PONG timeout). Disconnecting.")
                self.client.state = "DISCONNECTED"
                return

    def on_pong(self, pdu: dict) -> None:
        """Handle a received ``PONG`` PDU.

        If the seq_num matches the last sent ``PING``, signal that the
        heartbeat is alive.
        """
        if pdu.get("seq_num") == self._ping_seq:
            self._pong_received.set()
