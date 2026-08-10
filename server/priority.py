from __future__ import annotations

import asyncio
from typing import Any, Callable, Coroutine

from server.config import ServerConfig
from server.connection import ServerConnection
from shared.framing import ProtocolError
from shared.pdus import create_error, create_priority_grant, validate_seq_num

#raised when a player fails to respond within the time limit
class PriorityTimeout(Exception):
    def __init__(self, player_id: str) -> None:
        self.player_id = player_id
        super().__init__(f"Player '{player_id}' timed out on priority.")

#raised when client's connection is lost during priority
class ConnectionLost(Exception):
    def __init__(self, player_id: str) -> None:
        self.player_id = player_id
        super().__init__(f"Connection lost for player '{player_id}'.")


ReadPduCb = Callable[[str, float], Coroutine[Any, Any, dict[str, Any]]]

#manages priority grants and the pass/resolve cycle
class PriorityManager:

    def __init__(self, config: ServerConfig) -> None:
        self.config = config

    #grants priotiry to a player and awaits response
    async def grant_priority(
        self,
        conn: ServerConnection,
        player_id: str,
        *,
        read_pdu: ReadPduCb | None = None,
        timeout_ms: int | None = None,
    ) -> dict[str, Any] | None:
        timeout = timeout_ms if timeout_ms is not None else self.config.time_limit_ms
        timeout_s = timeout / 1000.0

        read_task: asyncio.Task | None = None
        if read_pdu is not None:
            read_task = asyncio.create_task(read_pdu(player_id, timeout_s))

        try:
            grant_pdu = create_priority_grant(
                seq_num=0,  # send_pdu overwrites this
                player_id=player_id,
                time_limit_ms=timeout,
            )
            await conn.send_pdu(grant_pdu)
            expected_seq = conn.seq_num
            conn.grant_token = expected_seq

            while True:
                # (re)register the waiter before the grant, and again after a stale response consumed it
                if read_task is None and read_pdu is not None:
                    read_task = asyncio.create_task(read_pdu(player_id, timeout_s))

                # wait for response with timeout
                try:
                    if read_task is not None:
                        response = await asyncio.wait_for(
                            asyncio.shield(read_task), timeout=timeout_s
                        )
                    else:
                        response = await asyncio.wait_for(
                            conn.recv_pdu(), timeout=timeout_s
                        )
                except asyncio.TimeoutError:
                    raise PriorityTimeout(player_id) from None
                except (ProtocolError, ConnectionError, EOFError, OSError) as exc:
                    raise ConnectionLost(player_id) from exc

                #Validate seq_num
                actual = response.get("seq_num", -1)
                if not validate_seq_num(expected_seq, actual):
                    err_pdu = create_error(
                        seq_num=actual,
                        code="STALE_ACTION",
                        message=(
                            f"Priority token mismatch. "
                            f"Expected {expected_seq}, got {actual}."
                        ),
                        rejected_action=response,
                    )
                    await conn.send_pdu_explicit(err_pdu, actual)
                    grant_pdu = create_priority_grant(
                        seq_num=expected_seq,
                        player_id=player_id,
                        time_limit_ms=timeout,
                    )
                    await conn.send_pdu_explicit(grant_pdu, expected_seq)
                    read_task = None  # consumed; re-register for the retry
                    continue

                # return the response
                return response
        finally:
            if read_task is not None:
                if not read_task.done():
                    read_task.cancel()
                elif not read_task.cancelled():
                    try:
                        read_task.exception()  #retrieve any latent error
                    except asyncio.CancelledError:
                        pass

    #run one full prio window
    async def run_priority_window(
        self,
        ap_conn: ServerConnection,
        nap_conn: ServerConnection,
        ap_id: str,
        nap_id: str,
        *,
        read_pdu: ReadPduCb | None = None,
        on_ap_pass_cb: Any = None,
    ) -> tuple[bool, dict[str, Any] | None]:
        ap_response = await self.grant_priority(
            ap_conn, ap_id, read_pdu=read_pdu,
        )
        if ap_response is not None and ap_response.get("type") != "PRIORITY_PASS":
            return False, ap_response, ap_id

        if on_ap_pass_cb:
            await on_ap_pass_cb()

        nap_response = await self.grant_priority(
            nap_conn, nap_id, read_pdu=read_pdu,
        )
        if nap_response is not None and nap_response.get("type") != "PRIORITY_PASS":
            return False, nap_response, nap_id

        return True, None, None
