"""
client/connection.py — Client-Side Connection Handling (Module 01: Network Protocol)

Manages the client's TCP connection to the game server.  Provides framed
send/recv, separate seq_num tracking for server-echo vs. client-originated
PDUs, and async read/write loops.

The client distinguishes between:

* ``seq_num`` — the last ``seq_num`` received from the server (used for
  echoing in priority-bearing PDUs).
* ``client_seq_num`` — the client's own counter (used for PING and
  PLAYER_READY, which are exempt from the priority-echo rule).
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Coroutine

from shared.framing import ProtocolError, decode_frame, encode_frame
from shared.verbose import format_pdu_received, format_pdu_sent


class ClientConnection:
    """A TCP connection to the MTGNP server.

    Parameters
    ----------
    reader :
        The asyncio stream reader.
    writer :
        The asyncio stream writer.
    on_pdu :
        An async callback invoked by *read_loop* for every valid incoming PDU.
        Signature: ``async def handler(connection: ClientConnection, pdu: dict)``.
    verbose :
        If *True*, log every PDU sent/received to stderr.
    """

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

        # seq_num tracks the seq_num from the most recently received server
        # PDU.  The client MUST echo this value in all priority-bearing PDUs
        # (CAST_SPELL, PRIORITY_PASS, etc.).
        self.seq_num: int = 0

        # client_seq_num is the client's own counter, used for PING (RFC
        # §10.2.24) and PLAYER_READY (RFC §10.2.1).  Both are exempt from
        # the priority-echo rule.
        self.client_seq_num: int = 0

        # Serialise writes.
        self._write_lock = asyncio.Lock()

        # Outgoing PDU queue consumed by *write_loop*.
        self.outgoing: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        # Connection liveness.
        self._closed = False

    # ── Public send / recv ──────────────────────────────────────────────

    async def send_pdu(self, pdu: dict[str, Any]) -> None:
        """Send a framed PDU to the server.

        Unlike the server-side version, this does **not** auto-assign
        *seq_num*.  The caller is responsible for setting the correct
        seq_num based on the PDU type:

        * Priority-bearing PDUs → set ``pdu["seq_num"] = self.seq_num``
          (echoing the server's counter).
        * PING / PLAYER_READY → set ``pdu["seq_num"] = self.client_seq_num``
          (the client's own counter).

        Raises
        ------
        ConnectionError
            If the underlying TCP write fails.
        """
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
        """Read one framed PDU from the server.

        Updates *seq_num* with the received PDU's seq_num for later echo.

        Returns
        -------
        The parsed JSON dict.

        Raises
        ------
        ProtocolError
            If framing or JSON parsing fails.
        ConnectionError | EOFError
            If the connection is lost.
        """
        try:
            pdu = await decode_frame(self.reader)
        except ProtocolError:
            raise
        except (ConnectionError, EOFError, OSError, asyncio.IncompleteReadError) as exc:
            self._closed = True
            raise ConnectionError(f"Receive failed: {exc}") from exc

        # Update our echo counter from the server's seq_num.
        server_seq = pdu.get("seq_num")
        if server_seq is not None:
            self.seq_num = server_seq

        if self.verbose:
            print(format_pdu_received("S→C", pdu), file=__import__("sys").stderr)

        return pdu

    # ── Async loops ─────────────────────────────────────────────────────

    async def read_loop(self) -> None:
        """Continuously read PDUs from the server and dispatch them.

        This coroutine runs until the connection is closed or an unrecoverable
        error occurs.
        """
        while not self._closed:
            try:
                pdu = await self.recv_pdu()
            except ProtocolError:
                # Protocol-level error — log and try to continue.
                continue
            except (ConnectionError, EOFError, asyncio.IncompleteReadError):
                self._closed = True
                break

            if self.on_pdu is not None:
                await self.on_pdu(self, pdu)

    async def write_loop(self) -> None:
        """Continuously drain the outgoing queue and send framed PDUs."""
        while not self._closed:
            try:
                pdu = await self.outgoing.get()
                await self.send_pdu(pdu)
            except (ConnectionError, OSError):
                self._closed = True
                break

    # ── Lifecycle ───────────────────────────────────────────────────────

    async def close(self) -> None:
        """Gracefully close the TCP connection."""
        self._closed = True
        try:
            self.writer.close()
            await self.writer.wait_closed()
        except (ConnectionError, OSError):
            pass


# ── Factory helper ───────────────────────────────────────────────────────────


async def connect(
    host: str = "127.0.0.1",
    port: int = 4444,
    *,
    on_pdu: Callable[[ClientConnection, dict[str, Any]], Coroutine[Any, Any, None]] | None = None,
    verbose: bool = False,
) -> ClientConnection:
    """Open a TCP connection to the MTGNP server and wrap it in a *ClientConnection*.

    Parameters
    ----------
    host :
        Server hostname or IP address.
    port :
        Server TCP port.
    on_pdu :
        Async callback for incoming PDUs (see *ClientConnection*).
    verbose :
        Enable verbose PDU logging.

    Returns
    -------
    A fully initialised *ClientConnection* ready for send/recv.
    """
    reader, writer = await asyncio.open_connection(host, port)
    return ClientConnection(reader, writer, on_pdu=on_pdu, verbose=verbose)
