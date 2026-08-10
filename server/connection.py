from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Coroutine

from shared.framing import ProtocolError, decode_frame, encode_frame
from shared.verbose import format_pdu_received, format_pdu_sent

logger = logging.getLogger(__name__)


class ServerConnection:
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

        self.seq_num: int = 0
        self.player_id: str | None = None
        self.grant_token: int | None = None
        self._write_lock = asyncio.Lock()
        self.outgoing: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._closed = False

    async def send_pdu(self, pdu: dict[str, Any]) -> None:
        # send pdu to client
        pdu_type = pdu.get("type", "")

        if pdu_type not in ("PING", "PONG"):
            self.seq_num += 1
            pdu["seq_num"] = self.seq_num

        await self._write_frame(pdu)

    async def send_pdu_explicit(self, pdu: dict[str, Any], seq_num: int) -> None:
        #send pdu to client with seq_num
        pdu["seq_num"] = seq_num
        await self._write_frame(pdu)

    async def _write_frame(self, pdu: dict[str, Any]) -> None:
        #write a pdu to the client
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
        #receive a pdu from the client, decode it, and verbose it
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

    async def read_loop(self) -> None:
        #read incoming PDUs from the client and dispatch them to the handler
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
                    print(f"ERROR IN PLAYER {self.player_id} LOOP")
                    traceback.print_exc()
                    print("="*50 + "\n")
                    #stop the connection on error
                    self._closed = True
                    break

    async def write_loop(self) -> None:
        #write outgoing PDUs to the client
        while not self._closed:
            try:
                pdu = await self.outgoing.get()
                await self.send_pdu(pdu)
            except (ConnectionError, OSError):
                self._closed = True
                break

    async def close(self) -> None:
        """Gracefully close the TCP connection."""
        self._closed = True
        try:
            self.writer.close()
            await self.writer.wait_closed()
        except (ConnectionError, OSError):
            pass
