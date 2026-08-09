"""
tests/test_verbose.py — Verbose-mode PDU logging (rubric PREREQUISITE).

Both client and server must print every PDU sent and received in a
readable, direction-labelled form when --verbose is active — and print
NOTHING when it is not.
"""

from __future__ import annotations

import asyncio

from client.connection import ClientConnection
from server.connection import ServerConnection
from shared.framing import encode_frame


class DummyWriter:
    def __init__(self) -> None:
        self.buffer = b""
        self.closed = False

    def write(self, data: bytes) -> None:
        self.buffer += data

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        pass

    def get_extra_info(self, name, default=None):
        if name == "peername":
            return ("127.0.0.1", 4444)
        return default


def _reader_with(*pdus: dict) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    for pdu in pdus:
        reader.feed_data(encode_frame(pdu))
    reader.feed_eof()
    return reader


GRANT = {"type": "PRIORITY_GRANT", "player_id": "p1", "time_limit_ms": 60000}
GSU = {"type": "GAME_STATE_UPDATE", "state": {"phase": "PRECOMBAT_MAIN",
                                             "life_totals": {"p1": 20, "p2": 20}}}


class TestServerVerbose:

    def test_server_send_prints_verbose(self, capsys):
        async def scenario():
            conn = ServerConnection(_reader_with(), DummyWriter(), verbose=True)
            conn.player_id = "p1"
            await conn.send_pdu(dict(GRANT))

        asyncio.run(scenario())
        err = capsys.readouterr().err
        assert "[S→C" in err
        assert "PRIORITY_GRANT" in err

    def test_server_recv_prints_verbose(self, capsys):
        async def scenario():
            conn = ServerConnection(_reader_with(dict(GSU)), DummyWriter(),
                                    verbose=True)
            conn.player_id = "p1"
            return await conn.recv_pdu()

        pdu = asyncio.run(scenario())
        assert pdu["type"] == "GAME_STATE_UPDATE"
        err = capsys.readouterr().err
        assert "[C→S" in err
        assert "GAME_STATE_UPDATE" in err

    def test_server_silent_without_verbose(self, capsys):
        async def scenario():
            conn = ServerConnection(_reader_with(), DummyWriter(),
                                    verbose=False)
            await conn.send_pdu(dict(GRANT))

        asyncio.run(scenario())
        assert capsys.readouterr().err == ""


class TestClientVerbose:

    def test_client_send_prints_verbose(self, capsys):
        async def scenario():
            conn = ClientConnection(_reader_with(), DummyWriter(), verbose=True)
            conn.player_id = "p1"
            await conn.send_pdu({"type": "PRIORITY_PASS"})

        asyncio.run(scenario())
        err = capsys.readouterr().err
        assert "[C→S" in err
        assert "PRIORITY_PASS" in err

    def test_client_recv_prints_verbose(self, capsys):
        async def scenario():
            conn = ClientConnection(_reader_with(dict(GRANT)), DummyWriter(),
                                    verbose=True)
            conn.player_id = "p1"
            return await conn.recv_pdu()

        pdu = asyncio.run(scenario())
        assert pdu["type"] == "PRIORITY_GRANT"
        err = capsys.readouterr().err
        assert "[S→C" in err
        assert "PRIORITY_GRANT" in err

    def test_client_silent_without_verbose(self, capsys):
        async def scenario():
            conn = ClientConnection(_reader_with(), DummyWriter(),
                                    verbose=False)
            await conn.send_pdu({"type": "PRIORITY_PASS"})

        asyncio.run(scenario())
        assert capsys.readouterr().err == ""
