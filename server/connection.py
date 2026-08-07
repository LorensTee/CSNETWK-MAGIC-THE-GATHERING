"""
server/connection.py — Server-Side Connection Handling (Module 01: Network Protocol)

Manages a single client connection on the server side.  Each connected player
gets its own ``ServerConnection`` instance, which owns the TCP reader/writer
pair, the outgoing PDU queue, and the per-connection seq_num counter.

The server uses ``read_loop`` to receive PDUs from the client and dispatches
them to the game lifecycle, and ``write_loop`` to drain the outgoing queue
and send framed PDUs to the client.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Coroutine

from shared.framing import ProtocolError, decode_frame, encode_frame
from shared.verbose import format_pdu_received, format_pdu_sent

logger = logging.getLogger(__name__)


class ServerConnection:
    """A connected player on the server side.

    Parameters
    ----------
    reader :
        The asyncio stream reader for this TCP connection.
    writer :
        The asyncio stream writer for this TCP connection.
    on_pdu :
        An async callback invoked by *read_loop* for every valid incoming PDU.
        Signature: ``async def handler(connection: ServerConnection, pdu: dict)``.
    verbose :
        If *True*, log every PDU sent/received to stderr via the verbose
        formatting helpers.
    """

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        on_pdu: Callable[[ServerConnection, dict[str, Any]], Coroutine[Any, Any, None]] | None = None,
        *,
        verbose: bool = False,
    ) -> None:
        self.reader = reader
        self.writer = writer
        self.on_pdu = on_pdu
        self.verbose = verbose

        # seq_num is the monotonically increasing counter the server uses
        # for every PDU it sends to *this* client.  Incremented before each
        # call to *send_pdu*.
        self.seq_num: int = 0

        # player_id is assigned after a successful PLAYER_READY (set by the
        # game lifecycle).
        self.player_id: str | None = None

        # Serialise writes so send_pdu is safe from concurrent tasks.
        self._write_lock = asyncio.Lock()

        # Queue of outgoing PDUs consumed by *write_loop*.
        self.outgoing: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        # Tracks whether the connection is still alive.
        self._closed = False

    # ── Public send / recv ──────────────────────────────────────────────

    async def send_pdu(self, pdu: dict[str, Any]) -> None:
        """Send a framed PDU to this client.

        Automatically sets the ``seq_num`` field to the current counter value
        and increments it afterwards.  This guarantees monotonicity on the
        wire (RFC §10.1).

        Raises
        ------
        ConnectionError
            If the underlying TCP write fails.
        """

        pdu_type = pdu.get("type", "")

        if pdu_type not in ("PING", "PONG"):
            self.seq_num += 1
            pdu["seq_num"] = self.seq_num

        if self.verbose:
            label = f"S→C {self.player_id or '?'}"
            print(format_pdu_sent(label, pdu), file=__import__("sys").stderr)

        async with self._write_lock:
            try:
                frame = encode_frame(pdu)
                self.writer.write(frame)
                await self.writer.drain()
            except (ConnectionError, OSError) as exc:
                self._closed = True
                raise ConnectionError(f"Send failed: {exc}") from exc

    async def recv_pdu(self) -> dict[str, Any]:
        """Read one framed PDU from this client's TCP stream.

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

        if self.verbose:
            label = f"C→S {self.player_id or '?'}"
            print(format_pdu_received(label, pdu), file=__import__("sys").stderr)

        return pdu

    # ── Async loops ─────────────────────────────────────────────────────

    async def read_loop(self) -> None:
        """Continuously read PDUs from the client and dispatch them."""
        import traceback

        while not self._closed:
            try:
                pdu = await self.recv_pdu()
            except ProtocolError as exc:
                import logging
                logger = logging.getLogger(__name__)
                logger.warning("Protocol error from %s: %s",
                               self.player_id or '?', exc)
                continue
            except (ConnectionError, EOFError, asyncio.IncompleteReadError):
                self._closed = True
                break

            if self.on_pdu is not None:
                try:
                    await self.on_pdu(self, pdu)
                except Exception as e:
                    print("\n" + "="*50)
                    print(f"🚨 FATAL ERROR IN PLAYER {self.player_id} LOOP 🚨")
                    traceback.print_exc()
                    print("="*50 + "\n")
                    # Break the loop so the socket closes cleanly instead of zombifying
                    self._closed = True
                    break

    async def write_loop(self) -> None:
        """Continuously drain the outgoing queue and send framed PDUs.

        This coroutine runs until the connection is closed.  It blocks on
        *outgoing.get()* so it won't busy-loop when the queue is empty.
        """
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
