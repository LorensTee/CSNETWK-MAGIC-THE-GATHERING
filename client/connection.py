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

        #Tracks the seq num from the most recently received server PDU
        self.seq_num: int = 0

        # clients own counter, used for ping n player_ready
        self.client_seq_num: int = 0

        #ensures 1 write at a time
        self._write_lock = asyncio.Lock()

        #outgoing PDU queue 
        self.outgoing: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        #connection state
        self._closed = False

    #writes a PDU to the server, ensuring only one write at a time
    async def send_pdu(self, pdu: dict[str, Any]) -> None:
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

    #receives a PDU from the server, updates the seq_num, and returns PDU
    async def recv_pdu(self) -> dict[str, Any]:
        try:
            pdu = await decode_frame(self.reader)
        except ProtocolError:
            raise
        except (ConnectionError, EOFError, OSError, asyncio.IncompleteReadError) as exc:
            self._closed = True
            raise ConnectionError(f"Receive failed: {exc}") from exc

        server_seq = pdu.get("seq_num")
        if server_seq is not None and pdu.get("type") not in ("ERROR", "PONG"):
            self.seq_num = server_seq

        if self.verbose:
            print(format_pdu_received("S→C", pdu), file=__import__("sys").stderr)

        return pdu

    #continuously read PDUs from the server and dispatch them to the callback
    async def read_loop(self) -> None:
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

    #emptying queue and send PDUs to the server
    async def write_loop(self) -> None:
        while not self._closed:
            try:
                pdu = await self.outgoing.get()
                await self.send_pdu(pdu)
            except (ConnectionError, OSError):
                self._closed = True
                break

    #closes the connection
    async def close(self) -> None:
        self._closed = True
        try:
            self.writer.close()
            await self.writer.wait_closed()
        except (ConnectionError, OSError):
            pass

# connect to the server and return a ClientConnection object
async def connect(
    host: str = "127.0.0.1",
    port: int = 4444,
    *,
    on_pdu: Callable[[ClientConnection, dict[str, Any]], Coroutine[Any, Any, None]] | None = None,
    verbose: bool = False,
) -> ClientConnection:
    reader, writer = await asyncio.open_connection(host, port)
    return ClientConnection(reader, writer, on_pdu=on_pdu, verbose=verbose)
