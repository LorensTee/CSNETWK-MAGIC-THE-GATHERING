"""
tests/test_server_watchdog.py — Unit tests for the server watchdog (Module 02).

The disconnect-timeout GAME_OVER must be sent through ``send_pdu`` so it is
verbose-logged (rubric prerequisite), seq-numbered, and does NOT close the
winner's socket (RFC §6.6: connections are retained after GAME_OVER).
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any

from server.server import GameServer


class FakeConn:
    """Minimal ServerConnection stand-in."""

    def __init__(self, player_id: str | None, closed: bool = False) -> None:
        self.player_id = player_id
        self._closed = closed
        self.seq_num = 0
        self.sent: list[dict[str, Any]] = []
        self.verbose = False

    async def send_pdu(self, pdu: dict[str, Any]) -> None:
        self.seq_num += 1
        pdu["seq_num"] = self.seq_num
        self.sent.append(dict(pdu))


class TestWatchdogSweep:

    def _make_server(self) -> GameServer:
        server = GameServer.__new__(GameServer)  # skip CardLoader init
        server.config = SimpleNamespace(disconnect_timeout_s=0)
        server._connections = []
        return server

    def test_game_over_sent_via_send_pdu_and_socket_retained(self):
        async def scenario():
            server = self._make_server()
            loser = FakeConn("p1", closed=True)
            winner = FakeConn("p2", closed=False)
            server._connections = [loser, winner]

            end_args = {}

            async def fake_end_game(gs, reason, winner_id, loser_id):
                end_args.update(reason=reason, winner_id=winner_id,
                                loser_id=loser_id)
                lifecycle._game_over.set()

            lifecycle = SimpleNamespace(
                _game_over=asyncio.Event(),
                _end_game=fake_end_game,
                gs=SimpleNamespace(),
            )
            disconnect_times = {loser: time.time() - 10}

            ended = await server._watchdog_sweep(lifecycle, disconnect_times)

            assert ended is True
            assert lifecycle._game_over.is_set()

            # GAME_OVER(DISCONNECT) is routed through _end_game, which
            # broadcasts via send_pdu (seq-numbered, verbose-visible) and
            # resets the ready-state for the next LOBBY.
            assert end_args["reason"] == "DISCONNECT"
            assert end_args["winner_id"] == "p2"
            assert end_args["loser_id"] == "p1"

            # The winner's socket must NOT be closed (RFC §6.6 reuse).
            assert winner._closed is False

        asyncio.run(scenario())

    def test_no_timeout_yet_does_not_end_game(self):
        async def scenario():
            server = self._make_server()
            loser = FakeConn("p1", closed=True)
            winner = FakeConn("p2", closed=False)
            server._connections = [loser, winner]

            lifecycle = SimpleNamespace(_game_over=asyncio.Event())
            # First sweep pass: timer starts, no GAME_OVER yet.
            disconnect_times: dict = {}
            ended = await server._watchdog_sweep(lifecycle, disconnect_times)

            assert ended is False
            assert not lifecycle._game_over.is_set()
            assert winner.sent == []

        asyncio.run(scenario())

    def test_both_closed_no_game_over_broadcast(self):
        async def scenario():
            server = self._make_server()
            loser = FakeConn("p1", closed=True)
            winner = FakeConn("p2", closed=True)
            server._connections = [loser, winner]

            lifecycle = SimpleNamespace(_game_over=asyncio.Event())
            disconnect_times = {loser: time.time() - 10}

            ended = await server._watchdog_sweep(lifecycle, disconnect_times)

            assert ended is True
            assert lifecycle._game_over.is_set()
            assert winner.sent == []  # nobody to broadcast to

        asyncio.run(scenario())
