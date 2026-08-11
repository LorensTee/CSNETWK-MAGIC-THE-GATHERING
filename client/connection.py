from __future__ import annotations

import asyncio
from typing import Any, Callable, Coroutine

from shared.framing import ProtocolError, decode_frame, encode_frame
from shared.verbose import format_pdu_received, format_pdu_sent


class ClientConnection:
    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        on_pdu: Callable[[ClientConnection, dict[str, Any]], Coroutine[Any, Any, None]] | None = None,
        *,
        verbose: bool = False,
    ) -> None:
        self.reader = reader
        self.writer = writer
        self.on_pdu = on_pdu
        self.verbose = verbose
        self.seq_num: int = 0 #seq_num tracks the seq_num from the most recently received serverPDU
        self.client_seq_num: int = 0  #client_seq_num is client's own counter, used for PING and PLAYER_READY
        self._write_lock = asyncio.Lock()
        self.outgoing: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._closed = False

    async def send_pdu(self, pdu: dict[str, Any]) -> None:
        """send framed PDU to the server"""
        if self.verbose:
            print(format_pdu_sent("C→S", pdu), file=__import__("sys").stderr)

        async with self._write_lock:
            try:
                frame = encode_frame(pdu)
                self.writer.write(frame)
                await self.writer.drain()
            except (ConnectionError, OSError) as exc:
                self._closed = True
                raise ConnectionError(f"Send failed: {exc}") from exc

    async def recv_pdu(self) -> dict[str, Any]:
        """Read one framed PDU from the server."""
        try:
            pdu = await decode_frame(self.reader)
        except ProtocolError:
            raise
        except (ConnectionError, EOFError, OSError, asyncio.IncompleteReadError) as exc:
            self._closed = True
            raise ConnectionError(f"Receive failed: {exc}") from exc

        # update echo counter from the server's seq_num
        server_seq = pdu.get("seq_num")
        if server_seq is not None:
            self.seq_num = server_seq

        if self.verbose:
            print(format_pdu_received("S→C", pdu), file=__import__("sys").stderr)

        return pdu


    async def read_loop(self) -> None:
        """continuously read PDUs from the server and dispatch them """
        while not self._closed:
            try:
                pdu = await self.recv_pdu()
            except ProtocolError:
                continue
            except (ConnectionError, EOFError, asyncio.IncompleteReadError):
                self._closed = True
                break

            if self.on_pdu is not None:
                await self.on_pdu(self, pdu)

    async def write_loop(self) -> None:
        """continuously empty the outgoing queue and send framed PDUs"""
        while not self._closed:
            try:
                pdu = await self.outgoing.get()
                await self.send_pdu(pdu)
            except (ConnectionError, OSError):
                self._closed = True
                break

    async def close(self) -> None:
        """close the TCP connection"""
        self._closed = True
        try:
            self.writer.close()
            await self.writer.wait_closed()
        except (ConnectionError, OSError):
            pass

async def connect(
    host: str = "127.0.0.1",
    port: int = 4444,
    *,
    on_pdu: Callable[[ClientConnection, dict[str, Any]], Coroutine[Any, Any, None]] | None = None,
    verbose: bool = False,
) -> ClientConnection:
    """open a TCP connection to server """
    reader, writer = await asyncio.open_connection(host, port)
    return ClientConnection(reader, writer, on_pdu=on_pdu, verbose=verbose)
