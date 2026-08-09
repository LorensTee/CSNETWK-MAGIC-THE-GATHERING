"""
tests/test_client.py — Client robustness (Module 03).

* Heartbeat keeps running across GAME_OVER → LOBBY (next game).
* Stdin reads use loop.add_reader (no background thread) so a blocked
  read can never stall shutdown.
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import pytest

from client.heartbeat import HeartbeatManager
from client.input_handler import InputHandler


class TestHeartbeat:

    def test_heartbeat_continues_after_game_over(self):
        async def scenario():
            client = SimpleNamespace(state="LOBBY",
                                     outgoing_queue=asyncio.Queue())
            hb = HeartbeatManager(client)
            hb.PING_INTERVAL_S = 0.01
            hb.PONG_TIMEOUT_S = 0.05

            async def pong_and_cycle():
                # GAME_OVER blip mid-run, then keep answering PONGs.
                await asyncio.sleep(0.02)
                hb.on_pong({})
                client.state = "GAME_OVER"
                await asyncio.sleep(0.02)
                client.state = "LOBBY"  # next game starts
                while True:
                    await asyncio.sleep(0.02)
                    hb.on_pong({})

            t = asyncio.create_task(pong_and_cycle())
            # run() must KEEP heartbeating (not exit at GAME_OVER) — the
            # wait_for timeout proves it is still alive.
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(hb.run(), timeout=0.3)
            t.cancel()

            pings = 0
            while not client.outgoing_queue.empty():
                pdu = client.outgoing_queue.get_nowait()
                if pdu["type"] == "PING":
                    pings += 1
            assert pings >= 2  # pinged before AND after the GAME_OVER blip

        asyncio.run(scenario())

    def test_pong_timeout_sets_disconnected(self):
        async def scenario():
            client = SimpleNamespace(state="LOBBY",
                                     outgoing_queue=asyncio.Queue())
            hb = HeartbeatManager(client)
            hb.PING_INTERVAL_S = 0.01
            hb.PONG_TIMEOUT_S = 0.02  # never answered

            await hb.run()
            assert client.state == "DISCONNECTED"

        asyncio.run(scenario())


class TestInputReadLine:

    def test_read_line_from_stdin_pipe(self, monkeypatch):
        import client.input_handler as ih

        r, w = os.pipe()
        os.write(w, b"cast lightning_bolt\n")
        os.close(w)

        class FakeStdin:
            def fileno(self):
                return r

        monkeypatch.setattr(ih.sys, "stdin", FakeStdin())
        handler = InputHandler(SimpleNamespace(state="PLAYING"))

        line = asyncio.run(handler._read_line())
        assert line == "cast lightning_bolt\n"

    def test_read_line_eof_returns_none(self, monkeypatch):
        import client.input_handler as ih

        r, w = os.pipe()
        os.close(w)  # EOF immediately

        class FakeStdin:
            def fileno(self):
                return r

        monkeypatch.setattr(ih.sys, "stdin", FakeStdin())
        handler = InputHandler(SimpleNamespace(state="PLAYING"))

        assert asyncio.run(handler._read_line()) is None
